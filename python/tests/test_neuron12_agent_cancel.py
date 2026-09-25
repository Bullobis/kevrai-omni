"""Neuron-12: cooperative cancellation for ``agent.run()``.

The ReAct loop is synchronous (router.chat / registry.execute block), so a
cancellation request can only be honoured at explicit checkpoints. These tests
drive the timing deterministically with a gate-based mock router / blocking
tool — no real network, GPU, or MNN subprocess.

Coverage:
  * multi-step run cancelled mid-flight stops within a bounded time, makes no
    further LLM/tool calls, returns cancelled=True, releases its token.
  * a cancel signal does NOT leak into the next run on the same session.
  * two concurrent sessions: cancelling one does not affect the other.
  * cancel() is idempotent.
  * cancelling after the run finished is a no-op.
  * the non-cancelled path still completes normally (regression).
  * real starlette TestClient /ws/agent: client drops the socket mid-run and
    the offloaded run actually stops, no stale token / step_callback.
  * POST /api/agent/cancel endpoint.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.agent import Agent, AgentMemory, ToolContext
from app.agent.tool_registry import Tool, ToolRegistry


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class _GateRouter:
    """Scripted router whose chat() blocks on a gate the test controls.

    Each chat() call increments ``calls`` under a condition, then waits on the
    ``_go`` event. The test uses ``wait_calls(n)`` to know the n-th chat is
    *blocking*, then ``release()`` to let it return. No real inference.
    """

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.tools: list[str] = []
        self._cond = threading.Condition()
        self._go = threading.Event()

    def is_ready(self):
        return True, "gate-mock"

    def chat(self, prompt: str, system: str = "", max_new_tokens: int = 2048):
        with self._cond:
            idx = self.calls
            self.calls += 1
            self._cond.notify_all()
        # Bound wait so a forgotten release fails loudly instead of hanging the
        # suite. This wait CANNOT be interrupted by the cancel Event — that is
        # exactly the documented boundary (a blocking chat() runs to its next
        # checkpoint).
        if not self._go.wait(timeout=10):
            return {"ok": False, "error": "gate timeout", "model_name": "gate-mock"}
        self._go.clear()  # re-arm for the next blocking chat()
        text = self.responses[min(idx, len(self.responses) - 1)]
        return {"ok": True, "text": text, "model_name": "gate-mock"}

    def wait_calls(self, n: int, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        with self._cond:
            while self.calls < n:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise AssertionError(
                        f"timed out waiting for {n} chat calls (have {self.calls})"
                    )
                self._cond.wait(remaining)

    def release(self) -> None:
        self._go.set()


def _build_agent(tmp_path, router, tool_hits: list[str] | None = None) -> Agent:
    mem = AgentMemory(tmp_path / "cancel.sqlite3")
    ctx = ToolContext(memory=mem, models_dir=tmp_path, app_root=tmp_path)
    # Non-empty dict skips the lazy hardware probe (which would otherwise block
    # on a ThreadPoolExecutor inside our worker thread).
    ctx.hardware_info = {
        "gpu_vendor": "mock", "gpu_best_vram_gb": 0,
        "ram_total_gb": 0, "disk": {"free_gb": 0},
    }
    reg = ToolRegistry()
    hits: list[str] = tool_hits if tool_hits is not None else []

    def _rec(params, ctx):
        hits.append("rec_tool")
        return {"ok": True, "recorded": True}

    reg.register(Tool(
        name="rec_tool",
        description="records that it was called",
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


# ===========================================================================
# 1. Cancel mid-flight at the post-LLM checkpoint
# ===========================================================================
def test_cancel_mid_llm_stops_run_and_makes_no_further_calls(tmp_path):
    router = _GateRouter([
        "Action: rec_tool|{}",          # chat #1 → tool call
        "Action: rec_tool|{}",          # chat #2 → would be another tool
        "最终答案：不应到达这里",          # chat #3 → never reached
    ])
    agent = _build_agent(tmp_path, router)
    holder: dict = {}
    sid = "cancel-mid"

    t = _run_in_thread(agent, "做点事", sid, holder)
    router.wait_calls(1)          # chat #1 is blocking
    # Release chat #1 → tool runs → loop iter 2 → chat #2 blocks.
    router.release()
    router.wait_calls(2)          # chat #2 is blocking now
    # Cancel while chat #2 is in-flight (blocking).
    assert agent.cancel(sid) is True
    router.release()              # let chat #2 return; checkpoint-after-LLM fires

    t.join(timeout=5)
    assert not t.is_alive(), "run did not stop within bounded time"
    result = holder["result"]

    assert result.cancelled is True
    assert result.success is False
    assert result.error == "cancelled"
    assert "已取消" in result.answer
    # chat #3 must never happen, and the second tool must never execute.
    assert router.calls == 2
    assert result.tools_used == ["rec_tool"]
    # Token released back to the pool on completion.
    assert sid not in agent._cancel_events


# ===========================================================================
# 2. Cancel signal does NOT leak into the next run
# ===========================================================================
def test_cancel_signal_does_not_leak_to_next_run(tmp_path):
    router = _GateRouter([
        "Action: rec_tool|{}",        # run #1 chat #1
        "最终答案：第二次正常完成",     # run #2 chat #1 (index 1 in the list)
    ])
    agent = _build_agent(tmp_path, router)
    sid = "no-leak"

    # Run #1: cancel mid-flight.
    h1: dict = {}
    t1 = _run_in_thread(agent, "第一次", sid, h1)
    router.wait_calls(1)
    agent.cancel(sid)
    router.release()
    t1.join(timeout=5)
    assert h1["result"].cancelled is True

    # Run #2 on the SAME session: must complete normally, despite the prior
    # cancel. The token was cleared/removed when run #1 finished.
    h2: dict = {}
    t2 = _run_in_thread(agent, "第二次", sid, h2)
    router.wait_calls(2)
    router.release()
    t2.join(timeout=5)
    r2 = h2["result"]
    assert r2.cancelled is False
    assert r2.success is True
    assert r2.error == ""


# ===========================================================================
# 3. Concurrent sessions: cancelling A does not touch B
# ===========================================================================
def test_cancel_one_session_does_not_affect_the_other(tmp_path):
    # Both sessions share one router; its chat() inspects the prompt (which
    # embeds the user message) to return an appropriate final answer.
    class _SharingRouter(_GateRouter):
        def chat(self, prompt, system="", max_new_tokens=2048):
            with self._cond:
                idx = self.calls
                self.calls += 1
                self._cond.notify_all()
            if not self._go.wait(timeout=10):
                return {"ok": False, "error": "gate timeout"}
            self._go.clear()
            return {"ok": True, "text": "最终答案：完成", "model_name": "gate-mock"}

    router = _SharingRouter([])
    agent = _build_agent(tmp_path, router)

    ha, hb = {}, {}
    ta = _run_in_thread(agent, "AAA 会话A的请求", "sess-A", ha)
    tb = _run_in_thread(agent, "BBB 会话B的请求", "sess-B", hb)

    router.wait_calls(2)   # both chats are in-flight
    assert agent.cancel("sess-A") is True   # cancel only A
    router.release()       # release both blocked chats
    ta.join(timeout=5)
    tb.join(timeout=5)

    assert ha["result"].cancelled is True
    assert hb["result"].cancelled is False
    assert hb["result"].success is True


# ===========================================================================
# 4. cancel() is idempotent
# ===========================================================================
def test_cancel_is_idempotent(tmp_path):
    router = _GateRouter(["最终答案：x"])
    agent = _build_agent(tmp_path, router)
    holder: dict = {}
    sid = "idem"
    t = _run_in_thread(agent, "hi", sid, holder)
    router.wait_calls(1)
    # Two cancels in a row: both report a running run, neither raises.
    assert agent.cancel(sid) is True
    assert agent.cancel(sid) is True
    router.release()
    t.join(timeout=5)
    assert holder["result"].cancelled is True


# ===========================================================================
# 5. cancel() after the run finished is a no-op
# ===========================================================================
def test_cancel_after_run_finished_is_noop(tmp_path):
    router = _GateRouter(["最终答案：完成了"])
    agent = _build_agent(tmp_path, router)
    holder: dict = {}
    sid = "done"
    t = _run_in_thread(agent, "hi", sid, holder)
    router.wait_calls(1)
    router.release()
    t.join(timeout=5)
    assert holder["result"].success is True

    # No run in flight → cancel reports False, raises nothing.
    assert agent.cancel(sid) is False
    assert sid not in agent._cancel_events


# ===========================================================================
# 6. Regression: a run with no cancel still completes normally
# ===========================================================================
def test_normal_completion_unchanged(tmp_path):
    router = _GateRouter(["Action: rec_tool|{}", "最终答案：正常结束"])
    agent = _build_agent(tmp_path, router)
    holder: dict = {}
    t = _run_in_thread(agent, "帮我", "regress", holder)
    router.wait_calls(1)
    router.release()
    router.wait_calls(2)
    router.release()
    t.join(timeout=5)
    r = holder["result"]
    assert r.success is True
    assert r.cancelled is False
    assert r.error == ""
    assert r.tools_used == ["rec_tool"]
    assert "正常结束" in r.answer


# ===========================================================================
# 7. Real starlette TestClient: WS drop mid-run stops the background run
# ===========================================================================
def test_ws_disconnect_mid_run_stops_offloaded_run(hub_client):
    hub_client.get("/api/agent/tools")  # build the singleton
    from app import main as app_main

    agent = app_main._AGENT_SINGLETON["agent"]
    agent.ctx.hardware_info = {
        "gpu_vendor": "mock", "gpu_best_vram_gb": 0,
        "ram_total_gb": 0, "disk": {"free_gb": 0},
    }
    router = _GateRouter([
        "Action: rec_tool|{}",            # chat #1
        "最终答案：不应到达",               # chat #2 — never reached
    ])
    orig = agent.router
    agent.router = router
    try:
        with hub_client.websocket_connect("/ws/agent/ws-stop") as ws:
            ws.send_json({"message": "请慢慢想"})
            router.wait_calls(1)          # chat #1 blocking on the server thread
            ws.close()                    # drop the client mid-inference

            # Wait until the server has observed the disconnect and signalled
            # cancellation (the token for this session must become set).
            deadline = time.time() + 5
            seen_cancel = False
            while time.time() < deadline:
                ev = agent._cancel_events.get("ws-stop")
                if ev is not None and ev.is_set():
                    seen_cancel = True
                    break
                time.sleep(0.01)
            assert seen_cancel, "server never cancelled the run on disconnect"

            router.release()               # let chat #1 return → checkpoint fires

        # The offloaded run must drain within a bounded time and drop its token.
        deadline = time.time() + 5
        while time.time() < deadline and "ws-stop" in agent._cancel_events:
            time.sleep(0.02)
        assert "ws-stop" not in agent._cancel_events, "run leaked its cancel token"
        assert router.calls == 1, "run continued past the cancellation point"
        assert agent._step_callback is None, "step callback not reset"
    finally:
        agent.router = orig


def test_ws_explicit_cancel_frame_mid_run(hub_client):
    hub_client.get("/api/agent/tools")
    from app import main as app_main

    agent = app_main._AGENT_SINGLETON["agent"]
    agent.ctx.hardware_info = {
        "gpu_vendor": "mock", "gpu_best_vram_gb": 0,
        "ram_total_gb": 0, "disk": {"free_gb": 0},
    }
    router = _GateRouter(["最终答案：x"])
    orig = agent.router
    agent.router = router
    try:
        with hub_client.websocket_connect("/ws/agent/ws-cancel-frame") as ws:
            ws.send_json({"message": "开始"})
            router.wait_calls(1)
            ws.send_json({"event": "cancel"})
            # ack frame
            ack = ws.receive_json()
            assert ack.get("event") == "cancelled"
            router.release()
            # final event should report cancelled
            final = ws.receive_json()
            assert final["event"] == "final"
            assert final["cancelled"] is True
            assert final["success"] is False
    finally:
        agent.router = orig


# ===========================================================================
# 8. REST endpoint
# ===========================================================================
def test_rest_agent_cancel_endpoint(hub_client):
    hub_client.get("/api/agent/tools")  # build singleton
    from app import main as app_main

    agent = app_main._AGENT_SINGLETON["agent"]
    agent.ctx.hardware_info = {
        "gpu_vendor": "mock", "gpu_best_vram_gb": 0,
        "ram_total_gb": 0, "disk": {"free_gb": 0},
    }
    router = _GateRouter(["最终答案：不该完成"])
    orig = agent.router
    agent.router = router
    try:
        holder: dict = {}
        t = _run_in_thread(agent, "REST 触发", "rest-cancel", holder)
        router.wait_calls(1)

        resp = hub_client.post("/api/agent/cancel", json={"session_id": "rest-cancel"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] is True
        assert body["session_id"] == "rest-cancel"

        # Idempotent second call while still running.
        again = hub_client.post("/api/agent/cancel", json={"session_id": "rest-cancel"})
        assert again.json()["cancelled"] is True

        router.release()
        t.join(timeout=5)
        assert not t.is_alive()
        assert holder["result"].cancelled is True
    finally:
        agent.router = orig


def test_rest_agent_cancel_noop_when_idle(hub_client):
    resp = hub_client.post("/api/agent/cancel", json={"session_id": "nobody-home"})
    assert resp.status_code == 200
    assert resp.json() == {"cancelled": False, "session_id": "nobody-home"}


def test_rest_agent_cancel_requires_bearer():
    from fastapi.testclient import TestClient
    from app import main as app_main

    with TestClient(app_main.app) as c:
        # Override the conftest-injected bearer by sending no auth header.
        c.headers.pop("authorization", None)
        resp = c.post("/api/agent/cancel", json={"session_id": "x"})
        assert resp.status_code == 401
