"""Local model import + registry persistence.

Hardening additions:
    * SHA-256 computed for every imported file; stored in the manifest.
    * Idempotent: re-importing the same SHA returns the existing entry.
    * Configurable per-file size cap (default 200 GiB).
    * ``import_mode`` supports ``copy`` (default, hard-link when possible) or
      ``symlink`` (zero-copy on same filesystem).
    * Manifest entries get a derived ``id`` = ``sha256[:16]``.
    * Process-local reentrant locks per ``models_dir`` so concurrent
      ``import_local`` calls don't race the read-modify-write of the registry.
    * PID/thread-stamped tmp file for cross-process rename safety.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

try:
    import xxhash  # type: ignore
    _HAS_XXHASH = True
except ImportError:  # pragma: no cover — optional dep
    _HAS_XXHASH = False

CHUNK_SIZE = 1 << 20  # 1 MiB

ImportMode = Literal["copy", "symlink"]
DEFAULT_MAX_SIZE_GB = 200


@dataclass
class ImportProgress:
    model_id: str
    file: str
    downloaded: int
    total: int

    @property
    def ratio(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.downloaded / self.total


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def sha256_file(path: Path, *, chunk_size: int = CHUNK_SIZE) -> str:
    """Stream SHA-256 of a single file."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(root: Path, *, chunk_size: int = CHUNK_SIZE) -> str:
    """Combined SHA-256 of all files under root (sorted by relative path).

    Filesystems may contain names that include surrogate-escaped bytes (Linux
    ``surrogateescape``); we encode with ``errors="surrogateescape"`` so the
    hash computation never blows up on non-UTF-8 file names.
    """
    h = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file())
    for p in files:
        rel = str(p.relative_to(root)).encode("utf-8", errors="surrogateescape")
        h.update(rel + b"\0")
        fh = p.open("rb")
        try:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        finally:
            fh.close()
    return h.hexdigest()


def file_size_or_tree(root: Path) -> int:
    if root.is_file():
        return root.stat().st_size
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ImportResult:
    """Result of an import — supports BOTH attribute access (``r.path``) and
    dict subscript (``r["path"]``) so legacy and new callers can coexist."""

    id: str
    name: str
    path: str
    sha256: str
    size_bytes: int
    imported_from: str
    mode: ImportMode
    duplicate: bool = False

    # Dict-compat (legacy callers that did info["path"])
    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def keys(self):  # type: ignore[no-untyped-def]
        return (
            "id", "name", "path", "sha256", "size_bytes",
            "imported_from", "mode", "duplicate",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "imported_from": self.imported_from,
            "mode": self.mode,
            "duplicate": self.duplicate,
        }


# ---------------------------------------------------------------------------
# Locking — per-models_dir reentrant, process-local
# ---------------------------------------------------------------------------


_REGISTRY_LOCK = threading.Lock()
_REGISTRY_LOCKS: dict[Path, threading.RLock] = {}


def _per_models_dir_lock(models_dir: Path) -> threading.RLock:
    """Return a per-``models_dir`` reentrant lock so concurrent import calls
    in the same process are serialised. Reentrant so ``import_local`` can
    hold the lock and call ``save_local_registry``."""
    with _REGISTRY_LOCK:
        lock = _REGISTRY_LOCKS.get(models_dir)
        if lock is None:
            lock = threading.RLock()
            _REGISTRY_LOCKS[models_dir] = lock
        return lock


# ---------------------------------------------------------------------------
# Import — v2 hardening
# ---------------------------------------------------------------------------


def _resolve_max_size_bytes(settings_max_gb: int | None) -> int:
    if not settings_max_gb:
        return DEFAULT_MAX_SIZE_GB * (1024 ** 3)
    return max(1, int(settings_max_gb)) * (1024 ** 3)


def import_local(
    src: Path,
    models_dir: Path,
    *,
    mode: ImportMode = "copy",
    max_size_bytes: int | None = None,
) -> "ImportResult":
    """Copy or symlink a local model into the user's models directory.

    Args:
        src: source file or directory to import.
        models_dir: destination directory.
        mode: ``copy`` (default) or ``symlink``.
        max_size_bytes: refuse imports larger than this (bytes).

    Returns an ``ImportResult`` (a dict-compatible dataclass — supports both
    attribute access ``r.path`` and legacy subscript ``r["path"]``) with
    keys: ``name``, ``path``, ``size_bytes``, ``imported_from`` plus new
    ``sha256``, ``id``, ``mode``, ``duplicate``.

    Re-importing an existing SHA returns the existing entry with
    ``duplicate=True``.
    """
    return _import_local_impl(
        src=src,
        models_dir=models_dir,
        mode=mode,
        max_size_bytes=max_size_bytes,
    )


def import_local_struct(
    src: Path,
    models_dir: Path,
    *,
    mode: ImportMode = "copy",
    max_size_bytes: int | None = None,
) -> ImportResult:
    """Typed variant of ``import_local`` — returns the dataclass directly."""
    return _import_local_impl(
        src=src,
        models_dir=models_dir,
        mode=mode,
        max_size_bytes=max_size_bytes,
    )


def _import_local_impl(
    *,
    src: Path,
    models_dir: Path,
    mode: ImportMode,
    max_size_bytes: int | None,
) -> ImportResult:
    if not src.exists():
        raise FileNotFoundError(str(src))

    src = src.expanduser().resolve()
    models_dir = models_dir.expanduser().resolve()
    models_dir.mkdir(parents=True, exist_ok=True)

    size = file_size_or_tree(src)
    cap = max_size_bytes or _resolve_max_size_bytes(None)
    if size > cap:
        raise ValueError(
            f"file/dir too large ({size} bytes > {cap} bytes cap)"
        )

    sha = sha256_file(src) if src.is_file() else sha256_tree(src)
    short = sha[:16]

    # Read-modify-write MUST be locked across threads, otherwise two concurrent
    # imports of distinct files both pass the duplicate-check and both append,
    # blowing away the other's write.
    with _per_models_dir_lock(models_dir):
        reg = load_local_registry(models_dir)
        for entry in reg:
            if entry.get("sha256_short") == short:
                p = Path(entry["path"])
                if p.exists():
                    return ImportResult(
                        id=entry["id"],
                        name=entry["name"],
                        path=entry["path"],
                        sha256=entry.get("sha256", sha),
                        size_bytes=entry.get("size_bytes", size),
                        imported_from=entry.get("imported_from", str(src)),
                        mode=entry.get("mode", mode),
                        duplicate=True,
                    )

        dest = models_dir / src.name
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            i = 1
            while (models_dir / f"{stem}-{i}{suffix}").exists():
                i += 1
            dest = models_dir / f"{stem}-{i}{suffix}"

        if mode == "symlink":
            try:
                os.symlink(str(src), str(dest), target_is_directory=src.is_dir())
            except (OSError, NotImplementedError):
                if src.is_dir():
                    shutil.copytree(src, dest)
                else:
                    shutil.copy2(src, dest)
                mode = "copy"
        else:
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=False)
            else:
                try:
                    os.link(str(src), str(dest))
                except (OSError, NotImplementedError):
                    shutil.copy2(src, dest)

        final_size = file_size_or_tree(dest)
        entry = {
            "id": f"local-{short}",
            "name": dest.name,
            "path": str(dest),
            "sha256": sha,
            "sha256_short": short,
            "size_bytes": final_size,
            "imported_from": str(src),
            "source_filename": src.name,
            "imported_at": _now_iso(),
            "mode": mode,
        }
        reg.append(entry)
        save_local_registry(models_dir, reg)

    return ImportResult(
        id=entry["id"],
        name=entry["name"],
        path=entry["path"],
        sha256=sha,
        size_bytes=final_size,
        imported_from=entry["imported_from"],
        mode=mode,
        duplicate=False,
    )


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


# ---------------------------------------------------------------------------
# Local registry
# ---------------------------------------------------------------------------


def local_registry_path(models_dir: Path) -> Path:
    return models_dir / "_local.json"


def load_local_registry(models_dir: Path) -> list[dict[str, Any]]:
    p = local_registry_path(models_dir)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_local_registry(models_dir: Path, entries: list[dict[str, Any]]) -> None:
    """Atomic write: PID/thread-stamped .tmp + os.replace."""
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    p = local_registry_path(models_dir)
    tmp = p.with_suffix(f".tmp.{os.getpid()}.{threading.get_ident():x}")
    blob = json.dumps(entries, indent=2, ensure_ascii=False)

    # Coordinate across threads within this process. Across processes, the
    # OS handles atomic rename but two writers may still race on the *content*
    # order — that's accepted as "last writer wins"; the file is always valid JSON.
    with _per_models_dir_lock(models_dir):
        tmp.write_text(blob, encoding="utf-8")
        os.replace(tmp, p)


# ---------------------------------------------------------------------------
# In-memory progress registry (kept for backward-compat with the original API)
# ---------------------------------------------------------------------------


_progress_lock = threading.Lock()
_progress: dict[str, ImportProgress] = {}


def set_progress(p: ImportProgress) -> None:
    with _progress_lock:
        _progress[f"{p.model_id}::{p.file}"] = p


def get_progress(model_id: str, file: str) -> ImportProgress | None:
    with _progress_lock:
        return _progress.get(f"{model_id}::{file}")


def snapshot_progress() -> list[dict[str, Any]]:
    with _progress_lock:
        return [
            {"model_id": p.model_id, "file": p.file, "downloaded": p.downloaded, "total": p.total, "ratio": p.ratio}
            for p in _progress.values()
        ]


# ---------------------------------------------------------------------------
# HF enumeration (preserved from original importer.py)
# ---------------------------------------------------------------------------


from .catalog import (  # noqa: E402  — keep at bottom to preserve ordering
    DEFAULT_MODEL_HOSTS,
    GGUFRepoEntry,
    ModelEntry,
    is_host_allowed,
)

HF_API = "https://huggingface.co/api"
HF_RESOLVE = "https://huggingface.co"

# API mirrors for repo enumeration — huggingface.co is unreachable from
# many CN networks, so we probe mirrors first (each with a short timeout)
# before falling back to the official host.
_HF_API_MIRRORS: tuple[str, ...] = (
    "https://hf-cdn.sufy.com/api",
    "https://hf-mirror.com/api",
    "https://huggingface.co/api",
)


def list_gguf_files(repo: str, pattern: str = "*.gguf") -> list[dict[str, Any]]:
    """Enumerate files in a HF repo (paginated), filter by pattern.

    Mirrors are tried in order (12s each) — the first reachable one wins.
    Raises the last error only when every mirror fails.
    """
    import httpx
    import fnmatch

    if not repo:
        return []
    last_err: Exception | None = None
    for base in _HF_API_MIRRORS:
        url = f"{base}/models/{repo}/tree/main?recursive=true"
        out: list[dict[str, Any]] = []
        try:
            next_cursor: str | None = None
            with httpx.Client(timeout=12.0, follow_redirects=True,
                              headers={"User-Agent": "kevrai-studio/2.3.0"}) as client:
                for _ in range(50):
                    q = {"cursor": next_cursor} if next_cursor else None
                    r = client.get(url, params=q)
                    r.raise_for_status()
                    page = r.json()
                    for item in page:
                        if item.get("type") == "file" and fnmatch.fnmatch(item.get("path", ""), pattern):
                            out.append({"path": item["path"], "size": item.get("size", 0)})
                    next_cursor = r.headers.get("x-next-cursor") or None
                    if not next_cursor:
                        break
            return out
        except Exception as e:  # noqa: BLE001 — try next mirror
            last_err = e
            continue
    if last_err is not None:
        raise last_err
    return []


def _huggingface_headers(token: str | None = None) -> dict[str, str]:
    h = {"User-Agent": "kevrai-studio/2.3.0"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def hf_resolve_url(repo: str, filename: str, revision: str = "main") -> str:
    from urllib.parse import quote
    return f"{HF_RESOLVE}/{repo}/resolve/{revision}/{quote(filename, safe='/')}"


def download_file(
    url: str,
    target: Path,
    token: str | None = None,
    progress_cb=None,
    *,
    enforce_allowlist: bool = False,
) -> bool:
    """Synchronous helper for one-off downloads (kept for backwards compat).

    `enforce_allowlist=False` (default) is permissive: any https URL works.
    """
    import httpx
    if enforce_allowlist and not is_host_allowed(url, DEFAULT_MODEL_HOSTS):
        raise ValueError(f"refusing to download from non-allowlisted host: {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    headers = _huggingface_headers(token)
    pos = target.stat().st_size if target.exists() else 0
    if pos > 0:
        headers["Range"] = f"bytes={pos}-"
    mode = "ab" if pos > 0 else "wb"
    with httpx.Client(timeout=None, follow_redirects=True) as client:
        with client.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", "0")) + pos
            with target.open(mode) as fh:
                downloaded = pos
                for chunk in r.iter_bytes(CHUNK_SIZE):
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        try:
                            progress_cb(downloaded, total)
                        except Exception:
                            pass
    return True
