"""MCP (Model Context Protocol) remote-server client manager.

Lets the application connect to *remote* MCP servers over the
streamable-HTTP and SSE transports, discover the tools they expose, and call
them. Server configurations are persisted to ``mcp/servers.json`` inside the
data directory. Local ``stdio`` servers (which require spawning an external
runtime such as ``npx``/``uvx``) are intentionally not handled here; remote
servers need no extra toolchain and work in the packaged application.

The manager exposes async methods for use from FastAPI ``async def`` routes
and thin synchronous wrappers (via :func:`asyncio.run`) for the agent's
synchronous tool handlers, which execute in worker threads.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

VALID_TRANSPORTS = ("streamable-http", "sse")


class MCPError(Exception):
    """Base error for MCP configuration / connection problems."""


class MCPConfigError(MCPError):
    """Invalid server configuration (HTTP 400)."""


class MCPConnectionError(MCPError):
    """Could not connect to / initialize an MCP server (HTTP 502)."""


def _normalize_url(url: str, transport: str) -> str:
    u = (url or "").strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        raise MCPConfigError("URL must start with http:// or https://")
    return u


class MCPManager:
    """Persist MCP server configs and talk to remote servers."""

    def __init__(self, app_root: str | Path):
        self.root = Path(app_root)
        self.dir = self.root / "mcp"
        self.path = self.dir / "servers.json"
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"servers": []})

    # ------------------------------------------------------------------ config
    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise MCPConfigError(f"unreadable mcp config: {e}") from None
        if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
            raise MCPConfigError("invalid mcp config shape")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.path)

    def list_servers(self) -> list[dict[str, Any]]:
        return list(self._read()["servers"])

    def _find(self, server_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        data = self._read()
        for s in data["servers"]:
            if s["id"] == server_id:
                return s, data["servers"]
        raise MCPConfigError(f"server not found: {server_id}")

    def get_server(self, server_id: str) -> dict[str, Any]:
        return self._find(server_id)[0]

    def add_server(self, name: str, transport: str, url: str,
                   enabled: bool = True) -> dict[str, Any]:
        nm = (name or "").strip()
        if not nm:
            raise MCPConfigError("name is required")
        if transport not in VALID_TRANSPORTS:
            raise MCPConfigError(
                f"transport must be one of {', '.join(VALID_TRANSPORTS)}")
        u = _normalize_url(url, transport)
        data = self._read()
        if any(s["name"].lower() == nm.lower() for s in data["servers"]):
            raise MCPConfigError(f"a server named {nm} already exists")
        server = {
            "id": "mcp_" + uuid.uuid4().hex[:10],
            "name": nm,
            "transport": transport,
            "url": u,
            "enabled": bool(enabled),
            "created_at": int(time.time()),
        }
        data["servers"].append(server)
        self._write(data)
        return server

    def remove_server(self, server_id: str) -> bool:
        data = self._read()
        before = len(data["servers"])
        data["servers"] = [s for s in data["servers"] if s["id"] != server_id]
        if len(data["servers"]) == before:
            raise MCPConfigError(f"server not found: {server_id}")
        self._write(data)
        return True

    def set_enabled(self, server_id: str, enabled: bool) -> dict[str, Any]:
        server, servers = self._find(server_id)
        server["enabled"] = bool(enabled)
        self._write({"servers": servers})
        return server

    # ------------------------------------------------------------------ client
    @staticmethod
    def _transport_ctx(server: dict[str, Any]):
        if server["transport"] == "sse":
            from mcp.client.sse import sse_client
            return sse_client(server["url"])
        from mcp.client.streamable_http import streamablehttp_client
        return streamablehttp_client(server["url"])

    @staticmethod
    async def _safe_close(cm: Any) -> None:
        """Tear down a context manager, never raising (teardown may re-raise
        the underlying connection errors as an ExceptionGroup)."""
        try:
            await cm.__aexit__(None, None, None)
        except BaseException:  # noqa: BLE001 - teardown may re-raise
            return  # swallow: never mask the real result/error

    async def _session(self, server: dict[str, Any]):
        try:
            ctx = self._transport_ctx(server)
            streams = await ctx.__aenter__()
        except BaseException as e:  # noqa: BLE001
            raise MCPConnectionError(
                f"cannot connect to {server['name']}: {e}") from None
        read, write = streams[0], streams[1]
        from mcp import ClientSession
        session = ClientSession(read, write)
        try:
            await session.__aenter__()
            await session.initialize()
        except BaseException as e:  # noqa: BLE001
            await self._safe_close(session)
            await self._safe_close(ctx)
            raise MCPConnectionError(
                f"initialize failed for {server['name']}: {e}") from None
        return session, ctx

    @staticmethod
    def _tool_dict(t: Any) -> dict[str, Any]:
        schema = getattr(t, "inputSchema", None) or {"type": "object"}
        return {
            "name": t.name,
            "description": getattr(t, "description", "") or "",
            "inputSchema": schema,
        }

    async def a_list_tools(self, server_id: str) -> list[dict[str, Any]]:
        server = self.get_server(server_id)
        session, ctx = await self._session(server)
        try:
            result = await session.list_tools()
            return [self._tool_dict(t) for t in result.tools]
        finally:
            await self._safe_close(session)
            await self._safe_close(ctx)

    async def a_call(self, server_id: str, tool: str,
                     arguments: dict[str, Any] | None) -> dict[str, Any]:
        server = self.get_server(server_id)
        session, ctx = await self._session(server)
        try:
            result = await session.call_tool(tool, arguments or {})
        except BaseException as e:  # noqa: BLE001
            raise MCPError(f"tool call failed: {e}") from None
        finally:
            await self._safe_close(session)
            await self._safe_close(ctx)
        return self._result_dict(result)

    @staticmethod
    def _result_dict(result: Any) -> dict[str, Any]:
        texts: list[str] = []
        for c in getattr(result, "content", []) or []:
            txt = getattr(c, "text", None)
            if txt is not None:
                texts.append(txt)
        structured = getattr(result, "structuredContent", None)
        return {
            "text": "\n".join(texts),
            "structured": structured,
            "isError": bool(getattr(result, "isError", False)),
        }

    async def a_all_tools(self, only_enabled: bool = True) -> list[dict[str, Any]]:
        """Aggregate tools across servers, each namespaced by server id/name."""
        out: list[dict[str, Any]] = []
        for s in self.list_servers():
            if only_enabled and not s.get("enabled"):
                continue
            try:
                tools = await self.a_list_tools(s["id"])
            except MCPError:
                continue
            for t in tools:
                out.append({
                    "server_id": s["id"],
                    "server_name": s["name"],
                    **t,
                })
        return out

    # ------------------------------------------------------------- sync wrappers
    def list_tools_sync(self, server_id: str) -> list[dict[str, Any]]:
        return asyncio.run(self.a_list_tools(server_id))

    def call_sync(self, server_id: str, tool: str,
                  arguments: dict[str, Any] | None) -> dict[str, Any]:
        return asyncio.run(self.a_call(server_id, tool, arguments))

    def all_tools_sync(self) -> list[dict[str, Any]]:
        return asyncio.run(self.a_all_tools())
