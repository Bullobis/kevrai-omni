"""Regression tests for combined legacy corrections + Oct 2026 frontier models.

Supersedes the separate data/legacy-fixes-v37 and data/frontier-oct26 branches.
All facts verified via the Hugging Face API (blobs) and model READMEs.
"""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
_BY_ID = {m["id"]: m for m in _MODELS}


def test_total_at_least_205():
    assert len(_MODELS) >= 205


# --- legacy corrections ------------------------------------------------------
def test_nex_pro_uses_independent_gguf_repo():
    m = _BY_ID["nex-n2.5-pro"]
    assert m["gguf_repo"] == "bartowski/Nex-N2.5-Pro-GGUF"
    q = m.get("quantizations", [])
    assert len(q) >= 4
    rec = [x for x in q if x["recommended"]]
    assert len(rec) == 1 and rec[0]["id"] == "Q4_K_M"


def test_nex_pro_quant_monotonic():
    q = _BY_ID["nex-n2.5-pro"]["quantizations"]
    order = ["Q3_K_M", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"]
    sizes = [next(x["size_gb"] for x in q if x["id"] == k) for k in order if k in [y["id"] for y in q]]
    assert sizes == sorted(sizes)


def test_minimax_w4a8_size():
    assert 1 <= _BY_ID["minimax-music3-w4a8-comfyui"]["size_gb"] <= 2


def test_apisr_small():
    assert _BY_ID["apisr"]["size_gb"] <= 0.2


def test_edge0_lossless():
    m = _BY_ID.get("edge0-35b")
    if m is not None:
        q = m.get("quantizations", [])
        assert len(q) == 1 and 15 < q[0]["size_gb"] < 22


def test_xing4_iq4():
    m = _BY_ID.get("xing4-29b-a4b")
    if m is not None:
        q = m.get("quantizations", [])
        assert len(q) == 1 and 15 < q[0]["size_gb"] < 22


def test_deepseek_flash_q2_vision_note():
    m = _BY_ID.get("deepseek-v4.1-flash")
    if m is not None:
        q = m.get("quantizations", [])
        assert len(q) >= 1 and q[0]["size_gb"] > 300
        assert "Vision" in m.get("notes", "")


# --- frontier models ---------------------------------------------------------
def test_pyannote_present():
    m = _BY_ID["pyannote-diarization-community-1"]
    assert m["repo"] == "pyannote/speaker-diarization-community-1"
    assert m["category"] == "audio" and m["license"] == "CC-BY-4.0"
    assert "audio" in m["modality"]["understand"]


def test_gliner_present():
    m = _BY_ID["gliner2.5"]
    assert m["repo"] == "fastino/GLiNER2.5-Decide" and m["license"] == "Apache-2.0"
    assert 1 <= m["size_gb"] <= 3


def test_clm_heads_base_dependency():
    m = _BY_ID["clm-v0.1-8b"]
    assert m["repo"] == "Contrastive-LM/CLM-v0.1-8B" and m["size_gb"] <= 0.5
    assert "Qwen3-8B" in m["hardware"]["notes"]


def test_frognano_present():
    m = _BY_ID["frognano-4b"]
    assert m["repo"] == "microsoft/FrogNano-4B-2609" and m["license"] == "MIT"
    assert 7 <= m["size_gb"] <= 10


def test_all_new_two_sources():
    ids = ("pyannote-diarization-community-1", "gliner2.5", "clm-v0.1-8b", "frognano-4b")
    for mid in ids:
        s = _BY_ID[mid]["sources"]
        assert len(s) >= 2 and all(x.startswith("https://") for x in s), mid
