"""Regression tests for multimodal/small/mobile/code batch: Phi-4-multimodal, Mistral-Small-3.2, Gemma-3n, Qwen2.5-VL-72B, Codestral."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
_BY_ID = {m["id"]: m for m in _MODELS}
_RANK = {"Q3_K_M": 0, "IQ4_NL": 1, "Q4_K_M": 2, "Q5_K_M": 3, "Q8_0": 4, "F16": 5}
_NEW = ("phi-4-multimodal", "mistral-small-3.2-24b", "gemma-3n-e4b", "qwen2.5-vl-72b", "codestral-22b")


def test_total_grew():
    assert len(_MODELS) >= 222


def test_repos():
    assert _BY_ID["phi-4-multimodal"]["repo"] == "microsoft/Phi-4-multimodal-instruct"
    assert _BY_ID["mistral-small-3.2-24b"]["gguf_repo"] == "unsloth/Mistral-Small-3.2-24B-Instruct-2506-GGUF"
    assert _BY_ID["gemma-3n-e4b"]["repo"] == "google/gemma-3n-E4B-it"
    assert _BY_ID["codestral-22b"]["gguf_repo"] == "bartowski/Codestral-22B-v0.1-GGUF"


def test_phi4mm_engines_and_multimodal():
    m = _BY_ID["phi-4-multimodal"]
    assert "transformers" in m["engine"] and not m.get("gguf_repo")
    mod = m["modality"]
    assert mod["multimodal"] and {"audio", "image"} <= set(mod["understand"])


def test_vision_entries_multimodal():
    for mid in ("mistral-small-3.2-24b", "gemma-3n-e4b", "qwen2.5-vl-72b"):
        assert _BY_ID[mid]["modality"]["multimodal"]


def test_codestral_license_flagged():
    # Codestral is the non-production license (other), must not be labelled permissive
    assert _BY_ID["codestral-22b"]["license"] == "other"


def test_quants_ordered_monotonic_one_recommended():
    for mid in _NEW:
        q = _BY_ID[mid].get("quantizations")
        if not q:
            continue
        assert [_RANK[x["id"]] for x in q] == sorted([_RANK[x["id"]] for x in q]), mid
        sizes = [x["size_gb"] for x in q]
        assert sizes == sorted(sizes), mid
        assert len([x for x in q if x["recommended"]]) == 1, mid
        for x in q:
            assert x["vram_gb"] >= x["size_gb"]
