"""Regression tests for R1-Distill-Qwen-14B and Ovis2-16B."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
_BY_ID = {m["id"]: m for m in _MODELS}
_RANK = {"Q3_K_M": 0, "IQ4_NL": 1, "Q4_K_M": 2, "Q5_K_M": 3, "Q8_0": 4, "F16": 5}


def test_total_grew():
    assert len(_MODELS) >= 224


def test_r1distill_repo_gguf_license():
    m = _BY_ID["r1-distill-qwen-14b"]
    assert m["repo"] == "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"
    assert m["gguf_repo"] == "unsloth/DeepSeek-R1-Distill-Qwen-14B-GGUF"
    assert m["license"] == "mit"


def test_ovis_repo_org_move_and_multimodal():
    m = _BY_ID["ovis2-16b"]
    assert m["repo"] == "ATH-MaaS/Ovis2-16B"
    assert "transformers" in m["engine"]
    assert m["modality"]["multimodal"] and "image" in m["modality"]["understand"]
    assert m["license"] == "apache-2.0"


def test_r1distill_quants():
    q = _BY_ID["r1-distill-qwen-14b"]["quantizations"]
    assert [_RANK[x["id"]] for x in q] == sorted([_RANK[x["id"]] for x in q])
    sizes = [x["size_gb"] for x in q]
    assert sizes == sorted(sizes)
    assert len([x for x in q if x["recommended"]]) == 1
    for x in q:
        assert x["vram_gb"] >= x["size_gb"]
