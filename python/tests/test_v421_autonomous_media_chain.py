"""End-to-end: a scripted autonomous brain drives on-device media tools.

These tests verify the headline autonomous-agent loop can select a media tool,
have the tool persist a file-based result, see that path fed back in the next
turn's observation, and continue (possibly to a second tool) before giving a
final answer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.agent import Agent
from app.agent.memory import AgentMemory
from app.agent.tool_registry import ToolContext
from app.agent.tools import build_skill_manager
from app.agent.tools import media_engine_tools as met
from app.catalog import load_catalog


# ---------------------------------------------------------------------------
# Scripted brain
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Fakes for the real engine managers (no heavy engines in the test env)
# ---------------------------------------------------------------------------
@pytest.fixture
def agent(tmp_path, monkeypatch):
    catalog_dir = Path(__file__).resolve().parent.parent.parent / "catalog"
    catalog, engines = load_catalog(catalog_dir)
    mem = AgentMemory(tmp_path / "agent.sqlite3")

    # Media tools persist artifacts under the data root.
    monkeypatch.setattr(met, "default_data_root", lambda: tmp_path)

    audio_path = tmp_path / "fake_audio.wav"

    class FakePiper:
        def synthesize(self, voice, text, **kw):
            audio_path.write_bytes(b"wav")
            return {"path": str(audio_path), "duration_s": 1.0}

    class FakeEmbed:
        def embed(self, repo, texts, **kw):
            return {"dimensions": 4,
                    "embeddings": [[0.1, 0.2, 0.3, 0.4] for _ in texts]}

    import app.embedding_runtime as embed_mod
    import app.piper_runtime as piper_mod
    monkeypatch.setattr(piper_mod, "PiperManager", FakePiper)
    monkeypatch.setattr(embed_mod, "EmbeddingManager", FakeEmbed)

    skills = build_skill_manager(None)
    skills.enable("media_engines")
    registry = skills.build_registry()

    ctx = ToolContext(catalog=catalog, engines_catalog=engines, memory=mem,
                      models_dir=tmp_path, app_root=tmp_path)
    router = ScriptedRouter([])
    return Agent(memory=mem, router=router, registry=registry, ctx=ctx), router


# ---------------------------------------------------------------------------
# 1. One media tool; the file path must be fed back before the final answer.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_brain_runs_tts_and_path_is_fed_back(agent):
    ag, router = agent
    router._responses = [
        "Thought: I should speak the text.\n"
        'Action: tts_speak|{"text": "你好世界"}',
        "Final Answer: 已完成朗读。",
    ]
    res = await ag.run("帮我朗读：你好世界", session_id="s1")
    assert res.success is True
    assert "tts_speak" in res.tools_used
    assert res.llm_used is True
    # Turn 2 was called only after the tool's observation (with the audio path)
    # was appended to the scratchpad.
    assert len(router.prompts) == 2
    assert "fake_audio.wav" in router.prompts[1]


# ---------------------------------------------------------------------------
# 2. Two media tools chained; the first tool's artifact path reaches turn 2.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_brain_chains_embed_then_speak(agent):
    ag, router = agent
    router._responses = [
        "Thought: embed the text first.\n"
        'Action: embed_text|{"texts": "hello"}',
        "Thought: now speak.\n"
        'Action: tts_speak|{"text": "done"}',
        "Final Answer: 已嵌入并朗读。",
    ]
    res = await ag.run("先嵌入再朗读 hello", session_id="s2")
    assert res.success is True
    assert "embed_text" in res.tools_used
    assert "tts_speak" in res.tools_used
    # The embedding tool's persisted vectors path appears in the observation
    # that the brain receives before choosing the second tool.
    assert "embeddings_" in router.prompts[1]
    assert "fake_audio.wav" in router.prompts[2]


# ---------------------------------------------------------------------------
# 3. A tool that reports an engine-missing error still degrades gracefully.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_brain_handles_tool_error(agent, monkeypatch):
    ag, router = agent

    class BrokenPiper:
        def synthesize(self, voice, text, **kw):
            raise RuntimeError("EngineMissing: piper not installed")

    import app.piper_runtime as piper_mod
    monkeypatch.setattr(piper_mod, "PiperManager", BrokenPiper)

    router._responses = [
        "Thought: try speaking.\n"
        'Action: tts_speak|{"text": "hi"}',
        "Final Answer: 引擎暂不可用，请先安装。",
    ]
    res = await ag.run("朗读 hi", session_id="s3")
    # The loop survives the tool error and reaches a final answer.
    assert res.success is True
    assert "tts_speak" in res.tools_used
    assert "安装" in res.answer
