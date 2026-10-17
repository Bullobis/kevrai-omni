"""国产加速卡识别与评分权重测试。

覆盖：单位换算、系统兜底分类、JSON 宽松解析、厂商权重、与主流程集成。
全部离线，不依赖真实硬件。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from app import accelerators, gpu, hardware


# ---------------------------------------------------------------------------
# 单位换算
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("16384 MiB", 16384),
        ("16 GB", 16384),
        ("16 GiB", 16384),
        ("4096 MB", 4096),
        ("17179869184", 16384),   # 裸字节数
        ("", 0),
        ("N/A", 0),
        ("12345", 12345),          # 小于 1e6 视为 MiB
    ],
)
def test_to_mb(raw: str, expected: int) -> None:
    assert accelerators._to_mb(raw) == expected


# ---------------------------------------------------------------------------
# 系统兜底分类
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,vendor",
    [
        ("01:00.0 VGA compatible controller: NVIDIA Corporation GA102 [10de:2206]", "nvidia"),
        ("00:02.0 Display controller: Intel Corporation Xe Graphics [8086:9a49]", "intel"),
        ("03:00.0 3D controller: Moore Threads Technology MTT S80", "moorethreads"),
        ("04:00.0 Processing accelerators: Iluvatar CoreX MRSC", "iluvatar"),
        ("05:00.0 Processing accelerators: Cambricon MLU370", "cambricon"),
        ("06:00.0 Processing accelerators: Hygon DCU Z100", "hygon"),
        ("07:00.0 Processing accelerators: Huawei Ascend 910", "ascend"),
        ("08:00.0 VGA: Advanced Micro Devices Radeon RX 7900", "amd"),
    ],
)
def test_classify(line: str, vendor: str) -> None:
    assert accelerators._classify(line) == vendor


def test_classify_ignores_unrelated_devices() -> None:
    assert accelerators._classify("00:1f.3 Audio device: Intel Corporation") is None
    assert accelerators._classify("02:00.0 Ethernet controller: Realtek") is None


def test_system_fallback_parses_lspci_and_strips_ids() -> None:
    lspci = (
        "00:02.0 VGA compatible controller: Intel Corporation UHD Graphics [8086:9a49]\n"
        "01:00.0 3D controller: Moore Threads Technology MTT S80 [1ed5:0100]\n"
        "00:1f.3 Audio device: Intel Corporation Comet Lake\n"
    )
    with mock.patch.object(accelerators, "_lspci_text", return_value=lspci), \
         mock.patch.object(accelerators, "_windows_gpu_names", return_value=[]):
        gpus = accelerators.detect_by_system()

    vendors = [g.vendor for g in gpus]
    assert "intel" in vendors
    assert "moorethreads" in vendors
    # 声卡不应被识别成显卡
    assert len(vendors) == 2
    # 设备 ID 方括号应被清理
    assert all("[" not in g.name for g in gpus)
    assert all(g.raw.get("system_fallback") for g in gpus)


def test_system_fallback_dedupes_same_vendor_model() -> None:
    lspci = (
        "00:02.0 VGA: Intel Corporation UHD Graphics\n"
        "00:03.0 VGA: Intel Corporation UHD Graphics\n"
    )
    with mock.patch.object(accelerators, "_lspci_text", return_value=lspci), \
         mock.patch.object(accelerators, "_windows_gpu_names", return_value=[]):
        gpus = accelerators.detect_by_system()
    assert len(gpus) == 1


def test_system_fallback_windows_names() -> None:
    with mock.patch.object(accelerators, "_lspci_text", return_value=""), \
         mock.patch.object(
             accelerators,
             "_windows_gpu_names",
             return_value=["Moore Threads MTT S80", "Intel(R) UHD Graphics"],
         ):
        gpus = accelerators.detect_by_system()
    assert {g.vendor for g in gpus} == {"moorethreads", "intel"}


# ---------------------------------------------------------------------------
# 厂商 JSON 宽松解析
# ---------------------------------------------------------------------------


def test_parse_generic_json_flat_dict() -> None:
    data = {"card0": {"Device Name": "MTT S80", "Memory Total": "16384 MiB"}}
    gpus = accelerators._parse_generic_json(data, "moorethreads")
    assert len(gpus) == 1
    assert gpus[0].vendor == "moorethreads"
    assert gpus[0].vram_mb == 16384
    assert "S80" in gpus[0].name


def test_parse_generic_json_nested_list() -> None:
    data = {
        "devices": [
            {"name": "MLU370", "memory_total": "24576 MiB"},
            {"name": "MLU590", "memory_total": "49152 MiB"},
        ]
    }
    gpus = accelerators._parse_generic_json(data, "cambricon")
    assert [g.vram_mb for g in gpus] == [24576, 49152]
    assert [g.index for g in gpus] == [0, 1]


def test_parse_generic_json_ignores_noise() -> None:
    assert accelerators._parse_generic_json({"foo": {"bar": 1}}, "intel") == []
    assert accelerators._parse_generic_json([], "intel") == []


# ---------------------------------------------------------------------------
# 厂商工具探测：工具缺失时安全返回空
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vendor_probes_return_empty_without_tools() -> None:
    with mock.patch.object(accelerators, "_which_any", return_value=None):
        out = await accelerators.detect_accelerators()
    assert out == []


@pytest.mark.asyncio
async def test_probe_never_raises_on_bad_output() -> None:
    with mock.patch.object(accelerators, "_which_any", return_value="/bin/echo"), \
         mock.patch.object(accelerators, "_run", return_value="garbage !!!"):
        out = await accelerators.detect_accelerators()
    # 解析失败应退化为 []，而不是抛异常
    assert isinstance(out, list)


@pytest.mark.asyncio
async def test_detect_accelerators_swallows_probe_exception() -> None:
    async def boom():
        raise RuntimeError("vendor tool crashed")

    with mock.patch.object(accelerators, "_detect_moorethreads", boom), \
         mock.patch.object(accelerators, "_detect_iluvatar", boom), \
         mock.patch.object(accelerators, "_detect_cambricon", boom), \
         mock.patch.object(accelerators, "_detect_hygon", boom), \
         mock.patch.object(accelerators, "_detect_intel", boom):
        out = await accelerators.detect_accelerators()
    assert out == []


@pytest.mark.asyncio
async def test_moorethreads_probe_parses_text_table() -> None:
    text = "MTT S80   16384 MiB   65C\nMTT S70   8192 MiB   60C\n"
    with mock.patch.object(accelerators, "_which_any", return_value="/usr/bin/mthreads-smi"), \
         mock.patch.object(accelerators, "_run", return_value=text):
        out = await accelerators._detect_moorethreads()
    assert len(out) == 2
    assert out[0].vendor == "moorethreads"
    assert out[0].vram_mb == 16384


# ---------------------------------------------------------------------------
# 权重与评分
# ---------------------------------------------------------------------------


def test_vendor_weights_cover_all_vendors() -> None:
    for v in accelerators.VENDOR_LABELS:
        assert v in accelerators.VENDOR_WEIGHTS, f"{v} 缺少权重"
        assert 0.0 <= accelerators.VENDOR_WEIGHTS[v] <= 1.0


def test_nvidia_scores_higher_than_cpu() -> None:
    base = {
        "ram_total_gb": 32,
        "gpu_total_vram_gb": 8,
        "cpu": {"physical_cores": 8, "avx2": True, "avx512": False},
    }
    hi = hardware._score({**base, "gpu_vendor": "nvidia"})
    lo = hardware._score({**base, "gpu_vendor": "cpu"})
    mid = hardware._score({**base, "gpu_vendor": "moorethreads"})
    assert hi > mid > lo


def test_unknown_vendor_gets_conservative_weight() -> None:
    base = {
        "ram_total_gb": 32,
        "gpu_total_vram_gb": 8,
        "cpu": {"physical_cores": 8, "avx2": True, "avx512": False},
    }
    unknown = hardware._score({**base, "gpu_vendor": "some-new-vendor"})
    cpu = hardware._score({**base, "gpu_vendor": "cpu"})
    nvidia = hardware._score({**base, "gpu_vendor": "nvidia"})
    assert cpu < unknown < nvidia


def test_vendor_label_falls_back_to_raw() -> None:
    assert hardware._vendor_label("nvidia") == "NVIDIA"
    assert hardware._vendor_label("moorethreads") == "摩尔线程"
    assert hardware._vendor_label("brand-new") == "brand-new"


# ---------------------------------------------------------------------------
# 与主流程集成
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gpu_detect_includes_accelerators_with_unique_index() -> None:
    fake_nvidia = gpu.GPUInfo(vendor="nvidia", name="RTX 3090", vram_mb=24576)
    fake_mtt = gpu.GPUInfo(vendor="moorethreads", name="MTT S80", vram_mb=16384)

    async def fake_nv():
        return [fake_nvidia]

    async def fake_acc():
        return [fake_mtt]

    with mock.patch.object(gpu, "_detect_nvidia", fake_nv), \
         mock.patch("app.accelerators.detect_accelerators", fake_acc):
        out = await gpu.detect()

    assert [g.vendor for g in out] == ["nvidia", "moorethreads"]
    # index 必须唯一，否则 UI 会选错设备
    assert [g.index for g in out] == [0, 1]


@pytest.mark.asyncio
async def test_gpu_detect_uses_system_fallback_when_no_tools() -> None:
    fallback = gpu.GPUInfo(vendor="cambricon", name="MLU370", vram_mb=0)

    async def no_gpus():
        return []

    with mock.patch.object(gpu, "_detect_nvidia", no_gpus), \
         mock.patch("app.accelerators.detect_accelerators", no_gpus), \
         mock.patch("app.accelerators.detect_by_system", return_value=[fallback]):
        out = await gpu.detect()
    assert out and out[0].vendor == "cambricon"


@pytest.mark.asyncio
async def test_gpu_detect_falls_back_to_cpu() -> None:
    async def no_gpus():
        return []

    with mock.patch.object(gpu, "_detect_nvidia", no_gpus), \
         mock.patch("app.accelerators.detect_accelerators", no_gpus), \
         mock.patch("app.accelerators.detect_by_system", return_value=[]):
        out = await gpu.detect()
    assert out and out[0].vendor == "cpu"


@pytest.mark.asyncio
async def test_gpu_detect_survives_accelerator_import_failure() -> None:
    """accelerators 模块缺失时，主流程必须仍然返回 CPU 兜底。"""
    async def no_gpus():
        return []

    with mock.patch.object(gpu, "_detect_nvidia", no_gpus), \
         mock.patch("builtins.__import__", side_effect=ImportError("boom")):
        out = await gpu.detect()
    assert out and out[0].vendor == "cpu"


@pytest.mark.asyncio
async def test_hardware_snapshot_exposes_vendor_label() -> None:
    hw = await hardware.detect_hardware(Path("."))
    assert "gpu_vendor_label" in hw
    assert "gpu_vendor_weight" in hw
    assert "gpu_labels" in hw
    assert 1 <= hw["score"] <= 100
