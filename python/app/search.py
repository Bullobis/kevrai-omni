"""Super search engine for the model catalog.

Provides weighted, typo-tolerant full-text search over model entries with:

* Field-weighted scoring (name > id > tags > engine > category > description > repo)
* Exact / prefix / substring / token / subsequence / edit-distance matching
* CJK bigram matching so Chinese queries (e.g. ``视频``, ``语音``) match well
* Match highlighting with character offsets (renderer wraps ``<mark>``)
* Faceted aggregation (engines, licenses, categories, size buckets)
* Multiple sort orders (relevance / name / size / trending)
* "Did you mean" suggestions (closest model name within edit distance 2)
* Recent-search history persisted under the cache root
* LRU memoization of the parsed corpus for fast repeat queries

The module is dependency-free (stdlib only) and never touches the network,
so it is safe to call from the request path.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

# A "token" is a maximal run of latin letters/digits or a single CJK ideograph.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")


def _normalize(text: str) -> str:
    return (text or "").lower().strip()


def _cjk_bigrams(text: str) -> list[str]:
    """Return adjacent CJK bigrams (and unigrams) for a string.

    For ``"视频生成"`` this yields ``["视频","频生","生成"]`` plus unigrams,
    which lets a 2-char Chinese query score highly against a Chinese field.
    """
    chars = [c for c in text if "\u4e00" <= c <= "\u9fff"]
    grams: list[str] = []
    for i in range(len(chars)):
        grams.append(chars[i])
        if i + 1 < len(chars):
            grams.append(chars[i] + chars[i + 1])
    return grams


def _tokens(text: str) -> list[str]:
    """Tokenize into latin words/digits AND CJK unigrams + bigrams.

    Including bigrams lets a 2-char Chinese query (e.g. ``视频``) match as a
    single token instead of two unrelated single characters.
    """
    base = [m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")]
    out: list[str] = []
    cjk_run: list[str] = []
    for t in base:
        if len(t) == 1 and "\u4e00" <= t <= "\u9fff":
            cjk_run.append(t)
        else:
            if cjk_run:
                out.extend(_cjk_bigrams("".join(cjk_run)))
                cjk_run = []
            out.append(t)
    if cjk_run:
        out.extend(_cjk_bigrams("".join(cjk_run)))
    return out


def _field_text(m: dict[str, Any]) -> dict[str, str]:
    """Flatten a model record into searchable field strings."""
    engines = m.get("engine") or []
    if isinstance(engines, str):
        engines = [engines]
    tags = m.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    modality = m.get("modality") or {}
    mod_notes = modality.get("notes", "") if isinstance(modality, dict) else ""
    return {
        "name": str(m.get("name", "")),
        "id": str(m.get("id", "")),
        "tags": " ".join(str(t) for t in tags),
        "engine": " ".join(str(e) for e in engines),
        "category": str(m.get("category", "")),
        "license": str(m.get("license", "")),
        "repo": str(m.get("repo", "")),
        "description": " ".join([
            str(m.get("description", "")),
            mod_notes,
            str(m.get("gguf_repo", "")),
            str(m.get("mnn_repo", "")),
        ]),
    }


# ---------------------------------------------------------------------------
# Edit distance (bounded, early-exit)
# ---------------------------------------------------------------------------

def _edit_distance(a: str, b: str, cap: int = 2) -> int:
    """Levenshtein distance capped at ``cap+1`` (returns cap+1 if exceeded)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > cap:
        return cap + 1
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        best = cur[0]
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if cur[j] < best:
                best = cur[j]
        if best > cap:
            return cap + 1
        prev = cur
    return prev[lb]


def _subsequence(q: str, text: str) -> bool:
    """True if all chars of q appear in text in order (fuzzy substring)."""
    it = iter(text)
    return all(c in it for c in q)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

FIELD_WEIGHTS: dict[str, int] = {
    "name": 12,
    "id": 9,
    "tags": 7,
    "engine": 5,
    "category": 5,
    "license": 2,
    "repo": 3,
    "description": 3,
}

# Bonus multipliers applied on top of the field weight.
_EXACT_BONUS = 6.0
_PREFIX_BONUS = 3.0
_SUBSTRING_BONUS = 2.0
_SUBSEQ_BONUS = 1.1
_CJK_BIGRAM_BONUS = 2.5
_TYPO_BONUS = 0.6


@dataclass
class Match:
    field: str
    start: int
    end: int
    value: str


@dataclass
class ScoredModel:
    model: dict[str, Any]
    score: float
    matches: list[Match] = field(default_factory=list)
    matched_fields: list[str] = field(default_factory=list)


def _score_field(field: str, text: str, qnorm: str, qtokens: list[str]) -> tuple[float, list[Match], set[str]]:
    """Score one field for one (possibly multi-word) query.

    Returns (score, highlights, set_of_query_tokens_matched_in_this_field).
    Whole-query strategies (exact/prefix/substring/subsequence/typo) count as
    matching every token. Per-token strategies count only the tokens that hit.
    """
    weight = FIELD_WEIGHTS.get(field, 1)
    tn = _normalize(text)
    if not tn or not qnorm:
        return 0.0, [], set()

    score = 0.0
    matches: list[Match] = []
    seen_spans: set[tuple[int, int]] = set()
    matched_tokens: set[str] = set()

    def _add_match(start: int, end: int) -> None:
        if 0 <= start < end and (start, end) not in seen_spans:
            seen_spans.add((start, end))
            matches.append(Match(field=field, start=start, end=end, value=text[start:end]))

    whole_hit = False
    # ---- whole-query strategies (best single strategy wins for the full q) --
    if qnorm == tn:
        score += weight * _EXACT_BONUS
        _add_match(0, len(text))
        whole_hit = True
    elif tn.startswith(qnorm):
        score += weight * _PREFIX_BONUS
        _add_match(0, len(qnorm))
        whole_hit = True
    elif qnorm in tn:
        idx = tn.find(qnorm)
        score += weight * _SUBSTRING_BONUS
        _add_match(idx, idx + len(qnorm))
        whole_hit = True
    elif len(qnorm) >= 2 and _subsequence(qnorm, tn):
        score += weight * _SUBSEQ_BONUS
        pos = 0
        for ch in qnorm:
            j = tn.find(ch, pos)
            if j >= 0:
                _add_match(j, j + 1)
                pos = j + 1
        whole_hit = True
    else:
        # typo tolerance: only for short single-word latin queries
        # (CJK uses bigram matching instead; edit distance is meaningless for
        # single ideographs and causes false positives)
        if (
            len(qnorm) <= 24
            and " " not in qnorm
            and len(qnorm) >= 3
            and all(ord(c) < 0x2E80 for c in qnorm)
        ):
            for tok in _tokens(tn):
                if (
                    len(tok) >= 3
                    and all(ord(c) < 0x2E80 for c in tok)
                    and abs(len(tok) - len(qnorm)) <= 2
                    and _edit_distance(qnorm, tok, cap=2) <= 2
                ):
                    score += weight * _TYPO_BONUS
                    m = re.search(re.escape(tok), text, re.IGNORECASE)
                    if m:
                        _add_match(m.start(), m.end())
                    whole_hit = True
                    break

    if whole_hit:
        matched_tokens.update(qtokens)
        return score, matches, matched_tokens

    # ---- per-token strategies ------------------------------------------------
    # A token that hits this field contributes its score; tokens that miss
    # here may hit another field of the same model (checked by the caller).
    for qt in qtokens:
        hit = False
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(qt) + r"(?![A-Za-z0-9])", tn):
            score += weight * 1.5
            hit = True
            for m in re.finditer(re.escape(qt), tn):
                _add_match(m.start(), m.end())
        elif qt in tn:
            score += weight * 0.8
            hit = True
            idx = tn.find(qt)
            _add_match(idx, idx + len(qt))
        elif len(qt) >= 2 and _subsequence(qt, tn):
            score += weight * 0.4
            hit = True
        # CJK bigram match
        if not hit and len(qt) >= 2 and all("\u4e00" <= c <= "\u9fff" for c in qt):
            if qt in _cjk_bigrams(tn) or qt in tn:
                score += weight * _CJK_BIGRAM_BONUS
                hit = True
                idx = tn.find(qt)
                if idx >= 0:
                    _add_match(idx, idx + len(qt))
        if hit:
            matched_tokens.add(qt)

    return score, matches, matched_tokens


# ---------------------------------------------------------------------------
# Corpus (parsed once, memoized)
# ---------------------------------------------------------------------------

class Corpus:
    """Parsed, searchable view of a list of model dicts."""

    def __init__(self, models: list[dict[str, Any]]):
        self.models = models
        self.fields: list[dict[str, str]] = [_field_text(m) for m in models]
        self._name_tokens: list[str] = []
        for f in self.fields:
            self._name_tokens.extend(_tokens(f["name"]))
        self._name_vocab = sorted(set(self._name_tokens))

    def suggest(self, q: str, limit: int = 5) -> list[str]:
        """Return closest model-name tokens (did-you-mean)."""
        qn = _normalize(q).split()
        if not qn:
            return []
        target = qn[-1]
        if len(target) < 2 or len(target) > 24:
            return []
        scored: list[tuple[int, str]] = []
        for tok in self._name_vocab:
            if abs(len(tok) - len(target)) > 2:
                continue
            d = _edit_distance(target, tok, cap=2)
            if d <= 2 and d > 0:
                scored.append((d, tok))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [t for _, t in scored[:limit]]


_CORPUS_LOCK = threading.Lock()
_CORPUS_CACHE: dict[int, Corpus] = {}


def get_corpus(models: list[dict[str, Any]]) -> Corpus:
    """Return a memoized Corpus keyed by the object identity of the list."""
    key = id(models)
    with _CORPUS_LOCK:
        c = _CORPUS_CACHE.get(key)
        if c is None:
            c = Corpus(models)
            _CORPUS_CACHE[key] = c
            # bound the cache
            if len(_CORPUS_CACHE) > 8:
                _CORPUS_CACHE.pop(next(iter(_CORPUS_CACHE)))
        return c


# ---------------------------------------------------------------------------
# Facets
# ---------------------------------------------------------------------------

_SIZE_BUCKETS = [
    (0, 5, "< 5 GB"),
    (5, 15, "5–15 GB"),
    (15, 40, "15–40 GB"),
    (40, 100, "40–100 GB"),
    (100, float("inf"), "≥ 100 GB"),
]


def _size_bucket(gb: float) -> str:
    for lo, hi, label in _SIZE_BUCKETS:
        if lo <= gb < hi:
            return label
    return "未知"


def compute_facets(models: Iterable[dict[str, Any]]) -> dict[str, Any]:
    engines: Counter[str] = Counter()
    licenses: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    for m in models:
        for e in (m.get("engine") or []):
            engines[str(e)] += 1
        if m.get("license"):
            licenses[str(m["license"])] += 1
        if m.get("category"):
            categories[str(m["category"])] += 1
        sizes[_size_bucket(float(m.get("size_gb") or 0))] += 1
    return {
        "engines": [{"value": k, "count": v} for k, v in engines.most_common(20)],
        "licenses": [{"value": k, "count": v} for k, v in licenses.most_common(20)],
        "categories": [{"value": k, "count": v} for k, v in categories.most_common(20)],
        "sizes": [{"value": label, "count": sizes.get(label, 0)} for _, _, label in _SIZE_BUCKETS],
    }


# ---------------------------------------------------------------------------
# Recent searches
# ---------------------------------------------------------------------------

def _history_path() -> Path:
    from .settings import default_cache_root
    return default_cache_root() / "search_history.json"


def recent_searches(limit: int = 8) -> list[str]:
    p = _history_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data[:limit] if isinstance(x, str)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def push_recent(q: str, limit: int = 12) -> None:
    q = (q or "").strip()
    if not q or len(q) > 200:
        return
    p = _history_path()
    items = recent_searches(limit=50)
    items = [x for x in items if x.lower() != q.lower()]
    items.insert(0, q)
    items = items[:limit]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def clear_recent() -> None:
    p = _history_path()
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Public search API
# ---------------------------------------------------------------------------

@dataclass
class SearchQuery:
    q: str = ""
    category: str = ""
    engine: str = ""
    license: str = ""
    size_bucket: str = ""
    trending_only: bool = False
    sort: str = "relevance"  # relevance | name_asc | size_desc | size_asc | trending
    page: int = 1
    page_size: int = 50


def _passes_filters(m: dict[str, Any], sq: SearchQuery) -> bool:
    if sq.category and m.get("category") != sq.category:
        return False
    if sq.engine:
        engines = m.get("engine") or []
        if isinstance(engines, str):
            engines = [engines]
        if sq.engine not in [str(e) for e in engines]:
            return False
    if sq.license and m.get("license") != sq.license:
        return False
    if sq.size_bucket and _size_bucket(float(m.get("size_gb") or 0)) != sq.size_bucket:
        return False
    if sq.trending_only and not m.get("trending"):
        return False
    return True


def search(models: list[dict[str, Any]], sq: SearchQuery) -> dict[str, Any]:
    """Execute a search and return a dict ready for JSON serialization."""
    t0 = time.perf_counter()
    corpus = get_corpus(models)
    qn = _normalize(sq.q)
    qtokens = _tokens(qn) if qn else []

    scored: list[ScoredModel] = []
    for idx, m in enumerate(models):
        if not _passes_filters(m, sq):
            continue
        if not qn:
            # No query: every surviving model matches with score 0; sorting decides.
            scored.append(ScoredModel(model=m, score=0.0))
            continue
        total = 0.0
        all_matches: list[Match] = []
        matched_fields: list[str] = []
        all_matched_tokens: set[str] = set()
        fields = corpus.fields[idx]
        for fname, ftext in fields.items():
            s, matches, tok_hits = _score_field(fname, ftext, qn, qtokens)
            if s > 0:
                total += s
                all_matches.extend(matches)
                matched_fields.append(fname)
            all_matched_tokens.update(tok_hits)
        # Every query token must match in at least one field of this model.
        if qtokens and not all_matched_tokens.issuperset(qtokens):
            continue
        if total > 0:
            # Trending models get a small nudge so ties break toward popular items.
            if m.get("trending"):
                total += 0.5
            scored.append(ScoredModel(model=m, score=total, matches=all_matches,
                                      matched_fields=matched_fields))

    # ---- sort ----
    if sq.sort == "name_asc":
        scored.sort(key=lambda s: str(s.model.get("name", "")).lower())
    elif sq.sort == "size_desc":
        scored.sort(key=lambda s: float(s.model.get("size_gb") or 0), reverse=True)
    elif sq.sort == "size_asc":
        scored.sort(key=lambda s: float(s.model.get("size_gb") or 0))
    elif sq.sort == "trending":
        scored.sort(key=lambda s: (bool(s.model.get("trending")), s.score), reverse=True)
    else:  # relevance
        scored.sort(key=lambda s: s.score, reverse=True)

    total_hits = len(scored)
    page = max(1, int(sq.page or 1))
    page_size = max(1, min(int(sq.page_size or 50), 200))
    page_items = scored[(page - 1) * page_size: page * page_size]

    # ---- serialize ----
    items = []
    for s in page_items:
        d = dict(s.model)
        d["_score"] = round(s.score, 3)
        d["_matched_fields"] = s.matched_fields
        d["_highlights"] = [
            {"field": mt.field, "start": mt.start, "end": mt.end, "value": mt.value}
            for mt in s.matches[:24]
        ]
        items.append(d)

    suggestions: list[str] = []
    if qn and total_hits == 0:
        suggestions = corpus.suggest(sq.q)

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "query": sq.q,
        "count": total_hits,
        "page": page,
        "page_size": page_size,
        "items": items,
        "facets": compute_facets(models),
        "suggestions": suggestions,
        "recent": recent_searches() if not qn else [],
        "elapsed_ms": elapsed_ms,
    }
