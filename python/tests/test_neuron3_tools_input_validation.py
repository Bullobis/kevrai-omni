"""Neuron-3 (K-Cortex third slice) regression tests — tools input validation.

Guards the fix where every tool handler previously called ``int(params.get(...))``
directly on LLM-supplied integer parameters. A small local model emitting a stray
string (``limit="abc"``) or a wrong-typed JSON value used to surface a cryptic
internal error (``invalid literal for int() with base 10: 'abc'``, ``ValueError``),
giving the ReAct loop no actionable signal. After the fix each handler coerces via
``_as_int`` and returns a clean ``"<field> must be an integer, got <value>"``.

These tests pin the contract for every numeric parameter across the six tool files:
normal int, numeric string accepted, out-of-range clamped, missing -> default, and
garbage types (str/list/dict/bool) -> ok:False with an actionable message.
"""
from __future__ import annotations

from app.agent.tool_registry import ToolContext, ToolRegistry
from app.agent.tools.catalog_tools import recommend_models, search_models
from app.agent.tools.media_prompt_tools import build_music_prompt, build_video_prompt
from app.agent.tools.system_tools import generate_text
from app.agent.tools.writing_tools import writing_outline, writing_summary


class _FakeModel:
    def __init__(self, **fields):
        self._d = fields

    def model_dump(self):
        return dict(self._d)


class _FakeCatalog:
    def __init__(self, models):
        self.models = models


def _reg(*tools) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _catalog_ctx() -> ToolContext:
    models = [
        _FakeModel(id="m1", name="model one", category="llm",
                   description="d", size_gb=4, engine=["llama.cpp"]),
        _FakeModel(id="m2", name="model two", category="llm",
                   description="d", size_gb=8, engine=["llama.cpp"]),
        _FakeModel(id="a1", name="audio one", category="audio",
                   description="music", size_gb=2, engine=["mnn"]),
    ]
    return ToolContext(catalog=_FakeCatalog(models))


# ===========================================================================
# search_models — limit
# ===========================================================================
class TestSearchModelsLimit:
    def test_default_limit_applied(self):
        r = _reg(search_models).execute("search_models", {"query": "model"}, _catalog_ctx())
        assert r["ok"] is True
        assert len(r["results"]) <= 10

    def test_numeric_string_accepted(self):
        r = _reg(search_models).execute(
            "search_models", {"query": "model", "limit": "3"}, _catalog_ctx())
        assert r["ok"] is True
        assert len(r["results"]) <= 3

    def test_huge_limit_clamped_to_50(self):
        r = _reg(search_models).execute(
            "search_models", {"query": "model", "limit": 100000}, _catalog_ctx())
        assert r["ok"] is True
        assert len(r["results"]) <= 50

    def test_garbage_string_is_clean_error(self):
        r = _reg(search_models).execute(
            "search_models", {"query": "model", "limit": "abc"}, _catalog_ctx())
        assert r["ok"] is False
        assert "limit" in r["error"] and "integer" in r["error"]
        assert "invalid literal" not in r["error"]

    def test_list_type_is_clean_error(self):
        r = _reg(search_models).execute(
            "search_models", {"query": "model", "limit": [5, 10]}, _catalog_ctx())
        assert r["ok"] is False
        assert "limit" in r["error"]

    def test_bool_rejected(self):
        r = _reg(search_models).execute(
            "search_models", {"query": "model", "limit": True}, _catalog_ctx())
        assert r["ok"] is False


# ===========================================================================
# recommend_models — limit
# ===========================================================================
class TestRecommendModelsLimit:
    def _ctx(self):
        ctx = _catalog_ctx()
        ctx.hardware_info = {
            "ram_total_gb": 64, "disk": {"free_gb": 500},
            "bandwidth_mbps": 100, "gpu_best_vram_gb": 16,
        }
        return ctx

    def test_numeric_string_accepted(self):
        r = _reg(recommend_models).execute(
            "recommend_models", {"limit": "4"}, self._ctx())
        assert r["ok"] is True
        assert r["count"] <= 4

    def test_garbage_string_is_clean_error(self):
        r = _reg(recommend_models).execute(
            "recommend_models", {"limit": "lots"}, self._ctx())
        assert r["ok"] is False
        assert "limit" in r["error"] and "integer" in r["error"]


# ===========================================================================
# build_video_prompt — duration_s
# ===========================================================================
class TestBuildVideoPromptDuration:
    def test_default_duration(self):
        r = _reg(build_video_prompt).execute(
            "build_video_prompt", {"subject": "猫", "action": "跑"}, ToolContext())
        assert r["ok"] is True
        assert r["parameters"]["duration_s"] == 5

    def test_numeric_string_accepted(self):
        r = _reg(build_video_prompt).execute(
            "build_video_prompt", {"subject": "猫", "action": "跑", "duration_s": "8"},
            ToolContext())
        assert r["ok"] is True
        assert r["parameters"]["duration_s"] == 8

    def test_out_of_range_clamped(self):
        r = _reg(build_video_prompt).execute(
            "build_video_prompt", {"subject": "猫", "action": "跑", "duration_s": 999},
            ToolContext())
        assert r["ok"] is True
        assert r["parameters"]["duration_s"] == 15

    def test_garbage_is_clean_error(self):
        r = _reg(build_video_prompt).execute(
            "build_video_prompt", {"subject": "猫", "action": "跑", "duration_s": "long"},
            ToolContext())
        assert r["ok"] is False
        assert "duration_s" in r["error"]


# ===========================================================================
# build_music_prompt — tempo_bpm, duration_s
# ===========================================================================
class TestBuildMusicPromptInts:
    def test_defaults(self):
        r = _reg(build_music_prompt).execute("build_music_prompt", {"mood": "激昂"}, ToolContext())
        assert r["ok"] is True
        assert r["parameters"]["tempo_bpm"] == 90
        assert r["parameters"]["duration_s"] == 30

    def test_bpm_clamped(self):
        r = _reg(build_music_prompt).execute(
            "build_music_prompt", {"mood": "激昂", "tempo_bpm": 5}, ToolContext())
        assert r["ok"] is True
        assert r["parameters"]["tempo_bpm"] == 40

    def test_garbage_bpm_clean_error(self):
        r = _reg(build_music_prompt).execute(
            "build_music_prompt", {"mood": "激昂", "tempo_bpm": "fast"}, ToolContext())
        assert r["ok"] is False
        assert "tempo_bpm" in r["error"]

    def test_garbage_duration_clean_error(self):
        r = _reg(build_music_prompt).execute(
            "build_music_prompt", {"mood": "激昂", "duration_s": "very-long"}, ToolContext())
        assert r["ok"] is False
        assert "duration_s" in r["error"]


# ===========================================================================
# generate_text — max_new_tokens
# ===========================================================================
class TestGenerateTextTokens:
    def test_garbage_tokens_clean_error(self):
        r = _reg(generate_text).execute(
            "generate_text", {"prompt": "hi", "max_new_tokens": "many"}, ToolContext())
        assert r["ok"] is False
        assert "max_new_tokens" in r["error"]
        assert "invalid literal" not in r["error"]

    def test_numeric_string_coerced_before_mnn_call(self):
        # Coercion must pass; we then expect the LLM-not-ready path (test env),
        # NOT an integer-type error.
        r = _reg(generate_text).execute(
            "generate_text", {"prompt": "hi", "max_new_tokens": "2048"}, ToolContext())
        assert "must be an integer" not in r.get("error", "")

    def test_missing_prompt_still_reports_prompt(self):
        r = _reg(generate_text).execute(
            "generate_text", {"prompt": "  "}, ToolContext())
        # Empty prompt is a handled error path (registry wraps it ok:True w/ error field)
        assert "prompt" in r.get("error", "") or r["ok"] is False


# ===========================================================================
# writing_outline — sections
# ===========================================================================
class TestWritingOutlineSections:
    def test_default_sections(self):
        r = _reg(writing_outline).execute("writing_outline", {"topic": "AI"}, ToolContext())
        assert r["ok"] is True
        assert 2 <= len(r["outline"]) <= 12

    def test_garbage_sections_clean_error(self):
        r = _reg(writing_outline).execute(
            "writing_outline", {"topic": "AI", "sections": "many"}, ToolContext())
        assert r["ok"] is False
        assert "sections" in r["error"]

    def test_sections_clamped(self):
        r = _reg(writing_outline).execute(
            "writing_outline", {"topic": "AI", "sections": 100}, ToolContext())
        assert r["ok"] is True
        assert len(r["outline"]) == 12


# ===========================================================================
# writing_summary — max_sentences
# ===========================================================================
class TestWritingSummaryMaxSentences:
    def test_default(self):
        text = "第一句。第二句。第三句。第四句。第五句。"
        r = _reg(writing_summary).execute("writing_summary", {"text": text}, ToolContext())
        assert r["ok"] is True
        assert r["stats"]["sentence_count"] == 5

    def test_garbage_clean_error(self):
        r = _reg(writing_summary).execute(
            "writing_summary", {"text": "句子。", "max_sentences": "three"}, ToolContext())
        assert r["ok"] is False
        assert "max_sentences" in r["error"]

    def test_clamped(self):
        text = "。".join(f"句{i}" for i in range(20)) + "。"
        r = _reg(writing_summary).execute(
            "writing_summary", {"text": text, "max_sentences": 99}, ToolContext())
        assert r["ok"] is True
        assert len(r["key_sentences"]) == 10
