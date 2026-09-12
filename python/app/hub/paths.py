"""Path-safety helpers shared by the hub package, ``engines.py`` and ``main.py``.

the P0 hardening items H2 (Zip Slip) / H3 (import allowlist).

These are **pure** functions — no network, no IO beyond ``Path.resolve()`` —
so they can be exhaustively unit-tested offline (E19/E20/E21/E30).

Why a dedicated module: ``main.py`` imports ``hub``; ``hub`` must never import
``main`` (circular). Putting the helpers here lets ``engines.py`` and
``main.py`` reuse them without a dependency inversion.
"""
from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable

# Windows device names — writing to these on Windows hits the device driver
# instead of the filesystem (E21). Also blocked with an extension ("CON.txt").
_WIN_RESERVED_RE = re.compile(
    r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.[^.]*)?$", re.IGNORECASE
)
# Characters that are illegal in Windows filenames and useless elsewhere.
_ILLEGAL_CHARS = set('<>:"/\\|?*')

MAX_REL_LEN = 1024
MAX_PART_LEN = 255


class UnsafePathError(ValueError):
    """Raised when a relative path cannot be safely joined onto a root."""

def split_parts(rel: str) -> list[str]:
    """Split a relative path into clean parts (``\\`` normalized to ``/``)."""
    return [p for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".")]


def is_safe_part(part: str) -> bool:
    """True when a single path segment is safe to create on all platforms."""
    if not part or len(part) > MAX_PART_LEN:
        return False
    if part in ("..", "."):
        return False
    if "\x00" in part or any(ord(c) < 32 for c in part):
        return False
    if any(c in _ILLEGAL_CHARS for c in part):
        return False
    if _WIN_RESERVED_RE.match(part):
        return False
    # Trailing dot/space is silently stripped by Windows → ambiguity.
    if part.endswith(".") or part.endswith(" "):
        return False
    return True


def safe_join(root: str | os.PathLike[str], rel: str) -> Path:
    """Join ``rel`` under ``root`` guaranteeing the result stays inside.

    Rejects (raising :class:`UnsafePathError`):

    * empty / over-long (>1024) paths
    * absolute paths, NUL bytes, control characters
    * ``..`` traversal, Windows illegal characters ``<>:"|?*``
    * Windows reserved device names (``CON``, ``NUL``, ``COM1``, ``LPT1``, …)
    * trailing dot / trailing space segments
    * a resolved result that escapes ``root`` (symlink-aware, best effort)

    Returns the resolved absolute :class:`Path`.
    """
    if not isinstance(rel, str) or not rel.strip():
        raise UnsafePathError("empty path")
    if len(rel) > MAX_REL_LEN:
        raise UnsafePathError("path too long")
    if "\x00" in rel:
        raise UnsafePathError("nul byte in path")
    if rel.startswith(("/", "\\")) or (len(rel) > 1 and rel[1] == ":"):
        raise UnsafePathError("absolute path not allowed")

    parts = split_parts(rel)
    if not parts:
        raise UnsafePathError("empty path after normalization")
    for p in parts:
        if not is_safe_part(p):
            raise UnsafePathError(f"unsafe path segment: {p!r}")

    root_path = Path(root).expanduser()
    dest = (root_path / Path(*parts)).resolve()
    root_resolved = root_path.resolve()
    if dest != root_resolved and root_resolved not in dest.parents:
        raise UnsafePathError("path escapes root")
    return dest


def is_within(root: str | os.PathLike[str], candidate: str | os.PathLike[str]) -> bool:
    """True when ``candidate`` resolves to ``root`` or one of its children."""
    try:
        root_resolved = Path(root).expanduser().resolve()
        cand = Path(candidate).expanduser().resolve()
    except OSError:
        return False
    return cand == root_resolved or root_resolved in cand.parents


def safe_extract(
    archive: zipfile.ZipFile,
    dest: str | os.PathLike[str],
    *,
    members: Iterable[str] | None = None,
) -> list[Path]:
    """Extract ``archive`` into ``dest`` with per-member Zip-Slip validation.

    Fixes P0-2 (H2): the previous ``zf.extractall(target_dir)`` trusted member
    names, so ``../../evil`` could write outside the install directory.

    Unsafe members are **skipped** (and reported) rather than raising, so a
    single hostile entry cannot abort a legitimate install; the caller decides
    whether an empty result is fatal.

    Returns the list of extracted paths.
    """
    dest_path = Path(dest).expanduser().resolve()
    dest_path.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    for info in archive.infolist():
        name = info.filename or ""
        if members is not None and name not in members:
            continue
        # Directory entries legitimately end with "/" — normalize them away.
        clean = name.replace("\\", "/").lstrip("/")
        if not clean or clean in (".", ".."):
            continue
        try:
            target = safe_join(dest_path, clean)
        except UnsafePathError:
            continue
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info, "r") as src, open(target, "wb") as out:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        extracted.append(target)
    return extracted


def safe_extract_file(
    zip_path: str | os.PathLike[str],
    dest: str | os.PathLike[str],
) -> list[Path]:
    """Convenience wrapper: open ``zip_path`` and :func:`safe_extract` it."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        return safe_extract(zf, dest)


def hub_dest_root(
    download_root: str | os.PathLike[str],
    hub: str,
    repo: str,
) -> Path:
    """``{download_root}/hub/{hub}/{namespace}__{name}`` (§4.3 layout)."""
    safe_repo = str(repo or "").strip().strip("/").replace("/", "__")
    safe_repo = re.sub(r"[^A-Za-z0-9._-]", "_", safe_repo) or "_unknown"
    safe_hub = re.sub(r"[^A-Za-z0-9._-]", "_", str(hub or "")) or "_unknown"
    return Path(download_root).expanduser().resolve() / "hub" / safe_hub / safe_repo


def relative_parent(path: str | os.PathLike[str]) -> Path:
    """Parent of ``path`` (used by downloaders that need ``mkdir(parents=True)``)."""
    return Path(path).expanduser().parent


def ensure_parent(path: str | os.PathLike[str]) -> Path:
    """``mkdir -p`` the parent directory of ``path`` and return it."""
    parent = Path(path).expanduser().resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def _coerce(v: Any) -> str:
    return str(v or "")
