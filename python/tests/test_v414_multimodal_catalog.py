"""Regression tests for the generic multimodal runtime catalog entries."""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))
_ENG = json.loads((_ROOT / "catalog" / "engines.json").read_text(encoding="utf-8"))
_BY_ID = {m["id"]: m for m in _MODELS["models"]}
_ENG_BY_ID = {e["id"]: e for e in _ENG["engines"]}


def test_transformers_engine_pinned():
    e = _ENG_BY_ID["transformers"]
    assert e["install"] == "pip"
    assert e["pypi"] == "transformers==5.18.0"
    # torch / accelerate / pillow needed for image+text inference.
    extra = e.get("pip_extra") or []
    assert "torch" in extra
    assert any(x.startswith("accelerate") for x in extra)
    assert "pillow" in extra


def test_totals_grew():
    assert len(_MODELS["models"]) >= 255
    assert len(_ENG["engines"]) >= 36


def test_multimodal_models_present():
    for mid in ("smolvlm-256", "janus-pro-7b", "minicpm-v-4.6"):
        m = _BY_ID[mid]
        assert m["category"] == "llm"
        assert m["engine"] == ["transformers"]
        mod = m["modality"]
        assert mod["multimodal"] is True
        assert "image" in mod["understand"]
        assert mod["generate"] == ["text"]
        assert len(m["sources"]) >= 2
        assert m["primary_url"].startswith("https://huggingface.co/")


def test_licenses():
    assert _BY_ID["smolvlm-256"]["license"] == "apache-2.0"
    assert _BY_ID["janus-pro-7b"]["license"] == "mit"
    assert _BY_ID["minicpm-v-4.6"]["license"] == "apache-2.0"


def test_janus_size_and_hardware():
    j = _BY_ID["janus-pro-7b"]
    assert j["size_gb"] == 14.85
    hw = j["hardware"]
    assert hw["vram_gb"] >= j["size_gb"]
    assert hw["disk_gb"] >= j["size_gb"]


def test_smolvlm_footprint_small():
    s = _BY_ID["smolvlm-256"]
    assert s["size_gb"] <= 0.6
    assert s["hardware"]["ram_gb"] <= 2
