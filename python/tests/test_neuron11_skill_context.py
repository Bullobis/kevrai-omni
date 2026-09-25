"""Neuron-11 (K-Cortex slice 11) evidence-based audit + regression tests.

Scope: three previously shallow paths —
  1. Skill execution runtime (registry wiring, disable/enable, exception
     isolation, imported/builtin isolation, malformed-skill tolerance).
  2. Agent context window / history truncation (duplication regression,
     boundaries, system-prompt protection, per-session isolation).
  3. WebSocket /ws/agent/{session_id} lifecycle (auth, bad input, happy path,
     disconnect mid-run must not leak an unhandled exception).

Each test either locks a behaviour audited as correct (guard) or reproduces a
bug that was confirmed and minimally fixed.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from starlette.websockets import WebSocketDisconnect

from app.agent import Agent, AgentMemory, ToolContext
from app.agent.skill import Skill, SkillManager
from app.agent.tool_registry import Tool, ToolRegistry
from app.agent.tools import build_default_registry, build_skill_manager


# ===========================================================================
# Shared fakes
# ===========================================================================
class _CaptureRouter:
    """Records the last prompt it was handed; always emits a final answer."""

    def __init__(self, text: str = "最终答案：done") -> None:
        self.text = text
        self.prompts: list[str] = []

    def is_ready(self) -> tuple[bool, str]:
        return True, "mock"

    def chat(self, prompt, system="", max_new_tokens=2048):
        self.prompts.append(prompt)
        return {"ok": True, "text": self.text, "model_name": "mock"}


def _make_agent(tmp_path, router=None, skill_manager=None):
    mem = AgentMemory(tmp_path / "n11.sqlite3")
    ctx = ToolContext(memory=mem, models_dir=tmp_path, app_root=tmp_path)
    agent = Agent(
        memory=mem,
        router=router or _CaptureRouter(),
        registry=None,
        ctx=ctx,
        skill_manager=skill_manager,
    )
    return mem, ctx, agent


# ===========================================================================
# Path 1 — skill execution runtime
# ===========================================================================
class TestSkillRuntime:
    def test_disabled_skill_tools_leave_registry(self):
        """Disabling a skill must rebuild the registry WITHOUT its tools;
        required skills stay. Re-enabling must bring them back."""
        mgr = build_skill_manager(state_path=None)
        before = set(mgr.active_tool_names())
        assert "drama_storycraft" in before

        mgr.disable("drama_studio")
        reg = mgr.build_registry()
        assert reg.get("drama_storycraft") is None
        assert "drama_storycraft" not in reg.list_names()
        # required core tools unaffected
        assert reg.get("set_preference") is not None
        assert reg.get("get_preferences") is not None

        mgr.enable("drama_studio")
        reg2 = mgr.build_registry()
        assert reg2.get("drama_storycraft") is not None

    def test_required_skill_cannot_be_disabled(self):
        mgr = build_skill_manager(state_path=None)
        with pytest.raises(ValueError):
            mgr.disable("core")
        assert mgr.is_enabled("core") is True
        # A required skill must never be persisted as disabled.
        assert "core" not in mgr.disabled_ids()

    def test_unknown_skill_toggle_raises(self):
        mgr = build_skill_manager(state_path=None)
        with pytest.raises(KeyError):
            mgr.disable("does_not_exist")

    def test_tool_exception_is_isolated(self):
        """A tool handler raising must become {ok:False}, never escape."""

        def _boom(params, ctx):
            raise RuntimeError("kaboom")

        t = Tool(name="boom_tool", description="x", parameters={}, handler=_boom)
        reg = ToolRegistry()
        reg.register(t)
        out = reg.execute("boom_tool", {}, None)
        assert out == {"ok": False, "error": "kaboom", "error_type": "RuntimeError"}

    def test_tool_non_dict_result_is_wrapped(self):
        def _returns_list(params, ctx):
            return [1, 2, 3]

        t = Tool(name="list_tool", description="x", parameters={}, handler=_returns_list)
        reg = ToolRegistry()
        reg.register(t)
        out = reg.execute("list_tool", {}, None)
        assert out["ok"] is True
        assert out["result"] == [1, 2, 3]

    def test_unknown_tool_is_graceful(self):
        reg = ToolRegistry()
        out = reg.execute("nope", {}, None)
        assert out["ok"] is False
        assert out["error_type"] == "UnknownTool"

    def test_agent_dispatches_through_registry(self, tmp_path):
        """Agent must call tools via its registry (so disabling a skill really
        removes the callable), and a failing tool must not break run()."""
        mem = AgentMemory(tmp_path / "r.sqlite3")
        ctx = ToolContext(memory=mem, models_dir=tmp_path, app_root=tmp_path)

        class _TwoStepRouter:
            def __init__(self):
                self.n = 0

            def is_ready(self):
                return True, "mock"

            def chat(self, prompt, system="", max_new_tokens=2048):
                self.n += 1
                if self.n == 1:
                    return {"ok": True, "text": 'Action: boom_tool|{"x": 1}', "model_name": "mock"}
                return {"ok": True, "text": "最终答案：after boom", "model_name": "mock"}

        agent = Agent(memory=mem, router=_TwoStepRouter(),
                      registry=build_default_registry(), ctx=ctx)

        def _boom(params, ctx):
            raise RuntimeError("x")

        agent.registry.register(
            Tool(name="boom_tool", description="x", parameters={}, handler=_boom)
        )
        result = asyncio.run(agent.run("go boom", session_id="s-boom"))
        # The tool failed but the loop continued to a final answer.
        assert "boom_tool" in result.tools_used
        assert result.answer == "after boom"

    def test_imported_skills_do_not_mutate_builtin_set(self, tmp_path):
        """extra_skills are layered on top of BUILTIN_SKILLS; the frozen built-in
        catalogue must be untouched, and a name collision is skipped."""
        from app.agent import skill_hub
        from app.agent.tools import BUILTIN_SKILLS

        before_ids = {s.id for s in BUILTIN_SKILLS}

        # an imported skill that shadows a builtin id must be skipped on load
        shadow = tmp_path / "core"  # 'core' is a builtin id
        shadow.mkdir()
        (shadow / "SKILL.md").write_text(
            "---\nname: Shadow Core\ndescription: tries to hijack core\n---\nbody\n",
            encoding="utf-8",
        )
        imported = skill_hub.load_imported(tmp_path)
        ids = {s.id for s in imported}
        assert "core" not in ids  # collision skipped
        # builtins unchanged
        assert {s.id for s in BUILTIN_SKILLS} == before_ids

    def test_load_imported_skips_malformed(self, tmp_path):
        from app.agent import skill_hub

        good = tmp_path / "good_imp"
        good.mkdir()
        (good / "SKILL.md").write_text(
            "---\nname: Good\ndescription: d\n---\nguidance body\n", encoding="utf-8"
        )
        bad = tmp_path / "bad_imp"
        bad.mkdir()
        (bad / "SKILL.md").write_text("not frontmatter at all\n", encoding="utf-8")

        skills = skill_hub.load_imported(tmp_path)
        ids = {s.id for s in skills}
        assert "good_imp" in ids
        assert "bad_imp" not in ids

    def test_skill_manager_persists_disable(self, tmp_path):
        """Toggle state survives a reload (atomic tmp+replace)."""
        state = tmp_path / "skills.json"
        mgr = build_skill_manager(state_path=state)
        mgr.disable("writing_studio")
        assert state.exists()
        mgr2 = build_skill_manager(state_path=state)
        assert mgr2.is_enabled("writing_studio") is False
        # required skill still enabled after reload
        assert mgr2.is_enabled("core") is True

    def test_corrupt_state_file_degrades_gracefully(self, tmp_path):
        state = tmp_path / "skills.json"
        state.write_text("{ broken json", encoding="utf-8")
        mgr = build_skill_manager(state_path=state)  # must not raise
        # falls back to defaults (writing_studio default_enabled=False)
        assert mgr.is_enabled("core") is True


# ===========================================================================
# Path 2 — context window / history truncation
# ===========================================================================
class TestHistoryContext:
    @pytest.mark.asyncio
    async def test_current_message_not_duplicated_in_history(self, tmp_path):
        """REGRESSION: the current user turn must appear exactly once in the
        assembled prompt (it is emitted under '## 当前用户请求'). Previously it
        was also pulled into '## 对话历史' because it was persisted before the
        history block was built."""
        router = _CaptureRouter()
        _, _, agent = _make_agent(tmp_path, router=router)
        await agent.run("唯一的当前问题XYZ", session_id="dupe")
        prompt = router.prompts[-1]
        assert prompt.count("唯一的当前问题XYZ") == 1

    @pytest.mark.asyncio
    async def test_first_turn_has_empty_history_block(self, tmp_path):
        router = _CaptureRouter()
        _, _, agent = _make_agent(tmp_path, router=router)
        await agent.run("hi", session_id="fresh")
        prompt = router.prompts[-1]
        assert "对话历史" not in prompt

    @pytest.mark.asyncio
    async def test_history_contains_prior_turns_but_not_current(self, tmp_path):
        router = _CaptureRouter()
        mem, _, agent = _make_agent(tmp_path, router=router)
        await agent.run("前轮用户AAA", session_id="h1")
        await agent.run("当前问题BBB", session_id="h1")
        prompt = router.prompts[-1]
        # prior turn visible in history
        assert "前轮用户AAA" in prompt
        # current turn exactly once
        assert prompt.count("当前问题BBB") == 1
        # history section present and ends before current request
        assert "## 对话历史" in prompt
        assert "## 当前用户请求" in prompt

    @pytest.mark.asyncio
    async def test_history_capped_to_n_messages(self, tmp_path):
        """With >n prior messages, only the last n (chronological) enter the
        history block; the oldest must be dropped."""
        router = _CaptureRouter()
        mem, _, agent = _make_agent(tmp_path, router=router)
        # Pre-seed 12 prior messages (6 user / 6 assistant).
        for i in range(6):
            mem.add_message("cap", "user", f"早期消息{i}")
            mem.add_message("cap", "assistant", f"回复{i}")
        await agent.run("触发截断", session_id="cap")
        prompt = router.prompts[-1]
        # n=10 -> last 10 of the 12 prior messages (i.e. replies 1..5, msgs 1..5)
        assert "早期消息0" not in prompt  # dropped (oldest)
        assert "早期消息5" in prompt     # kept (recent)
        # current request present once
        assert prompt.count("触发截断") == 1

    @pytest.mark.asyncio
    async def test_history_content_truncated_to_500(self, tmp_path):
        router = _CaptureRouter()
        mem, _, agent = _make_agent(tmp_path, router=router)
        long_text = "字" * 900
        mem.add_message("trunc", "user", long_text)
        mem.add_message("trunc", "assistant", "短回复")
        await agent.run("新问题", session_id="trunc")
        prompt = router.prompts[-1]
        # the 900-char prior message must be cut to 500 in the history block
        # (500 '字' chars), so the history line holds at most 500 of them.
        assert ("字" * 501) not in prompt
        assert ("字" * 500) in prompt

    @pytest.mark.asyncio
    async def test_system_prompt_always_present_and_separate(self, tmp_path):
        router = _CaptureRouter()
        _, _, agent = _make_agent(tmp_path, router=router)
        await agent.run("任意", session_id="sys")
        prompt = router.prompts[-1]
        # system prompt header must lead the prompt and never be truncated away
        assert prompt.startswith("你是 **Kevrai Agent")
        assert "可用工具" in prompt

    @pytest.mark.asyncio
    async def test_session_isolation(self, tmp_path):
        router = _CaptureRouter()
        mem, _, agent = _make_agent(tmp_path, router=router)
        await agent.run("A会话秘密词ALPHA", session_id="sessA")
        await agent.run("B会话提问", session_id="sessB")
        prompt_b = router.prompts[-1]
        assert "ALPHA" not in prompt_b

    @pytest.mark.asyncio
    async def test_input_message_capped_to_5000(self, tmp_path):
        router = _CaptureRouter()
        mem, _, agent = _make_agent(tmp_path, router=router)
        huge = "长" * 6000
        result = await agent.run(huge, session_id="cap-in")
        # The persisted user message must be the truncated 5000-char version.
        msgs = mem.get_recent_messages("cap-in", n=5)
        user_msgs = [m for m in msgs if m["role"] == "user"]
        assert user_msgs, "user message should be persisted"
        assert len(user_msgs[-1]["content"]) == 5000

    @pytest.mark.asyncio
    async def test_record_task_truncates_summary(self, tmp_path):
        """agent.run passes message[:200] to record_task (line 182); the
        task_history row must hold at most 200 chars even for a long input."""
        router = _CaptureRouter()
        mem, _, agent = _make_agent(tmp_path, router=router)
        long_msg = "任务" * 300  # 600 chars
        await agent.run(long_msg, session_id="rt")
        rows = mem.get_task_history(limit=5)
        assert rows, "a task should be recorded"
        assert len(rows[0]["task_summary"]) == 200


# ===========================================================================
# Path 3 — /ws/agent/{session_id} lifecycle
# ===========================================================================
def test_ws_agent_rejects_bearerless_upgrade():
    from fastapi.testclient import TestClient
    from app import main as app_main

    with TestClient(app_main.app, headers={"authorization": "Bearer wrong-secret"}) as c:
        with pytest.raises(WebSocketDisconnect) as exc, c.websocket_connect("/ws/agent/x"):
            pass
        assert exc.value.code == 1008


def test_ws_agent_invalid_session_id(hub_client):
    """An invalid session_id must get an error event then a clean close."""
    with hub_client.websocket_connect("/ws/agent/bad id spaces") as ws:
        msg = ws.receive_json()
        assert msg["event"] == "error"
        assert "session_id" in msg["message"]
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_ws_agent_empty_and_too_long_message(hub_client):
    with hub_client.websocket_connect("/ws/agent/ok-sess") as ws:
        ws.send_json({"message": ""})
        m1 = ws.receive_json()
        assert m1["event"] == "error"
        assert "empty" in m1["message"]

        ws.send_json({"message": "字" * 5001})
        m2 = ws.receive_json()
        assert m2["event"] == "error"
        assert "long" in m2["message"]


def test_ws_agent_happy_path_final_event(hub_client):
    """Rule-based mode (no LLM) streams a final event for a simple query."""
    with hub_client.websocket_connect("/ws/agent/happy") as ws:
        ws.send_json({"message": "你好"})
        events = []
        while True:
            m = ws.receive_json()
            events.append(m)
            if m.get("event") == "final":
                break
        finals = [e for e in events if e["event"] == "final"]
        assert len(finals) == 1
        assert "answer" in finals[0]


def test_ws_agent_disconnect_mid_run_no_unhandled_exception(hub_client):
    """Disconnecting the client while agent.run() is still working must not
    surface an unhandled task exception. agent.run has no cooperative
    cancellation (see parliament note) and will finish its current bounded run,
    but the handler must swallow send failures on the dead socket."""
    import threading
    import time

    from app import main as app_main

    hub_client.get("/api/agent/tools")  # build singleton
    agent = app_main._AGENT_SINGLETON["agent"]

    class _SlowRouter:
        def is_ready(self):
            return True, "slow"

        def chat(self, prompt, system="", max_new_tokens=2048):
            time.sleep(0.5)  # a bounded local LLM inference
            return {"ok": True, "text": "最终答案：slow", "model_name": "slow"}

    orig = agent.router
    agent.router = _SlowRouter()
    try:
        with hub_client.websocket_connect("/ws/agent/disc-mid") as ws:
            ws.send_json({"message": "请慢慢想"})
            time.sleep(0.1)  # let the server enter agent.run
            ws.close()       # drop the client mid-inference
        # If the handler raised an unhandled exception, the TestClient portal
        # would surface it here. Bounded wait: the slow router finishes ~0.5s.
    finally:
        agent.router = orig


def test_ws_agent_step_callback_reset_after_run(hub_client):
    """After a run, the step callback must be cleared so a later run on the
    shared singleton does not leak a stale callback."""
    hub_client.get("/api/agent/tools")
    from app import main as app_main
    agent = app_main._AGENT_SINGLETON["agent"]
    with hub_client.websocket_connect("/ws/agent/cb-reset") as ws:
        ws.send_json({"message": "你好"})
        while True:
            m = ws.receive_json()
            if m.get("event") == "final":
                break
    assert agent._step_callback is None
