"""HuggingFace adapter for the dual-source hub.


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
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .. import USER_AGENT
from .base import (
    HUB_HF,
    PageResult,
    RemoteFile,
    RemoteModel,
    SearchSpec,
    SourceAdapter,
    SourceCursor,
    as_int,
    as_iso,
    as_str_list,
    clamp_str,
    hub_display,
    is_valid_repo,
    pick,
    split_owner,
    strip_markdown,
    synth_id,
)
from .net import (
    CircuitBreaker,
    FetchOutcome,
    TokenBucket,
    TTLCache,
    request_with_retry,
    timeout_for,
)
from .taxonomy import (
    build_tags,
    canonical_license,
    infer_engines,
    map_category,
    size_gb_from_bytes,
    trending_from,
)

#: A real `trendingScore` at or above this marks a model as hot. The key is
#: absent on some payloads, hence the `> 0` guard at the call site.
_HF_TRENDING_SCORE = 100

log = logging.getLogger("kevrai.hub.hf")

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

#: Sort values confirmed to return HTTP 200 against the live API. This was
#: empty pre-v2.9.0 ("sending a wrong `sort` would silently reorder results"),
#: but all five were then probed live and are honoured server-side, so the
#: mapping is now real instead of a local-only fallback.
_HF_SORT_MAP: dict[str, str] = {
    "downloads": "downloads",
    "likes": "likes",
    "recent": "lastModified",
    "name_asc": "createdAt",
}

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
        #: Last mirror that answered successfully; hoisted to the front of the
        #: rotation so a known-dead mirror is not re-tried on every call.
        self._preferred_mirror = ""
        # Observability counters (asserted by the offline test-suite).
        self.requests_made = 0
        self.last_warning = ""

    # --- plumbing ---------------------------------------------------------

    def _client_or_new(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=timeout_for("search"),
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
            self._client_owned = True
        return self._client

    def auth_headers(self) -> dict[str, str]:
        """Bearer token for gated repos (matches ``main.py``'s gated path)."""
        token = str(getattr(self._settings, "hf_token", "") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _ordered_mirrors(self) -> tuple[str, ...]:
        """Mirrors with the last known-good one first (see :meth:`_fetch`)."""
        pref = getattr(self, "_preferred_mirror", "")
        if pref and pref in self.mirrors:
            return (pref,) + tuple(m for m in self.mirrors if m != pref)
        return tuple(self.mirrors)

    def _mirror_origins(self) -> tuple[str, ...]:
        """Mirror bases with the trailing ``/api`` stripped (for resolve URLs)."""
        out = []
        for m in self._ordered_mirrors():
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
        """Try every mirror in order; return the first successful outcome.

        **Mirror stickiness (v2.9.0).** Measured live from a CN network, the
        first entry of the rotation (``hf-cdn.sufy.com``) answers **HTTP 403
        after ~45 s**, and the official site is unreachable — so an unprimed
        adapter burns ~50 s before it reaches the mirror that actually works.
        The mirror that last succeeded is therefore moved to the front, which
        turns every subsequent call into a single fast request.
        """
        client = self._client_or_new()
        last = FetchOutcome(ok=False, code="network", error="no mirror configured")
        for mirror in self._ordered_mirrors():
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
                self._preferred_mirror = str(mirror)
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

        # v2.9.0 — `author` is a real value only when the request carried
        # `full=true` (verified live: plain `/api/models` returns `null`).
        # `split_owner` is the fallback so the company row is never blank.
        owner = clamp_str(_pick_hf(obj, "author", "") or "", 128).strip() or split_owner(repo)
        library = clamp_str(_pick_hf(obj, "library", "") or "", 64).strip()
        trending_score = as_int(_pick_hf(obj, "trending", 0), 0)
        used_storage = as_int(pick(obj, "usedStorage", default=0), 0)

        return RemoteModel(
            id=synth_id(self.hub, repo),
            name=(repo.rsplit("/", 1)[-1] or repo)[:200],
            description="",
            category=category,
            license=canonical_license("", tags),
            size_gb=size_gb_from_bytes(used_storage) if used_storage > 0 else 0.0,
            size_known=used_storage > 0,
            engine=engines,
            engine_confidence="low",
            # Prefer the real upstream score; fall back to the local rule the
            # list endpoint's older payloads require.
            trending=(
                trending_score >= _HF_TRENDING_SCORE
                if trending_score > 0
                else trending_from(downloads, likes)
            ),
            repo=repo,
            hardware={},
            tags=build_tags(tags, [library] if library else None),
            modality={},
            hub=self.hub,
            downloads=downloads,
            likes=likes,
            revision="main",
            task=pipeline,
            import_only=import_only,
            # --- v2.9.0 upstream provenance ---
            owner=owner,
            owner_url="",                 # HF exposes no org avatar on this API
            owner_full_name="",           # …nor a localized org name
            nickname="",
            library=library,
            trending_score=trending_score,
            frameworks=[library] if library else [],
            architectures=as_str_list(
                pick(pick(obj, "config", default=None), "architectures", default=[]),
                limit=16,
            ),
            created_at=as_iso(_pick_hf(obj, "created_at", None)),
            updated_at=as_iso(_pick_hf(obj, "updated_at", None)),
            is_hot=trending_score >= _HF_TRENDING_SCORE,
            is_new=False,                 # HF has no "new model" flag
        )

    # --- SourceAdapter surface -------------------------------------------

    async def search(self, spec: SearchSpec, cursor: SourceCursor) -> PageResult:
        """One page of HF results (cursor pagination, §2.5 mirror rotation)."""
        if cursor.done:
            return PageResult(items=[], next=None, total=None)
        limit = max(1, min(int(spec.page_size or 30), 100))
        # `full=true` is what makes `author` and `lastModified` non-null on the
        # list endpoint (verified live: without it both are `null`). It costs
        # nothing extra — same request, a slightly larger body.
        params: dict[str, Any] = {"limit": str(limit), "full": "true"}
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
        # The detail endpoint carries signals the list endpoint omits.
        if isinstance(outcome.data, Mapping):
            # `cardData` holds the README's YAML front-matter as a *dict* — it is
            # NOT prose. Pre-v2.9.0 this was passed straight through `clamp_str`,
            # which stringified the dict and rendered a raw Python repr
            # ("{'library_name': 'transformers', ...}") as the model description.
            card = pick(outcome.data, "cardData", default=None)
            if isinstance(card, Mapping):
                if not model.license:
                    model.license = canonical_license(
                        pick(card, "license", default="") or ""
                    )
                if not model.task:
                    model.task = clamp_str(
                        pick(card, "pipeline_tag", default="") or "", 128
                    ).strip()
                # A human-written `description` may exist on some repos.
                prose = pick(card, "description", "summary", "model-description", default=None)
                if isinstance(prose, str) and prose.strip():
                    model.description = strip_markdown(prose, limit=4000)
            # `author` is populated here even when the list endpoint returns null.
            author = clamp_str(pick(outcome.data, "author", default="") or "", 128).strip()
            if author:
                model.owner = author
            # `siblings` is the full file list — gives us engines for free.
            siblings = pick(outcome.data, "siblings", default=None)
            if isinstance(siblings, list):
                paths = [
                    str(pick(s, "rfilename", "path", default="") or "")
                    for s in siblings
                ]
                engines, import_only = infer_engines(
                    paths, repo=repo, category=model.category
                )
                if engines:
                    model.engine = engines
                    model.import_only = import_only
            architectures = as_str_list(
                pick(pick(outcome.data, "config", default=None), "architectures", default=[]),
                limit=16,
            )
            if architectures:
                model.architectures = architectures
        model.engine_confidence = "high"
        if not model.description:
            # HF's API never returns prose (verified live: `description` is
            # `null` and `cardData` has no description key, even with
            # `?full=true`). The README is the only real source, so it is
            # fetched separately — best-effort, so a failure leaves the detail
            # page intact with an empty description rather than erroring out.
            model.description = await self._fetch_readme(repo, model.revision)
        self._cache.set(key, model, ttl=600.0)
        return model

    async def _fetch_readme(self, repo: str, revision: str = "") -> str:
        """Fetch + flatten a repo's ``README.md`` (returns ``""`` on any issue).

        Uses the mirror **origins** (``self._mirror_origins()``), not
        ``self.mirrors`` — the latter carry an ``/api`` suffix, and raw files
        live at the site root (``/{repo}/raw/{rev}/README.md``). Building this
        URL off the API base yields ``/api/…/raw/…``, which 404s.
        """
        key = f"hf:readme:{repo}"
        cached = self._cache.get(key)
        if cached is not None:
            return str(cached)
        rev = (revision or "main").strip() or "main"
        client = self._client_or_new()
        text = ""
        for origin in self._mirror_origins():
            url = f"{str(origin).rstrip('/')}/{repo}/raw/{rev}/README.md"
            try:
                async with self._sem:
                    self.requests_made += 1
                    resp = await client.get(
                        url,
                        headers=self.auth_headers() or None,
                        timeout=timeout_for("detail"),
                    )
            except Exception as exc:  # noqa: BLE001 — description is optional
                log.debug("hf readme fetch failed for %s: %s", repo, exc)
                continue
            if resp.status_code == 200 and resp.text:
                text = resp.text
                break
        prose = strip_markdown(text, limit=4000)
        # Cache the miss too (short TTL) so a README-less repo is not re-probed
        # on every single detail view.
        self._cache.set(key, prose, ttl=1800.0 if prose else 300.0)
        return prose

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
