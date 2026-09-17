"""ModelScope (魔搭) adapter for the dual-source hub.


**Reuse over re-implementation** (design T02 hard requirement): the ModelScope
URL shapes and the ``/repo/files`` parsing already exist in
``app/mnn_catalog.py`` and are battle-tested there. This module imports those
constants (``_MS_API``, ``_MS_RESOLVE``, ``_MS_ORG``) and mirrors the same URL
forms rather than inventing a second client.

Verified by live probe at implementation time (2026-09):

* ``PUT https://modelscope.cn/api/v1/models`` with body
  ``{"Name": "qwen", "PageNumber": 1, "PageSize": 2, "SortBy": "Default"}``
  → ``{"Code": 200, "Data": {"Models": [...], "TotalCount": 27334}}``.
  **Only ``SortBy: "Default"`` is accepted** — ``Downloads`` / ``Stars`` /
  ``UpdatedTime`` all return ``Code 10010202002``. Sorting by popularity is
  therefore applied locally after the page is fetched, and
  :data:`_MS_SORT_MAP` stays empty.
* ``GET https://modelscope.cn/api/v1/models/{ns}/{name}`` → ``{"Code":200,
  "Data": {...}}`` with ``Tasks``, ``Revision``, ``StorageSize``, ``License``.
* ``GET .../models/{ns}/{name}/repo/files?Revision=master&Root=`` →
  ``{"Code":200,"Data":{"Files":[{"Name","Path","Size","Sha256","IsLFS","Type"}]}}``
  (already used by ``mnn_catalog.list_mnn_files``).
* Item keys confirmed: ``Path`` (namespace), ``Name``, ``ChineseName``,
  ``Description``, ``License``, ``Downloads``, ``Stars``, ``Revision``,
  ``Tasks`` (list of **objects**: ``Name``/``DomainName``/``ChineseName``/``Id``),
  ``Frameworks``, ``Architectures``, ``Libraries``, ``Tags``, ``StorageSize``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Mapping, Sequence
from urllib.parse import quote

import httpx

from .base import (
    HUB_MODELSCOPE,
    PageResult,
    RemoteFile,
    RemoteModel,
    SearchSpec,
    SourceAdapter,
    SourceCursor,
    as_int,
    as_str_list,
    clamp_int,
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

from .. import USER_AGENT

# Reused verbatim from the existing, proven ModelScope client.
try:  # pragma: no cover - mnn_catalog is always importable in-tree
    from ..mnn_catalog import _MS_API as MS_API_BASE
    from ..mnn_catalog import _MS_ORG as MS_ORG
    from ..mnn_catalog import _MS_RESOLVE as MS_RESOLVE_BASE
except Exception:  # pragma: no cover
    MS_API_BASE = "https://modelscope.cn/api/v1/models"
    MS_RESOLVE_BASE = "https://modelscope.cn/models"
    MS_ORG = "MNN"

# Only "Default" is confirmed by live probe; every other value errors out.
_MS_SORT_MAP: dict[str, str] = {}

# Namespaces whose repos are pre-converted MNN bundles (mnn_catalog:26).
_MNN_NAMESPACES = frozenset({"mnn", "taobao-mnn"})


class ModelScopeAdapter(SourceAdapter):
    """Read-only ModelScope client (search / detail / file listing)."""

    hub = HUB_MODELSCOPE
    display_name = hub_display(HUB_MODELSCOPE)

    def __init__(
        self,
        *,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        settings: Any | None = None,
        breaker: CircuitBreaker | None = None,
        bucket: TokenBucket | None = None,
        cache: TTLCache | None = None,
        sleep: Any | None = None,
    ) -> None:
        super().__init__(base_url=base_url, client=client, settings=settings)
        self.api_base = str(base_url or MS_API_BASE).rstrip("/")
        self.resolve_base = (MS_RESOLVE_BASE if not base_url
                             else str(base_url).rstrip("/").replace("/api/v1/models", ""))
        self._breaker = breaker or CircuitBreaker(fail_threshold=5, open_seconds=60.0)
        self._bucket = bucket or TokenBucket(rate=5.0, capacity=10.0)
        self._cache = cache if cache is not None else TTLCache(max_entries=512)
        self._negative = TTLCache(max_entries=256)
        self._sem = asyncio.Semaphore(4)
        self._sleep = sleep
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
        """Bearer token for private repos.

        【待确认】the exact header ModelScope expects for private repos has not
        been verified against a real private repo. Public search/detail/files
        never send it, so this does not block anything. Verified fact: public
        reads need no token at all.
        """
        token = str(getattr(self._settings, "ms_token", "") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _fetch(
        self,
        method: str,
        path: str,
        *,
        kind: str = "search",
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
    ) -> FetchOutcome:
        client = self._client_or_new()
        url = f"{self.api_base}/{path.lstrip('/')}" if path else self.api_base
        async with self._sem:
            self.requests_made += 1
            return await request_with_retry(
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

    @staticmethod
    def _unwrap(payload: Any) -> tuple[Any, str]:
        """Return ``(data, error_code)`` from a ModelScope envelope.

        Tolerates: non-dict payloads, a missing ``Code``, and ``Data`` that is a
        ``str`` instead of a list/dict (extreme case E05 — a bare string must
        never be iterated character-by-character).
        """
        if not isinstance(payload, Mapping):
            return None, "bad_json"
        code = str(pick(payload, "Code", "code", default="") or "")
        data = pick(payload, "Data", "data", default=None)
        if code and code not in {"200", "0"}:
            return data, code
        return data, ""

    # --- normalization ----------------------------------------------------

    def _normalize(self, obj: Any) -> RemoteModel | None:
        """One ModelScope (PascalCase) item → :class:`RemoteModel`."""
        if not isinstance(obj, Mapping):
            return None
        namespace = clamp_str(pick(obj, "Path", "path", default="") or "", 128).strip()
        name = clamp_str(pick(obj, "Name", "name", default="") or "", 200).strip()
        if not namespace or not name:
            return None
        repo = f"{namespace}/{name}"
        if not is_valid_repo(repo):
            # Fall back to a permissive-but-safe id rather than dropping the row.
            repo = f"{namespace}/{name}"[:220]

        tasks = pick(obj, "Tasks", "tasks", default=None)
        task_name = ""
        task_zh = ""
        if isinstance(tasks, Sequence) and not isinstance(tasks, (str, bytes)) and tasks:
            first = tasks[0]
            if isinstance(first, Mapping):
                task_name = str(pick(first, "Name", "name", default="") or "")
                task_zh = str(pick(first, "ChineseName", "chineseName", default="") or "")
            else:
                task_name = str(first or "")
        domain = pick(obj, "Domain", "domain", default=None)

        category = map_category(
            pipeline_tag=task_name, tasks=tasks, domain=domain,
            tags=as_str_list(pick(obj, "Tags", "tags", default=[]), limit=32),
        )
        downloads = as_int(pick(obj, "Downloads", "downloads", default=0), 0)
        likes = as_int(pick(obj, "Stars", "stars", default=0), 0)
        storage = as_int(pick(obj, "StorageSize", "storageSize", default=0), 0)
        size_gb = size_gb_from_bytes(storage) if storage > 0 else 0.0

        ns_lower = namespace.lower()
        is_mnn = ns_lower in _MNN_NAMESPACES or name.lower().endswith("-mnn")
        engines, import_only = infer_engines([], repo=repo, category=category)
        if is_mnn and "mnn" not in engines:
            engines = ["mnn"] + engines
            import_only = False

        tags = build_tags(
            [t for t in tasks if True] if isinstance(tasks, Sequence) and not isinstance(tasks, (str, bytes)) else [],
            pick(obj, "Frameworks", default=[]),
            pick(obj, "Architectures", default=[]),
            pick(obj, "Libraries", default=[]),
            pick(obj, "Tags", default=[]),
        )

        return RemoteModel(
            id=synth_id(self.hub, repo),
            name=(name or repo.rsplit("/", 1)[-1])[:200],
            description=clamp_str(pick(obj, "Description", "description", default="") or "", 2000),
            category=category,
            license=canonical_license(pick(obj, "License", "license", default="") or ""),
            size_gb=size_gb,
            size_known=storage > 0,
            engine=engines,
            engine_confidence="low",
            trending=trending_from(downloads, likes),
            repo=repo,
            hardware={},
            tags=tags,
            modality={},
            hub=self.hub,
            downloads=downloads,
            likes=likes,
            revision=clamp_str(pick(obj, "Revision", "revision", default="") or "", 64) or "master",
            task=task_name or task_zh,
            import_only=import_only,
        )

    # --- SourceAdapter surface -------------------------------------------

    async def search(self, spec: SearchSpec, cursor: SourceCursor) -> PageResult:
        """One page of ModelScope results (1-based ``PageNumber``)."""
        if cursor.done:
            return PageResult(items=[], next=None, total=None)
        page = cursor.page if cursor.page else 1
        size = clamp_int(spec.page_size, 30, 1, 100)
        body: dict[str, Any] = {
            "Name": spec.q or "",
            "PageNumber": max(1, int(page)),
            "PageSize": size,
            "SortBy": _MS_SORT_MAP.get(spec.sort, "Default"),
        }

        key = f"ms:search:{spec.signature()}:{page}:{size}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        outcome = await self._fetch("PUT", "", kind="search", json_body=body)
        if not outcome.ok:
            msg = f"魔搭检索失败（{outcome.code}）"
            self.last_warning = msg
            if outcome.code == "not_found":
                return PageResult(items=[], next=None, total=0)
            return PageResult(items=[], next=None, total=None,
                              degraded=True, code=outcome.code, warning=msg)

        data, err = self._unwrap(outcome.data)
        if err:
            return PageResult(items=[], next=None, total=None, degraded=True,
                              code="http_error",
                              warning=f"魔搭返回错误码 {err}")
        if not isinstance(data, Mapping):
            return PageResult(items=[], next=None, total=None, degraded=True,
                              code="bad_json", warning="魔搭返回结构异常")

        raw_models = pick(data, "Models", "models", default=None)
        if not isinstance(raw_models, list):
            # E05: a string here must not be iterated char-by-char.
            return PageResult(items=[], next=None, total=0, code="empty")
        total = as_int(pick(data, "TotalCount", "totalCount", default=0), 0) or None

        items: list[RemoteModel] = []
        for raw in raw_models:
            model = self._normalize(raw)
            if model is not None:
                items.append(model)

        has_more = bool(items) and (total is None or (page * size) < int(total))
        next_cursor = SourceCursor(page=int(page) + 1) if has_more else None
        result = PageResult(items=items, next=next_cursor, total=total)
        self._cache.set(key, result, ttl=90.0)
        return result

    async def detail(self, repo: str) -> RemoteModel:
        """Fetch one model's metadata."""
        if not is_valid_repo(repo):
            raise ValueError(f"bad repo: {repo!r}")
        key = f"ms:detail:{repo}"
        if self._negative.get(key) is not None:
            raise LookupError(f"model not found (cached): {repo}")
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        outcome = await self._fetch("GET", f"/{repo}", kind="detail")
        if not outcome.ok:
            if outcome.code == "not_found":
                self._negative.set(key, True, ttl=300.0)
                raise LookupError(f"model not found: {repo}")
            raise RuntimeError(f"modelscope detail failed: {outcome.code} {outcome.error}")
        data, err = self._unwrap(outcome.data)
        if err or not isinstance(data, Mapping):
            raise LookupError(f"model not found: {repo}")
        model = self._normalize(data)
        if model is None:
            raise LookupError(f"model not found: {repo}")
        model.engine_confidence = "high"
        self._cache.set(key, model, ttl=600.0)
        return model

    async def files(self, repo: str, revision: str = "") -> list[RemoteFile]:
        """Enumerate repo files (same URL shape as ``mnn_catalog``)."""
        if not is_valid_repo(repo):
            raise ValueError(f"bad repo: {repo!r}")
        rev = (revision or "master").strip() or "master"
        key = f"ms:files:{repo}:{rev}"
        cached = self._cache.get(key)
        if cached is not None:
            return list(cached)
        outcome = await self._fetch(
            "GET", f"/{repo}/repo/files", kind="files",
            params={"Revision": rev, "Root": ""},
        )
        if not outcome.ok:
            if outcome.code == "not_found":
                self._negative.set(key, True, ttl=300.0)
                raise LookupError(f"repo not found: {repo}")
            raise RuntimeError(f"modelscope files failed: {outcome.code} {outcome.error}")
        data, err = self._unwrap(outcome.data)
        if err or not isinstance(data, Mapping):
            raise LookupError(f"repo not found: {repo}")
        raw_files = pick(data, "Files", "files", default=None)
        if not isinstance(raw_files, list):
            return []
        out: list[RemoteFile] = []
        for item in raw_files:
            if not isinstance(item, Mapping):
                continue
            ftype = str(pick(item, "Type", "type", default="") or "")
            path = clamp_str(pick(item, "Path", "path", default="") or "", 512)
            if not path or ftype == "tree":
                continue
            out.append(RemoteFile(
                path=path,
                size=as_int(pick(item, "Size", "size", default=0), 0),
                sha256=str(pick(item, "Sha256", "sha256", default="") or ""),
                is_lfs=bool(pick(item, "IsLFS", "isLFS", default=False)),
                download_url=self.resolve_url(repo, path, rev),
                type=ftype or "blob",
            ))
        self._cache.set(key, out, ttl=600.0)
        return out

    def resolve_url(self, repo: str, path: str, revision: str = "") -> str:
        """``https://modelscope.cn/models/{repo}/resolve/{rev}/{path}``."""
        rev = (revision or "master").strip() or "master"
        clean = str(path or "").lstrip("/")
        return f"{self.resolve_base}/{repo}/resolve/{rev}/{quote(clean, safe='/')}"

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
            "token_set": bool(str(getattr(self._settings, "ms_token", "") or "").strip()),
            "requests": self.requests_made,
        }
