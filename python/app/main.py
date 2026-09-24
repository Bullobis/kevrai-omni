"""FastAPI app — Kevrai Omni sidecar HTTP control plane.

Hardening changes:
    * Structured JSON logging with per-request ``request_id``.
    * CORS restricted to Electron local origins (no ``*``).
    * Simple in-memory token-bucket rate limit on ``/api/models/import``.
    * New endpoints: ``/api/gpu``, ``/api/settings`` (GET/PUT),
      ``/api/download/start`` (POST), ``/api/download/{id}`` (GET),
      ``/api/download/{id}/cancel`` (POST), ``/ws/download/{id}`` (WS).
    * ``GET /api/models/{id}`` validates ``id`` shape (defense in depth).
    * Lifespan handler: create data dirs, instantiate ``EngineManager``
      and a shared ``Downloader`` available via ``app.state``.
    * Backward compat: ``engines.is_installed`` import still works.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi import (
    Path as PathParam,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from . import USER_AGENT, __version__, ltx_runtime, mnn_runtime
from . import converter as converter_service
from . import drama as drama_agent
from . import engines as engines_module  # re-export
from . import mnn_catalog as mnn_market
from . import search as search_mod
from .catalog import (
    Catalog,
    load_catalog,
)
from .converter import (
    KIND_HF_TO_GGUF,
    KIND_HF_TO_MLX,
    KIND_HF_TO_MNN,
    KIND_HF_TO_ONNX,
    KIND_ONNX_TO_MNN,
    KIND_TORCH_TO_MNN,
)
from .downloader import Downloader, DownloadRefused
from .engines import (
    EngineManager,
    apply_engine_update,
    check_engine_updates,
    ensure_engine,
    list_engines_status,
    load_update_cache,
)
from .gpu import detect as detect_gpus
from .hardware import detect_hardware
from .hub import (
    HUB_CURATED,
    BadCursor,
    build_registry,
    cross_source_candidates,
    get_registry,
    reset_registry,
)
from .hub.base import ALL_HUBS, SearchSpec, is_valid_repo
from .hub.paths import UnsafePathError, hub_dest_root, safe_join
from .importer import (
    annotate_registry_engines,
    import_local,
    list_gguf_files,
    load_local_registry,
    snapshot_progress,
)
from .ltx_runtime import LtxBusyError, LtxManager, LtxParamError, LtxParams
from .recommend import recommend as recommend_models
from .search import SearchQuery
from .search import push_recent as search_push_recent
from .search import search as run_search
from .settings import (
    Settings,
    default_data_root,
    default_settings_path,
    ensure_dirs,
    load_settings,
    save_settings,
)
from .source_scheduler import SourceScheduler
from .sources_registry import (
    SourceMeta,
    SourceRegistry,
    normalize_user_mirrors,
)

# ---------------------------------------------------------------------------
# GZip compression (super optimization: shrink JSON responses on the wire)
# ---------------------------------------------------------------------------
try:
    from fastapi.middleware.gzip import GZipMiddleware
    _HAS_GZIP = True
except Exception:  # pragma: no cover
    _HAS_GZIP = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Make engines.is_installed(eng_id) still importable for callers that use it.
engines_module.is_installed = EngineManager.is_installed  # type: ignore[attr-defined]  # legacy alias


def _app_data_root() -> Path:
    return default_data_root()


APP_ROOT = _app_data_root()
APP_ROOT.mkdir(parents=True, exist_ok=True)
MODELS_DIR = APP_ROOT / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
CATALOG_DIR = Path(__file__).resolve().parent.parent.parent / "catalog"

try:
    CATALOG, ENGINES = load_catalog(CATALOG_DIR)
except Exception:
    # If catalog files are missing in dev, fall back to empty.
    CATALOG, ENGINES = Catalog(version="0", models=[]), {}

# P0-1 hardening: CORS is restricted to the Electron app origin plus the known
# vite dev servers. ``file://`` is deliberately REMOVED — it let any local HTML
# page (opened from disk) issue simple POSTs at the control plane. The real
# boundary is the per-request Bearer secret (see ``_auth_middleware`` below);
# CORS only governs browser reads and is kept narrow as defense-in-depth.
ALLOWED_ORIGINS = [
    "http://localhost:5173",    # vite dev server
    "http://localhost:5174",
    "http://localhost:5175",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "app://.",                   # Electron custom-file/protocol origin
]

# Only the headers the Electron main process actually sends. ``*`` was removed
# because it let any local page preflight a wider header surface.
ALLOWED_HEADERS = ["content-type", "x-request-id", "authorization"]

# Paths exempt from the Bearer-secret check. /api/health is polled by the
# Electron main process during bootstrap and carries no state-changing
# capability, so it stays open.
_UNAUTHENTICATED_PATHS = {"/api/health"}


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    """Emit JSON line per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", None)
        if rid:
            payload["request_id"] = rid
        for k, v in record.__dict__.items():
            if k.startswith("_") or k in {
                "name", "msg", "args", "levelname", "levelno",
                "pathname", "filename", "module", "exc_info",
                "exc_text", "stack_info", "lineno", "funcName",
                "created", "msecs", "relativeCreated", "thread",
                "threadName", "processName", "process", "message",
                "request_id",
            }:
                continue
            payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _configure_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_kevrai_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root._kevrai_configured = True  # type: ignore[attr-defined]


_configure_logging()
log = logging.getLogger("kevrai")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    # Data dirs
    settings = load_settings()
    ensure_dirs(settings)
    # Engine manager
    em = EngineManager(APP_ROOT)
    em.ensure_engine_dir()
    app.state.engine_manager = em
    app.state.settings = settings
    app.state.settings_path = default_settings_path()
    # Downloader (concurrency cap from settings)
    app.state.downloader = Downloader(
        max_concurrent=max(1, int(settings.max_concurrent_downloads))
    )
    # Rate-limit state for /api/models/import
    app.state.import_bucket = _TokenBucket(rate=3 / 60.0, capacity=3)
    # Converter toolchain cache (llm-export clone lives under data_root/tools)
    converter_service.configure_tools_dir(APP_ROOT / "tools")
    # LTX-2.5 video generation manager (outputs to data_root/outputs/ltx)
    app.state.ltx = LtxManager(APP_ROOT / "outputs" / "ltx")
    # Dual-source hub registry (HF + ModelScope + curated). Adapters own their
    # httpx clients, so the lifespan must aclose() them on shutdown.
    app.state.hub = build_registry(settings)
    # v2.8.1 — source metadata registry + health (design §2.4).
    app.state.source_registry = _build_source_registry(settings)
    # Aggregated multi-file download jobs (in-process; restart clears them).
    app.state.hub_jobs = {}
    log.info("kevrai-sidecar started", extra={"version": __version__, "data_root": str(APP_ROOT)})
    try:
        yield
    finally:
        log.info("kevrai-sidecar stopping")
        hub = getattr(app.state, "hub", None)
        if hub is not None:
            with contextlib.suppress(Exception):
                await hub.aclose()
        dl: Downloader = app.state.downloader
        with contextlib.suppress(Exception):
            await dl.aclose()


class _TokenBucket:
    """Simple in-memory token bucket for rate-limiting."""

    def __init__(self, rate: float, capacity: float) -> None:
        self.rate = float(rate)
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.ts = time.monotonic()
        self._lock = asyncio.Lock()

    async def take(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.ts
            self.ts = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Kevrai Omni Sidecar", version=__version__, lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=ALLOWED_HEADERS,
)
if _HAS_GZIP:
    app.add_middleware(GZipMiddleware, minimum_size=1024)


# ---------------------------------------------------------------------------
# Request-ID middleware + access log
# ---------------------------------------------------------------------------


@app.middleware("http")
async def _request_id_middleware(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex
    token = _RidToken.set(rid)
    t0 = time.monotonic()
    # 显式初始化：异常路径下 `response` 不会被赋值，此前依赖
    # `locals().get("response")` 会让 status 恒为 0，异常日志失去诊断价值。
    response: Response | None = None
    try:
        response = await call_next(request)
    except Exception:  # pragma: no cover — propagate
        log.exception("request failed", extra={"path": request.url.path})
        raise
    finally:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "http_access",
            extra={
                "path": request.url.path,
                "method": request.method,
                "status": response.status_code if response is not None else 500,
                "elapsed_ms": elapsed_ms,
            },
        )
        _RidToken.reset(token)
    response.headers["x-request-id"] = rid
    return response


# ---------------------------------------------------------------------------
# P0-1: sidecar bearer-secret authentication
# ---------------------------------------------------------------------------
# The sidecar binds 127.0.0.1:17890 but exposes a state-changing control plane
# (download arbitrary URLs, install engines, git-clone skills, load/unload
# models, read settings incl. tokens). CORS only governs *browser reads* and
# does nothing against a local non-browser process, so we require a per-session
# random secret that the Electron main process generates at spawn time and
# passes via ``KEVRAI_SIDECAR_SECRET``. Every request (except /api/health) must
# carry ``Authorization: Bearer <secret>``.


def _sidecar_secret() -> str:
    """Expected bearer secret from the environment.

    Returns "" when the sidecar was started without the env var (e.g. a dev
    running ``uvicorn`` directly). The middleware fails CLOSED in that case
    rather than silently serving an unauthenticated control plane.
    """
    return os.environ.get("KEVRAI_SIDECAR_SECRET", "")


def _extract_bearer(headers: Any) -> str:
    """Pull the token out of an ``Authorization: Bearer <token>`` header.

    Accepts a Starlette ``Headers`` / dict-like (HTTP and WebSocket requests)
    or a raw ASGI header list of ``(bytes, bytes)`` pairs. Returns "" when
    missing or malformed.
    """
    auth = ""
    if hasattr(headers, "get") and callable(headers.get):
        # Starlette Headers is case-insensitive.
        auth = headers.get("authorization", "") or ""
    else:  # ASGI list of (bytes, bytes) pairs
        for pair in headers or []:
            k, v = pair[0], pair[1]
            if k == b"authorization":
                auth = v.decode("latin-1", "ignore")
                break
    if isinstance(auth, bytes):
        auth = auth.decode("latin-1", "ignore")
    auth = str(auth)
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return ""


def _bearer_matches(headers: Any) -> bool:
    expected = _sidecar_secret()
    if not expected:
        return False
    presented = _extract_bearer(headers)
    return bool(presented) and hmac.compare_digest(presented, expected)


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    path = request.url.path
    # CORS preflight must reach CORSMiddleware; never reject OPTIONS here.
    if request.method == "OPTIONS" or path in _UNAUTHENTICATED_PATHS:
        return await call_next(request)

    if not _sidecar_secret():
        # Started directly without Electron's generated secret: fail closed
        # with an explicit 500 instead of silently trusting every local caller.
        return JSONResponse(
            status_code=500,
            content={
                "detail": (
                    "sidecar secret not configured (KEVRAI_SIDECAR_SECRET); "
                    "the control plane is refused until launched by the Electron host"
                )
            },
        )

    if not _bearer_matches(request.headers):
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})

    return await call_next(request)


async def _ws_authorize(websocket: WebSocket) -> bool:
    """Authenticate a WebSocket upgrade (the HTTP middleware does not cover WS).

    Closes with policy-violation (1008) when the bearer secret is missing/wrong
    or when no secret was configured at all. Returns True only when the caller
    may proceed to ``accept()``.
    """
    if not _sidecar_secret() or not _bearer_matches(websocket.headers):
        await websocket.close(code=1008)
        return False
    return True


# Lightweight contextvar for log enrichment
try:
    import contextvars
    _RidToken = contextvars.ContextVar("kevrai_request_id", default="")
except Exception:  # pragma: no cover
    class _RidToken:  # type: ignore[no-redef]
        @staticmethod
        def set(v: str) -> str:
            return v

        @staticmethod
        def reset(v: str) -> None:
            pass


# Patch the json formatter to attach `request_id` if available.
_orig_format = _JsonFormatter.format


def _format_with_rid(self: _JsonFormatter, record: logging.LogRecord) -> str:  # type: ignore[override]
    with contextlib.suppress(Exception):  # best-effort request-id injection
        rid = _RidToken.get() if hasattr(_RidToken, "get") else ""
        if rid and not getattr(record, "request_id", None):
            record.request_id = rid
    return _orig_format(self, record)


_JsonFormatter.format = _format_with_rid  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ImportReq(BaseModel):
    path: str


class EnsureEngineReq(BaseModel):
    engine_id: str


class SettingsUpdate(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_dir: str | None = None
    engine_dir: str | None = None
    download_dir: str | None = None
    theme: str | None = None
    default_engine_id: str | None = None
    hardware_acceleration: str | None = None
    telemetry_enabled: bool | None = None
    max_concurrent_downloads: int | None = None
    max_model_size_gb: int | None = None
    allow_custom_blocked_mirrors: bool | None = None
    debug_http_logs: bool | None = None
    hf_token: str | None = None
    ms_token: str | None = None
    hub_enabled_sources: list[str] | None = None
    hub_page_size: int | None = None
    hub_cache_ttl_s: int | None = None


#: Settings that must never be echoed back in plaintext (P0-1).
_SENSITIVE_SETTINGS: frozenset[str] = frozenset({"hf_token", "ms_token"})


def _redact_settings(settings: Settings) -> dict[str, Any]:
    """Return ``model_dump()`` with secret values replaced by a presence flag.

    The renderer never read these from the Python sidecar (it uses Electron's
    own settings store), so redaction is a zero-frontend-risk hardening.
    ``*_set`` booleans let the UI show "已配置" without leaking the secret.
    """
    data = settings.model_dump()
    for key in _SENSITIVE_SETTINGS:
        value = data.pop(key, "")
        data[f"{key}_set"] = bool(str(value or "").strip())
    return data


class DownloadStartReq(BaseModel):
    """Start a download. Either `url` OR `candidates` (list) must be provided.

    If `candidates` is given, the sidecar speed-tests every URL, picks the
    fastest, and starts the download from that one. `url` is then treated as
    a hint and may be overwritten by the auto-pick.
    """
    url: str = ""
    candidates: list[str] = Field(default_factory=list)
    dest_filename: str
    sha256: str | None = None
    auto_pick: bool = True
    # True when the target repo is gated on HuggingFace (e.g. LTX-2.5):
    # requires settings.hf_token + license acceptance on the repo page.
    gated: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# MNN 仓库直下 repo 参数：owner/name，仅字母数字._-，段不以点开头，禁路径穿越/绝对路径
_REPO_RE = re.compile(r"^(?![.])(?!.*\.\.)[A-Za-z0-9._-]{1,64}/(?![.])[A-Za-z0-9._-]{1,128}$")


def _validate_model_id(model_id: str) -> str:
    if not _MODEL_ID_RE.fullmatch(model_id or ""):
        raise HTTPException(
            status_code=400,
            detail=f"invalid model_id shape: {model_id!r}",
        )
    return model_id


def _get_settings(request: Request) -> Settings:
    s = getattr(request.app.state, "settings", None)
    if s is None:
        s = load_settings()
    return s


def _get_downloader(request: Request) -> Downloader:
    return request.app.state.downloader


def _get_engine_manager(request: Request) -> EngineManager:
    return request.app.state.engine_manager


def _get_hub(request: Request):
    """Return the hub registry, rebuilding it if settings changed mid-flight.

    ``get_registry`` keys off the settings object identity, so a ``PUT
    /api/settings`` (which replaces ``app.state.settings``) transparently
    refreshes tokens/mirrors for the adapters.
    """
    settings = _get_settings(request)
    return get_registry(settings)


def _build_source_registry(settings: Settings) -> SourceRegistry:
    """Construct the source registry from settings (design §2.3.3 / §2.4.7)."""
    persist_path = None
    if bool(getattr(settings, "source_health_persist", True)):
        persist_path = default_data_root() / "source_health.json"
    reg = SourceRegistry(
        persist_path=persist_path,
        cache_ttl_s=float(getattr(settings, "probe_cache_ttl_s", 300) or 300),
        fail_threshold=int(getattr(settings, "cooldown_fail_threshold", 3) or 3),
        cooldown_seconds=float(getattr(settings, "cooldown_seconds", 300.0) or 300.0),
    )
    # Normalise the legacy ``extra_model_mirrors`` into non-preset user sources.
    for meta in normalize_user_mirrors(
        getattr(settings, "extra_model_mirrors", []) or [],
        existing_ids=set(reg.sources.keys()),
    ):
        reg.add_source(meta)
    # User-defined sources from the new ``source_registry`` setting.
    for raw in getattr(settings, "source_registry", []) or []:
        if isinstance(raw, dict):
            meta = SourceMeta.from_dict({**raw, "preset": False})
            if meta.id:
                reg.add_source(meta)
    # GitCode is opt-in only (design §2.6): enable the preset entry when the
    # user has both flipped the switch and supplied a verified template.
    gc = reg.get("gitcode")
    if gc is not None:
        template = str(getattr(settings, "gitcode_repo_api", "") or "").strip()
        gc.enabled = bool(getattr(settings, "gitcode_enabled", False)) and bool(template)
    reg.load()
    return reg


def _get_source_registry(request: Request) -> SourceRegistry:
    reg = getattr(request.app.state, "source_registry", None)
    if reg is None:
        reg = _build_source_registry(_get_settings(request))
        request.app.state.source_registry = reg
    return reg


def _get_scheduler(request: Request) -> SourceScheduler:
    settings = _get_settings(request)
    reg = _get_source_registry(request)
    return SourceScheduler(
        reg,
        ewma_alpha=float(getattr(settings, "ewma_alpha", 0.4) or 0.4),
        size_threshold_mb=float(getattr(settings, "size_profile_threshold_mb", 512) or 512),
        cache_ttl_s=float(getattr(settings, "probe_cache_ttl_s", 300) or 300),
    )


def _hub_error_detail(e: Exception, hub: str = "", repo: str = "") -> dict[str, Any]:
    """Map adapter exceptions to the error-body contract of design §2.6."""
    if isinstance(e, LookupError):
        return {"error": "model_not_found", "hub": hub, "repo": repo}
    if isinstance(e, ValueError):
        return {"error": "bad_repo", "hub": hub, "repo": repo}
    return {"error": "upstream_unavailable", "hub": hub, "repo": repo}


# ---------------------------------------------------------------------------
# Existing routes — DO NOT change response shapes
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__, "models_dir": str(MODELS_DIR), "app_root": str(APP_ROOT)}


@app.get("/api/categories")
def categories() -> dict[str, Any]:
    return {"categories": [
        {"id": "llm",       "label": "大语言模型 / LLM"},
        {"id": "tts",       "label": "语音合成 / TTS"},
        {"id": "video",     "label": "视频生成 / Video"},
        {"id": "image",     "label": "图像生成 / Image"},
        {"id": "superres",  "label": "超分辨率 / Super-Resolution"},
        {"id": "audio",     "label": "音频生成 / Audio"},
        {"id": "3d",        "label": "3D 生成 / 3D"},
        {"id": "vision",    "label": "视觉工具 / Vision"},
        {"id": "pending",   "label": "待官方开源 / Pending"},
        {"id": "other",     "label": "其它 / Other"},
    ]}


@app.get("/api/models")
def list_models(category: str | None = None, q: str | None = None,
                sort: str | None = None) -> dict[str, Any]:
    out = []
    for m in CATALOG.models:
        if category and m.category != category:
            continue
        if q and q.lower() not in (
            m.name + " " + m.description + " " + m.id + " " + m.repo
            + " " + " ".join(m.engine or [])
        ).lower():
            continue
        out.append(m.model_dump())
    if sort == "name":
        out.sort(key=lambda x: str(x.get("name", "")).lower())
    elif sort == "size_desc":
        out.sort(key=lambda x: float(x.get("size_gb") or 0), reverse=True)
    elif sort == "trending":
        out.sort(key=lambda x: bool(x.get("trending")), reverse=True)
    return {"count": len(out), "models": out, "gguf_repos": [g.model_dump() for g in CATALOG.gguf_repos]}


# IMPORTANT: specific routes MUST be declared before the parameterized
# `/api/models/{model_id}` route, otherwise the latter will swallow them.
@app.get("/api/models/local")
def list_local() -> dict[str, Any]:
    # v2.8.0 DIY: entries carry a read-time compatible_engines annotation so
    # the UI can offer a matching runtime (llama.cpp / mnn / diffusers / …).
    return {"local": annotate_registry_engines(load_local_registry(MODELS_DIR))}


@app.get("/api/models/{model_id}")
def model_detail(
    model_id: str = PathParam(...),
) -> dict[str, Any]:
    model_id = _validate_model_id(model_id)
    for m in CATALOG.models:
        if m.id == model_id:
            # v2.3.0 — no synchronous GGUF enumeration here: on cold hits it
            # can take 10s+ and would block the whole detail panel. The UI
            # lazy-loads files via GET /api/models/{id}/gguf-files instead.
            return m.model_dump()
    for e in load_local_registry(MODELS_DIR):
        if e.get("id") == model_id:
            return annotate_registry_engines([e])[0]
    raise HTTPException(status_code=404, detail=f"model {model_id} not found")


@app.get("/api/models/{model_id}/gguf-files")
def model_gguf_files(model_id: str) -> dict[str, Any]:
    """Lazy GGUF file enumeration for a model (called after the panel shows)."""
    model_id = _validate_model_id(model_id)
    for m in CATALOG.models:
        if m.id == model_id:
            if not m.gguf_repo:
                return {"files": [], "count": 0}
            try:
                files = list_gguf_files(m.gguf_repo, "*.gguf")
                return {"files": files, "count": len(files), "repo": m.gguf_repo}
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"GGUF 仓库枚举失败：{e}") from e
    raise HTTPException(status_code=404, detail=f"model {model_id} not found")


@app.get("/api/gguf-repos")
def gguf_repos() -> dict[str, Any]:
    """List all GGUF repos and their files (enumerated live from HF)."""
    # Two entry shapes (success carries `files` + `count`, failure carries
    # `error`), so the element type must be declared — otherwise mypy unifies
    # the list on whichever dict literal it sees first and rejects the other.
    out: list[dict[str, Any]] = []
    for g in CATALOG.gguf_repos:
        try:
            files = list_gguf_files(g.owner_repo, g.filter)
        except Exception as e:
            files = []
            out.append({"id": g.id, "name": g.name, "owner_repo": g.owner_repo, "error": str(e)})
            continue
        out.append({"id": g.id, "name": g.name, "owner_repo": g.owner_repo, "files": files, "count": len(files)})
    return {"repos": out}


#: Extensions that may be imported as a model file or archive (P0-3).
_IMPORT_ALLOWED_EXTS: frozenset[str] = frozenset({
    ".gguf", ".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".onnx",
    ".mnn", ".json", ".zip", ".tar", ".gz", ".tgz", ".txt", ".md", ".yaml", ".yml",
})


@app.post("/api/models/import")
async def import_model(req: ImportReq, request: Request) -> dict[str, Any]:
    bucket: _TokenBucket = request.app.state.import_bucket
    if not await bucket.take():
        raise HTTPException(
            status_code=429,
            detail="import rate limit exceeded (3/min)",
        )

    # P0-3: reject NUL bytes / control chars before touching the filesystem.
    raw = str(req.path or "")
    if not raw or "\x00" in raw or any(ord(c) < 32 for c in raw):
        raise HTTPException(status_code=400, detail="invalid path")

    src = Path(raw).expanduser().resolve()
    try:
        if not src.exists():
            raise HTTPException(status_code=400, detail="path does not exist")
        if src.is_file() and src.suffix.lower() not in _IMPORT_ALLOWED_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported file extension: {src.suffix or '(none)'}",
            )
        # A directory import must not swallow a user data dir; refuse obviously
        # dangerous roots (filesystem root / home) — they are never model dirs.
        if src.is_dir() and (src == Path(src.anchor) or src == Path.home()):
            raise HTTPException(status_code=400, detail="refusing to import this directory")
    except HTTPException:
        raise
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"invalid path: {e}") from e

    settings = _get_settings(request)
    info = import_local(
        src,
        MODELS_DIR,
        max_size_bytes=settings.max_model_size_gb * (1024 ** 3),
    )
    reg = load_local_registry(MODELS_DIR)
    # ``info`` is a dict (legacy compat) with new hardening fields.
    return {
        "ok": True,
        "imported": info,
        "local_count": len(reg),
    }


@app.get("/api/engines")
def engines() -> dict[str, Any]:
    out = list_engines_status(ENGINES, APP_ROOT)
    # v2.4.1 — attach cached update info (no network here; the UI triggers
    # POST /api/engines/check-updates explicitly).
    cache = load_update_cache(APP_ROOT)
    for e in out:
        info = cache.get(e["id"]) or {}
        tag = str(info.get("latest_tag", "") or "")
        e["latest_tag"] = tag
        e["update_available"] = bool(tag) and tag != (e.get("version") or "") and e["installed"]
    return {"engines": out}


class CheckUpdatesReq(BaseModel):
    force: bool = False


@app.post("/api/engines/check-updates")
async def engines_check_updates(req: CheckUpdatesReq) -> dict[str, Any]:
    results = await check_engine_updates(APP_ROOT, ENGINES, force=req.force)
    return {"results": results}


@app.post("/api/engines/update")
def update_engine(req: EnsureEngineReq) -> dict[str, Any]:
    res = apply_engine_update(req.engine_id, ENGINES, APP_ROOT)
    if not res.ok:
        raise HTTPException(status_code=400, detail=res.message)
    return {"ok": True, "result": {"engine_id": res.engine_id, "path": res.path, "message": res.message}}


@app.post("/api/engines/install")
def install_engine(req: EnsureEngineReq) -> dict[str, Any]:
    res = ensure_engine(req.engine_id, CATALOG, ENGINES, APP_ROOT)
    if not res.ok:
        raise HTTPException(status_code=400, detail=res.message)
    return {"ok": True, "result": {"engine_id": res.engine_id, "path": res.path, "message": res.message}}


@app.get("/api/progress")
def progress() -> dict[str, Any]:
    return {"progress": snapshot_progress()}


# ---------------------------------------------------------------------------
# New endpoints
# ---------------------------------------------------------------------------


@app.get("/api/gpu")
async def gpu() -> dict[str, Any]:
    gpus = await detect_gpus()
    return {"gpus": [g.model_dump() for g in gpus], "count": len(gpus)}


# ---------------------------------------------------------------------------
# Environment / dependency management (in-app installer)
# ---------------------------------------------------------------------------


@app.get("/api/env/status")
async def env_status(request: Request) -> dict[str, Any]:
    """Detect Python / Node / pip packages / installed engines / disk / GPU.

    ``check_status`` forks ``pip freeze`` plus several version probes, which
    measured **650 ms on every call**. The UI polls this endpoint, so results
    are cached briefly; ``?refresh=1`` forces a fresh probe.
    """
    from .env import check_status

    force = str(request.query_params.get("refresh", "")).lower() in {"1", "true", "yes"}
    cached = _ENV_STATUS_CACHE.get("data")
    if cached is not None and not force:
        age = time.monotonic() - float(_ENV_STATUS_CACHE.get("ts") or 0.0)
        if age < _ENV_STATUS_TTL_S:
            return cached

    settings = _get_settings(request)
    em: EngineManager = request.app.state.engine_manager
    status = await check_status(
        em=em,
        models_dir=Path(settings.resolved_model_dir()),
        engines_dir=Path(settings.resolved_engine_dir()),
        catalog_engines=ENGINES,
    )
    payload = status.to_dict()
    _ENV_STATUS_CACHE["data"] = payload
    _ENV_STATUS_CACHE["ts"] = time.monotonic()
    return payload


@app.post("/api/env/install")
async def env_install(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Install a pip package on demand (user opted in via the in-app UI)."""
    from .env import InstallError, install_pip_package
    kind = str(body.get("kind") or "").lower()
    if kind != "pip":
        raise HTTPException(status_code=400, detail=f"unsupported kind: {kind!r}")
    name = str(body.get("name") or "").strip()
    version = body.get("version")
    mirrors = body.get("mirrors")
    if not name or not re.match(r"^[A-Za-z0-9._-]{1,128}$", name):
        raise HTTPException(status_code=400, detail="invalid package name")
    if version is not None and not re.match(r"^[A-Za-z0-9_.+!~-]{1,64}$", str(version)):
        raise HTTPException(status_code=400, detail="invalid version")
    if mirrors is not None and (
        not isinstance(mirrors, list)
        or not all(isinstance(m, str) and m.startswith("https://") for m in mirrors)
    ):
        raise HTTPException(status_code=400, detail="mirrors must be list of https urls")
    try:
        result = await asyncio.to_thread(
            install_pip_package,
            name,
            str(version) if version else None,
            mirrors if isinstance(mirrors, list) else None,
        )
    except InstallError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return result


@app.post("/api/env/upgrade")
async def env_upgrade(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    from .env import InstallError, upgrade_pip_package
    name = str(body.get("name") or "").strip()
    mirrors = body.get("mirrors")
    if not name or not re.match(r"^[A-Za-z0-9._-]{1,128}$", name):
        raise HTTPException(status_code=400, detail="invalid package name")
    if mirrors is not None and (
        not isinstance(mirrors, list)
        or not all(isinstance(m, str) and m.startswith("https://") for m in mirrors)
    ):
        raise HTTPException(status_code=400, detail="mirrors must be list of https urls")
    try:
        result = await asyncio.to_thread(
            upgrade_pip_package, name,
            mirrors if isinstance(mirrors, list) else None,
        )
    except InstallError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return result


@app.post("/api/env/install-engine")
async def env_install_engine(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Install an engine from the catalog, using its declared `sources[]` mirrors.

    The sidecar measures each mirror with `/api/sources/measure` and downloads
    from the fastest one — same auto-pick logic as `/api/download/start`.
    """
    eid = str(body.get("id") or "").strip()
    if not eid or not re.match(r"^[A-Za-z0-9._-]{1,128}$", eid):
        raise HTTPException(status_code=400, detail="invalid engine id")
    if eid not in ENGINES:
        raise HTTPException(status_code=404, detail=f"unknown engine: {eid}")
    cat = ENGINES[eid]
    sources: list[str] = list(cat.get("sources") or [])
    plat_url = (cat.get("platforms") or {}).get(platform_key())
    if plat_url:
        sources.append(plat_url)
    if not sources:
        raise HTTPException(status_code=404, detail="engine has no download sources")
    from .sources import measure_sources, pick_best
    ranking = await measure_sources(sources)
    best = pick_best(ranking) or {"url": sources[0]}
    em: EngineManager = request.app.state.engine_manager
    result = await asyncio.to_thread(
        em.install, eid, str(best["url"]), cat.get("version", "")
    )
    return {
        "ok": True,
        "engine_id": eid,
        "source_used": best.get("url"),
        "ranking_top5": ranking[:5],
        "result": result,
    }


def platform_key() -> str:
    if sys.platform.startswith("win"):
        return "windows-x64"
    if sys.platform == "darwin":
        return "darwin-arm64" if os.uname().machine == "arm64" else "darwin-x64"
    return "linux-x64"


@app.get("/api/settings")
def get_settings(request: Request) -> dict[str, Any]:
    s = _get_settings(request)
    # P0-1: never echo secrets back to the renderer.
    return _redact_settings(s)


@app.put("/api/settings")
def put_settings(request: Request, body: SettingsUpdate) -> dict[str, Any]:
    s = _get_settings(request).model_copy()
    patch = body.model_dump(exclude_unset=True)
    try:
        for k, v in patch.items():
            if v is None:
                continue
            # Theme / HardwareAccel are Literal — validate_assignment re-runs
            # the validator on setattr so typos here become a 400, not a
            # silently-persisted garbage value (which previously survived).
            if hasattr(s, k):
                setattr(s, k, v)
    except ValidationError as e:
        locs = ".".join(str(x) for x in e.errors()[0].get("loc", [])) or "value"
        raise HTTPException(
            status_code=400, detail=f"invalid settings value for {locs}"
        ) from e
    save_settings(s, request.app.state.settings_path)
    request.app.state.settings = s
    # Settings identity changed → rebuild the hub registry so new tokens/mirrors
    # take effect immediately without a restart.
    reset_registry()
    # v2.8.1 — rebuild the source registry so new mirrors / GitCode settings apply.
    prev_reg = getattr(request.app.state, "source_registry", None)
    if prev_reg is not None:
        with contextlib.suppress(Exception):
            prev_reg.save()
    request.app.state.source_registry = _build_source_registry(s)
    # Update downloader concurrency — only rebuild when the concurrency limit
    # actually changes. Rebuilding unconditionally orphans every in-flight
    # download task (progress/cancel return 404 while the download continues)
    # (BUG-04).
    new_concurrency = max(1, int(s.max_concurrent_downloads))
    old_dl: Downloader | None = getattr(request.app.state, "downloader", None)
    if old_dl is None or old_dl.max_concurrent != new_concurrency:
        request.app.state.downloader = Downloader(max_concurrent=new_concurrency)
    # P0-1: redact secrets in the PUT echo too.
    return _redact_settings(s)


# ---------------------------------------------------------------------------
# Dual-source hub (v2.8.0) — HuggingFace + ModelScope + curated
# ---------------------------------------------------------------------------
# All routes are NEW (`/api/hub/*`); no existing path or response shape changes.


class HubDownloadReq(BaseModel):
    hub: str
    repo: str
    revision: str = ""
    files: list[str] = Field(default_factory=list)
    auto_pick: bool = True


def _hub_query_spec(
    *,
    q: str,
    sources: str,
    category: str,
    engine: str,
    license_: str,
    sort: str,
    page_size: int,
) -> SearchSpec:
    """Build a normalized :class:`SearchSpec` from raw query params."""
    raw_sources = [s.strip() for s in str(sources or "").split(",") if s.strip()]
    spec = SearchSpec(
        q=str(q or ""),
        category=str(category or ""),
        engine=str(engine or ""),
        license=str(license_ or ""),
        sort=str(sort or "relevance"),
        page_size=int(page_size or 30),
        sources=raw_sources or list(ALL_HUBS),
    )
    return spec.normalized()


@app.get("/api/hub/sources")
def hub_sources(request: Request) -> dict[str, Any]:
    """List available sources plus their circuit/token state (design §2.7)."""
    hub = _get_hub(request)
    sources = hub.health()
    return {"sources": sources, "enabled": hub.enabled_sources()}


@app.get("/api/hub/health")
async def hub_health(request: Request, timeout_s: float = 12.0) -> dict[str, Any]:
    """Probe each remote source's reachability (v2.9.0).

    The market UI calls this once at start-up and **hides** any source the
    user's network cannot reach, instead of rendering an empty section. This is
    a NEW route — ``/api/hub/sources`` keeps its exact previous semantics.

    Always HTTP 200: an unreachable source is data (``online: false``), not an
    error. ``degraded`` is true only when *every* remote source is down and no
    curated entries are available either.
    """
    hub = _get_hub(request)
    try:
        budget = max(1.0, min(float(timeout_s or 12.0), 30.0))
    except (TypeError, ValueError):
        budget = 12.0
    try:
        probed = await hub.probe_sources(timeout_s=budget)
    except Exception as e:  # noqa: BLE001 — health must never 500
        log.warning("hub.probe_sources failed", extra={"err": str(e)})
        return {
            "sources": {}, "enabled": hub.enabled_sources(),
            "online": [], "degraded": False,
            "warning": str(e)[:200],
        }
    sources = probed.get("sources") or {}
    online = [h for h, info in sources.items() if info.get("online")]
    return {
        "sources": sources,
        "enabled": probed.get("enabled") or hub.enabled_sources(),
        "online": online,
        # Curated is local and never fails, so the market is only fully degraded
        # if the caller also excluded it.
        "degraded": not online and "curated" not in (probed.get("enabled") or []),
    }


@app.get("/api/hub/search")
async def hub_search(
    request: Request,
    q: str = "",
    sources: str = "curated,hf,modelscope",
    category: str = "",
    engine: str = "",
    license: str = "",
    sort: str = "relevance",
    page_size: int = 30,
    cursor: str = "",
    strict: int = 0,
) -> dict[str, Any]:
    """Merged dual-source search with cursor pagination (design §2.3).

    Never returns 5xx for an upstream failure: a broken source is reported in
    ``warnings`` with ``degraded: true`` and HTTP 200 (design §2.6). Pass
    ``strict=1`` to get a 502 instead (for diagnostics/tests).
    """
    t0 = time.monotonic()
    spec = _hub_query_spec(
        q=q, sources=sources, category=category, engine=engine,
        license_=license, sort=sort, page_size=page_size,
    )
    hub = _get_hub(request)
    try:
        page = await hub.search(spec, cursor)
    except BadCursor:
        raise HTTPException(status_code=400, detail={"error": "bad_cursor"}) from None
    except Exception as e:  # noqa: BLE001 — degrade, never 500
        log.warning("hub.search failed", extra={"err": str(e)})
        if strict:
            raise HTTPException(
                status_code=502,
                detail={"error": "upstream_unavailable"},
            ) from e
        return {
            "items": [], "next_cursor": "", "has_more": False,
            "page_size": spec.page_size, "counts": {},
            "counts_note": "远程总数为上游估算值，非精确去重后计数",
            "facets": {"engines": [], "licenses": [], "categories": [], "sizes": []},
            "facets_scope": "curated_full" if spec.sources == [HUB_CURATED] else "loaded",
            "degraded": True,
            "warnings": [{"hub": "", "code": "exception", "message": str(e)[:200]}],
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }

    if strict and page.degraded and not page.items:
        raise HTTPException(
            status_code=502,
            detail={"error": "upstream_unavailable", "warnings": page.warnings},
        )

    items = [m.to_dict() for m in page.items]
    facets = _hub_facets(page.items)
    return {
        "items": items,
        "next_cursor": page.next_cursor,
        "has_more": page.has_more,
        "page_size": spec.page_size,
        "counts": page.counts,
        "counts_note": "远程总数为上游估算值，非精确去重后计数",
        "facets": facets,
        "facets_scope": "curated_full" if spec.sources == [HUB_CURATED] else "loaded",
        "degraded": page.degraded,
        "warnings": page.warnings,
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
    }


def _hub_facets(items: list[Any]) -> dict[str, Any]:
    """Facets over the *loaded* page range (design §2.3 honesty note)."""
    engines: dict[str, int] = {}
    licenses: dict[str, int] = {}
    categories: dict[str, int] = {}
    for m in items:
        for e in getattr(m, "engine", []) or []:
            engines[str(e)] = engines.get(str(e), 0) + 1
        lic = str(getattr(m, "license", "") or "")
        if lic:
            licenses[lic] = licenses.get(lic, 0) + 1
        cat = str(getattr(m, "category", "") or "")
        if cat:
            categories[cat] = categories.get(cat, 0) + 1
    return {
        "engines": [{"value": k, "count": v} for k, v in sorted(engines.items(), key=lambda kv: -kv[1])],
        "licenses": [{"value": k, "count": v} for k, v in sorted(licenses.items(), key=lambda kv: -kv[1])],
        "categories": [{"value": k, "count": v} for k, v in sorted(categories.items(), key=lambda kv: -kv[1])],
        "sizes": [],
    }


@app.get("/api/hub/model")
async def hub_model(
    request: Request,
    hub: str = HUB_CURATED,
    repo: str = "",
    revision: str = "",
) -> dict[str, Any]:
    """Remote model detail. ``repo`` travels as a query param (design §1.4)."""
    if hub not in ALL_HUBS:
        raise HTTPException(status_code=400, detail={"error": "unknown_hub", "hub": hub})
    if not is_valid_repo(repo):
        raise HTTPException(status_code=400, detail={"error": "bad_repo", "repo": repo})
    reg = _get_hub(request)
    try:
        model = await reg.detail(hub, repo)
    except LookupError:
        raise HTTPException(
            status_code=404,
            detail={"error": "model_not_found", "hub": hub, "repo": repo},
        ) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=_hub_error_detail(e, hub, repo)) from e
    return {"model": model.to_dict(), "also_on": model.also_on, "degraded": False}


@app.get("/api/hub/model/files")
async def hub_model_files(
    request: Request,
    hub: str = HUB_CURATED,
    repo: str = "",
    revision: str = "",
) -> dict[str, Any]:
    """File listing + format-driven engine inference (design §1.7 / §4.1)."""
    if hub not in ALL_HUBS:
        raise HTTPException(status_code=400, detail={"error": "unknown_hub", "hub": hub})
    if not is_valid_repo(repo):
        raise HTTPException(status_code=400, detail={"error": "bad_repo", "repo": repo})
    reg = _get_hub(request)
    try:
        result = await reg.files(hub, repo, revision)
    except LookupError:
        raise HTTPException(
            status_code=404,
            detail={"error": "model_not_found", "hub": hub, "repo": repo},
        ) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=_hub_error_detail(e, hub, repo)) from e
    return result


@app.post("/api/hub/download")
async def hub_download(request: Request, body: HubDownloadReq) -> dict[str, Any]:
    """Multi-file download orchestration (design §2.7 / §4.3).

    Every file gets its own ``task_id`` from the shared :class:`Downloader`;
    progress/cancel/WS reuse the existing endpoints unchanged.
    """
    settings = _get_settings(request)
    if body.hub not in ALL_HUBS:
        raise HTTPException(status_code=400, detail={"error": "unknown_hub", "hub": body.hub})
    if not is_valid_repo(body.repo):
        raise HTTPException(status_code=400, detail={"error": "bad_repo", "repo": body.repo})
    files = [str(f) for f in (body.files or []) if isinstance(f, str) and f]
    if not files:
        raise HTTPException(status_code=400, detail="files required")
    if len(files) > 200:
        raise HTTPException(status_code=400, detail="too many files (max 200)")

    reg = _get_hub(request)
    try:
        listing = await reg.files(body.hub, body.repo, body.revision)
    except LookupError:
        raise HTTPException(
            status_code=404,
            detail={"error": "model_not_found", "hub": body.hub, "repo": body.repo},
        ) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=_hub_error_detail(e, body.hub, body.repo)) from e

    by_path = {str(f.get("path") or ""): f for f in listing.get("files", [])}
    download_root = Path(settings.resolved_download_dir())
    dest_root = hub_dest_root(download_root, body.hub, body.repo)

    # Total size guard (settings.max_model_size_gb).
    total = sum(int(by_path.get(p, {}).get("size", 0) or 0) for p in files)
    max_bytes = max(1, int(settings.max_model_size_gb)) * (1024 ** 3)
    if total > max_bytes:
        raise HTTPException(
            status_code=400,
            detail={"error": "model_too_large", "size_bytes": total, "max_bytes": max_bytes},
        )

    extra_mirrors = getattr(settings, "extra_model_mirrors", []) or []
    also_on = listing.get("also_on") or []
    dl: Downloader = _get_downloader(request)
    jobs: dict[str, Any] = getattr(request.app.state, "hub_jobs", None) or {}
    request.app.state.hub_jobs = jobs
    job_id = uuid.uuid4().hex
    tasks_out: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    task_meta: dict[str, dict[str, Any]] = {}

    for rel in files:
        info = by_path.get(rel)
        if info is None:
            skipped.append({"path": rel, "reason": "not_in_repo"})
            continue
        try:
            dest = safe_join(dest_root, rel)
        except UnsafePathError:
            skipped.append({"path": rel, "reason": "unsafe_path"})
            continue
        if dest.exists() and int(dest.stat().st_size) == int(info.get("size") or 0):
            skipped.append({"path": rel, "reason": "already_exists"})
            continue

        candidates = info.get("candidates") or cross_source_candidates(
            hub=body.hub, repo=body.repo, path=rel, revision=body.revision,
            also_on=also_on, extra_mirrors=extra_mirrors,
        )
        if not candidates:
            candidates = [reg.adapter(body.hub).resolve_url(body.repo, rel, body.revision)]
        candidates = [c for c in candidates if c]

        chosen = candidates[0]
        if body.auto_pick and len(candidates) > 1:
            try:
                from .sources import measure_sources, pick_best
                ranking = await measure_sources(candidates)
                best = pick_best(ranking)
                if best is not None:
                    chosen = str(best["url"])
            except Exception as e:  # noqa: BLE001 — probe glitch must not block
                log.warning("hub download probe failed", extra={"err": str(e)})

        headers: dict[str, str] = {}
        try:
            ad = reg.adapter(body.hub)
            if ad is not None:
                headers = ad.auth_headers() or {}
        except Exception:  # pragma: no cover
            headers = {}
        try:
            task_id = await dl.start(chosen, dest, sha256="", extra_headers=headers or None)
        except Exception as e:  # noqa: BLE001 — one file failing must not abort the job
            skipped.append({"path": rel, "reason": f"start_failed: {str(e)[:80]}"})
            continue
        tasks_out.append({"task_id": task_id, "path": rel, "dest": str(dest)})
        task_meta[task_id] = {"path": rel, "dest": str(dest), "size": int(info.get("size") or 0)}

    jobs[job_id] = {"tasks": task_meta, "dest_root": str(dest_root),
                    "created_at": time.time()}
    # Bound the in-process job table (restart-only lifetime, §2.7).
    if len(jobs) > 200:
        for stale in sorted(jobs, key=lambda k: jobs[k].get("created_at", 0))[:100]:
            jobs.pop(stale, None)
    return {"job_id": job_id, "dest_root": str(dest_root),
            "tasks": tasks_out, "skipped": skipped}


@app.get("/api/hub/jobs/{job_id}")
async def hub_job(request: Request, job_id: str) -> dict[str, Any]:
    """Aggregated progress for a multi-file hub download job."""
    if not _MODEL_ID_RE.fullmatch(job_id or ""):
        raise HTTPException(status_code=400, detail="invalid job_id")
    jobs = getattr(request.app.state, "hub_jobs", {}) or {}
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    dl: Downloader = _get_downloader(request)
    tasks_snap: list[dict[str, Any]] = []
    files_done = files_failed = 0
    bytes_total = bytes_done = 0
    for task_id, meta in job.get("tasks", {}).items():
        snap = await dl.progress(task_id)
        status = "unknown"
        done_b = 0
        if snap is not None:
            status = str(snap.get("status") or snap.get("state") or "running")
            done_b = int(snap.get("downloaded") or snap.get("bytes_done") or 0)
        if status in {"done", "completed", "success"}:
            files_done += 1
        elif status in {"failed", "error", "canceled", "cancelled"}:
            files_failed += 1
        expected = int(meta.get("size") or 0)
        bytes_total += expected
        bytes_done += min(done_b, expected) if expected else done_b
        tasks_snap.append({"task_id": task_id, "path": meta.get("path"),
                           "dest": meta.get("dest"), "status": status,
                           "bytes_done": done_b, "bytes_total": expected})
    files_total = len(job.get("tasks", {}))
    ratio = (bytes_done / bytes_total) if bytes_total else (files_done / files_total if files_total else 0.0)
    if files_failed and files_done + files_failed == files_total:
        status = "failed" if files_done == 0 else "partial"
    elif files_total and files_done == files_total:
        status = "done"
    else:
        status = "running"
    return {
        "job_id": job_id, "files_total": files_total, "files_done": files_done,
        "files_failed": files_failed, "bytes_total": bytes_total,
        "bytes_done": bytes_done, "ratio": round(min(1.0, max(0.0, ratio)), 4),
        "status": status, "dest_root": job.get("dest_root"), "tasks": tasks_snap,
    }


@app.post("/api/download/start")
async def download_start(request: Request, body: DownloadStartReq) -> dict[str, Any]:
    settings = _get_settings(request)
    if not body.dest_filename:
        raise HTTPException(status_code=400, detail="dest_filename required")
    if re.match(r"^[A-Za-z0-9._-]{1,128}$", body.dest_filename) is None:
        raise HTTPException(status_code=400, detail="dest_filename has unsafe characters")

    auto_pick = bool(body.auto_pick) and bool(getattr(settings, "auto_pick_best_source", True))

    # Build the list of candidate URLs. Start from the caller-supplied url /
    # candidates, then auto-expand mirror equivalents (host-swap the primary
    # URL onto every configured mirror) so the picker can compare real sources
    # for the *same* file and choose the fastest reachable one. All mirrors are
    # enabled by default (see Settings.extra_model_mirrors).
    candidates: list[str] = []
    for u in [body.url] + list(body.candidates or []):
        if u and u not in candidates:
            candidates.append(u)
    if auto_pick and candidates:
        from .sources import expand_mirror_candidates
        mirrors = getattr(settings, "extra_model_mirrors", []) or []
        for u in expand_mirror_candidates(candidates[0], mirrors):
            if u not in candidates:
                candidates.append(u)
    if not candidates:
        raise HTTPException(status_code=400, detail="url or candidates required")

    # Auto-pick the best source (measure latency + throughput for the first
    # 64 KiB of each), or fall back to the primary URL.
    #
    # v2.8.1 (design §2.4): the measurement is routed through the
    # SourceScheduler, which adds graded scoring, EWMA history, circuit-breaker
    # cooling (dead sources are skipped without a network call) and a probe
    # cache. `pick_best` semantics are preserved: the first OK entry wins, and
    # an empty-but-nonempty ranking still yields the 422 fast-fail below.
    chosen_url: str
    ranking: list[dict[str, Any]] = []
    skipped: list[str] = []
    if auto_pick and len(candidates) > 1:
        from .sources import pick_best
        try:
            sched = _get_scheduler(request)
            result = await sched.select(
                candidates, file_size=0, purpose="model",
            )
            ranking = result.ranking
            skipped = result.skipped
        except Exception as e:
            log.warning("sources.select failed", extra={"err": str(e)})
            ranking = []
        best = pick_best(ranking)
        if best is None:
            # Probe ran but no candidate is reachable. Fail fast with a clear
            # error instead of spawning a background task that is guaranteed to
            # fail. (If the probe itself crashed, `ranking` is empty and we fall
            # through to the primary URL so a probe glitch can't block a real
            # download.) Dead/ cooling sources are also skipped, so a wholly
            # unreachable set surfaces here rather than after a timeout.
            if ranking or skipped:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "all_sources_unreachable",
                        "message": (
                            f"全部 {len(ranking)} 个候选下载源当前均不可达，"
                            "请检查网络或稍后重试"
                        ),
                        "ranking": ranking[:10],
                    },
                )
            chosen_url = candidates[0]
        else:
            chosen_url = str(best["url"])
    else:
        chosen_url = candidates[0]

    # Validate the chosen URL (permissive: scheme + host presence). When the
    # request is gated we will attach the user's HF Bearer token, so the host
    # allowlist is force-enabled here (P0-2) — the token must never be sent to
    # an arbitrary/caller-controlled URL.
    try:
        from .downloader import _check_url as _ck
        _ck(chosen_url, has_auth_header=bool(body.gated))
    except DownloadRefused as e:
        raise HTTPException(status_code=400, detail=f"refused url: {e}") from e
    except Exception:
        raise HTTPException(status_code=400, detail="bad url") from None

    dest_dir = Path(settings.resolved_download_dir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / body.dest_filename
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"dest already exists: {dest}")

    # Gated repos (e.g. Lightricks/LTX-2.5) need an HF bearer token. The user
    # must ALSO have accepted the model's license agreement on the repo page —
    # a token without acceptance still gets 401/403 from HuggingFace.
    extra_headers: dict[str, str] = {}
    if body.gated:
        token = (getattr(settings, "hf_token", "") or "").strip()
        if not token:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "gated_requires_token",
                    "message": (
                        "该模型仓库为 gated（受控访问）：请先在 HuggingFace 仓库页面"
                        "接受许可协议，然后在「设置」中填入你的 HF Token 后重试。"
                    ),
                },
            )
        extra_headers["Authorization"] = f"Bearer {token}"

    dl: Downloader = _get_downloader(request)
    try:
        task_id = await dl.start(chosen_url, dest, sha256=body.sha256,
                                 extra_headers=extra_headers or None)
    except DownloadRefused as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {
        "task_id": task_id,
        "started_at": time.time(),
        "url": chosen_url,
        "candidates_tried": len(candidates),
        "ranking": ranking[:5],  # top-5 for UI display
        "skipped": skipped[:10],  # v2.8.1 — cooling sources skipped this round
        "dest": str(dest),
    }


@app.post("/api/sources/measure")
async def sources_measure(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Measure latency + throughput for a list of candidate URLs and return
    a ranking (best first). Does not start any download — read-only probe.

    v2.8.1 (design §2.4): routed through the SourceScheduler so the ranking
    carries graded ``score`` + ``source_type`` + ``cooling`` fields and honours
    the probe cache. Pass ``force=true`` to bypass the cache (used by the UI
    "一键重新测速" button).
    """
    urls = body.get("urls") or []
    if not isinstance(urls, list) or not urls:
        raise HTTPException(status_code=400, detail="urls list required")
    urls = [str(u) for u in urls if isinstance(u, str) and u][:32]
    if not urls:
        raise HTTPException(status_code=400, detail="urls list empty")
    force = bool(body.get("force"))
    file_size = 0
    try:
        file_size = int(body.get("file_size") or 0)
    except (TypeError, ValueError):
        file_size = 0
    sched = _get_scheduler(request)
    try:
        result = await sched.select(
            urls, file_size=file_size, purpose="model", force=force,
        )
    except Exception as e:
        log.warning("sources.select failed", extra={"err": str(e)})
        from .sources import measure_sources
        ranking = await measure_sources(urls)
        return {"ranking": ranking, "best": ranking[0] if ranking else None,
                "skipped": [], "from_cache": False}
    ranking = result.ranking
    return {
        "ranking": ranking,
        "best": ranking[0] if ranking else None,
        "skipped": result.skipped,
        "from_cache": result.from_cache,
    }


@app.get("/api/sources/registry")
async def sources_registry(request: Request) -> dict[str, Any]:
    """Return the full source registry: metadata + health for every source."""
    reg = _get_source_registry(request)
    return {"sources": reg.snapshot(), "cooling": _get_scheduler(request).skipped_cooling()}


@app.get("/api/sources/health")
async def sources_health(request: Request) -> dict[str, Any]:
    """Return per-source health (EWMA latency/throughput, success rate, breaker)."""
    reg = _get_source_registry(request)
    health = {sid: h.to_dict() for sid, h in reg.health.items()}
    return {"health": health, "cooling": _get_scheduler(request).skipped_cooling()}


@app.post("/api/sources/lock")
async def sources_lock(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Lock (or unlock) a preferred source id.

    Saving to settings makes the choice durable and visible to
    ``SourceScheduler.select`` (which hoists the locked source to the top).
    Pass ``{"source_id": ""}`` to clear the lock.
    """
    source_id = str(body.get("source_id") or "").strip()
    if source_id:
        reg = _get_source_registry(request)
        enabled_ids = {m.id for m in reg.enabled_sources("")}
        if source_id not in reg.sources or source_id not in enabled_ids:
            raise HTTPException(status_code=404, detail="unknown or disabled source")
    s = _get_settings(request).model_copy()
    s.locked_source = source_id
    save_settings(s, request.app.state.settings_path)
    request.app.state.settings = s
    return {"locked": source_id}


@app.get("/api/download/{task_id}")
async def download_status(request: Request, task_id: str) -> dict[str, Any]:
    dl: Downloader = _get_downloader(request)
    snap = await dl.progress(task_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="task not found")
    return snap


@app.post("/api/download/{task_id}/cancel")
async def download_cancel(request: Request, task_id: str) -> dict[str, Any]:
    dl: Downloader = _get_downloader(request)
    ok = await dl.cancel(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="task not found")
    return {"ok": True, "task_id": task_id}


@app.websocket("/ws/download/{task_id}")
async def ws_download(websocket: WebSocket, task_id: str) -> None:
    """Stream download progress updates over WebSocket.

    On connect, the server sends the current snapshot, then all subsequent
    events from the task queue. Closes when the task reaches a terminal state.
    """
    if not await _ws_authorize(websocket):
        return
    await websocket.accept()
    dl: Downloader = websocket.app.state.downloader
    task = dl.get_task(task_id)
    if task is None:
        await websocket.send_json({"error": "task not found", "task_id": task_id})
        await websocket.close()
        return
    try:
        await websocket.send_json(task.snapshot())
        terminal = {"done", "failed", "cancelled"}
        while True:
            try:
                evt = await asyncio.wait_for(task.queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                # keep-alive heartbeat
                await websocket.send_json({"event": "heartbeat"})
                continue
            await websocket.send_json(evt)
            if evt.get("status") in terminal or evt.get("event") == "done":
                break
    except WebSocketDisconnect:
        pass
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()


# ===========================================================================
# v2.3.0 — 硬件检测 / 智能推荐 / MNN 引擎运行时
# ===========================================================================

# 硬件快照缓存（带宽探测成本高，5 分钟内复用）
_HW_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_HW_CACHE_TTL = 300.0

# /api/env/status 每次都 fork pip freeze 等 4 个子进程（实测恒 650ms）。
# 加一层短 TTL 缓存；?refresh=1 可强制刷新。
_ENV_STATUS_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_ENV_STATUS_TTL_S = 30.0


@app.get("/api/hardware")
async def hardware(request: Request, refresh: int = 0) -> dict[str, Any]:
    """整机硬件快照：CPU / 内存 / GPU / 磁盘 / 带宽 + 综合评分。"""
    settings = _get_settings(request)
    now = time.time()
    if (not refresh and _HW_CACHE["data"]
            and now - _HW_CACHE["ts"] < _HW_CACHE_TTL):
        return {"hardware": _HW_CACHE["data"], "cached": True}
    hw = await detect_hardware(Path(settings.resolved_model_dir()))
    _HW_CACHE["ts"] = now
    _HW_CACHE["data"] = hw
    return {"hardware": hw, "cached": False}


@app.get("/api/recommend")
async def recommend(limit: int = 12, category: str | None = None,
                    refresh: int = 0) -> dict[str, Any]:
    """根据本机硬件推荐可跑的好模型（含官方建议配置对比与理由）。"""
    limit = max(1, min(int(limit or 12), 50))
    if category is not None and category not in {
        c["id"] for c in categories()["categories"]
    }:
        raise HTTPException(status_code=400, detail=f"unknown category: {category!r}")

    now = time.time()
    if not (_HW_CACHE["data"] and now - _HW_CACHE["ts"] < _HW_CACHE_TTL):
        from .settings import load_settings as _ls
        s = _ls()
        _HW_CACHE["data"] = await detect_hardware(Path(s.resolved_model_dir()))
        _HW_CACHE["ts"] = now
    hw = _HW_CACHE["data"]
    recs = recommend_models(
        [m.model_dump() for m in CATALOG.models],
        hw, limit=limit, category=category,
    )
    return {
        "hardware": hw,
        "count": len(recs),
        "recommendations": recs,
    }


class MnnLoadReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_dir: str
    model_name: str = ""


class MnnChatReq(BaseModel):
    prompt: str = Field(min_length=1, max_length=32_000)
    history: list[dict[str, str]] = Field(default_factory=list)
    max_new_tokens: int = 512


@app.get("/api/mnn/models")
def mnn_models() -> dict[str, Any]:
    """MNN 官方预转换模型市场（内置精选清单，离线可用）。"""
    items = mnn_market.market_list()
    return {"count": len(items), "models": items}


@app.get("/api/mnn/models/{entry_id}/files")
def mnn_model_files(entry_id: str) -> dict[str, Any]:
    """动态枚举某个 MNN 模型仓库的文件清单（镜像回退）。"""
    if not _MODEL_ID_RE.fullmatch(entry_id or ""):
        raise HTTPException(status_code=400, detail=f"invalid entry id: {entry_id!r}")
    entry = mnn_market.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown mnn model: {entry_id}")
    try:
        files = mnn_market.list_mnn_files(entry["repo"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"枚举文件失败：{e}") from e
    total = sum(f.get("size", 0) for f in files)
    return {"entry": entry, "files": files, "count": len(files),
            "total_bytes": total}


@app.get("/api/mnn/status")
def mnn_status() -> dict[str, Any]:
    """MNN 运行时状态（引擎可用性 / 当前加载模型 / 下载任务）。"""
    st = mnn_runtime.status()
    st["download"] = _mnn_dl_state()
    return st


@app.post("/api/mnn/load")
async def mnn_load(req: MnnLoadReq, request: Request) -> dict[str, Any]:
    d = Path(req.model_dir).expanduser()
    if not d.is_dir():
        raise HTTPException(status_code=404, detail=f"model dir not found: {req.model_dir}")
    # 防目录穿越：必须在模型目录内或数据根内。
    # 不能用 startswith 比对字符串：`/data/models-evil` 对 `/data/models`
    # 的 startswith 为 True，兄弟目录即可绕过。改为按路径分量判断，
    # 并在 resolve() 之后比较（symlink 也一并挡住）。
    settings = _get_settings(request)
    try:
        d_resolved = d.resolve()
        roots = [
            Path(settings.resolved_model_dir()).resolve(),
            Path(APP_ROOT).resolve(),
        ]
        ok = any(d_resolved.is_relative_to(r) for r in roots)
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(status_code=400, detail="model dir outside allowed roots")
    try:
        res = await asyncio.to_thread(
            mnn_runtime.load_model, d, req.model_name[:100]
        )
        return {"ok": True, "status": res}
    except mnn_runtime.MnnEngineMissing as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"加载失败：{e}") from e


@app.post("/api/mnn/unload")
def mnn_unload() -> dict[str, Any]:
    mnn_runtime.unload_model()
    return {"ok": True, "status": mnn_runtime.status()}


@app.post("/api/mnn/chat")
async def mnn_chat(req: MnnChatReq) -> dict[str, Any]:
    """与已加载的 MNN 模型对话（同步推理，返回完整结果与速度统计）。"""
    if not mnn_runtime.status()["loaded"]:
        raise HTTPException(status_code=409, detail="MNN 模型尚未加载")
    if not (1 <= req.max_new_tokens <= 4096):
        raise HTTPException(status_code=400, detail="max_new_tokens must be 1..4096")
    try:
        res = await asyncio.to_thread(
            mnn_runtime.chat, req.prompt, req.history, req.max_new_tokens
        )
        return {"ok": True, **res}
    except mnn_runtime.MnnEngineMissing as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"推理失败：{e}") from e


# ---------------------------------------------------------------------------
# MNN 模型下载（多文件目录，单任务槽 + 进度轮询）
# ---------------------------------------------------------------------------

_MNN_DL: dict[str, Any] = {
    "active": False,
    "entry_id": "",
    "name": "",
    "files_total": 0,
    "files_done": 0,
    "bytes_total": 0,
    "bytes_done": 0,
    "current_file": "",
    "status": "idle",  # idle | running | done | failed | cancelled
    "error": "",
    "dest": "",
    "cancel": False,
}


def _mnn_dl_state() -> dict[str, Any]:
    return {k: v for k, v in _MNN_DL.items() if k != "cancel"}


class MnnDownloadReq(BaseModel):
    entry_id: str = ""
    repo: str = ""


def _mnn_download_worker(entry: dict[str, Any], dest: Path) -> None:
    """Blocking worker: enumerate files then download each into dest/."""

    _MNN_DL.update({
        "active": True, "status": "running", "error": "",
        "entry_id": entry["id"], "name": entry.get("name", ""),
        "files_done": 0, "bytes_done": 0, "current_file": "",
    })
    try:
        files = mnn_market.list_mnn_files(entry["repo"])
        if not files:
            raise RuntimeError("仓库文件列表为空")
        _MNN_DL["files_total"] = len(files)
        _MNN_DL["bytes_total"] = sum(f.get("size", 0) for f in files)
        for f in files:
            if _MNN_DL["cancel"]:
                _MNN_DL["status"] = "cancelled"
                return
            _mnn_download_one_file(f, dest)
            _MNN_DL["files_done"] += 1
        _MNN_DL["status"] = "done"
        _MNN_DL["current_file"] = ""
    except Exception as e:  # noqa: BLE001
        _MNN_DL["status"] = "failed"
        _MNN_DL["error"] = str(e)
    finally:
        _MNN_DL["active"] = False


def _mnn_download_one_file(f: dict[str, Any], dest: Path) -> None:
    """Download one file with per-mirror retry + .part resume.

    Mirrors derived from the file's own url (host-swap onto every known
    MNN mirror); each mirror gets 2 attempts with backoff. Raises after
    all candidates fail.
    """
    import time as _time

    import httpx

    path = f["path"]
    _MNN_DL["current_file"] = path
    target = dest / path
    target.parent.mkdir(parents=True, exist_ok=True)
    want_size = int(f.get("size", 0))
    if target.exists() and want_size and target.stat().st_size == want_size:
        _MNN_DL["bytes_done"] += want_size
        return

    tmp = target.with_suffix(target.suffix + ".part")
    # Candidates: primary (ModelScope resolve) first, HF mirror fallback.
    urls: list[str] = []
    for u in (f.get("url"), f.get("hf_url")):
        if u and u not in urls:
            urls.append(str(u))

    last_err: Exception | None = None
    for u in urls:
        for attempt in range(2):
            if _MNN_DL["cancel"]:
                _MNN_DL["status"] = "cancelled"
                return
            try:
                resume = tmp.stat().st_size if tmp.exists() else 0
                headers = {"User-Agent": USER_AGENT}
                if resume:
                    headers["Range"] = f"bytes={resume}-"
                with (
                    httpx.Client(
                        timeout=httpx.Timeout(connect=15.0, read=120.0),
                        follow_redirects=True,
                    ) as client,
                    client.stream("GET", u, headers=headers) as resp,
                ):
                    if resp.status_code in (301, 302, 303, 307, 308):
                        resp.raise_for_status()
                    if resume and resp.status_code == 200:
                        # server ignored Range → restart
                        resume = 0
                    resp.raise_for_status()
                    mode = "ab" if resume else "wb"
                    with open(tmp, mode) as fh:
                        for chunk in resp.iter_bytes(65536):
                            if _MNN_DL["cancel"]:
                                _MNN_DL["status"] = "cancelled"
                                return
                            fh.write(chunk)
                            _MNN_DL["bytes_done"] += len(chunk)
                if not want_size or tmp.stat().st_size >= want_size:
                    tmp.replace(target)
                    return
                # size mismatch → treat as retryable
                last_err = RuntimeError(f"大小不符：{tmp.stat().st_size} < {want_size}")
            except Exception as e:  # noqa: BLE001
                last_err = e
                _time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"文件 {path} 下载失败（所有镜像重试均失败）：{last_err}")


@app.post("/api/mnn/download")
async def mnn_download(req: MnnDownloadReq, request: Request) -> dict[str, Any]:
    """开始下载一个 MNN 市场模型到 models/mnn/<entry_id>/。

    支持两种来源：
    - entry_id：mnn_market 市场条目（保持原有行为）
    - repo：直接从 catalog 里 engine 含 mnn 的模型仓库直下，
      按 repo 枚举文件，entry_id 取仓库短名（taobao-mnn/xxx -> xxx）
    """
    if _MNN_DL["active"]:
        raise HTTPException(status_code=409, detail="已有 MNN 模型下载任务进行中")

    if req.entry_id and req.repo:
        raise HTTPException(status_code=400, detail="entry_id 与 repo 只能提供一项")

    if req.entry_id:
        if not _MODEL_ID_RE.fullmatch(req.entry_id or ""):
            raise HTTPException(status_code=400, detail=f"invalid entry id: {req.entry_id!r}")
        entry = mnn_market.get_entry(req.entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"unknown mnn model: {req.entry_id}")
        entry_id = req.entry_id
    elif req.repo:
        repo = req.repo.strip()
        if not _REPO_RE.fullmatch(repo):
            raise HTTPException(status_code=400, detail=f"invalid repo: {req.repo!r}（须为 owner/name 格式）")
        entry = {
            "id": repo.split("/")[-1] or repo,
            "repo": repo,
            "name": repo,
        }
        entry_id = entry["id"]
    else:
        raise HTTPException(status_code=400, detail="entry_id 与 repo 至少提供一项")

    settings = _get_settings(request)
    dest = Path(settings.resolved_model_dir()) / "mnn" / entry_id
    if (dest / "config.json").exists():
        raise HTTPException(status_code=409, detail=f"该模型已下载：{dest}")
    dest.mkdir(parents=True, exist_ok=True)
    _MNN_DL["cancel"] = False
    _MNN_DL["dest"] = str(dest)
    asyncio.get_running_loop().run_in_executor(None, _mnn_download_worker, entry, dest)
    return {"ok": True, "entry_id": entry_id, "repo": req.repo, "dest": str(dest)}


@app.post("/api/mnn/download/cancel")
def mnn_download_cancel() -> dict[str, Any]:
    if not _MNN_DL["active"]:
        raise HTTPException(status_code=404, detail="no active mnn download")
    _MNN_DL["cancel"] = True
    return {"ok": True}


@app.get("/api/mnn/download")
def mnn_download_status() -> dict[str, Any]:
    return _mnn_dl_state()


@app.get("/api/mnn/local")
def mnn_local(request: Request) -> dict[str, Any]:
    """列出已下载的 MNN 模型目录（含是否可加载）。

    v2.8.0 DIY: 用户通过「本地导入」进来的 MNN 目录模型（config.json +
    *.mnn，登记在 _local.json 注册表中）也会出现在这里，与 mnn/ 子目录下
    官方预转换模型合并展示；按绝对路径去重，导入模型带 "diy": true 标记。
    """
    settings = _get_settings(request)
    root = Path(settings.resolved_model_dir()) / "mnn"
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(d: Path, *, diy: bool) -> None:
        real = str(d.resolve())
        if real in seen:
            return
        seen.add(real)
        out.append({
            "id": d.name,
            "dir": str(d),
            "size_gb": round(
                sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1e9, 2
            ),
            **({"diy": True} if diy else {}),
        })

    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir() and (d / "config.json").is_file():
                _add(d, diy=False)

    # DIY-imported MNN models — the registry lives in MODELS_DIR, the same
    # directory /api/models/import writes to (settings.model_dir overrides
    # only the *download* root, not the import registry).
    for e in annotate_registry_engines(load_local_registry(MODELS_DIR)):
        if "mnn" in (e.get("compatible_engines") or []):
            try:
                _add(Path(e["path"]), diy=True)
            except (OSError, KeyError):
                continue
    return {"models": out, "count": len(out)}


# ---------------------------------------------------------------------------
# 模型格式转换（Model Converter）
# ---------------------------------------------------------------------------

class ConvertStartReq(BaseModel):
    kind: str = Field(..., description="转换类型: hf-to-mnn-llm / hf-to-gguf / hf-to-onnx / hf-to-mlx / onnx-to-mnn / torch-to-mnn")
    src: str = Field(..., max_length=4096, description="源模型目录（HF）或文件（onnx/pt）绝对路径")
    dst: str = Field(..., max_length=4096, description="输出目录（hf-to-mnn-llm/hf-to-gguf/hf-to-onnx/hf-to-mlx）或 .mnn 文件（onnx/torch）绝对路径")
    arch: str = Field(default="", max_length=64, description="hf-to-mnn-llm 的模型架构（qwen/qwen3/llama3/...），留空自动识别")
    quant_bit: int = Field(default=4, ge=1, le=8, description="权重量化位数")
    lm_quant_bit: int | None = Field(default=None, ge=1, le=16, description="LM 头量化位数，留空默认跟随 quant_bit")
    quant_block: int = Field(default=0, ge=0, le=128, description="量化块大小，0=通道级")
    visual_quant_bit: int | None = Field(default=None, ge=1, le=16, description="多模态视觉编码器量化位数")
    biz_code: str = Field(default="kevrai", max_length=128)
    outtype: str = Field(default="f16", max_length=16, description="hf-to-gguf 输出精度：f16/f32/bf16")
    task: str = Field(default="", max_length=64, description="hf-to-onnx 导出任务类型，留空自动推断")
    quantize: bool = Field(default=True, description="hf-to-mlx 是否量化（默认 4bit）")
    weight_quant_bits: int | None = Field(default=None, ge=1, le=8, description="onnx-to-mnn 权重量化位数（如 4/8）")
    weight_quant_block: int | None = Field(default=None, ge=0, le=128, description="onnx-to-mnn 权重量化块大小")


@app.get("/api/convert/capabilities")
def convert_capabilities() -> dict[str, Any]:
    """各引擎支持的模型格式与可用的转换路径。"""
    return {
        "converters": [
            {
                "kind": KIND_HF_TO_MNN,
                "from": ["huggingface safetensors / pytorch 原始权重目录"],
                "to": "MNN-LLM 模型目录（config.json + 权重）",
                "target_engine": "mnn",
                "tool": "llmexport（官方独立 pip 包，或 alibaba/MNN transformers/llm/export/llmexport.py）",
                "options": ["arch", "quant_bit", "lm_quant_bit", "quant_block", "visual_quant_bit"],
                "doc": "https://github.com/alibaba/MNN/blob/master/transformers/README.md",
            },
            {
                "kind": KIND_HF_TO_GGUF,
                "from": ["huggingface safetensors / pytorch 原始权重目录"],
                "to": "*.gguf（llama.cpp / ollama 可直接加载）",
                "target_engine": "llama.cpp / ollama",
                "tool": "llama.cpp convert_hf_to_gguf.py",
                "options": ["outtype"],
                "doc": "https://github.com/ggml-org/llama.cpp/blob/master/convert_hf_to_gguf.py",
            },
            {
                "kind": KIND_HF_TO_ONNX,
                "from": ["huggingface safetensors / pytorch 原始权重目录"],
                "to": "ONNX 模型目录（onnxruntime 可直接加载）",
                "target_engine": "onnxruntime",
                "tool": "optimum-cli export onnx",
                "options": ["task"],
                "doc": "https://huggingface.co/docs/optimum/onnx/usage_guides/export_a_model",
            },
            {
                "kind": KIND_HF_TO_MLX,
                "from": ["huggingface safetensors / pytorch 原始权重目录"],
                "to": "MLX 模型目录（Apple Silicon）",
                "target_engine": "mlx",
                "tool": "python -m mlx_lm.convert",
                "options": ["quantize"],
                "doc": "https://huggingface.co/docs/hub/mlx",
            },
            {
                "kind": KIND_ONNX_TO_MNN,
                "from": ["onnx (*.onnx)"],
                "to": "*.mnn",
                "target_engine": "mnn",
                "tool": "MNNConvert（pymnn 或 MNN 源码编译）",
                "options": ["biz_code", "weight_quant_bits", "weight_quant_block"],
                "doc": "https://github.com/alibaba/MNN/blob/master/tools/converter/README.md",
            },
            {
                "kind": KIND_TORCH_TO_MNN,
                "from": ["torchscript (*.pt / *.torchscript)"],
                "to": "*.mnn",
                "target_engine": "mnn",
                "tool": "MNNConvert（pymnn 或 MNN 源码编译）",
                "options": ["biz_code"],
                "doc": "https://github.com/alibaba/MNN/blob/master/tools/converter/README.md",
            },
        ],
        "note": "MNN-LLM 目录可直接被 /api/mnn/load 加载；*.gguf 供 llama.cpp/ollama；*.onnx 供 onnxruntime；*.mnn 供 MNN C++/Python 推理使用。",
    }


@app.post("/api/convert/start")
async def convert_start(req: ConvertStartReq, request: Request) -> dict[str, Any]:
    """发起模型格式转换任务（单飞）。"""
    src = Path(req.src).expanduser()
    if not src.exists():
        raise HTTPException(status_code=400, detail=f"源路径不存在：{src}")

    # 输出路径落盘前防护：禁止越出数据根目录
    settings = _get_settings(request)
    data_root = settings.resolved_model_dir().resolve()
    dst = Path(req.dst).expanduser()
    try:
        dst_resolved = dst.resolve()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"输出路径非法：{req.dst}") from None
    # 同上：startswith 会被兄弟目录绕过（models-evil vs models），
    # 改用路径分量判断。
    if not dst_resolved.is_relative_to(data_root):
        raise HTTPException(
            status_code=400,
            detail=f"输出路径必须位于模型目录内：{data_root}",
        )

    options: dict[str, Any] = {
        "arch": req.arch.strip(),
        "quant_bit": req.quant_bit,
        "quant_block": req.quant_block,
        "biz_code": req.biz_code,
        "outtype": req.outtype,
        "task": req.task.strip(),
        "quantize": req.quantize,
    }
    if req.lm_quant_bit is not None:
        options["lm_quant_bit"] = req.lm_quant_bit
    if req.visual_quant_bit is not None:
        options["visual_quant_bit"] = req.visual_quant_bit
    if req.weight_quant_bits is not None:
        options["weight_quant_bits"] = req.weight_quant_bits
    if req.weight_quant_block is not None:
        options["weight_quant_block"] = req.weight_quant_block

    try:
        task = converter_service.start_convert(
            req.kind, str(src), str(dst), options=options,
            loop=asyncio.get_running_loop(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"ok": True, "task_id": task.id}


@app.get("/api/convert/tasks")
def convert_tasks() -> dict[str, Any]:
    tasks = converter_service.list_tasks()
    return {"tasks": tasks, "count": len(tasks), "active": converter_service.active_task()}


@app.get("/api/convert/{task_id}")
def convert_task(task_id: str) -> dict[str, Any]:
    task = converter_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"unknown convert task: {task_id}")
    return task


@app.post("/api/convert/{task_id}/cancel")
def convert_cancel(task_id: str) -> dict[str, Any]:
    if not converter_service.cancel_task(task_id):
        raise HTTPException(status_code=404, detail=f"unknown convert task: {task_id}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# AI 短剧生成 Agent（Drama Agent）
# ---------------------------------------------------------------------------

class DramaScriptReq(BaseModel):
    topic: str = Field(default="", max_length=1000)
    angle: str = Field(default="", max_length=500)
    answers: Any = None
    mode: str = Field(default="micro_film", max_length=40)
    style_anchor: str = Field(default="", max_length=200)


class DramaRenderPlanReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_choices: dict[str, str] = Field(default_factory=dict)


@app.get("/api/drama/options")
def drama_options(request: Request) -> dict[str, Any]:
    """短剧 Agent 各环节可选模型（对话 AI + 图片/3D/音频/TTS/视频/LLM）。"""
    try:
        return drama_agent.drama_options(
            CATALOG,
            mnn_market,
            mnn_runtime.status(),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取短剧选项失败：{e}") from e


@app.get("/api/drama/storycraft")
def drama_storycraft() -> dict[str, Any]:
    """剧作方法论库：两种基调结构、节拍、导演/动画风格锚点、题材库、四段产物。"""
    try:
        return {"ok": True, **drama_agent.storycraft_reference()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取剧作方法论失败：{e}") from e


@app.post("/api/drama/brainstorm")
async def drama_brainstorm(req: DramaScriptReq) -> dict[str, Any]:
    """创意头脑风暴：返回引导方向 + 开放式问题（updream 式）。"""
    try:
        res = await asyncio.to_thread(drama_agent.brainstorm, req.topic, req.mode)
        return {"ok": True, **res}
    except drama_agent.LlmNotReady as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except drama_agent.DramaAgentError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"头脑风暴失败：{e}") from e


@app.post("/api/drama/script")
async def drama_script(req: DramaScriptReq) -> dict[str, Any]:
    """基于创意 + 头脑风暴结论生成结构化剧本。"""
    try:
        script = await asyncio.to_thread(
            drama_agent.generate_script,
            req.topic, req.angle, req.answers, req.mode, req.style_anchor,
        )
        return {"ok": True, "script": script}
    except drama_agent.LlmNotReady as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except drama_agent.DramaAgentError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"剧本生成失败：{e}") from e


@app.post("/api/drama/storyboard")
def drama_storyboard(body: dict[str, Any]) -> dict[str, Any]:
    """剧本 → 分镜表（补齐 3D/TTS/音乐渲染字段）。"""
    script = (body or {}).get("script")
    if not isinstance(script, dict):
        raise HTTPException(status_code=400, detail="缺少 script 对象")
    try:
        sb = drama_agent.build_storyboard(script)
        return {"ok": True, **sb}
    except drama_agent.DramaAgentError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分镜生成失败：{e}") from e


@app.post("/api/drama/render-plan")
def drama_render_plan(body: dict[str, Any]) -> dict[str, Any]:
    """按用户选择的模型为分镜生成逐镜头渲染指令卡。"""
    sb = (body or {}).get("storyboard")
    choices = (body or {}).get("model_choices") or {}
    if not isinstance(sb, dict):
        raise HTTPException(status_code=400, detail="缺少 storyboard 对象")
    if not isinstance(choices, dict):
        raise HTTPException(status_code=400, detail="model_choices 必须是对象")
    try:
        plan = drama_agent.render_plan(sb, choices, CATALOG)
        return {"ok": True, **plan}
    except drama_agent.DramaAgentError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"渲染计划失败：{e}") from e


# ---------------------------------------------------------------------------
# OpenAI 兼容端点（/v1/*）—— 让 OpenClaw 等外部 Agent 直接用 Kevrai 本地模型
# 协议子集：GET /v1/models、POST /v1/chat/completions（文本对话，转发 mnn_runtime）
# ---------------------------------------------------------------------------

class V1ChatReq(BaseModel):
    model: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list, min_length=1)
    max_tokens: int | None = Field(default=None, ge=1, le=4096)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    stream: bool = False

@app.get("/v1/models")
def v1_models() -> dict[str, Any]:
    """OpenAI 兼容模型列表：当前已加载的 MNN 模型 + catalog 中带 mnn 引擎的对话模型。"""
    st = mnn_runtime.status()
    out: list[dict[str, Any]] = []
    if st.get("loaded"):
        out.append({
            "id": st.get("model_name") or "mnn-loaded",
            "object": "model",
            "owned_by": "kevrai-mnn",
            "loaded": True,
        })
    seen = {o["id"] for o in out}
    for m in CATALOG.models:
        if m.category != "llm":
            continue
        if "mnn" not in (m.engine or []):
            continue
        if m.id in seen:
            continue
        out.append({
            "id": m.id,
            "object": "model",
            "owned_by": "kevrai",
            "loaded": False,
            "modality": m.modality,
        })
    return {"object": "list", "data": out}


@app.post("/v1/chat/completions")
async def v1_chat_completions(req: V1ChatReq):
    """OpenAI 兼容对话：转发到当前已加载的 MNN 模型。

    支持：
      * 纯文本 messages（str content）
      * OpenAI 多段 content（list parts：type=text / image_url / audio），
        图片支持 http(s) URL、data:base64、本地绝对路径 / file://；
        音频支持本地绝对路径 / file://
      * stream=True → SSE 流式返回（chat.completion.chunk）
    """
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")

    history: list[dict[str, str]] = []
    prompt = ""
    images: list[str] = []
    audios: list[str] = []
    for msg in req.messages:
        role = str(msg.get("role", "user"))
        content = msg.get("content")
        if isinstance(content, list):
            # OpenAI 多段 content：提取文本 + 图片 + 音频
            text_parts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = str(part.get("type", ""))
                if ptype == "text":
                    text_parts.append(str(part.get("text", "") or ""))
                elif ptype == "image_url":
                    url = part.get("image_url")
                    if isinstance(url, dict):
                        url = url.get("url", "")
                    img = await _media_to_local(str(url or ""), "image")
                    if img:
                        images.append(img)
                elif ptype == "audio":
                    src = part.get("audio") or part.get("input_audio") or {}
                    url = src.get("url") or src.get("data") if isinstance(src, dict) else src
                    aud_path = await _media_to_local(str(url or ""), "audio")
                    if aud_path:
                        audios.append(aud_path)
            content = "\n".join(text_parts) if text_parts else ""
        content = str(content or "").strip()
        if not content:
            continue
        history.append({"role": role if role in ("user", "assistant") else "user", "content": content})

    if not history and not images and not audios:
        raise HTTPException(status_code=400, detail="messages 中没有可用内容")
    prompt = history[-1]["content"] if history else ""
    hist = history[:-1]

    model_id = req.model or (mnn_runtime.status().get("model_name") or "kevrai-mnn")

    if req.stream:
        return _v1_stream(prompt, hist, images, audios, req, model_id)
    return await _v1_once(prompt, hist, images, audios, req, model_id)


async def _media_to_local(url: str, kind: str) -> str:
    """把多段 content 里的媒体引用落成本地文件路径（http(s)/data:base64/file:///绝对路径）。

    注意：本函数是 async —— 它由 ``v1_chat_completions`` 这条纯 async 链路调用，
    此前用同步 ``httpx.Client`` 会在网络慢时**冻结整个 sidecar 最长 60 秒**
    （下载进度、WebSocket、Agent 全部停摆）。改为 AsyncClient。
    """
    url = (url or "").strip()
    if not url:
        return ""
    import base64
    import tempfile
    if url.startswith("data:"):
        try:
            meta, _, b64 = url.partition(",")
            raw = base64.b64decode(b64)
            ext = ".png" if kind == "image" else ".wav"
            mime = meta.split(";")[0].split("/")[-1] if "/" in meta else ""
            if mime in ("jpeg", "jpg"):
                ext = ".jpg"
            elif mime == "webp":
                ext = ".webp"
            elif mime == "mp3":
                ext = ".mp3"
            elif mime == "ogg":
                ext = ".ogg"
            fd, path = tempfile.mkstemp(prefix=f"kevrai-{kind}-", suffix=ext)
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            return path
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"data URL 解码失败: {e}") from e
    if url.startswith("file://"):
        url = url[len("file://"):]
    if url.startswith(("http://", "https://")):
        try:
            import httpx
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                r = await client.get(url)
                r.raise_for_status()
            ext = ".png" if kind == "image" else ".wav"
            ctype = r.headers.get("content-type", "")
            if "jpeg" in ctype:
                ext = ".jpg"
            elif "webp" in ctype:
                ext = ".webp"
            elif "audio" in ctype or "mp3" in ctype:
                ext = ".mp3"
            fd, path = tempfile.mkstemp(prefix=f"kevrai-{kind}-", suffix=ext)
            with os.fdopen(fd, "wb") as f:
                f.write(r.content)
            return path
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"媒体下载失败: {e}") from e
    # 本地绝对路径
    if os.path.exists(url):
        return url
    raise HTTPException(status_code=400, detail=f"{kind} 文件不存在: {url}")


async def _v1_once(prompt: str, hist: list[dict[str, str]], images: list[str],
                   audios: list[str], req: V1ChatReq, model_id: str) -> dict[str, Any]:
    try:
        if images or audios:
            res = await asyncio.to_thread(
                mnn_runtime.chat_multimodal, prompt, hist, req.max_tokens or 512, images, audios
            )
        else:
            res = await asyncio.to_thread(
                mnn_runtime.chat, prompt, hist, req.max_tokens or 512
            )
    except mnn_runtime.MnnEngineMissing as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"推理失败：{e}") from e

    text = res.get("text", "")
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "kevrai": {
            "elapsed_s": res.get("elapsed_s"),
            "speed_cps": res.get("speed_cps"),
            "multimodal": bool(res.get("multimodal")),
        },
    }


def _v1_stream(prompt: str, hist: list[dict[str, str]], images: list[str],
               audios: list[str], req: V1ChatReq, model_id: str) -> StreamingResponse:
    """SSE 流式响应：逐段输出 chat.completion.chunk。"""
    def gen():
        try:
            it = mnn_runtime.chat_stream(prompt, hist, req.max_tokens or 512, images, audios)
            for delta, _finished in it:
                if not delta:
                    continue
                chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_id,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": delta},
                        "finish_reason": None,
                    }],
                }
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            final = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_id,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }],
            }
            yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
        except mnn_runtime.MnnEngineMissing as e:
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'engine_missing'}})}\n\n"
        except RuntimeError as e:
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'runtime_error'}})}\n\n"
        except ValueError as e:
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'invalid_request'}})}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'error': {'message': f'推理失败：{e}', 'type': 'internal_error'}})}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ===========================================================================
# v2.4.0 — Super Search (weighted fuzzy search + facets + history)
# ===========================================================================

_SORT_WHITELIST = {"relevance", "name_asc", "size_desc", "size_asc", "trending"}


# ---------------------------------------------------------------------------
# Search corpus cache
#
# ``CATALOG.models`` is a list of pydantic ModelEntry; ``.model_dump()`` is
# called per-request below. We memoize the dumped list *and* the tokenized
# Corpus keyed on ``CATALOG.version`` so that repeated searches (including
# per-keystroke typeahead) reuse the same Corpus instead of rebuilding it
# for all 121 models on every request. A version bump invalidates both.
# ---------------------------------------------------------------------------

_DUMPED_MODELS_CACHE: dict[str, list[dict[str, Any]]] = {}


def _get_dumped_models() -> list[dict[str, Any]]:
    v = CATALOG.version
    cached = _DUMPED_MODELS_CACHE.get(v)
    if cached is not None:
        return cached
    dumped = [m.model_dump() for m in CATALOG.models]
    _DUMPED_MODELS_CACHE[v] = dumped
    # bound the cache (we expect at most a handful of catalog versions)
    if len(_DUMPED_MODELS_CACHE) > 4:
        _DUMPED_MODELS_CACHE.pop(next(iter(_DUMPED_MODELS_CACHE)))
    return dumped


@app.get("/api/search")
def api_search(
    q: str = "",
    category: str | None = None,
    engine: str | None = None,
    license: str | None = None,
    size_bucket: str | None = None,
    trending: int = 0,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Weighted, typo-tolerant search over the whole catalog with facets."""
    sq = SearchQuery(
        q=(q or "")[:200],
        category=(category or "")[:64],
        engine=(engine or "")[:64],
        license=(license or "")[:128],
        size_bucket=(size_bucket or "")[:32],
        trending_only=bool(trending),
        sort=sort if sort in _SORT_WHITELIST else "relevance",
        page=max(1, int(page or 1)),
        page_size=max(1, min(int(page_size or 50), 200)),
    )
    models = _get_dumped_models()
    result = run_search(models, sq, cache_key=CATALOG.version)
    if q and q.strip():
        search_push_recent(q.strip())
    return result


@app.get("/api/search/recent")
def api_search_recent() -> dict[str, Any]:
    return {"recent": search_mod.recent_searches()}


@app.delete("/api/search/recent")
def api_search_clear_recent() -> dict[str, Any]:
    search_mod.clear_recent()
    return {"ok": True}


# ===========================================================================
# v2.4.0 — LTX-2.5 video generation runtime
# ===========================================================================

def _ltx_manager(request: Request) -> LtxManager:
    mgr = getattr(request.app.state, "ltx", None)
    if mgr is None:
        mgr = LtxManager(APP_ROOT / "outputs" / "ltx")
        request.app.state.ltx = mgr
    return mgr


@app.get("/api/ltx/capabilities")
def ltx_capabilities(request: Request) -> dict[str, Any]:
    cap = ltx_runtime.capabilities()
    cap["outputs_dir"] = str(_ltx_manager(request).output_root)
    return cap


class LtxGenerateReq(BaseModel):
    model_config = {"protected_namespaces": ()}  # model_id 字段合法
    mode: str = "t2v"
    prompt: str = Field(min_length=1, max_length=2000)
    negative_prompt: str = ""  # 默认无负面提示词（用户偏好：不内置）
    model_id: str = "Lightricks/LTX-2.5"
    preset: str = "balanced"
    width: int = 768
    height: int = 432
    num_frames: int = 97
    num_inference_steps: int = 25
    guidance_scale: float = 3.0
    seed: int = -1
    image_path: str = ""
    strength: float = 0.85
    fps: int = 24
    output_format: str = "mp4"
    enable_vae_slicing: bool = True
    enable_model_cpu_offload: bool = False


@app.post("/api/ltx/generate")
def ltx_generate(request: Request, req: LtxGenerateReq) -> dict[str, Any]:
    params = LtxParams(
        mode=req.mode, prompt=req.prompt, negative_prompt=req.negative_prompt,
        model_id=req.model_id[:256], preset=req.preset, width=req.width,
        height=req.height, num_frames=req.num_frames,
        num_inference_steps=req.num_inference_steps, guidance_scale=req.guidance_scale,
        seed=req.seed, image_path=req.image_path, strength=req.strength,
        fps=req.fps, output_format=req.output_format,
        enable_vae_slicing=req.enable_vae_slicing,
        enable_model_cpu_offload=req.enable_model_cpu_offload,
    )
    mgr = _ltx_manager(request)
    try:
        task = mgr.start(params)
    except LtxParamError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except LtxBusyError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"ok": True, "task": task.snapshot()}


@app.get("/api/ltx/tasks")
def ltx_tasks(request: Request) -> dict[str, Any]:
    mgr = _ltx_manager(request)
    return {"tasks": mgr.list_tasks(), "active": mgr.active()}


@app.get("/api/ltx/tasks/{task_id}")
def ltx_task_status(request: Request, task_id: str) -> dict[str, Any]:
    if not _MODEL_ID_RE.fullmatch(task_id or ""):
        raise HTTPException(status_code=400, detail="invalid task id")
    mgr = _ltx_manager(request)
    task = mgr.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task.snapshot()


@app.post("/api/ltx/tasks/{task_id}/cancel")
def ltx_task_cancel(request: Request, task_id: str) -> dict[str, Any]:
    if not _MODEL_ID_RE.fullmatch(task_id or ""):
        raise HTTPException(status_code=400, detail="invalid task id")
    mgr = _ltx_manager(request)
    if not mgr.cancel(task_id):
        raise HTTPException(status_code=404, detail="task not found or already finished")
    return {"ok": True, "task_id": task_id}


@app.get("/api/ltx/outputs")
def ltx_outputs(request: Request) -> dict[str, Any]:
    """List generated video files (newest first)."""
    root = _ltx_manager(request).output_root
    items: list[dict[str, Any]] = []
    if root.is_dir():
        for f in sorted(root.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            if f.suffix.lower() in (".mp4", ".gif", ".webm", ".png"):
                st = f.stat()
                items.append({
                    "name": f.name,
                    "path": str(f),
                    "size_bytes": st.st_size,
                    "mtime": st.st_mtime,
                })
    return {"outputs": items, "count": len(items), "dir": str(root)}


# ===========================================================================
# v2.7.0 — Kevrai Agent (general-purpose AI agent layer)
# ===========================================================================
# Inspired by OpenClaw's agent architecture (gateway + runtime, pluggable
# tools, local memory, model-agnostic routing) but customised for Kevrai
# Omni's local model-management context. The agent runs in the sidecar,
# uses the local MNN LLM for reasoning when available, and falls back to a
# deterministic rule-based mode when no LLM is loaded. Complements the
# existing /v1/* OpenAI-compatible endpoints (which let external agents like
# OpenClaw call Kevrai's local models).

_AGENT_SINGLETON: dict[str, Any] = {}


def _get_agent(request: Request):
    """Lazily build and cache the agent singleton for this sidecar instance."""
    if "agent" in _AGENT_SINGLETON:
        return _AGENT_SINGLETON["agent"]
    from .agent import Agent, AgentMemory, ModelRouter, ToolContext, skill_hub
    from .agent.tools import build_skill_manager

    db_path = APP_ROOT / "agent" / "memory.sqlite3"
    memory = AgentMemory(db_path)
    router = ModelRouter()
    # v2.8.0: pluggable skills; enable/disable state persists next to memory.
    skill_state = APP_ROOT / "agent" / "skills.json"
    # v2.9.0: skills imported through the skill hub are layered on top of the
    # built-in set via ``extra_skills`` — they never mutate BUILTIN_SKILLS.
    try:
        imported = skill_hub.load_imported(skill_hub.default_library_root(APP_ROOT))
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("skill hub load failed: %s", exc)
        imported = []
    skill_manager = build_skill_manager(skill_state, extra_skills=imported)

    hw = _HW_CACHE["data"] or {}
    # NOTE: detect_hardware is async; we must not call it here in this sync
    # helper without awaiting (would store a coroutine instead of a dict).
    # If the cache is empty, the check_hardware tool will detect on first
    # use using a worker thread. The /api/hardware endpoint populates this
    # cache when called by the UI.
    ctx = ToolContext(
        catalog=CATALOG,
        engines_catalog=ENGINES,
        hardware_info=hw,
        memory=memory,
        settings=getattr(request.app.state, "settings", None),
        models_dir=MODELS_DIR,
        app_root=APP_ROOT,
    )
    agent = Agent(memory=memory, router=router, ctx=ctx, skill_manager=skill_manager)
    _AGENT_SINGLETON["agent"] = agent
    return agent


class AgentChatReq(BaseModel):
    message: str = Field(min_length=1, max_length=5000)
    session_id: str = Field(default="default", max_length=128)


@app.get("/api/agent/tools")
def agent_tools(request: Request) -> dict[str, Any]:
    """List all tools available to the agent."""
    agent = _get_agent(request)
    return {"tools": agent.registry.list_tools(), "count": len(agent.registry.list_names())}


@app.get("/api/agent/status")
def agent_status(request: Request) -> dict[str, Any]:
    """Agent runtime status (LLM readiness, model name, tool count)."""
    agent = _get_agent(request)
    ready, model_name = agent.router.is_ready()
    return {
        "llm_ready": ready,
        "model_name": model_name,
        "tool_count": len(agent.registry.list_names()),
        "mode": "llm" if ready else "rule_based",
    }


@app.get("/api/agent/skills")
def agent_skills(request: Request) -> dict[str, Any]:
    """List all pluggable skills with enabled state and bundled tool names."""
    agent = _get_agent(request)
    if agent.skills is None:
        return {"skills": [], "count": 0, "active_count": 0}
    skills = agent.skills.list_skills()
    active = sum(1 for s in skills if s["enabled"])
    return {
        "skills": skills,
        "count": len(skills),
        "active_count": active,
        "active_tool_count": len(agent.registry.list_names()),
    }


class SkillToggleReq(BaseModel):
    enabled: bool


@app.post("/api/agent/skills/reset")
def agent_reset_skills(request: Request) -> dict[str, Any]:
    """Restore default skill enablement and rebuild the registry.

    Declared before the ``{skill_id}`` route so "reset" is not captured as a
    skill id.
    """
    agent = _get_agent(request)
    if agent.skills is None:
        raise HTTPException(status_code=404, detail="skill system unavailable")
    agent.skills.reset()
    agent.reload_skills()
    return {"ok": True, "skills": agent.skills.list_skills(),
            "active_tool_count": len(agent.registry.list_names())}


class SkillHubImportReq(BaseModel):
    path: str = Field("", max_length=4096)


class SkillHubGitReq(BaseModel):
    url: str = Field("", max_length=2048)


def _skill_hub_root() -> Any:
    from .agent import skill_hub

    return skill_hub.default_library_root(APP_ROOT)


@app.get("/api/agent/skill-hub")
def agent_skill_hub_list() -> dict[str, Any]:
    """List skills imported into the local skill hub."""
    from .agent import skill_hub

    items = skill_hub.scan_library(_skill_hub_root())
    return {
        "skills": items,
        "count": len(items),
        "ok_count": sum(1 for i in items if i.get("ok")),
        "builtin_count": len(skill_hub.builtin_skill_ids()),
        "root": str(_skill_hub_root()),
    }


@app.post("/api/agent/skill-hub/import")
def agent_skill_hub_import(req: SkillHubImportReq, request: Request) -> dict[str, Any]:
    """Import a local directory holding a SKILL.md."""
    from .agent import skill_hub

    try:
        result = skill_hub.import_dir(req.path, _skill_hub_root())
    except skill_hub.SkillHubError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _AGENT_SINGLETON.clear()
    return {"ok": True, "imported": [result], "reloaded": True}


@app.post("/api/agent/skill-hub/import-zip")
def agent_skill_hub_import_zip_path(req: SkillHubImportReq) -> dict[str, Any]:
    """Import skills from a zip archive that is **already on disk**.

    Path-based rather than multipart on purpose: the Electron shell hands the
    renderer a native file path, never the bytes, so there is nothing for a
    multipart upload to do here — and accepting one would drag in the
    ``python-multipart`` dependency (Starlette asserts on it at runtime) for a
    code path no client actually uses.
    """
    from .agent import skill_hub

    try:
        results = skill_hub.import_zip(req.path, _skill_hub_root())
    except skill_hub.SkillHubError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _AGENT_SINGLETON.clear()
    return {"ok": True, "imported": results, "count": len(results), "reloaded": True}


@app.post("/api/agent/skill-hub/import-git")
def agent_skill_hub_import_git(req: SkillHubGitReq) -> dict[str, Any]:
    """Shallow-clone a skill repo / plugin marketplace and import its skills."""
    from .agent import skill_hub

    try:
        results = skill_hub.import_git(req.url, _skill_hub_root())
    except skill_hub.SkillHubError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _AGENT_SINGLETON.clear()
    return {"ok": True, "imported": results, "count": len(results), "reloaded": True}


@app.delete("/api/agent/skill-hub/{skill_id}")
def agent_skill_hub_remove(skill_id: str) -> dict[str, Any]:
    """Delete an imported skill. Built-in skills are refused with 403."""
    from .agent import skill_hub

    try:
        result = skill_hub.remove_imported(skill_id, _skill_hub_root())
    except skill_hub.SkillHubError as e:
        if e.code == "builtin_protected":
            raise HTTPException(status_code=403, detail=str(e)) from e
        if e.code in ("not_found", "invalid_skill_id"):
            raise HTTPException(status_code=404, detail=str(e)) from e
        raise HTTPException(status_code=400, detail=str(e)) from e
    _AGENT_SINGLETON.clear()
    return {"ok": True, "removed": result, "reloaded": True}


@app.post("/api/agent/skills/{skill_id}")
def agent_toggle_skill(skill_id: str, req: SkillToggleReq, request: Request) -> dict[str, Any]:
    """Enable or disable a skill, persist it, and rebuild the active registry."""
    agent = _get_agent(request)
    if agent.skills is None:
        raise HTTPException(status_code=404, detail="skill system unavailable")
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", skill_id or ""):
        raise HTTPException(status_code=400, detail="invalid skill_id")
    try:
        spec = agent.skills.set_enabled(skill_id, bool(req.enabled))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown skill: {skill_id}") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # Rebuild the registry the agent dispatches to.
    agent.reload_skills()
    return {
        "ok": True,
        "skill": spec,
        "active_tool_count": len(agent.registry.list_names()),
        "active_tools": agent.registry.list_names(),
    }


@app.post("/api/agent/chat")
async def agent_chat(req: AgentChatReq, request: Request) -> dict[str, Any]:
    """Send a message to the agent and get the full result (non-streaming).

    For real-time streaming of reasoning steps, use the WebSocket endpoint
    ``/ws/agent/{session_id}`` instead.
    """
    agent = _get_agent(request)
    if not agent.ctx.hardware_info:
        with contextlib.suppress(Exception):  # hardware probe is best-effort
            from .settings import load_settings as _ls
            s = _ls()
            # detect_hardware 是 async def，必须 await；否则这里存进去的是
            # coroutine 对象，后续 agent 内部 hw.get(...) 会抛
            # AttributeError 并直接 500（且 try 之外，无法被兜住）。
            agent.ctx.hardware_info = await detect_hardware(Path(s.resolved_model_dir()))
    result = await agent.run(req.message, session_id=req.session_id)
    return {
        "ok": result.success,
        "answer": result.answer,
        "session_id": result.session_id,
        "tools_used": result.tools_used,
        "llm_used": result.llm_used,
        "model_name": result.model_name,
        "duration_ms": result.duration_ms,
        "steps": [
            {
                "iteration": s.iteration,
                "thought": s.thought,
                "action_tool": s.action_tool,
                "action_params": s.action_params,
                "observation_ok": s.observation.get("ok") if s.observation else None,
                "is_final": s.is_final,
            }
            for s in result.steps
        ],
        "error": result.error or "",
    }


@app.get("/api/agent/sessions")
def agent_sessions(request: Request, limit: int = 50) -> dict[str, Any]:
    """List agent conversation sessions (most recent first)."""
    agent = _get_agent(request)
    sessions = agent.memory.list_sessions(limit=max(1, min(limit, 200)))
    return {"sessions": sessions, "count": len(sessions)}


@app.get("/api/agent/sessions/{session_id}/messages")
def agent_session_messages(
    request: Request,
    session_id: str = PathParam(...),
    limit: int = 100,
) -> dict[str, Any]:
    """Get messages for a specific agent session."""
    agent = _get_agent(request)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", session_id or ""):
        raise HTTPException(status_code=400, detail="invalid session_id")
    msgs = agent.memory.get_messages(session_id, limit=max(1, min(limit, 500)))
    return {"session_id": session_id, "messages": msgs, "count": len(msgs)}


@app.delete("/api/agent/sessions/{session_id}")
def agent_delete_session(request: Request, session_id: str = PathParam(...)) -> dict[str, Any]:
    """Delete an agent session and all its messages."""
    agent = _get_agent(request)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", session_id or ""):
        raise HTTPException(status_code=400, detail="invalid session_id")
    ok = agent.memory.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True, "session_id": session_id}


@app.get("/api/agent/preferences")
def agent_get_preferences(request: Request) -> dict[str, Any]:
    """Get all stored agent preferences."""
    agent = _get_agent(request)
    return {"preferences": agent.memory.get_all_preferences()}


class AgentPreferenceReq(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(max_length=2000)


@app.put("/api/agent/preferences")
def agent_set_preference(req: AgentPreferenceReq, request: Request) -> dict[str, Any]:
    """Set (or update) an agent preference."""
    agent = _get_agent(request)
    agent.memory.set_preference(req.key, req.value)
    return {"ok": True, "key": req.key, "value": req.value}


@app.websocket("/ws/agent/{session_id}")
async def ws_agent(websocket: WebSocket, session_id: str) -> None:
    """Stream agent reasoning steps in real-time.

    Protocol:
      Client sends JSON: {"message": "user query"}
      Server sends events:
        {"event": "step", "iteration": N, "thought": "...", "action_tool": "...",
         "action_params": {...}, "observation_ok": true/false}
        {"event": "final", "answer": "...", "tools_used": [...], "duration_ms": N}
        {"event": "error", "message": "..."}
    """
    if not await _ws_authorize(websocket):
        return
    await websocket.accept()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", session_id or ""):
        await websocket.send_json({"event": "error", "message": "invalid session_id"})
        await websocket.close()
        return

    class _FakeReq:
        app = websocket.app
    agent = _get_agent(_FakeReq())  # type: ignore[arg-type]

    try:
        while True:
            data = await websocket.receive_json()
            message = str(data.get("message") or "").strip()
            if not message:
                await websocket.send_json({"event": "error", "message": "empty message"})
                continue
            if len(message) > 5000:
                await websocket.send_json({"event": "error", "message": "message too long (max 5000 chars)"})
                continue

            # 在 async 作用域里取一次运行中的 loop，供回调跨线程调度。
            # 此前在回调内用 asyncio.get_event_loop()：该 API 在 3.12+ 已废弃，
            # 且当回调不在 loop 线程时会抛错，被下面的 except 静默吞掉 →
            # **所有步骤流式事件都会丢失**，用户看不到思考过程。
            _ws_loop = asyncio.get_running_loop()

            _ws_loop_ref = _ws_loop

            def _on_step(step, _loop=_ws_loop_ref):
                # 默认参数把 loop 绑定到定义处，避免闭包晚绑定（B023）：
                # 否则循环下一轮重绑 _ws_loop 时，先前排队的事件可能被
                # 投递到错误的 loop。
                loop_ = _loop

                def _emit(s=step):
                    with contextlib.suppress(Exception):
                        asyncio.ensure_future(websocket.send_json({
                            "event": "step",
                            "iteration": s.iteration,
                            "thought": s.thought,
                            "action_tool": s.action_tool,
                            "action_params": s.action_params,
                            "observation_ok": (s.observation or {}).get("ok") if s.observation else None,
                            "is_final": s.is_final,
                        }))

                with contextlib.suppress(Exception):
                    loop_.call_soon_threadsafe(_emit)

            agent.set_step_callback(_on_step)
            try:
                result = await agent.run(message, session_id=session_id)
                await websocket.send_json({
                    "event": "final",
                    "answer": result.answer,
                    "tools_used": result.tools_used,
                    "llm_used": result.llm_used,
                    "model_name": result.model_name,
                    "duration_ms": result.duration_ms,
                    "success": result.success,
                    "error": result.error or "",
                })
            except Exception as e:
                log.exception("agent websocket run failed")
                await websocket.send_json({"event": "error", "message": str(e)})
            finally:
                agent.set_step_callback(None)
    except WebSocketDisconnect:
        pass
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()
