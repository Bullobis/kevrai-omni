"""Local curated adapter — wraps the existing ``search.py`` engine.

Design reference: ``docs/DESIGN_V280_DUAL_SOURCE.md`` §1.2 / §1.5.

**Reuse, never re-implement.** The curated source is the 121 hand-verified
entries in ``catalog/models.json``. Its scoring, CJK handling and facets already
live in :func:`app.search.search`; this adapter only:

1. calls that function,
2. converts each ``ModelEntry`` dict into a :class:`~app.hub.base.RemoteModel`
   with ``hub == "curated"`` and ``also_on`` populated from the other hubs,
3. slices the page and returns a :class:`~app.hub.base.PageResult`.

No network, so this source is *never* degraded (§2.6).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .base import (
    HUB_CURATED,
    PageResult,
    RemoteFile,
    RemoteModel,
    SearchSpec,
    SourceAdapter,
    SourceCursor,
    clamp_str,
    hub_display,
)
from .taxonomy import curated_category

#: ``<repo>/catalog`` — same resolution as ``main.CATALOG_DIR`` (no import of
#: ``main`` to avoid the circular dependency).
CATALOG_DIR = Path(__file__).resolve().parents[3] / "catalog"


class CuratedAdapter(SourceAdapter):
    """Local curated catalog exposed through the uniform adapter surface."""

    hub = HUB_CURATED
    display_name = hub_display(HUB_CURATED)

    def __init__(self, *, settings: Any | None = None) -> None:
        super().__init__(base_url="", client=None, settings=settings)

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _to_remote(entry: Mapping[str, Any]) -> RemoteModel:
        """Convert one ``ModelEntry.model_dump()``-shaped dict to RemoteModel."""
        hardware = entry.get("hardware") if isinstance(entry.get("hardware"), dict) else {}
        modality = entry.get("modality") if isinstance(entry.get("modality"), dict) else {}
        category = curated_category(entry.get("category"))
        engine = entry.get("engine") or []
        if isinstance(engine, str):
            engine = [engine]
        return RemoteModel(
            id=clamp_str(entry.get("id") or "", 128),
            name=clamp_str(entry.get("name") or "", 200),
            description=clamp_str(entry.get("description") or "", 2000),
            category=category,
            license=clamp_str(entry.get("license") or "", 64),
            size_gb=float(entry.get("size_gb") or 0.0),
            size_known=bool(float(entry.get("size_gb") or 0.0) > 0),
            engine=[str(e) for e in engine],
            engine_confidence="high",
            trending=bool(entry.get("trending")),
            repo=clamp_str(entry.get("repo") or "", 220),
            hardware=dict(hardware),
            tags=list(entry.get("tags") or []),
            modality=dict(modality),
            files=list(entry.get("files") or []),
            size_bytes=int(entry.get("size_bytes") or 0),
            # NOTE: never write the hub id into `source` — that slot is a URL
            # field (E27). Provenance lives in `hub`.
            source=clamp_str(entry.get("source") or "", 512),
            sources=list(entry.get("sources") or []),
            primary_url=clamp_str(entry.get("primary_url") or "", 1024),
            gguf_repo=clamp_str(entry.get("gguf_repo") or "", 220),
            mnn_repo=clamp_str(entry.get("mnn_repo") or "", 220),
            hub=HUB_CURATED,
            downloads=0,
            likes=0,
            revision="",
            task=clamp_str(entry.get("task") or "", 128),
            import_only=not bool(engine),
        )

    # ------------------------------------------------------------------
    # SourceAdapter surface
    # ------------------------------------------------------------------

    async def search(self, spec: SearchSpec, cursor: SourceCursor) -> PageResult:
        """Page through the local catalog using ``search.py``'s ranking."""
        try:
            from ..catalog import load_catalog
            from ..search import SearchQuery, search as local_search
        except Exception as e:  # pragma: no cover — in-tree modules
            return PageResult(items=[], next=None, total=0, degraded=True,
                              code="network", warning=f"本地检索不可用：{e}")

        try:
            catalog, _engines = load_catalog(CATALOG_DIR)
        except Exception as e:  # pragma: no cover — defensive
            return PageResult(items=[], next=None, total=0, degraded=True,
                              code="network", warning=f"本地目录加载失败：{e}")

        try:
            models = [m.model_dump() for m in getattr(catalog, "models", [])]
        except Exception as e:  # pragma: no cover — defensive
            return PageResult(items=[], next=None, total=0, degraded=True,
                              code="network", warning=f"本地目录解析失败：{e}")

        offset = max(0, int(cursor.offset or 0))
        # Over-fetch so the registry's cross-source dedupe never starves the page.
        fetch_n = min(200, max(spec.page_size, offset + spec.page_size))
        sq = SearchQuery(
            q=spec.q,
            category=spec.category or "",
            engine=spec.engine or "",
            license=spec.license or "",
            sort=spec.sort if spec.sort in
            {"relevance", "name_asc", "size_desc", "size_asc", "trending"} else "relevance",
            page=1,
            page_size=fetch_n,
        )
        try:
            raw = local_search(models, sq)
        except Exception as e:  # pragma: no cover — defensive
            return PageResult(items=[], next=None, total=0, degraded=True,
                              code="network", warning=f"本地检索失败：{e}")

        items_raw = raw.get("items") if isinstance(raw, Mapping) else []
        if not isinstance(items_raw, list):
            items_raw = []
        total = int(raw.get("count") or len(items_raw)) if isinstance(raw, Mapping) else len(items_raw)

        page_raw = items_raw[offset:offset + spec.page_size]
        items: list[RemoteModel] = []
        for entry in page_raw:
            if isinstance(entry, Mapping):
                items.append(self._to_remote(entry))

        consumed = offset + len(page_raw)
        has_more = bool(items) and consumed < total
        next_cursor = SourceCursor(offset=consumed) if has_more else None
        # Preserve the raw dicts so the registry can carry `_score` for ranking.
        result = PageResult(items=items, next=next_cursor, total=total)
        for model, entry in zip(items, page_raw):
            if isinstance(entry, Mapping) and entry.get("_score") is not None:
                model.hardware = dict(model.hardware)
                model.hardware["_score"] = entry.get("_score")
        return result

    async def detail(self, repo: str) -> RemoteModel:
        """Curated detail lookup by ``repo`` or synthetic id (no network)."""
        try:
            from ..catalog import load_catalog
            catalog, _engines = load_catalog(CATALOG_DIR)
        except Exception as e:  # pragma: no cover
            raise LookupError(f"catalog unavailable: {e}") from e
        for m in getattr(catalog, "models", []):
            dump = m.model_dump()
            if repo in {dump.get("id"), dump.get("repo")}:
                return self._to_remote(dump)
        raise LookupError(f"curated model not found: {repo}")

    async def files(self, repo: str, revision: str = "") -> list[RemoteFile]:
        """Curated entries carry no file tree — always empty (never error)."""
        return []

    def resolve_url(self, repo: str, path: str, revision: str = "") -> str:
        """Curated entries are not directly downloadable through the hub."""
        return ""

    def health(self) -> dict[str, Any]:
        count: int | None = None
        try:
            from ..catalog import load_catalog
            catalog, _engines = load_catalog(CATALOG_DIR)
            count = len(getattr(catalog, "models", []))
        except Exception:  # pragma: no cover
            count = None
        return {
            "hub": self.hub,
            "display_name": self.display_name,
            "enabled": True,
            "online": False,
            "count": count,
            "circuit": "closed",
            "circuit_reopen_in_s": 0.0,
            "token_required": False,
            "token_set": False,
        }


__all__ = ["CuratedAdapter"]
