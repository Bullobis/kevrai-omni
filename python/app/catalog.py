"""Schema & validation for the model/engine catalog.

Hardening additions:
    * `jsonschema`-backed validation at load time.
    * Positive-only host allowlist (extensible; the user can add mirrors).
    * Optional mtime-keyed disk cache (~/.cache/KevraiOmni/catalog.json).
    * No negative blocklist: downloads are source-agnostic; the catalog only
      exposes curated `sources[]` mirrors and the user may add custom ones.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Network policy: POSITIVE allowlist of well-known model/hosting hosts.
# ---------------------------------------------------------------------------
# This is an *advisory* default — the application will not refuse a download
# from a host outside this set, because the user has explicit opt-in mirrors
# and the "best source" picker compares every reachable mirror. To enforce a
# stricter policy, set `Settings.enforce_host_allowlist = True`.

DEFAULT_MODEL_HOSTS: set[str] = {
    # Official
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "release.github.com",
    # Official HF partner mirrors
    "hf-mirror.com",
    # Community/popular mirrors (as many sources as possible)
    "hf-mirror.us",
    "hf-cdn.sufy.com",
    "huggingface.dl.in.tel",
    "hf-cn-mirror.com",
    "modelscope.cn",
    "www.modelscope.cn",
    "aliyuncs.com",
    "oss.aliyuncs.com",
    "mirrors.aliyun.com",
    "mirrors.tuna.tsinghua.edu.cn",
    "mirrors.tencent.com",
    "mirrors.huaweicloud.com",
    "mirrors.cloud.tencent.com",
    "mirror.iscas.ac.cn",
    "mirror.nju.edu.cn",
    "ftp.ub.edu",
    # PyPI / Python ecosystem (for engine deps)
    "pypi.org",
    "files.pythonhosted.org",
    "pypi.tuna.tsinghua.edu.cn",
    "mirrors.aliyun.com",  # (also a pypi mirror)
    # npm / Node ecosystem
    "registry.npmjs.org",
    "registry.npmmirror.com",
    # Other model registries
    "civitai.com",
    "download.civitai.com",
    # GitHub mirror (GitCode, CSDN) — fast inside CN for repo/release/zip
    "gitcode.com",
    "www.gitcode.com",
}

DEFAULT_ENGINE_HOSTS: set[str] = DEFAULT_MODEL_HOSTS | {
    "github.com",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
}

# Backward-compat aliases (older callers/tests imported these names).
ALLOWED_MODEL_HOSTS = DEFAULT_MODEL_HOSTS
ALLOWED_ENGINE_HOSTS = DEFAULT_ENGINE_HOSTS
# Removed in v2.2.0: no host is refused by default. Kept as an empty set
# so older imports don't break.
DEFAULT_BLOCKED_MIRRORS: set[str] = set()


# ---------------------------------------------------------------------------
# Pydantic models (kept for legacy callers and `model_validate` ergonomics)
# ---------------------------------------------------------------------------


class ModelEntry(BaseModel):
    id: str
    category: str
    name: str
    repo: str = ""
    gguf_repo: str = ""
    size_gb: float = 0
    engine: list[str] = Field(default_factory=list)
    license: str = ""
    trending: bool = False
    description: str = ""
    files: list[str] = Field(default_factory=list)
    size_bytes: int = 0
    source: str = ""
    # Multi-source download mirrors (any http(s) URL the user wants). The
    # "best source" picker speed-tests each one and picks the fastest.
    sources: list[str] = Field(default_factory=list)
    primary_url: str = ""
    # v2.3.0 — official recommended hardware config (drives the recommender).
    hardware: dict[str, Any] = Field(default_factory=dict)
    # v2.3.0 — official pre-converted MNN-format repo (taobao-mnn org), when
    # the model can also run on the MNN engine.
    mnn_repo: str = ""
    # v2.4.0 — modality capability annotation:
    #   {"multimodal": bool, "understand": ["image","audio","video",...],
    #    "generate": ["text","image",...], "notes": str}
    modality: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id_safe(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", v or ""):
            raise ValueError(f"unsafe model id: {v!r}")
        return v

    @field_validator("repo", "gguf_repo", "source", "primary_url")
    @classmethod
    def _url_optional(cls, v: str) -> str:
        # No host filtering: any well-formed URL is acceptable.
        return (v or "").strip()

    @field_validator("sources")
    @classmethod
    def _sources_urls(cls, v: list[str]) -> list[str]:
        out = []
        for u in v or []:
            s = str(u).strip()
            if not s:
                continue
            if not re.match(r"^https?://", s, re.I):
                raise ValueError(f"source url must be http(s): {s!r}")
            out.append(s)
        return out


class GGUFRepoEntry(BaseModel):
    id: str
    name: str
    owner_repo: str
    filter: str = "*.gguf"
    note: str = ""
    sources: list[str] = Field(default_factory=list)

    @field_validator("owner_repo")
    @classmethod
    def _check_owner_repo(cls, v: str) -> str:
        v = v.strip()
        if "/" not in v:
            raise ValueError(f"owner_repo must be 'owner/name', got {v!r}")
        return v


class Catalog(BaseModel):
    version: str
    updated: str = ""
    notice: str = ""
    models: list[ModelEntry]
    gguf_repos: list[GGUFRepoEntry] = Field(default_factory=list)
    custom_sources_allowed: bool = True
    categories: list[Any] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# JSON-schema validation (jsonschema)
# ---------------------------------------------------------------------------


_MODEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "category", "name", "repo", "engine", "license", "description"],
    "additionalProperties": True,
    "properties": {
        "id": {"type": "string", "pattern": r"^[A-Za-z0-9._-]{1,128}$"},
        "category": {"type": "string", "minLength": 1, "maxLength": 64},
        "name": {"type": "string", "minLength": 1, "maxLength": 256},
        "repo": {"type": "string"},
        "gguf_repo": {"type": "string"},
        "size_gb": {"type": "number", "minimum": 0, "maximum": 100000},
        "size_bytes": {"type": "integer", "minimum": 0},
        "engine": {
            "type": "array",
            "items": {"type": "string"},
        },
        "license": {"type": "string", "maxLength": 128},
        "trending": {"type": "boolean"},
        "description": {"type": "string"},
        "source": {"type": "string"},
        "files": {"type": "array"},
    },
}

_GGUF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "name", "owner_repo"],
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "owner_repo": {"type": "string", "pattern": r"^[^/]+/[^/]+$"},
        "filter": {"type": "string"},
        "note": {"type": "string"},
    },
}

_ROOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["version", "models"],
    "properties": {
        "version": {"type": "string"},
        "updated": {"type": "string"},
        "notice": {"type": "string"},
        "custom_sources_allowed": {"type": "boolean"},
        "models": {"type": "array", "items": _MODEL_SCHEMA},
        "gguf_repos": {"type": "array", "items": _GGUF_SCHEMA},
    },
}


def validate_catalog(data: Any) -> None:
    """Validate a parsed catalog dict using jsonschema. Raises on failure."""
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return  # fall back to pydantic-level checks only
    if not isinstance(data, dict):
        raise ValueError("catalog root must be an object")
    validator = jsonschema.Draft202012Validator(_ROOT_SCHEMA)
    errors = list(validator.iter_errors(data))
    if not errors:
        return
    msgs = []
    for err in errors[:10]:
        path = "/".join(str(x) for x in err.absolute_path) or "<root>"
        msgs.append(f"{path}: {err.message}")
    raise ValueError(
        "catalog failed schema validation:\n  " + "\n  ".join(msgs)
    )


def _extract_urls_from_text(text: str) -> list[str]:
    """Pull http(s) URLs out of freeform text (informational only; never blocks)."""
    return re.findall(r"https?://[^\s\"'<>]+", text or "")


def _note_url_warnings(data: dict[str, Any]) -> list[str]:
    """Walk the catalog payload for URL-shaped strings inside freeform text.
    Returns a list of human-readable warnings; never raises. This used to be a
    hard block; per the user's request we keep the field walker for visibility
    but no longer refuse a download because of it.
    """
    warnings: list[str] = []

    def _scan_text(s: str, where: str) -> None:
        for url in _extract_urls_from_text(s):
            warnings.append(f"info: url in {where}: {url}")

    notice = data.get("notice")
    if isinstance(notice, str):
        _scan_text(notice, "catalog.notice")
    for m in data.get("models", []) or []:
        if not isinstance(m, dict):
            continue
        for k, v in m.items():
            if k in {"description", "note"} and isinstance(v, str):
                _scan_text(v, f"models.{m.get('id','?')}.{k}")
    for g in data.get("gguf_repos", []) or []:
        if not isinstance(g, dict):
            continue
        for k, v in g.items():
            if k == "note" and isinstance(v, str):
                _scan_text(v, f"gguf_repos.{g.get('id','?')}.{k}")
    return warnings


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def _cache_path() -> Path:
    from .settings import default_cache_root

    return default_cache_root() / "catalog.json"


def _cache_fingerprint(catalog_dir: Path) -> str:
    """Stable hash of all relevant files in `catalog_dir`."""
    h = hashlib.sha256()
    for name in ("models.json", "engines.json"):
        p = catalog_dir / name
        if p.exists():
            st = p.stat()
            h.update(name.encode())
            h.update(str(st.st_size).encode())
            h.update(str(int(st.st_mtime_ns)).encode())
    return h.hexdigest()[:16]


def _maybe_use_cache(catalog_dir: Path, dev_mode: bool) -> dict[str, Any] | None:
    if dev_mode:
        return None
    fp = catalog_dir / "models.json"
    if not fp.exists():
        return None
    cache = _cache_path()
    if not cache.exists():
        return None
    try:
        meta = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict):
        return None
    if meta.get("fingerprint") != _cache_fingerprint(catalog_dir):
        return None
    if not isinstance(meta.get("data"), dict):
        return None
    return meta["data"]


def _write_cache(catalog_dir: Path, data: dict[str, Any]) -> None:
    fp = _cache_path()
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"fingerprint": _cache_fingerprint(catalog_dir), "data": data},
            ensure_ascii=False,
        )
        # Atomic-ish: write + replace
        tmp = fp.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, fp)
    except OSError:
        pass  # best-effort


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------


def load_catalog(
    catalog_dir: Path,
    *,
    dev_mode: bool | None = None,
) -> tuple[Catalog, dict[str, Any]]:
    """Load catalog. Validates schema, enforces URL policy, caches by mtime.

    Args:
        catalog_dir: directory containing `models.json` (and optional `engines.json`).
        dev_mode: if True, bypass cache. If None, inferred from env `KEVRAI_DEV`.
    """
    if dev_mode is None:
        dev_mode = bool(os.environ.get("KEVRAI_DEV"))

    cached = _maybe_use_cache(catalog_dir, dev_mode)
    if cached is not None:
        data = cached
    else:
        models_path = catalog_dir / "models.json"
        if not models_path.exists():
            raise FileNotFoundError(f"models.json not found: {models_path}")
        with models_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("models.json must be a JSON object")
        # Validation
        validate_catalog(data)
        _note_url_warnings(data)
        _write_cache(catalog_dir, data)

    catalog = Catalog.model_validate(data)

    engines_path = catalog_dir / "engines.json"
    engines: dict[str, Any] = {}
    if engines_path.exists():
        with engines_path.open("r", encoding="utf-8") as fh:
            engines_data = json.load(fh)
        engines = {e["id"]: e for e in engines_data.get("engines", [])}

    return catalog, engines


# ---------------------------------------------------------------------------
# URL allowlist helper (unchanged signature for backward compat)
# ---------------------------------------------------------------------------


def is_host_allowed(url: str, allowed_hosts: set[str]) -> bool:
    from urllib.parse import urlparse

    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host in allowed_hosts
