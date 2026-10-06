"""Tests for the agent brain router (local LLM backend selection)."""
from __future__ import annotations

import json

import pytest

from app.agent.model_router import ModelRouter


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "brain.json"


@pytest.fixture
def router(state_path, monkeypatch):
    # Make MNN availability deterministic (off) unless a test overrides.
    monkeypatch.setattr(ModelRouter, "_check_mnn", lambda self: (False, ""))
    return ModelRouter(state_path)


# ----------------------------------------------------------------------
# Configuration & persistence
# ----------------------------------------------------------------------
def test_default_backend_is_auto(router):
    assert router.backend == (None, None)


def test_configure_transformers(router, state_path):
    router.configure("transformers:Qwen/Qwen3-0.6B")
    assert router.backend == ("transformers", "Qwen/Qwen3-0.6B")
    # Persisted to disk.
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data == {"type": "transformers", "repo": "Qwen/Qwen3-0.6B"}


def test_state_roundtrip(state_path, monkeypatch):
    monkeypatch.setattr(ModelRouter, "_check_mnn", lambda self: (False, ""))
    ModelRouter(state_path).configure("transformers:o/r")
    reloaded = ModelRouter(state_path)
    assert reloaded.backend == ("transformers", "o/r")


@pytest.mark.parametrize("spec", ["", "auto", "mnn"])
def test_configure_mnn_and_auto(router, spec):
    router.configure(spec)
    expected = ("mnn", None) if spec == "mnn" else (None, None)
    assert router.backend == expected


def test_invalid_spec_raises(router):
    with pytest.raises(ValueError):
        router.configure("vapourware:model")
    with pytest.raises(ValueError):
        router.configure("transformers:no-slash")


def test_corrupt_state_ignored(state_path, monkeypatch):
    state_path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(ModelRouter, "_check_mnn", lambda self: (False, ""))
    assert ModelRouter(state_path).backend == (None, None)


# ----------------------------------------------------------------------
# Readiness
# ----------------------------------------------------------------------
def test_transformers_ready_when_configured(router):
    router.configure("transformers:o/r")
    ready, name = router.is_ready()
    assert ready is True
    assert name == "transformers:o/r"


def test_auto_not_ready_without_mnn(router):
    ready, name = router.is_ready()
    assert ready is False


def test_mnn_forced_not_ready(router):
    router.configure("mnn")
    ready, _ = router.is_ready()
    assert ready is False


# ----------------------------------------------------------------------
# Chat routing
# ----------------------------------------------------------------------
def test_transformers_chat(router, monkeypatch):
    router.configure("transformers:o/r")

    class FakeManager:
        def __init__(self):
            pass

        def generate(self, repo, prompt, *, system="", max_new_tokens=1024):
            assert repo == "o/r"
            return "planned answer"

    import app.llm_runtime as brain_mod
    monkeypatch.setattr(brain_mod, "TextBrainManager", FakeManager)
    res = router.chat("goal", system="sys")
    assert res["ok"] is True
    assert res["text"] == "planned answer"
    assert res["model_name"] == "transformers:o/r"


def test_transformers_empty_output(router, monkeypatch):
    router.configure("transformers:o/r")

    class FakeManager:
        def generate(self, repo, prompt, *, system="", max_new_tokens=1024):
            return "   "

    import app.llm_runtime as brain_mod
    monkeypatch.setattr(brain_mod, "TextBrainManager", FakeManager)
    res = router.chat("goal")
    assert res["ok"] is False
    assert res["error_type"] == "EmptyOutput"


def test_transformers_brain_error(router, monkeypatch):
    router.configure("transformers:o/r")

    class FakeManager:
        def generate(self, repo, prompt, *, system="", max_new_tokens=1024):
            raise brain_mod.BrainModelError("boom")

    import app.llm_runtime as brain_mod
    monkeypatch.setattr(brain_mod, "TextBrainManager", FakeManager)
    res = router.chat("goal")
    assert res["ok"] is False
    assert res["error_type"] == "BrainModelError"


def test_auto_chat_not_ready_message(router):
    res = router.chat("hello")
    assert res["ok"] is False
    assert res["error_type"] == "LlmNotReady"


def test_mnn_chat_success(router, monkeypatch):
    router.configure("mnn")
    monkeypatch.setattr(ModelRouter, "_check_mnn",
                        lambda self: (True, "mnn-model"))
    import app.mnn_runtime as mnn
    monkeypatch.setattr(mnn, "chat",
                        lambda prompt, max_new_tokens=2048:
                        {"text": "mnn answer"})
    res = router.chat("hello")
    assert res["ok"] is True
    assert res["text"] == "mnn answer"
    assert res["model_name"] == "mnn-model"
