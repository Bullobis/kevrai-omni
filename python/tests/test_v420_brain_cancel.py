"""Tests for cooperative mid-generation cancellation of the text brain."""
from __future__ import annotations

import sys

import pytest

import app.llm_runtime as brain_mod
from app.llm_runtime import TextBrainManager


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeStoppingList:
    def __init__(self, items):
        self._items = items

    def __call__(self, input_ids, scores, **kwargs):
        return any(c(input_ids, scores, **kwargs) for c in self._items)


class FakeTokenizer:
    model_max_length = 512

    def __call__(self, text, add_special_tokens=True):
        return {"input_ids": [1, 2, 3]}

    def decode(self, ids, skip_special_tokens=True):
        return "partial"


class FakeConfig:
    max_position_embeddings = 512


class CancelAwareModel:
    """Simulates token-by-token decode, honouring stopping criteria."""

    config = FakeConfig()

    def __init__(self, rec):
        self._rec = rec

    def eval(self):
        return self

    def generate(self, input_ids, max_new_tokens, do_sample,
                 repetition_penalty, stopping_criteria=None):
        ids = [1, 2, 3]
        produced = 0
        for i in range(max_new_tokens):
            ids.append(900 + i)
            produced += 1
            if stopping_criteria is not None and stopping_criteria(None, None):
                break
        self._rec["produced"] = produced
        return [ids]


def _fake_tf(rec):
    class AutoTok:
        @staticmethod
        def from_pretrained(repo):
            return FakeTokenizer()

    class AutoModel:
        @staticmethod
        def from_pretrained(repo, torch_dtype=None):
            return CancelAwareModel(rec)

    class TF:
        AutoTokenizer = AutoTok
        AutoModelForCausalLM = AutoModel
        StoppingCriteriaList = FakeStoppingList

    return TF


class FakeTorch:
    long = int

    class no_grad:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    @staticmethod
    def tensor(rows, dtype=None):
        return rows[0]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    rec = {"produced": 0}
    monkeypatch.setattr(brain_mod, "import_transformers",
                        lambda data_root=None: _fake_tf(rec))
    monkeypatch.setitem(sys.modules, "torch", FakeTorch())
    mgr = TextBrainManager(data_root=tmp_path)
    return mgr, rec


# ---------------------------------------------------------------------------
def test_no_signal_runs_full_length(manager):
    mgr, rec = manager
    mgr.generate("o/a", "goal", max_new_tokens=8, should_stop=lambda: False)
    assert rec["produced"] == 8


def test_signal_already_set_stops_after_first_token(manager):
    mgr, rec = manager
    mgr.generate("o/a", "goal", max_new_tokens=8, should_stop=lambda: True)
    # Criteria is evaluated after the first generated token, then halts.
    assert rec["produced"] == 1


def test_signal_trips_after_n_calls(manager):
    mgr, rec = manager
    calls = {"n": 0}

    def signal():
        calls["n"] += 1
        return calls["n"] >= 3

    mgr.generate("o/a", "goal", max_new_tokens=10, should_stop=signal)
    assert rec["produced"] == 3


def test_faulty_signal_does_not_stall_decode(manager):
    mgr, rec = manager

    def bad_signal():
        raise RuntimeError("signal broken")

    mgr.generate("o/a", "goal", max_new_tokens=5, should_stop=bad_signal)
    assert rec["produced"] == 5


def test_no_stopping_criteria_when_no_signal(manager):
    mgr, rec = manager
    # should_stop omitted entirely -> behaves as before, full run.
    mgr.generate("o/a", "goal", max_new_tokens=4)
    assert rec["produced"] == 4


# ---------------------------------------------------------------------------
# Router forwards should_stop
# ---------------------------------------------------------------------------
def test_router_forwards_stop_signal(tmp_path, monkeypatch):
    from app.agent.model_router import ModelRouter

    monkeypatch.setattr(ModelRouter, "_check_mnn", lambda self: (False, ""))
    router = ModelRouter(tmp_path / "brain.json")
    router.configure("transformers:o/r")
    captured = {}

    class RecManager:
        def generate(self, repo, prompt, *, system="", max_new_tokens=1024,
                     should_stop=None):
            captured["should_stop"] = should_stop
            return "ok"

    monkeypatch.setattr(brain_mod, "TextBrainManager", lambda: RecManager())
    sentinel = lambda: False  # noqa: E731
    router.chat("goal", should_stop=sentinel)
    assert captured["should_stop"] is sentinel
