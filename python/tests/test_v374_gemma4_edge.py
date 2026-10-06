"""Regression tests for Gemma 4 GGUF annotations + Edge E2B/E4B additions."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
_BY_ID = {m["id"]: m for m in _MODELS}


def test_total_grew_to_207():
    assert len(_MODELS) >= 207


def test_gemma4_12b_gguf_quant():
    m = _BY_ID["gemma-4-12b"]
    assert m["gguf_repo"] == "unsloth/gemma-4-12b-it-GGUF"
    q = m["quantizations"]
    assert len(q) >= 5
    rec = [x for x in q if x["recommended"]]
    assert rec and rec[0]["id"] == "Q4_K_M"


def test_gemma4_31b_gguf_quant():
    m = _BY_ID["gemma-4-31b"]
    assert m["gguf_repo"] == "unsloth/gemma-4-31B-it-GGUF"
    assert len(m["quantizations"]) >= 5


def test_gemma4_26b_gguf_quant():
    m = _BY_ID["gemma-4-26b-a4b"]
    assert m["gguf_repo"] == "unsloth/gemma-4-26B-A4B-it-GGUF"
    assert len(m["quantizations"]) >= 5


def test_gemma4_quant_monotonic():
    order = ["Q3_K_M", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0", "F16"]
    for mid in ("gemma-4-12b", "gemma-4-31b", "gemma-4-26b-a4b", "gemma-4-e2b", "gemma-4-e4b"):
        q = _BY_ID[mid]["quantizations"]
        have = [x["id"] for x in q]
        sizes = [next(x["size_gb"] for x in q if x["id"] == k) for k in order if k in have]
        assert sizes == sorted(sizes), mid


def test_edge_e2b_multimodal():
    m = _BY_ID["gemma-4-e2b"]
    assert m["modality"]["multimodal"] is True
    assert "image" in m["modality"]["understand"]
    assert m["gguf_repo"] == "unsloth/gemma-4-E2B-it-GGUF"


def test_edge_e4b_multimodal():
    m = _BY_ID["gemma-4-e4b"]
    assert m["modality"]["multimodal"] is True
    assert "image" in m["modality"]["understand"]
    assert m["gguf_repo"] == "unsloth/gemma-4-E4B-it-GGUF"
    # E4B larger than E2B at same quant
    def q4(mid):
        return next(x["size_gb"] for x in _BY_ID[mid]["quantizations"] if x["id"] == "Q4_K_M")
    assert q4("gemma-4-e4b") > q4("gemma-4-e2b")
