"""Regression tests for October 2026 frontier additions (PR data/frontier-oct26).

All facts verified via the Hugging Face API (blobs) and model READMEs.
"""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
_BY_ID = {m["id"]: m for m in _MODELS}


def test_total_grew_to_205():
    assert len(_MODELS) == 205


def test_pyannote_diarization_present():
    m = _BY_ID["pyannote-diarization-community-1"]
    assert m["repo"] == "pyannote/speaker-diarization-community-1"
    assert m["category"] == "audio"
    assert m["license"] == "CC-BY-4.0"
    assert "audio" in m["modality"]["understand"]


def test_gliner_present():
    m = _BY_ID["gliner2.5"]
    assert m["repo"] == "fastino/GLiNER2.5-Decide"
    assert m["license"] == "Apache-2.0"
    # weights ~1.8GB
    assert 1 <= m["size_gb"] <= 3


def test_clm_heads_and_base_dependency():
    m = _BY_ID["clm-v0.1-8b"]
    assert m["repo"] == "Contrastive-LM/CLM-v0.1-8B"
    # heads are tiny
    assert m["size_gb"] <= 0.5
    # note must mention the Qwen3-8B encoder dependency
    assert "Qwen3-8B" in m["hardware"]["notes"]


def test_frognano_present():
    m = _BY_ID["frognano-4b"]
    assert m["repo"] == "microsoft/FrogNano-4B-2609"
    assert m["license"] == "MIT"
    # 4B FP16 ~8.7GB
    assert 7 <= m["size_gb"] <= 10


def test_all_new_have_two_sources():
    for mid in ("pyannote-diarization-community-1", "gliner2.5", "clm-v0.1-8b", "frognano-4b"):
        m = _BY_ID[mid]
        assert len(m["sources"]) >= 2, mid
        for s in m["sources"]:
            assert s.startswith("https://"), mid
