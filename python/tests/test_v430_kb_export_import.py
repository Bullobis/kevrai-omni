"""Tests for knowledge base export / import (app.knowledge_base).

Uses the same deterministic fake embedder as the v4.29 suite — no model
download. Covers exact-text export, original cleanup on delete, the legacy
chunks-based reconstruction fallback, round-trip import + retrieval, bundle
validation, and the missing-engine guidance.
"""
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


def _make(mgr, name="库", model="BAAI/bge-m3"):
    k = mgr.create_kb(name, model)
    bid = k["id"]
    mgr.add_document(bid, "a.md", "The quick brown fox jumps over the lazy dog.")
    mgr.add_document(bid, "note.txt", "Always read the manual first.",
                     pinned=True)
    return bid


def test_export_carries_exact_text(mgr):
    bid = _make(mgr)
    bundle = mgr.export_kb(bid)
    assert bundle["format"] == "kevrai-kb" and bundle["version"] == 1
    assert bundle["meta"]["model"] == "BAAI/bge-m3"
    by_name = {d["filename"]: d for d in bundle["documents"]}
    # Exact original text, not the overlapping chunks, and not reconstructed.
    assert by_name["a.md"]["text"] == \
        "The quick brown fox jumps over the lazy dog."
    assert by_name["a.md"].get("reconstructed") is not True
    assert by_name["note.txt"]["pinned"] is True
    assert "manual" in by_name["note.txt"]["text"]


def test_delete_removes_original(mgr, tmp_path):
    bid = _make(mgr)
    docs = mgr.list_documents(bid)
    target = next(d for d in docs if d["filename"] == "a.md")
    mgr.delete_document(bid, target["id"])
    originals = mgr._load_originals(bid)
    assert target["id"] not in originals
    bundle = mgr.export_kb(bid)
    assert [d["filename"] for d in bundle["documents"]] == ["note.txt"]


def test_import_roundtrip_search_hits(mgr, tmp_path):
    bid = _make(mgr)
    bundle = mgr.export_kb(bid)
    result = mgr.import_kb(bundle)
    assert result["doc_count"] == 2
    new_hit = mgr.search(result["kb_id"], "brown fox", top_k=2)
    assert new_hit["results"], "imported KB should still retrieve"
    assert "fox" in new_hit["results"][0]["text"]


def test_import_name_dedup(mgr):
    bid = _make(mgr, name="我的库")
    bundle = mgr.export_kb(bid)
    r1 = mgr.import_kb(bundle)
    r2 = mgr.import_kb(bundle)
    assert r1["name"] == "我的库 (2)"
    assert r2["name"] == "我的库 (3)"


def test_pinned_survives_import(mgr):
    bid = _make(mgr)
    bundle = mgr.export_kb(bid)
    result = mgr.import_kb(bundle)
    docs = mgr.list_documents(result["kb_id"])
    pinned = [d for d in docs if d.get("pinned")]
    assert len(pinned) == 1 and pinned[0]["filename"] == "note.txt"


def test_legacy_reconstruct_when_originals_missing(mgr, tmp_path):
    bid = _make(mgr)
    # Simulate a KB ingested before originals.json existed.
    (tmp_path / "knowledge" / bid / "originals.json").unlink()
    bundle = mgr.export_kb(bid)
    by_name = {d["filename"]: d for d in bundle["documents"]}
    # The normal doc must be rebuilt from chunks and flagged reconstructed.
    assert by_name["a.md"].get("reconstructed") is True
    assert "fox" in by_name["a.md"]["text"]
    # The pinned doc keeps its text from docs.json (still exact).
    assert "manual" in by_name["note.txt"]["text"]


def test_bad_bundle_rejected(mgr):
    with pytest.raises(kb.KnowledgeParamError):
        mgr.import_kb({})
    with pytest.raises(kb.KnowledgeParamError):
        mgr.import_kb({"format": "other", "version": 1})
    with pytest.raises(kb.KnowledgeParamError):
        mgr.import_kb({"format": "kevrai-kb", "version": 99})
    with pytest.raises(kb.KnowledgeParamError):
        mgr.import_kb({"format": "kevrai-kb", "version": 1,
                       "meta": {}, "documents": []})


def test_import_engine_missing_guidance(tmp_path):
    # Build a valid bundle with an embedder, then import with no embedder.
    src = kb.KnowledgeBase(tmp_path, embedder=FakeEmbedder())
    bid = _make(src)
    bundle = src.export_kb(bid)

    dst_root = tmp_path / "dst"
    dst_root.mkdir()
    dst = kb.KnowledgeBase(dst_root, embedder=None)
    with pytest.raises(kb.KnowledgeEngineMissing):
        dst.import_kb(bundle)
    # Transactional: the failed import must not leave a half-built KB behind.
    assert dst.list_kbs() == []


def test_import_rolls_back_when_embedder_fails(tmp_path):
    # Embedder present at create time but raises on the second (non-pinned) doc.
    src = kb.KnowledgeBase(tmp_path, embedder=FakeEmbedder())
    bid = _make(src)
    bundle = src.export_kb(bid)

    class EmbeddingEngineMissing(RuntimeError):
        pass

    class FlakyEmbedder:
        # Succeeds for the pinned path (no embed call) then fails on chunk embed.
        def embed(self, repo: str, texts: list[str], **_kw) -> dict:
            raise EmbeddingEngineMissing("backend gone")

    dst_root = tmp_path / "dst2"
    dst_root.mkdir()
    dst = kb.KnowledgeBase(dst_root, embedder=FlakyEmbedder())
    with pytest.raises(kb.KnowledgeEngineMissing):
        dst.import_kb(bundle)
    assert dst.list_kbs() == []


def test_export_missing_kb_404(mgr):
    with pytest.raises(kb.KnowledgeNotFound):
        mgr.export_kb("doesnotexist")


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path):
    from starlette.testclient import TestClient

    from app import main

    main.app.state.knowledge = kb.KnowledgeBase(tmp_path, embedder=FakeEmbedder())
    return TestClient(main.app)


def test_route_export_then_import(client):
    r = client.post("/api/knowledge/kbs",
                    json={"name": "路由库", "model": "BAAI/bge-m3"})
    bid = r.json()["id"]
    client.post(f"/api/knowledge/kbs/{bid}/documents",
                json={"filename": "x.md", "content": "Searchable alpha bravo text"})
    r = client.get(f"/api/knowledge/kbs/{bid}/export")
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["format"] == "kevrai-kb"
    r = client.post("/api/knowledge/import", json={"bundle": bundle})
    assert r.status_code == 200, r.text
    new_id = r.json()["kb_id"]
    r = client.post(f"/api/knowledge/kbs/{new_id}/search",
                    json={"query": "alpha bravo"})
    assert r.status_code == 200 and r.json()["results"]


def test_route_import_bad_bundle_400(client):
    r = client.post("/api/knowledge/import",
                    json={"bundle": {"format": "nope"}})
    assert r.status_code == 400
