"""Regression tests for the sentence-transformers engine + embedding models."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DATA = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))
_ENG = json.loads((_ROOT / "catalog" / "engines.json").read_text(encoding="utf-8"))
_BY_ID = {m["id"]: m for m in _DATA["models"]}
_ENG_BY_ID = {e["id"]: e for e in _ENG["engines"]}


def test_engine_present():
    e = _ENG_BY_ID["sentence-transformers"]
    assert e["install"] == "pip"
    assert e["pypi"] == "sentence-transformers==6.1.0"
    assert e["license"] == "Apache-2.0"
    assert isinstance(e["platforms"], dict) and e["platforms"]


def test_total_grew():
    assert len(_DATA["models"]) >= 246
    assert len(_ENG["engines"]) >= 34


def test_embedding_models_present():
    ids = ["bge-m3", "bge-large-en", "bge-large-zh", "gte-large",
           "multilingual-e5-large", "gte-qwen2-7b-instruct"]
    for mid in ids:
        m = _BY_ID[mid]
        assert m["category"] == "embedding"
        assert m["engine"] == ["sentence-transformers"]
        assert m["modality"]["understand"] == ["text"]
        assert "embedding" in m["modality"]["generate"]
        assert len(m["sources"]) >= 2


def test_bge_m3_is_trending():
    assert _BY_ID["bge-m3"]["trending"] is True
    assert _BY_ID["bge-m3"]["size_gb"] == 2.27


def test_gte_qwen_largest():
    assert _BY_ID["gte-qwen2-7b-instruct"]["size_gb"] == 30.45
    assert _BY_ID["gte-qwen2-7b-instruct"]["license"] == "apache-2.0"
