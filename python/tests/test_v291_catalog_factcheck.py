"""v2.9.1 K-Catalog fact-check regression tests (ADR-0003).

Locks in the corrected repository slugs verified live against the
HuggingFace API on 2026-09-24. Guards against re-introducing the ghost
slugs removed in this fact-check.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PYDIR = Path(__file__).resolve().parents[1]
REPO = PYDIR.parent
sys.path.insert(0, str(PYDIR))


def _models():
    data = json.loads((REPO / "catalog" / "models.json").read_text(encoding="utf-8"))
    return {m["id"]: m for m in data["models"]}, data


def _engines():
    data = json.loads((REPO / "catalog" / "engines.json").read_text(encoding="utf-8"))
    return {e["id"]: e for e in data["engines"]}


# ---------------------------------------------------------------------------
# Corrected HF slugs (all verified 200 on https://huggingface.co/api/models)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mid,expected_repo", [
    ("glm-4.5", "zai-org/GLM-4.5"),
    ("hunyuanvideo", "tencent/HunyuanVideo"),
    ("real-esrgan", "ai-forever/Real-ESRGAN"),
    ("apisr", "camenduru/APISR"),
    ("supir", "camenduru/SUPIR"),
    ("4x-ultrasharp", "lokCX/4x-Ultrasharp"),
    ("audioldm2", "haoheliu/audioldm2-full"),
    ("diffrhythm", "ASLP-lab/DiffRhythm-base"),
    ("triposr", "stabilityai/TripoSR"),
    ("gpt-sovits", "lj1995/GPT-SoVITS"),
    ("yolov10", "onnx-community/YOLOv10"),
    ("opensora-2.0", "hpcai-tech/Open-Sora"),
    ("minimax-music3-w4a8-comfyui", "NidAll/MiniMax-Music3-W4A8"),
    ("insightface", "immich-app/buffalo_l"),
])
def test_corrected_hf_slugs(mid, expected_repo):
    m, _ = _models()
    assert mid in m, f"{mid} missing from catalog"
    assert m[mid]["repo"] == expected_repo, \
        f"{mid} repo drift: got {m[mid]['repo']!r}, want {expected_repo!r}"


# ---------------------------------------------------------------------------
# Ghost slugs that must NOT reappear
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ghost_slug", [
    "THUDM/glm-4-9b-chat",           # org renamed to zai-org; and GLM-4.5 entry must point to GLM-4.5
    "Tencent-Hunyuan/HunyuanVideo",  # HF org is lowercase tencent
    "xinntao/Real-ESRGAN",           # no such HF repo; Real-ESRGAN is GitHub-only
    "Kiteretsu77/APISR",             # no such HF repo
    "Fanghua-Yu/SUPIR",              # no such HF repo; community mirror is camenduru
    "Kim2091/4x-UltraSharp",         # case-slug: correct is lokCX/4x-Ultrasharp
    "haoheliu/audioldm2",            # bare slug; correct is audioldm2-full
    "ASLP-lab/DiffRhythm",           # bare slug; correct is DiffRhythm-base
    "VAST-AI-Research/TripoSR",      # HF org has no models; correct is stabilityai/TripoSR
    "RVC-Boss/GPT-SoVITS",           # no HF repo under RVC-Boss
    "THU-MIG/yolov10",               # no HF repo under THU-MIG; ONNX community build is canonical
    "hpcaitech/Open-Sora",           # org renamed hpcai-tech
    "dummy9996/MiniMax-Music3-w4a8-bf16-comfyui",  # does not exist
    "buffalo_l",                     # bare slug (not owner/repo)
])
def test_ghost_slugs_absent(ghost_slug):
    _, data = _models()
    for m in data["models"]:
        assert m.get("repo") != ghost_slug, \
            f"{m['id']} re-introduces ghost slug {ghost_slug}"
        assert m.get("gguf_repo") != ghost_slug


# ---------------------------------------------------------------------------
# SUPIR must be labelled as a non-official mirror
# ---------------------------------------------------------------------------
def test_supir_marked_non_official():
    m, _ = _models()
    s = m["supir"]
    blob = json.dumps(s, ensure_ascii=False)
    assert ("非官方" in s.get("description", "")
            or "非官方" in s.get("source_note", "")), \
        "camenduru/SUPIR must be labelled as a non-official community mirror"


# ---------------------------------------------------------------------------
# Version consistency (must stay in lock-step with package.json)
# ---------------------------------------------------------------------------
def test_catalog_version_matches_package():
    pkg = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
    for name in ("models", "engines"):
        cat = json.loads((REPO / "catalog" / f"{name}.json").read_text(encoding="utf-8"))
        assert cat["version"] == pkg["version"], f"{name}.json version drift"


# ---------------------------------------------------------------------------
# Non-pending models must carry >=2 source mirrors
# ---------------------------------------------------------------------------
def test_non_pending_models_have_two_sources():
    _, data = _models()
    for m in data["models"]:
        if m.get("category") == "pending":
            continue
        srcs = m.get("sources") or []
        assert len(srcs) >= 2, f"{m['id']} has only {len(srcs)} sources"


# ---------------------------------------------------------------------------
# No ad-copy exaggeration in descriptions
# ---------------------------------------------------------------------------
_AD_WORDS = ("最强", "第一", "天花板", "遥遥领先", "秒杀", "吊打", "yyds")


def test_no_ad_copy_descriptions():
    _, data = _models()
    for m in data["models"]:
        d = m.get("description", "")
        for w in _AD_WORDS:
            assert w not in d, f"{m['id']} still contains ad-copy word {w!r}: {d}"
