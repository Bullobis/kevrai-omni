"""Regression tests for the Demucs engine + separation models."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))
_ENG = json.loads((_ROOT / "catalog" / "engines.json").read_text(encoding="utf-8"))
_BY_ID = {m["id"]: m for m in _MODELS["models"]}
_ENG_BY_ID = {e["id"]: e for e in _ENG["engines"]}


def test_engine_present():
    e = _ENG_BY_ID["demucs"]
    assert e["install"] == "pip" and e["pypi"] == "demucs==4.1.0"
    assert e["license"] == "MIT" and isinstance(e["platforms"], dict)


def test_totals_grew():
    assert len(_MODELS["models"]) >= 249
    assert len(_ENG["engines"]) >= 35


def test_separation_models():
    for mid in ("htdemucs", "htdemucs-ft", "htdemucs-6s"):
        m = _BY_ID[mid]
        assert m["category"] == "audio" and m["engine"] == ["demucs"]
        assert m["modality"]["understand"] == ["audio"]
        assert "audio" in m["modality"]["generate"]
        assert len(m["sources"]) >= 2


def test_htdemucs_default_trending():
    assert _BY_ID["htdemucs"]["trending"] is True
    assert _BY_ID["htdemucs"]["size_gb"] == 0.08


def test_ft_largest():
    assert _BY_ID["htdemucs-ft"]["size_gb"] == 0.32
