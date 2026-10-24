"""MCP (Model Context Protocol) server client manager.

Lets the application connect to MCP servers and discover/call the tools they
expose. Three transports are supported:

- ``streamable-http`` / ``sse``: remote servers reached over HTTP(S).
- ``stdio``: local servers spawned as child processes (e.g. ``npx -y
  some-mcp-server`` or ``uvx some-server``) and talked to over their
  stdin/stdout. The child is always torn down when the session closes, with
  SIGTERM -> SIGKILL escalation, so no orphan processes are left behind.

Server configurations are persisted to ``mcp/servers.json`` inside the data
directory. The manager exposes async methods for use from FastAPI ``async def``
routes and thin synchronous wrappers (via :func:`asyncio.run`) for the agent's
synchronous tool handlers, which execute in worker threads.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

VALID_TRANSPORTS = ("streamable-http", "sse", "stdio")

# Hard upper bound for the handshake (spawn + initialize). A stdio server that
# starts but never speaks MCP, or a remote endpoint that hangs, must fail fast
# with a readable error instead of blocking the request forever.
HANDSHAKE_TIMEOUT = 30.0


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

    def add_server(self, name: str, transport: str, url: str = "",
                   enabled: bool = True, *, command: str = "",
                   args: list[str] | None = None,
                   env: dict[str, str] | None = None,
                   cwd: str = "") -> dict[str, Any]:
        nm = (name or "").strip()
        if not nm:
            raise MCPConfigError("name is required")
        if transport not in VALID_TRANSPORTS:
            raise MCPConfigError(
                f"transport must be one of {', '.join(VALID_TRANSPORTS)}")
        data = self._read()
        if any(s["name"].lower() == nm.lower() for s in data["servers"]):
            raise MCPConfigError(f"a server named {nm} already exists")
        server: dict[str, Any] = {
            "id": "mcp_" + uuid.uuid4().hex[:10],
            "name": nm,
            "transport": transport,
            "enabled": bool(enabled),
            "created_at": int(time.time()),
        }
        if transport == "stdio":
            cmd = (command or "").strip()
            if not cmd:
                raise MCPConfigError(
                    "command is required for a stdio server "
                    "(e.g. npx, uvx, or python)")
            clean_args = [str(a) for a in (args or [])]
            clean_env = {str(k): str(v) for k, v in (env or {}).items()}
            server.update({
                "command": cmd,
                "args": clean_args,
                "env": clean_env,
                "cwd": (cwd or "").strip(),
            })
        else:
            server["url"] = _normalize_url(url, transport)
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
        if server["transport"] == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client
            # The SDK spawns the command as an argv list (no shell), and on
            # exit closes stdin, waits for graceful exit, then escalates
            # SIGTERM -> SIGKILL for the whole process tree — so callers never
            # leak a child process.
            #
            # **环境变量这块必须说清楚，否则排障会走很大弯路：**
            # SDK 确实会把我们给的 env 合并进"默认环境"，但它的"默认环境"
            # **不是完整的 ``os.environ``**，而是 SDK 自己维护的一份白名单
            # （``get_default_environment()`` 只取 ``DEFAULT_INHERITED_ENV_VARS``，
            # 含 PATH 一类，但**不含 PYTHONPATH**）。
            #
            # 后果：子进程不会继承父进程里没在白名单上的变量。典型踩坑是
            # 用 ``--target`` / 虚拟环境跑的应用——父进程靠 PYTHONPATH 找到
            # 依赖，子进程却没有，于是报
            # ``ModuleNotFoundError: No module named 'mcp.server'``
            # 这种看起来像"mcp 没装"、实则只是环境没传过去的错。
            # 需要哪个变量，就在 ``add_server(env=...)`` 里显式给出。
            params = StdioServerParameters(
                command=server["command"],
                args=list(server.get("args") or []),
                env=server.get("env") or None,
                cwd=server.get("cwd") or None,
            )
            return stdio_client(params)
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
            streams = await asyncio.wait_for(
                ctx.__aenter__(), timeout=HANDSHAKE_TIMEOUT)
        except FileNotFoundError:
            # The configured binary (npx/uvx/python…) does not exist on PATH.
            # stdio_client already cleaned up its internal streams on spawn
            # failure, so there is nothing to close here.
            raise MCPConnectionError(
                f"cannot start {server['name']}: command not found: "
                f"{server.get('command', '')}") from None
        except asyncio.TimeoutError:
            raise MCPConnectionError(
                f"timed out connecting to {server['name']} "
                f"after {HANDSHAKE_TIMEOUT:.0f}s") from None
        except BaseException as e:  # noqa: BLE001
            raise MCPConnectionError(
                f"cannot connect to {server['name']}: {e}") from None
        read, write = streams[0], streams[1]
        from mcp import ClientSession
        session = ClientSession(read, write)
        try:
            await session.__aenter__()
            await asyncio.wait_for(
                session.initialize(), timeout=HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError:
            await self._safe_close(session)
            await self._safe_close(ctx)
            raise MCPConnectionError(
                f"{server['name']} did not respond to the MCP initialize "
                f"request within {HANDSHAKE_TIMEOUT:.0f}s") from None
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
