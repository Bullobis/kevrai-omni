"""Tests for streaming knowledge-base ask (app.knowledge_base.ask_stream_sync).

No model download / torch / GPU is required: embeddings come from the same
deterministic fake as test_v429, and the brain is a fake streaming generator
that yields pre-set chunks. Covers:
  * sources event emitted first (before any delta)
  * answer deltas streamed in order
  * done event carries the full cleaned answer
  * no-brain path: sources then done with note
  * brain failure → done with error note, no exception
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import knowledge_base as kb  # noqa: E402


class FakeEmbedder:
    """Deterministic char-bucket embedder (same as v429)."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    def embed(self, repo: str, texts: list[str], **_kw) -> dict:
        vecs = []
        for text in texts:
            v = [0.0] * self.dim
            for ch in text:
                v[ord(ch) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            vecs.append([x / n for x in v])
        return {"dimensions": self.dim, "embeddings": vecs}


@pytest.fixture()
def mgr(tmp_path):
    return kb.KnowledgeBase(tmp_path, embedder=FakeEmbedder())


def _seed_kb(mgr, name="stream-test", doc_text="The sky is blue on a clear day."):
    k = mgr.create_kb(name, "BAAI/bge-m3")
    bid = k["id"]
    mgr.add_document(bid, "facts.txt", doc_text)
    return bid


# ---------------------------------------------------------------------------
# ask_stream_sync
# ---------------------------------------------------------------------------


def test_stream_emits_sources_first(mgr):
    """The first yielded event must be 'sources' — before any delta."""
    bid = _seed_kb(mgr)

    def fake_stream(system: str, prompt: str):
        yield from ["The ", "sky ", "is ", "blue."]

    events = list(mgr.ask_stream_sync(bid, "what colour?", top_k=2,
                                       brain_stream=fake_stream))
    assert events[0]["type"] == "sources"
    assert events[0]["sources"], "sources must be non-empty"
    assert events[0]["results"], "results must be non-empty"


def test_stream_deltas_then_done(mgr):
    """Deltas arrive in order, then a done event with the full answer."""
    bid = _seed_kb(mgr)

    chunks = ["The ", "sky ", "is ", "blue."]

    def fake_stream(system: str, prompt: str):
        yield from chunks

    events = list(mgr.ask_stream_sync(bid, "what colour?", brain_stream=fake_stream))

    delta_events = [e for e in events if e["type"] == "delta"]
    assert len(delta_events) == len(chunks)
    assert [e["delta"] for e in delta_events] == chunks

    done = events[-1]
    assert done["type"] == "done"
    assert done["brain_used"] is True
    assert done["answer"] == "The sky is blue."
    assert done["answer_note"] is None


def test_stream_no_brain_returns_note(mgr):
    """No brain_stream → sources event, then done with a note, no deltas."""
    bid = _seed_kb(mgr)
    events = list(mgr.ask_stream_sync(bid, "what colour?", brain_stream=None))

    assert events[0]["type"] == "sources"
    delta_events = [e for e in events if e["type"] == "delta"]
    assert delta_events == []

    done = events[-1]
    assert done["type"] == "done"
    assert done["brain_used"] is False
    assert done["answer_note"]  # non-empty note


def test_stream_brain_failure_graceful(mgr):
    """A raising brain_stream yields done with error note, not an exception."""
    bid = _seed_kb(mgr)

    def boom(system: str, prompt: str):
        raise RuntimeError("model exploded")
        yield  # pragma: no cover — make it a generator

    events = list(mgr.ask_stream_sync(bid, "hello", brain_stream=boom))
    assert events[0]["type"] == "sources"
    done = events[-1]
    assert done["type"] == "done"
    assert done["brain_used"] is False
    assert "model exploded" in (done["answer_note"] or "")


def test_stream_sources_structure(mgr):
    """Source entries have ref, doc_id, doc_name, score, excerpt."""
    bid = _seed_kb(mgr, doc_text="Dogs bark and cats meow.")
    events = list(mgr.ask_stream_sync(bid, "sound?", brain_stream=None))
    sources = events[0]["sources"]
    assert len(sources) >= 1
    s = sources[0]
    assert "ref" in s and s["ref"] == 1
    assert "doc_id" in s
    assert "doc_name" in s
    assert "score" in s
    assert "excerpt" in s


def test_stream_clean_brain_answer_applied(mgr):
    """The done answer goes through clean_brain_answer (cuts loops)."""
    bid = _seed_kb(mgr)

    def loop_stream(system: str, prompt: str):
        # Simulate a brain that emits the answer then repeats it on a new line.
        yield "天空是蓝色的。\n\n天空是蓝色的。"

    events = list(mgr.ask_stream_sync(bid, "colour?", brain_stream=loop_stream))
    done = events[-1]
    assert done["type"] == "done"
    # clean_brain_answer should cut the repeated line.
    assert done["answer"] == "天空是蓝色的。"
