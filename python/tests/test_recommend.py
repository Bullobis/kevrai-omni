"""Tests for ``app.recommend`` — hardware-aware model recommender.

These tests pin the *behavior contracts* the Agent's ``recommend_models`` tool
relies on (see ``app/agent/tools/catalog_tools.py``). They are deliberately
written against the documented fit tiers (perfect / good / tight / no), the
exclusion rules, and the ranking weights — not against coverage figures.
"""
from __future__ import annotations

import pytest

from app.recommend import _effective_vram, rate_model, recommend


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _hw(
    *,
    vram: float = 0.0,
    ram: float = 32.0,
    disk_free: float = 500.0,
    vendor: str = "nvidia",
    discrete: bool = True,
    bandwidth: float = 100.0,
) -> dict:
    """Build a hardware snapshot shaped like the sidecar's ``hardware_info``."""
    return {
        "gpu_vendor": vendor,
        "gpu_best_vram_gb": vram,
        "ram_total_gb": ram,
        "has_discrete_gpu": discrete,
        "bandwidth_mbps": bandwidth,
        "disk": {"free_gb": disk_free},
    }


def _model(
    *,
    mid: str = "m1",
    vram: float = 8.0,
    min_vram: float = 0.0,
    ram: float = 16.0,
    disk: float = 10.0,
    size: float = 10.0,
    engine: list | None = None,
    category: str = "llm",
    trending: bool = False,
) -> dict:
    """Build a catalog model with a `hardware` block."""
    return {
        "id": mid,
        "name": mid,
        "category": category,
        "size_gb": size,
        "engine": ["llama.cpp"] if engine is None else engine,
        "trending": trending,
        "hardware": {
            "vram_gb": vram,
            "min_vram_gb": min_vram,
            "ram_gb": ram,
            "disk_gb": disk,
        },
    }


# ---------------------------------------------------------------------------
# _effective_vram
# ---------------------------------------------------------------------------
def test_effective_vram_discrete_uses_gpu_vram():
    assert _effective_vram(_hw(vram=12.0)) == 12.0


def test_effective_vram_apple_uses_70pct_unified_memory():
    # Apple Silicon: GPU may use ~70% of physical RAM, ignoring gpu_best_vram.
    hw = _hw(vram=999.0, ram=64.0, vendor="apple")
    assert _effective_vram(hw) == pytest.approx(64.0 * 0.7)


def test_effective_vram_no_gpu_is_zero():
    assert _effective_vram(_hw(vram=0.0)) == 0.0


# ---------------------------------------------------------------------------
# rate_model — happy path tiers
# ---------------------------------------------------------------------------
def test_rate_model_perfect_when_vram_headroom_is_ample():
    r = rate_model(_model(vram=8.0, min_vram=5.0, ram=16.0, disk=10.0), _hw(vram=16.0, disk_free=100.0))
    assert r["fit"] == "perfect"
    assert r["need"]["vram_gb"] == 8.0
    assert r["disk_note"] == "磁盘剩余 100GB 充足"


def test_rate_model_good_when_between_min_and_recommended():
    # vram(6) >= min_vram(5) but < recommended(8) → quantisation can bridge it.
    r = rate_model(_model(vram=8.0, min_vram=5.0, ram=16.0, disk=10.0), _hw(vram=6.0, disk_free=100.0))
    assert r["fit"] == "good"
    assert "量化可跑" in r["reasons"][0]


def test_rate_model_perfect_vram_downgrades_to_good_when_disk_is_merely_sufficient():
    # disk_free(10) >= need(10) but < need*1.2(12) → exactly-enough downgrade.
    r = rate_model(_model(vram=8.0, min_vram=5.0, ram=16.0, disk=10.0), _hw(vram=16.0, disk_free=10.0))
    assert r["fit"] == "good"
    assert r["disk_note"] == "磁盘剩余 10GB 刚好够（建议预留更多空间）"


def test_rate_model_good_when_vram_requirement_is_zero():
    # A model with no VRAM requirement at all is "good" regardless of GPU.
    m = _model(vram=0.0, min_vram=0.0, ram=8.0, disk=10.0)
    r = rate_model(m, _hw(vram=8.0, ram=32.0, disk_free=100.0))
    assert r["fit"] == "good"


def test_rate_model_good_when_vram_requirement_is_zero_and_ram_is_zero():
    # need_vram <= 0 with no RAM at all also lands on the `good` default (the
    # `elif need_vram <= 0` arm, which the CPU-fallback branch cannot catch).
    m = _model(vram=0.0, min_vram=0.0, ram=0.0, disk=0.0, size=0.0)
    hw = _hw(vram=0.0, ram=0.0, disk_free=0.0, discrete=False)
    r = rate_model(m, hw)
    assert r["fit"] == "good"


def test_rate_model_tightens_when_ram_well_below_requirement_on_gpu_path():
    # vram path says "perfect", but system RAM < need_ram*0.6 → tightened to
    # "tight" with an explicit reason (the pure-CPU safety net).
    m = _model(vram=8.0, min_vram=5.0, ram=16.0, disk=10.0)
    hw = _hw(vram=16.0, ram=8.0, disk_free=100.0)  # 8 < 16*0.6 = 9.6
    r = rate_model(m, hw)
    assert r["fit"] == "tight"
    assert any("低于推荐" in x for x in r["reasons"])


def test_rate_model_ram_shortfall_does_not_soften_a_tight_rating():
    # When the rating is already "tight", the RAM safety net leaves it alone.
    m = _model(vram=8.0, min_vram=5.0, ram=16.0, disk=10.0)
    hw = _hw(vram=6.0, ram=8.0, disk_free=100.0)  # vram → good, then ram net
    r = rate_model(m, hw)
    assert r["fit"] == "good"
    assert any("低于推荐" in x for x in r["reasons"])


# ---------------------------------------------------------------------------
# rate_model — CPU fallback branch
# ---------------------------------------------------------------------------
def test_rate_model_cpu_fallback_good_when_ram_has_headroom():
    # No usable VRAM, but RAM >= need_ram * 1.5 → comfortable CPU inference.
    m = _model(vram=8.0, min_vram=5.0, ram=16.0, disk=10.0)
    hw = _hw(vram=0.0, ram=32.0, disk_free=100.0, discrete=False)
    r = rate_model(m, hw)
    assert r["fit"] == "good"
    assert "CPU 推理" in r["reasons"][0]


def test_rate_model_cpu_fallback_tight_when_ram_is_barely_enough():
    # need_ram*1.1 <= ram < need_ram*1.5 → works but tight.
    m = _model(vram=8.0, min_vram=5.0, ram=16.0, disk=10.0)
    hw = _hw(vram=0.0, ram=18.0, disk_free=100.0, discrete=False)
    r = rate_model(m, hw)
    assert r["fit"] == "tight"
    assert "余量不大" in r["reasons"][0]


def test_rate_model_no_when_neither_vram_nor_ram_suffices():
    m = _model(vram=32.0, min_vram=24.0, ram=64.0, disk=10.0)
    hw = _hw(vram=0.0, ram=16.0, disk_free=100.0, discrete=False)
    r = rate_model(m, hw)
    assert r["fit"] == "no"


# ---------------------------------------------------------------------------
# rate_model — disk is a hard gate
# ---------------------------------------------------------------------------
def test_rate_model_no_when_disk_below_requirement():
    # vram is perfect, but disk_free(4) < need_disk(10) → "no" wins.
    m = _model(vram=8.0, min_vram=5.0, ram=16.0, disk=10.0)
    r = rate_model(m, _hw(vram=16.0, disk_free=4.0))
    assert r["fit"] == "no"
    assert r["disk_note"] == "磁盘剩余 4GB < 需 10GB"


def test_rate_model_no_when_disk_below_requirement_even_with_cpu_fallback():
    m = _model(vram=8.0, ram=16.0, disk=10.0)
    hw = _hw(vram=0.0, ram=64.0, disk_free=1.0, discrete=False)
    r = rate_model(m, hw)
    assert r["fit"] == "no"


# ---------------------------------------------------------------------------
# rate_model — need_ram derivation from disk when `ram_gb` is absent
# ---------------------------------------------------------------------------
def test_rate_model_derives_need_ram_from_disk_when_missing():
    m = _model(vram=8.0, ram=16.0, disk=10.0)
    m["hardware"]["ram_gb"] = 0  # force the `disk * 1.2` fallback
    r = rate_model(m, _hw(vram=16.0, disk_free=100.0))
    assert r["need"]["ram_gb"] == pytest.approx(12.0)


def test_rate_model_uses_size_gb_for_disk_when_hardware_disk_absent():
    m = _model(vram=8.0, ram=16.0, disk=0.0, size=10.0)
    r = rate_model(m, _hw(vram=16.0, disk_free=100.0))
    assert r["need"]["disk_gb"] == pytest.approx(10.0)


def test_rate_model_defaults_min_vram_to_60pct_of_recommended():
    m = _model(vram=10.0, min_vram=0.0, ram=16.0, disk=10.0)
    # vram 6 >= 10*0.6 = 6 → good
    r = rate_model(m, _hw(vram=6.0, disk_free=100.0))
    assert r["fit"] == "good"


# ---------------------------------------------------------------------------
# rate_model — no-discrete-GPU LLM downgrade
# ---------------------------------------------------------------------------
def test_rate_model_downgrades_big_llm_on_cpu_only_machine():
    # Apple unified memory gives enough "vram", but no discrete GPU + big
    # min_vram → perfect is downgraded to good.
    m = _model(vram=32.0, min_vram=24.0, ram=48.0, disk=20.0, category="llm")
    hw = _hw(vram=0.0, ram=64.0, disk_free=200.0, vendor="apple", discrete=False)
    r = rate_model(m, hw)
    assert r["fit"] == "good"
    assert any("无独显" in x for x in r["reasons"])


# ---------------------------------------------------------------------------
# recommend — exclusion rules
# ---------------------------------------------------------------------------
def test_recommend_excludes_no_fit_models():
    hw = _hw(vram=0.0, ram=4.0, disk_free=1.0, discrete=False)
    big = _model(mid="big", vram=32.0, min_vram=24.0, ram=64.0, disk=100.0)
    assert rate_model(big, hw)["fit"] == "no"
    assert recommend([big], hw) == []


def test_recommend_excludes_pending_category():
    hw = _hw(vram=16.0)
    pending = _model(mid="p", category="pending")
    # Even though it would otherwise rate fine, pending models are dropped.
    assert rate_model(pending, hw)["fit"] in ("perfect", "good")
    assert recommend([pending], hw) == []


def test_recommend_excludes_models_without_engine():
    hw = _hw(vram=16.0)
    assert recommend([_model(mid="no-engine", engine=[])], hw) == []


def test_recommend_category_filter_keeps_only_requested_category():
    hw = _hw(vram=16.0)
    llm = _model(mid="l", category="llm")
    tts = _model(mid="t", category="tts")
    out = recommend([llm, tts], hw, category="tts")
    assert [m["id"] for m in out] == ["t"]


def test_recommend_attaches_recommendation_block():
    hw = _hw(vram=16.0)
    out = recommend([_model(mid="a")], hw)
    assert len(out) == 1
    rec = out[0]["recommendation"]
    assert rec["fit"] == "perfect"
    assert set(rec) == {"score", "fit", "reasons", "disk_note", "need"}


# ---------------------------------------------------------------------------
# recommend — limit
# ---------------------------------------------------------------------------
def test_recommend_respects_limit():
    hw = _hw(vram=16.0)
    models = [_model(mid=f"m{i}", size=10.0) for i in range(10)]
    assert len(recommend(models, hw, limit=3)) == 3


def test_recommend_limit_below_one_still_returns_one():
    hw = _hw(vram=16.0)
    out = recommend([_model(mid="a"), _model(mid="b")], hw, limit=0)
    assert len(out) == 1


# ---------------------------------------------------------------------------
# recommend — ranking
# ---------------------------------------------------------------------------
def test_recommend_ranks_better_fit_first():
    hw = _hw(vram=10.0, disk_free=100.0)
    good = _model(mid="good", vram=18.0, min_vram=12.0, ram=16.0, disk=10.0)
    perfect = _model(mid="perfect", vram=8.0, min_vram=5.0, ram=16.0, disk=10.0)
    # Sanity-check the chosen fixtures.
    assert rate_model(good, hw)["fit"] == "good"
    assert rate_model(perfect, hw)["fit"] == "perfect"
    out = recommend([good, perfect], hw)
    assert [m["id"] for m in out] == ["perfect", "good"]
    assert out[0]["recommendation"]["score"] > out[1]["recommendation"]["score"]


def test_recommend_video_gets_smaller_bonus_than_llm():
    # Category preference ladder: llm(+15) > video(+8) > everything else.
    hw = _hw(vram=16.0)
    video = _model(mid="video", category="video")
    image = _model(mid="image", category="image")
    out = recommend([image, video], hw)
    assert [m["id"] for m in out] == ["video", "image"]
    assert out[0]["recommendation"]["score"] - out[1]["recommendation"]["score"] == 8


def test_recommend_prefers_llm_over_other_categories_at_equal_fit():
    hw = _hw(vram=16.0)
    llm = _model(mid="llm", category="llm")
    image = _model(mid="image", category="image")
    out = recommend([image, llm], hw)
    assert [m["id"] for m in out] == ["llm", "image"]


def test_recommend_trending_weight_breaks_tie_towards_trending_model():
    hw = _hw(vram=16.0)
    plain = _model(mid="plain")
    hot = _model(mid="hot", trending=True)
    out = recommend([plain, hot], hw)
    assert out[0]["id"] == "hot"
    assert out[0]["recommendation"]["score"] - out[1]["recommendation"]["score"] == 10


def test_recommend_prefers_medium_sized_model_on_slow_bandwidth():
    hw = _hw(vram=16.0, bandwidth=20.0)
    small = _model(mid="small", size=10.0)      # 5-25GB → +8 (slow) +6 (medium)
    huge = _model(mid="huge", size=300.0)       # >200GB → -10 (slow)
    out = recommend([huge, small], hw)
    assert [m["id"] for m in out] == ["small", "huge"]
    assert out[0]["recommendation"]["score"] > out[1]["recommendation"]["score"]


def test_recommend_multi_engine_model_gets_bonus():
    hw = _hw(vram=16.0)
    single = _model(mid="single", engine=["llama.cpp"])
    multi = _model(mid="multi", engine=["llama.cpp", "mnn"])
    out = recommend([single, multi], hw)
    assert out[0]["id"] == "multi"
    assert out[0]["recommendation"]["score"] - out[1]["recommendation"]["score"] == 4


def test_recommend_size_tiebreak_prefers_smaller_model():
    """A score tie must resolve to the **smaller** model.

    Regression guard for a sign inversion: the tuple stores ``-size`` and the
    sort key is ``(-score, -size)``. The key previously read ``(-t[0], t[1])``,
    which skipped the second negation and therefore sorted the *largest* model
    first — the opposite of the module docstring's "体积适中 / 小模型加分"
    intent, and inconsistent with the ``-size`` already computed at append
    time. The bug was deterministic (not an unstable-sort artefact), so the
    ordering below is asserted in both input orders.
    """
    hw = _hw(vram=16.0, bandwidth=100.0)
    big = _model(mid="big", size=28.0)
    small = _model(mid="small", size=27.0)
    out = recommend([big, small], hw)
    assert out[0]["recommendation"]["score"] == out[1]["recommendation"]["score"] == 115
    assert [m["id"] for m in out] == ["small", "big"]
    # Order must not depend on input order.
    assert [m["id"] for m in recommend([small, big], hw)] == ["small", "big"]
