"""FastAPI app — Kevrai Studio sidecar HTTP control plane.

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
    Body,
    FastAPI,
    HTTPException,
    Path as PathParam,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .catalog import (
    Catalog,
    is_host_allowed,
    load_catalog,
    DEFAULT_MODEL_HOSTS,
)
from .engines import (
    EngineManager,
    EngineState,
    ensure_engine,
    list_engines_status,
)
from . import engines as engines_module  # re-export
from .importer import (
    ImportResult,
    import_local,
    list_gguf_files,
    load_local_registry,
    save_local_registry,
    snapshot_progress,
)
from .downloader import Downloader, DownloadRefused, DownloadTask
from .gpu import detect as detect_gpus
from . import mnn_catalog as mnn_market
from . import mnn_runtime
from . import converter as converter_service
from .converter import (
    KIND_HF_TO_MNN,
    KIND_HF_TO_GGUF,
    KIND_HF_TO_ONNX,
    KIND_HF_TO_MLX,
    KIND_ONNX_TO_MNN,
    KIND_TORCH_TO_MNN,
    KIND_MNN_TO_JSON,
)
from .hardware import detect_hardware
from .recommend import recommend as recommend_models
from . import drama as drama_agent
from . import search as search_mod
from .search import SearchQuery, search as run_search, push_recent as search_push_recent
from . import ltx_runtime
from .ltx_runtime import LtxManager, LtxParams, LtxParamError, LtxBusyError, LtxEngineMissing
from .settings import (
    Settings,
    default_data_root,
    default_settings_path,
    ensure_dirs,
    load_settings,
    save_settings,
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
engines_module.is_installed = EngineManager.is_installed  # legacy alias


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

ALLOWED_ORIGINS = [
    "http://localhost:5173",    # vite dev server
    "http://localhost:5174",
    "http://localhost:5175",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "app://.",                   # electron file scheme
    "file://",
]


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
    log.info("kevrai-sidecar started", extra={"version": __version__, "data_root": str(APP_ROOT)})
    try:
        yield
    finally:
        log.info("kevrai-sidecar stopping")
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

app = FastAPI(title="Kevrai Studio Sidecar", version=__version__, lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
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
    try:
        response = await call_next(request)
    except Exception as exc:  # pragma: no cover — propagate
        log.exception("request failed", extra={"path": request.url.path})
        raise
    finally:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "http_access",
            extra={
                "path": request.url.path,
                "method": request.method,
                "status": getattr(locals().get("response"), "status_code", 0),
                "elapsed_ms": elapsed_ms,
            },
        )
        _RidToken.reset(token)
    response.headers["x-request-id"] = rid
    return response


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
    try:
        import contextvars as _cv
        rid = _RidToken.get() if hasattr(_RidToken, "get") else ""
        if rid and not getattr(record, "request_id", None):
            record.request_id = rid
    except Exception:
        pass
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
    return {"local": load_local_registry(MODELS_DIR)}


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
            return e
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
    out = []
    for g in CATALOG.gguf_repos:
        try:
            files = list_gguf_files(g.owner_repo, g.filter)
        except Exception as e:
            files = []
            out.append({"id": g.id, "name": g.name, "owner_repo": g.owner_repo, "error": str(e)})
            continue
        out.append({"id": g.id, "name": g.name, "owner_repo": g.owner_repo, "files": files, "count": len(files)})
    return {"repos": out}


@app.post("/api/models/import")
async def import_model(req: ImportReq, request: Request) -> dict[str, Any]:
    bucket: _TokenBucket = request.app.state.import_bucket
    if not await bucket.take():
        raise HTTPException(
            status_code=429,
            detail="import rate limit exceeded (3/min)",
        )

    src = Path(req.path).expanduser().resolve()
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
    return {"engines": list_engines_status(ENGINES, APP_ROOT)}


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
    """Detect Python / Node / pip packages / installed engines / disk / GPU."""
    from .env import check_status
    settings = _get_settings(request)
    em: EngineManager = request.app.state.engine_manager
    status = await check_status(
        em=em,
        models_dir=Path(settings.resolved_model_dir()),
        engines_dir=Path(settings.resolved_engine_dir()),
        catalog_engines=ENGINES,
    )
    return status.to_dict()


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
    return s.model_dump()


@app.put("/api/settings")
def put_settings(request: Request, body: SettingsUpdate) -> dict[str, Any]:
    s = _get_settings(request).model_copy()
    patch = body.model_dump(exclude_unset=True)
    for k, v in patch.items():
        if v is None:
            continue
        # Theme / HardwareAccel are Literal — let pydantic catch typos
        if hasattr(s, k):
            setattr(s, k, v)
    save_settings(s, request.app.state.settings_path)
    request.app.state.settings = s
    # Update downloader concurrency — only rebuild when the concurrency limit
    # actually changes. Rebuilding unconditionally orphans every in-flight
    # download task (progress/cancel return 404 while the download continues)
    # (BUG-04).
    new_concurrency = max(1, int(s.max_concurrent_downloads))
    old_dl: Downloader | None = getattr(request.app.state, "downloader", None)
    if old_dl is None or old_dl.max_concurrent != new_concurrency:
        request.app.state.downloader = Downloader(max_concurrent=new_concurrency)
    return s.model_dump()


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
    chosen_url: str
    ranking: list[dict[str, Any]] = []
    if auto_pick and len(candidates) > 1:
        from .sources import measure_sources, pick_best
        try:
            ranking = await measure_sources(candidates)
        except Exception as e:
            log.warning("sources.measure failed", extra={"err": str(e)})
            ranking = []
        best = pick_best(ranking)
        if best is None:
            # Probe ran but no candidate is reachable. Fail fast with a clear
            # error instead of spawning a background task that is guaranteed to
            # fail. (If the probe itself crashed, `ranking` is empty and we fall
            # through to the primary URL so a probe glitch can't block a real
            # download.)
            if ranking:
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

    # Validate the chosen URL (permissive: only scheme + host presence).
    try:
        from .downloader import _check_url as _ck
        _ck(chosen_url)
    except DownloadRefused as e:
        raise HTTPException(status_code=400, detail=f"refused url: {e}") from e
    except Exception:
        raise HTTPException(status_code=400, detail="bad url")

    dest_dir = Path(settings.resolved_download_dir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / body.dest_filename
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"dest already exists: {dest}")

    dl: Downloader = _get_downloader(request)
    try:
        task_id = await dl.start(chosen_url, dest, sha256=body.sha256)
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
        "dest": str(dest),
    }


@app.post("/api/sources/measure")
async def sources_measure(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Measure latency + throughput for a list of candidate URLs and return
    a ranking (best first). Does not start any download — read-only probe."""
    from .sources import measure_sources
    urls = body.get("urls") or []
    if not isinstance(urls, list) or not urls:
        raise HTTPException(status_code=400, detail="urls list required")
    urls = [str(u) for u in urls if isinstance(u, str) and u][:32]
    if not urls:
        raise HTTPException(status_code=400, detail="urls list empty")
    ranking = await measure_sources(urls)
    return {"ranking": ranking, "best": ranking[0] if ranking else None}


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
    # 防目录穿越：必须在模型目录内或数据根内
    settings = _get_settings(request)
    allowed_roots = [Path(settings.resolved_model_dir()), APP_ROOT]
    try:
        ok = any(str(d).startswith(str(r)) for r in allowed_roots)
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
    import httpx

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
                headers = {"User-Agent": "KevraiStudio/2.3.0"}
                if resume:
                    headers["Range"] = f"bytes={resume}-"
                with httpx.Client(timeout=(15.0, 120.0), follow_redirects=True) as client:
                    with client.stream("GET", u, headers=headers) as resp:
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
    """列出已下载的 MNN 模型目录（含是否可加载）。"""
    settings = _get_settings(request)
    root = Path(settings.resolved_model_dir()) / "mnn"
    out = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir() and (d / "config.json").is_file():
                out.append({
                    "id": d.name,
                    "dir": str(d),
                    "size_gb": round(
                        sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1e9, 2
                    ),
                })
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
        raise HTTPException(status_code=400, detail=f"输出路径非法：{req.dst}")
    if not str(dst_resolved).startswith(str(data_root)):
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
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
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


class DramaRenderPlanReq(BaseModel):
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


@app.post("/api/drama/brainstorm")
async def drama_brainstorm(req: DramaScriptReq) -> dict[str, Any]:
    """创意头脑风暴：返回引导方向 + 开放式问题（updream 式）。"""
    try:
        res = await asyncio.to_thread(drama_agent.brainstorm, req.topic)
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
            drama_agent.generate_script, req.topic, req.angle, req.answers
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
                    img = _media_to_local(str(url or ""), "image")
                    if img:
                        images.append(img)
                elif ptype == "audio":
                    src = part.get("audio") or part.get("input_audio") or {}
                    if isinstance(src, dict):
                        url = src.get("url") or src.get("data")
                    else:
                        url = src
                    aud_path = _media_to_local(str(url or ""), "audio")
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


def _media_to_local(url: str, kind: str) -> str:
    """把多段 content 里的媒体引用落成本地文件路径（http(s)/data:base64/file:///绝对路径）。"""
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
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                r = client.get(url)
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
            for delta, finished in it:
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
    models = [m.model_dump() for m in CATALOG.models]
    result = run_search(models, sq)
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
    mode: str = "t2v"
    prompt: str = Field(min_length=1, max_length=2000)
    negative_prompt: str = "low quality, blurry, distorted, watermark, text, deformed"
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
