"""Regression test for corrected GLM-5.3-Flash quantizations."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_BY_ID = {
    m["id"]: m
    for m in json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
}
_ORDER = ["Q2_K", "Q3_K_M", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0", "F16"]
_RANK = {k: i for i, k in enumerate(_ORDER)}


def test_glm53flash_quants():
    q = _BY_ID["glm-5.3-flash"]["quantizations"]
    ids = [x["id"] for x in q]
    assert ids == _ORDER
    sizes = [x["size_gb"] for x in q]
    assert sizes == sorted(sizes)
    rec = [x for x in q if x["recommended"]]
    assert len(rec) == 1 and rec[0]["id"] == "Q4_K_M"
    # full precision reflects the ~320B MoE
    assert sizes[-1] > 640
    for x in q:
        assert x["vram_gb"] >= x["size_gb"]
