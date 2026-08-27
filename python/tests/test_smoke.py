"""End-to-end smoke tests for the FastAPI sidecar (in-process TestClient)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    # Patch APP_ROOT etc. to a temp dir BEFORE importing the app
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="kevrai-smoke-"))
    os.environ["LOCALAPPDATA"] = str(tmp)        # windows path
    os.environ["XDG_DATA_HOME"] = str(tmp)       # linux path
    # Now import the app fresh
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "app_root" in body
    assert "models_dir" in body


def test_categories(client):
    r = client.get("/api/categories")
    assert r.status_code == 200
    cats = {c["id"] for c in r.json()["categories"]}
    assert {"llm", "tts", "video", "image", "audio", "3d"} <= cats


def test_list_models(client):
    r = client.get("/api/models")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 50
    # Filter by category
    r2 = client.get("/api/models", params={"category": "llm"})
    assert r2.status_code == 200
    assert all(m["category"] == "llm" for m in r2.json()["models"])
    # Search
    r3 = client.get("/api/models", params={"q": "Qwen"})
    assert r3.status_code == 200
    assert any("Qwen" in m["name"] for m in r3.json()["models"])


def test_model_detail_and_404(client):
    r = client.get("/api/models/qwen3-32b")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "qwen3-32b"
    assert body["gguf_repo"]
    # 404 for unknown
    r2 = client.get("/api/models/does-not-exist")
    assert r2.status_code == 404


def test_gguf_repos_endpoint(client):
    r = client.get("/api/gguf-repos")
    # may 200 with empty list if network blocked; that's ok for smoke
    assert r.status_code == 200
    assert "repos" in r.json()


def test_local_import_then_list(client, tmp_path):
    # Create a fake model file
    src = tmp_path / "fake.gguf"
    src.write_bytes(b"GGUF" * 100)
    r = client.post("/api/models/import", json={"path": str(src)})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["imported"]["size_bytes"] > 0

    r2 = client.get("/api/models/local")
    assert r2.status_code == 200
    items = r2.json()["local"]
    assert any(i["name"].startswith("fake") for i in items)


def test_engines_endpoint(client):
    r = client.get("/api/engines")
    assert r.status_code == 200
    engines = r.json()["engines"]
    assert len(engines) >= 5
    ids = {e["id"] for e in engines}
    assert "llama.cpp" in ids
    assert "mnn" in ids
    assert "diffusers" in ids


def test_engines_install_unknown_returns_400(client):
    r = client.post("/api/engines/install", json={"engine_id": "no-such-engine-xyz"})
    assert r.status_code == 400