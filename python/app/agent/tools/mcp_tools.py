"""Agent tools for remote MCP (Model Context Protocol) servers.

Two generic tools let the reasoning brain discover and call any tool exposed
by a configured remote MCP server, regardless of when that server was added:

- ``list_mcp_tools``: enumerate every enabled server and its tools (with
  descriptions and parameter schemas).
- ``call_mcp_tool``: invoke one server tool with a JSON arguments object and
  return its text (and structured content).

The :class:`~app.mcp_manager.MCPManager` is created lazily and cached on the
:class:`~app.agent.tool_registry.ToolContext`.
"""
from __future__ import annotations

import json
from typing import Any

from ...mcp_manager import MCPConnectionError, MCPManager
from ..tool_registry import Tool, ToolContext


def _manager(ctx: ToolContext) -> MCPManager:
    store = ctx.extra.setdefault("_mcp", {})
    if "mgr" not in store:
        store["mgr"] = MCPManager(ctx.app_root)
    return store["mgr"]


def _list_mcp_tools(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    mgr = _manager(ctx)
    servers = mgr.list_servers()
    if not servers:
        return {"servers": [], "tool_count": 0,
                "note": "No MCP servers configured. Add one in Settings → MCP Servers."}
    catalog: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for s in servers:
        if not s.get("enabled"):
            continue
        try:
            tools = mgr.list_tools_sync(s["id"])
        except MCPConnectionError as e:
            errors.append({"server": s["name"], "error": str(e)})
            continue
        catalog.append({
            "server_id": s["id"],
            "server_name": s["name"],
            "tools": [
                {"name": t["name"], "description": t["description"],
                 "parameters": t["inputSchema"]}
                for t in tools
            ],
        })
    return {
        "servers": catalog,
        "tool_count": sum(len(c["tools"]) for c in catalog),
        "errors": errors,
    }


def _call_mcp_tool(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    server_id = str(params.get("server_id") or "").strip()
    tool_name = str(params.get("tool_name") or "").strip()
    if not server_id or not tool_name:
        return {"error": "server_id and tool_name are required"}
    arguments = params.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as e:
            return {"error": f"arguments is not valid JSON: {e}"}
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return {"error": "arguments must be a JSON object"}
    mgr = _manager(ctx)
    try:
        return mgr.call_sync(server_id, tool_name, arguments)
    except MCPConnectionError as e:
        return {"error": str(e), "error_type": "MCPConnectionError"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "error_type": type(e).__name__}


list_mcp_tools = Tool(
    name="list_mcp_tools",
    description=(
        "List the tools exposed by every configured remote MCP server. Returns "
        "each server's id/name and its tools with descriptions and parameter "
        "schemas. Call this before call_mcp_tool to discover what is available."
    ),
    parameters={"type": "object", "properties": {}},
    handler=_list_mcp_tools,
    category="media",
)

call_mcp_tool = Tool(
    name="call_mcp_tool",
    description=(
        "Invoke a tool on a remote MCP server. Provide the server_id and "
        "tool_name (from list_mcp_tools) and an arguments JSON object. Returns "
        "the tool's text and structured result."
    ),
    parameters={
        "type": "object",
        "properties": {
            "server_id": {"type": "string", "description": "MCP server id."},
            "tool_name": {"type": "string", "description": "Tool to call."},
            "arguments": {
                "type": "object",
                "description": "Tool arguments as a JSON object.",
                "additionalProperties": True,
            },
        },
        "required": ["server_id", "tool_name"],
    },
    handler=_call_mcp_tool,
    category="media",
)

MCP_TOOLS = [list_mcp_tools, call_mcp_tool]
