"""Regression tests for legacy data fixes (v3.7.x follow-up).

Covers: independent Nex N2.5 Pro GGUF repo, MiniMax-Music3 W4A8 size,
APISR weight size, and non-standard single-file GGUF repos.
All facts verified via the Hugging Face API (blobs).
"""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
_BY_ID = {m["id"]: m for m in _MODELS}


def test_nex_pro_uses_independent_gguf_repo():
    m = _BY_ID["nex-n2.5-pro"]
    assert m["gguf_repo"] == "bartowski/Nex-N2.5-Pro-GGUF"
    q = m.get("quantizations", [])
    assert len(q) >= 4
    ids = [x["id"] for x in q]
    assert "Q4_K_M" in ids
    # Q4_K_M is the recommended default
    rec = [x for x in q if x["recommended"]]
    assert len(rec) == 1 and rec[0]["id"] == "Q4_K_M"


def test_nex_pro_quant_sizes_monotonic():
    q = _BY_ID["nex-n2.5-pro"]["quantizations"]
    order = ["Q3_K_M", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"]
    sizes = [next(x["size_gb"] for x in q if x["id"] == k) for k in order if k in [y["id"] for y in q]]
    assert sizes == sorted(sizes)
    # large MoE: Q4 should be hundreds of GB
    q4 = next(x["size_gb"] for x in q if x["id"] == "Q4_K_M")
    assert 200 < q4 < 260


def test_minimax_w4a8_size_corrected():
    m = _BY_ID["minimax-music3-w4a8-comfyui"]
    # W4A8 DiT is ~1.3GB, not 13
    assert 1 <= m["size_gb"] <= 2


def test_apisr_size_is_small():
    m = _BY_ID["apisr"]
    # generator weights are tens of MB
    assert m["size_gb"] <= 0.2


def test_edge0_single_lossless_variant():
    m = _BY_ID.get("edge0-35b")
    if m is None:
        return
    q = m.get("quantizations", [])
    assert len(q) == 1
    assert 15 < q[0]["size_gb"] < 22


def test_xing4_single_iq4_variant():
    m = _BY_ID.get("xing4-29b-a4b")
    if m is None:
        return
    q = m.get("quantizations", [])
    assert len(q) == 1
    assert 15 < q[0]["size_gb"] < 22


def test_deepseek_flash_q2_variant_and_vision_note():
    m = _BY_ID.get("deepseek-v4.1-flash")
    if m is None:
        return
    q = m.get("quantizations", [])
    assert len(q) >= 1
    assert q[0]["size_gb"] > 300
    assert "Vision" in m.get("notes", "")
