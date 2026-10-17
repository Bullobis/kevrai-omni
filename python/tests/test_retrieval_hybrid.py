"""Tests for hybrid retrieval: BM25 + dense vectors fused with RRF.

Everything is offline and deterministic — no model download, no network. The
embedding engine is replaced by a keyword-routed fake whose cosine ranking is
fully controllable, so dense and lexical orderings can be made to disagree on
purpose: that disagreement is exactly what the hybrid mode must resolve.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import knowledge_base as kb  # noqa: E402
from app.retrieval import (  # noqa: E402
    BM25,
    MODE_BM25,
    MODE_DENSE,
    MODE_HYBRID,
    fuse_dense_bm25,
    hybrid_rank,
    rrf_fuse,
    tokenize,
)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------
class KeywordEmbedder:
    """Embedder whose vectors are chosen by keyword, giving exact control.

    ``ALPHA`` → [1,0,0,0], ``BETA`` → [0,1,0,0], ``GAMMA`` → [0,0,1,0];
    anything else → [0,0,0,1]. Cosine is therefore 1.0 only between texts
    routed to the same bucket, and 0.0 otherwise — so a query mentioning a
    Chinese term that the fake cannot route scores 0 against every chunk and
    dense retrieval degenerates, which is the situation BM25 must rescue.
    """

    ROUTES = {"ALPHA": 0, "BETA": 1, "GAMMA": 2}

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls = 0

    def embed(self, repo: str, texts: list[str], **_kw) -> dict:
        self.calls += 1
        vecs = []
        for text in texts:
            v = [0.0] * self.dim
            upper = str(text).upper()
            idx = next((i for word, i in self.ROUTES.items() if word in upper),
                       self.dim - 1)
            v[idx] = 1.0
            vecs.append(v)
        return {"dimensions": self.dim, "embeddings": vecs}


def _make_kb(tmp_path, *, embedder, name="hybrid"):
    mgr = kb.KnowledgeBase(tmp_path, embedder=embedder)
    meta = mgr.create_kb(name, "fake/model", chunk_size=120, overlap=0)
    return mgr, meta["id"]


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------
def test_tokenize_ascii_words():
    assert tokenize("Hello World") == ["hello", "world"]


def test_tokenize_cjk_emits_unigrams_and_bigrams():
    toks = tokenize("知识库")
    assert "知" in toks and "识" in toks and "库" in toks
    assert "知识" in toks and "识库" in toks


def test_tokenize_bigrams_do_not_cross_separators():
    # "知 识" must not produce the bigram "知识" — they are not contiguous.
    assert "知识" not in tokenize("知 识", bigrams=True)
    assert "知识" in tokenize("知识", bigrams=True)


def test_tokenize_empty_and_punctuation():
    assert tokenize("") == []
    assert tokenize("。，！") == []


def test_tokenize_splits_version_like_strings():
    toks = tokenize("v2.8.0")
    # The whole run is not a single token, but each part is reachable.
    assert toks == ["v2", "8", "0"]


# --------------------------------------------------------------------------
# BM25
# --------------------------------------------------------------------------
def test_bm25_empty_corpus_scores_nothing():
    assert BM25().fit([]).search("anything") == []
    assert BM25().fit(["a"]).score_terms(["x"]) == [0.0]


def test_bm25_exact_term_ranks_first():
    bm = BM25().fit([
        "The quick brown fox jumps over the lazy dog",
        "BM25 is a ranking function used by search engines",
        "Unrelated passage about cooking pasta",
    ])
    hits = bm.search("BM25 ranking", top_k=3)
    assert hits, "BM25 must match a literal term"
    assert hits[0][0] == 1


def test_bm25_rarer_term_scores_higher_than_common():
    corpus = ["alpha beta common", "alpha common", "alpha common"]
    bm = BM25().fit(corpus)
    rare = bm.score_terms(["beta"])[0]
    common = bm.score_terms(["alpha"])[0]
    assert rare > common > 0.0


def test_bm25_b_controls_length_normalisation():
    short, long = "alpha", "alpha " + ("filler " * 60)
    # b=0 disables length normalisation: equal tf gives equal score.
    assert BM25(b=0.0).fit([short, long]).score_terms(["alpha"])[0] == pytest.approx(
        BM25(b=0.0).fit([short, long]).score_terms(["alpha"])[1]
    )
    # b=1 penalises the long document.
    s_short, s_long = BM25(b=1.0).fit([short, long]).score_terms(["alpha"])
    assert s_short > s_long


def test_bm25_rejects_bad_parameters():
    with pytest.raises(ValueError):
        BM25(k1=-1)
    with pytest.raises(ValueError):
        BM25(b=1.5)


def test_bm25_no_match_returns_empty():
    bm = BM25().fit(["alpha beta gamma"])
    assert bm.search("zeta") == []


def test_bm25_idf_is_positive_and_smoothed():
    bm = BM25().fit(["alpha", "alpha", "beta"])
    assert bm.idf("unknown-term") > 0.0  # smoothed, never zero/negative
    assert bm.idf("alpha") < bm.idf("beta")  # rarer term → higher idf


# --------------------------------------------------------------------------
# RRF
# --------------------------------------------------------------------------
def test_rrf_fuse_empty_inputs():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []


def test_rrf_fuse_single_list_preserves_order():
    fused = rrf_fuse([[("a", 10.0), ("b", 5.0)]], k=60)
    assert [k for k, _ in fused] == ["a", "b"]
    assert fused[0][1] == pytest.approx(1 / 61)
    assert fused[1][1] == pytest.approx(1 / 62)


def test_rrf_fuse_rewards_agreement():
    # x ranks 1st in both lists, y 2nd in both: x must win by a clear margin.
    agreed = rrf_fuse([[("x", 1.0), ("y", 0.9)], [("x", 0.5), ("y", 0.4)]], k=60)
    assert agreed[0][0] == "x"
    assert agreed[0][1] == pytest.approx(2 / 61)
    assert agreed[1][1] == pytest.approx(2 / 62)
    # A single flip in one list is not enough to overturn a 2-list consensus.
    flipped = rrf_fuse([[("x", 1.0), ("y", 0.9)], [("y", 0.5), ("x", 0.4)]], k=60)
    assert flipped[0][0] == "x"


def test_rrf_fuse_is_scale_free():
    # Raw scores differ by orders of magnitude; ranks are identical, so the
    # fused result must be identical too.
    a = rrf_fuse([[("x", 1.0), ("y", 0.5)]], k=60)
    b = rrf_fuse([[("x", 1000.0), ("y", 0.001)]], k=60)
    assert a == b


def test_fuse_dense_bm25_merges_both_lists():
    dense = [(0, 0.9), (1, 0.4)]
    lexical = [(3, 12.0), (1, 9.0)]
    fused = fuse_dense_bm25(dense, lexical)
    # 1 appears in both → highest RRF weight.
    assert fused[0][0] == 1
    assert {i for i, _ in fused} == {0, 1, 3}


def test_hybrid_rank_returns_lexical_order():
    records = [{"text": "alpha"}, {"text": "beta beta"}, {"text": "gamma"}]
    ranked = hybrid_rank(records, "beta", candidate_pool=10)
    assert ranked[0][0] == 1


# --------------------------------------------------------------------------
# KnowledgeBase integration
# --------------------------------------------------------------------------
def test_search_default_mode_is_dense(tmp_path):
    """The default must reproduce the historical dense behaviour exactly."""
    mgr, kb_id = _make_kb(tmp_path, embedder=KeywordEmbedder())
    mgr.add_document(kb_id, "a.txt", "ALPHA document about nothing else")
    mgr.add_document(kb_id, "b.txt", "BETA document about nothing else")

    hit = mgr.search(kb_id, "ALPHA")
    assert hit["mode"] == MODE_DENSE
    assert hit["results"][0]["doc_name"] == "a.txt"
    assert hit["results"][0]["score"] == pytest.approx(1.0)
    assert hit["results"][1]["score"] == pytest.approx(0.0)
    assert all(r["match"] == "dense" for r in hit["results"])


def test_search_bm25_matches_exact_term_where_dense_fails(tmp_path):
    """A Chinese keyword query is invisible to the fake embedder but exact for BM25."""
    mgr, kb_id = _make_kb(tmp_path, embedder=KeywordEmbedder())
    mgr.add_document(kb_id, "cn.txt", "混合检索采用关键词与向量融合的方案")
    mgr.add_document(kb_id, "en.txt", "ALPHA unrelated english passage")

    # "ALPHA" dominates the fake vector, so dense retrieval confidently returns
    # the wrong document — the exact failure BM25 has to correct.
    dense = mgr.search(kb_id, "ALPHA 混合检索", mode=MODE_DENSE)
    assert dense["results"][0]["doc_name"] == "en.txt"

    lexical = mgr.search(kb_id, "ALPHA 混合检索", mode=MODE_BM25)
    assert lexical["mode"] == MODE_BM25
    assert lexical["results"], "BM25 must find the exact Chinese term"
    assert lexical["results"][0]["doc_name"] == "cn.txt"
    assert lexical["results"][0]["match"] == "bm25"


def test_search_hybrid_keeps_both_signals(tmp_path):
    mgr, kb_id = _make_kb(tmp_path, embedder=KeywordEmbedder())
    mgr.add_document(kb_id, "dense.txt", "ALPHA semantic passage")
    mgr.add_document(kb_id, "lex.txt", "混合检索 lexical passage")

    hit = mgr.search(kb_id, "ALPHA 混合检索", mode=MODE_HYBRID, top_k=5)
    assert hit["mode"] == MODE_HYBRID
    names = {r["doc_name"] for r in hit["results"]}
    assert {"dense.txt", "lex.txt"} <= names, "hybrid must keep hits from both sides"
    kinds = {r["match"] for r in hit["results"]}
    assert kinds <= {"dense", "bm25", "both"}


def test_search_hybrid_marks_agreement_as_both(tmp_path):
    mgr, kb_id = _make_kb(tmp_path, embedder=KeywordEmbedder())
    mgr.add_document(kb_id, "hit.txt", "ALPHA 混合检索 passage")
    mgr.add_document(kb_id, "miss.txt", "unrelated filler text")

    hit = mgr.search(kb_id, "ALPHA 混合检索", mode=MODE_HYBRID, top_k=2)
    top = hit["results"][0]
    assert top["doc_name"] == "hit.txt"
    assert top["match"] == "both", "a hit on both sides is labelled 'both'"


def _ingested_kb_without_engine(tmp_path, name):
    """A KB whose chunks exist but whose embedder has since become unavailable.

    This is the realistic "engine uninstalled / model deleted after indexing"
    state: ingestion needs an engine (an unchanged contract), but retrieval of
    already-indexed text must keep working lexically.
    """
    mgr = kb.KnowledgeBase(tmp_path, embedder=KeywordEmbedder())
    kb_id = mgr.create_kb(name, "fake/model", chunk_size=120, overlap=0)["id"]
    mgr.add_document(kb_id, "doc.txt", "关键词检索无需嵌入引擎")
    mgr._embedder = None
    return mgr, kb_id


def test_search_bm25_works_without_embedding_engine(tmp_path):
    mgr, kb_id = _ingested_kb_without_engine(tmp_path, "noengine")
    hit = mgr.search(kb_id, "关键词", mode=MODE_BM25)
    assert hit["results"], "BM25 must work with no embedding engine at all"
    assert hit["results"][0]["doc_name"] == "doc.txt"
    assert hit["results"][0]["match"] == "bm25"


def test_search_dense_without_embedding_engine_still_raises(tmp_path):
    """The historical contract: dense retrieval requires the engine."""
    mgr, kb_id = _ingested_kb_without_engine(tmp_path, "noengine2")
    with pytest.raises(kb.KnowledgeEngineMissing):
        mgr.search(kb_id, "关键词", mode=MODE_DENSE)


def test_search_hybrid_degrades_to_bm25_without_engine(tmp_path):
    mgr, kb_id = _ingested_kb_without_engine(tmp_path, "noengine3")
    hit = mgr.search(kb_id, "关键词", mode=MODE_HYBRID)
    assert hit["mode"] == MODE_HYBRID
    assert hit["results"], "hybrid must not fail when the embedder is missing"
    assert hit["warning"] and "嵌入引擎" in hit["warning"]
    assert hit["results"][0]["match"] == "bm25"


def test_search_rejects_unknown_mode(tmp_path):
    mgr, kb_id = _make_kb(tmp_path, embedder=KeywordEmbedder())
    mgr.add_document(kb_id, "doc.txt", "some content here")
    with pytest.raises(kb.KnowledgeParamError):
        mgr.search(kb_id, "content", mode="tfidf")


def test_search_empty_store_returns_all_modes(tmp_path):
    mgr, kb_id = _make_kb(tmp_path, embedder=KeywordEmbedder())
    for mode in (MODE_DENSE, MODE_BM25, MODE_HYBRID):
        assert mgr.search(kb_id, "anything", mode=mode)["results"] == []


def test_ask_propagates_mode(tmp_path):
    mgr, kb_id = _make_kb(tmp_path, embedder=KeywordEmbedder())
    mgr.add_document(kb_id, "cn.txt", "混合检索的说明文档")

    out = mgr.ask(kb_id, "混合检索", mode=MODE_BM25)
    assert out["mode"] == MODE_BM25
    assert out["results"], "ask must use the requested retrieval strategy"
    assert out["results"][0]["doc_name"] == "cn.txt"


def test_ask_stream_propagates_mode(tmp_path):
    mgr, kb_id = _make_kb(tmp_path, embedder=KeywordEmbedder())
    mgr.add_document(kb_id, "cn.txt", "混合检索的说明文档")

    events = list(mgr.ask_stream_sync(kb_id, "混合检索", mode=MODE_BM25))
    sources = next(e for e in events if e.get("type") == "sources")
    assert sources["mode"] == MODE_BM25
    assert sources["sources"][0]["doc_name"] == "cn.txt"


def test_retrieval_modes_contract():
    assert kb.RETRIEVAL_MODES == ("dense", "bm25", "hybrid")
    assert kb.DEFAULT_RETRIEVAL_MODE == "dense"


# --------------------------------------------------------------------------
# API layer
# --------------------------------------------------------------------------
def test_api_search_accepts_mode(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setattr(main, "APP_ROOT", Path(tmp_path))
    kb_obj = kb.KnowledgeBase(tmp_path, embedder=KeywordEmbedder())
    kb_id = kb_obj.create_kb("api", "fake/model", chunk_size=120, overlap=0)["id"]
    kb_obj.add_document(kb_id, "cn.txt", "混合检索的接口测试文档")

    client = TestClient(main.app)
    monkeypatch.setattr(client.app.state, "knowledge", kb_obj, raising=False)

    res = client.post(f"/api/knowledge/kbs/{kb_id}/search",
                      json={"query": "混合检索", "mode": "bm25"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "bm25"
    assert body["results"][0]["doc_name"] == "cn.txt"


def test_api_search_rejects_bad_mode(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setattr(main, "APP_ROOT", Path(tmp_path))
    kb_obj = kb.KnowledgeBase(tmp_path, embedder=KeywordEmbedder())
    kb_id = kb_obj.create_kb("api2", "fake/model")["id"]

    client = TestClient(main.app)
    monkeypatch.setattr(client.app.state, "knowledge", kb_obj, raising=False)

    res = client.post(f"/api/knowledge/kbs/{kb_id}/search",
                      json={"query": "x", "mode": "nope"})
    assert res.status_code == 400


def test_api_capabilities_advertises_modes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setattr(main, "APP_ROOT", Path(tmp_path))
    kb_obj = kb.KnowledgeBase(tmp_path, embedder=KeywordEmbedder())
    client = TestClient(main.app)
    monkeypatch.setattr(client.app.state, "knowledge", kb_obj, raising=False)

    body = client.get("/api/knowledge/capabilities").json()
    assert body["retrieval_modes"] == ["dense", "bm25", "hybrid"]
    assert body["default_retrieval_mode"] == "dense"
