"""Source metadata registry + runtime health (v2.8.1).

The registry upgrades the old "bare list of mirror URLs" into a *metadata
registry*: each source is a :class:`SourceMeta` (type / path rule / default
weight / token need / enabled / preset), and each source accrues a
:class:`SourceHealth` (EWMA latency + throughput, success rate, and a reused
:class:`~app.hub.net.CircuitBreaker`).

Iron rules honoured here (design §3.3):

* The circuit breaker and TTL cache are **imported** from ``app.hub.net`` — no
  second implementation lives in this module.
* 4 dead mirrors (``hf-mirror.us`` / ``hf-cn-mirror.com`` /
  ``huggingface.dl.in.tel`` / ``hf-cdn.sufy.com``) are **kept** but
  ``enabled=False`` so a user's historical config is never silently dropped.
* GitCode is **pluggable and disabled by default** — no unverified URL is ever
  hard-coded (see :data:`PRESET_SOURCES` and :func:`build_gitcode_candidates`).
* Everything is offline-testable: the clock and cache are injectable.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hub.net import CircuitBreaker, TTLCache

# ---------------------------------------------------------------------------
# Preset source registry (design §2.3.2 / §2.5 / §2.6)
# ---------------------------------------------------------------------------

#: ``type`` values used across the registry.
TYPE_HF_MIRROR = "hf_mirror"
TYPE_MODELSCOPE = "modelscope"
TYPE_GITHUB_ENGINE = "github_engine"
TYPE_GITCODE = "gitcode"

#: ``path_rule`` values — how a source maps a repo path to a download URL.
RULE_HF_PATH = "hf_path"      # keep the HF path, swap only the host
RULE_NATIVE = "native"        # source has its own native path layout
RULE_GITCODE_API = "gitcode_api"

#: Preset :class:`SourceMeta` definitions, in display order.
#:
#: ``enabled`` reflects the *real* probe results the user measured (design
#: §1.2): only ``hf-mirror.com`` and ``modelscope.cn`` are reachable today.
PRESET_SOURCES: list[dict[str, Any]] = [
    {
        "id": "hf-mirror-com",
        "name": "HF 镜像（hf-mirror.com）",
        "origin": "https://hf-mirror.com",
        "type": TYPE_HF_MIRROR,
        "host_patterns": ["hf-mirror.com"],
        "path_rule": RULE_HF_PATH,
        "default_weight": 1.0,
        "requires_token": False,
        "enabled": True,
        "preset": True,
        "note": "实测可用（HTTP 200）",
    },
    {
        "id": "hf-mirror-us",
        "name": "HF 镜像（hf-mirror.us）",
        "origin": "https://hf-mirror.us",
        "type": TYPE_HF_MIRROR,
        "host_patterns": ["hf-mirror.us"],
        "path_rule": RULE_HF_PATH,
        "default_weight": 0.3,
        "requires_token": False,
        "enabled": False,
        "preset": True,
        "note": "实测不可用（连接失败）",
    },
    {
        "id": "hf-cn-mirror-com",
        "name": "HF 镜像（hf-cn-mirror.com）",
        "origin": "https://hf-cn-mirror.com",
        "type": TYPE_HF_MIRROR,
        "host_patterns": ["hf-cn-mirror.com"],
        "path_rule": RULE_HF_PATH,
        "default_weight": 0.3,
        "requires_token": False,
        "enabled": False,
        "preset": True,
        "note": "实测不可用（连接失败）",
    },
    {
        "id": "huggingface-dl-in-tel",
        "name": "HF 镜像（huggingface.dl.in.tel）",
        "origin": "https://huggingface.dl.in.tel",
        "type": TYPE_HF_MIRROR,
        "host_patterns": ["huggingface.dl.in.tel"],
        "path_rule": RULE_HF_PATH,
        "default_weight": 0.3,
        "requires_token": False,
        "enabled": False,
        "preset": True,
        "note": "实测不可用（连接失败）",
    },
    {
        "id": "hf-cdn-sufy-com",
        "name": "HF CDN（hf-cdn.sufy.com）",
        "origin": "https://hf-cdn.sufy.com",
        "type": TYPE_HF_MIRROR,
        "host_patterns": ["hf-cdn.sufy.com"],
        "path_rule": RULE_HF_PATH,
        "default_weight": 0.4,
        "requires_token": False,
        "enabled": False,
        "preset": True,
        "note": "实测不可用（HTTP 403 反爬）",
    },
    {
        "id": "modelscope",
        "name": "魔搭 ModelScope",
        "origin": "https://modelscope.cn",
        "type": TYPE_MODELSCOPE,
        "host_patterns": ["modelscope.cn", "modelscope.aliyun.com"],
        "path_rule": RULE_NATIVE,
        "default_weight": 1.0,
        "requires_token": False,
        "enabled": True,
        "preset": True,
        "note": "实测可用（HTTP 200）",
    },
    {
        "id": "github",
        "name": "GitHub（引擎二进制源）",
        "origin": "https://github.com",
        "type": TYPE_GITHUB_ENGINE,
        "host_patterns": ["github.com", "objects.githubusercontent.com", "codeload.github.com"],
        "path_rule": RULE_NATIVE,
        "default_weight": 0.9,
        "requires_token": False,
        "enabled": True,
        "preset": True,
        "note": "用于引擎二进制下载（purpose=engine），非模型源",
    },
    {
        "id": "gitcode",
        "name": "GitCode（可插拔 · 需自测）",
        "origin": "https://gitcode.com",
        "type": TYPE_GITCODE,
        "host_patterns": ["gitcode.com", "raw.gitcode.com"],
        "path_rule": RULE_GITCODE_API,
        "default_weight": 0.5,
        "requires_token": False,
        "enabled": False,
        "preset": True,
        "note": "未验证：本环境无法证实下载链路（raw 418 反爬）。默认关闭，配置后启用。",
    },
]


def matched_host_patterns(host: str) -> list[str]:
    """Return preset source ids whose ``host_patterns`` match ``host``.

    ``host`` is compared case-insensitively and with a ``www.`` prefix stripped,
    matching the semantics of :func:`app.sources._host_of`.
    """
    h = (host or "").lower().removeprefix("www.")
    if not h:
        return []
    out: list[str] = []
    for spec in PRESET_SOURCES:
        for pat in spec.get("host_patterns", []):
            p = str(pat).lower().removeprefix("www.")
            if h == p or h.endswith("." + p):
                out.append(str(spec["id"]))
                break
    return out


# ---------------------------------------------------------------------------
# SourceMeta
# ---------------------------------------------------------------------------


@dataclass
class SourceMeta:
    """Static metadata for one download source (design §2.3.2)."""

    id: str
    name: str
    origin: str
    type: str
    host_patterns: list[str] = field(default_factory=list)
    default_weight: float = 1.0
    requires_token: bool = False
    enabled: bool = True
    preset: bool = False
    path_rule: str = RULE_NATIVE
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "origin": self.origin,
            "type": self.type,
            "host_patterns": list(self.host_patterns),
            "default_weight": round(float(self.default_weight), 4),
            "requires_token": bool(self.requires_token),
            "enabled": bool(self.enabled),
            "preset": bool(self.preset),
            "path_rule": self.path_rule,
            "note": self.note,
        }

    def matches(self, host: str) -> bool:
        """True when ``host`` belongs to this source's pattern set."""
        h = (host or "").lower().removeprefix("www.")
        if not h:
            return False
        for pat in self.host_patterns:
            p = str(pat).lower().removeprefix("www.")
            if h == p or h.endswith("." + p):
                return True
        return False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceMeta:
        """Build from a (user-supplied / persisted) dict, tolerating gaps."""
        patterns = data.get("host_patterns")
        if not isinstance(patterns, list):
            patterns = []
        return cls(
            id=str(data.get("id") or "").strip(),
            name=str(data.get("name") or data.get("id") or "").strip(),
            origin=str(data.get("origin") or "").strip(),
            type=str(data.get("type") or RULE_NATIVE).strip(),
            host_patterns=[str(p) for p in patterns if str(p).strip()],
            default_weight=_safe_float(data.get("default_weight"), 1.0),
            requires_token=bool(data.get("requires_token", False)),
            enabled=bool(data.get("enabled", True)),
            preset=bool(data.get("preset", False)),
            path_rule=str(data.get("path_rule") or RULE_NATIVE).strip(),
            note=str(data.get("note") or ""),
        )


def _safe_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


# ---------------------------------------------------------------------------
# SourceHealth
# ---------------------------------------------------------------------------


@dataclass
class SourceHealth:
    """Runtime health for one source (design §2.4.2 / §2.4.4)."""

    source_id: str
    ewma_latency_ms: float = 0.0
    ewma_speed_mbps: float = 0.0
    success_rate: float = 1.0
    total_trials: int = 0
    total_fails: int = 0
    last_ok_ts: float = 0.0
    breaker: CircuitBreaker = field(
        default_factory=lambda: CircuitBreaker(fail_threshold=3, open_seconds=300.0)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "ewma_latency_ms": round(float(self.ewma_latency_ms), 1),
            "ewma_speed_mbps": round(float(self.ewma_speed_mbps), 2),
            "success_rate": round(float(self.success_rate), 4),
            "total_trials": int(self.total_trials),
            "total_fails": int(self.total_fails),
            "last_ok_ts": float(self.last_ok_ts),
            "breaker": self.breaker.snapshot(),
            "available": self.available(),
        }

    def observe(self, probe: Any, *, alpha: float = 0.4) -> None:
        """Fold one probe result into the running averages (§2.4.2).

        Successes update the EWMA of latency/throughput; every observation
        updates the success rate and (via the breaker) the cooling state.
        """
        ok = bool(getattr(probe, "ok", False))
        self.total_trials += 1
        if ok:
            lat = max(0.0, _safe_float(getattr(probe, "latency_ms", 0.0), 0.0))
            spd = max(0.0, _safe_float(getattr(probe, "speed_mbps", 0.0), 0.0))
            if self.ewma_latency_ms <= 0.0:
                self.ewma_latency_ms = lat
            else:
                self.ewma_latency_ms = alpha * lat + (1.0 - alpha) * self.ewma_latency_ms
            if self.ewma_speed_mbps <= 0.0:
                self.ewma_speed_mbps = spd
            else:
                self.ewma_speed_mbps = alpha * spd + (1.0 - alpha) * self.ewma_speed_mbps
            self.last_ok_ts = time.time()
            self.breaker.record_ok()
        else:
            self.total_fails += 1
            self.breaker.record_fail()
        self.success_rate = 1.0 - (self.total_fails / max(1, self.total_trials))

    def available(self) -> bool:
        """True when not currently cooling (breaker not open)."""
        return self.breaker.allow()

    def to_persist(self) -> dict[str, Any]:
        """Minimal serializable state (breaker counters are not persisted)."""
        return {
            "ewma_latency_ms": float(self.ewma_latency_ms),
            "ewma_speed_mbps": float(self.ewma_speed_mbps),
            "success_rate": float(self.success_rate),
            "total_trials": int(self.total_trials),
            "total_fails": int(self.total_fails),
            "last_ok_ts": float(self.last_ok_ts),
        }

    @classmethod
    def from_persist(cls, source_id: str, data: dict[str, Any]) -> SourceHealth:
        h = cls(source_id=source_id)
        h.ewma_latency_ms = _safe_float(data.get("ewma_latency_ms"), 0.0)
        h.ewma_speed_mbps = _safe_float(data.get("ewma_speed_mbps"), 0.0)
        h.success_rate = _safe_float(data.get("success_rate"), 1.0)
        h.total_trials = int(_safe_float(data.get("total_trials"), 0))
        h.total_fails = int(_safe_float(data.get("total_fails"), 0))
        h.last_ok_ts = _safe_float(data.get("last_ok_ts"), 0.0)
        return h


# ---------------------------------------------------------------------------
# SourceRegistry
# ---------------------------------------------------------------------------


class SourceRegistry:
    """Holds every :class:`SourceMeta` plus per-source :class:`SourceHealth`.

    The registry is intentionally *not* an IO owner: probing lives in
    :mod:`app.sources`, decision-making in :mod:`app.source_scheduler`. This
    class only stores state, exposes lookups, and optionally persists health.
    """

    def __init__(
        self,
        *,
        persist_path: str | os.PathLike[str] | None = None,
        cache_ttl_s: float = 300.0,
        fail_threshold: int = 3,
        cooldown_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.persist_path = Path(persist_path) if persist_path else None
        self.cache_ttl_s = float(cache_ttl_s)
        self.fail_threshold = max(1, int(fail_threshold))
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        self.sources: dict[str, SourceMeta] = {}
        self.health: dict[str, SourceHealth] = {}
        #: probe cache: key → SourceProbe.to_dict(); reused TTLCache (§3.3).
        self.probe_cache = TTLCache(max_entries=512, clock=clock)
        self._load_presets()

    # -- construction helpers ---------------------------------------------

    def _load_presets(self) -> None:
        for spec in PRESET_SOURCES:
            self.add_source(SourceMeta.from_dict(spec))

    def add_source(self, meta: SourceMeta) -> None:
        """Register (or replace) a source and ensure a health record exists."""
        if not meta.id:
            return
        self.sources[meta.id] = meta
        if meta.id not in self.health:
            self.health[meta.id] = SourceHealth(
                source_id=meta.id,
                breaker=CircuitBreaker(
                    fail_threshold=self.fail_threshold,
                    open_seconds=self.cooldown_seconds,
                    clock=self._clock,
                ),
            )

    def remove_source(self, source_id: str) -> None:
        self.sources.pop(source_id, None)
        self.health.pop(source_id, None)

    def get(self, source_id: str) -> SourceMeta | None:
        return self.sources.get(source_id)

    def health_of(self, source_id: str) -> SourceHealth:
        """Return the health record, creating an empty one on first use."""
        h = self.health.get(source_id)
        if h is None:
            h = SourceHealth(
                source_id=source_id,
                breaker=CircuitBreaker(
                    fail_threshold=self.fail_threshold,
                    open_seconds=self.cooldown_seconds,
                    clock=self._clock,
                ),
            )
            self.health[source_id] = h
        return h

    # -- metadata lookups --------------------------------------------------

    def enabled_sources(self, purpose: str = "") -> list[SourceMeta]:
        """Enabled sources, optionally filtered by ``purpose``.

        ``purpose="model"`` excludes engine-only sources; ``purpose="engine"``
        restricts to :data:`TYPE_GITHUB_ENGINE`; empty purpose returns all
        enabled sources (used by the registry view endpoint).
        """
        out: list[SourceMeta] = []
        for meta in self.sources.values():
            if not meta.enabled:
                continue
            if purpose == "model" and meta.type == TYPE_GITHUB_ENGINE:
                continue
            if purpose == "engine" and meta.type != TYPE_GITHUB_ENGINE:
                continue
            out.append(meta)
        return out

    def for_host(self, host: str) -> SourceMeta | None:
        """Best-effort metadata lookup for a URL host."""
        for meta in self.sources.values():
            if meta.matches(host):
                return meta
        return None

    # -- health mutation ---------------------------------------------------

    def mark_unavailable(self, source_id: str, reason: str = "") -> None:
        """Trip a source into cooling (used when the user declares it dead)."""
        h = self.health_of(source_id)
        for _ in range(self.fail_threshold):
            h.breaker.record_fail()
        if source_id in self.sources:
            meta = self.sources[source_id]
            if reason and not meta.note:
                meta.note = reason

    def record_outcome(self, source_id: str, ok: bool) -> None:
        """Record a download-level outcome onto a source's health record."""
        h = self.health_of(source_id)
        h.total_trials += 1
        if ok:
            h.breaker.record_ok()
            h.last_ok_ts = time.time()
        else:
            h.total_fails += 1
            h.breaker.record_fail()
        h.success_rate = 1.0 - (h.total_fails / max(1, h.total_trials))

    # -- views -------------------------------------------------------------

    def snapshot(self) -> list[dict[str, Any]]:
        """Full registry view: metadata + health for every source."""
        out: list[dict[str, Any]] = []
        for meta in self.sources.values():
            item = meta.to_dict()
            item["health"] = self.health_of(meta.id).to_dict()
            out.append(item)
        return out

    # -- persistence -------------------------------------------------------

    def load(self) -> None:
        """Load persisted health (best-effort; a missing file is not an error)."""
        if self.persist_path is None or not self.persist_path.exists():
            return
        try:
            raw = json.loads(self.persist_path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        records = raw.get("health")
        if not isinstance(records, dict):
            return
        for sid, data in records.items():
            if not isinstance(data, dict):
                continue
            restored = SourceHealth.from_persist(str(sid), data)
            # Keep a live breaker (fresh clock); only carry the numeric stats.
            live = self.health_of(str(sid))
            live.ewma_latency_ms = restored.ewma_latency_ms
            live.ewma_speed_mbps = restored.ewma_speed_mbps
            live.success_rate = restored.success_rate
            live.total_trials = restored.total_trials
            live.total_fails = restored.total_fails
            live.last_ok_ts = restored.last_ok_ts

    def save(self) -> None:
        """Atomically persist health stats (mirrors ``settings`` write style)."""
        if self.persist_path is None:
            return
        payload = {
            "version": 1,
            "health": {sid: h.to_persist() for sid, h in self.health.items()},
        }
        blob = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=".source-health-", suffix=".tmp",
                dir=str(self.persist_path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(blob)
                    fh.flush()
                    with contextlib.suppress(OSError):
                        os.fsync(fh.fileno())
                os.replace(tmp_path, self.persist_path)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
        except OSError:
            # Health is non-critical — a failed write must never break a run.
            return


# ---------------------------------------------------------------------------
# Normalisation of the legacy ``extra_model_mirrors`` setting (design §2.4.7)
# ---------------------------------------------------------------------------


def normalize_user_mirrors(mirrors: Iterable[str], *, existing_ids: Iterable[str] = ()) -> list[SourceMeta]:
    """Turn ``settings.extra_model_mirrors`` into ``preset=False`` sources.

    Hosts already covered by a preset source are skipped so the same mirror is
    never registered twice. Malformed / non-http entries are ignored.
    """
    seen = set(existing_ids)
    out: list[SourceMeta] = []
    for m in mirrors or []:
        raw = (m or "").strip()
        if not raw:
            continue
        if not (raw.startswith("http://") or raw.startswith("https://")):
            continue
        host = _host_of(raw)
        if not host:
            continue
        sid = host.replace(".", "-")
        if sid in seen:
            continue
        seen.add(sid)
        out.append(SourceMeta(
            id=sid,
            name=f"自定义镜像（{host}）",
            origin=raw.rstrip("/"),
            type=TYPE_HF_MIRROR,
            host_patterns=[host],
            path_rule=RULE_HF_PATH,
            default_weight=1.0,
            requires_token=False,
            enabled=True,
            preset=False,
            note="来自 extra_model_mirrors",
        ))
    return out


def _host_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# GitCode pluggable candidate builder (design §2.6) — no hard-coded URLs
# ---------------------------------------------------------------------------


def build_gitcode_candidates(
    repo: str,
    path: str,
    revision: str,
    template: str,
) -> list[str]:
    """Build GitCode download URLs from a *user-supplied* template.

    The template must contain ``{repo}``, ``{path}`` and ``{revision}``
    placeholders. When the template is empty (the default) this returns ``[]``
    — the app never guesses an unverified URL (design §2.6, red line).

    Args:
        repo: ``owner/name`` repository identifier.
        path: file path within the repo.
        revision: git revision (branch/tag/sha); ``"main"`` when blank.
        template: user-confirmed URL template, e.g.
            ``"https://<host>/{repo}/-/raw/{revision}/{path}"``.

    Returns:
        A single-element list when the template is valid, else ``[]``.
    """
    tpl = (template or "").strip()
    if not tpl:
        return []
    if "{repo}" not in tpl or "{path}" not in tpl:
        return []
    if not (tpl.startswith("http://") or tpl.startswith("https://")):
        return []
    rev = (revision or "main") or "main"
    try:
        return [tpl.format(repo=repo, path=path, revision=rev)]
    except (KeyError, IndexError, ValueError):
        return []


__all__ = [
    "PRESET_SOURCES",
    "RULE_GITCODE_API",
    "RULE_HF_PATH",
    "RULE_NATIVE",
    "SourceHealth",
    "SourceMeta",
    "SourceRegistry",
    "TYPE_GITCODE",
    "TYPE_GITHUB_ENGINE",
    "TYPE_HF_MIRROR",
    "TYPE_MODELSCOPE",
    "build_gitcode_candidates",
    "matched_host_patterns",
    "normalize_user_mirrors",
]
