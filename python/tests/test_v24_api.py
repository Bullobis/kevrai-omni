"""API-level tests for v2.4.0 endpoints (super search + LTX-2.5).

Uses FastAPI TestClient so these exercise the full HTTP stack including
query-string parsing, Pydantic validation, and status codes.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CATALOG_DIR = REPO_ROOT / "catalog"


@pytest.fixture(scope="module")
def client():
    tmp = Path(tempfile.mkdtemp(prefix="kevrai-v24-"))
    os.environ["LOCALAPPDATA"] = str(tmp)
    os.environ["XDG_DATA_HOME"] = str(tmp)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


# ---------- /api/search ----------

def test_search_empty_returns_all(client):
    r = client.get("/api/search")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 100  # catalog has 110 models
    assert "facets" in body
    assert "items" in body
    assert body["elapsed_ms"] >= 0


def test_search_ltx_ranks_first(client):
    r = client.get("/api/search", params={"q": "LTX-2.5"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items
    assert items[0]["id"] == "ltx-2.5"
    assert items[0]["_score"] > 0
    assert items[0]["_highlights"]


def test_search_chinese(client):
    r = client.get("/api/search", params={"q": "视频"})
    assert r.status_code == 200
    ids = [i["id"] for i in r.json()["items"]]
    assert "ltx-2.5" in ids


def test_search_with_engine_filter(client):
    r = client.get("/api/search", params={"q": "", "engine": "mnn"})
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert "mnn" in (item.get("engine") or [])


def test_search_with_category_filter(client):
    r = client.get("/api/search", params={"category": "video"})
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert item["category"] == "video"


def test_search_sort_name(client):
    r = client.get("/api/search", params={"sort": "name_asc", "page_size": 200})
    names = [i["name"] for i in r.json()["items"]]
    assert names == sorted(names, key=str.lower)


def test_search_pagination(client):
    r1 = client.get("/api/search", params={"page": 1, "page_size": 5})
    r2 = client.get("/api/search", params={"page": 2, "page_size": 5})
    assert len(r1.json()["items"]) == 5
    assert len(r2.json()["items"]) == 5
    assert r1.json()["items"][0]["id"] != r2.json()["items"][0]["id"]


def test_search_invalid_sort_falls_back(client):
    r = client.get("/api/search", params={"sort": "DROP TABLE"})
    assert r.status_code == 200  # falls back to relevance


def test_search_extreme_long_query(client):
    r = client.get("/api/search", params={"q": "a" * 5000})
    # server caps q at 200 chars; must not 500
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_search_regex_metachars(client):
    r = client.get("/api/search", params={"q": ".*+?^${}()|[]\\"})
    assert r.status_code == 200


def test_search_html_injection(client):
    r = client.get("/api/search", params={"q": "<script>alert(1)</script>"})
    assert r.status_code == 200
    # raw query is echoed back but the renderer escapes it; ensure no crash
    assert "<script>" in r.json()["query"]


def test_search_facets_shape(client):
    r = client.get("/api/search")
    f = r.json()["facets"]
    for key in ("engines", "licenses", "categories", "sizes"):
        assert key in f
        assert isinstance(f[key], list)


def test_search_recent_roundtrip(client):
    # POST-like via GET pushes recent
    client.get("/api/search", params={"q": "uniquefoo123"})
    r = client.get("/api/search/recent")
    assert r.status_code == 200
    assert "uniquefoo123" in r.json()["recent"]
    d = client.delete("/api/search/recent")
    assert d.status_code == 200
    r2 = client.get("/api/search/recent")
    assert "uniquefoo123" not in r2.json()["recent"]


# ---------- /api/ltx/* ----------

def test_ltx_capabilities(client):
    r = client.get("/api/ltx/capabilities")
    assert r.status_code == 200
    cap = r.json()
    assert cap["model"] == "Lightricks/LTX-2.5"
    assert "t2v" in [m["id"] for m in cap["modes"]]
    assert "balanced" in [p["id"] for p in cap["presets"]]
    assert "limits" in cap


def test_ltx_generate_empty_prompt_rejected(client):
    r = client.post("/api/ltx/generate", json={"prompt": ""})
    assert r.status_code == 422  # Pydantic min_length


def test_ltx_generate_invalid_mode(client):
    r = client.post("/api/ltx/generate", json={"prompt": "cat", "mode": "x2v"})
    assert r.status_code == 400


def test_ltx_generate_bad_dimensions(client):
    r = client.post("/api/ltx/generate", json={"prompt": "cat", "width": 10})
    assert r.status_code == 400


def test_ltx_generate_i2v_without_image(client):
    r = client.post("/api/ltx/generate", json={"prompt": "cat", "mode": "i2v"})
    assert r.status_code == 400


def test_ltx_generate_accepts_valid_params(client):
    # A valid request should be accepted (the task will fail later without
    # torch, but the API call itself returns 200 with a task snapshot).
    r = client.post("/api/ltx/generate", json={
        "prompt": "a cat playing piano", "preset": "draft",
        "width": 384, "height": 256, "num_frames": 33,
        "num_inference_steps": 5,
    })
    assert r.status_code == 200
    task = r.json()["task"]
    assert task["id"]
    assert task["state"] in ("queued", "loading", "running", "failed", "done", "cancelled")


def test_ltx_tasks_list(client):
    r = client.get("/api/ltx/tasks")
    assert r.status_code == 200
    body = r.json()
    assert "tasks" in body
    assert "active" in body or body.get("active") is None


def test_ltx_task_404(client):
    r = client.get("/api/ltx/tasks/deadbeef1234")
    assert r.status_code == 404


def test_ltx_cancel_unknown_404(client):
    r = client.post("/api/ltx/tasks/deadbeef1234/cancel")
    assert r.status_code == 404


def test_ltx_task_invalid_id(client):
    r = client.get("/api/ltx/tasks/../etc/passwd")
    assert r.status_code in (400, 404)


def test_ltx_outputs(client):
    r = client.get("/api/ltx/outputs")
    assert r.status_code == 200
    body = r.json()
    assert "outputs" in body
    assert "dir" in body


# ---------- /api/models sort enhancement ----------

def test_models_sort_size(client):
    r = client.get("/api/models", params={"sort": "size_desc"})
    assert r.status_code == 200
    sizes = [float(m.get("size_gb") or 0) for m in r.json()["models"]]
    assert sizes == sorted(sizes, reverse=True)


def test_models_search_includes_repo(client):
    # Searching for a repo keyword should now match the repo field too.
    r = client.get("/api/models", params={"q": "Qwen"})
    assert r.status_code == 200
    assert any("qwen" in m["id"].lower() for m in r.json()["models"])
