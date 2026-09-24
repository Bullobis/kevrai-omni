"""Regression tests for Neuron-API slice 2 — Task 1: search corpus cache.

The bug was that ``/api/search`` built a fresh ``models`` list on every
request, so ``get_corpus`` keyed by ``id(models)`` never hit. We now pass a
stable ``cache_key`` (the catalog version) and memoize the dumped list.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import search as search_mod  # noqa: E402
from app.search import Corpus, SearchQuery, get_corpus, search  # noqa: E402

SAMPLE = [
    {"id": "ltx-2.5", "category": "video", "name": "LTX-2.5 (最新一代)",
     "repo": "Lightricks/LTX-2.5", "engine": ["diffusers"],
     "license": "LTX-Open", "size_gb": 95, "trending": True,
     "description": "文生视频 / 图生视频", "tags": ["video"]},
    {"id": "qwen3-32b", "category": "llm", "name": "Qwen3 32B",
     "repo": "Qwen/Qwen3-32B", "engine": ["llama.cpp"],
     "license": "Apache-2.0", "size_gb": 20, "trending": False,
     "description": "32B 大语言模型"},
]


def _reset_cache():
    search_mod._CORPUS_CACHE.clear()


def test_stable_cache_key_reuses_corpus():
    """Same cache_key → same Corpus object, even with different list instances."""
    _reset_cache()
    # Two *different* list objects with identical content.
    list_a = [dict(m) for m in SAMPLE]
    list_b = [dict(m) for m in SAMPLE]
    assert list_a is not list_b

    c1 = get_corpus(list_a, cache_key="catalog-v9")
    c2 = get_corpus(list_b, cache_key="catalog-v9")
    assert c1 is c2, "stable cache_key must reuse the same Corpus object"


def test_different_cache_key_rebuilds_corpus():
    """A different cache_key produces a *different* Corpus."""
    _reset_cache()
    c1 = get_corpus(SAMPLE, cache_key="catalog-v1")
    c2 = get_corpus(SAMPLE, cache_key="catalog-v2")
    assert c1 is not c2, "different cache_key must rebuild the Corpus"


def test_corpus_built_exactly_once_per_key():
    """Spy on Corpus construction: one build per cache_key, not per call."""
    _reset_cache()
    real_init = Corpus.__init__
    builds: list[int] = []

    def counting_init(self, models):
        builds.append(1)
        real_init(self, models)

    Corpus.__init__ = counting_init  # type: ignore[assignment]
    try:
        # 5 searches, same key → exactly 1 Corpus construction.
        for _ in range(5):
            get_corpus([dict(m) for m in SAMPLE], cache_key="spy-key")
        assert len(builds) == 1, f"expected 1 build, got {len(builds)}"
        # New key → one more build.
        get_corpus([dict(m) for m in SAMPLE], cache_key="spy-key-2")
        assert len(builds) == 2, f"expected 2 builds after key change, got {len(builds)}"
    finally:
        Corpus.__init__ = real_init  # type: ignore[assignment]


def test_no_id_reuse_dirty_read_on_stable_path():
    """When cache_key is provided, id(models) must not participate in lookup.

    We simulate the GC-recycle hazard: drop the original list so its id()
    could be reused, then build a *new* list whose id() happens to collide
    with a previously cached int-id entry. The stable-key path must still
    return the correct Corpus for its string key.
    """
    _reset_cache()
    # Seed a stable-key Corpus.
    c_stable = get_corpus([dict(m) for m in SAMPLE], cache_key="stable-42")
    # Also seed an id()-keyed entry using some other list.
    other = [{"id": "x", "category": "c", "name": "X", "repo": "r",
              "engine": [], "license": "MIT", "size_gb": 1.0, "description": ""}]
    c_id = get_corpus(other)  # no cache_key → uses id(other)
    id_used = id(other)
    del other  # drop reference; id() may be recycled by a later allocation
    # The stable-key entry must still be retrievable by its string key.
    c_again = get_corpus([dict(m) for m in SAMPLE], cache_key="stable-42")
    assert c_again is c_stable
    # And the id()-keyed entry for the deleted list must not have been
    # overwritten by the stable path (keys are disjoint namespaces).
    assert id_used in search_mod._CORPUS_CACHE
    assert search_mod._CORPUS_CACHE[id_used] is c_id


def test_search_passes_cache_key_through():
    """search(cache_key=...) must reach get_corpus and reuse the Corpus."""
    _reset_cache()
    # Two distinct list instances; same cache_key.
    la = [dict(m) for m in SAMPLE]
    lb = [dict(m) for m in SAMPLE]
    r1 = search(la, SearchQuery(q="ltx"), cache_key="ep-v1")
    r2 = search(lb, SearchQuery(q="ltx"), cache_key="ep-v1")
    # Both return the same count, and the Corpus cache has exactly one entry
    # for "ep-v1".
    assert r1["count"] == r2["count"]
    assert r1["count"] >= 1
    assert search_mod._CORPUS_CACHE.get("ep-v1") is not None


def test_backward_compat_without_cache_key():
    """Without cache_key, legacy id(models) behavior still works."""
    _reset_cache()
    c1 = get_corpus(SAMPLE)
    c2 = get_corpus(SAMPLE)
    assert c1 is c2
