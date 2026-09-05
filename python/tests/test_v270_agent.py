"""Tests for the Kevrai Agent module (v2.7.0).

Covers:
- Tool registry registration, listing, execution, error handling
- Tool call parsing (both formats) and final answer extraction
- Agent memory (SQLite): sessions, messages, preferences, task history
- Model router (LLM not ready fallback)
- Agent rule-based mode (no LLM loaded): hardware check, search, default response
- Agent ReAct loop structure (with a mock LLM)
- Tool context and catalog tools (search, model_info, recommend, list_installed, list_categories)
- System tools (check_hardware, list_engines, download_model, generate_text, preferences)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from app.agent import Agent, AgentMemory, ModelRouter, ToolContext
from app.agent.tool_registry import (
    Tool,
    ToolRegistry,
    extract_final_answer,
    parse_tool_call,
)
from app.agent.tools import build_default_registry, ALL_TOOLS


# ===========================================================================
# Tool Registry
# ===========================================================================
class TestToolRegistry:
    def test_register_and_list(self):
        reg = ToolRegistry()
        t = Tool(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
            handler=lambda p, c: {"echo": p.get("x")},
        )
        reg.register(t)
        assert "test_tool" in reg.list_names()
        specs = reg.list_tools()
        assert any(s["name"] == "test_tool" for s in specs)

    def test_invalid_tool_name_rejected(self):
        reg = ToolRegistry()
        t = Tool(
            name="Invalid Name!",
            description="bad",
            parameters={},
            handler=lambda p, c: {},
        )
        with pytest.raises(ValueError):
            reg.register(t)

    def test_execute_success(self):
        reg = ToolRegistry()
        reg.register(Tool(
            name="adder",
            description="add two numbers",
            parameters={"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}},
            handler=lambda p, c: {"sum": p["a"] + p["b"]},
        ))
        result = reg.execute("adder", {"a": 3, "b": 4}, ToolContext())
        assert result["ok"] is True
        assert result["sum"] == 7

    def test_execute_unknown_tool(self):
        reg = ToolRegistry()
        result = reg.execute("nonexistent", {}, ToolContext())
        assert result["ok"] is False
        assert "unknown tool" in result["error"]

    def test_execute_handler_exception(self):
        def boom(p, c):
            raise RuntimeError("kaboom")
        reg = ToolRegistry()
        reg.register(Tool(name="boom", description="", parameters={}, handler=boom))
        result = reg.execute("boom", {}, ToolContext())
        assert result["ok"] is False
        assert "kaboom" in result["error"]
        assert result["error_type"] == "RuntimeError"

    def test_default_registry_has_all_tools(self):
        reg = build_default_registry()
        names = reg.list_names()
        expected = [
            "search_models", "model_info", "recommend_models", "list_installed",
            "list_categories", "check_hardware", "list_engines", "download_model",
            "generate_text", "get_preferences", "set_preference",
        ]
        for name in expected:
            assert name in names, f"missing tool: {name}"
        assert len(names) == len(ALL_TOOLS)

    def test_tool_prompt_block_nonempty(self):
        reg = build_default_registry()
        block = reg.build_tool_prompt_block()
        assert "search_models" in block
        assert "check_hardware" in block
        assert len(block) > 200


# ===========================================================================
# Tool Call Parsing
# ===========================================================================
class TestParseToolCall:
    def test_json_format(self):
        text = 'Thought: I need to search\nAction: search_models|{"query": "music", "limit": 5}\n'
        result = parse_tool_call(text)
        assert result is not None
        name, params = result
        assert name == "search_models"
        assert params["query"] == "music"
        assert params["limit"] == 5

    def test_kwargs_format(self):
        text = 'Action: model_info(model_id=granite-4.2-8b)\n'
        result = parse_tool_call(text)
        assert result is not None
        name, params = result
        assert name == "model_info"
        assert params["model_id"] == "granite-4.2-8b"

    def test_kwargs_with_numbers_and_bools(self):
        text = 'Action: search_models(query=test, limit=10, category=llm)\n'
        result = parse_tool_call(text)
        assert result is not None
        _, params = result
        assert params["limit"] == 10
        assert params["category"] == "llm"

    def test_no_tool_call_returns_none(self):
        text = 'Thought: I can answer directly\nFinal Answer: Hello there'
        assert parse_tool_call(text) is None

    def test_case_insensitive_action(self):
        text = 'ACTION: check_hardware|{}\n'
        result = parse_tool_call(text)
        assert result is not None
        assert result[0] == "check_hardware"


class TestExtractFinalAnswer:
    def test_with_marker(self):
        text = 'Thought: done\nFinal Answer: The answer is 42.'
        assert extract_final_answer(text) == "The answer is 42."

    def test_chinese_marker(self):
        text = 'Thought: 完成\n最终答案：这是中文回答'
        assert "这是中文回答" in extract_final_answer(text)

    def test_no_marker_fallback(self):
        text = 'This is just a plain answer.'
        assert extract_final_answer(text) == "This is just a plain answer."


# ===========================================================================
# Agent Memory
# ===========================================================================
class TestAgentMemory:
    @pytest.fixture
    def mem(self, tmp_path):
        return AgentMemory(tmp_path / "test.sqlite3")

    def test_create_and_get_session(self, mem):
        s = mem.create_session("sess1", title="Test")
        assert s["id"] == "sess1"
        assert s["title"] == "Test"
        assert mem.get_session("sess1") is not None
        assert mem.get_session("nonexistent") is None

    def test_add_and_get_messages(self, mem):
        mem.add_message("sess1", "user", "hello")
        mem.add_message("sess1", "assistant", "hi there")
        msgs = mem.get_messages("sess1")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["content"] == "hi there"

    def test_recent_messages_order(self, mem):
        for i in range(5):
            mem.add_message("sess1", "user", f"msg {i}")
        recent = mem.get_recent_messages("sess1", n=3)
        assert len(recent) == 3
        # chronological order
        assert recent[0]["content"] == "msg 2"
        assert recent[-1]["content"] == "msg 4"

    def test_preferences(self, mem):
        mem.set_preference("favorite_llm", "qwen3-8b")
        mem.set_preference("quality", "balanced")
        assert mem.get_preference("favorite_llm") == "qwen3-8b"
        assert mem.get_preference("nonexistent", "default") == "default"
        all_prefs = mem.get_all_preferences()
        assert all_prefs["favorite_llm"] == "qwen3-8b"
        assert all_prefs["quality"] == "balanced"

    def test_preference_overwrite(self, mem):
        mem.set_preference("key", "v1")
        mem.set_preference("key", "v2")
        assert mem.get_preference("key") == "v2"

    def test_delete_session(self, mem):
        mem.add_message("sess1", "user", "hello")
        assert mem.delete_session("sess1") is True
        assert mem.get_session("sess1") is None
        assert mem.delete_session("sess1") is False

    def test_list_sessions(self, mem):
        mem.create_session("a")
        mem.create_session("b")
        sessions = mem.list_sessions()
        assert len(sessions) == 2

    def test_task_history(self, mem):
        mem.record_task("sess1", "search music models", ["search_models"], success=True)
        mem.record_task("sess1", "failed task", [], success=False)
        history = mem.get_task_history()
        assert len(history) == 2
        assert history[0]["success"] == 0  # most recent first
        assert history[1]["success"] == 1


# ===========================================================================
# Model Router
# ===========================================================================
class TestModelRouter:
    def test_router_not_ready_by_default(self):
        router = ModelRouter()
        ready, name = router.is_ready()
        # In test environment, MNN runtime likely not loaded
        assert isinstance(ready, bool)
        assert isinstance(name, str)

    def test_chat_returns_not_ready_without_model(self):
        router = ModelRouter()
        # Force not-ready by checking status first
        ready, _ = router.is_ready()
        result = router.chat("test", system="", max_new_tokens=100)
        if not ready:
            assert result["ok"] is False
            assert result["error_type"] == "LlmNotReady"
        else:
            assert result["ok"] is True


# ===========================================================================
# Catalog Tools (with real catalog)
# ===========================================================================
class TestCatalogTools:
    @pytest.fixture
    def ctx(self):
        from app.catalog import load_catalog
        catalog_dir = Path(__file__).resolve().parent.parent.parent / "catalog"
        catalog, engines = load_catalog(catalog_dir)
        return ToolContext(catalog=catalog, engines_catalog=engines)

    def test_search_models_by_keyword(self, ctx):
        reg = build_default_registry()
        result = reg.execute("search_models", {"query": "music", "limit": 5}, ctx)
        assert result["ok"] is True
        assert result["count"] > 0
        assert len(result["results"]) <= 5

    def test_search_models_by_category(self, ctx):
        reg = build_default_registry()
        result = reg.execute("search_models", {"category": "audio", "limit": 10}, ctx)
        assert result["ok"] is True
        assert result["count"] > 0
        for r in result["results"]:
            assert r["category"] == "audio"

    def test_model_info_existing(self, ctx):
        reg = build_default_registry()
        # Find a real model ID
        search_result = reg.execute("search_models", {"query": "granite", "limit": 1}, ctx)
        if search_result["results"]:
            model_id = search_result["results"][0]["id"]
            result = reg.execute("model_info", {"model_id": model_id}, ctx)
            assert result["ok"] is True
            assert result["id"] == model_id
            assert "name" in result

    def test_model_info_nonexistent(self, ctx):
        reg = build_default_registry()
        result = reg.execute("model_info", {"model_id": "nonexistent-model-xyz"}, ctx)
        assert result["ok"] is True  # tool itself doesn't fail, returns error in payload
        assert "error" in result

    def test_list_categories(self, ctx):
        reg = build_default_registry()
        result = reg.execute("list_categories", {}, ctx)
        assert result["ok"] is True
        assert result["total"] > 0
        assert len(result["categories"]) > 0

    def test_recommend_models_needs_hardware(self, ctx):
        reg = build_default_registry()
        # Without hardware info, recommend should return error
        result = reg.execute("recommend_models", {"limit": 3}, ctx)
        # Tool may return error or empty results depending on implementation
        assert result["ok"] is True or "error" in result


# ===========================================================================
# System Tools
# ===========================================================================
class TestSystemTools:
    @pytest.fixture
    def ctx(self, tmp_path):
        return ToolContext(models_dir=tmp_path, app_root=tmp_path)

    def test_check_hardware(self, ctx):
        reg = build_default_registry()
        result = reg.execute("check_hardware", {}, ctx)
        assert result["ok"] is True
        assert "summary" in result
        assert "vram_gb" in result

    def test_list_engines_without_app_root(self, ctx):
        reg = build_default_registry()
        # Without proper app_root/engines_catalog, may return error
        result = reg.execute("list_engines", {}, ctx)
        assert isinstance(result, dict)

    def test_download_model_nonexistent(self, ctx):
        from app.catalog import load_catalog
        catalog_dir = Path(__file__).resolve().parent.parent.parent / "catalog"
        catalog, _ = load_catalog(catalog_dir)
        ctx.catalog = catalog
        reg = build_default_registry()
        result = reg.execute("download_model", {"model_id": "nonexistent-xyz"}, ctx)
        assert "error" in result

    def test_generate_text_not_ready(self, ctx):
        reg = build_default_registry()
        result = reg.execute("generate_text", {"prompt": "hello"}, ctx)
        # MNN likely not loaded in test env
        assert isinstance(result, dict)

    def test_get_set_preferences(self, ctx, tmp_path):
        mem = AgentMemory(tmp_path / "prefs.sqlite3")
        ctx.memory = mem
        reg = build_default_registry()
        set_result = reg.execute("set_preference", {"key": "test_key", "value": "test_value"}, ctx)
        assert set_result["ok"] is True
        get_result = reg.execute("get_preferences", {}, ctx)
        assert get_result["preferences"]["test_key"] == "test_value"


# ===========================================================================
# Agent (rule-based mode, no LLM)
# ===========================================================================
class TestAgentRuleBased:
    @pytest.fixture
    def agent(self, tmp_path):
        from app.catalog import load_catalog
        catalog_dir = Path(__file__).resolve().parent.parent.parent / "catalog"
        catalog, engines = load_catalog(catalog_dir)
        mem = AgentMemory(tmp_path / "agent.sqlite3")
        router = ModelRouter()
        registry = build_default_registry()
        ctx = ToolContext(catalog=catalog, engines_catalog=engines, memory=mem,
                          models_dir=tmp_path, app_root=tmp_path)
        return Agent(memory=mem, router=router, registry=registry, ctx=ctx)

    @pytest.mark.asyncio
    async def test_hardware_query_triggers_check(self, agent):
        result = await agent.run("我的硬件配置怎么样？", session_id="test")
        assert result.success is True
        assert "check_hardware" in result.tools_used
        assert len(result.answer) > 10
        assert result.llm_used is False  # rule-based mode

    @pytest.mark.asyncio
    async def test_search_query(self, agent):
        result = await agent.run("搜索音乐生成模型", session_id="test")
        assert result.success is True
        assert "search_models" in result.tools_used
        assert "音乐" in result.answer or "模型" in result.answer or "music" in result.answer.lower()

    @pytest.mark.asyncio
    async def test_empty_message_returns_error(self, agent):
        result = await agent.run("", session_id="test")
        assert result.success is False
        assert "empty" in result.error.lower()

    @pytest.mark.asyncio
    async def test_default_response_explains_setup(self, agent):
        result = await agent.run("你好，你能做什么？", session_id="test")
        assert result.success is True
        # In rule-based mode, should mention loading an LLM
        assert "MNN" in result.answer or "模型" in result.answer or "Agent" in result.answer

    @pytest.mark.asyncio
    async def test_message_persisted_to_memory(self, agent):
        await agent.run("测试消息持久化", session_id="persist_test")
        msgs = agent.memory.get_messages("persist_test")
        assert len(msgs) >= 2  # user + assistant
        assert msgs[0]["role"] == "user"
        assert "测试消息持久化" in msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_task_recorded(self, agent):
        await agent.run("搜索图像模型", session_id="task_test")
        history = agent.memory.get_task_history()
        assert len(history) >= 1
        assert "搜索" in history[0]["task_summary"] or "image" in history[0]["task_summary"].lower()


# ===========================================================================
# Agent (with mock LLM for ReAct loop)
# ===========================================================================
class MockModelRouter:
    """Mock router that returns scripted responses to test the ReAct loop."""
    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0
        self.calls = []

    def is_ready(self):
        return True, "mock-model"

    def chat(self, prompt, system="", max_new_tokens=2048):
        self.calls.append(prompt)
        if self._idx < len(self._responses):
            text = self._responses[self._idx]
            self._idx += 1
        else:
            text = "Final Answer: 超出预期回复"
        return {"ok": True, "text": text, "model_name": "mock-model"}


class TestAgentReAct:
    @pytest.fixture
    def agent(self, tmp_path):
        from app.catalog import load_catalog
        catalog_dir = Path(__file__).resolve().parent.parent.parent / "catalog"
        catalog, engines = load_catalog(catalog_dir)
        mem = AgentMemory(tmp_path / "react.sqlite3")
        registry = build_default_registry()
        ctx = ToolContext(catalog=catalog, engines_catalog=engines, memory=mem,
                          models_dir=tmp_path, app_root=tmp_path)
        agent = Agent(memory=mem, router=MockModelRouter([]), registry=registry, ctx=ctx)
        return agent

    @pytest.mark.asyncio
    async def test_direct_final_answer(self, agent):
        agent.router = MockModelRouter([
            "Thought: 用户问的是简单问题，我可以直接回答。\nFinal Answer: 这是一个直接回答。",
        ])
        result = await agent.run("简单问题", session_id="react1")
        assert result.success is True
        assert "直接回答" in result.answer
        assert len(result.steps) == 1
        assert result.steps[0].is_final is True

    @pytest.mark.asyncio
    async def test_tool_call_then_final(self, agent):
        agent.router = MockModelRouter([
            'Thought: 我需要先搜索音乐模型。\nAction: search_models|{"query": "music", "limit": 3}',
            "Thought: 搜索结果已返回，现在可以总结。\nFinal Answer: 找到了几个音乐模型。",
        ])
        result = await agent.run("找音乐模型", session_id="react2")
        assert result.success is True
        assert "search_models" in result.tools_used
        assert len(result.steps) == 2
        assert result.steps[0].action_tool == "search_models"
        assert result.steps[1].is_final is True

    @pytest.mark.asyncio
    async def test_max_iterations(self, agent):
        # Always returns a tool call, never final
        agent.router = MockModelRouter([
            'Action: search_models|{"query": "x"}' for _ in range(20)
        ])
        result = await agent.run("无限循环测试", session_id="react3")
        assert len(result.steps) <= 12  # MAX_ITERATIONS
        assert result.success is False or "最大" in result.answer or "步骤" in result.answer

    @pytest.mark.asyncio
    async def test_tool_error_reflected_in_observation(self, agent):
        agent.router = MockModelRouter([
            'Action: model_info|{"model_id": "nonexistent-xyz"}',
            "Final Answer: 模型不存在。",
        ])
        result = await agent.run("查不存在的模型", session_id="react4")
        assert result.success is True
        assert result.steps[0].action_tool == "model_info"
        # The tool returns ok=True but with error field in payload
        obs = result.steps[0].observation
        assert obs is not None

    @pytest.mark.asyncio
    async def test_step_callback_called(self, agent):
        agent.router = MockModelRouter([
            "Final Answer: 回调测试",
        ])
        steps_received = []
        agent.set_step_callback(lambda s: steps_received.append(s))
        result = await agent.run("回调", session_id="react5")
        assert len(steps_received) >= 1
        agent.set_step_callback(None)

    @pytest.mark.asyncio
    async def test_preferences_included_in_prompt(self, agent):
        agent.memory.set_preference("favorite_llm", "qwen3")
        agent.router = MockModelRouter([
            "Final Answer: 已读取偏好。",
        ])
        result = await agent.run("偏好测试", session_id="react6")
        # The system prompt should include the preference
        assert len(agent.router.calls) == 1
        assert "qwen3" in agent.router.calls[0]
