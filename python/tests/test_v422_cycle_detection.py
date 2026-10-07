"""Tests for no-progress cycle detection in the agent ReAct loop."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.agent import Agent
from app.agent.memory import AgentMemory
from app.agent.tool_registry import Tool, ToolContext, ToolRegistry
from app.catalog import load_catalog


class ScriptedRouter:
    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0
        self.prompts: list[str] = []

    def is_ready(self):
        return True, "scripted-brain"

    def chat(self, prompt, system="", max_new_tokens=2048, should_stop=None):
        self.prompts.append(prompt)
        text = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return {"ok": True, "text": text, "model_name": "scripted-brain"}


def _action(text: str) -> str:
    import json as _json
    payload = _json.dumps({"text": text}, ensure_ascii=False)
    return f"Thought: repeat\nAction: echo|{payload}"


@pytest.fixture
def make(tmp_path):
    catalog_dir = Path(__file__).resolve().parent.parent.parent / "catalog"
    catalog, engines = load_catalog(catalog_dir)
    mem = AgentMemory(tmp_path / "agent.sqlite3")

    calls: list[int] = []

    def handler(params, ctx):
        calls.append(1)
        return {"ok": True, "echo": params.get("text", "")}

    reg = ToolRegistry()
    reg.register(Tool(
        name="echo", description="echoes text",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=handler,
    ))
    ctx = ToolContext(catalog=catalog, engines_catalog=engines, memory=mem,
                      models_dir=tmp_path, app_root=tmp_path)

    def build(responses):
        router = ScriptedRouter(responses)
        agent = Agent(memory=mem, router=router, registry=reg, ctx=ctx)
        return agent, router

    return build, calls


@pytest.mark.asyncio
async def test_repeated_identical_action_is_stopped(make):
    build, calls = make
    agent, _ = build([_action("hi")] * 8)
    res = await agent.run("重复测试", session_id="s1")
    assert res.success is False
    assert "重复" in res.error or "进展" in res.error
    # initial call + two nudged repeats, then stop on the third repeat
    assert sum(calls) == 4
    assert res.tools_used.count("echo") == 4


@pytest.mark.asyncio
async def test_nudge_then_final_succeeds(make):
    build, calls = make
    agent, router = build([
        _action("hi"),
        _action("hi"),
        "Final Answer: 好了。",
    ])
    res = await agent.run("先重复再总结", session_id="s2")
    assert res.success is True
    assert sum(calls) == 2
    # The cycle correction was injected before the brain produced the final.
    assert "没有任何新进展" in router.prompts[2]


@pytest.mark.asyncio
async def test_different_params_do_not_count_as_cycle(make):
    build, calls = make
    agent, router = build([
        _action("one"),
        _action("two"),
        "Final Answer: 完成。",
    ])
    res = await agent.run("不同参数", session_id="s3")
    assert res.success is True
    assert sum(calls) == 2
    joined = "\n".join(router.prompts)
    assert "没有任何新进展" not in joined
