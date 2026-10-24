"""app/engine_router.py 测试 + default_engine_id 真正生效的端到端验证。

核心断言：显式选择不在候选内必须**抛错**，绝不静默回退。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine_router import (  # noqa: E402
    EngineResolveError,
    resolve_engine,
)

# ---------------------------------------------------------------------------
# 优先级：explicit > default > catalog 第一个
# ---------------------------------------------------------------------------

def test_explicit_wins():
    r = resolve_engine(["llama.cpp", "mnn"], explicit="mnn")
    assert r["engine"] == "mnn"
    assert r["source"] == "explicit"


def test_default_engine_id_is_consumed():
    """这条就是原先的死设置——settings 里改了却无人读取。"""
    r = resolve_engine(["llama.cpp", "mnn"], default_engine_id="mnn")
    assert r["engine"] == "mnn"
    assert r["source"] == "default"


def test_default_not_in_candidates_falls_back_to_first():
    """设置里的引擎该模型不支持时，回退到 catalog 第一个，但必须标明来源。"""
    r = resolve_engine(["llama.cpp", "mnn"], default_engine_id="vllm")
    assert r["engine"] == "llama.cpp"
    assert r["source"] == "catalog"


def test_explicit_beats_default():
    r = resolve_engine(
        ["llama.cpp", "mnn"], explicit="llama.cpp", default_engine_id="mnn"
    )
    assert r["engine"] == "llama.cpp"
    assert r["source"] == "explicit"


def test_catalog_order_is_last_resort():
    r = resolve_engine(["diffusers", "mnn"])
    assert r["engine"] == "diffusers"
    assert r["source"] == "catalog"


# ---------------------------------------------------------------------------
# 显式选择不可用 → 必须报错，不静默回退
# ---------------------------------------------------------------------------

def test_explicit_not_in_candidates_raises():
    """用户选了 A 却静默跑 B，比直接失败更糟（他以为是 A 的行为）。"""
    with pytest.raises(EngineResolveError) as ei:
        resolve_engine(["llama.cpp"], explicit="mnn")
    assert "mnn" in str(ei.value)
    assert "llama.cpp" in str(ei.value), "报错里应列出真实候选，便于排查"


def test_explicit_on_empty_candidates_raises():
    with pytest.raises(EngineResolveError):
        resolve_engine([], explicit="mnn")


# ---------------------------------------------------------------------------
# 运行时映射
# ---------------------------------------------------------------------------

def test_mnn_maps_to_mnn_endpoint():
    r = resolve_engine(["mnn"])
    assert r["runtime"] == "mnn"
    assert r["route"] == "/api/mnn/load"


def test_llamacpp_maps_to_engines():
    r = resolve_engine(["llama.cpp"])
    assert r["runtime"] == "llamacpp"
    assert r["route"] == "/api/engines"


def test_engine_without_runtime_is_reported_not_silently_accepted():
    """diffusers / vllm 等当前无应用内加载端点 —— 必须如实说明，不假装能跑。"""
    r = resolve_engine(["diffusers"])
    assert r["runtime"] == "other"
    assert r["route"] is None
    assert "diffusers" in r["note"], "应说明是哪个引擎没有端点"


# ---------------------------------------------------------------------------
# 空 / 脏输入
# ---------------------------------------------------------------------------

def test_no_candidates():
    r = resolve_engine(None)
    assert r["engine"] is None
    assert r["source"] == "none"
    assert "没有声明" in r["note"]


def test_string_candidate_is_accepted():
    r = resolve_engine("mnn")
    assert r["engine"] == "mnn"


def test_duplicates_and_blanks_normalized():
    r = resolve_engine(["mnn", "mnn", "  ", "llama.cpp"])
    assert r["candidates"] == ["mnn", "llama.cpp"]


def test_non_string_entries_dropped():
    r = resolve_engine(["mnn", 123, None])
    assert r["candidates"] == ["mnn"]


# ---------------------------------------------------------------------------
# 端点：GET /api/engine/route
# ---------------------------------------------------------------------------

def _client():
    import httpx

    from app import main as app_main

    transport = httpx.ASGITransport(app=app_main.app)
    headers = {"authorization": f"Bearer {os.environ['KEVRAI_SIDECAR_SECRET']}"}
    return httpx.AsyncClient(transport=transport, base_url="http://t", headers=headers)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from app import main as app_main
    from app.settings import Settings

    s = Settings()
    s.model_dir = str(tmp_path / "models")
    s.engine_dir = str(tmp_path / "engines")
    app_main.app.state.settings = s
    app_main.app.state.settings_path = str(tmp_path / "settings.json")
    yield s


async def test_route_endpoint_returns_runtime(_isolated):
    """只给 mnn 一个候选：默认设置是 llama.cpp，不在候选里，故按 catalog 第一个走。

    （这条原来写成 "mnn,llama.cpp" 并断言 mnn，是测试自己的错：
     default_engine_id 默认就是 llama.cpp 且在候选内，选中它才是正确行为。）
    """
    async with _client() as c:
        r = await c.get("/api/engine/route", params={"engine": "mnn"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "mnn"
    assert body["source"] == "catalog"
    assert body["route"] == "/api/mnn/load"


async def test_route_endpoint_honours_settings_default(_isolated):
    """端到端证明 default_engine_id 不再是被写入却无人读取的死字段。"""
    _isolated.default_engine_id = "llama.cpp"
    async with _client() as c:
        r = await c.get("/api/engine/route", params={"engine": "mnn,llama.cpp"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "llama.cpp", body
    assert body["source"] == "default"


async def test_route_endpoint_reports_other_runtime(_isolated):
    async with _client() as c:
        r = await c.get("/api/engine/route", params={"engine": "diffusers"})
    body = r.json()
    assert body["runtime"] == "other"
    assert body["route"] is None


async def test_post_route_rejects_unavailable_explicit(_isolated):
    async with _client() as c:
        r = await c.post("/api/engine/route", json={
            "candidates": ["llama.cpp"], "explicit": "mnn",
        })
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "engine_not_available"


# ---------------------------------------------------------------------------
# /api/mnn/load 引擎不匹配必须拒绝
# ---------------------------------------------------------------------------

async def test_mnn_load_rejects_wrong_engine(_isolated, tmp_path):
    d = tmp_path / "models" / "m"
    d.mkdir(parents=True)
    async with _client() as c:
        r = await c.post("/api/mnn/load", json={
            "model_dir": str(d), "engine": "llama.cpp",
        })
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "engine_mismatch"
    assert detail["requested"] == "llama.cpp"


async def test_mnn_load_accepts_mnn_engine(_isolated, tmp_path, monkeypatch):
    """声明 mnn 时不该被拦截（后续失败只能是别的原因，不是引擎校验）。"""
    from app import mnn_runtime

    d = tmp_path / "models" / "m"
    d.mkdir(parents=True)

    class _Missing(Exception):
        pass

    monkeypatch.setattr(mnn_runtime, "MnnEngineMissing", _Missing)
    monkeypatch.setattr(
        mnn_runtime, "load_model", lambda *a, **k: (_ for _ in ()).throw(_Missing("no engine"))
    )
    async with _client() as c:
        r = await c.post("/api/mnn/load", json={
            "model_dir": str(d), "engine": "mnn",
        })
    # 409 表示缺引擎（被 except 捕获），而不是 409 engine_mismatch
    assert r.status_code == 409
    assert r.json()["detail"] != "engine_mismatch" or isinstance(r.json()["detail"], str)
    body = r.json()
    if isinstance(body.get("detail"), dict):
        assert body["detail"]["error"] != "engine_mismatch"
