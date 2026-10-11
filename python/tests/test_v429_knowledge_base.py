"""Tests for the local knowledge base / RAG core (app.knowledge_base).

No model download / torch / GPU is required: embeddings come from a tiny
deterministic fake. Covers chunking, document parsing, CRUD, ingestion,
pure-Python cosine retrieval, ask context assembly, and the FastAPI routes.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import knowledge_base as kb  # noqa: E402

# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeEmbedder:
    """Deterministic char-bucket embedder.

    Vectors are built by bucketing characters mod ``dim``; identical text maps
    to an identical (normalised) vector, and texts sharing more characters
    score higher cosine — enough to make retrieval deterministic for tests.
    """

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.calls: int = 0

    def embed(self, repo: str, texts: list[str], **_kw) -> dict:
        self.calls += 1
        vecs = []
        for text in texts:
            v = [0.0] * self.dim
            for ch in text:
                v[ord(ch) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            vecs.append([x / n for x in v])
        return {"dimensions": self.dim, "embeddings": vecs}


# --------------------------------------------------------------------------
# Chunking & parsing
# --------------------------------------------------------------------------


def test_chunk_empty_and_whitespace():
    assert kb.chunk_text("") == []
    assert kb.chunk_text("   \n\t  ") == []


def test_chunk_packs_paragraphs_within_size():
    text = "Alpha paragraph.\n\nBeta paragraph.\n\nGamma paragraph."
    chunks = kb.chunk_text(text, chunk_size=200, overlap=0)
    assert len(chunks) == 1
    assert "Alpha" in chunks[0] and "Gamma" in chunks[0]


def test_chunk_splits_long_paragraph_and_overlaps():
    long = ("Sentence number one here. " * 20).strip()
    chunks = kb.chunk_text(long, chunk_size=60, overlap=10)
    assert len(chunks) >= 2
    # Every chunk must be trimmed and non-empty.
    assert all(c.strip() for c in chunks)


def test_chunk_rejects_bad_params():
    with pytest.raises(kb.KnowledgeParamError):
        kb.chunk_text("x", chunk_size=0)
    with pytest.raises(kb.KnowledgeParamError):
        kb.chunk_text("x", chunk_size=100, overlap=60)


def test_extract_csv_flattens_cells():
    out = kb.extract_text("a.csv", "apple, orange\npotato, carrot")
    assert "apple" in out and "potato" in out


def test_extract_json_flattens_leaves():
    out = kb.extract_text("a.json", '{"title": "Notes", "tags": ["x", "y"]}')
    assert "Notes" in out and "x" in out


# --------------------------------------------------------------------------
# CRUD / ingestion / retrieval (pure manager)
# --------------------------------------------------------------------------


@pytest.fixture()
def mgr(tmp_path):
    return kb.KnowledgeBase(tmp_path, embedder=FakeEmbedder())


def test_create_and_list_kb(mgr):
    created = mgr.create_kb("我的库", "BAAI/bge-m3")
    assert created["name"] == "我的库"
    assert created["doc_count"] == 0
    listed = mgr.list_kbs()
    assert len(listed) == 1 and listed[0]["id"] == created["id"]


def test_create_requires_valid_name_and_model(mgr):
    with pytest.raises(kb.KnowledgeParamError):
        mgr.create_kb("   ", "BAAI/bge-m3")
    with pytest.raises(kb.KnowledgeParamError):
        mgr.create_kb("ok", "no-slash")


def test_add_document_writes_files_and_chunks(mgr, tmp_path):
    k = mgr.create_kb("docs", "BAAI/bge-m3")
    bid = k["id"]
    doc = mgr.add_document(bid, "note.md", "The cat sat on the mat. " * 4)
    assert doc["chunk_count"] >= 1
    # On-disk JSON persisted.
    kb_dir = tmp_path / "knowledge" / bid
    assert (kb_dir / "meta.json").is_file()
    assert (kb_dir / "docs.json").is_file()
    assert (kb_dir / "chunks.json").is_file()
    assert mgr.get_kb(bid)["doc_count"] == 1


def test_add_document_empty_content_rejected(mgr):
    k = mgr.create_kb("x", "BAAI/bge-m3")
    with pytest.raises(kb.KnowledgeParamError):
        mgr.add_document(k["id"], "n.md", "   ")


def test_no_embedder_engine_missing(tmp_path):
    mgr = kb.KnowledgeBase(tmp_path, embedder=None)
    k = mgr.create_kb("x", "BAAI/bge-m3")
    with pytest.raises(kb.KnowledgeEngineMissing):
        mgr.add_document(k["id"], "n.md", "some content here")


class EmbeddingEngineMissing(RuntimeError):
    """Mirrors embedding_runtime.EmbeddingEngineMissing (matched by name)."""


class RaisingEmbedder:
    def embed(self, repo: str, texts: list[str], **_kw) -> dict:
        raise EmbeddingEngineMissing("sentence-transformers not installed")


def test_backend_engine_missing_translated_add(tmp_path):
    mgr = kb.KnowledgeBase(tmp_path, embedder=RaisingEmbedder())
    k = mgr.create_kb("x", "BAAI/bge-m3")
    with pytest.raises(kb.KnowledgeEngineMissing):
        mgr.add_document(k["id"], "n.md", "some content here")


def test_backend_engine_missing_translated_search(tmp_path):
    mgr = kb.KnowledgeBase(tmp_path, embedder=FakeEmbedder())
    k = mgr.create_kb("x", "BAAI/bge-m3")
    mgr.add_document(k["id"], "n.md", "useful content body text")
    mgr._embedder = RaisingEmbedder()
    with pytest.raises(kb.KnowledgeEngineMissing):
        mgr.search(k["id"], "body")


def test_search_ranks_relevant_chunk_top(mgr):
    k = mgr.create_kb("find", "BAAI/bge-m3")
    bid = k["id"]
    mgr.add_document(bid, "animals.md",
                     "The quick brown fox jumps over the lazy dog.")
    mgr.add_document(bid, "cities.md",
                     "Tokyo is a busy city with trains and tall buildings.")
    res = mgr.search(bid, "fox dog", top_k=2)
    assert len(res["results"]) == 2
    top = res["results"][0]
    assert top["doc_name"] == "animals.md"
    assert "fox" in top["text"]


def test_delete_document_removes_chunks(mgr):
    k = mgr.create_kb("del", "BAAI/bge-m3")
    bid = k["id"]
    d = mgr.add_document(bid, "a.md", "alpha content about mining asteroids")
    mgr.add_document(bid, "b.md", "beta content about cooking pasta")
    mgr.delete_document(bid, d["id"])
    assert mgr.get_kb(bid)["doc_count"] == 1
    with pytest.raises(kb.KnowledgeNotFound):
        mgr.delete_document(bid, d["id"])


def test_delete_kb_removes_folder(mgr, tmp_path):
    k = mgr.create_kb("gone", "BAAI/bge-m3")
    bid = k["id"]
    mgr.add_document(bid, "a.md", "some meaningful content here")
    mgr.delete_kb(bid)
    assert not (tmp_path / "knowledge" / bid).exists()
    with pytest.raises(kb.KnowledgeNotFound):
        mgr.get_kb(bid)


def test_kb_id_traversal_rejected(mgr):
    with pytest.raises(kb.KnowledgeParamError):
        mgr.get_kb("../evil")


def test_search_empty_query_rejected(mgr):
    k = mgr.create_kb("q", "BAAI/bge-m3")
    with pytest.raises(kb.KnowledgeParamError):
        mgr.search(k["id"], "   ")


def test_search_empty_kb_returns_no_results(mgr):
    k = mgr.create_kb("empty", "BAAI/bge-m3")
    res = mgr.search(k["id"], "anything")
    assert res["results"] == []


def test_ask_assembles_context_without_brain(mgr):
    k = mgr.create_kb("ask", "BAAI/bge-m3")
    bid = k["id"]
    mgr.add_document(bid, "w.md", "Water boils at one hundred degrees.")
    out = mgr.ask(bid, "when does water boil?", top_k=1)
    assert out["brain_used"] is False
    assert out["answer"] is None
    assert "未配置生成模型" in out["answer_note"]
    assert out["results"], "context should be retrieved"


def test_ask_uses_brain_when_provided(mgr):
    k = mgr.create_kb("brain", "BAAI/bge-m3")
    bid = k["id"]
    mgr.add_document(bid, "w.md", "The sky is blue on a clear day.")

    def fake_brain(system: str, prompt: str) -> str:
        return "ANSWER:" + prompt.split("问题：")[-1]

    out = mgr.ask(bid, "what colour is the sky?", top_k=1, brain=fake_brain)
    assert out["brain_used"] is True
    assert out["answer"].startswith("ANSWER:")


def test_ask_brain_failure_does_not_raise(mgr):
    k = mgr.create_kb("failbrain", "BAAI/bge-m3")
    bid = k["id"]
    mgr.add_document(bid, "w.md", "some content about robots")

    def boom(system: str, prompt: str) -> str:
        raise RuntimeError("model down")

    out = mgr.ask(bid, "robots?", brain=boom)
    assert out["brain_used"] is False
    assert out["results"], "context still returned on brain failure"


def test_clean_brain_answer_cuts_second_anchor():
    from app.knowledge_base import clean_brain_answer

    raw = "续航是 42 小时。\n答案：续航是 8 小时。\n答案：续航是 42 小时。"
    assert clean_brain_answer(raw) == "续航是 42 小时。"


# --------------------------------------------------------------------------
# Pinned documents (skip chunk/embed; inject full text on ask)
# --------------------------------------------------------------------------


def test_pinned_stores_full_text_and_no_chunks(mgr, tmp_path):
    k = mgr.create_kb("pin", "BAAI/bge-m3")
    bid = k["id"]
    body = "Company policy: remote work allowed three days per week."
    doc = mgr.add_document(bid, "policy.md", body, pinned=True)
    assert doc["pinned"] is True
    assert doc["chunk_count"] == 0
    # No chunks persisted for the pinned document.
    chunks = mgr._read_json(tmp_path / "knowledge" / bid / "chunks.json")
    assert chunks == []
    assert mgr.get_kb(bid)["chunk_count"] == 0
    # The full text lives on the docs.json record.
    docs = mgr._read_json(tmp_path / "knowledge" / bid / "docs.json")
    assert docs[0]["text"] == body
    # Listing strips the raw text but keeps the pinned flag.
    listed = mgr.list_documents(bid)
    assert listed[0]["pinned"] is True
    assert "text" not in listed[0]


def test_non_pinned_doc_has_no_text_field(mgr):
    k = mgr.create_kb("plain", "BAAI/bge-m3")
    doc = mgr.add_document(k["id"], "n.md", "ordinary indexed content here")
    assert doc["pinned"] is False
    assert "text" not in doc


def test_search_returns_pinned_list_separate_from_results(mgr):
    k = mgr.create_kb("mix", "BAAI/bge-m3")
    bid = k["id"]
    mgr.add_document(bid, "indexed.md", "Tokyo trains and tall buildings.")
    mgr.add_document(bid, "note.md", "Always-on reminder text.", pinned=True)
    res = mgr.search(bid, "trains", top_k=4)
    # The pinned doc is not in the cosine results.
    assert all(r["doc_name"] != "note.md" for r in res["results"])
    # But it is surfaced in the dedicated pinned list.
    assert len(res["pinned"]) == 1
    assert res["pinned"][0]["doc_name"] == "note.md"
    assert res["pinned"][0]["text"] == "Always-on reminder text."


def test_ask_brain_answers_from_pinned_only(mgr):
    k = mgr.create_kb("pinask", "BAAI/bge-m3")
    bid = k["id"]
    # No indexed (chunked) documents at all — only a pinned note.
    mgr.add_document(bid, "fact.md", "The meeting room capacity is 12 people.",
                     pinned=True)

    def fake_brain(system: str, prompt: str) -> str:
        # Echo the injected pinned text so we can prove it reached the prompt.
        return "ANSWER:12" if "会议室" in prompt or "12 people" in prompt else "ANSWER:?"

    out = mgr.ask(bid, "how many people fit in the meeting room?",
                  top_k=4, brain=fake_brain)
    assert out["results"] == [], "no semantic chunks expected"
    assert out["brain_used"] is True
    assert out["answer"] == "ANSWER:12"
    # Pinned block is prefixed in the context.
    assert out["context"].startswith("【钉选】(fact.md)")


def test_unpin_reindexes_then_searchable(mgr):
    k = mgr.create_kb("unpin", "BAAI/bge-m3")
    bid = k["id"]
    doc = mgr.add_document(bid, "note.md",
                            "Renewable solar panels output maximum at noon.",
                            pinned=True)
    # Not searchable while pinned.
    assert mgr.search(bid, "solar panels")["results"] == []
    # Unpin → re-index the stored text.
    updated = mgr.set_pinned(bid, doc["id"], False)
    assert updated["pinned"] is False
    assert updated["chunk_count"] >= 1
    # Now the text is retrievable.
    res = mgr.search(bid, "solar panels")
    assert res["results"], "unpinned document should now be searchable"
    assert res["pinned"] == []


def test_pin_non_pinned_doc_without_text_errors(mgr):
    k = mgr.create_kb("pinerr", "BAAI/bge-m3")
    bid = k["id"]
    doc = mgr.add_document(bid, "idx.md", "a normally indexed paragraph here")
    with pytest.raises(kb.KnowledgeParamError):
        mgr.set_pinned(bid, doc["id"], True)


def test_set_pinned_is_idempotent(mgr):
    k = mgr.create_kb("pinidem", "BAAI/bge-m3")
    bid = k["id"]
    doc = mgr.add_document(bid, "p.md", "pinned body text", pinned=True)
    # Pinning an already-pinned doc is a no-op.
    again = mgr.set_pinned(bid, doc["id"], True)
    assert again["pinned"] is True
    # Unpinning an already-unpinned doc is a no-op.
    mgr.set_pinned(bid, doc["id"], False)
    mgr.set_pinned(bid, doc["id"], False)  # second time must not raise
    assert mgr.search(bid, "pinned body text")["results"]


def test_clean_brain_answer_cuts_repeated_line():
    from app.knowledge_base import clean_brain_answer

    raw = "续航 42 小时。\n重量 137 克。\n续航 42 小时。\n重量 137 克。"
    assert clean_brain_answer(raw) == "续航 42 小时。\n重量 137 克。"


def test_clean_brain_answer_keeps_distinct_multiline():
    from app.knowledge_base import clean_brain_answer

    raw = "第一点：续航 42 小时。\n第二点：重量 137 克。"
    assert clean_brain_answer(raw) == "第一点：续航 42 小时。\n第二点：重量 137 克。"


def test_clean_brain_answer_empty():
    from app.knowledge_base import clean_brain_answer

    assert clean_brain_answer("   ") == ""
    assert clean_brain_answer("") == ""


# --------------------------------------------------------------------------
# FastAPI routes
# --------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path):
    from starlette.testclient import TestClient

    from app import main

    main.app.state.knowledge = kb.KnowledgeBase(tmp_path, embedder=FakeEmbedder())
    return TestClient(main.app)


def test_route_full_flow(client):
    # capabilities
    r = client.get("/api/knowledge/capabilities")
    assert r.status_code == 200 and r.json()["kb_count"] == 0
    # create
    r = client.post("/api/knowledge/kbs",
                    json={"name": "API 库", "model": "BAAI/bge-m3"})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    # add doc
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                    json={"filename": "a.md", "content": "Dense text about quarks"})
    assert r.status_code == 200 and r.json()["chunk_count"] >= 1
    # list docs
    r = client.get(f"/api/knowledge/kbs/{bid}/documents")
    assert r.status_code == 200 and r.json()["count"] == 1
    # search
    r = client.post(f"/api/knowledge/kbs/{bid}/search",
                    json={"query": "quarks"})
    assert r.status_code == 200 and len(r.json()["results"]) == 1
    # ask (no brain)
    r = client.post(f"/api/knowledge/kbs/{bid}/ask",
                    json={"question": "what are quarks?"})
    assert r.status_code == 200
    body = r.json()
    assert body["brain_used"] is False and body["results"]
    # delete doc
    doc_id = body["results"][0]["doc_id"]
    r = client.delete(f"/api/knowledge/kbs/{bid}/documents/{doc_id}")
    assert r.status_code == 200
    # delete kb
    r = client.delete(f"/api/knowledge/kbs/{bid}")
    assert r.status_code == 200
    r = client.get(f"/api/knowledge/kbs/{bid}")
    assert r.status_code == 404


def test_route_missing_kb_404(client):
    r = client.get("/api/knowledge/kbs/doesnotexist")
    assert r.status_code == 404


def test_route_bad_create_400(client):
    # Whitespace-only name passes Pydantic min_length but trips the manager's
    # own KnowledgeParamError, which the route maps to 400.
    r = client.post("/api/knowledge/kbs", json={"name": "   ", "model": "x"})
    assert r.status_code == 400


def test_route_pydantic_validation_422(client):
    # A truly empty string violates Field(min_length=1) → 422.
    r = client.post("/api/knowledge/kbs", json={"name": "", "model": "x"})
    assert r.status_code == 422


def test_route_pin_lifecycle(client):
    # Create a KB and add a pinned document through the API.
    bid = client.post("/api/knowledge/kbs",
                      json={"name": "PIN API", "model": "BAAI/bge-m3"}).json()["id"]
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "short.md", "content": "Top secret note.",
                         "pinned": True})
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["pinned"] is True and doc["chunk_count"] == 0
    doc_id = doc["id"]
    # Listing exposes the pinned flag.
    r = client.get(f"/api/knowledge/kbs/{bid}/documents")
    assert r.status_code == 200
    assert r.json()["items"][0]["pinned"] is True
    # Ask with a brain: pinned-only context still produces an answer.
    r = client.post(f"/api/knowledge/kbs/{bid}/ask",
                   json={"question": "what is the note about?"})
    body = r.json()
    assert body["pinned"] and body["context"].startswith("【钉选】")
    # Unpin through the dedicated endpoint.
    r = client.post(f"/api/knowledge/kbs/{bid}/documents/{doc_id}/pin",
                    json={"pinned": False})
    assert r.status_code == 200, r.text
    assert r.json()["pinned"] is False and r.json()["chunk_count"] >= 1
    # Pinning a now-unpinned (indexed) doc without full text → 400.
    r = client.post(f"/api/knowledge/kbs/{bid}/documents/{doc_id}/pin",
                    json={"pinned": True})
    assert r.status_code == 400
