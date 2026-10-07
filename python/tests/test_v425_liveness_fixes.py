"""Regression: liveness-audit fixes for Piper voice URLs and the Demucs engine.

Reads the raw catalog JSON (these are metadata URLs; the pydantic loader is not
needed for this check).
"""
from __future__ import annotations

import json
from pathlib import Path

CAT_DIR = Path(__file__).resolve().parent.parent.parent / "catalog"
MODELS = json.loads((CAT_DIR / "models.json").read_text(encoding="utf-8"))
ENGINES = json.loads((CAT_DIR / "engines.json").read_text(encoding="utf-8"))


def _model(mid):
    return next(m for m in MODELS["models"] if m["id"] == mid)


def test_piper_voice_urls_use_real_filenames():
    # catalog id -> actual onnx filename in rhasspy/piper-voices
    cases = {
        "piper-en-lessac": "en_US-lessac-medium.onnx",
        "piper-en-amy": "en_US-amy-medium.onnx",
        "piper-zh-huayan": "zh_CN-huayan-medium.onnx",
    }
    for mid, fname in cases.items():
        m = _model(mid)
        joined = m["sources"] + [m["primary_url"]]
        assert any(u.endswith(fname) for u in joined), f"{mid} missing {fname}"
        assert not any(f"piper-{mid.split('-', 1)[1]}.onnx" in u for u in joined)


def test_demucs_engine_drops_bare_directory_url():
    dem = next(e for e in ENGINES["engines"] if e["id"] == "demucs")
    assert "https://dl.fbaipublicfiles.com/demucs/" not in dem["sources"]
    # still well-sourced
    assert len(dem["sources"]) >= 4
