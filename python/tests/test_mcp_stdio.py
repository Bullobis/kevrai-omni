"""Tests for the local ``stdio`` MCP transport added on top of the existing
remote streamable-HTTP / SSE support.

A real FastMCP server is written to a temp file and spawned as a child process
(via ``python script.py``) through :class:`~app.mcp_manager.MCPManager`. We
assert tool discovery, a real tool call, and — critically — that the child
process is reaped when the session closes (no orphans), plus that a missing
command maps to a readable connection error instead of a 500.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

from app.mcp_manager import (
    MCPConfigError,
    MCPConnectionError,
    MCPManager,
)

# A FastMCP stdio server. It records its own PID into DEMO_PID_FILE so the test
# can afterwards assert the process is gone. Run as ``python server.py``.
_STDIO_SERVER = '''
import os
from mcp.server.fastmcp import FastMCP

m = FastMCP("demo-stdio")

pid_file = os.environ.get("DEMO_PID_FILE")
if pid_file:
    with open(pid_file, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


@m.tool()
def add(a: int, b: int) -> int:
    """add two integers"""
    return a + b


@m.tool()
def reverse(text: str) -> str:
    """reverse a string"""
    return text[::-1]


m.run()
'''


def _write_server(tmp_path) -> tuple[object, object]:
    script = tmp_path / "stdio_server.py"
    script.write_text(_STDIO_SERVER, encoding="utf-8")
    pid_file = tmp_path / "server.pid"
    return script, pid_file


def _child_env(pid_file) -> dict[str, str]:
    """构造传给 stdio 子进程的环境。

    PYTHONPATH 必须**显式**传：MCP SDK 给子进程的环境是它自己的白名单，
    **不含** PYTHONPATH。在 --target / venv 装的依赖下，少了这一项子进程
    会报 "No module named 'mcp.server'"——看起来像 mcp 没装，实际只是环境
    没继承过去。详见 app/mcp_manager.py 的注释。
    """
    env = {"DEMO_PID_FILE": str(pid_file)}
    if os.environ.get("PYTHONPATH"):
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    return env


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_gone(pid: int, timeout: float = 5.0) -> bool:
    """Return True once the pid is no longer running (poll briefly)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_alive(pid)


# ---------------------------------------------------------------------------
# config validation (stdio)
# ---------------------------------------------------------------------------
def test_stdio_requires_command(tmp_path):
    mgr = MCPManager(tmp_path)
    with pytest.raises(MCPConfigError):
        mgr.add_server("s", "stdio")


def test_stdio_stores_command_args_env(tmp_path):
    mgr = MCPManager(tmp_path)
    s = mgr.add_server(
        "s", "stdio", command="npx", args=["-y", "x"],
        env={"TOKEN": "abc"}, cwd="/tmp")
    assert s["command"] == "npx"
    assert s["args"] == ["-y", "x"]
    assert s["env"] == {"TOKEN": "abc"}
    assert s["cwd"] == "/tmp"
    # http/sse fields are untouched / absent
    assert "url" not in s


def test_stdio_rejects_bad_transport(tmp_path):
    mgr = MCPManager(tmp_path)
    with pytest.raises(MCPConfigError):
        mgr.add_server("s", "web", command="x")


# ---------------------------------------------------------------------------
# real stdio loopback
# ---------------------------------------------------------------------------
async def _stdio_loopback(tmp_path):
    script, pid_file = _write_server(tmp_path)
    mgr = MCPManager(tmp_path)
    added = mgr.add_server(
        "demo", "stdio",
        command=sys.executable,
        args=[str(script)],
        env=_child_env(pid_file),
    )
    tools = await mgr.a_list_tools(added["id"])
    result = await mgr.a_call(added["id"], "add", {"a": 40, "b": 2})
    return added, tools, result, pid_file


def test_stdio_discovery_call_and_reap(tmp_path):
    added, tools, result, pid_file = asyncio.run(_stdio_loopback(tmp_path))
    names = sorted(t["name"] for t in tools)
    assert names == ["add", "reverse"]
    assert any("integers" in t["description"] for t in tools)
    assert result["text"] == "42"
    assert result["structured"] == {"result": 42}
    assert result["isError"] is False
    # child process must have been reaped when the sessions closed
    pid = int(pid_file.read_text(encoding="utf-8").strip())
    assert _wait_gone(pid), f"stdio child {pid} still running after close"


async def _stdio_reverse_call(tmp_path):
    script, pid_file = _write_server(tmp_path)
    mgr = MCPManager(tmp_path)
    added = mgr.add_server(
        "demo", "stdio",
        command=sys.executable,
        args=[str(script)],
        env=_child_env(pid_file),
    )
    return await mgr.a_call(added["id"], "reverse", {"text": "abc"})


def test_stdio_second_tool_call(tmp_path):
    result = asyncio.run(_stdio_reverse_call(tmp_path))
    assert result["text"] == "cba"


# ---------------------------------------------------------------------------
# error mapping
# ---------------------------------------------------------------------------
async def _missing_command(tmp_path):
    mgr = MCPManager(tmp_path)
    s = mgr.add_server("dead", "stdio", command="no_such_binary_xyz_qw")
    with pytest.raises(MCPConnectionError) as exc:
        await mgr.a_list_tools(s["id"])
    return str(exc.value)


def test_missing_command_maps_to_connection_error(tmp_path):
    msg = asyncio.run(_missing_command(tmp_path))
    assert "command not found" in msg
    assert "no_such_binary_xyz_qw" in msg


async def _bad_script(tmp_path):
    # A script that exits immediately without speaking MCP -> initialize hangs
    # until the handshake timeout, which must raise MCPConnectionError.
    script = tmp_path / "broken.py"
    script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    mgr = MCPManager(tmp_path)
    s = mgr.add_server("broken", "stdio", command=sys.executable,
                      args=[str(script)])
    with pytest.raises(MCPConnectionError):
        await mgr.a_list_tools(s["id"])


def test_exiting_script_maps_to_connection_error(tmp_path):
    asyncio.run(_bad_script(tmp_path))
