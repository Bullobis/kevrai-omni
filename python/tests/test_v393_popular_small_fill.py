"""Regression tests for the verified gap fill: Phi-4-mini, Llama-3.3-70B, Mistral-Nemo-12B."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
_BY_ID = {m["id"]: m for m in _MODELS}
_QUANT_RANK = {"Q3_K_M": 0, "IQ4_NL": 1, "Q4_K_M": 2, "Q5_K_M": 3, "Q8_0": 4, "F16": 5}
_NEW = ("phi-4-mini", "llama-3.3-70b", "mistral-nemo-12b")


def test_total_grew():
    assert len(_MODELS) >= 213


def test_new_ids_present():
    for mid in _NEW:
        assert mid in _BY_ID


def test_repos_and_gguf():
    assert _BY_ID["phi-4-mini"]["repo"] == "microsoft/Phi-4-mini-instruct"
    assert _BY_ID["phi-4-mini"]["gguf_repo"] == "unsloth/Phi-4-mini-instruct-GGUF"
    assert _BY_ID["llama-3.3-70b"]["repo"] == "meta-llama/Llama-3.3-70B-Instruct"
    assert _BY_ID["llama-3.3-70b"]["gguf_repo"] == "unsloth/Llama-3.3-70B-Instruct-GGUF"
    assert _BY_ID["mistral-nemo-12b"]["repo"] == "mistralai/Mistral-Nemo-Instruct-2407"
    assert _BY_ID["mistral-nemo-12b"]["gguf_repo"] == "bartowski/Mistral-Nemo-Instruct-2407-GGUF"


def test_licenses():
    assert _BY_ID["phi-4-mini"]["license"] == "mit"
    assert _BY_ID["llama-3.3-70b"]["license"] == "llama3.3"
    assert _BY_ID["mistral-nemo-12b"]["license"] == "apache-2.0"


def test_quantizations_ordered_monotonic_single_recommended():
    for mid in _NEW:
        q = _BY_ID[mid]["quantizations"]
        ranks = [_QUANT_RANK[x["id"]] for x in q]
        assert ranks == sorted(ranks), mid
        sizes = [x["size_gb"] for x in q]
        assert sizes == sorted(sizes), mid
        rec = [x for x in q if x["recommended"]]
        assert len(rec) == 1 and rec[0]["id"] == "Q4_K_M", mid


def test_size_gb_consistent_with_f16_quant():
    # size_gb (base) should be at least as large as the F16 quant where present
    for mid in ("phi-4-mini", "llama-3.3-70b"):
        f16 = next(x["size_gb"] for x in _BY_ID[mid]["quantizations"] if x["id"] == "F16")
        assert abs(_BY_ID[mid]["size_gb"] - f16) <= 0.1
