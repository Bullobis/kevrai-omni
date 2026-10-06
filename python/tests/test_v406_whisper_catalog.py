"""Regression tests for the faster-whisper engine + Whisper catalog entries."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DATA = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))
_ENG = json.loads((_ROOT / "catalog" / "engines.json").read_text(encoding="utf-8"))
_BY_ID = {m["id"]: m for m in _DATA["models"]}
_ENG_BY_ID = {e["id"]: e for e in _ENG["engines"]}


def test_engine_present():
    e = _ENG_BY_ID["faster-whisper"]
    assert e["install"] == "pip" and e["pypi"] == "faster-whisper"
    assert e["license"] == "MIT"


def test_total_grew():
    assert len(_BY_ID) >= 240


def test_whisper_large_v3():
    m = _BY_ID["whisper-large-v3"]
    assert m["repo"] == "Systran/faster-whisper-large-v3"
    assert m["engine"] == ["faster-whisper"]
    assert m["modality"]["understand"] == ["audio"]
    assert m["modality"]["generate"] == ["text"]


def test_whisper_turbo():
    m = _BY_ID["whisper-large-v3-turbo"]
    assert m["repo"] == "dropbox-dash/faster-whisper-large-v3-turbo"
    assert m["size_gb"] == 1.62
    assert m["size_gb"] < _BY_ID["whisper-large-v3"]["size_gb"]
