"""Regression: ``app.catalog.load_catalog`` must tolerate malformed engines.json.

Previously a single bad entry (non-dict / missing ``id``), a non-object root,
or corrupt JSON crashed the whole catalog load with KeyError/AttributeError,
losing every engine.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.catalog import load_catalog


def _write_catalog(catalog_dir: Path, engines_obj) -> None:
    (catalog_dir / "models.json").write_text(
        json.dumps({"version": "1", "models": []}), encoding="utf-8"
    )
    if engines_obj is not None:
        (catalog_dir / "engines.json").write_text(
            json.dumps(engines_obj), encoding="utf-8"
        )


def test_load_catalog_skips_malformed_engine_entries(tmp_path):
    _write_catalog(tmp_path, {
        "engines": [
            {"name": "missing id"},                     # no "id" -> skipped
            "not-a-dict",                                # wrong type -> skipped
            42,                                          # wrong type -> skipped
            {"id": "llama.cpp", "name": "LLaMA.cpp"},  # kept
        ],
    })
    _, engines = load_catalog(tmp_path, dev_mode=True)
    assert set(engines) == {"llama.cpp"}
    assert engines["llama.cpp"]["name"] == "LLaMA.cpp"


def test_load_catalog_tolerates_non_object_engines_root(tmp_path):
    _write_catalog(tmp_path, [1, 2, 3])
    _, engines = load_catalog(tmp_path, dev_mode=True)
    assert engines == {}


def test_load_catalog_tolerates_corrupt_engines_json(tmp_path):
    _write_catalog(tmp_path, None)
    (tmp_path / "engines.json").write_text("{{{ not json", encoding="utf-8")
    catalog, engines = load_catalog(tmp_path, dev_mode=True)
    assert catalog.version == "1"
    assert engines == {}
