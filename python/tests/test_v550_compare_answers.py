"""Tests for multi-model answer comparison (v4.8.x)."""
from __future__ import annotations

import pytest

from app.llm_runtime import (
    COMPARE_MAX_MODELS,
    BrainError,
    BrainParamError,
    TextBrainManager,
    compare_answers,
    strip_reasoning,
)


def test_strip_reasoning_keeps_final_answer():
    out = strip_reasoning("<think>hidden steps</think>\nFinal answer here.")
    assert out == "Final answer here."


def test_strip_reasoning_no_block_unchanged():
    assert strip_reasoning("Just an answer.") == "Just an answer."


class FakeManager:
    def __init__(self, answers=None, fail_for=()):
        self.answers = answers or {}
        self.fail_for = fail_for
        self.unload_calls = 0
        self.answered = []

    def answer(self, repo, question, *, system="", max_new_tokens=512):
        self.answered.append(repo)
        if repo in self.fail_for:
            raise BrainError("boom")
        return self.answers.get(repo, f"ans:{repo}")

    def unload(self):
        self.unload_calls += 1


def test_compare_returns_answers_in_order():
    mgr = FakeManager(answers={"a/x": "X-answer", "a/y": "Y-answer"})
    out = compare_answers(mgr, ["a/x", "a/y"], "q?")
    assert [r["repo"] for r in out] == ["a/x", "a/y"]
    assert out[0]["answer"] == "X-answer" and out[0]["error"] is None
    assert out[1]["answer"] == "Y-answer"


def test_per_model_error_is_captured():
    mgr = FakeManager(fail_for=("a/bad",))
    out = compare_answers(mgr, ["a/ok", "a/bad", "a/ok2"], "q?")
    assert out[0]["error"] is None
    assert out[1]["error"] == "boom" and out[1]["answer"] == ""
    assert out[2]["error"] is None  # later models still run


def test_unload_after_each():
    mgr = FakeManager()
    compare_answers(mgr, ["a/x", "a/y"], "q?")
    assert mgr.unload_calls == 2


def test_empty_repos_rejected():
    with pytest.raises(BrainParamError):
        compare_answers(FakeManager(), [], "q?")


def test_too_many_repos_rejected():
    repos = [f"a/m{i}" for i in range(COMPARE_MAX_MODELS + 1)]
    with pytest.raises(BrainParamError):
        compare_answers(FakeManager(), repos, "q?")


def test_empty_question_rejected():
    with pytest.raises(BrainParamError):
        compare_answers(FakeManager(), ["a/x"], "  ")


def test_answer_validation_without_model():
    mgr = TextBrainManager.__new__(TextBrainManager)
    with pytest.raises(BrainParamError):
        TextBrainManager.answer(mgr, "o/n", "")
    with pytest.raises(BrainParamError):
        TextBrainManager.answer(mgr, "o/n", "hi", max_new_tokens=0)


# ---------------------------------------------------------------------------
# route (validation path; the happy path loads real models and is not unit-run)
# ---------------------------------------------------------------------------
def test_route_compare_validation_400(hub_client):
    r = hub_client.post("/api/agent/compare-answers",
                        json={"repos": [], "question": ""})
    assert r.status_code == 400
