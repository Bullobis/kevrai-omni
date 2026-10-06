"""Regression tests for the corrected Kimi-K2.6 GGUF quantizations."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_BY_ID = {
    m["id"]: m
    for m in json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
}
_RANK = {"Q2_K": 0, "Q3_K_M": 1, "Q4_K_M": 2, "Q5_K_M": 3, "Q6_K": 4, "Q8_0": 5, "F16": 6}


def test_k26_gguf_repo():
    m = _BY_ID["kimi-k2.6"]
    assert m["gguf_repo"] == "unsloth/Kimi-K2.6-GGUF"


def test_k26_quants_are_practical_and_ordered():
    q = _BY_ID["kimi-k2.6"]["quantizations"]
    ids = [x["id"] for x in q]
    assert ids == ["Q2_K", "Q4_K_M", "Q8_0", "F16"]
    assert [_RANK[i] for i in ids] == sorted([_RANK[i] for i in ids])
    sizes = [x["size_gb"] for x in q]
    assert sizes == sorted(sizes)
    # full-precision reflects the ~1T MoE
    assert sizes[-1] > 2000
    # recommended is the practical Q4, not a multi-TB full-precision file
    rec = [x for x in q if x["recommended"]]
    assert len(rec) == 1 and rec[0]["id"] == "Q4_K_M"
    assert rec[0]["size_gb"] < 600
    for x in q:
        assert x["vram_gb"] >= x["size_gb"]
