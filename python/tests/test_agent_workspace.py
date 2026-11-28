"""Tests for the fixed Agent coding workspace and execution modes.

These tests cover the boundary, the three explicit modes, the test-runner
allowlist, and the force-prompt approval path for destructive tools. They do
not claim that any mode is an OS sandbox.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.agent.approval import ApprovalManager
from app.agent.tool_registry import Tool, ToolContext, ToolRegistry
from app.agent.tools.workspace_tools import CODING_WORKSPACE_TOOLS
from app.agent.workspace import (
    MODE_APPROVAL_SHELL,
    MODE_NONE,
    MODE_TEST_RUNNER,
    WorkspaceError,
    WorkspaceRuntime,
)


@pytest.fixture()
def ws(tmp_path):
    return WorkspaceRuntime(tmp_path / "agent" / "workspace")


def test_default_mode_is_none_and_not_explicit(ws):
    assert ws.mode == MODE_NONE
    assert ws.explicit is False
    assert ws.describe()["mode"] == MODE_NONE


def test_approval_shell_requires_risk_ack(ws):
    with pytest.raises(WorkspaceError):
        ws.set_mode(MODE_APPROVAL_SHELL)
    assert ws.set_mode(MODE_APPROVAL_SHELL, acknowledge_risk=True) == MODE_APPROVAL_SHELL
    assert ws.explicit is True
    # Persisted across a new runtime instance.
    again = WorkspaceRuntime(ws.root)
    assert again.mode == MODE_APPROVAL_SHELL and again.explicit is True


@pytest.mark.parametrize("path", ["../escape.txt", "/etc/passwd", "~/.ssh/id_rsa", "C:\\x"])
def test_path_guards_reject_escapes(ws, path):
    with pytest.raises(WorkspaceError):
        ws.resolve(path)


def test_symlink_escape_rejected(ws, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = ws.root / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(WorkspaceError):
        ws.resolve("link.txt")


def test_write_read_edit_and_search(ws):
    written = ws.write_file("src/main.py", "print('one')\n")
    assert written["additions"] == 1 and written["deletions"] == 0
    plain = ws.read_file("src/main.py")
    assert plain["line_count"] == 1 and "1 |" not in plain["content"]
    numbered = ws.read_file("src/main.py", with_line_numbers=True)
    assert "    1 | print('one')" in numbered["content"]
    edited = ws.edit_file("src/main.py", "one", "two")
    assert edited["replacements"] == 1
    assert edited["additions"] == 1 and edited["deletions"] == 1
    assert "+" in edited["diff"] and "-" in edited["diff"]
    assert "two" in ws.read_file("src/main.py")["content"]
    found = ws.search_text("two", "src")
    assert found["matches"] and found["matches"][0]["line"] == 1
    listed = ws.list_dir("src")
    assert any(e["name"] == "main.py" for e in listed["entries"])


def test_write_rejects_directory_and_too_large(ws):
    ws.write_file("ok.txt", "ok")
    with pytest.raises(WorkspaceError):
        ws.write_file(".", "no")
    with pytest.raises(WorkspaceError):
        ws.write_file("big.txt", "x" * (1024 * 1024 + 1))


def test_run_test_requires_test_runner_mode(ws):
    with pytest.raises(WorkspaceError):
        ws.run_test("python -m compileall -q .")
    ws.set_mode(MODE_TEST_RUNNER)
    result = ws.run_test("python -m compileall -q .")
    assert result["mode"] == MODE_TEST_RUNNER
    assert result["timed_out"] is False
    assert result["exit_code"] == 0


def test_test_runner_rejects_non_allowlisted_and_shell_meta(ws):
    ws.set_mode(MODE_TEST_RUNNER)
    with pytest.raises(WorkspaceError):
        ws.run_test("python -c 'print(1)'")
    with pytest.raises(WorkspaceError):
        ws.run_test("pytest -q; rm -rf /")
    with pytest.raises(WorkspaceError):
        ws.run_test("npm run deploy")


def test_shell_mode_runs_command_and_writes_audit(ws):
    with pytest.raises(WorkspaceError):
        ws.run_command("echo hi")
    ws.set_mode(MODE_APPROVAL_SHELL, acknowledge_risk=True)
    result = ws.run_command("echo hi")
    assert result["mode"] == MODE_APPROVAL_SHELL
    assert result["exit_code"] == 0
    assert "hi" in result["output"]
    assert ws.audit_path.exists()
    assert "echo hi" in ws.audit_path.read_text(encoding="utf-8")


def _wait_pending(manager: ApprovalManager, session_id: str, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pending = [r for r in manager.list_pending() if r["session_id"] == session_id]
        if pending:
            return pending[0]
        time.sleep(0.02)
    raise AssertionError("no pending approval request")


def test_force_prompt_overrides_session_auto():
    manager = ApprovalManager(default_timeout=5.0)
    manager.set_policy("s", "auto")
    holder: dict = {}

    def _gate():
        holder["result"] = manager.gate(
            "s", "run_command", {"command": "echo hi"}, force_prompt=True,
        )

    t = threading.Thread(target=_gate, daemon=True)
    t.start()
    pending = _wait_pending(manager, "s")
    manager.reject(pending["id"], "s")
    t.join(timeout=5)
    assert holder["result"].approved is False
    assert holder["result"].reason == "reject"


def test_force_prompt_respects_explicit_tool_auto():
    manager = ApprovalManager(default_timeout=5.0)
    manager.set_policy("s", "auto")
    manager.set_tool_policy("s", "run_command", "auto")
    result = manager.gate(
        "s", "run_command", {"command": "echo hi"}, force_prompt=True,
    )
    assert result.approved is True
    assert result.reason == "auto"


def test_workspace_tools_use_context_runtime(ws):
    ctx = ToolContext(workspace=ws)
    reg = ToolRegistry()
    for tool in CODING_WORKSPACE_TOOLS:
        reg.register(tool)
    ws.write_file("a.txt", "hello")
    assert reg.execute("read_file", {"path": "a.txt"}, ctx)["content"] == "hello"
    assert reg.get("run_command").requires_confirmation is True
    assert reg.get("edit_lines").requires_confirmation is True
    out = reg.execute("edit_lines", {
        "path": "a.txt", "start_line": 1, "new_text": "HELLO",
    }, ctx)
    assert out["ok"] is True and out["additions"] == 1 and out["deletions"] == 1
    assert ws.read_file("a.txt")["content"] == "HELLO"
    assert reg.execute("read_file", {"path": "../x"}, ctx)["error"]


def test_missing_workspace_context_fails_closed():
    ctx = ToolContext()
    reg = ToolRegistry()
    for tool in CODING_WORKSPACE_TOOLS:
        reg.register(tool)
    assert "error" in reg.execute("read_file", {"path": "a.txt"}, ctx)
    assert isinstance(
        Tool(name="x", description="x", parameters={}, handler=lambda p, c: {}, category="x"),
        Tool,
    )


class TestExecutionAPI:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from app import main as M

        monkeypatch.setattr(M, "APP_ROOT", tmp_path)
        M._AGENT_SINGLETON.clear()
        with TestClient(M.app) as c:
            yield c
        M._AGENT_SINGLETON.clear()

    def test_get_defaults_to_none_and_lists_three_modes(self, client):
        r = client.get("/api/agent/execution-mode")
        assert r.status_code == 200
        d = r.json()
        assert d["mode"] == "none" and d["explicit"] is False
        assert {m["id"] for m in d["modes"]} == {"none", "test_runner", "approval_shell"}

    def test_shell_requires_acknowledgement(self, client):
        r = client.put("/api/agent/execution-mode", json={"mode": "approval_shell"})
        assert r.status_code == 400

    def test_set_test_runner_persists(self, client):
        r = client.put("/api/agent/execution-mode", json={"mode": "test_runner"})
        assert r.status_code == 200
        assert r.json()["mode"] == "test_runner" and r.json()["explicit"] is True
        again = client.get("/api/agent/execution-mode").json()
        assert again["mode"] == "test_runner"

    def test_unknown_mode_rejected(self, client):
        r = client.put("/api/agent/execution-mode", json={"mode": "sandbox"})
        assert r.status_code == 400

    def test_coding_tools_are_opt_in_and_marked(self, client):
        before = client.get("/api/agent/tools").json()["tools"]
        assert not any(t["name"] == "run_command" for t in before)
        r = client.post("/api/agent/skills/coding_workspace", json={"enabled": True})
        assert r.status_code == 200
        tools = client.get("/api/agent/tools").json()["tools"]
        by_name = {t["name"]: t for t in tools}
        assert by_name["run_command"]["requires_confirmation"] is True
        assert by_name["run_command"]["required_permission"] == "danger-full-access"
        assert by_name["write_file"]["requires_confirmation"] is True
        assert by_name["read_file"]["required_permission"] == "read-only"


def test_edit_lines_replaces_only_selected_range(ws):
    ws.write_file("main.py", "one\ntwo\nthree\nfour\n")
    result = ws.edit_lines(
        "main.py", 2, 3, "TWO\nTHREE", expected_text="two\nthree",
    )
    assert result["operation"] == "replace"
    assert result["start_line"] == 2 and result["end_line"] == 3
    assert result["additions"] == 2 and result["deletions"] == 2
    assert ws.read_file("main.py")["content"] == "one\nTWO\nTHREE\nfour\n"


def test_edit_lines_delete_and_insert(ws):
    ws.write_file("main.py", "one\ntwo\nthree\n")
    deleted = ws.edit_lines("main.py", 2, 2, "", operation="delete")
    assert deleted["additions"] == 0 and deleted["deletions"] == 1
    assert ws.read_file("main.py")["content"] == "one\nthree\n"

    before = ws.edit_lines("main.py", 2, None, "inserted", operation="insert_before")
    assert before["operation"] == "insert_before"
    assert ws.read_file("main.py")["content"] == "one\ninserted\nthree\n"

    ws.edit_lines("main.py", 1, None, "after-one", operation="insert_after")
    assert ws.read_file("main.py")["content"] == "one\nafter-one\ninserted\nthree\n"


def test_edit_lines_guards_and_bounds(ws):
    ws.write_file("main.py", "one\ntwo\n")
    with pytest.raises(WorkspaceError):
        ws.edit_lines("main.py", 2, 2, "x", expected_text="wrong")
    with pytest.raises(WorkspaceError):
        ws.edit_lines("main.py", 1, 3, "x")
    with pytest.raises(WorkspaceError):
        ws.edit_lines("main.py", 2, 1, "x")
    with pytest.raises(WorkspaceError):
        ws.edit_lines("main.py", 0, None, "x", operation="insert_before")
    with pytest.raises(WorkspaceError):
        ws.edit_lines("main.py", 3, None, "", operation="insert_after")
