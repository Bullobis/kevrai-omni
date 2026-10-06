"""Regression tests for vision/reasoning/code fill: Qwen2.5-VL-7B, Llama-3.2-11B-Vision, Phi-4-reasoning, Devstral Small 2."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
_BY_ID = {m["id"]: m for m in _MODELS}
_RANK = {"Q3_K_M": 0, "IQ4_NL": 1, "Q4_K_M": 2, "Q5_K_M": 3, "Q8_0": 4, "F16": 5}
_NEW = ("qwen2.5-vl-7b", "llama-3.2-11b-vision", "phi-4-reasoning", "devstral-small-2-24b")


def test_total_grew():
    assert len(_MODELS) >= 217


def test_ids_and_repos():
    assert _BY_ID["qwen2.5-vl-7b"]["repo"] == "Qwen/Qwen2.5-VL-7B-Instruct"
    assert _BY_ID["llama-3.2-11b-vision"]["gguf_repo"] == "leafspark/Llama-3.2-11B-Vision-Instruct-GGUF"
    assert _BY_ID["phi-4-reasoning"]["repo"] == "microsoft/Phi-4-reasoning"
    assert _BY_ID["devstral-small-2-24b"]["repo"] == "mistralai/Devstral-Small-2-24B-Instruct-2512"


def test_vision_models_multimodal():
    for mid in ("qwen2.5-vl-7b", "llama-3.2-11b-vision"):
        mod = _BY_ID[mid]["modality"]
        assert mod["multimodal"] and "image" in mod["understand"]


def test_licenses():
    assert _BY_ID["qwen2.5-vl-7b"]["license"] == "apache-2.0"
    assert _BY_ID["llama-3.2-11b-vision"]["license"] == "llama3.2"
    assert _BY_ID["phi-4-reasoning"]["license"] == "mit"
    assert _BY_ID["devstral-small-2-24b"]["license"] == "apache-2.0"


def test_quants_ordered_monotonic_one_recommended_vram_ge_size():
    for mid in _NEW:
        q = _BY_ID[mid]["quantizations"]
        assert [_RANK[x["id"]] for x in q] == sorted([_RANK[x["id"]] for x in q])
        sizes = [x["size_gb"] for x in q]
        assert sizes == sorted(sizes)
        assert len([x for x in q if x["recommended"]]) == 1
        for x in q:
            assert x["vram_gb"] >= x["size_gb"]
