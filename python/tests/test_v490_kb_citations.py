"""Tests for grounded-answer source citations (KnowledgeBase.ask `sources`)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import knowledge_base as kb  # noqa: E402


class FakeEmbedder:
    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    def embed(self, repo: str, texts: list[str], **_kw) -> dict:
        vecs = []
        for t in texts:
            v = [0.0] * self.dim
            for ch in t:
                v[ord(ch) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            vecs.append([x / n for x in v])
        return {"dimensions": self.dim, "embeddings": vecs}


@pytest.fixture()
def base(tmp_path):
    mgr = kb.KnowledgeBase(tmp_path, embedder=FakeEmbedder())
    kid = mgr.create_kb("引用库", model="BAAI/bge-m3")["id"]
    mgr.add_document(kid, "policy.md",
                     "年假政策：入职满一年享有五天年假，满五年享有十天年假。")
    mgr.add_document(kid, "expense.md",
                     "报销政策：差旅费用需在三十天内提交发票进行报销。")
    return mgr, kid


def test_sources_map_retrieved_chunks(base):
    mgr, kid = base
    out = mgr.ask(kid, "年假有几天？",
                  brain=lambda system, prompt: "满一年有五天年假 [1]。")
    sources = out["sources"]
    assert sources, "expected a source map"
    assert [s["ref"] for s in sources] == list(range(1, len(sources) + 1))
    first = sources[0]
    assert set(first) >= {"ref", "doc_id", "doc_name", "score", "excerpt"}
    assert isinstance(first["score"], float)
    # The answer kept the model's citation marker.
    assert "[1]" in out["answer"]


def test_sources_present_without_brain(base):
    mgr, kid = base
    out = mgr.ask(kid, "报销流程是什么？", brain=None)
    assert out["answer"] is None
    assert out["brain_used"] is False
    assert out["sources"], "sources should be returned even without a brain"


def test_source_excerpt_truncates():
    long = "word " * 100
    excerpt = kb._source_excerpt(long, limit=40)
    assert excerpt.endswith("…")
    assert len(excerpt) <= 44  # 40 + ellipsis (… is one char)
    flat = kb._source_excerpt("a\n\n b\t c", limit=40)
    assert flat == "a b c"


def test_system_prompt_forbids_invented_sources(base):
    mgr, kid = base
    captured = {}

    def fake_brain(system, prompt):
        captured["system"] = system
        captured["prompt"] = prompt
        return "ok [1]"

    mgr.ask(kid, "年假", brain=fake_brain)
    assert "禁止编造来源" in captured["system"]
    assert "[1]" in captured["prompt"]
