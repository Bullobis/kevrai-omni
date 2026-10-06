"""Regression tests for corrected frontier MoE quantizations (V3/V3.1/V4-Flash/Qwen3.5-397)."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_BY_ID = {
    m["id"]: m
    for m in json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
}
_ORDER = ["Q2_K", "Q3_K_M", "IQ4_NL", "Q4_K", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0", "F16"]
_RANK = {k: i for i, k in enumerate(_ORDER)}
_FIXED = {
    "deepseek-v3": 6,
    "deepseek-v3.1": 7,
    "deepseek-v4-flash": 4,
    "deepseek-v4-flash-0731": 4,
    "qwen3.5-397b-a17b": 6,
}


def test_quant_counts():
    for mid, n in _FIXED.items():
        assert len(_BY_ID[mid]["quantizations"]) == n, mid


def test_ordered_monotonic_one_recommended():
    for mid in _FIXED:
        q = _BY_ID[mid]["quantizations"]
        ranks = [_RANK[x["id"]] for x in q]
        assert ranks == sorted(ranks), mid
        sizes = [x["size_gb"] for x in q]
        assert sizes == sorted(sizes), mid
        rec = [x for x in q if x["recommended"]]
        assert len(rec) == 1 and rec[0]["id"] == "Q4_K_M", mid
        for x in q:
            assert x["vram_gb"] >= x["size_gb"]


def test_no_tiny_f16_artifact():
    # the old V4-Flash-0731 bug was an ~10GB file labelled F16 on a 160GB-class model
    f16 = [x for x in _BY_ID["deepseek-v4-flash-0731"]["quantizations"] if x["id"] == "F16"]
    assert not f16


def test_v31_has_full_precision():
    f16 = next(x for x in _BY_ID["deepseek-v3.1"]["quantizations"] if x["id"] == "F16")
    assert f16["size_gb"] > 1300
