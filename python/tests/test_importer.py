"""Tests for local model import + registry persistence.

Note: ``import_local`` now returns an ``ImportResult`` dataclass (not a dict).
Use attribute access (``res.path``, ``res.size_bytes``) — see ``app.importer``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.importer import (
    import_local,
    load_local_registry,
    register_reference,
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


def test_register_reference_points_at_existing_dir_without_copy(tmp_path: Path):
    """A finished hub download must become visible without a second copy."""
    download_dir = tmp_path / "downloads" / "hub" / "hf" / "owner__model"
    download_dir.mkdir(parents=True)
    (download_dir / "config.json").write_text('{"model_type":"llama"}', encoding="utf-8")
    (download_dir / "model.safetensors").write_bytes(b"W" * 4096)

    models_dir = tmp_path / "models"
    entry = register_reference(
        download_dir, models_dir,
        catalog_id="demo-model", name="Demo Model",
        repo="owner/model", engine=["transformers"], category="llm",
    )

    assert entry["id"].startswith("download-")
    assert entry["path"] == str(download_dir.resolve())
    assert entry["size_bytes"] > 0
    assert entry["mode"] == "reference"
    # No model files were duplicated into models/ — only the registry JSON
    # lives there; the model itself stays in the download directory.
    assert [p.name for p in models_dir.iterdir()] == ["_local.json"]

    reg = load_local_registry(models_dir)
    assert len(reg) == 1
    # Idempotent: a second completion callback refreshes instead of appending.
    again = register_reference(
        download_dir, models_dir,
        catalog_id="demo-model", name="Demo Model v2",
        repo="owner/model", engine=["transformers"], category="llm",
    )
    assert again["id"] == entry["id"]
    assert again["name"] == "Demo Model v2"
    assert len(load_local_registry(models_dir)) == 1
