"""HTTP-level tests for v2.4.1 additions: gated downloads (HF token),
settings token roundtrip, engine check-updates / update endpoints."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def client():
    tmp = Path(tempfile.mkdtemp(prefix="kevrai-v241-"))
    os.environ["LOCALAPPDATA"] = str(tmp)
    os.environ["XDG_DATA_HOME"] = str(tmp)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


# ---------- gated download ----------

def test_gated_download_requires_token(client):
    r = client.post("/api/download/start", json={
        "url": "https://huggingface.co/Lightricks/LTX-2.5/resolve/main/model.safetensors",
        "dest_filename": "ltx.bin",
        "auto_pick": False,
        "gated": True,
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "gated_requires_token"
    assert "Token" in detail["message"] or "协议" in detail["message"]


def test_settings_hf_token_roundtrip(client):
    r = client.put("/api/settings", json={"hf_token": "hf_test_123"})
    assert r.status_code == 200
    r2 = client.get("/api/settings")
    assert r2.status_code == 200
    assert r2.json()["hf_token"] == "hf_test_123"
    # 清回去，避免影响其他用例
    client.put("/api/settings", json={"hf_token": ""})
    assert client.get("/api/settings").json()["hf_token"] == ""


def test_non_gated_download_unaffected_by_token(client):
    """gated=False 路径不应触发 422（即便没有 token）。
    这里只验证参数校验通过、错误来源不是 gated 拦截。"""
    r = client.post("/api/download/start", json={
        "url": "ftp://bad.scheme/x",
        "dest_filename": "x.bin",
        "auto_pick": False,
    })
    assert r.status_code == 400  # scheme refused, not 422
    assert "gated" not in str(r.json()).lower()


# ---------- engine update endpoints ----------

def test_check_updates_endpoint_shape(client, monkeypatch):
    from app import main as app_main

    async def fake_check(root, catalog, *, force=False, client=None):
        return [{"engine_id": "llama.cpp", "update_available": True,
                 "latest_tag": "b9999", "from_cache": False}]

    monkeypatch.setattr(app_main, "check_engine_updates", fake_check)
    r = client.post("/api/engines/check-updates", json={"force": True})
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["engine_id"] == "llama.cpp"
    assert results[0]["update_available"] is True


def test_update_unknown_engine_400(client):
    r = client.post("/api/engines/update", json={"engine_id": "ghost-engine"})
    assert r.status_code == 400


def test_engines_endpoint_exposes_update_fields(client):
    r = client.get("/api/engines")
    assert r.status_code == 200
    engs = r.json()["engines"]
    assert engs
    first = engs[0]
    # v2.4.1 新字段存在即可（默认未安装，update_available 为 False）
    assert "version" in first
    assert "latest_tag" in first
    assert "update_available" in first
    assert first["update_available"] is False or first["installed"] is True
