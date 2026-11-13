"""v4.17.5 DSH 插件系统的回归测试。

背景：这一版新增了 PluginManager（安装→激活→停用→卸载）但没有任何测试。
手工复现出的三个问题都在这里钉死：

1. 装完插件后调用两次「停用」会 AttributeError → 500（handle 已为 None）
2. 安装 id 可以是 ``../../outside``，git clone 会写到 plugins/ 之外
3. 清单里的 ``main`` 可以是 ``../../evil.py``，激活时执行插件目录外的文件
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.plugins import PluginManager, _safe_plugin_id  # noqa: E402


def _has_git() -> bool:
    return bool(os.environ.get("PATH")) and subprocess.run(
        ["git", "--version"], capture_output=True
    ).returncode == 0


def _git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    for name, content in files.items():
        (repo / name).write_text(content)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "init"],
    ):
        subprocess.run(cmd, cwd=repo, check=True)
    return repo


def _plugin(root: Path, plugin_id: str = "demo") -> PluginManager:
    repo = _git_repo(root.parent, {
        "plugin.json": json.dumps({
            "id": plugin_id, "name": "Demo", "version": "1.0.0", "main": "plugin.py",
        }),
        "plugin.py": (
            "def register(kevrai):\n"
            "    @kevrai.plugin.tool(name='hello', description='hi')\n"
            "    def hello(x: str = 'world'):\n"
            "        return 'hi ' + x\n"
            "    @kevrai.plugin.route('/api/demo/ping')\n"
            "    def ping():\n"
            "        return {'pong': True}\n"
        ),
    })
    pm = PluginManager(root)
    pm.install(repo.as_uri(), plugin_id)
    return pm


# ---------------------------------------------------------------------------
# 正常生命周期
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_git(), reason="git not installed")
def test_full_lifecycle_tool_and_route(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    pm = _plugin(root)
    app = FastAPI()
    pm.mount_routes(app)

    meta = pm.activate("demo")
    assert meta.active is True
    assert "demo:hello" in pm.tools()

    client = TestClient(app)
    assert client.get("/api/demo/ping").json() == {"pong": True}

    pm.deactivate("demo")
    assert pm.tools() == {}
    # 路由必须真的从 app 上摘掉，否则停用后还能访问
    assert all(getattr(r, "path", "") != "/api/demo/ping" for r in app.router.routes)


@pytest.mark.skipif(not _has_git(), reason="git not installed")
def test_deactivate_twice_is_idempotent(tmp_path):
    """停用一个本来就没激活的插件不能 500。"""
    root = tmp_path / "data"
    root.mkdir()
    pm = _plugin(root)
    pm.mount_routes(FastAPI())

    pm.activate("demo")
    pm.deactivate("demo")
    pm.deactivate("demo")  # 第二次：handle 为 None

    pm.uninstall("demo")
    assert not (root / "plugins" / "demo").exists()


# ---------------------------------------------------------------------------
# 安全边界
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["../../outside", "..", ".", "a/b", "", "x" * 65])
def test_safe_plugin_id_rejects(bad):
    assert _safe_plugin_id(bad) is None


@pytest.mark.parametrize("good", ["demo", "a.b_c-1", "x" * 64])
def test_safe_plugin_id_accepts(good):
    assert _safe_plugin_id(good) == good


def test_normalize_git_url_rejects_option_injection():
    """A repo URL starting with '-' is parsed by git as an option."""
    from app.plugins import _normalize_git_url

    with pytest.raises(RuntimeError):
        _normalize_git_url("--upload-pack=/bin/sh")
    with pytest.raises(RuntimeError):
        _normalize_git_url("   ")
    with pytest.raises(RuntimeError):
        _normalize_git_url("https://x/y\n--evil")
    assert _normalize_git_url("owner/repo") == "https://github.com/owner/repo"
    assert _normalize_git_url(" https://example.com/a.git ") == "https://example.com/a.git"


def test_install_uses_dash_separator_before_repo(tmp_path, monkeypatch):
    """Defence in depth: the clone command must carry '--' before the URL."""
    from app.plugins import PluginManager
    import app.plugins as plugins_mod

    root = tmp_path / "data"
    root.mkdir()
    pm = PluginManager(root)
    seen: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(plugins_mod.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="cordis.yml|plugin.json"):
        pm.install("https://example.com/a.git", "demo")
    assert seen, "clone must be attempted"
    clone = seen[0]
    assert clone[:4] == [clone[0], "clone", "--depth", "1"]
    assert clone[4] == "--"
    assert clone[5] == "https://example.com/a.git"


@pytest.mark.skipif(not _has_git(), reason="git not installed")
def test_install_rejects_path_traversal(tmp_path):
    """id ../../../outside 不能把仓库 clone 到 plugins 之外。"""
    root = tmp_path / "data"
    root.mkdir()
    pm = PluginManager(root)
    repo = _git_repo(tmp_path, {
        "plugin.json": json.dumps({"id": "outside", "main": "plugin.py"}),
        "plugin.py": "def register(k): pass\n",
    })
    outside = tmp_path / "outside"
    with pytest.raises(RuntimeError):
        pm.install(repo.as_uri(), "../../outside")
    assert not outside.exists()


def test_manifest_with_illegal_id_is_ignored(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    pdir = root / "plugins" / "weird"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps({"id": "../../evil", "main": "plugin.py"}))
    (pdir / "plugin.py").write_text("def register(k): pass\n")
    pm = PluginManager(root)
    assert pm.list() == []


@pytest.mark.skipif(not _has_git(), reason="git not installed")
def test_activate_rejects_entry_outside_plugin_dir(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    pm = PluginManager(root)
    repo = _git_repo(tmp_path, {
        "plugin.json": json.dumps({"id": "escape", "main": "../../../evil.py"}),
        "plugin.py": "def register(k): pass\n",
    })
    pm.install(repo.as_uri(), "escape")
    with pytest.raises(RuntimeError, match="入口"):
        pm.activate("escape")
