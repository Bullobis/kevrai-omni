"""Regression tests for the Piper engine + TTS voice models."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))
_ENG = json.loads((_ROOT / "catalog" / "engines.json").read_text(encoding="utf-8"))
_BY_ID = {m["id"]: m for m in _MODELS["models"]}
_ENG_BY_ID = {e["id"]: e for e in _ENG["engines"]}


def test_engine_present():
    e = _ENG_BY_ID["piper"]
    assert e["install"] == "pip" and e["pypi"] == "piper-tts==1.8.0"
    assert e["category"] == "tts" and isinstance(e["platforms"], dict)


def test_totals_grew():
    assert len(_MODELS["models"]) >= 252
    assert len(_ENG["engines"]) >= 36


def test_voice_models():
    for mid in ("piper-en-lessac", "piper-en-amy", "piper-zh-huayan"):
        m = _BY_ID[mid]
        assert m["category"] == "tts" and m["engine"] == ["piper"]
        assert m["modality"]["understand"] == ["text"]
        assert m["modality"]["generate"] == ["speech"]
        assert len(m["sources"]) >= 2


def test_license_is_gpl():
    # piper-tts is GPL-3.0; must not be mislabelled as MIT.
    assert _ENG_BY_ID["piper"]["license"] == "GPL-3.0"
    assert _BY_ID["piper-en-lessac"]["license"] == "gpl-3.0"


def test_default_voice_trending():
    assert _BY_ID["piper-en-lessac"]["trending"] is True
    assert _BY_ID["piper-en-lessac"]["size_gb"] == 0.063
