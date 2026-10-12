"""Tests for session rename and Markdown export (v4.8.x)."""
from __future__ import annotations

import pytest

from app.agent import Agent
from app.agent.memory import AgentMemory
from app.agent.session_export import export_session, render_session_markdown
from app.agent.tool_registry import ToolContext, ToolRegistry


def _mem(tmp_path):
    return AgentMemory(tmp_path / "m.sqlite3")


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------
def test_rename_session_persists(tmp_path):
    mem = _mem(tmp_path)
    sid = "s1"
    mem.add_message(sid, "user", "hi")
    assert mem.rename_session(sid, "我的会话")
    assert mem.get_session(sid)["title"] == "我的会话"


def test_rename_session_blank_title(tmp_path):
    mem = _mem(tmp_path)
    mem.create_session("s2")
    mem.rename_session("s2", "   ")
    assert mem.get_session("s2")["title"] == ""


def test_rename_creates_missing_session(tmp_path):
    mem = _mem(tmp_path)
    mem.rename_session("new", "标题")
    assert mem.get_session("new")["title"] == "标题"


# ---------------------------------------------------------------------------
# markdown rendering
# ---------------------------------------------------------------------------
def test_render_markdown_structure():
    md = render_session_markdown("标题", [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "在的",
         "tool_name": "t", "tool_params": '{"a":1}'},
    ])
    assert md.startswith("# 标题")
    assert "## 🧑 用户" in md
    assert "你好" in md
    assert "## 🤖 助手" in md
    assert "🛠️ 工具：t" in md
    assert '{"a":1}' in md


def test_render_markdown_empty():
    md = render_session_markdown("", [])
    assert "# 未命名会话" in md


def test_export_session_loads_from_memory(tmp_path):
    mem = _mem(tmp_path)
    sid = "s9"
    mem.rename_session(sid, "导出测试")
    mem.add_message(sid, "user", "问题")
    mem.add_message(sid, "assistant", "回答")
    md = export_session(mem, sid)
    assert "# 导出测试" in md
    assert "问题" in md and "回答" in md


def test_export_session_missing_raises(tmp_path):
    mem = _mem(tmp_path)
    with pytest.raises(KeyError):
        export_session(mem, "ghost")


# ---------------------------------------------------------------------------
# Agent + routes
# ---------------------------------------------------------------------------
def _agent(tmp_path):
    mem = _mem(tmp_path)
    ctx = ToolContext(memory=mem, models_dir=tmp_path, app_root=tmp_path)
    ctx.hardware_info = {
        "gpu_vendor": "m", "gpu_best_vram_gb": 0,
        "ram_total_gb": 0, "disk": {"free_gb": 0},
    }
    return Agent(memory=mem, registry=ToolRegistry(), ctx=ctx), mem


def test_agent_rename_rejects_blank(tmp_path):
    agent, _ = _agent(tmp_path)
    with pytest.raises(ValueError):
        agent.rename_session("s", "  ")


def test_agent_export_missing_raises(tmp_path):
    agent, _ = _agent(tmp_path)
    with pytest.raises(KeyError):
        agent.export_session_markdown("nope")


def test_route_rename(hub_client):
    # create a session via a chat-less path: use rename which creates it
    sid = "route-rename-" + "ab12"
    r = hub_client.post(f"/api/agent/sessions/{sid}/rename",
                        json={"title": "路由命名"})
    assert r.status_code == 200
    assert r.json()["title"] == "路由命名"


def test_route_rename_blank_400(hub_client):
    r = hub_client.post("/api/agent/sessions/route-rename-x/rename",
                        json={"title": "  "})
    assert r.status_code == 400


def test_route_export_markdown(hub_client):
    sid = "route-export-ab"
    hub_client.post(f"/api/agent/sessions/{sid}/rename", json={"title": "导出"})
    r = hub_client.get(f"/api/agent/sessions/{sid}/export")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "# 导出" in r.text


def test_route_export_missing_404(hub_client):
    r = hub_client.get("/api/agent/sessions/no-such-session-zz/export")
    assert r.status_code == 404
