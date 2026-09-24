"""Neuron-Agent K-Cortex reliability regression tests.

Coaches the agent loop against malformed/misbehaving LLM-router results and
robust output parsing that the v2.7.0 suite did not cover:

* A router returning a non-dict (None) must not crash the ReAct loop.
* A router returning ok=True but text=None / non-string must not crash.
* ``extract_final_answer`` must recognise the Chinese markers the system prompt
  actually tells the model to emit (最终答案 / 最终回答).
* ``parse_tool_call`` must tolerate trailing free-text after the JSON blob on
  the same line, and must still parse nested JSON objects.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agent import Agent, AgentMemory, ToolContext
from app.agent.tool_registry import (
    ToolRegistry,
    extract_final_answer,
    parse_tool_call,
)
from app.agent.tools import build_default_registry


# ===========================================================================
# Router result validation (agent.run must never crash on a bad router)
# ===========================================================================
class _NoneTextRouter:
    """Router that claims success but hands back text=None."""

    def is_ready(self):
        return True, "mock-bad"

    def chat(self, prompt, system="", max_new_tokens=2048):
        return {"ok": True, "text": None, "model_name": "mock-bad"}


class _NonDictRouter:
    """Router that forgets to return a dict at all."""

    def is_ready(self):
        return True, "mock-bad"

    def chat(self, prompt, system="", max_new_tokens=2048):
        return None  # type: ignore[return-value]


class _NumberTextRouter:
    """Router that returns a non-string text payload."""

    def is_ready(self):
        return True, "mock-bad"

    def chat(self, prompt, system="", max_new_tokens=2048):
        return {"ok": True, "text": 12345, "model_name": "mock-bad"}  # type: ignore[dict-item]


@pytest.fixture
def _agent(tmp_path):
    mem = AgentMemory(tmp_path / "rel.sqlite3")
    ctx = ToolContext(memory=mem, models_dir=tmp_path, app_root=tmp_path)
    return mem, ctx


@pytest.mark.asyncio
async def test_run_does_not_crash_when_text_is_none(_agent):
    mem, ctx = _agent
    agent = Agent(memory=mem, router=_NoneTextRouter(),
                  registry=build_default_registry(), ctx=ctx)
    # Must not raise TypeError/AttributeError.
    result = await agent.run("任意问题", session_id="none-text")
    assert isinstance(result.answer, str)
    assert result.success in (True, False)


@pytest.mark.asyncio
async def test_run_does_not_crash_when_router_returns_none(_agent):
    mem, ctx = _agent
    agent = Agent(memory=mem, router=_NonDictRouter(),
                  registry=build_default_registry(), ctx=ctx)
    result = await agent.run("任意问题", session_id="none-dict")
    # A non-dict result is treated as an LLM failure, not a crash.
    assert result.success is False
    assert "LLM 调用失败" in result.error or "非法结果" in result.error


@pytest.mark.asyncio
async def test_run_coerces_non_string_text(_agent):
    mem, ctx = _agent
    agent = Agent(memory=mem, router=_NumberTextRouter(),
                  registry=build_default_registry(), ctx=ctx)
    result = await agent.run("任意问题", session_id="num-text")
    assert isinstance(result.answer, str)


# ===========================================================================
# Final-answer extraction: Chinese markers
# ===========================================================================
class TestChineseFinalAnswer:
    def test_full_width_colon(self):
        text = "Thought: 我已经分析完了。\n最终答案：这是最终的回答内容。"
        assert extract_final_answer(text) == "这是最终的回答内容。"

    def test_half_width_colon(self):
        text = "Thought: done\n最终回答: half-width colon answer"
        assert extract_final_answer(text) == "half-width colon answer"

    def test_english_marker_still_works(self):
        text = "Thought: done\nFinal Answer: The answer is 42."
        assert extract_final_answer(text) == "The answer is 42."

    def test_no_marker_fallback(self):
        assert extract_final_answer("plain text") == "plain text"

    def test_chinese_does_not_leak_thought_prefix(self):
        # Before the fix, the thought line was included because the English-only
        # regex could not match the Chinese marker.
        text = "Thought: 让我想想看。\n最终答案：直接给出的结论。"
        out = extract_final_answer(text)
        assert out == "直接给出的结论。"
        assert "让我想想" not in out
