"""HubRegistry — fan-out, quota, merge/rank, dedupe, cursors, degradation.

The registry owns the three adapters and is the single place that:

* allocates a per-source quota (curated first),
* fans out concurrently with ``asyncio.gather(return_exceptions=True)`` so one
  flaky upstream can never take the market page down,
* merges + ranks + de-dupes (§2.4, pure function so it is offline-testable),
* encodes/decodes the opaque cursor (§2.3),
* builds cross-source download candidates and the on-disk layout (§4.3).

Every network failure degrades to HTTP-200 semantics — the caller
(``main.py``) decides the status code, but this module never raises for a
remote hiccup.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from .base import (
    ALL_HUBS,
    HUB_CURATED,
    HUB_HF,
    HUB_MODELSCOPE,
    REMOTE_HUBS,
    PageResult,
    RemoteFile,
    RemoteModel,
    SearchSpec,
    SourceCursor,
    clamp_str,
    is_valid_repo,
    normalize_repo_key,
)
from .net import TTLCache
from .taxonomy import infer_engines, size_gb_from_bytes, total_size_bytes

log = logging.getLogger("kevrai.hub.registry")

#: Order used to hand out the integer remainder and to break rank ties.
_HUB_PRIORITY: dict[str, int] = {HUB_CURATED: 0, HUB_MODELSCOPE: 1, HUB_HF: 2}

_CURSOR_VERSION = 1
_MAX_CURSOR_BYTES = 2048


class BadCursor(ValueError):
    """Raised when a client-supplied cursor cannot be decoded."""

@dataclass
class MergedPage:
    """The result shape ``/api/hub/search`` serializes."""

    items: list[RemoteModel] = field(default_factory=list)
    next_cursor: str = ""
    has_more: bool = False
    counts: dict[str, int | None] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)
    degraded: bool = False


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested offline)
# ---------------------------------------------------------------------------


def allocate_quota(page_size: int, sources: Sequence[str]) -> dict[str, int]:
    """Split ``page_size`` across ``sources`` (curated gets the remainder).

    Base quota is ``floor(page_size / n)``; the integer remainder is handed out
    in :data:`_HUB_PRIORITY` order so the curated source is never starved
    (design §2.3).
    """
    enabled = [s for s in ALL_HUBS if s in (sources or ())]
    if not enabled:
        return {}
    n = len(enabled)
    size = max(1, int(page_size))
    base = size // n
    remainder = size - base * n
    ordered = sorted(enabled, key=lambda h: _HUB_PRIORITY.get(h, 99))
    quota = {h: base for h in enabled}
    for h in ordered[:remainder]:
        quota[h] += 1
    return quota


def _relevance_norm(model: RemoteModel, idx: int, bucket_len: int) -> float:
    """Normalized relevance in ``[0, 1]`` (curated uses its real score)."""
    if model.hub == HUB_CURATED:
        raw = model.hardware.get("_score") if isinstance(model.hardware, Mapping) else None
        try:
            r = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            r = 0.0
        # `search.py` scores are unbounded; squash into [0, 1) monotonically.
        return min(1.0, r / 40.0) if r > 0 else 0.0
    return 1.0 - (idx / max(1, bucket_len))


def _popularity_norm(downloads: Any) -> float:
    try:
        d = float(downloads or 0)
    except (TypeError, ValueError):
        d = 0.0
    if d <= 0:
        return 0.0
    return min(1.0, math.log10(1.0 + d) / 6.0)


def rank_score(model: RemoteModel, idx: int, bucket_len: int) -> float:
    """§2.4 ranking formula."""
    score = _relevance_norm(model, idx, bucket_len) * 60.0
    score += _popularity_norm(model.downloads) * 25.0
    if model.hub == HUB_CURATED:
        score += 40.0
    if model.trending:
        score += 5.0
    return score


def merge_rank(
    buckets: Mapping[str, Sequence[RemoteModel]],
    *,
    limit: int,
) -> list[RemoteModel]:
    """Merge per-source buckets into one deterministic ranked list (§2.4).

    Ties break on ``(hub priority, repo)`` so the output is reproducible and
    can be asserted exactly in tests.
    """
    scored: list[tuple[float, int, str, RemoteModel]] = []
    for hub, items in buckets.items():
        n = len(items or [])
        for idx, model in enumerate(items or []):
            s = rank_score(model, idx, n)
            scored.append((s, _HUB_PRIORITY.get(hub, 99),
                           normalize_repo_key(model.repo), model))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [t[3] for t in scored[: max(0, int(limit))]]


def dedupe(items: Sequence[RemoteModel]) -> list[RemoteModel]:
    """Collapse entries sharing a ``repo`` across hubs (§1.5).

    The first (highest ranked) entry wins; the other hubs are recorded in its
    ``also_on`` list so the UI can show "也在 HF / 魔搭".
    """
    by_repo: dict[str, RemoteModel] = {}
    order: list[str] = []
    for m in items:
        key = normalize_repo_key(m.repo)
        if not key:
            # No repo → cannot dedupe; keep as its own key.
            key = f"~{m.hub}:{m.id}"
        if key in by_repo:
            primary = by_repo[key]
            if m.hub != primary.hub and m.hub not in primary.also_on:
                primary.also_on.append(m.hub)
            continue
        by_repo[key] = m
        order.append(key)
    return [by_repo[k] for k in order]


# ---------------------------------------------------------------------------
# Cursor codec (§2.3)
# ---------------------------------------------------------------------------


def encode_cursor(sig: str, cursors: Mapping[str, SourceCursor]) -> str:
    """base64url-encode a per-source cursor map, tagged with the query sig."""
    payload = {
        "v": _CURSOR_VERSION,
        "sig": clamp_str(sig, 32),
        "s": {hub: cur.to_dict() for hub, cur in cursors.items()},
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str, *, expect_sig: str = "") -> dict[str, SourceCursor]:
    """Decode a cursor; raise :class:`BadCursor` on any malformed input.

    A ``sig`` mismatch is *not* an error — it silently restarts at page 1
    (design §2.3), because the user may have changed the query between pages.
    """
    if not token:
        return {}
    if len(token) > _MAX_CURSOR_BYTES:
        raise BadCursor("cursor too long")
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad)
    except Exception as e:
        raise BadCursor("cursor not base64url") from e
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise BadCursor("cursor not json") from e
    if not isinstance(payload, Mapping):
        raise BadCursor("cursor not an object")
    if int(payload.get("v") or 0) != _CURSOR_VERSION:
        return {}
    if expect_sig and str(payload.get("sig") or "") != expect_sig:
        return {}
    raw_s = payload.get("s")
    if not isinstance(raw_s, Mapping):
        return {}
    out: dict[str, SourceCursor] = {}
    for hub, cur in raw_s.items():
        if hub in ALL_HUBS:
            out[hub] = SourceCursor.from_dict(cur)
    return out


def _first_page_cursors(sources: Sequence[str]) -> dict[str, SourceCursor]:
    return {h: SourceCursor() for h in sources if h in ALL_HUBS}


# ---------------------------------------------------------------------------
# Cross-source download candidates (§4.2)
# ---------------------------------------------------------------------------


def cross_source_candidates(
    *,
    hub: str,
    repo: str,
    path: str,
    revision: str = "",
    also_on: Sequence[str] | None = None,
    extra_mirrors: Sequence[str] | None = None,
) -> list[str]:
    """Ordered, de-duplicated download candidates for one file (design §4.2).

    ModelScope first when the model lives there (CN-fast, Range-friendly),
    then every HuggingFace mirror for the equivalent path.
    """
    out: list[str] = []
    also = {str(h) for h in (also_on or []) if isinstance(h, str)}
    rev_ms = (revision or "master") or "master"
    rev_hf = (revision or "main") or "main"

    if hub == HUB_MODELSCOPE:
        out.append(f"https://modelscope.cn/models/{repo}/resolve/{rev_ms}/{quote(path, safe='/')}")
    if hub == HUB_HF or hub in also or (hub == HUB_MODELSCOPE and HUB_HF in also):
        try:
            from ..importer import hf_resolve_url
            from ..sources import expand_mirror_candidates

            primary = hf_resolve_url(repo, path, rev_hf)
            for c in expand_mirror_candidates(primary, list(extra_mirrors or [])):
                if c not in out:
                    out.append(c)
        except Exception as exc:  # noqa: BLE001 — mirror expansion is best-effort
            log.debug("expand mirrors failed for %s: %s", repo, exc)
    if hub != HUB_MODELSCOPE and HUB_MODELSCOPE in also:
        ms = f"https://modelscope.cn/models/{repo}/resolve/{rev_ms}/{quote(path, safe='/')}"
        if ms not in out:
            out.append(ms)
    return out


def _annotate_candidates(urls: Sequence[str]) -> list[dict[str, Any]]:
    """Attach lightweight source metadata to each candidate URL (v2.8.1).

    Best-effort and side-effect free: when a host matches no preset source we
    still return an entry with a synthesized ``type`` of ``""`` so callers can
    always zip URLs with metadata. Never raises.
    """
    out: list[dict[str, Any]] = []
    # Imported lazily: `sources_registry` pulls in the curated catalog, which is
    # heavy enough that paying for it on every registry import is wasteful.  The
    # callable is looked up once here and compared against None explicitly,
    # because mypy cannot model an import failing inside try/except.
    matched_host_patterns: Any = None
    try:
        from ..sources_registry import matched_host_patterns as _mhp
    except ImportError:  # pragma: no cover — defensive
        _mhp = None  # type: ignore[assignment]
    matched_host_patterns = _mhp
    for u in urls or []:
        host = ""
        try:
            from urllib.parse import urlparse
            host = (urlparse(u).hostname or "").lower()
        except Exception:
            host = ""
        ids = matched_host_patterns(host) if matched_host_patterns is not None else []
        out.append({"url": u, "host": host, "source_ids": ids})
    return out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class HubRegistry:
    """Orchestrates the curated + HF + ModelScope adapters."""

    def __init__(
        self,
        *,
        curated: Any,
        hf: Any | None = None,
        modelscope: Any | None = None,
        settings: Any | None = None,
        cache_ttl_s: int = 90,
    ) -> None:
        self._adapters: dict[str, Any] = {}
        self._adapters[HUB_CURATED] = curated
        if hf is not None:
            self._adapters[HUB_HF] = hf
        if modelscope is not None:
            self._adapters[HUB_MODELSCOPE] = modelscope
        self._settings = settings
        self._ttl = max(1, int(cache_ttl_s or 90))
        self._cache = TTLCache(max_entries=512)
        # In-flight 请求合并（single-flight）。TTLCache 只缓存**已完成**的
        # 结果，对同时到达的并发请求无效：实测 30 个并发相同查询会打出
        # 30 次上游请求，延迟从 1.6s 涨到 11.4s，且因拿到的分片不同而
        # 返回不一致的结果。这里让后到的请求等待同一个 task。
        self._inflight: dict[str, asyncio.Task[MergedPage]] = {}
        self._inflight_lock = asyncio.Lock()

    # -- introspection -----------------------------------------------------

    def adapter(self, hub: str) -> Any | None:
        return self._adapters.get(hub)

    def available(self) -> list[str]:
        return [h for h in ALL_HUBS if h in self._adapters]

    def enabled_sources(self) -> list[str]:
        """Sources allowed by settings (falls back to every wired adapter)."""
        configured = getattr(self._settings, "hub_enabled_sources", None)
        if isinstance(configured, list) and configured:
            allowed = [h for h in ALL_HUBS if h in configured]
        else:
            allowed = self.available()
        return [h for h in allowed if h in self._adapters]

    def health(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for hub in ALL_HUBS:
            ad = self._adapters.get(hub)
            if ad is None:
                continue
            try:
                out.append(ad.health())
            except Exception:  # pragma: no cover — defensive
                out.append({"hub": hub, "enabled": False, "online": False})
        return out

    # -- search ------------------------------------------------------------

    async def search(self, spec: SearchSpec, cursor_token: str = "") -> MergedPage:
        """Fan out to every enabled source, merge, rank, and paginate.

        Concurrent identical queries are coalesced onto a single upstream
        fan-out (see :attr:`_inflight`), so a burst of N requests costs one
        round-trip rather than N.
        """
        spec = spec.normalized()
        key = f"{spec.signature()}|{cursor_token}"

        async with self._inflight_lock:
            existing = self._inflight.get(key)
            if existing is not None and not existing.done():
                task = existing
                leader = False
            else:
                task = asyncio.ensure_future(self._search_uncached(spec, cursor_token))
                self._inflight[key] = task
                leader = True

        try:
            return await asyncio.shield(task)
        finally:
            # 只有 leader 负责清理，且必须等 task 真正结束后再释放槽位，
            # 否则后到的请求会看到一个已 done 但尚未被消费的 task。
            if leader:
                async with self._inflight_lock:
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)

    async def _search_uncached(self, spec: SearchSpec, cursor_token: str = "") -> MergedPage:
        """真正的扇出实现（合并层不关心它，见 :meth:`search`）。"""
        sources = [s for s in spec.sources if s in self._adapters]
        if not sources:
            sources = self.enabled_sources()
        if not sources:
            return MergedPage(items=[], next_cursor="", has_more=False,
                              counts={}, warnings=[], degraded=True)

        try:
            cursors = decode_cursor(cursor_token, expect_sig=spec.signature())
        except BadCursor:
            raise
        if not cursors:
            cursors = _first_page_cursors(sources)

        quota = allocate_quota(spec.page_size, sources)
        per_source_limit = max(quota.values()) if quota else spec.page_size

        results = await asyncio.gather(
            *(
                self._safe_search(hub, spec, cursors.get(hub, SourceCursor()), per_source_limit)
                for hub in sources
            ),
            return_exceptions=True,
        )

        buckets: dict[str, list[RemoteModel]] = {}
        warnings: list[dict[str, str]] = []
        counts: dict[str, int | None] = {}
        next_cursors: dict[str, SourceCursor] = {}

        for hub, res in zip(sources, results, strict=False):
            if isinstance(res, BaseException) or res is None:
                warnings.append({"hub": hub, "code": "exception",
                                 "message": f"{hub} 检索异常"})
                counts[hub] = None
                buckets[hub] = []
                continue
            assert isinstance(res, PageResult)
            buckets[hub] = list(res.items)
            counts[hub] = res.total
            if res.degraded:
                warnings.append({
                    "hub": hub,
                    "code": res.code or "degraded",
                    "message": res.warning or f"{hub} 已降级",
                })
            # Keep the source cursor for the next page even on an empty page.
            # `cursor_here` narrows the Optional in one place so the assignment
            # below cannot receive a None. (The previous form computed an
            # equivalent condition inline, but mypy could not see that the
            # trailing `or has_more_here` still implied a non-None cursor.)
            cap = quota.get(hub, per_source_limit)
            has_more_here = res.next is not None and len(res.items) <= cap
            cursor_here = res.next
            if cursor_here is not None and (
                len(res.items) > 0 or hub != HUB_CURATED or has_more_here
            ):
                next_cursors[hub] = cursor_here

        merged = merge_rank(buckets, limit=spec.page_size * 2)
        deduped = dedupe(merged)
        trimmed = deduped[: spec.page_size]
        has_more = bool(next_cursors) and len(deduped) > 0

        next_cursor = encode_cursor(spec.signature(), next_cursors) if has_more else ""
        degraded = bool(warnings) or (not any(buckets.get(h) for h in REMOTE_HUBS if h in self._adapters)
                                      and any(h in self._adapters for h in REMOTE_HUBS))
        return MergedPage(items=trimmed, next_cursor=next_cursor, has_more=has_more,
                          counts=counts, warnings=warnings, degraded=degraded)

    # -- availability probing ----------------------------------------------

    async def probe_sources(self, *, timeout_s: float = 12.0) -> dict[str, Any]:
        """One minimal request per remote source to see if it is reachable.

        The renderer uses this to **hide** a source the user's network cannot
        reach, instead of showing an empty market section. Design constraints:

        * one tiny request per source (page size 1),
        * a hard per-source timeout so a black-holing network cannot stall the
          app start-up,
        * never raises — an unreachable source is simply ``online: False``.

        On the timeout budget: measured live from a CN network, a **cold** first
        call takes 7.5–8.1 s end-to-end. The first HuggingFace mirror in the
        rotation (``hf-cdn.sufy.com``) answers **403 after ~45 s**, and the
        official site is unreachable from CN, so the adapter has to walk the
        whole rotation before it succeeds. A 2.5 s budget therefore reported
        *both* perfectly healthy sources as offline. 12 s covers the healthy
        path with room to spare while still bounding a black-holed network.
        """
        results: dict[str, Any] = {}
        remote = [h for h in REMOTE_HUBS if h in self._adapters]
        if not remote:
            return {"sources": results, "enabled": self.enabled_sources()}

        async def probe(hub: str) -> tuple[str, dict[str, Any]]:
            ad = self._adapters.get(hub)
            if ad is None:
                return hub, {"hub": hub, "online": False, "latency_ms": None,
                             "code": "disabled"}
            spec = SearchSpec(q="", page_size=1, sources=[hub])
            t0 = time.monotonic()
            try:
                res = await asyncio.wait_for(
                    self._safe_search(hub, spec, SourceCursor(), 1),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                return hub, {"hub": hub, "online": False,
                             "latency_ms": int(timeout_s * 1000), "code": "timeout"}
            except Exception as exc:  # noqa: BLE001 — probe must never raise
                log.debug("probe %s failed: %s", hub, exc)
                return hub, {"hub": hub, "online": False, "latency_ms": None,
                             "code": "network"}
            elapsed = int((time.monotonic() - t0) * 1000)
            # Only genuine reachability failures mark a source offline. An empty
            # or not-found page from a *reachable* source is still "online".
            online = res.code not in {"timeout", "network", "circuit_open",
                                      "http_error", "bad_json"}
            return hub, {
                "hub": hub,
                "online": online,
                "latency_ms": elapsed,
                "code": res.code or "ok",
                "display_name": getattr(ad, "display_name", hub),
            }

        pairs = await asyncio.gather(*(probe(h) for h in remote),
                                     return_exceptions=True)
        for item in pairs:
            if isinstance(item, BaseException):
                continue
            hub, info = item
            results[hub] = info
        return {"sources": results, "enabled": self.enabled_sources()}

    async def _safe_search(
        self,
        hub: str,
        spec: SearchSpec,
        cursor: SourceCursor,
        limit: int,
    ) -> PageResult:
        """Call one adapter's ``search`` with a per-source clamped page size."""
        ad = self._adapters.get(hub)
        if ad is None:
            return PageResult(items=[], next=None, total=0, degraded=True,
                              code="disabled", warning=f"{hub} 未启用")
        sub = SearchSpec(
            q=spec.q, category=spec.category, engine=spec.engine,
            license=spec.license, sort=spec.sort,
            page_size=max(1, min(100, int(limit))), sources=[hub],
        )
        try:
            res = await ad.search(sub, cursor or SourceCursor())
        except Exception as e:  # noqa: BLE001 — one source must never 500 the page
            return PageResult(items=[], next=None, total=None, degraded=True,
                              code="network", warning=f"{hub} 检索异常：{str(e)[:120]}")
        if isinstance(res, PageResult):
            return res
        return PageResult(items=[], next=None, total=None, degraded=True,
                          code="network", warning=f"{hub} 返回异常类型")

    # -- detail / files ----------------------------------------------------

    async def detail(self, hub: str, repo: str) -> RemoteModel:
        """Detail for one model (raises LookupError / RuntimeError to caller)."""
        ad = self._adapters.get(hub)
        if ad is None:
            raise LookupError(f"unknown hub: {hub}")
        if not is_valid_repo(repo):
            raise ValueError(f"bad repo: {repo!r}")
        model = await ad.detail(repo)
        await self._annotate_also_on(model)
        return model

    async def files(self, hub: str, repo: str, revision: str = "") -> dict[str, Any]:
        """File listing + engine inference for one model (§1.7 / §4.1)."""
        ad = self._adapters.get(hub)
        if ad is None:
            raise LookupError(f"unknown hub: {hub}")
        if not is_valid_repo(repo):
            raise ValueError(f"bad repo: {repo!r}")
        files: list[RemoteFile] = await ad.files(repo, revision)
        total = total_size_bytes(files)
        size_gb = size_gb_from_bytes(total)
        category = ""
        try:
            model = await ad.detail(repo)
            category = model.category
        except Exception:
            category = ""
        engines, import_only = infer_engines(
            [f.path for f in files], task="", repo=repo, category=category,
        )
        also_on = await self._also_on_for(repo)
        out_files = []
        for f in files:
            cands = cross_source_candidates(
                hub=hub, repo=repo, path=f.path,
                revision=revision or f"{''}",
                also_on=also_on,
                extra_mirrors=getattr(self._settings, "extra_model_mirrors", []),
            )
            d = f.to_dict()
            d["candidates"] = cands
            # v2.8.1 — annotate each candidate with its source metadata so the
            # UI can show source type / weight without a second round-trip.
            d["candidate_meta"] = _annotate_candidates(cands)
            out_files.append(d)
        return {
            "files": out_files,
            "count": len(out_files),
            "total_size": total,
            "size_gb": size_gb,
            "engines": engines,
            "import_only": import_only,
            "revision": revision or "",
            "also_on": also_on,
        }

    # -- helpers -----------------------------------------------------------

    async def _also_on_for(self, repo: str) -> list[str]:
        """Which other source could also serve ``repo`` (best-effort, cached)."""
        key = f"also_on:{normalize_repo_key(repo)}"
        cached = self._cache.get(key)
        if cached is not None:
            return list(cached)
        found: list[str] = []
        # Only a cheap presence check for the *other* remote source.
        for hub in REMOTE_HUBS:
            ad = self._adapters.get(hub)
            if ad is None:
                continue
            try:
                m = await ad.detail(repo)
                if m is not None:
                    found.append(hub)
            except Exception as exc:  # noqa: BLE001 — this hub is unreachable; try next
                log.debug("also_on: hub %s detail failed: %s", hub, exc)
                continue
        self._cache.set(key, found, ttl=300.0)
        return found

    async def _annotate_also_on(self, model: RemoteModel) -> None:
        try:
            found = await self._also_on_for(model.repo)
        except Exception:
            return
        for hub in found:
            if hub != model.hub and hub not in model.also_on:
                model.also_on.append(hub)

    async def aclose(self) -> None:
        """Release every adapter's HTTP client (lifespan shutdown calls this)."""
        for hub in list(self._adapters):
            ad = self._adapters.get(hub)
            if ad is None:
                continue
            closed = getattr(ad, "aclose", None)
            if closed is None:
                continue
            try:
                await closed()
            except Exception as exc:  # noqa: BLE001 — keep closing remaining adapters
                log.debug("aclose: adapter %s failed: %s", hub, exc)
                continue


__all__ = [
    "BadCursor",
    "HubRegistry",
    "MergedPage",
    "allocate_quota",
    "cross_source_candidates",
    "decode_cursor",
    "dedupe",
    "encode_cursor",
    "merge_rank",
    "rank_score",
]
