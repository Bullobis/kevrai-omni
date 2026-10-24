"""Hybrid retrieval core: BM25 (lexical) + dense vectors, fused with RRF.

A deliberately dependency-light module (standard library only, no ``rank_bm25``
/ ``jieba`` / ``faiss``). It exists because the dense-only retrieval in
:mod:`app.knowledge_base` cannot match exact terms — product codes, error
strings, Chinese keywords — that a user may quote verbatim.

Design
------
* :func:`tokenize` is CJK-aware: ASCII runs become words, and CJK runs are
  expanded into unigrams **and** bigrams. Bigrams give Chinese the phrase
  signal a whitespace tokenizer gets for free, while unigrams keep recall high
  for single-character queries.

* :class:`BM25` implements Okapi BM25 with the standard ``k1``/``b``
  parameters and a smoothed IDF. It is fitted on a corpus of chunk dicts and
  queried by text; both accept the same tokenizer.

* :func:`rrf_fuse` merges several ranked lists with Reciprocal Rank Fusion
  (``score = Σ 1/(k + rank)``). RRF is scale-free, so raw BM25 scores (unbounded)
  and cosine scores (0..1) can be combined without calibration.

* `dense` remains the default everywhere: hybrid retrieval is opt-in and never
  changes existing rankings unless explicitly requested.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any

# ASCII word / number runs, and CJK ideographs (CJK Unified Ideographs + Ext A +
# compatibility ideographs). Everything else is a separator.
_ASCII_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# Contiguous CJK runs — used to build bigrams without crossing separators.
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")

# Okapi BM25 defaults — the widely used values from the original paper.
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75
# RRF damping constant (Cormack et al. 2009); 60 is the canonical value.
DEFAULT_RRF_K = 60
# Lexical candidates pulled into the hybrid fusion.
HYBRID_CANDIDATE_POOL = 50

# Retrieval modes accepted by the knowledge base.
MODE_DENSE = "dense"
MODE_BM25 = "bm25"
MODE_HYBRID = "hybrid"
RETRIEVAL_MODES = (MODE_DENSE, MODE_BM25, MODE_HYBRID)


def tokenize(text: str, *, bigrams: bool = True) -> list[str]:
    """Split ``text`` into lowercase search tokens, CJK-aware.

    * ASCII runs (``Wi-Fi``, ``gpt2``, ``404``) become one token each, and a
      digit-bearing token is additionally split on non-alphanumerics so
      ``v2.8.0`` also yields ``v2``, ``8``, ``0``.
    * CJK runs become unigrams plus, when ``bigrams`` is set, adjacent
      bigrams — ``知识库`` yields ``知``, ``识``, ``库``, ``知识``, ``识库``.
    """
    if not text:
        return []
    low = str(text).lower()
    tokens: list[str] = []

    for run in _ASCII_RE.findall(low):
        tokens.append(run)
        # Split alphanumeric boundaries so "v2.8.0" is reachable as "8".
        parts = [p for p in re.split(r"[^a-z0-9]+", run) if p]
        if len(parts) > 1:
            tokens.extend(parts)

    for run in _CJK_RE.findall(low):
        tokens.append(run)

    # Build bigrams from *contiguous* CJK runs of the original string, so
    # "知识 库" (space inside) does not create a cross-space bigram.
    if bigrams:
        for segment in _CJK_RUN_RE.findall(low):
            if len(segment) < 2:
                continue
            tokens.extend(segment[i:i + 2] for i in range(len(segment) - 1))
    return tokens


class BM25:
    """Okapi BM25 over an in-memory corpus.

    The corpus is a sequence of records; :meth:`fit` pre-computes per-record
    term frequencies, lengths and document frequencies. Retrieval is a pure
    in-memory scan — fine for the corpus sizes a local knowledge base reaches
    (thousands of chunks), and it keeps this module dependency-free.
    """

    def __init__(self, k1: float = DEFAULT_K1, b: float = DEFAULT_B,
                 tokenizer: Callable[[str], list[str]] | None = None) -> None:
        if k1 < 0:
            raise ValueError("k1 must be non-negative")
        if not 0.0 <= b <= 1.0:
            raise ValueError("b must be within [0, 1]")
        self.k1 = float(k1)
        self.b = float(b)
        self._tokenizer = tokenizer or tokenize
        self._tf: list[dict[str, int]] = []
        self._lens: list[int] = []
        self._df: dict[str, int] = {}
        self._avgdl = 0.0

    # -- fitting ----------------------------------------------------------
    def fit(self, texts: Iterable[str]) -> BM25:
        """Index ``texts``, replacing any previously fitted corpus."""
        self._tf = []
        self._lens = []
        self._df = {}
        for text in texts:
            toks = self._tokenizer(text or "")
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            self._tf.append(tf)
            self._lens.append(len(toks))
            for t in tf:
                self._df[t] = self._df.get(t, 0) + 1
        total = sum(self._lens)
        self._avgdl = (total / len(self._lens)) if self._lens else 0.0
        return self

    @property
    def size(self) -> int:
        """Number of indexed records."""
        return len(self._tf)

    # -- scoring ----------------------------------------------------------
    def idf(self, term: str) -> float:
        """Smoothed inverse document frequency (always positive)."""
        n = len(self._tf)
        if n == 0:
            return 0.0
        df = self._df.get(term, 0)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def score_terms(self, terms: Sequence[str]) -> list[float]:
        """Return the BM25 score of every indexed record for ``terms``."""
        n = len(self._tf)
        if n == 0:
            return []
        avgdl = self._avgdl or 1.0
        # Weight each query term by IDF once instead of per record.
        weighted: list[tuple[str, float]] = []
        seen: set[str] = set()
        for t in terms:
            if t in seen:
                continue
            seen.add(t)
            weighted.append((t, self.idf(t)))

        scores = [0.0] * n
        for term, idf in weighted:
            if idf <= 0.0:
                continue
            for i, tf in enumerate(self._tf):
                f = tf.get(term)
                if not f:
                    continue
                dl = self._lens[i]
                denom = f + self.k1 * (1.0 - self.b + self.b * dl / avgdl)
                if denom <= 0.0:
                    continue
                scores[i] += idf * (f * (self.k1 + 1.0)) / denom
        return scores

    def search(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        """Rank the corpus for ``query``; returns ``(index, score)`` pairs."""
        terms = self._tokenizer(query or "")
        if not terms:
            return []
        scores = self.score_terms(terms)
        ranked = sorted(
            ((i, s) for i, s in enumerate(scores) if s > 0.0),
            key=lambda t: (-t[1], t[0]),
        )
        return ranked[:max(1, int(top_k))]


def rrf_fuse(rankings: Sequence[Sequence[tuple[Any, float]]],
             k: int = DEFAULT_RRF_K) -> list[tuple[Any, float]]:
    """Fuse ranked lists with Reciprocal Rank Fusion.

    Each ``ranking`` is a sequence of ``(key, score)`` ordered best-first; only
    the *rank* matters. Keys present in several lists accumulate weight, which
    is what lets a chunk that is both lexically and semantically relevant win.
    """
    fused: dict[Any, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            key = item[0]
            fused[key] = fused.get(key, 0.0) + 1.0 / (int(k) + rank)
    # Ties broken by key for deterministic output.
    return sorted(fused.items(), key=lambda kv: (-kv[1], str(kv[0])))


def hybrid_rank(
    records: Sequence[dict[str, Any]],
    query: str,
    *,
    k1: float = DEFAULT_K1,
    b: float = DEFAULT_B,
    rrf_k: int = DEFAULT_RRF_K,
    candidate_pool: int = 50,
) -> list[tuple[int, float]]:
    """BM25-only ranking over ``records`` (each exposing ``text``).

    Returns ``(record_index, bm25_score)`` best-first. Exposed separately so
    callers can use lexical retrieval without a dense index.
    """
    bm25 = BM25(k1=k1, b=b).fit([str(r.get("text") or "") for r in records])
    return bm25.search(query, top_k=max(1, min(candidate_pool, len(records)) or 1))


def fuse_dense_bm25(
    dense: Sequence[tuple[int, float]],
    lexical: Sequence[tuple[int, float]],
    k: int = DEFAULT_RRF_K,
) -> list[tuple[int, float]]:
    """Fuse a dense ranking and a BM25 ranking into one RRF ranking."""
    return rrf_fuse([dense, lexical], k=k)


__all__ = [
    "BM25",
    "DEFAULT_B",
    "DEFAULT_K1",
    "DEFAULT_RRF_K",
    "HYBRID_CANDIDATE_POOL",
    "MODE_BM25",
    "MODE_DENSE",
    "MODE_HYBRID",
    "RETRIEVAL_MODES",
    "fuse_dense_bm25",
    "hybrid_rank",
    "rrf_fuse",
    "tokenize",
]
