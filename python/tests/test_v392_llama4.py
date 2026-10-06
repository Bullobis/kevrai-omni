"""Regression tests for the Llama 4 Scout / Maverick additions."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
_BY_ID = {m["id"]: m for m in _MODELS}


def test_total_grew():
    assert len(_MODELS) >= 210


def test_scout_repo_and_gguf():
    m = _BY_ID["llama-4-scout"]
    assert m["repo"] == "meta-llama/Llama-4-Scout-17B-16E-Instruct"
    assert m["gguf_repo"] == "unsloth/Llama-4-Scout-17B-16E-Instruct-GGUF"


def test_maverick_repo_and_gguf():
    m = _BY_ID["llama-4-maverick"]
    assert m["repo"] == "meta-llama/Llama-4-Maverick-17B-128E-Instruct"
    assert m["gguf_repo"] == "unsloth/Llama-4-Maverick-17B-128E-Instruct-GGUF"


def test_both_native_multimodal():
    for mid in ("llama-4-scout", "llama-4-maverick"):
        mod = _BY_ID[mid]["modality"]
        assert mod["multimodal"] is True
        assert "image" in mod["understand"]


def test_quantizations_monotonic_with_one_recommended():
    order = ["Q3_K_M", "IQ4_NL", "Q4_K_M", "Q5_K_M", "Q8_0", "F16"]
    for mid in ("llama-4-scout", "llama-4-maverick"):
        q = _BY_ID[mid]["quantizations"]
        assert [x["id"] for x in q] == order
        rec = [x for x in q if x["recommended"]]
        assert len(rec) == 1 and rec[0]["id"] == "Q4_K_M"
        sizes = [x["size_gb"] for x in q]
        assert sizes == sorted(sizes)


def test_maverick_larger_than_scout():
    def q4(mid):
        return next(x["size_gb"] for x in _BY_ID[mid]["quantizations"] if x["id"] == "Q4_K_M")
    assert q4("llama-4-maverick") > q4("llama-4-scout")
