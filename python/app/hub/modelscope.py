"""ModelScope (魔搭) adapter for the dual-source hub.


**Reuse over re-implementation** (design T02 hard requirement): the ModelScope
URL shapes and the ``/repo/files`` parsing already exist in
``app/mnn_catalog.py`` and are battle-tested there. This module imports those
constants (``_MS_API``, ``_MS_RESOLVE``, ``_MS_ORG``) and mirrors the same URL
forms rather than inventing a second client.

Verified by live probe at implementation time (2026-09 / re-probe 2026-11):

* ``GET https://modelscope.cn/openapi/v1/models`` with
  ``search=qwen&page_number=1&page_size=10&sort=default``
  → ``{"success": true, "data": {"models": [...], "total_count": 28944}}``.
  This is the documented OpenAPI gateway and is now the **search** path:
  direct TTFB 0.25 s vs 6.1 s for the legacy cold query below.
* ``PUT https://modelscope.cn/api/v1/models`` with body
  ``{"Name": "qwen", "PageNumber": 1, "PageSize": 2, "SortBy": "Default"}``
  → ``{"Code": 200, "Data": {"Models": [...], "TotalCount": 27334}}``.
  Kept only for detail/file/README compatibility; its search backend is the
  slow path this module no longer uses.
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
import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from .. import USER_AGENT
from .base import (
    HUB_MODELSCOPE,
    PageResult,
    RemoteFile,
    RemoteModel,
    SearchSpec,
    SourceAdapter,
    SourceCursor,
    as_bool,
    as_int,
    as_iso,
    as_str_list,
    clamp_int,
    clamp_str,
    degraded_message,
    hub_display,
    is_valid_repo,
    pick,
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

# Reused verbatim from the existing, proven ModelScope client.
try:  # pragma: no cover - mnn_catalog is always importable in-tree
    from ..mnn_catalog import _MS_API as MS_API_BASE
    from ..mnn_catalog import _MS_RESOLVE as MS_RESOLVE_BASE
except Exception:  # pragma: no cover
    MS_API_BASE = "https://modelscope.cn/api/v1/models"
    MS_RESOLVE_BASE = "https://modelscope.cn/models"

# OpenAPI sort values verified live: default / downloads / likes /
# last_modified all return 200 and change the order; name_asc has no upstream
# sort, so it is sorted over the returned page after the fetch.
_OPENAPI_SORT: dict[str, str] = {
    "downloads": "downloads",
    "likes": "likes",
    "recent": "last_modified",
}

# Namespaces whose repos are pre-converted MNN bundles (mnn_catalog:26).
_MNN_NAMESPACES = frozenset({"mnn", "taobao-mnn"})

log = logging.getLogger("kevrai.hub.modelscope")


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
        # Public list/search is served by the newer OpenAPI gateway. The legacy
        # `/api/v1/models` endpoint returns the same data but through a
        # different backend: measured live at 6.1 s TTFB for a cold search vs
        # 0.25 s on `/openapi/v1/models`. The legacy endpoint stays in use for
        # detail/files/README, which OpenAPI does not replace today.
        raw_base = str(self.api_base)
        self.openapi_base = (
            raw_base.replace("/api/v1/models", "/openapi/v1/models")
            if "/api/v1/models" in raw_base
            else f"{raw_base}/openapi/v1/models"
        ).rstrip("/")
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
            # Two independent latency causes measured live:
            #   1. legacy PUT /api/v1/models cold query: 6.1 s TTFB; the
            #      OpenAPI search path below is 0.25 s.
            #   2. this process inherited the machine's HTTPS_PROXY; the same
            #      domestic request through it was 6.1 s vs 0.11 s direct.
            # ModelScope is a domestic endpoint, so ignore ambient proxy vars
            # by default; KEVRAI_MODELSCOPE_PROXY is the explicit opt-in for
            # networks that genuinely need a proxy.
            proxy = os.environ.get("KEVRAI_MODELSCOPE_PROXY", "").strip() or None
            self._client = httpx.AsyncClient(
                timeout=timeout_for("search"),
                follow_redirects=True,
                trust_env=False,
                proxy=proxy,
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
        if path.startswith(("http://", "https://")):
            url = path
        else:
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

    @staticmethod
    def _parse_org(obj: Any) -> tuple[str, str, str]:
        """Return ``(owner, full_name, avatar)`` from an upstream ``Organization``.

        **Must tolerate dict / str / None.** Verified live shapes:
        ``{"Name": "Qwen", "FullName": "千问", "Avatar": "https://…"}`` on the
        detail endpoint, but a bare string on some older payloads and ``None``
        on user-uploaded repos that belong to no org. Getting this wrong is a
        hard crash inside the normalizer, which would take the whole page down.
        """
        if isinstance(obj, Mapping):
            owner = clamp_str(
                pick(obj, "Name", "name", "FullName", "fullName", default="") or "", 128
            ).strip()
            full = clamp_str(
                pick(obj, "FullName", "fullName", default="") or "", 128
            ).strip()
            avatar = clamp_str(
                pick(obj, "Avatar", "avatar", "Logo", "logo", default="") or "", 512
            ).strip()
            # Never emit a non-http avatar into the renderer's <img src>.
            if avatar and not avatar.startswith(("http://", "https://")):
                avatar = ""
            return owner, full, avatar
        if isinstance(obj, str):
            return obj.strip()[:128], "", ""
        return "", "", ""

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

        # v2.9.0 — organisation ("公司"), the single source the old code threw
        # away entirely. `Organization` may be a dict, a bare string, or None.
        owner, owner_full, owner_avatar = self._parse_org(
            pick(obj, "Organization", "organization", default=None)
        )
        if not owner:
            # User repos carry the namespace in `Path` (verified live: the
            # namespace and the org name are the same value for org-owned repos).
            owner = namespace

        chinese_name = clamp_str(
            pick(obj, "ChineseName", "chineseName", default="") or "", 200
        ).strip()
        frameworks = as_str_list(pick(obj, "Frameworks", "frameworks", default=[]), limit=16)
        libraries = as_str_list(pick(obj, "Libraries", "libraries", default=[]), limit=16)
        architectures = as_str_list(
            pick(obj, "Architectures", "architectures", default=[]), limit=16
        )
        # `Library` is the *primary* framework; 魔搭 has no dedicated field for
        # it, so the first non-empty entry is used (verified live: Qwen3-8B
        # ships `Frameworks: ["pytorch"]`, `Libraries: ["pytorch", ...]`).
        library = next(iter(frameworks or libraries), "")

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
            # 魔搭 `Description` is empty on BOTH the list and detail endpoints
            # (verified live); the real prose lives in `ReadMeContent` and is
            # pulled by `detail()` below. The list page gets the Chinese name as
            # a useful one-line stand-in rather than a blank row.
            description=clamp_str(
                pick(obj, "Description", "description", default="") or "", 2000
            ) or chinese_name,
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
            # --- v2.9.0 upstream provenance ---
            owner=owner,
            owner_url=owner_avatar,
            owner_full_name=owner_full,
            nickname=clamp_str(pick(obj, "NickName", "nickName", default="") or "", 128).strip(),
            name_cn=chinese_name,
            library=library,
            frameworks=frameworks,
            architectures=architectures,
            created_at=as_iso(pick(obj, "CreatedTime", "createdTime", default=None)),
            updated_at=as_iso(pick(obj, "LastUpdatedTime", "lastUpdatedTime", default=None)),
            # `IsHot` arrives as int 0/1, `IsNewModel` as a real JSON bool —
            # `as_bool` handles both (verified live).
            is_hot=as_bool(pick(obj, "IsHot", "isHot", default=False)),
            is_new=as_bool(pick(obj, "IsNewModel", "isNewModel", default=False)),
        )

    # --- SourceAdapter surface -------------------------------------------

    def _normalize_openapi(self, obj: Any) -> RemoteModel | None:
        """One OpenAPI ``data.models[]`` item → :class:`RemoteModel`.

        OpenAPI is deliberately lighter than the legacy PascalCase payload:
        ``id`` is already ``owner/name`` and the useful fields are ISO dates,
        integer counters and a flat ``tags`` array. Do not route this through
        ``_normalize`` — that expects ``Path``/``Name``/``StorageSize`` and
        would drop every row.
        """
        if not isinstance(obj, Mapping):
            return None
        repo = clamp_str(pick(obj, "id", default="") or "", 220).strip()
        if not repo or "/" not in repo:
            return None
        namespace, name = repo.split("/", 1)
        if not namespace or not name:
            return None
        if not is_valid_repo(repo):
            repo = repo[:220]

        tasks = pick(obj, "tasks", default=[])
        task_name = ""
        if isinstance(tasks, Sequence) and not isinstance(tasks, (str, bytes)) and tasks:
            task_name = str(tasks[0] or "").strip()
        display_name = clamp_str(
            pick(obj, "display_name", "displayName", default="") or "", 200
        ).strip()
        tags = as_str_list(pick(obj, "tags", default=[]), limit=64)
        category = map_category(
            pipeline_tag=task_name, tasks=tasks,
            tags=tags,
        )
        storage = as_int(pick(obj, "file_size", "fileSize", default=0), 0)
        size_gb = size_gb_from_bytes(storage) if storage > 0 else 0.0
        downloads = as_int(pick(obj, "downloads", default=0), 0)
        likes = as_int(pick(obj, "likes", default=0), 0)

        libraries = [
            t.split(":", 1)[1].strip() for t in tags
            if t.lower().startswith("library:") and t.split(":", 1)[1].strip()
        ]
        library = libraries[0] if libraries else ""
        engines, import_only = infer_engines([], repo=repo, category=category)
        if namespace.lower() in _MNN_NAMESPACES and "mnn" not in engines:
            engines = ["mnn"] + engines
            import_only = False

        return RemoteModel(
            id=synth_id(self.hub, repo),
            name=(name or display_name or repo.rsplit("/", 1)[-1])[:200],
            description=clamp_str(
                pick(obj, "description", default="") or display_name, 2000
            ),
            category=category,
            license=canonical_license(
                pick(obj, "license", default="") or ""
            ),
            size_gb=size_gb,
            size_known=storage > 0,
            engine=engines,
            engine_confidence="low",
            trending=trending_from(downloads, likes),
            repo=repo,
            hardware={},
            tags=build_tags(tasks, [], [], [], tags),
            modality={},
            hub=self.hub,
            downloads=downloads,
            likes=likes,
            revision="master",
            task=task_name,
            import_only=import_only,
            owner=namespace,
            owner_full_name="",
            name_cn=display_name if display_name != name else "",
            library=library,
            frameworks=[library] if library else [],
            architectures=[],
            created_at=as_iso(pick(obj, "created_at", "createdAt", default=None)),
            updated_at=as_iso(
                pick(obj, "last_modified", "lastModified", default=None)
            ),
            is_hot=trending_from(downloads, likes),
            is_new=False,
        )


    async def search(self, spec: SearchSpec, cursor: SourceCursor) -> PageResult:
        """One page of ModelScope results via the OpenAPI list endpoint.

        The legacy ``PUT /api/v1/models`` backend took 6.1 s for a cold qwen
        search in a live measurement; the same query on
        ``GET /openapi/v1/models?search=qwen`` took 0.25 s. The slowness was
        upstream route/index cold-start, not local rate limiting, so the fix is
        to use the API ModelScope itself documents instead of shrinking the
        client timeout.
        """
        if cursor.done:
            return PageResult(items=[], next=None, total=None)
        page = cursor.page if cursor.page else 1
        size = clamp_int(spec.page_size, 30, 1, 100)
        sort = _OPENAPI_SORT.get(spec.sort, "default")
        params: dict[str, Any] = {
            "page_number": max(1, int(page)),
            "page_size": size,
            "sort": sort,
        }
        if spec.q:
            params["search"] = spec.q

        key = f"ms:openapi:search:{spec.signature()}:{page}:{size}:{sort}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        outcome = await self._fetch(
            "GET", self.openapi_base, kind="search", params=params
        )
        if not outcome.ok:
            msg = degraded_message("魔搭 ModelScope", outcome.code)
            self.last_warning = msg
            if outcome.code == "not_found":
                return PageResult(items=[], next=None, total=0)
            return PageResult(items=[], next=None, total=None,
                              degraded=True, code=outcome.code, warning=msg)

        payload = outcome.data
        if not isinstance(payload, Mapping):
            self._breaker.record_fail()
            return PageResult(items=[], next=None, total=None, degraded=True,
                              code="bad_json", warning="魔搭 OpenAPI 返回结构异常")
        if pick(payload, "success", default=False) is False:
            self._breaker.record_fail()
            return PageResult(items=[], next=None, total=None, degraded=True,
                              code="http_error",
                              warning="魔搭 OpenAPI 返回失败")
        data = pick(payload, "data", default=None)
        if not isinstance(data, Mapping):
            self._breaker.record_fail()
            return PageResult(items=[], next=None, total=None, degraded=True,
                              code="bad_json", warning="魔搭 OpenAPI 缺少 data")
        raw_models = pick(data, "models", default=None)
        if not isinstance(raw_models, list):
            self._breaker.record_fail()
            return PageResult(items=[], next=None, total=None, degraded=True,
                              code="bad_json",
                              warning="魔搭 OpenAPI models 字段不是列表")

        total = as_int(pick(data, "total_count", "totalCount", default=0), 0) or None
        items: list[RemoteModel] = []
        for raw in raw_models:
            model = self._normalize_openapi(raw)
            if model is None:
                continue
            if spec.category and model.category != spec.category:
                continue
            if spec.engine and spec.engine not in model.engine:
                continue
            if spec.license and model.license != spec.license:
                continue
            items.append(model)

        if spec.sort == "name_asc":
            items.sort(key=lambda m: (m.name or m.repo).lower())

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
        # `Description` is empty on the detail endpoint too — verified live. The
        # real prose is in `ReadMeContent` (16474 chars for Qwen/Qwen3-8B),
        # which the pre-v2.9.0 code never read, leaving every detail page blank.
        model.description = self._readme_text(data, model.description)
        if not model.description:
            # Some repos ship an empty `ReadMeContent` but do have a README.md
            # in the repo tree — fetch it the same way the file list does.
            model.description = await self._fetch_readme(repo, model.revision)
        # 魔搭 publishes the deployed backends per task, e.g.
        # `BackendSupport.backend_info = {"vllm": {"text": "0.9.2"}, ...}`
        # (verified live). Those names are real engines, so they can upgrade the
        # low-confidence list-stage guess. `infer_engines`-style filtering keeps
        # only ids that actually exist in `catalog/engines.json`, so a name we
        # cannot install (e.g. `lmdeploy`) is ignored rather than shown.
        backend_engines = self._backend_engines(data)
        if backend_engines:
            merged = list(model.engine)
            for e in backend_engines:
                if e not in merged:
                    merged.append(e)
            model.engine = merged
            model.import_only = False
        model.engine_confidence = "high"
        self._cache.set(key, model, ttl=600.0)
        return model

    @staticmethod
    def _backend_engines(data: Any) -> list[str]:
        """Installable engine ids from ``BackendSupport.backend_info``."""
        from .taxonomy import known_engine_ids

        if not isinstance(data, Mapping):
            return []
        bs = pick(data, "BackendSupport", "backendSupport", default=None)
        info = pick(bs, "backend_info", "backendInfo", default=None)
        if not isinstance(info, Mapping):
            return []
        known = known_engine_ids()
        out: list[str] = []
        for key in info:
            name = str(key or "").strip()
            # `deploy_task` is metadata, not an engine.
            if not name or name == "deploy_task":
                continue
            if name in known and name not in out:
                out.append(name)
        return out

    async def _fetch_readme(self, repo: str, revision: str = "") -> str:
        """Fetch + flatten a repo's ``README.md`` via the resolve URL.

        Mirrors :meth:`files`' URL shape so ModelScope keeps working exactly the
        way it already does for MNN downloads. Best-effort: any failure yields
        ``""`` and the detail page renders without a description.
        """
        key = f"ms:readme:{repo}"
        cached = self._cache.get(key)
        if cached is not None:
            return str(cached)
        rev = (revision or "master").strip() or "master"
        try:
            async with self._sem:
                self.requests_made += 1
                resp = await self._client_or_new().get(
                    self.resolve_url(repo, "README.md", rev),
                    headers=self.auth_headers() or None,
                    timeout=timeout_for("detail"),
                    follow_redirects=True,
                )
        except Exception as exc:  # noqa: BLE001 — description is optional
            log.debug("modelscope readme fetch failed for %s: %s", repo, exc)
            return ""
        prose = strip_markdown(resp.text, limit=4000) if resp.status_code == 200 else ""
        self._cache.set(key, prose, ttl=1800.0 if prose else 300.0)
        return prose

    @staticmethod
    def _readme_text(data: Any, fallback: str = "") -> str:
        """Extract a usable description from a ModelScope detail payload."""
        if not isinstance(data, Mapping):
            return fallback
        prose = clamp_str(
            pick(data, "ReadMeContent", "readMeContent", default="") or "", 4000
        ).strip()
        if prose:
            return strip_markdown(prose)
        return fallback

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
