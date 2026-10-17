"""国产加速卡 / Intel GPU 探测与系统兜底识别。

覆盖 gpu.py 原本缺失的厂商：

    * 摩尔线程 Moore Threads (MUSA)   -> vendor "moorethreads"
    * 天数智芯 Iluvatar CoreX         -> vendor "iluvatar"
    * 寒武纪 Cambricon (MLU)          -> vendor "cambricon"
    * 海光 Hygon (DCU)                -> vendor "hygon"
    * Intel 显卡 / 数据中心加速卡      -> vendor "intel"

设计原则（与 gpu.py 保持一致）：
    1. 厂商管理工具优先，拿不到就走系统兜底（Linux lspci / sysfs，Windows WMI）
    2. 每个探测都包在 try/except 里，且带超时并 kill 子进程 —— 绝不让某个厂商的
       工具卡死拖垮整体 detect()
    3. 永不抛异常，失败退化为 []
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import re
import shutil
from typing import Any

from .gpu import GPUInfo

__all__ = [
    "detect_accelerators",
    "detect_by_system",
    "VENDOR_LABELS",
    "VENDOR_WEIGHTS",
]

log = _log = None  # 延迟导入，避免循环依赖
try:  # pragma: no cover - logging 总是可用
    import logging

    log = logging.getLogger("kevrai.accelerators")
except Exception:  # pragma: no cover
    log = None


def _debug(msg: str, *args: Any) -> None:
    if log is not None:  # pragma: no cover
        with contextlib.suppress(Exception):
            log.debug(msg, *args)


# ---------------------------------------------------------------------------
# 常量：展示名与评分权重
# ---------------------------------------------------------------------------

#: vendor -> 中文展示名
VENDOR_LABELS: dict[str, str] = {
    "nvidia": "NVIDIA",
    "amd": "AMD",
    "apple": "Apple Silicon",
    "ascend": "华为昇腾",
    "moorethreads": "摩尔线程",
    "iluvatar": "天数智芯",
    "cambricon": "寒武纪",
    "hygon": "海光",
    "intel": "Intel",
    "cpu": "纯 CPU",
}

#: vendor -> 生态成熟度权重（0.0 ~ 1.0）。
#: 用于 hardware.py 综合评分：本地推理软件栈对该厂商的支持越成熟，权重越高。
#: 数值是"生态可用性"的相对排序，不是性能跑分。
VENDOR_WEIGHTS: dict[str, float] = {
    "nvidia": 1.0,        # CUDA + llama.cpp 一等公民
    "apple": 0.9,         # Metal / MLX
    "amd": 0.7,           # ROCm / Vulkan
    "intel": 0.6,         # SYCL / oneAPI / Vulkan，生态可用但在本地推理栈里覆盖有限
    "moorethreads": 0.45,  # MUSA，兼容层可用，算子覆盖仍在补齐
    "hygon": 0.45,        # DCU，类 ROCm 生态
    "ascend": 0.4,        # CANN / torch_npu
    "cambricon": 0.35,    # MLU，需 Cambricon PyTorch 插件
    "iluvatar": 0.35,     # CoreX，需厂商 SDK
    "cpu": 0.1,
}


# ---------------------------------------------------------------------------
# 通用子进程工具
# ---------------------------------------------------------------------------


async def _run(argv: list[str], timeout: float = 3.0) -> str:
    """运行命令并返回 stdout；任何失败都返回空串，且不泄漏子进程。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError, OSError):
        return ""
    try:
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(Exception):
            proc.kill()
        return ""
    except Exception:
        with contextlib.suppress(Exception):
            proc.kill()
        return ""
    if proc.returncode != 0:
        return ""
    return stdout_b.decode("utf-8", errors="replace")


def _which_any(names: tuple[str, ...]) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _to_mb(raw: str) -> int:
    """把 '16384 MiB' / '16 GB' / '17179869184' 统一换算成 MiB。"""
    if not raw:
        return 0
    s = raw.strip().replace(",", "")
    m = re.search(r"([\d.]+)\s*(GB|GiB|MB|MiB|KB|B)?", s, re.IGNORECASE)
    if not m:
        return 0
    try:
        val = float(m.group(1))
    except ValueError:
        return 0
    unit = (m.group(2) or "").upper()
    if unit in ("GB", "GIB"):
        return int(val * 1024)
    if unit in ("MB", "MIB"):
        return int(val)
    if unit == "KB":
        return int(val / 1024)
    # 裸数字：字节数（sysfs 常见）
    if val > 1_000_000:
        return int(val / (1024 * 1024))
    return int(val)


# ---------------------------------------------------------------------------
# 各厂商管理工具探测
# ---------------------------------------------------------------------------


async def _detect_moorethreads() -> list[GPUInfo]:
    """摩尔线程：mthreads-smi（MUSA 生态）。支持 --json 时优先解析 JSON。"""
    binpath = _which_any(("mthreads-smi", "musa-smi"))
    if not binpath:
        return []
    text = await _run([binpath, "--json"])
    if text.strip().startswith("{"):
        with contextlib.suppress(json.JSONDecodeError):
            data = json.loads(text)
            return _parse_generic_json(data, "moorethreads")
    # 退化：文本表格
    text = await _run([binpath])
    if not text:
        return []
    out: list[GPUInfo] = []
    idx = 0
    for line in text.splitlines():
        if not re.search(r"MTT|Moore", line, re.IGNORECASE):
            continue
        vram = 0
        m = re.search(r"(\d+)\s*MiB", line)
        if m:
            vram = int(m.group(1))
        name = line.split()[0] if line.split() else "Moore Threads GPU"
        out.append(GPUInfo(vendor="moorethreads", index=idx, name=name, vram_mb=vram))
        idx += 1
    return out


async def _detect_iluvatar() -> list[GPUInfo]:
    """天数智芯 CoreX：ix-smi。"""
    binpath = _which_any(("ix-smi",))
    if not binpath:
        return []
    text = await _run([binpath, "--json"]) or await _run([binpath])
    if not text:
        return []
    if text.strip().startswith("{"):
        with contextlib.suppress(json.JSONDecodeError):
            return _parse_generic_json(json.loads(text), "iluvatar")
    out: list[GPUInfo] = []
    idx = 0
    for line in text.splitlines():
        if not re.search(r"Iluvatar|CoreX|IX", line, re.IGNORECASE):
            continue
        vram = _to_mb(line)
        out.append(
            GPUInfo(vendor="iluvatar", index=idx, name=line.split()[0], vram_mb=vram)
        )
        idx += 1
    return out


async def _detect_cambricon() -> list[GPUInfo]:
    """寒武纪 MLU：cnmon（文本输出）。"""
    binpath = _which_any(("cnmon",))
    if not binpath:
        return []
    text = await _run([binpath])
    if not text:
        return []
    out: list[GPUInfo] = []
    idx = 0
    for line in text.splitlines():
        if not re.search(r"MLU|Cambricon", line, re.IGNORECASE):
            continue
        vram = _to_mb(line)
        out.append(
            GPUInfo(vendor="cambricon", index=idx, name="Cambricon MLU", vram_mb=vram)
        )
        idx += 1
    return out


async def _detect_hygon() -> list[GPUInfo]:
    """海光 DCU：hy-smi，或复用 rocm-smi（DCU 走类 ROCm 接口）。"""
    binpath = _which_any(("hy-smi",))
    if binpath:
        text = await _run([binpath, "--json"]) or await _run([binpath])
        if text.strip().startswith("{"):
            with contextlib.suppress(json.JSONDecodeError):
                return _parse_generic_json(json.loads(text), "hygon")
        if text:
            out: list[GPUInfo] = []
            idx = 0
            for line in text.splitlines():
                if not re.search(r"DCU|Hygon|Z100|K100", line, re.IGNORECASE):
                    continue
                out.append(
                    GPUInfo(
                        vendor="hygon",
                        index=idx,
                        name="Hygon DCU",
                        vram_mb=_to_mb(line),
                    )
                )
                idx += 1
            if out:
                return out
    # 兜底：rocm-smi 可能就是 DCU 的管理工具，按名称区分
    rocm = _which_any(("rocm-smi",)) or "/opt/rocm/bin/rocm-smi"
    if not os.path.isfile(rocm):
        return []
    text = await _run([rocm, "--json"])
    if not text.strip().startswith("{"):
        return []
    with contextlib.suppress(json.JSONDecodeError):
        data = json.loads(text)
        cards = _parse_generic_json(data, "amd")
        # 只有名字里带 DCU/Hygon 才改判为海光，避免误伤真 AMD 卡
        out = []
        for g in cards:
            if re.search(r"DCU|Hygon", g.name, re.IGNORECASE):
                out.append(
                    GPUInfo(
                        vendor="hygon",
                        index=g.index,
                        name=g.name,
                        vram_mb=g.vram_mb,
                        raw=g.raw,
                    )
                )
        return out
    return []


async def _detect_intel() -> list[GPUInfo]:
    """Intel 独显 / 数据中心 GPU / 集成显卡。

    优先 xpu-smi（数据中心 Max 系列），其次 sysfs（Linux 集成显卡显存兜底）。
    """
    out: list[GPUInfo] = []
    binpath = _which_any(("xpu-smi",))
    if binpath:
        text = await _run([binpath, "dump", "-d", "0", "-m", "0,1,2"])
        if not text:
            text = await _run([binpath, "--json"])
        if text.strip().startswith("{"):
            with contextlib.suppress(json.JSONDecodeError):
                out.extend(_parse_generic_json(json.loads(text), "intel"))
        elif text:
            idx = 0
            for line in text.splitlines():
                if not re.search(r"Intel|Max|Arc|Data Center GPU", line, re.IGNORECASE):
                    continue
                out.append(
                    GPUInfo(
                        vendor="intel",
                        index=idx,
                        name="Intel GPU",
                        vram_mb=_to_mb(line),
                    )
                )
                idx += 1
    if out:
        return out
    return out


# ---------------------------------------------------------------------------
# 系统兜底：即使没装厂商工具也能识别
# ---------------------------------------------------------------------------

#: lspci / WMI 名称关键字 -> vendor
#:
#: 注意：必须加词边界 \b。否则在 IGNORECASE 下 "Intel Corpor**ati**on" 会被
#: `ATI` 命中而误判成 AMD —— 这个坑是真的踩过。
_SYSTEM_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bNVIDIA\b", "nvidia"),
    (r"\bAMD\b|\bATI\b|Radeon", "amd"),
    (r"\bIntel\b.*\b(Arc|Xe|Iris|UHD|HD Graphics|Data Center GPU|Max)\b", "intel"),
    (r"Moore\s*Threads|\bMTT\b", "moorethreads"),
    (r"Iluvatar|CoreX", "iluvatar"),
    (r"Cambricon|\bMLU\b", "cambricon"),
    (r"Hygon|\bDCU\b", "hygon"),
    (r"Huawei|Ascend", "ascend"),
    (r"\bApple\b", "apple"),
)


def _classify(name: str) -> str | None:
    for pat, vendor in _SYSTEM_PATTERNS:
        if re.search(pat, name, re.IGNORECASE):
            return vendor
    return None


def _sysfs_gpu_names() -> list[str]:
    """Linux: /sys/class/drm 里各设备的 vendor/device 名。"""
    names: list[str] = []
    base = "/sys/class/drm"
    if not os.path.isdir(base):
        return names
    for entry in sorted(os.listdir(base)):
        if not entry.startswith("card"):
            continue
        for f in ("device/vendor", "device/device"):
            p = os.path.join(base, entry, f)
            with contextlib.suppress(OSError), open(p, encoding="utf-8") as fh:
                names.append(fh.read().strip())
    return names


def _lspci_text() -> str:
    if platform.system().lower() != "linux":
        return ""
    binpath = shutil.which("lspci")
    if not binpath:
        return ""
    try:
        import subprocess

        return subprocess.run(
            [binpath, "-nn"],  # noqa: S603 — 固定参数
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        ).stdout
    except Exception:
        return ""


def _windows_gpu_names() -> list[str]:
    """Windows: 用 PowerShell 读 Win32_VideoController（无需额外依赖）。"""
    if platform.system().lower() != "windows":
        return []
    try:
        import subprocess

        ps = (
            "Get-CimInstance Win32_VideoController | "
            "Select-Object -ExpandProperty Name"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],  # noqa: S603,S607
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        ).stdout
        return [ln.strip() for ln in out.splitlines() if ln.strip()]
    except Exception:
        return []


def detect_by_system() -> list[GPUInfo]:
    """纯系统信息兜底识别（同步、无子进程超时风险）。

    只在厂商管理工具全部失败时作为最后手段，避免与工具结果重复。
    """
    candidates: list[str] = []

    text = _lspci_text()
    if text:
        for line in text.splitlines():
            # 只关心显示/加速类设备
            if not re.search(
                r"VGA|3D|Display|Acceler|Processing", line, re.IGNORECASE
            ):
                continue
            candidates.append(line)
    candidates.extend(_windows_gpu_names())

    out: list[GPUInfo] = []
    idx = 0
    seen: set[str] = set()
    for line in candidates:
        vendor = _classify(line)
        if not vendor:
            continue
        name = line.split(":", 2)[-1].strip() if ":" in line else line.strip()
        name = re.sub(r"\[[0-9a-f]{4}:[0-9a-f]{4}\]", "", name).strip()
        # 去重键必须基于"厂商 + 清理后的型号"，不能带 PCI 地址前缀，
        # 否则同一张卡的多个 lspci 条目（如 VGA + Audio 两个 function）会被算成两张。
        key = f"{vendor}:{name}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            GPUInfo(
                vendor=vendor,
                index=idx,
                name=name[:80] or VENDOR_LABELS.get(vendor, vendor),
                vram_mb=0,  # 系统兜底拿不到显存，留 0 由上层按型号估算
                raw={"system_fallback": True},
            )
        )
        idx += 1
    return out


# ---------------------------------------------------------------------------
# 通用 JSON 解析（各厂商 --json 结构差异很大，做宽松匹配）
# ---------------------------------------------------------------------------


def _parse_generic_json(data: Any, vendor: str) -> list[GPUInfo]:
    """宽松解析厂商 JSON：递归找含 name / memory 字样的字典。"""
    out: list[GPUInfo] = []
    idx = 0

    def walk(node: Any) -> None:
        nonlocal idx
        if isinstance(node, dict):
            keys = {str(k).lower() for k in node.keys()}
            has_name = any("name" in k or "model" in k or "product" in k for k in keys)
            has_mem = any(
                "memory" in k or "vram" in k or "mem" in k for k in keys
            )
            if has_name and has_mem:
                name = ""
                vram = 0
                for k, v in node.items():
                    kk = str(k).lower()
                    if not name and ("name" in kk or "model" in kk or "product" in kk):
                        name = str(v)
                    if not vram and ("memory" in kk or "vram" in kk):
                        vram = _to_mb(str(v))
                if name:
                    out.append(
                        GPUInfo(vendor=vendor, index=idx, name=name, vram_mb=vram, raw=node)
                    )
                    idx += 1
                    return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


async def detect_accelerators() -> list[GPUInfo]:
    """探测国产加速卡 + Intel。永不抛异常。"""
    results = await asyncio.gather(
        _detect_moorethreads(),
        _detect_iluvatar(),
        _detect_cambricon(),
        _detect_hygon(),
        _detect_intel(),
        return_exceptions=True,
    )
    out: list[GPUInfo] = []
    for r in results:
        if isinstance(r, BaseException):
            _debug("accelerator probe failed: %s", r)
            continue
        out.extend(r)
    return out
