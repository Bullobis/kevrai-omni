"""Tests for local model import + registry persistence.

Note: ``import_local`` now returns an ``ImportResult`` dataclass (not a dict).
Use attribute access (``res.path``, ``res.size_bytes``) — see ``app.importer``.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.importer import (
    import_local,
    load_local_registry,
    save_local_registry,
)


def test_import_local_file(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    f = src / "model.gguf"
    f.write_bytes(b"GGUF" * 1000)

    models_dir = tmp_path / "models"
    res = import_local(src, models_dir)

    assert Path(res.path).exists()
    assert res.size_bytes > 0
    assert "model.gguf" in res.path or "model" in res.path


def test_import_local_dir(tmp_path: Path):
    src = tmp_path / "model_dir"
    src.mkdir()
    (src / "config.json").write_text("{}")
    (src / "weights.bin").write_bytes(b"x" * 5000)

    models_dir = tmp_path / "models"
    res = import_local(src, models_dir)
    assert Path(res.path).is_dir()
    assert (Path(res.path) / "weights.bin").exists()


def test_import_local_renames_on_collision(tmp_path: Path):
    src1 = tmp_path / "a"
    src1.mkdir()
    (src1 / "m.gguf").write_bytes(b"a")
    src2 = tmp_path / "b"
    src2.mkdir()
    (src2 / "m.gguf").write_bytes(b"b")

    models_dir = tmp_path / "models"
    info1 = import_local(src1, models_dir)
    info2 = import_local(src2, models_dir)
    assert info1.path != info2.path, "collision must rename"


def test_registry_roundtrip(tmp_path: Path):
    reg = [{"id": "local-x", "name": "x", "path": "/x", "size_bytes": 1}]
    save_local_registry(tmp_path, reg)
    assert load_local_registry(tmp_path) == reg


def test_registry_missing_file_returns_empty(tmp_path: Path):
    assert load_local_registry(tmp_path) == []


def test_import_local_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        import_local(tmp_path / "nope", tmp_path / "models")