"""HuggingFace adapter for the dual-source hub.

Design reference: ``docs/DESIGN_V280_DUAL_SOURCE.md`` §1.6 / §2.5.

Reuse (no re-implementation):
* ``app.importer._HF_API_MIRRORS``  — mirror rotation order.
* ``app.importer.hf_resolve_url()`` — canonical ``/resolve/`` download URL.

**Field-name policy (design T02 requirement).** Everything upstream-derived is
read through :data:`_HF_FIELD_MAP`. If a future live probe shows a renamed
field, change the single constant — the body of this module stays untouched.

Verified 2026-09 against ``https://hf-mirror.com/api/models?search=qwen&limit=2``:

* ``GET /api/models`` returns a **JSON array** (not ``{"data": [...]}``).
* Item keys: ``_id, id, modelId, likes, trendingScore, private, downloads,
  tags, pipeline_tag, library_name, createdAt``.
* Pagination is **cursor based**: the ``Link`` header carries
  ``<...&cursor=...>; rel="next"``. A ``page`` query param is ignored
  (verified: page=1 and page=2 returned identical results), which is why
  :meth:`HuggingFaceAdapter.search` threads the cursor through
  :attr:`~app.hub.base.SourceCursor.token` instead of a page number.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .base import (
    HUB_HF,
    PageResult,
    RemoteFile,
    RemoteModel,
    SearchSpec,
    SourceAdapter,
    SourceCursor,
    as_bool,
    as_int,
    as_str_list,
    clamp_str,
    hub_display,
    is_valid_repo,
    pick,
    synth_id,
)
from .net import (
    CircuitBreaker,
    FetchOutcome,
    TTLCache,
    TokenBucket,
    request_with_retry,
    timeout_for,
)
from .taxonomy import (
    build_tags,
    canonical_license,
    infer_engines,
    map_category,
    size_gb_from_bytes,
    total_size_bytes,
    trending_from,
)

# ---------------------------------------------------------------------------
# 【单一收口点】HuggingFace /api/models field names — change here only.
# ---------------------------------------------------------------------------
_HF_FIELD_MAP: dict[str, str] = {
    # Confirmed by live probe (see module docstring).
    "id": "id",
    "name": "modelId",
    "author": "author",
    "downloads": "downloads",
    "likes": "likes",
    "tags": "tags",
    "pipeline_tag": "pipeline_tag",
    "library": "library_name",
    "last_modified": "lastModified",
    "created_at": "createdAt",
    "trending": "trendingScore",
    "private": "private",
    # Unverified at implementation time — kept as an alias so a rename is a
    # one-line change. `createdAt` is the confirmed sibling key.
    "updated_at": "lastModified",
}

#: Sort values we are *certain* the upstream accepts. Left empty on purpose:
#: sending a wrong `sort` would silently reorder results, so we sort locally.
_HF_SORT_MAP: dict[str, str] = {}

_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?', re.I)


def _hf(val: str) -> str:
    """Look up a HuggingFace field name through the single map."""
    return _HF_FIELD_MAP.get(val, val)


def _pick_hf(obj: Any, logical: str, default: Any = None) -> Any:
    """Read ``obj`` using the logical field name (e.g. ``"downloads"``)."""
    return pick(obj, _hf(logical), default=default)


def _next_cursor_from_link(header: str | None) -> str:
    """Extract the ``cursor=`` value from an RFC-8288 ``Link`` header."""
    if not header:
        return ""
    m = _LINK_NEXT_RE.search(header)
    if not m:
        return ""
    target = unquote(m.group(1))
    try:
        qs = parse_qs(urlparse(target).query)
    except Exception:
        return ""
    vals = qs.get("cursor") or []
    return str(vals[0]) if vals else ""


class HuggingFaceAdapter(SourceAdapter):
    """Read-only HuggingFace Hub client (search / detail / file listing)."""

    hub = HUB_HF
    display_name = hub_display(HUB_HF)

    def __init__(
        self,
        *,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        settings: Any | None = None,
        mirrors: Sequence[str] | None = None,
        breaker: CircuitBreaker | None = None,
        bucket: TokenBucket | None = None,
        cache: TTLCache | None = None,
        sleep: Any | None = None,
    ) -> None:
        super().__init__(base_url=base_url, client=client, settings=settings)
        try:
            from ..importer import _HF_API_MIRRORS  # reuse, never duplicate
            default_mirrors: tuple[str, ...] = tuple(_HF_API_MIRRORS)
        except Exception:  # pragma: no cover — importer always importable
            default_mirrors = ("https://huggingface.co/api",)
        self.mirrors: tuple[str, ...] = (
            tuple(default_mirrors) if mirrors is None else tuple(mirrors)
        )
        if base_url:
            primary = str(base_url).rstrip("/")
            self.mirrors = (primary,) + tuple(m for m in self.mirrors if m != primary)
        self._breaker = breaker or CircuitBreaker(fail_threshold=5, open_seconds=60.0)
        self._bucket = bucket or TokenBucket(rate=5.0, capacity=10.0)
        self._cache = cache if cache is not None else TTLCache(max_entries=512)
        self._negative = TTLCache(max_entries=256)
        self._sem = asyncio.Semaphore(4)
        self._sleep = sleep
        # Observability counters (asserted by the offline test-suite).
        self.requests_made = 0
        self.last_warning = ""

    # --- plumbing ---------------------------------------------------------

    def _client_or_new(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=timeout_for("search"),
                follow_redirects=True,
                headers={"User-Agent": "kevrai-omni/2.8.0"},
            )
            self._client_owned = True
        return self._client

    def auth_headers(self) -> dict[str, str]:
        """Bearer token for gated repos (matches ``main.py``'s gated path)."""
        token = str(getattr(self._settings, "hf_token", "") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _mirror_origins(self) -> tuple[str, ...]:
        """Mirror bases with the trailing ``/api`` stripped (for resolve URLs)."""
        out = []
        for m in self.mirrors:
            s = str(m).rstrip("/")
            out.append(s[:-4] if s.endswith("/api") else s)
        return tuple(out)

    async def _fetch(
        self,
        method: str,
        path: str,
        *,
        kind: str = "search",
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
    ) -> FetchOutcome:
        """Try every mirror in order; return the first successful outcome."""
        client = self._client_or_new()
        last = FetchOutcome(ok=False, code="network", error="no mirror configured")
        for mirror in self.mirrors:
            url = f"{str(mirror).rstrip('/')}/{path.lstrip('/')}"
            async with self._sem:
                self.requests_made += 1
                outcome = await request_with_retry(
                    client,
                    method,
                    url,
                    params=params,
                    headers=self.auth_headers() or None,
                    json_body=json_body,
                    timeout=timeout_for(kind),
                    breaker=self._breaker,
                    bucket=self._bucket,
                    sleep=self._sleep,
                )
            if outcome.ok:
                return outcome
            last = outcome
            if outcome.code in {"circuit_open", "local_limited"}:
                break
        return last

    # --- normalization ----------------------------------------------------

    def _normalize(self, obj: Any) -> RemoteModel | None:
        """One HF search item → :class:`RemoteModel` (never raises)."""
        if not isinstance(obj, Mapping):
            return None
        repo = clamp_str(_pick_hf(obj, "id", "") or _pick_hf(obj, "name", ""), 200).strip()
        if not repo or "/" not in repo:
            return None
        tags = as_str_list(_pick_hf(obj, "tags", []), limit=64)
        pipeline = str(_pick_hf(obj, "pipeline_tag", "") or "").strip()
        category = map_category(pipeline_tag=pipeline, tags=tags)
        downloads = as_int(_pick_hf(obj, "downloads", 0), 0)
        likes = as_int(_pick_hf(obj, "likes", 0), 0)
        # List stage: no file listing, so engines are a low-confidence guess
        # driven by the tag vocabulary (e.g. "gguf" / "safetensors").
        engines, import_only = infer_engines([], repo=repo, category=category)
        return RemoteModel(
            id=synth_id(self.hub, repo),
            name=(repo.rsplit("/", 1)[-1] or repo)[:200],
            description="",
            category=category,
            license=canonical_license("", tags),
            size_gb=0.0,
            size_known=False,
            engine=engines,
            engine_confidence="low",
            trending=trending_from(downloads, likes),
            repo=repo,
            hardware={},
            tags=build_tags(tags),
            modality={},
            hub=self.hub,
            downloads=downloads,
            likes=likes,
            revision="main",
            task=pipeline,
            import_only=import_only,
        )

    # --- SourceAdapter surface -------------------------------------------

    async def search(self, spec: SearchSpec, cursor: SourceCursor) -> PageResult:
        """One page of HF results (cursor pagination, §2.5 mirror rotation)."""
        if cursor.done:
            return PageResult(items=[], next=None, total=None)
        limit = max(1, min(int(spec.page_size or 30), 100))
        params: dict[str, Any] = {"limit": str(limit)}
        if spec.q:
            params["search"] = spec.q
            if spec.category and spec.category != "other":
                # HF uses tags for task filtering; keep it best-effort only.
                params["filter"] = spec.category
        elif spec.category and spec.category != "other":
            params["filter"] = spec.category
        if spec.engine:
            params["library"] = spec.engine
        sort = _HF_SORT_MAP.get(spec.sort, "")
        if sort:
            params["sort"] = sort
        if cursor.token:
            params["cursor"] = cursor.token

        key = f"hf:search:{spec.signature()}:{cursor.token or 'first'}:{limit}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        outcome = await self._fetch("GET", "/models", kind="search", params=params)
        if not outcome.ok:
            msg = f"HuggingFace 检索失败（{outcome.code}）"
            self.last_warning = msg
            if outcome.code == "not_found":
                return PageResult(items=[], next=None, total=0)
            return PageResult(items=[], next=None, total=None,
                              degraded=True, code=outcome.code, warning=msg)

        payload = outcome.data
        raw_items: list[Any] = []
        if isinstance(payload, list):
            raw_items = list(payload)
        elif isinstance(payload, Mapping):
            # Tolerate a wrapped shape in case the upstream changes it.
            maybe = pick(payload, "data", "items", "models", default=None)
            if isinstance(maybe, list):
                raw_items = list(maybe)
        if isinstance(payload, list) and not raw_items:
            return PageResult(items=[], next=None, total=0, code="empty")

        items: list[RemoteModel] = []
        for raw in raw_items:
            model = self._normalize(raw)
            if model is not None:
                items.append(model)

        next_token = ""
        if outcome.response is not None:
            next_token = _next_cursor_from_link(
                outcome.response.headers.get("Link")
                or outcome.response.headers.get("link")
            )
        next_cursor: SourceCursor | None = None
        if next_token and items:
            next_cursor = SourceCursor(page=max(1, cursor.page or 1) + 1, token=next_token)
        result = PageResult(items=items, next=next_cursor, total=None)
        self._cache.set(key, result, ttl=90.0)
        return result

    async def detail(self, repo: str) -> RemoteModel:
        """Fetch a single repo's metadata."""
        if not is_valid_repo(repo):
            raise ValueError(f"bad repo: {repo!r}")
        neg = self._negative.get(f"hf:detail:{repo}")
        if neg is not None:
            raise LookupError(f"model not found (cached): {repo}")
        key = f"hf:detail:{repo}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        outcome = await self._fetch("GET", f"/models/{repo}", kind="detail")
        if not outcome.ok:
            if outcome.code == "not_found":
                self._negative.set(key, True, ttl=300.0)
                raise LookupError(f"model not found: {repo}")
            raise RuntimeError(f"hf detail failed: {outcome.code} {outcome.error}")
        model = self._normalize(outcome.data)
        if model is None:
            raise LookupError(f"model not found: {repo}")
        # The detail endpoint carries a description the list endpoint omits.
        desc = ""
        if isinstance(outcome.data, Mapping):
            desc = clamp_str(pick(outcome.data, "description", "cardData", default=""), 2000)
            if isinstance(desc, (dict, list)):
                desc = ""
        model.description = str(desc or "")
        model.engine_confidence = "high"
        self._cache.set(key, model, ttl=600.0)
        return model

    async def files(self, repo: str, revision: str = "") -> list[RemoteFile]:
        """Enumerate a repo tree (recursive, cursor-paginated)."""
        if not is_valid_repo(repo):
            raise ValueError(f"bad repo: {repo!r}")
        rev = (revision or "main").strip() or "main"
        key = f"hf:files:{repo}:{rev}"
        cached = self._cache.get(key)
        if cached is not None:
            return list(cached)

        out: list[RemoteFile] = []
        cursor = ""
        for _ in range(20):
            params: dict[str, Any] = {"recursive": "true"}
            if cursor:
                params["cursor"] = cursor
            outcome = await self._fetch(
                "GET", f"/models/{repo}/tree/{rev}", kind="files", params=params
            )
            if not outcome.ok:
                if out:
                    break
                if outcome.code == "not_found":
                    self._negative.set(key, True, ttl=300.0)
                    raise LookupError(f"repo not found: {repo}")
                raise RuntimeError(f"hf files failed: {outcome.code} {outcome.error}")
            payload = outcome.data
            if not isinstance(payload, list):
                break
            for item in payload:
                if not isinstance(item, Mapping):
                    continue
                if str(pick(item, "type", default="") or "") != "file":
                    continue
                path = clamp_str(pick(item, "path", default="") or "", 512)
                if not path:
                    continue
                lfs = pick(item, "lfs", default=None)
                out.append(RemoteFile(
                    path=path,
                    size=as_int(pick(item, "size", default=0), 0),
                    sha256=str(pick(lfs, "oid", default="") or "") if isinstance(lfs, Mapping) else "",
                    is_lfs=lfs is not None,
                    download_url=self.resolve_url(repo, path, rev),
                    type="file",
                ))
            if outcome.response is not None:
                cursor = str(outcome.response.headers.get("x-next-cursor") or "")
            else:
                cursor = ""
            if not cursor:
                break
        self._cache.set(key, out, ttl=600.0)
        return out

    def resolve_url(self, repo: str, path: str, revision: str = "") -> str:
        """Canonical HF resolve URL (reuses ``importer.hf_resolve_url``)."""
        from ..importer import hf_resolve_url

        return hf_resolve_url(repo, path, (revision or "main") or "main")

    def mirror_candidates(self, repo: str, path: str, revision: str = "") -> list[str]:
        """Canonical URL first, then the same path on every configured mirror."""
        primary = self.resolve_url(repo, path, revision)
        out = [primary]
        rel = f"/{repo}/resolve/{(revision or 'main') or 'main'}/{path}"
        for origin in self._mirror_origins():
            cand = f"{origin}{rel}"
            if cand not in out:
                out.append(cand)
        return out

    def health(self) -> dict[str, Any]:
        snap = self._breaker.snapshot()
        return {
            "hub": self.hub,
            "display_name": self.display_name,
            "enabled": True,
            "online": True,
            "count": None,
            "circuit": snap["state"],
            "circuit_reopen_in_s": snap["reopen_in_s"],
            "token_required": False,
            "token_set": bool(str(getattr(self._settings, "hf_token", "") or "").strip()),
            "requests": self.requests_made,
        }
