"""Kevrai dual-source model hub — HuggingFace + ModelScope + local curated.


Public surface
--------------
* :class:`~app.hub.base.SourceAdapter` — the uniform retrieval interface.
* :class:`~app.hub.registry.HubRegistry` — the orchestrator (fan-out, quota,
  merge/rank, dedupe, cursors, degradation).
* :func:`get_registry` — process-wide singleton, wired with the three adapters
  and the live :class:`~app.settings.Settings`.

The package deliberately does **not** import :mod:`app.main` (that would be a
circular import: ``main`` imports ``hub``). Everything it needs from the wider
app is imported lazily inside functions.
"""
from __future__ import annotations

from .base import (
    ALL_HUBS,
    CATEGORIES,
    HUB_CURATED,
    HUB_HF,
    HUB_MODELSCOPE,
    REMOTE_HUBS,
    PageResult,
    RemoteFile,
    RemoteModel,
    SearchSpec,
    SourceAdapter,
    SourceCursor,
    hub_display,
    is_valid_repo,
    normalize_repo_key,
    synth_id,
)
from .curated import CuratedAdapter
from .hf import HuggingFaceAdapter
from .modelscope import ModelScopeAdapter
from .net import (
    CircuitBreaker,
    FetchOutcome,
    TTLCache,
    TokenBucket,
    request_with_retry,
    timeout_for,
)
from .registry import (
    BadCursor,
    HubRegistry,
    MergedPage,
    allocate_quota,
    cross_source_candidates,
    decode_cursor,
    dedupe,
    encode_cursor,
    merge_rank,
    rank_score,
)
from .taxonomy import infer_engines, map_category

__all__ = [
    "ALL_HUBS",
    "BadCursor",
    "CATEGORIES",
    "CircuitBreaker",
    "CuratedAdapter",
    "FetchOutcome",
    "HUB_CURATED",
    "HUB_HF",
    "HUB_MODELSCOPE",
    "HubRegistry",
    "HuggingFaceAdapter",
    "MergedPage",
    "ModelScopeAdapter",
    "PageResult",
    "REMOTE_HUBS",
    "RemoteFile",
    "RemoteModel",
    "SearchSpec",
    "SourceAdapter",
    "SourceCursor",
    "TTLCache",
    "TokenBucket",
    "allocate_quota",
    "cross_source_candidates",
    "decode_cursor",
    "dedupe",
    "encode_cursor",
    "get_registry",
    "hub_display",
    "infer_engines",
    "is_valid_repo",
    "map_category",
    "merge_rank",
    "normalize_repo_key",
    "rank_score",
    "request_with_retry",
    "reset_registry",
    "synth_id",
    "timeout_for",
]

_REGISTRY: "HubRegistry | None" = None
_REGISTRY_SETTINGS_ID: int | None = None


def build_registry(
    settings: object | None = None,
    *,
    hf_base_url: str | None = None,
    ms_base_url: str | None = None,
    hf_client: object | None = None,
    ms_client: object | None = None,
    ttl_s: int | None = None,
) -> HubRegistry:
    """Construct a fresh :class:`HubRegistry` with the three adapters.

    ``hf_base_url`` / ``ms_base_url`` / ``*_client`` are injectable so tests can
    point the adapters at an in-process fake server (design §6.3).
    """
    curated = CuratedAdapter(settings=settings)
    try:
        hf = HuggingFaceAdapter(
            base_url=hf_base_url, client=hf_client, settings=settings,
        )
    except Exception:  # pragma: no cover — construction is pure/total
        hf = None
    try:
        ms = ModelScopeAdapter(
            base_url=ms_base_url, client=ms_client, settings=settings,
        )
    except Exception:  # pragma: no cover
        ms = None
    return HubRegistry(
        curated=curated, hf=hf, modelscope=ms, settings=settings,
        cache_ttl_s=int(ttl_s or getattr(settings, "hub_cache_ttl_s", 90) or 90),
    )


def get_registry(settings: object | None = None) -> HubRegistry:
    """Return the process-wide registry, (re)building it if settings changed.

    The registry is cheap to build (no network), so rebuilding when the
    settings object identity changes keeps token / mirror edits live without a
    restart.
    """
    global _REGISTRY, _REGISTRY_SETTINGS_ID
    sid = id(settings) if settings is not None else None
    if _REGISTRY is None or (sid is not None and sid != _REGISTRY_SETTINGS_ID):
        _REGISTRY = build_registry(settings)
        _REGISTRY_SETTINGS_ID = sid
    return _REGISTRY


def reset_registry() -> None:
    """Drop the singleton (used by tests and after a settings mutation)."""
    global _REGISTRY, _REGISTRY_SETTINGS_ID
    _REGISTRY = None
    _REGISTRY_SETTINGS_ID = None
