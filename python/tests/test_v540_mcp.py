"""Tests for remote MCP server manager, agent tools, and routes (v4.8.x)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import uuid

import pytest

from app.agent.tool_registry import ToolContext
from app.agent.tools.mcp_tools import _call_mcp_tool, _list_mcp_tools
from app.mcp_manager import (
    MCPConfigError,
    MCPConnectionError,
    MCPManager,
)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def test_add_and_list(tmp_path):
    mgr = MCPManager(tmp_path)
    s = mgr.add_server("x", "streamable-http", "https://h/mcp")
    assert s["id"] and s["enabled"] is True
    assert len(mgr.list_servers()) == 1


def test_add_requires_name_and_valid_url(tmp_path):
    mgr = MCPManager(tmp_path)
    with pytest.raises(MCPConfigError):
        mgr.add_server(" ", "streamable-http", "https://h/mcp")
    with pytest.raises(MCPConfigError):
        mgr.add_server("x", "bad", "https://h/mcp")
    with pytest.raises(MCPConfigError):
        mgr.add_server("x", "streamable-http", "ftp://h")


def test_duplicate_name_rejected(tmp_path):
    mgr = MCPManager(tmp_path)
    mgr.add_server("X", "streamable-http", "https://h/mcp")
    with pytest.raises(MCPConfigError):
        mgr.add_server("x", "streamable-http", "https://h2/mcp")


def test_remove_and_set_enabled(tmp_path):
    mgr = MCPManager(tmp_path)
    s = mgr.add_server("x", "sse", "https://h/sse")
    mgr.set_enabled(s["id"], False)
    assert mgr.get_server(s["id"])["enabled"] is False
    assert mgr.remove_server(s["id"])
    assert mgr.list_servers() == []


def test_remove_missing_raises(tmp_path):
    mgr = MCPManager(tmp_path)
    with pytest.raises(MCPConfigError):
        mgr.remove_server("nope")


# ---------------------------------------------------------------------------
# async loopback against a real FastMCP server
# ---------------------------------------------------------------------------
async def _loopback(tmp_path):
    from mcp.server.fastmcp import FastMCP

    port = free_port()
    srv = FastMCP("demo", host="127.0.0.1", port=port)

    @srv.tool()
    def add(a: int, b: int) -> int:
        """add two integers"""
        return a + b

    task = asyncio.create_task(srv.run_streamable_http_async())
    await asyncio.sleep(1.0)
    try:
        mgr = MCPManager(tmp_path)
        added = mgr.add_server("demo", "streamable-http",
                               f"http://127.0.0.1:{port}/mcp")
        tools = await mgr.a_list_tools(added["id"])
        result = await mgr.a_call(added["id"], "add", {"a": 4, "b": 5})
        return added, tools, result
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_loopback_discovery_and_call(tmp_path):
    added, tools, result = asyncio.run(_loopback(tmp_path))
    assert [t["name"] for t in tools] == ["add"]
    assert "add two integers" in tools[0]["description"]
    assert result["text"] == "9"
    assert result["structured"] == {"result": 9}
    assert result["isError"] is False


async def _bad_server(tmp_path):
    mgr = MCPManager(tmp_path)
    port = free_port()  # nothing listening
    s = mgr.add_server("dead", "streamable-http",
                       f"http://127.0.0.1:{port}/mcp")
    with pytest.raises(MCPConnectionError):
        await mgr.a_list_tools(s["id"])


def test_bad_server_connection_error(tmp_path):
    asyncio.run(_bad_server(tmp_path))


# ---------------------------------------------------------------------------
# agent tools (fake manager injected)
# ---------------------------------------------------------------------------
class FakeMgr:
    def __init__(self):
        self.servers = [{"id": "s1", "name": "demo", "enabled": True}]

    def list_servers(self):
        return self.servers

    def list_tools_sync(self, sid):
        return [{"name": "add", "description": "adds",
                 "inputSchema": {"type": "object",
                                 "properties": {"a": {"type": "integer"}},
                                 "required": ["a"]}}]

    def call_sync(self, sid, tool, args):
        return {"text": json.dumps(args), "structured": args, "isError": False}


def _ctx(tmp_path, mgr):
    ctx = ToolContext(memory=None, models_dir=tmp_path, app_root=tmp_path)
    ctx.extra["_mcp"] = {"mgr": mgr}
    return ctx


def test_agent_list_tools(tmp_path):
    out = _list_mcp_tools({}, _ctx(tmp_path, FakeMgr()))
    assert out["tool_count"] == 1
    assert out["servers"][0]["tools"][0]["name"] == "add"


def test_agent_list_tools_empty(tmp_path):
    mgr = FakeMgr()
    mgr.servers = []
    out = _list_mcp_tools({}, _ctx(tmp_path, mgr))
    assert out["tool_count"] == 0


def test_agent_call_tool(tmp_path):
    out = _call_mcp_tool(
        {"server_id": "s1", "tool_name": "add", "arguments": {"a": 1}},
        _ctx(tmp_path, FakeMgr()))
    assert out["structured"] == {"a": 1}


def test_agent_call_tool_missing_fields(tmp_path):
    out = _call_mcp_tool({}, _ctx(tmp_path, FakeMgr()))
    assert "required" in out["error"]


def test_agent_call_tool_json_string_args(tmp_path):
    out = _call_mcp_tool(
        {"server_id": "s1", "tool_name": "add", "arguments": '{"a":2}'},
        _ctx(tmp_path, FakeMgr()))
    assert out["structured"] == {"a": 2}


# ---------------------------------------------------------------------------
# routes (APP_ROOT is a shared, module-level constant, so use unique names and
# clean up; never assert global emptiness)
# ---------------------------------------------------------------------------
def _add(hub_client, name, url="https://h/mcp", transport="streamable-http"):
    return hub_client.post("/api/mcp/servers", json={
        "name": name, "transport": transport, "url": url})


def test_route_crud(hub_client):
    name = "crud-" + uuid.uuid4().hex[:8]
    r = _add(hub_client, name)
    assert r.status_code == 201
    sid = r.json()["id"]
    try:
        r = hub_client.put(f"/api/mcp/servers/{sid}/enabled",
                           json={"enabled": False})
        assert r.status_code == 200 and r.json()["enabled"] is False
        r = hub_client.get("/api/mcp/servers")
        assert any(s["id"] == sid for s in r.json()["servers"])
    finally:
        assert hub_client.delete(f"/api/mcp/servers/{sid}").status_code == 200


def test_route_add_bad_400(hub_client):
    r = hub_client.post("/api/mcp/servers", json={
        "name": "", "transport": "x", "url": "y"})
    assert r.status_code == 400


def test_route_tools_bad_url_502(hub_client):
    port = free_port()
    name = "dead-" + uuid.uuid4().hex[:8]
    r = _add(hub_client, name, url=f"http://127.0.0.1:{port}/mcp")
    sid = r.json()["id"]
    try:
        r = hub_client.get(f"/api/mcp/servers/{sid}/tools")
        assert r.status_code == 502
    finally:
        hub_client.delete(f"/api/mcp/servers/{sid}")


def test_route_call_missing_tool_400(hub_client):
    name = "c2-" + uuid.uuid4().hex[:8]
    r = _add(hub_client, name)
    sid = r.json()["id"]
    try:
        r = hub_client.post(f"/api/mcp/servers/{sid}/call",
                            json={"tool_name": "", "arguments": {}})
        assert r.status_code == 400
    finally:
        hub_client.delete(f"/api/mcp/servers/{sid}")
