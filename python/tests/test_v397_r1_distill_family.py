"""Regression tests for the R1 distill family additions (llama-8b, qwen-7b/1.5b, llama-70b)."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
_BY_ID = {m["id"]: m for m in _MODELS}
_RANK = {"Q3_K_M": 0, "IQ4_NL": 1, "Q4_K_M": 2, "Q5_K_M": 3, "Q8_0": 4, "F16": 5}
_NEW = ("r1-distill-llama-8b", "r1-distill-qwen-7b", "r1-distill-qwen-1.5b", "r1-distill-llama-70b")


def test_total_grew():
    assert len(_MODELS) >= 228


def test_repos_and_gguf():
    assert _BY_ID["r1-distill-llama-8b"]["repo"] == "deepseek-ai/DeepSeek-R1-Distill-Llama-8B"
    assert _BY_ID["r1-distill-qwen-7b"]["gguf_repo"] == "unsloth/DeepSeek-R1-Distill-Qwen-7B-GGUF"
    assert _BY_ID["r1-distill-qwen-1.5b"]["repo"] == "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"
    assert _BY_ID["r1-distill-llama-70b"]["gguf_repo"] == "unsloth/DeepSeek-R1-Distill-Llama-70B-GGUF"


def test_all_mit():
    for mid in _NEW:
        assert _BY_ID[mid]["license"] == "mit"


def test_quants_ordered_monotonic_one_recommended():
    for mid in _NEW:
        q = _BY_ID[mid]["quantizations"]
        ranks = [_RANK[x["id"]] for x in q]
        assert ranks == sorted(ranks), mid
        sizes = [x["size_gb"] for x in q]
        assert sizes == sorted(sizes), mid
        assert len([x for x in q if x["recommended"]]) == 1, mid
        for x in q:
            assert x["vram_gb"] >= x["size_gb"]


def test_size_ordering_across_family():
    def q4(mid):
        return next(x["size_gb"] for x in _BY_ID[mid]["quantizations"] if x["id"] == "Q4_K_M")
    assert q4("r1-distill-qwen-1.5b") < q4("r1-distill-qwen-7b") < q4("r1-distill-llama-70b")


def test_distill_llama70_distinct_repo_from_llama33():
    # same architecture/size as llama-3.3-70b but a different upstream repo
    assert _BY_ID["r1-distill-llama-70b"]["repo"] != _BY_ID["llama-3.3-70b"]["repo"]
    assert _BY_ID["r1-distill-llama-70b"]["gguf_repo"] != _BY_ID["llama-3.3-70b"]["gguf_repo"]
