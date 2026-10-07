"""Regression: Nex-N2.5-Pro static quantization sizes are decimal GB.

bartowski ships each quant as multi-part files under a ``<QUANT>/`` folder; the
real summed sizes are ~7.4% larger than the binary-GiB figures recorded
earlier. These static ``quantizations`` entries are catalog metadata, so the
test reads the raw JSON (the pydantic loader does not expose that field).
"""
from __future__ import annotations

import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent.parent / "catalog" / "models.json"


def _quants():
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    m = next(x for x in data["models"] if x["id"] == "nex-n2.5-pro")
    return {q["id"]: q for q in m["quantizations"]}


def test_nex_quant_sizes_match_decimal_reality():
    q = _quants()
    # actual multi-part sums (GB): Q3 189.95, Q4 250.83, Q5 305.61, Q6 349.8,
    # Q8 421.58 — allow tolerance for re-packs.
    assert 183 <= q["Q3_K_M"]["size_gb"] <= 197
    assert 244 <= q["Q4_K_M"]["size_gb"] <= 258
    assert 299 <= q["Q5_K_M"]["size_gb"] <= 312
    assert 343 <= q["Q6_K"]["size_gb"] <= 357
    assert 414 <= q["Q8_0"]["size_gb"] <= 429


def test_nex_quant_vram_covers_weights():
    for item in _quants().values():
        assert item["vram_gb"] > item["size_gb"]


def test_nex_old_gib_values_are_gone():
    q = _quants()
    assert q["Q4_K_M"]["size_gb"] != 233.61
    assert q["Q8_0"]["size_gb"] != 392.62
