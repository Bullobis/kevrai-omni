"""Hardening tests for the text brain: shared load, context truncation, limits."""
from __future__ import annotations

import sys

import pytest

import app.llm_runtime as brain_mod
from app.llm_runtime import (
    BrainParamError,
    TextBrainManager,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeTensorRow:
    def __init__(self, ids):
        self._ids = list(ids)

    def __getitem__(self, sl):
        return self._ids[sl]

    def tolist(self):
        return list(self._ids)


class FakeTensor:
    def __init__(self, ids):
        self._ids = list(ids)
        self.shape = (1, len(self._ids))

    def __getitem__(self, idx):
        assert idx == 0
        return FakeTensorRow(self._ids)


class FakeNoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeTorch:
    long = int
    no_grad = FakeNoGrad

    @staticmethod
    def tensor(rows, dtype=None):
        return FakeTensor(rows[0])


class FakeTokenizer:
    model_max_length = 128

    def __call__(self, text, add_special_tokens=True):
        # One token per character gives long, deterministic sequences.
        return {"input_ids": [ord(c) % 50 + 1 for c in text]}

    def decode(self, ids, skip_special_tokens=True):
        return " ".join(str(i) for i in ids)


class FakeConfig:
    max_position_embeddings = 128


class FakeModel:
    config = FakeConfig()

    def __init__(self, rec):
        self._rec = rec

    def eval(self):
        return self

    def generate(self, input_ids, max_new_tokens, do_sample,
                 repetition_penalty, stopping_criteria=None):
        self._rec["input_len"] = input_ids.shape[1]
        self._rec["generate_calls"] += 1
        new = list(range(900, 900 + max_new_tokens))
        return [input_ids[0].tolist() + new]


def _make_fake_tf(rec):
    class FakeAutoTok:
        @staticmethod
        def from_pretrained(repo):
            rec["tokenizer_loads"] += 1
            return FakeTokenizer()

    class FakeAutoModel:
        @staticmethod
        def from_pretrained(repo, torch_dtype=None):
            rec["model_loads"] += 1
            return FakeModel(rec)

    class FakeTF:
        AutoTokenizer = FakeAutoTok
        AutoModelForCausalLM = FakeAutoModel

    return FakeTF


@pytest.fixture
def wired(tmp_path, monkeypatch):
    rec = {"model_loads": 0, "tokenizer_loads": 0, "generate_calls": 0,
           "input_len": 0}
    fake_tf = _make_fake_tf(rec)
    monkeypatch.setattr(brain_mod, "import_transformers",
                        lambda data_root=None: fake_tf)
    fake_torch = FakeTorch()
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    mgr = TextBrainManager(data_root=tmp_path)
    return mgr, rec


# ---------------------------------------------------------------------------
# Shared / cached loading
# ---------------------------------------------------------------------------
def test_model_loaded_once_across_turns(wired):
    mgr, rec = wired
    mgr.generate("o/a", "do thing one", max_new_tokens=8)
    mgr.generate("o/a", "do thing two", max_new_tokens=8)
    assert rec["model_loads"] == 1
    assert rec["tokenizer_loads"] == 1
    assert rec["generate_calls"] == 2


def test_changing_repo_reloads(wired):
    mgr, rec = wired
    mgr.generate("o/a", "first", max_new_tokens=8)
    mgr.generate("o/b", "second", max_new_tokens=8)
    assert rec["model_loads"] == 2


# ---------------------------------------------------------------------------
# Context truncation
# ---------------------------------------------------------------------------
def test_long_prompt_is_truncated_to_budget(wired):
    mgr, rec = wired
    long_prompt = "x" * 400  # 400 tokens > context 128
    # context 128, 16 new tokens -> budget 112
    mgr.generate("o/a", long_prompt, max_new_tokens=16)
    assert rec["input_len"] <= 112


def test_system_prefix_kept_when_truncating(wired):
    mgr, rec = wired
    out = mgr.generate("o/a", "z" * 400, system="SYS", max_new_tokens=16)
    # Generation still decodes the synthetic continuation.
    assert "900" in out
    assert rec["input_len"] <= 112


def test_excessive_new_tokens_rejected(wired):
    mgr, _ = wired
    # context 128, requesting 1024 new tokens leaves no room.
    with pytest.raises(BrainParamError):
        mgr.generate("o/a", "hello", max_new_tokens=1024)


def test_empty_prompt_rejected(wired):
    mgr, _ = wired
    with pytest.raises(BrainParamError):
        mgr.generate("o/a", "   ", max_new_tokens=8)


# ---------------------------------------------------------------------------
# Router keeps one shared manager across ReAct turns
# ---------------------------------------------------------------------------
def test_router_shared_brain(tmp_path, monkeypatch):
    from app.agent.model_router import ModelRouter

    monkeypatch.setattr(ModelRouter, "_check_mnn", lambda self: (False, ""))
    router = ModelRouter(tmp_path / "brain.json")
    router.configure("transformers:o/r")

    instances = []

    class CountingManager:
        def __init__(self):
            instances.append(self)
            self.calls = 0

        def generate(self, repo, prompt, *, system="", max_new_tokens=1024, should_stop=None):
            self.calls += 1
            return f"turn {self.calls}"

    monkeypatch.setattr(brain_mod, "TextBrainManager", CountingManager)
    r1 = router.chat("goal one")
    r2 = router.chat("goal two")
    assert r1["ok"] and r2["ok"]
    assert len(instances) == 1
    assert instances[0].calls == 2
    assert router._brain_manager is instances[0]
