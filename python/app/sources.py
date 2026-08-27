"""Multi-source download URL ranking.

The user wants as many sources as possible and the *fastest* one picked
automatically. This module:

    1. Takes a list of candidate URLs.
    2. Issues a `Range: bytes=0-65535` request to each (first 64 KiB).
    3. Measures latency (ms) and throughput (MB/s) for the probe.
    4. Ranks by composite score (low latency + high throughput + status 2xx).
    5. Returns the best URL plus the full ranking for UI display.

No host is refused — only malformed schemes or unreachable hosts are skipped.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


PROBE_RANGE = 65535          # 64 KiB probe chunk
PROBE_TIMEOUT = 8.0          # seconds per probe
PROBE_CONCURRENCY = 8        # parallel probes
DEFAULT_TIMEOUT = 30.0


@dataclass
class SourceProbe:
    url: str
    host: str
    ok: bool
    latency_ms: float         # total time to first byte
    speed_mbps: float         # MB/s during probe
    status: int
    size_bytes: int           # size of probe body
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "host": self.host,
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 1),
            "speed_mbps": round(self.speed_mbps, 2),
            "status": self.status,
            "size_bytes": self.size_bytes,
            "error": self.error,
        }


def _safe_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _score(p: SourceProbe) -> float:
    """Higher is better. Combines latency + throughput into one score."""
    if not p.ok or p.status >= 400:
        return -1e9
    # Lower latency + higher speed is better. Composite:
    #   score = speed_mbps * 10 - latency_ms * 0.05
    return p.speed_mbps * 10.0 - p.latency_ms * 0.05


async def _probe_one(client: httpx.AsyncClient, url: str) -> SourceProbe:
    host = _safe_host(url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return SourceProbe(url=url, host=host, ok=False, latency_ms=0,
                           speed_mbps=0, status=0, size_bytes=0,
                           error=f"unsupported scheme: {parsed.scheme}")
    t0 = time.perf_counter()
    try:
        req = client.build_request(
            "GET", url,
            headers={"Range": f"bytes=0-{PROBE_RANGE - 1}",
                     "User-Agent": "kevrai-studio/2.3.0"},
        )
        resp = await client.send(req, follow_redirects=True)
        # Stream a fixed-size body
        body = bytearray()
        async for chunk in resp.aiter_bytes(8192):
            body.extend(chunk)
            if len(body) >= PROBE_RANGE:
                break
        elapsed = time.perf_counter() - t0
        size = len(body)
        speed = (size / (1024 * 1024)) / max(elapsed, 1e-6) if elapsed > 0 else 0
        await resp.aclose()
        # 2xx and 206 (partial) are both acceptable
        ok = 200 <= resp.status_code < 400
        return SourceProbe(
            url=url, host=host, ok=ok,
            latency_ms=elapsed * 1000.0, speed_mbps=speed,
            status=resp.status_code, size_bytes=size,
        )
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return SourceProbe(url=url, host=host, ok=False,
                           latency_ms=elapsed * 1000.0, speed_mbps=0,
                           status=0, size_bytes=0, error=str(e)[:200])


async def measure_sources(
    urls: list[str],
    *,
    timeout: float = PROBE_TIMEOUT,
    concurrency: int = PROBE_CONCURRENCY,
) -> list[dict[str, Any]]:
    """Probe every URL in `urls` and return a sorted ranking (best first).

    Each entry is a dict matching `SourceProbe.to_dict()`. The very first
    item is the recommended source.
    """
    if not urls:
        return []
    # de-dup
    seen = set()
    uniq = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    sem = asyncio.Semaphore(concurrency)
    timeout_obj = httpx.Timeout(timeout)

    async with httpx.AsyncClient(timeout=timeout_obj, follow_redirects=True) as client:
        async def _run(u: str) -> SourceProbe:
            async with sem:
                return await _probe_one(client, u)
        probes = await asyncio.gather(*[_run(u) for u in uniq])
    probes_list = list(probes)
    probes_list.sort(key=_score, reverse=True)
    return [p.to_dict() for p in probes_list]


def pick_best(ranking: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first OK entry from a ranking."""
    for p in ranking:
        if p.get("ok"):
            return p
    return None


# Hosts that mirror the HuggingFace path layout
# (`/<owner>/<repo>/resolve/<ref>/<file>`). Swapping the host of a primary
# HF URL onto any of these yields a working equivalent download URL.
HF_MIRROR_HOSTS: set[str] = {
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "hf-mirror.com",
    "hf-mirror.us",
    "hf-cdn.sufy.com",
    "huggingface.dl.in.tel",
    "hf-cn-mirror.com",
}


def _host_of(url: str) -> str:
    try:
        h = urlparse(url).hostname or ""
    except Exception:
        return ""
    return h.lower().lstrip("www.")


def _swap_host(url: str, new_host: str) -> str:
    """Replace only the host (keep scheme/path/query/fragment)."""
    try:
        p = urlparse(url)
    except Exception:
        return url
    if not p.scheme or not p.hostname:
        return url
    netloc = new_host
    if p.port:
        netloc = f"{new_host}:{p.port}"
    return p._replace(netloc=netloc).geturl()


def expand_mirror_candidates(
    primary_url: str,
    mirrors: list[str],
    *,
    hf_hosts: set[str] | None = None,
) -> list[str]:
    """Build an ordered, de-duplicated list of candidate download URLs.

    The primary URL is always first. When the primary lives on a known
    HuggingFace-style host, we swap its host onto every configured mirror to
    produce equivalent mirror URLs — this is what lets the auto-picker compare
    many real sources for the *same* file and pick the fastest reachable one.

    Args:
        primary_url: the original download URL (usually huggingface.co).
        mirrors: list of mirror origins, e.g. ``["https://hf-mirror.com", ...]``.
        hf_hosts: override the set of hosts treated as HF-path-compatible.
    """
    primary = (primary_url or "").strip()
    if not primary:
        return []
    out: list[str] = [primary]
    seen = {primary.lower()}
    hosts = hf_hosts if hf_hosts is not None else HF_MIRROR_HOSTS
    ph = _host_of(primary)
    if ph in hosts or ph.endswith(".huggingface.co"):
        for m in mirrors or []:
            m = (m or "").strip()
            if not m:
                continue
            mh = _host_of(m)
            if not mh or mh == ph:
                continue
            cand = _swap_host(primary, mh)
            if cand.lower() not in seen:
                seen.add(cand.lower())
                out.append(cand)
    return out
