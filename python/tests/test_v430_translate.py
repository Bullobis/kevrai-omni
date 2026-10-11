"""Tests for the on-device translation feature (app.translate + /api/translate).

No torch/transformers/GPU is required: the brain call is injected as a fake
``generate`` callback. Covers prompt assembly, noisy-output cleaning, brain
resolution, parameter validation, and the FastAPI route's happy path plus the
"no brain configured" guidance path (must not 500).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import translate as tr  # noqa: E402

# --------------------------------------------------------------------------
# Prompt assembly
# --------------------------------------------------------------------------

def test_build_prompt_named_source_target():
    p = tr.build_prompt("你好世界", "zh", "en")
    assert "翻译成英文" in p
    assert p.rstrip().endswith("英文：")
    assert "你好世界" in p


def test_build_prompt_auto_detect():
    p = tr.build_prompt("Hello", "auto", "ja")
    assert "判断下面文本的语言" in p
    assert p.rstrip().endswith("日文：")


# --------------------------------------------------------------------------
# Output cleaning (independent from knowledge_base.clean_brain_answer)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ('"The cat sat on the mat."', "The cat sat on the mat."),
    ("「你好世界」", "你好世界"),
    ("译文：The cat sat on the mat.", "The cat sat on the mat."),
    ("Translation: The cat sat on the mat.", "The cat sat on the mat."),
    ("The cat sat on the mat.\nThe cat sat on the mat.", "The cat sat on the mat."),
    ("The cat sat on the mat.\n译文：另一句胡话", "The cat sat on the mat."),
    ("Translation: foo\nTranslation: bar", "foo"),
    ("", ""),
    ("   ", ""),
    ("  Plain text without scaffolding  ", "Plain text without scaffolding"),
    # Real Qwen3-0.6B-shaped output: translation first, then a rambling paragraph.
    ("Today is nice.\n\nThe translation meets all the requirements.", "Today is nice."),
    ("“快速的棕色狐狸跳过了狗。”\n\n这句话出自故事。", "快速的棕色狐狸跳过了狗。"),
    ("**Bold translation here**\n\nNote: extra.", "Bold translation here"),
])
def test_clean_translation(raw, expected):
    assert tr.clean_translation(raw) == expected


# --------------------------------------------------------------------------
# Brain resolution
# --------------------------------------------------------------------------

def test_resolve_explicit_model_wins():
    assert tr.resolve_brain_repo("Other/Repo",
                                  ("transformers", "Qwen/Qwen3-0.6B")) == "Other/Repo"


def test_resolve_falls_back_to_current_brain():
    assert tr.resolve_brain_repo("", ("transformers", "Qwen/Qwen3-0.6B")) \
        == "Qwen/Qwen3-0.6B"


@pytest.mark.parametrize("backend", [
    (None, None),
    ("mnn", None),
    ("transformers", "no-slash"),
])
def test_resolve_no_usable_brain(backend):
    assert tr.resolve_brain_repo("", backend) is None
    assert tr.resolve_brain_repo("  ", backend) is None


# --------------------------------------------------------------------------
# translate_text with an injected fake brain
# --------------------------------------------------------------------------

def test_translate_text_cleans_brain_output():
    seen = {}

    def fake(system, prompt):
        seen["system"] = system
        seen["prompt"] = prompt
        return '"Good morning"'

    out = tr.translate_text(fake, text="早上好", source_lang="zh", target_lang="en")
    assert out == "Good morning"
    # The strict system instruction is handed to the model.
    assert "只输出译文" in seen["system"]


def test_translate_text_rejects_empty_text():
    with pytest.raises(tr.TranslateParamError):
        tr.translate_text(lambda s, p: "x", text="   ", source_lang="zh",
                          target_lang="en")


def test_translate_text_rejects_auto_target():
    with pytest.raises(tr.TranslateParamError):
        tr.translate_text(lambda s, p: "x", text="hi", source_lang="zh",
                          target_lang="auto")


def test_translate_text_rejects_unknown_target():
    with pytest.raises(tr.TranslateParamError):
        tr.translate_text(lambda s, p: "x", text="hi", source_lang="zh",
                          target_lang="klingon")


# --------------------------------------------------------------------------
# FastAPI routes
# --------------------------------------------------------------------------


class _FakeRouter:
    def __init__(self, backend):
        self.backend = backend


class _FakeAgent:
    def __init__(self, backend):
        self.router = _FakeRouter(backend)


class _FakeManager:
    """Records the last generate() call; returns noisy output to be cleaned."""

    last = None

    def __init__(self, root):
        self.root = root

    def generate(self, repo, prompt, *, system="", max_new_tokens=1024,
                 should_stop=None):
        _FakeManager.last = {"repo": repo, "prompt": prompt}
        return '"Hello, world"'


@pytest.fixture()
def client(monkeypatch):
    from starlette.testclient import TestClient

    from app import llm_runtime, main

    monkeypatch.setattr(llm_runtime, "TextBrainManager", _FakeManager)
    return TestClient(main.app)


def _point_agent(monkeypatch, backend):
    from app import main
    monkeypatch.setattr(main, "_get_agent",
                        lambda request: _FakeAgent(backend))


def test_route_happy_path_cleans_and_returns_model(client, monkeypatch):
    _point_agent(monkeypatch, ("transformers", "Qwen/Qwen3-0.6B"))
    r = client.post("/api/translate",
                    json={"text": "你好世界", "source_lang": "zh",
                          "target_lang": "en"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["translation"] == "Hello, world"  # quotes stripped
    assert body["model"] == "Qwen/Qwen3-0.6B"
    assert body["target_lang"] == "en"


def test_route_explicit_model_overrides_current(client, monkeypatch):
    _point_agent(monkeypatch, ("transformers", "Qwen/Qwen3-0.6B"))
    r = client.post("/api/translate",
                    json={"text": "hi", "source_lang": "en",
                          "target_lang": "ja", "model": "Other/Repo"})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "Other/Repo"


def test_route_no_brain_returns_guidance_not_500(client, monkeypatch):
    _point_agent(monkeypatch, (None, None))  # rule-based, no transformers brain
    r = client.post("/api/translate",
                    json={"text": "hi", "source_lang": "auto",
                          "target_lang": "en"})
    assert r.status_code == 409, r.text
    assert "大脑" in r.json()["detail"]


def test_route_pydantic_validation_422(client, monkeypatch):
    _point_agent(monkeypatch, ("transformers", "Qwen/Qwen3-0.6B"))
    # Empty text violates min_length=1 -> 422 (never touches the model).
    r = client.post("/api/translate",
                    json={"text": "", "target_lang": "en"})
    assert r.status_code == 422
