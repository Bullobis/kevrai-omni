"""Neuron3-Misc 审计回归测试 — ``app.recommend``。

审计结论：recommend.py 未发现真实 bug（评分/排序符号已在历史修复中校正并有钉板测试）。
本组测试把审计中实证过的边界条件固化为回归守卫，防止后续改动引入崩溃：
  * 无 GPU / 空 catalog / 空 hardware_info / 空模型字段 → 优雅降级，不抛异常、不除零
  * 显存为 0 / 负数 / 超大值 → 安全比较
  * 同分同体积 tie-break → 稳定（不依赖 dict 迭代顺序以外的随机性）
"""
from __future__ import annotations

from app.recommend import _effective_vram, rate_model, recommend


def _hw(**kw):
    base = {
        "gpu_vendor": "nvidia",
        "gpu_best_vram_gb": 16.0,
        "ram_total_gb": 32.0,
        "has_discrete_gpu": True,
        "bandwidth_mbps": 100.0,
        "disk": {"free_gb": 500.0},
    }
    base.update(kw)
    return base


def _m(**kw):
    base = {
        "id": "m",
        "category": "llm",
        "size_gb": 10.0,
        "engine": ["llama.cpp"],
        "hardware": {"vram_gb": 8.0, "min_vram_gb": 5.0, "ram_gb": 16.0, "disk_gb": 10.0},
    }
    base.update(kw)
    return base


# --- 空输入不崩溃 -----------------------------------------------------------
def test_recommend_empty_catalog_returns_empty():
    assert recommend([], _hw()) == []


def test_recommend_empty_hardware_info_returns_empty_or_graceful():
    # 无任何硬件信息：不应抛异常；磁盘未知 → 模型被磁盘门槛排除 → 空列表。
    out = recommend([_m()], {})
    assert isinstance(out, list)


def test_rate_model_completely_empty_inputs():
    r = rate_model({}, {})
    assert r["fit"] in ("perfect", "good", "tight", "no")
    assert r["need"] == {"vram_gb": 0.0, "ram_gb": 0.0, "disk_gb": 0.0}


# --- 极端硬件值 -------------------------------------------------------------
def test_effective_vram_zero_or_negative_is_zero():
    assert _effective_vram(_hw(gpu_best_vram_gb=0.0)) == 0.0
    assert _effective_vram(_hw(gpu_best_vram_gb=-99.0)) == 0.0


def test_rate_model_negative_reported_vram_does_not_crash():
    hw = _hw(gpu_best_vram_gb=-99.0)
    r = rate_model(_m(), hw)
    assert r["fit"] in ("perfect", "good", "tight", "no")


def test_rate_model_huge_vram_does_not_crash():
    hw = _hw(gpu_best_vram_gb=1e9, ram_total_gb=1e5, disk={"free_gb": 1e6})
    r = rate_model(_m(), hw)
    assert r["fit"] == "perfect"


# --- 无 GPU / 无已安装模型场景 ----------------------------------------------
def test_recommend_no_discrete_gpu_falls_back_to_cpu():
    hw = _hw(gpu_vendor="cpu", gpu_best_vram_gb=0.0, has_discrete_gpu=False)
    out = recommend([_m()], hw)
    # 32GB RAM 对 16GB 需求是 CPU 可行（good），不应被排除
    assert len(out) == 1
    assert out[0]["recommendation"]["fit"] in ("good", "tight")


def test_recommend_no_gpu_no_ram_excludes_model():
    hw = _hw(gpu_vendor="cpu", gpu_best_vram_gb=0.0, ram_total_gb=1.0,
             has_discrete_gpu=False, disk={"free_gb": 1.0})
    assert recommend([_m()], hw) == []


# --- tie-break 确定性 --------------------------------------------------------
def test_tie_identical_models_is_stable():
    hw = _hw()
    a = _m(id="a")
    b = _m(id="b")
    assert [x["id"] for x in recommend([a, b], hw)] == ["a", "b"]
    assert [x["id"] for x in recommend([b, a], hw)] == ["b", "a"]
