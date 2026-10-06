"""Tests for the ReAct premature-conclusion guard (forces tool use)."""
from __future__ import annotations

from app.agent.agent import (
    MAX_PREMATURE_NUDGES,
    Agent,
    _needs_tool,
)
from app.agent.memory import AgentMemory
from app.agent.tool_registry import ToolContext


class FakeRegistry:
    def __init__(self):
        self.calls: list[str] = []

    def execute(self, name, params, ctx):
        self.calls.append(name)
        if name == "search_models":
            return {"ok": True, "results": [{"id": "bge-m3"}]}
        return {"ok": True, "id": "bge-m3", "license": "MIT",
                "size_gb": 2.27, "name": "BGE-M3"}

    def build_tool_prompt_block(self):
        return ""


class FakeRouter:
    def __init__(self, scripts):
        self._scripts = list(scripts)
        self._i = 0

    def is_ready(self):
        return True, "fake-brain"

    def chat(self, prompt, system="", max_new_tokens=1024):
        text = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return {"ok": True, "text": text}


def _agent(tmp_path, scripts):
    memory = AgentMemory(tmp_path / "m.sqlite3")
    reg = FakeRegistry()
    router = FakeRouter(scripts)
    ctx = ToolContext(catalog=None, engines_catalog=None, hardware_info={},
                      memory=memory, settings=None,
                      models_dir=tmp_path, app_root=tmp_path)
    agent = Agent(memory=memory, router=router, registry=reg, ctx=ctx)
    return agent, reg


async def test_premature_final_answer_rejected_then_tools_run(tmp_path):
    scripts = [
        # 1) fabricated, tool-free conclusion — must be rejected.
        'Final Answer: bge-m3 存储空间为 10485198.0GB（编造）',
        # 2) proper first action
        '我先搜索。\nAction: search_models|{"query": "bge"}',
        # 3) proper second action
        '查看详情。\nAction: model_info|{"model_id": "bge-m3"}',
        # 4) grounded final answer
        'Final Answer: bge-m3 许可 MIT，约 2.27GB。',
    ]
    agent, reg = _agent(tmp_path, scripts)
    res = await agent.run("先搜索 embedding 模型，再告诉我 bge-m3 详情",
                          session_id="s1")
    assert reg.calls == ["search_models", "model_info"]
    assert "MIT" in res.answer and "2.27" in res.answer
    assert "10485198" not in res.answer
    assert res.tools_used == ["search_models", "model_info"]


async def test_greeting_needs_no_tool(tmp_path):
    scripts = ['Final Answer: 你好，我是 Kevrai Agent。']
    agent, reg = _agent(tmp_path, scripts)
    res = await agent.run("你好", session_id="s2")
    assert reg.calls == []
    assert "你好" in res.answer


async def test_conclusion_accepted_after_nudge_budget(tmp_path):
    # The model keeps refusing to call tools; after the nudge cap its answer is
    # accepted so the loop cannot spin forever.
    scripts = [f"Final Answer: 我无法获取数据（第{i}次）"
               for i in range(MAX_PREMATURE_NUDGES + 2)]
    agent, reg = _agent(tmp_path, scripts)
    res = await agent.run("推荐一些模型", session_id="s3")
    assert reg.calls == []
    assert res.success is True
    assert "无法获取数据" in res.answer


def test_needs_tool_intent():
    assert _needs_tool("帮我搜索嵌入模型")
    assert _needs_tool("我的硬件能跑什么")
    assert not _needs_tool("你好")
    assert not _needs_tool("介绍一下你自己")
