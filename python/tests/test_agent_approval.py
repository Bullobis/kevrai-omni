"""Tool-call approval gate for the ReAct loop.

Default policy is ``auto``: every tool runs immediately, exactly as before.
Switching a session to ``confirm`` parks each tool call and blocks the loop
until the human approves (optionally editing the params), rejects, the request
times out, or the run is cancelled. These tests drive that timing
deterministically — a gate-based fake router + a recording tool, no real LLM.

Coverage:
  * auto policy (default) runs the tool immediately, creates no pending request.
  * confirm + approve runs the tool with the original params.
  * confirm + approve-with-edited-params runs the edited params.
  * confirm + reject skips the tool and feeds the loop a "用户未批准" observation
    (the model adapts / concludes) instead of crashing.
  * confirm + timeout behaves like a rejection (no permanent hang).
  * cancel() while the loop is waiting unblocks it and ends the run as cancelled.
  * REST: list / approve(+edit) / reject / policy endpoints + 404 paths.
"""
from __future__ import annotations

import asyncio
import threading
import time

from app.agent import Agent, AgentMemory, ToolContext
from app.agent.tool_registry import Tool, ToolRegistry


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class _GateRouter:
    """Scripted router whose chat() blocks on a gate the test controls."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0
        self._cond = threading.Condition()
        self._go = threading.Event()

    def is_ready(self):
        return True, "gate-mock"

    def chat(self, prompt: str, system: str = "", max_new_tokens: int = 2048, should_stop=None):
        with self._cond:
            idx = self.calls
            self.calls += 1
            self._cond.notify_all()
        if not self._go.wait(timeout=10):
            return {"ok": False, "error": "gate timeout", "model_name": "gate-mock"}
        self._go.clear()
        text = self.responses[min(idx, len(self.responses) - 1)]
        return {"ok": True, "text": text, "model_name": "gate-mock"}

    def wait_calls(self, n: int, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        with self._cond:
            while self.calls < n:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise AssertionError(f"timed out waiting for {n} chat calls (have {self.calls})")
                self._cond.wait(remaining)

    def release(self) -> None:
        self._go.set()


def _build_agent(tmp_path, router, hits: list[dict]) -> Agent:
    mem = AgentMemory(tmp_path / "approval.sqlite3")
    ctx = ToolContext(memory=mem, models_dir=tmp_path, app_root=tmp_path)
    # Non-empty dict skips the lazy hardware probe.
    ctx.hardware_info = {
        "gpu_vendor": "mock", "gpu_best_vram_gb": 0,
        "ram_total_gb": 0, "disk": {"free_gb": 0},
    }
    reg = ToolRegistry()

    def _rec(params, ctx):
        hits.append(dict(params))
        return {"ok": True, "ran": True}

    reg.register(Tool(
        name="rec_tool",
        description="records the params it was called with",
        parameters={"type": "object", "properties": {}},
        handler=_rec,
    ))
    return Agent(memory=mem, router=router, registry=reg, ctx=ctx)


def _run_in_thread(agent: Agent, message: str, session_id: str, holder: dict) -> threading.Thread:
    def _runner() -> None:
        holder["result"] = asyncio.run(agent.run(message, session_id=session_id))

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    return t


def _wait_pending(agent: Agent, session_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pend = [r for r in agent.approval.list_pending() if r["session_id"] == session_id]
        if pend:
            return pend[0]
        time.sleep(0.02)
    raise AssertionError("no pending approval request appeared")


# ===========================================================================
# 1. auto policy (default): tool runs immediately, no pending request
# ===========================================================================
def test_auto_policy_runs_tool_without_prompt(tmp_path):
    router = _GateRouter([
        'Action: rec_tool|{"query": "orig"}',
        "最终答案：done",
    ])
    hits: list[dict] = []
    agent = _build_agent(tmp_path, router, hits)
    holder: dict = {}
    sid = "auto-sess"

    t = _run_in_thread(agent, "go", sid, holder)
    router.wait_calls(1)
    router.release()
    router.wait_calls(2)
    router.release()
    t.join(timeout=5)
    assert not t.is_alive()
    r = holder["result"]

    assert r.success is True
    # Tool ran with its original params, no approval ever asked.
    assert hits == [{"query": "orig"}]
    assert agent.approval.list_pending() == []
    assert agent.approval.get_policy(sid) == "auto"


# ===========================================================================
# 2. confirm + approve -> runs the tool with the original params
# ===========================================================================
def test_confirm_approve_runs_tool(tmp_path):
    router = _GateRouter([
        'Action: rec_tool|{"query": "orig"}',
        "最终答案：done",
    ])
    hits: list[dict] = []
    agent = _build_agent(tmp_path, router, hits)
    agent.set_tool_approval_policy("approve-sess", "confirm")
    holder: dict = {}

    t = _run_in_thread(agent, "go", "approve-sess", holder)
    router.wait_calls(1)
    router.release()          # chat#1 -> tool action
    req = _wait_pending(agent, "approve-sess")       # loop blocked at the gate
    assert req["tool_name"] == "rec_tool"
    assert req["tool_params"] == {"query": "orig"}

    agent.approval.approve(req["id"])                # resolve -> run original
    router.wait_calls(2)
    router.release()           # loop continues -> chat#2
    t.join(timeout=5)
    assert not t.is_alive()
    r = holder["result"]

    assert r.success is True
    assert hits == [{"query": "orig"}]
    assert agent.approval.list_pending() == []


# ===========================================================================
# 3. confirm + approve with edited params -> runs the edited params
# ===========================================================================
def test_confirm_approve_with_edited_params(tmp_path):
    router = _GateRouter([
        'Action: rec_tool|{"query": "orig"}',
        "最终答案：done",
    ])
    hits: list[dict] = []
    agent = _build_agent(tmp_path, router, hits)
    agent.set_tool_approval_policy("edit-sess", "confirm")
    holder: dict = {}

    t = _run_in_thread(agent, "go", "edit-sess", holder)
    router.wait_calls(1)
    router.release()
    req = _wait_pending(agent, "edit-sess")

    agent.approval.approve(req["id"], edited_params={"query": "edited", "limit": 9})
    router.wait_calls(2)
    router.release()
    t.join(timeout=5)
    assert not t.is_alive()

    # The tool ran with the EDITED params, not the originals.
    assert hits == [{"query": "edited", "limit": 9}]


# ===========================================================================
# 4. confirm + reject -> tool skipped, "用户未批准" observation, loop adapts
# ===========================================================================
def test_confirm_reject_skips_tool_and_observes(tmp_path):
    router = _GateRouter([
        'Action: rec_tool|{"query": "orig"}',
        "最终答案：改道完成",
    ])
    hits: list[dict] = []
    agent = _build_agent(tmp_path, router, hits)
    agent.set_tool_approval_policy("reject-sess", "confirm")
    holder: dict = {}

    t = _run_in_thread(agent, "go", "reject-sess", holder)
    router.wait_calls(1)
    router.release()
    req = _wait_pending(agent, "reject-sess")

    agent.approval.reject(req["id"])
    router.wait_calls(2)
    router.release()
    t.join(timeout=5)
    assert not t.is_alive()
    r = holder["result"]

    assert r.success is True
    # The tool MUST NOT have run.
    assert hits == []
    # The rejected step carries a graceful ApprovalDenied observation.
    step = [s for s in r.steps if s.action_tool == "rec_tool"][0]
    assert step.observation.get("ok") is False
    assert step.observation.get("error_type") == "ApprovalDenied"
    assert "未批准" in step.observation.get("error", "")


# ===========================================================================
# 5. confirm + timeout -> treated as a rejection (no permanent hang)
# ===========================================================================
def test_confirm_timeout_treated_as_rejection(tmp_path):
    router = _GateRouter([
        'Action: rec_tool|{"query": "x"}',
        "最终答案：超时后结束",
    ])
    hits: list[dict] = []
    agent = _build_agent(tmp_path, router, hits)
    agent.set_tool_approval_policy("timeout-sess", "confirm")
    agent.approval.default_timeout = 0.4   # short, deterministic
    holder: dict = {}

    t = _run_in_thread(agent, "go", "timeout-sess", holder)
    router.wait_calls(1)
    router.release()

    # Nobody resolves it; wait for the pending request to time out and clear.
    deadline = time.time() + 5
    while time.time() < deadline and agent.approval.list_pending():
        time.sleep(0.05)
    assert agent.approval.list_pending() == []

    router.wait_calls(2)
    router.release()
    t.join(timeout=5)
    assert not t.is_alive()
    r = holder["result"]

    assert r.success is True
    assert hits == []   # tool never ran
    step = [s for s in r.steps if s.action_tool == "rec_tool"][0]
    assert step.observation.get("error_type") == "ApprovalDenied"


# ===========================================================================
# 6. cancel() while the loop is waiting unblocks it -> cancelled run
# ===========================================================================
def test_cancel_while_waiting_unblocks(tmp_path):
    router = _GateRouter([
        'Action: rec_tool|{"query": "x"}',
        "最终答案：不应到达",
    ])
    hits: list[dict] = []
    agent = _build_agent(tmp_path, router, hits)
    agent.set_tool_approval_policy("cancel-sess", "confirm")
    holder: dict = {}

    t = _run_in_thread(agent, "go", "cancel-sess", holder)
    router.wait_calls(1)
    router.release()
    _wait_pending(agent, "cancel-sess")

    # Cancel while blocked on the approval gate.
    assert agent.cancel("cancel-sess") is True
    t.join(timeout=5)
    assert not t.is_alive(), "run did not stop within bounded time"
    r = holder["result"]

    assert r.cancelled is True
    assert r.error == "cancelled"
    assert hits == []                      # tool never ran
    assert agent.approval.list_pending() == []


# ===========================================================================
# 7. REST endpoints (through the real TestClient singleton)
# ===========================================================================
def test_rest_approval_policy_endpoints(hub_client):
    hub_client.get("/api/agent/tools")  # build the singleton

    # Default policy is auto.
    r = hub_client.get("/api/agent/approval-policy?session_id=rest-policy")
    assert r.status_code == 200
    assert r.json()["policy"] == "auto"

    # Switch to confirm.
    r = hub_client.put("/api/agent/approval-policy",
                       json={"session_id": "rest-policy", "policy": "confirm"})
    assert r.status_code == 200
    assert r.json()["policy"] == "confirm"

    r = hub_client.get("/api/agent/approval-policy?session_id=rest-policy")
    assert r.json()["policy"] == "confirm"

    # Invalid policy -> 400.
    r = hub_client.put("/api/agent/approval-policy",
                       json={"session_id": "rest-policy", "policy": "bogus"})
    assert r.status_code == 400

    # Reset so we don't leak confirm into later tests.
    hub_client.put("/api/agent/approval-policy",
                   json={"session_id": "rest-policy", "policy": "auto"})


def test_rest_approve_unknown_id_is_404(hub_client):
    hub_client.get("/api/agent/tools")
    assert hub_client.post("/api/agent/approvals/nope/approve", json={}).status_code == 404
    assert hub_client.post("/api/agent/approvals/nope/reject", json={}).status_code == 404


def test_rest_approval_approve_resolves_blocked_gate(hub_client):
    hub_client.get("/api/agent/tools")
    from app import main as app_main

    agent = app_main._AGENT_SINGLETON["agent"]
    agent.set_tool_approval_policy("rest-appr", "confirm")
    try:
        holder: dict = {}

        def _park() -> None:
            holder["gate"] = agent.approval.gate("rest-appr", "rest_tool", {"x": 1})

        th = threading.Thread(target=_park, daemon=True)
        th.start()

        # Wait for the blocked gate to surface as a pending request.
        pid = None
        deadline = time.time() + 5
        while time.time() < deadline:
            body = hub_client.get("/api/agent/approvals").json()
            pend = [r for r in body["requests"] if r["session_id"] == "rest-appr"]
            if pend:
                pid = pend[0]["id"]
                break
            time.sleep(0.05)
        assert pid, "blocked gate never surfaced as a pending request"

        # Approve over REST with edited params.
        resp = hub_client.post(f"/api/agent/approvals/{pid}/approve",
                               json={"edited_params": {"x": 2, "y": 3}})
        assert resp.status_code == 200
        assert resp.json()["decision"] == "edit"

        th.join(timeout=5)
        assert not th.is_alive()
        assert holder["gate"].approved is True
        assert holder["gate"].effective_params == {"x": 2, "y": 3}
        assert agent.approval.list_pending() == []
    finally:
        agent.set_tool_approval_policy("rest-appr", "auto")


def test_rest_approval_reject_resolves_blocked_gate(hub_client):
    hub_client.get("/api/agent/tools")
    from app import main as app_main

    agent = app_main._AGENT_SINGLETON["agent"]
    agent.set_tool_approval_policy("rest-rej", "confirm")
    try:
        holder: dict = {}

        def _park() -> None:
            holder["gate"] = agent.approval.gate("rest-rej", "rest_tool", {"x": 1})

        th = threading.Thread(target=_park, daemon=True)
        th.start()

        pid = None
        deadline = time.time() + 5
        while time.time() < deadline:
            body = hub_client.get("/api/agent/approvals").json()
            pend = [r for r in body["requests"] if r["session_id"] == "rest-rej"]
            if pend:
                pid = pend[0]["id"]
                break
            time.sleep(0.05)
        assert pid

        resp = hub_client.post(f"/api/agent/approvals/{pid}/reject", json={})
        assert resp.status_code == 200
        assert resp.json()["decision"] == "reject"

        th.join(timeout=5)
        assert not th.is_alive()
        assert holder["gate"].approved is False
        assert holder["gate"].reason == "reject"
    finally:
        agent.set_tool_approval_policy("rest-rej", "auto")
