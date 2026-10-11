"""Tests for the agent knowledge-base (RAG) retrieval tools."""
from __future__ import annotations

import pytest

from app.agent.tool_registry import ToolContext
from app.agent.tools import knowledge_tools as kt


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeKB:
    def __init__(self):
        self._bases = [
            {"id": "k1", "name": "公司政策", "doc_count": 2, "chunk_count": 3},
            {"id": "k2", "name": "笔记", "doc_count": 1, "chunk_count": 1},
        ]

    def list_kbs(self):
        return list(self._bases)

    def search(self, kb_id, query, top_k=5):
        if kb_id != "k1":
            raise KeyError(kb_id)
        return {
            "results": [
                {"score": 0.91, "doc_name": "公司政策.md",
                 "text": "入职第一年享有 5 天年假。"},
            ],
            "pinned": [
                {"doc_name": "考勤制度.md", "text": "工作时间为 9:00-18:00。"},
            ],
        }


class _EngineMissing(Exception):
    pass


class FakeKBMissing(FakeKB):
    def search(self, kb_id, query, top_k=5):
        raise _EngineMissing("未配置嵌入引擎，无法检索")


@pytest.fixture
def ctx():
    c = ToolContext()
    c.extra["_knowledge"] = {"kb": FakeKB()}
    return c


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------
def test_list_knowledge_bases(ctx):
    out = kt.list_knowledge_bases.execute({}, ctx)
    assert out["count"] == 2
    assert out["bases"][0]["name"] == "公司政策"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
def test_search_by_id_returns_passages_and_pinned(ctx):
    out = kt.search_knowledge.execute(
        {"kb": "k1", "query": "年假有几天"}, ctx)
    assert out["kb_id"] == "k1"
    assert "5 天年假" in out["passages"][0]["text"]
    assert out["pinned"][0]["doc_name"] == "考勤制度.md"


def test_search_resolves_kb_by_display_name(ctx):
    out = kt.search_knowledge.execute(
        {"kb": "公司政策", "query": "年假"}, ctx)
    assert out["kb_id"] == "k1"


def test_search_unknown_kb_lists_available(ctx):
    out = kt.search_knowledge.execute(
        {"kb": "不存在", "query": "x"}, ctx)
    assert "error" in out
    assert "公司政策" in out["error"]


def test_search_requires_query(ctx):
    out = kt.search_knowledge.execute({"kb": "k1", "query": ""}, ctx)
    assert out["error"] == "query is required"


def test_search_requires_kb(ctx):
    out = kt.search_knowledge.execute({"query": "x"}, ctx)
    assert "kb" in out["error"]


def test_search_engine_missing_gets_install_hint():
    c = ToolContext()
    c.extra["_knowledge"] = {"kb": FakeKBMissing()}
    out = kt.search_knowledge.execute({"kb": "k1", "query": "x"}, c)
    assert "error" in out
    assert "向量嵌入" in out["error"]


# ---------------------------------------------------------------------------
# real construction path
# ---------------------------------------------------------------------------
def test_knowledge_helper_builds_knowledge_base(tmp_path, monkeypatch):
    monkeypatch.setattr(kt, "default_data_root", lambda: tmp_path)
    c = ToolContext()
    kb = kt._knowledge(c)
    # Construction must not require an embedding call; verify it is wired.
    from app.knowledge_base import KnowledgeBase

    assert isinstance(kb, KnowledgeBase)
    assert kb.root == tmp_path / "knowledge"
