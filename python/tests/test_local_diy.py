"""DIY feature ↔ engine/model integration tests (v2.8.0).

Covers the strengthened local-import pipeline:
  * ``detect_compatible_engines`` — filesystem-driven engine inference
  * ``/api/models/local`` — read-time ``compatible_engines`` annotation
  * ``/api/mnn/local`` — merges DIY-imported MNN dir models with the official
    ``mnn/`` subtree (deduped by absolute path, tagged ``diy: true``)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Unit: detect_compatible_engines
# ---------------------------------------------------------------------------

def test_detect_gguf_file(tmp_path: Path):
    from app.importer import detect_compatible_engines
    f = tmp_path / "qwen3-4b.gguf"
    f.write_bytes(b"GGUF" + b"\0" * 16)
    assert detect_compatible_engines(f) == ["llama.cpp"]


def test_detect_gguf_case_insensitive_and_rejects_other_files(tmp_path: Path):
    from app.importer import detect_compatible_engines
    (tmp_path / "M.GGUF").write_bytes(b"x")
    (tmp_path / "note.txt").write_text("hi")
    assert detect_compatible_engines(tmp_path / "M.GGUF") == ["llama.cpp"]
    assert detect_compatible_engines(tmp_path / "note.txt") == []


def test_detect_diffusers_dir(tmp_path: Path):
    from app.importer import detect_compatible_engines
    d = tmp_path / "flux-mini"
    d.mkdir()
    (d / "model_index.json").write_text("{}")
    assert detect_compatible_engines(d) == ["diffusers", "transformers"]


def test_detect_mnn_dir(tmp_path: Path):
    from app.importer import detect_compatible_engines
    d = tmp_path / "qwen-mnn"
    d.mkdir()
    (d / "config.json").write_text("{}")
    (d / "embeddings.mnn").write_bytes(b"\0")
    assert detect_compatible_engines(d) == ["mnn"]


def test_detect_hf_dir_without_mnn_is_transformers(tmp_path: Path):
    from app.importer import detect_compatible_engines
    d = tmp_path / "qwen3-hf"
    d.mkdir()
    (d / "config.json").write_text("{}")
    (d / "model.safetensors").write_bytes(b"\0")
    assert detect_compatible_engines(d) == ["transformers"]


def test_detect_missing_or_unknown_path(tmp_path: Path):
    from app.importer import detect_compatible_engines
    assert detect_compatible_engines(tmp_path / "nope") == []
    assert detect_compatible_engines(tmp_path) == []  # empty dir


# ---------------------------------------------------------------------------
# API: /api/models/local annotates compatible_engines
# ---------------------------------------------------------------------------

@pytest.fixture()
def diy_client(tmp_xdg, monkeypatch):
    """TestClient with MODELS_DIR pointed at an isolated tmp dir."""
    from fastapi.testclient import TestClient
    from app import main as app_main
    from app.settings import default_data_root

    mdir = default_data_root() / "models"
    mdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_main, "MODELS_DIR", mdir)
    with TestClient(app_main.app) as c:
        yield c, mdir


def test_models_local_annotates_engines(diy_client):
    from app.importer import save_local_registry
    client, mdir = diy_client

    gguf = mdir / "tiny.gguf"
    gguf.write_bytes(b"GGUF\0")
    hf = mdir / "hf-model"
    hf.mkdir()
    (hf / "config.json").write_text("{}")
    ghost = mdir / "ghost.gguf"  # registered but deleted afterwards

    save_local_registry(mdir, [
        {"id": "local-a", "name": "tiny.gguf", "path": str(gguf), "size_bytes": 5},
        {"id": "local-b", "name": "hf-model", "path": str(hf), "size_bytes": 2},
        {"id": "local-c", "name": "ghost.gguf", "path": str(ghost), "size_bytes": 9},
    ])

    r = client.get("/api/models/local")
    assert r.status_code == 200
    entries = {e["id"]: e for e in r.json()["local"]}
    assert entries["local-a"]["compatible_engines"] == ["llama.cpp"]
    assert entries["local-b"]["compatible_engines"] == ["transformers"]
    assert entries["local-c"]["compatible_engines"] == []  # file vanished


def test_model_detail_local_annotates(diy_client):
    from app.importer import save_local_registry
    client, mdir = diy_client
    gguf = mdir / "detail.gguf"
    gguf.write_bytes(b"GGUF\0")
    save_local_registry(mdir, [
        {"id": "local-detail", "name": "detail.gguf", "path": str(gguf), "size_bytes": 5},
    ])
    r = client.get("/api/models/local-detail")
    assert r.status_code == 200
    assert r.json()["compatible_engines"] == ["llama.cpp"]


# ---------------------------------------------------------------------------
# API: /api/mnn/local merges DIY-registered MNN dirs
# ---------------------------------------------------------------------------

def test_mnn_local_merges_diy_registry(diy_client):
    from app.importer import save_local_registry
    client, mdir = diy_client

    # Official pre-converted model under <models>/mnn/.
    official = mdir / "mnn" / "qwen-official"
    official.mkdir(parents=True)
    (official / "config.json").write_text("{}")

    # DIY-imported MNN dir somewhere else on disk.
    diy_dir = mdir / "my-import"
    diy_dir.mkdir()
    (diy_dir / "config.json").write_text("{}")
    (diy_dir / "llm.mnn").write_bytes(b"\0")

    # A registered .gguf entry must NOT appear in the MNN list.
    gguf = mdir / "not-mnn.gguf"
    gguf.write_bytes(b"GGUF\0")

    save_local_registry(mdir, [
        {"id": "local-diy", "name": "my-import", "path": str(diy_dir), "size_bytes": 1},
        {"id": "local-gguf", "name": "not-mnn.gguf", "path": str(gguf), "size_bytes": 5},
    ])

    r = client.get("/api/mnn/local")
    assert r.status_code == 200
    models = {m["id"]: m for m in r.json()["models"]}
    assert r.json()["count"] == 2
    assert "qwen-official" in models and "diy" not in models["qwen-official"]
    assert "my-import" in models and models["my-import"]["diy"] is True


def test_mnn_local_dedupes_by_path(diy_client):
    """A DIY entry pointing *into* the official mnn/ tree must not duplicate."""
    from app.importer import save_local_registry
    client, mdir = diy_client

    official = mdir / "mnn" / "same-model"
    official.mkdir(parents=True)
    (official / "config.json").write_text("{}")
    (official / "llm.mnn").write_bytes(b"\0")

    save_local_registry(mdir, [
        {"id": "local-dup", "name": "same-model", "path": str(official), "size_bytes": 1},
    ])

    r = client.get("/api/mnn/local")
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert "diy" not in r.json()["models"][0]  # official listing wins
