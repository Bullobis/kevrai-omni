"""v2.6.0 regression tests — catalog fact audit & post-May-2026 additions.

Every slug asserted here was verified live against the HuggingFace / GitHub
APIs on 2026-09-04 (existence, license, creation date). These tests guard
against re-introducing ghost repos (the v2.4.1 Hailuo-H3 class of bug) and
against dropping the newly curated models.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PYDIR = Path(__file__).resolve().parents[1]
REPO = PYDIR.parent
sys.path.insert(0, str(PYDIR))
sys.path.insert(0, str(REPO))


def _models():
    data = json.loads((REPO / "catalog" / "models.json").read_text(encoding="utf-8"))
    return {m["id"]: m for m in data["models"]}, data


def _engines():
    data = json.loads((REPO / "catalog" / "engines.json").read_text(encoding="utf-8"))
    return {e["id"]: e for e in data["engines"]}


# ---------------------------------------------------------------------------
# 1) Corrected slugs — these previously pointed at nonexistent / wrong repos
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mid,expected_repo", [
    ("deepseek-v4-pro", "deepseek-ai/DeepSeek-V4-Pro"),
    ("qwen3.8-max", "Qwen/Qwen3.8-2.4T-A95B"),
    ("qwen3.8-27b", "Qwen/Qwen3.8-27B"),
    ("trellis2", "microsoft/TRELLIS.2-4B"),
    ("hunyuanimage-3.0", "tencent/HunyuanImage-3.0"),
    ("seedvr2", "ByteDance-Seed/SeedVR2-3B"),
    ("direct3d-s2", "wushuang98/Direct3D-S2"),
    ("triposr", "VAST-AI-Research/TripoSR"),
    ("ace-step-1.5", "ACE-Step/Ace-Step1.5"),
    ("dots3-note-preview", "dots-studio/dots3-note-prev"),
    ("magi2-preview", "sand-ai/MAGI-2-preview"),
    ("lingbot-video", "robbyant/lingbot-video-dense-1.3b"),
])
def test_corrected_repo_slugs(mid, expected_repo):
    m, _ = _models()
    assert m[mid]["repo"] == expected_repo


def test_mistral_gguf_slug_has_mistralai_prefix():
    m, _ = _models()
    assert m["mistral-small-24b"]["gguf_repo"] == \
        "bartowski/mistralai_Mistral-Small-24B-Base-2501-GGUF"


def test_direct3d_license_is_mit():
    m, _ = _models()
    assert m["direct3d-s2"]["license"] == "MIT"


@pytest.mark.parametrize("ghost", [
    "llama4-multilingual",   # meta-llama/Llama-4-Multilingual does not exist
    "glm-5.3-pending",       # promoted to a real llm entry
])
def test_ghost_entries_removed(ghost):
    m, _ = _models()
    assert ghost not in m


def test_no_bare_deepseek_v4_or_qwen38_max_slug():
    _, data = _models()
    for m in data["models"]:
        assert m.get("repo") not in ("deepseek-ai/DeepSeek-V4", "Qwen/Qwen3.8-Max"), \
            f"{m['id']} still uses a nonexistent bare slug"


# ---------------------------------------------------------------------------
# 2) GLM-5.3 promoted out of pending
# ---------------------------------------------------------------------------
def test_glm53_promoted_to_llm():
    m, _ = _models()
    assert "glm-5.3" in m
    assert m["glm-5.3"]["category"] == "llm"
    assert m["glm-5.3"]["repo"] == "zai-org/GLM-5.3"


# ---------------------------------------------------------------------------
# 3) New post-May-2026 curated models are present and well-formed
# ---------------------------------------------------------------------------
NEW_MODELS = [
    "granite-4.2-8b", "granite-4.2-30b", "minimax-m3", "muse-glimmer-30b",
    "deepseek-v4-flash-vision", "lfm2.5-vl-3b", "moss-tts-1.5",
    "dots-tts-soar", "stable-audio-3-medium", "magenta-realtime-2",
    "moss-soundeffect-2", "krea-2-turbo",
]


@pytest.mark.parametrize("mid", NEW_MODELS)
def test_new_model_complete(mid):
    m, _ = _models()
    assert mid in m, f"missing new model {mid}"
    e = m[mid]
    assert e["repo"] and "/" in e["repo"]
    assert len(e["sources"]) >= 2, f"{mid} needs >=2 mirrors"
    for u in e["sources"]:
        assert u.startswith("https://"), f"{mid} bad source {u}"
    # every source must reference the same owner/repo as `repo`
    for u in e["sources"]:
        assert e["repo"] in u, f"{mid} source {u} inconsistent with repo"
    assert e["hardware"], f"{mid} needs hardware block"
    assert e["modality"], f"{mid} needs modality block"
    assert e["tags"], f"{mid} needs tags"
    assert e["size_gb"] > 0
    assert isinstance(e["trending"], bool)


def test_new_models_are_not_pending():
    m, _ = _models()
    for mid in NEW_MODELS + ["glm-5.3"]:
        assert m[mid]["category"] != "pending"


# ---------------------------------------------------------------------------
# 4) MiniMax-Music3 family present, correct license, modality annotated
# ---------------------------------------------------------------------------
MUSIC3 = [
    "minimax-music3", "minimax-music3-comfyui", "minimax-music3-gguf",
    "minimax-music3-turbo-fp8", "minimax-music3-w4a8-comfyui",
    "minimax-music3-lora-fiona-crapple", "minimax-music3-latent-refiner",
    "minimax-music3-mlx",
]


@pytest.mark.parametrize("mid", MUSIC3)
def test_music3_family_complete(mid):
    m, _ = _models()
    assert mid in m
    assert m[mid]["category"] == "audio"
    assert m[mid]["modality"]["generate"] == ["audio"]


def test_music3_official_license_and_repo():
    m, _ = _models()
    official = m["minimax-music3"]
    assert official["repo"] == "MiniMaxAI/MiniMax-Music3"
    assert "Community License" in official["license"]
    assert "sglang-omni" in official["engine"]
    # architecture facts from the official model card
    for fact in ("8B", "0.6B", "2.4B", "123M", "32"):
        assert fact in official["description"]


# ---------------------------------------------------------------------------
# 5) Engine github slug corrections
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("eid,expected_github", [
    ("kokoro-engine", "hexgrad/Kokoro"),
    ("chatterbox", "resemble-ai/chatterbox"),
    ("indextts", "index-tts/index-tts"),
    ("triposr", "VAST-AI-Research/TripoSR"),
    ("triposg", "VAST-AI-Research/TripoSG"),
    ("direct3d-s2", "DreamTechAI/Direct3D-S2"),
    ("sglang-omni", "sgl-project/sglang-omni"),
])
def test_engine_github_slugs(eid, expected_github):
    e = _engines()
    assert e[eid]["github"] == expected_github


# ---------------------------------------------------------------------------
# 6) Whole-catalog invariants after the edit
# ---------------------------------------------------------------------------
def test_unique_ids_and_sources_after_edit():
    _, data = _models()
    ids = [m["id"] for m in data["models"]]
    assert len(ids) == len(set(ids)), "duplicate model ids"
    for m in data["models"]:
        if m["category"] != "pending":
            assert len(m.get("sources", [])) >= 2, f"{m['id']} <2 sources"


def test_catalog_version_bumped():
    _, data = _models()
    assert data["version"] == "2.7.0"
    # every non-pending entry still carries a safe id
    for m in data["models"]:
        assert re.fullmatch(r"[A-Za-z0-9._-]+", m["id"])
