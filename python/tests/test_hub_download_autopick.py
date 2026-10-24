"""POST /api/hub/download — 未给 files 时后端自动挑权重文件。

真实 bug：目录里 259 个模型中有 165 个没有 gguf_repo。前端拿不到具体分片
路径，旧代码在 `if not files: 400` 处直接拒绝，安装流程于是退化成"只装引擎
然后弹一句 toast"——**权重从未被下载，且没有任何报错**。用户以为装好了。

修复后：files 缺失 + auto_pick=true 时，后端用 app.repo_picker 从仓库清单里
挑出权重与必需配置。auto_pick 原先只负责**换源**（在镜像间挑最快 URL），
这里补的是**挑文件**这一环，两件事互不冲突。

本测试用假的 hub/downloader 驱动真实端点，确保：
  * 自动挑出来的文件确实被送去下载
  * 关掉 auto_pick 时行为不变（仍是 400，避免静默改变契约）
  * 仓库里压根没有权重时给明确的 404 而不是空任务
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app import main as app_main  # noqa: E402


# ---------------------------------------------------------------------------
# 假件
# ---------------------------------------------------------------------------

class _Adapter:
    def resolve_url(self, repo, path, revision=""):
        return f"https://hf.example/{repo}/resolve/{revision or 'main'}/{path}"

    def auth_headers(self):
        return {}


class _FakeHub:
    """返回可控的仓库清单。"""

    def __init__(self, files) -> None:
        self._files = files
        self.seen: list[str] = []

    async def files(self, hub, repo, revision=""):
        return {"files": self._files, "also_on": []}

    def adapter(self, hub):
        return _Adapter()


class _FakeDownloader:
    """记录被要求下载的文件，其余接口给出最小可用响应。"""

    def __init__(self) -> None:
        self.started: list[str] = []

    async def start(self, url, dest, *, sha256=None, candidates=None,
                    extra_headers=None):
        self.started.append(url)
        return f"task-{len(self.started)}"

    async def progress(self, task_id):
        return {"status": "done", "downloaded_bytes": 1024, "total_bytes": 1024}

    async def aclose(self):
        return None


def _wire(tmp_xdg: Path, hub_files):
    from app.settings import Settings

    s = Settings()
    s.model_dir = str(tmp_xdg / "models")
    s.engine_dir = str(tmp_xdg / "engines")
    s.download_dir = str(tmp_xdg / "downloads")
    app_main.app.state.settings = s
    app_main.app.state.settings_path = str(tmp_xdg / "settings.json")
    app_main.app.state.hub = None
    app_main.app.state.downloader = None
    app_main.app.state.hub_jobs = {}
    hub = _FakeHub(hub_files)
    dl = _FakeDownloader()
    app_main.app.state.hub = hub
    app_main.app.state.downloader = dl
    return hub, dl


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """绝不允许这些用例真的去联网。"""
    monkeypatch.setattr(app_main, "get_registry", lambda settings=None: _FakeHub([]))


def _client():
    transport = httpx.ASGITransport(app=app_main.app)
    headers = {"authorization": f"Bearer {os.environ['KEVRAI_SIDECAR_SECRET']}"}
    return httpx.AsyncClient(transport=transport, base_url="http://t",
                             headers=headers)


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

async def test_missing_files_is_auto_picked(tmp_xdg, monkeypatch):
    """files 缺失且 auto_pick=true → 后端代挑权重并真的下载（旧行为：什么都没下）。"""
    hub_files = [
        {"path": "config.json", "size": 800},
        {"path": "tokenizer.json", "size": 900},
        {"path": "model.safetensors", "size": 5_000_000},
        {"path": "README.md", "size": 3000},
        {"path": "original/model.safetensors", "size": 9_000_000},
    ]
    hub, dl = _wire(tmp_xdg, hub_files)
    monkeypatch.setattr(app_main, "get_registry", lambda settings=None: hub)

    async with _client() as c:
        r = await c.post("/api/hub/download", json={
            "hub": "hf", "repo": "org/model", "revision": "main", "auto_pick": True,
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("auto_files_picked") is True

    urls = " ".join(dl.started)
    assert "model.safetensors" in urls, "权重应被下载"
    assert "config.json" in urls, "配置应一并下载"
    assert "README.md" not in urls, "文档不该被下载"
    assert "original/" not in urls, "原始检查点不该被下载"


async def test_auto_pick_off_still_requires_files(tmp_xdg, monkeypatch):
    """关掉 auto_pick 时契约不变——仍是 400。不能静默放宽旧约束。"""
    hub, _ = _wire(tmp_xdg, [{"path": "model.safetensors", "size": 10}])
    monkeypatch.setattr(app_main, "get_registry", lambda settings=None: hub)

    async with _client() as c:
        r = await c.post("/api/hub/download", json={
            "hub": "hf", "repo": "org/model", "auto_pick": False,
        })
    assert r.status_code == 400


async def test_explicit_files_still_win(tmp_xdg, monkeypatch):
    """显式给了 files 就照给的下，不做任何自动挑选。"""
    hub, dl = _wire(tmp_xdg, [
        {"path": "model.safetensors", "size": 10},
        {"path": "other.bin", "size": 20},
    ])
    monkeypatch.setattr(app_main, "get_registry", lambda settings=None: hub)

    async with _client() as c:
        r = await c.post("/api/hub/download", json={
            "hub": "hf", "repo": "org/model",
            "files": ["other.bin"], "auto_pick": True,
        })
    assert r.status_code == 200, r.text
    assert r.json().get("auto_files_picked") is False
    assert len(dl.started) == 1 and "other.bin" in dl.started[0]


async def test_repo_without_weights_returns_clear_error(tmp_xdg, monkeypatch):
    """识别不出权重要给明确错误，而不是建一个空任务让用户干等。"""
    hub, dl = _wire(tmp_xdg, [{"path": "README.md", "size": 10}])
    monkeypatch.setattr(app_main, "get_registry", lambda settings=None: hub)

    async with _client() as c:
        r = await c.post("/api/hub/download", json={
            "hub": "hf", "repo": "org/docs-only", "auto_pick": True,
        })
    assert r.status_code == 404
    assert r.json().get("detail", {}).get("error") == "no_weight_files"
    assert dl.started == [], "不该发起任何下载"


async def test_gguf_only_repo_still_downloads(tmp_xdg, monkeypatch):
    """仓库里只有 gguf 权重时必须保留它——排除掉就又变成"什么都不下"。"""
    hub, dl = _wire(tmp_xdg, [
        {"path": "config.json", "size": 10},
        {"path": "model-Q4_K_M.gguf", "size": 1000},
    ])
    monkeypatch.setattr(app_main, "get_registry", lambda settings=None: hub)

    async with _client() as c:
        r = await c.post("/api/hub/download", json={
            "hub": "hf", "repo": "org/gguf-only", "auto_pick": True,
        })
    assert r.status_code == 200, r.text
    assert any("model-Q4_K_M.gguf" in u for u in dl.started)
