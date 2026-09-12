"""Source scheduler — graded scoring + EWMA + circuit-breaker + probe cache.

Design reference: ``docs/DESIGN_V281_SOURCES.md`` §2.4.

This module is the **pure decision layer**. It owns no HTTP client; probing is
delegated to :func:`app.sources.measure_sources`, storage to
:class:`app.sources_registry.SourceRegistry`. Everything here is a pure function
or a thin orchestration over injectable primitives, so the whole module is
offline-testable (design §4.3).

Core algorithm (design §2.4.2):

1. **Graded profile** — ``file_size`` picks the (latency, throughput) weights:
   ``< size_profile_threshold_mb`` → ``(0.6, 0.4)`` else ``(0.2, 0.8)``.
2. **EWMA blend** — this probe is blended with the source's history
   (``α·sample + (1-α)·ewma``) before normalisation, damping single-shot jitter.
3. **Min-max normalisation** within one ranking, so latency (ms) and throughput
   (MB/s) become comparable ``[0, 1]`` terms.
4. **Progressive down-weighting** — the old fixed ``-1e9`` failure penalty is
   replaced by ``base · success_rate · default_weight``; failures degrade a
   source gradually instead of blacklisting it forever.
5. **Cooling** — a reused :class:`~app.hub.net.CircuitBreaker` (3 fails →
   ``open_seconds`` cooldown → half-open probe) skips dead sources entirely
   during cooldown, so ``/api/download/start`` no longer pays a timeout for
   them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .sources_registry import (
    SourceHealth,
    SourceRegistry,
)

#: Sentinel returned for sources that must rank last (failed / cooling).
NEG_INF = float("-inf")

#: File-size threshold (MB) that switches latency-profile → throughput-profile.
DEFAULT_SIZE_THRESHOLD_MB = 512.0

#: Profile weight presets (latency, throughput).
PROFILE_SMALL = (0.6, 0.4)   # small files: first-byte latency dominates
PROFILE_LARGE = (0.2, 0.8)   # large files: sustained throughput dominates
PROFILE_ENGINE = (0.1, 0.9)  # engine binaries are large archives

#: Legacy linear constants kept identical to ``sources._score`` so the coarse
#: tie-break ordering matches the pre-2.8.1 behaviour when no registry exists.
_LEGACY_SPEED_W = 10.0
_LEGACY_LATENCY_W = 0.05


def choose_profile_weights(
    file_size: int,
    profile: str = "",
    *,
    threshold_mb: float = DEFAULT_SIZE_THRESHOLD_MB,
) -> tuple[float, float]:
    """Return ``(w_latency, w_throughput)`` for a file (design §2.4.3).

    An explicit ``profile`` (``"latency"`` / ``"speed"``) overrides the
    size-based auto-selection. ``file_size`` is in **bytes**.
    """
    p = (profile or "").lower()
    if p == "latency":
        return PROFILE_SMALL
    if p in {"speed", "throughput"}:
        return PROFILE_LARGE
    if p == "engine":
        return PROFILE_ENGINE
    size_mb = max(0.0, float(file_size)) / (1024.0 * 1024.0)
    if size_mb < float(threshold_mb):
        return PROFILE_SMALL
    return PROFILE_LARGE


def _min_max(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    return min(values), max(values)


def _norm(value: float, lo: float, hi: float) -> float:
    """Min-max normalise ``value`` to ``[0, 1]``; constant range → 1.0."""
    if hi <= lo:
        return 1.0
    return (value - lo) / (hi - lo)


def _is_usable_probe(probe: Any) -> bool:
    """True only when a probe represents a *real* downloadable payload.

    A 2xx status is not sufficient: SPA fallback pages and opaque error pages
    answer 200 with an empty body. Treating those as usable lets a 0-byte
    "source" win the ranking. Single source of truth for both the normalisation
    pool and the final score, so the two can never drift apart.

    Accepts either a probe-like object (``SourceProbe`` / ``_ProbeView``, which
    expose attributes) or a plain dict (as returned by ``SourceProbe.to_dict()``),
    since the ranking path works with dicts.
    """

    def _get(key: str, default: Any) -> Any:
        if isinstance(probe, dict):
            return probe.get(key, default)
        return getattr(probe, key, default)

    if not bool(_get("ok", False)):
        return False
    try:
        if int(_get("status", 0) or 0) >= 400:
            return False
    except (TypeError, ValueError):
        return False
    try:
        return int(_get("size_bytes", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def ewma_blend(sample: float, history: float, alpha: float) -> float:
    """Blend a fresh sample with history: ``α·sample + (1-α)·history``.

    When ``history`` is ``0.0`` (no prior observation) the sample is returned
    unchanged, so a first probe is not artificially halved.
    """
    if history <= 0.0:
        return float(sample)
    return float(alpha) * float(sample) + (1.0 - float(alpha)) * float(history)


@dataclass
class SelectionResult:
    """Outcome of :meth:`SourceScheduler.select` (design §2.3.1)."""

    best_url: str = ""
    ranking: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    from_cache: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "best": self.best_url,
            "best_url": self.best_url,
            "ranking": self.ranking,
            "skipped": self.skipped,
            "from_cache": self.from_cache,
        }


class SourceScheduler:
    """Graded, history-aware source selection over a :class:`SourceRegistry`."""

    def __init__(
        self,
        registry: SourceRegistry,
        *,
        ewma_alpha: float = 0.4,
        size_threshold_mb: float = DEFAULT_SIZE_THRESHOLD_MB,
        cache_ttl_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = registry
        self.ewma_alpha = float(ewma_alpha)
        self.size_threshold_mb = float(size_threshold_mb)
        self.cache_ttl_s = float(cache_ttl_s)
        self._clock = clock

    # -- scoring -----------------------------------------------------------

    def score_source(
        self,
        probe: Any,
        health: SourceHealth | None,
        file_size: int,
        profile: str = "",
        *,
        lat_norm: float = 1.0,
        spd_norm: float = 1.0,
    ) -> float:
        """Return a graded score (design §2.4.2).

        ``lat_norm`` / ``spd_norm`` are the *already normalised* latency and
        throughput terms computed by :meth:`_score_ranking` across the whole
        candidate set; this method folds in the profile weights, EWMA-smoothed
        success rate and default weight. Kept public + pure so it can be
        asserted directly in tests.
        """
        if not _is_usable_probe(probe):
            # Rejects non-2xx *and* 2xx-with-empty-body (see _is_usable_probe).
            return NEG_INF
        if health is not None and not health.available():
            return NEG_INF
        w_lat, w_spd = choose_profile_weights(
            file_size, profile, threshold_mb=self.size_threshold_mb,
        )
        base = w_lat * lat_norm + w_spd * spd_norm
        sr = 1.0 if health is None else max(0.0, health.success_rate)
        weight = 1.0
        meta = self.registry.for_host(getattr(probe, "host", ""))
        if meta is not None:
            weight = max(0.0, float(meta.default_weight))
        return base * sr * weight

    def _score_ranking(
        self,
        probes: list[dict[str, Any]],
        file_size: int,
        profile: str,
    ) -> list[dict[str, Any]]:
        """Normalise + EWMA-blend + score every probe, sorted best-first."""
        # Gather EWMA-effective latency/speed for every OK probe.
        eff_lat: list[float] = []
        eff_spd: list[float] = []
        per_item: list[tuple[dict[str, Any], SourceHealth | None, float, float]] = []
        for p in probes:
            host = str(p.get("host") or "")
            meta = self.registry.for_host(host)
            sid = str(p.get("source_id") or (meta.id if meta else host))
            health = self.registry.health_of(sid) if sid else None
            ok = _is_usable_probe(p)
            if ok:
                lat = float(p.get("latency_ms", 0.0) or 0.0)
                spd = float(p.get("speed_mbps", 0.0) or 0.0)
                if health is not None:
                    lat = ewma_blend(lat, health.ewma_latency_ms, self.ewma_alpha)
                    spd = ewma_blend(spd, health.ewma_speed_mbps, self.ewma_alpha)
                eff_lat.append(lat)
                eff_spd.append(spd)
            per_item.append((p, health, lat if ok else 0.0, spd if ok else 0.0))

        lat_lo, lat_hi = _min_max(eff_lat)
        spd_lo, spd_hi = _min_max(eff_spd)

        out: list[dict[str, Any]] = []
        for p, health, lat_eff, spd_eff in per_item:
            ok = _is_usable_probe(p)
            if ok:
                lat_norm = 1.0 - _norm(lat_eff, lat_lo, lat_hi)  # lower latency → higher
                spd_norm = _norm(spd_eff, spd_lo, spd_hi)         # higher speed  → higher
            else:
                lat_norm = 0.0
                spd_norm = 0.0
            score = self.score_source(
                _ProbeView(p, ok), health, file_size, profile,
                lat_norm=lat_norm, spd_norm=spd_norm,
            )
            item = dict(p)
            item["score"] = None if score == NEG_INF else round(float(score), 6)
            item["cooling"] = bool(health is not None and not health.available())
            item["ewma_latency_ms"] = round(float(health.ewma_latency_ms), 1) if health else 0.0
            item["ewma_speed_mbps"] = round(float(health.ewma_speed_mbps), 2) if health else 0.0
            item["success_rate"] = round(float(health.success_rate), 4) if health else 1.0
            out.append(item)

        # Failed / cooling entries sort last; among OK entries, higher score wins.
        out.sort(key=lambda it: (it.get("score") is not None, it.get("score") or 0.0),
                 reverse=True)
        return out

    # -- selection ---------------------------------------------------------

    async def select(
        self,
        urls: Sequence[str],
        *,
        file_size: int = 0,
        purpose: str = "model",
        profile: str = "",
        force: bool = False,
    ) -> SelectionResult:
        """Probe, score and rank ``urls`` (design §2.4).

        Steps:

        1. Split candidates into *probe-worthy* and *cooling/skipped* by asking
           each source's breaker via :meth:`SourceRegistry.health_of`.
        2. Serve cached probe results (TTL) unless ``force`` is set.
        3. Probe the remainder through :func:`app.sources.measure_sources`.
        4. Fold results into health (EWMA) + cache (TTL) + breaker.
        5. Score + sort and return a :class:`SelectionResult`.
        """
        uniq: list[str] = []
        seen: set[str] = set()
        for u in urls or []:
            s = str(u or "").strip()
            if s and s not in seen:
                seen.add(s)
                uniq.append(s)
        if not uniq:
            return SelectionResult()

        skipped: list[str] = []
        to_probe: list[str] = []
        cached_probes: list[dict[str, Any]] = []
        any_cache_hit = False

        for u in uniq:
            host = _host_of(u)
            meta = self.registry.for_host(host)
            sid = meta.id if meta else host
            health = self.registry.health_of(sid)
            # Cooling sources are skipped without any network IO (§2.4.4).
            if not health.available():
                skipped.append(u)
                continue
            cache_key = _cache_key(u, file_size)
            hit = None if force else self.registry.probe_cache.get(cache_key)
            if isinstance(hit, dict):
                item = dict(hit)
                item["from_cache"] = True
                cached_probes.append(item)
                any_cache_hit = True
                continue
            to_probe.append(u)

        # Probe the reachable candidates (network work is isolated to sources).
        fresh_dicts: list[dict[str, Any]] = []
        if to_probe:
            from .sources import measure_sources
            try:
                fresh_dicts = await measure_sources(
                    to_probe,
                    concurrency=_probe_concurrency(),
                )
            except Exception:
                # A probe glitch must never take down the caller (§4.2).
                fresh_dicts = []
            # Fold fresh results into health + cache.
            for d in fresh_dicts:
                host = str(d.get("host") or "") or _host_of(str(d.get("url") or ""))
                meta = self.registry.for_host(host)
                sid = meta.id if meta else host
                d.setdefault("source_id", sid)
                d.setdefault("source_type", meta.type if meta else "")
                d["from_cache"] = False
                health = self.registry.health_of(sid)
                health.observe(_ProbeView(d, bool(d.get("ok"))), alpha=self.ewma_alpha)
                self.registry.probe_cache.set(
                    _cache_key(str(d.get("url") or ""), file_size),
                    {k: v for k, v in d.items() if k != "from_cache"},
                    ttl=self.cache_ttl_s,
                )

        all_probes = cached_probes + fresh_dicts
        ranking = self._score_ranking(all_probes, file_size, profile)

        # Honour a user-locked source: hoist it to the top when present.
        locked = _locked_source_id()
        if locked:
            ranking.sort(
                key=lambda it: 0 if str(it.get("source_id") or "") == locked else 1
            )

        best_url = ""
        for it in ranking:
            if it.get("ok") and it.get("score") is not None:
                best_url = str(it.get("url") or "")
                break

        return SelectionResult(
            best_url=best_url,
            ranking=ranking,
            skipped=skipped,
            from_cache=bool(any_cache_hit),
        )

    # -- introspection -----------------------------------------------------

    def skipped_cooling(self) -> list[str]:
        """Source ids currently in cooling (open breaker)."""
        out: list[str] = []
        for sid, h in self.registry.health.items():
            if not h.available():
                out.append(sid)
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _ProbeView:
    """Lightweight probe view so scoring never depends on the raw dict shape."""

    data: dict[str, Any]
    ok_flag: bool

    @property
    def ok(self) -> bool:  # noqa: D401 — attribute-like by design
        return bool(self.ok_flag)

    @property
    def status(self) -> int:
        try:
            return int(self.data.get("status", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def host(self) -> str:
        return str(self.data.get("host") or "")

    @property
    def size_bytes(self) -> int:
        """Probe body size — 0 means the source returned no payload.

        Exposed so :meth:`SourceScheduler.score_source` can reject 2xx-but-
        empty responses (SPA fallback / opaque error pages).
        """
        try:
            return int(self.data.get("size_bytes", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def latency_ms(self) -> float:
        try:
            return float(self.data.get("latency_ms", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def speed_mbps(self) -> float:
        try:
            return float(self.data.get("speed_mbps", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0


def _host_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _cache_key(url: str, file_size: int) -> str:
    """Cache key = url + coarse size bucket (design §2.4.5)."""
    bucket = 0
    if file_size and file_size > 0:
        # 512 MB buckets keep the cache stable across small size differences.
        bucket = int(float(file_size) // (512 * 1024 * 1024))
    return f"{url.strip().lower()}#{bucket}"


def _probe_concurrency() -> int:
    """Read ``probe_concurrency`` from settings, defaulting to the module cap."""
    try:
        from .settings import load_settings
        return max(1, int(getattr(load_settings(), "probe_concurrency", 8) or 8))
    except Exception:
        from .sources import PROBE_CONCURRENCY
        return int(PROBE_CONCURRENCY)


def _locked_source_id() -> str:
    try:
        from .settings import load_settings
        return str(getattr(load_settings(), "locked_source", "") or "")
    except Exception:
        return ""


__all__ = [
    "DEFAULT_SIZE_THRESHOLD_MB",
    "NEG_INF",
    "PROFILE_ENGINE",
    "PROFILE_LARGE",
    "PROFILE_SMALL",
    "SelectionResult",
    "SourceScheduler",
    "choose_profile_weights",
    "ewma_blend",
]
