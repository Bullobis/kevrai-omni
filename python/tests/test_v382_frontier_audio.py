"""Regression tests for frontier audio + sub-2-bit compression additions."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
_BY_ID = {m["id"]: m for m in _MODELS}


def test_total_grew_to_211():
    assert len(_MODELS) == 211


def test_ternary_bonsai_requires_prismml_runtime_note():
    m = _BY_ID["ternary-bonsai-2-27b"]
    assert m["repo"] == "prism-ml/Ternary-Bonsai-2-27B-gguf"
    assert m["license"] == "Apache-2.0"
    # the fork requirement must be surfaced so users are not stuck
    assert "PrismML" in m["description"] or "PrismML" in m["hardware"]["notes"]
    q = m["quantizations"]
    rec = [x for x in q if x["recommended"]]
    assert rec and rec[0]["id"] == "PQ2_0"


def test_ternary_bonsai_is_multimodal():
    m = _BY_ID["ternary-bonsai-2-27b"]
    assert m["modality"]["multimodal"] is True
    assert "image" in m["modality"]["understand"]


def test_auk_flash_mit_and_multisource():
    m = _BY_ID["auk-flash"]
    assert m["license"] == "MIT"
    hosts = {s.split("/")[2] for s in m["sources"]}
    assert len(hosts) >= 2  # Hugging Face + ModelScope


def test_auk_flash_audio_generation():
    m = _BY_ID["auk-flash"]
    assert "audio" in m["modality"]["generate"]


def test_nemotron_diarization_audio_to_speakers():
    m = _BY_ID["nemotron-3-diarization"]
    assert m["repo"] == "nvidia/Nemotron-3-Diarization"
    assert "audio" in m["modality"]["understand"]
    assert m["size_gb"] <= 1
