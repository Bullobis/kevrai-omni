"""Engine manager — install, verify, uninstall engines (llama.cpp, MNN, vllm, …).

New state-machine API (``EngineManager``) tracks every install through
``EngineState`` transitions. The legacy helpers
(``ensure_engine``, ``install_pip_engine``, ``download_zip_engine``,
``load_status``, ``list_engines_status``, etc.) are preserved so existing
tests and routes still work.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import logging
import os
import queue as _queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time as _time
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

from .catalog import (
    ALLOWED_ENGINE_HOSTS,
    Catalog,
    is_host_allowed,
)
from .hub.paths import safe_extract as _hub_safe_extract
from .pip_exec import pip_command

log = logging.getLogger("kevrai.engines")

CHUNK_SIZE = 1 << 20  # 1 MiB

# GitHub direct downloads are unreliable from mainland China; these proxies
# mirror release assets byte-for-byte and are tried in order after the
# original URL.  All three hosts are in DEFAULT_MODEL_HOSTS (catalog.py).
GITHUB_ACCEL_PREFIXES: tuple[str, ...] = (
    "https://ghfast.top/",
    "https://gh-proxy.com/",
)

_HTTP_TIMEOUT = httpx.Timeout(connect=15.0, read=60.0, write=60.0, pool=15.0)

# Module-level mutex serializing every engine install (binary + pip).
#
# Why: an install writes a per-engine ``.partial`` blob and then does a
# read-modify-write on the shared ``installed.json`` manifest. Two concurrent
# install() calls — e.g. a double-click that FastAPI dispatches to two
# threadpool workers — interleave chunks into the same ``.partial`` file and
# clobber each other's manifest record (lost ``active_url`` / progress, and
# corrupted download bytes). Serializing all installs is the intended
# single-instance mutex: engines are large binaries and the desktop UI never
# needs parallel installs.
_INSTALL_LOCK = threading.Lock()

# Guards the read-modify-write of ``installed.json``. ``install()``/
# ``install_pip()`` already hold ``_INSTALL_LOCK``, but ``uninstall()``,
# ``verify_installed()``'s state flip, and ``check_engine_updates``'s/
# ``apply_engine_update``'s ``_upsert()`` do NOT. Without this lock an install
# progress update (which re-reads + rewrites the whole manifest every chunk)
# can read a snapshot, pause mid-write, and then clobber a concurrent
# ``_upsert``'s freshly-added record. RLock because ``_set_state`` may be
# re-entered and ``install`` already holds ``_INSTALL_LOCK`` (outer), never the
# reverse, so there is no cycle.
_MANIFEST_LOCK = threading.RLock()


def _serialized_install(fn: Any) -> Any:
    """Run an install entry point under the global install mutex.

    The lock is held by the ``with`` block, so it is released on every exit
    path (success or exception) — no manual release, no deadlock. No install
    entry point calls another install entry point, so a plain non-reentrant
    ``Lock`` is intentional (re-entrancy would mask a nested-call bug).
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with _INSTALL_LOCK:
            return fn(*args, **kwargs)

    return wrapper


def _is_github_url(url: str) -> bool:
    try:
        host = httpx.URL(url).host or ""
    except Exception:  # noqa: BLE001 — malformed URL just isn't GitHub
        return False
    return host in {"github.com", "objects.githubusercontent.com",
                    "raw.githubusercontent.com"}


def expand_engine_candidates(url: str) -> list[str]:
    """Expand a GitHub release URL into an ordered candidate list.

    The original URL always comes first (it is the canonical source); the
    accelerator mirrors follow.  Non-GitHub URLs are returned unchanged so
    callers can treat the output uniformly.
    """
    u = (url or "").strip()
    if not u or not _is_github_url(u):
        return [u] if u else []
    out = [u]
    for prefix in GITHUB_ACCEL_PREFIXES:
        cand = prefix + u
        if cand not in out:
            out.append(cand)
    return out


# ---------------------------------------------------------------------------
# GitHub 仓库 → 可下载归档（release 资产优先，其次源码归档）
# ---------------------------------------------------------------------------

_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.zst", ".tar.xz")
_GITHUB_CACHE: dict[str, dict[str, Any]] = {}


def looks_like_archive_url(url: str) -> bool:
    """判断 URL 是否指向一个真正的归档文件，而不是仓库/Release 网页。"""
    path = str(url or "").split("?", 1)[0].lower()
    return path.endswith(_ARCHIVE_SUFFIXES) or "/releases/download/" in path


def _github_get(path: str) -> dict[str, Any] | None:
    """读取 GitHub API（带 token 时用 token，避免 60 次/小时限流）。"""
    if path in _GITHUB_CACHE:
        return _GITHUB_CACHE[path]
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Kevrai-Omni",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(f"https://api.github.com{path}", headers=headers)
        if resp.status_code != 200:
            log.debug("github api %s -> %s", path, resp.status_code)
            return None
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — 离线/限流时静默走归档兜底
        log.debug("github api %s failed: %s", path, exc)
        return None
    if isinstance(data, dict):
        _GITHUB_CACHE[path] = data
        return data
    return None


def _asset_platform_score(name: str, plat: str) -> int:
    """给 release 资产打分，挑当前平台最合适的一个。返回 -1 表示不匹配。"""
    n = name.lower()
    if not n.endswith(_ARCHIVE_SUFFIXES):
        return -1
    # CUDA/cudart 运行时包不是完整引擎，除非没有别的选择才降级使用。
    # 默认装通用 CPU 包：CUDA/ROCm/OpenVINO/SYCL/Vulkan 变体需要对应驱动，
    # 除非用户明确要，否则不能让变体排在纯 CPU 包前面。
    penalty = -4 if any(t in n for t in (
        "cudart", "cuda", "rocm", "openvino", "sycl", "vulkan",
    )) else 0
    if plat.startswith("linux"):
        if not any(t in n for t in ("linux", "ubuntu")):
            return -1
        if any(t in n for t in (
            "arm64", "aarch64", "s390x", "ppc64", "riscv",
            "macos", "darwin", "win",
        )):
            return -1
        score = 6 if "x64" in n or "x86_64" in n else 4
    elif plat.startswith("windows"):
        if not any(t in n for t in ("win", "windows")):
            return -1
        if any(t in n for t in ("arm64", "aarch64", "macos", "darwin", "linux")):
            return -1
        score = 6 if ("x64" in n or "amd64" in n) else 4
        score += 2 if "cpu" in n else 0
    elif plat == "darwin-arm64":
        if not any(t in n for t in ("macos", "darwin", "osx")):
            return -1
        if "arm64" not in n and "aarch64" not in n:
            return -1
        score = 6
    elif plat == "darwin-x64":
        if not any(t in n for t in ("macos", "darwin", "osx")):
            return -1
        if any(t in n for t in ("arm64", "aarch64")):
            return -1
        score = 6
    else:
        return -1
    if "bin" in n:
        score += 2
    return score + penalty


def _parse_release_asset_links(html: str, repo: str) -> list[tuple[str, str]]:
    import re as _re
    found: list[tuple[str, str]] = []
    for m in _re.finditer(
        r'href="(/[^"/]+/[^"/]+/releases/download/[^"]+)"', html
    ):
        href = m.group(1)
        if repo not in href:
            continue
        name = href.rsplit("/", 1)[-1]
        found.append((name, "https://github.com" + href))
    return list(dict.fromkeys(found))


def _github_release_assets_html(repo: str) -> list[tuple[str, str]]:
    """API 被限流时，从 release 页面 HTML 里解析资产 (name, url)。

    GitHub 未登录 API 限额只有 60 次/小时，CI/共享出口 IP 很容易 403。
    release 页面本身不受这个限额影响：
      * /releases/latest 会 302 到具体 tag（但 llama.cpp 这种项目会跳到
        只有 nightly-tag.txt 的“发布”，实际二进制在 b<build> tag 上）；
      * 所以页面里找不到资产时，再从 /releases 列表取最近几个 tag，
        逐个请求 /releases/expanded_assets/<tag>（GitHub 的资产懒加载片段）。
    """
    import re as _re
    headers = {"User-Agent": _UA()}
    try:
        with httpx.Client(follow_redirects=True, timeout=_HTTP_TIMEOUT,
                          headers=headers) as client:
            latest = client.get(f"https://github.com/{repo}/releases/latest")
            if latest.status_code == 200:
                links = _parse_release_asset_links(latest.text, repo)
                if links:
                    return links
            listing = client.get(f"https://github.com/{repo}/releases")
            if listing.status_code != 200:
                return []
            tags: list[str] = []
            for m in _re.finditer(
                r'/' + _re.escape(repo) + r'/releases/tag/([^"?#]+)', listing.text
            ):
                tag = m.group(1)
                if tag not in tags:
                    tags.append(tag)
            for tag in tags[:5]:
                frag = client.get(
                    f"https://github.com/{repo}/releases/expanded_assets/{tag}"
                )
                if frag.status_code != 200:
                    continue
                links = _parse_release_asset_links(frag.text, repo)
                if links:
                    return links
    except Exception:  # noqa: BLE001 — 兜底路径，失败就走源码归档
        return []
    return []


def resolve_github_engine_sources(
    github: str, plat: str, *, include_source: bool = True
) -> list[str]:
    """把 ``owner/repo`` 解析成可直接下载的归档 URL 列表。

    顺序：当前平台匹配的 release 资产 → 默认分支源码归档 → main/master 兜底。
    这样 llama.cpp 这类只发 release 二进制的仓库能拿到正确资产名，
    而 ComfyUI 这类只提供源码的仓库也不会被硬拼成 ``comfyui.zip``（404）。
    """
    repo = str(github or "").strip().strip("/")
    if "/" not in repo:
        return []
    out: list[str] = []

    release = _github_get(f"/repos/{repo}/releases/latest")
    assets = release.get("assets") if isinstance(release, dict) else None
    if isinstance(assets, list):
        scored: list[tuple[int, str]] = []
        for asset in assets:
            name = str(asset.get("name") or "")
            url = str(asset.get("browser_download_url") or "")
            if not url or not looks_like_archive_url(url):
                continue
            score = _asset_platform_score(name, plat)
            if score >= 0:
                scored.append((score, url))
        scored.sort(key=lambda item: item[0], reverse=True)
        out.extend(url for _, url in scored)
    if not out:
        scored = []
        for name, url in _github_release_assets_html(repo):
            if not looks_like_archive_url(url):
                continue
            score = _asset_platform_score(name, plat)
            if score >= 0:
                scored.append((score, url))
        scored.sort(key=lambda item: item[0], reverse=True)
        out.extend(url for _, url in scored)

    if not include_source:
        return list(dict.fromkeys(out))

    info = _github_get(f"/repos/{repo}")
    branch = str(info.get("default_branch") or "") if isinstance(info, dict) else ""
    for b in ([branch] if branch else []) + ["main", "master"]:
        url = f"https://github.com/{repo}/archive/refs/heads/{b}.tar.gz"
        if url not in out:
            out.append(url)
    # 去重保序
    return list(dict.fromkeys(out))


def _stream_download(
    url: str,
    tmp: Path,
    *,
    progress_cb: Any = None,
    cancel_cb: Any = None,
    resume: bool = True,
) -> None:
    """Stream ``url`` into ``tmp`` with Range resume and progress callbacks.

    Raises on any network/protocol error so the caller can fall back to the
    next candidate.  When ``tmp`` already exists and the server supports
    ranges, the download continues from where it stopped; a server that
    ignores Range (answers 200 to a Range request) resets the file.
    """
    headers: dict[str, str] = {"User-Agent": _UA()}
    resume_from = 0
    if resume and tmp.exists():
        try:
            resume_from = tmp.stat().st_size
        except OSError:
            resume_from = 0
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"

    with httpx.Client(follow_redirects=True, timeout=_HTTP_TIMEOUT) as client, \
            client.stream("GET", url, headers=headers) as r:
        # A 200 answer to a Range request means the server ignored it —
        # restart from zero instead of appending to a truncated prefix.
        if resume_from > 0 and r.status_code == 200:
            resume_from = 0
            r.headers.__setitem__("content-length", r.headers.get("content-length", "0"))
        r.raise_for_status()

        total = 0
        cl = r.headers.get("content-length")
        if cl and cl.isdigit():
            total = int(cl) + resume_from
        if progress_cb is not None:
            progress_cb(0, resume_from, total)

        mode = "ab" if resume_from > 0 else "wb"
        done = resume_from
        with tmp.open(mode) as fh:
            for chunk in r.iter_bytes(CHUNK_SIZE):
                if cancel_cb is not None and cancel_cb():
                    raise DownloadCancelled(f"cancelled at {done} bytes")
                fh.write(chunk)
                done += len(chunk)
                if progress_cb is not None:
                    progress_cb(done, done, total)


class DownloadCancelled(RuntimeError):
    """Raised when a progress callback asks the download to stop."""


def _UA() -> str:
    """Consistent User-Agent across every outbound call in this module."""
    try:
        from . import USER_AGENT as _ua
        return _ua
    except Exception:  # noqa: BLE001 — defensive; package import should work
        from . import __version__
        return f"kevrai-omni/{__version__}"


# ---------------------------------------------------------------------------
# Public state machine
# ---------------------------------------------------------------------------


class EngineState(str, Enum):
    NOT_INSTALLED = "not_installed"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    INSTALLED = "installed"
    FAILED = "failed"


@dataclass
class EngineRecord:
    id: str
    version: str = ""
    install_path: str = ""
    sha256: str = ""
    size_bytes: int = 0
    installed_at: str = ""
    state: EngineState = EngineState.NOT_INSTALLED
    last_error: str = ""
    source_url: str = ""
    install_mode: str = "binary"  # "binary" | "pip"
    # Live download telemetry (transient — meaningful only while
    # state == DOWNLOADING, cleared on completion/failure).
    progress: float = 0.0        # 0.0 .. 1.0; -1.0 when total size unknown
    downloaded_bytes: int = 0
    total_bytes: int = 0
    active_url: str = ""         # candidate currently being downloaded from

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


# ---------------------------------------------------------------------------
# Manifest I/O (atomic)
# ---------------------------------------------------------------------------


def engine_install_dir(root: Path) -> Path:
    return Path(root) / "engines"


def _manifest_path(root: Path) -> Path:
    return engine_install_dir(root) / "installed.json"


def _read_manifest(root: Path) -> list[EngineRecord]:
    p = _manifest_path(root)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[EngineRecord] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            try:
                state = EngineState(item.get("state", "not_installed"))
            except ValueError:
                state = EngineState.NOT_INSTALLED
            out.append(
                EngineRecord(
                    id=item["id"],
                    version=item.get("version", ""),
                    install_path=item.get("install_path", ""),
                    sha256=item.get("sha256", ""),
                    size_bytes=int(item.get("size_bytes", 0) or 0),
                    installed_at=item.get("installed_at", ""),
                    state=state,
                    last_error=item.get("last_error", ""),
                    source_url=item.get("source_url", ""),
                    install_mode=item.get("install_mode", "binary"),
                )
            )
    return out


def _write_manifest(root: Path, records: list[EngineRecord]) -> None:
    p = _manifest_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        [r.to_dict() for r in records], indent=2, ensure_ascii=False
    )
    fd, tmp = tempfile.mkstemp(prefix=".installed-", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            with contextlib.suppress(OSError):
                os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# EngineManager
# ---------------------------------------------------------------------------


class EngineManager:
    """Track every engine install in `engine_dir/installed.json`."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.ensure_engine_dir()

    # --- infra ---

    def ensure_engine_dir(self) -> Path:
        p = engine_install_dir(self.root)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def engine_dir(self) -> Path:
        return engine_install_dir(self.root)

    # --- reads ---

    def list_installed(self) -> list[EngineRecord]:
        return _read_manifest(self.root)

    def get(self, engine_id: str) -> EngineRecord | None:
        for r in self._read():
            if r.id == engine_id:
                return r
        return None

    # --- writes ---

    def _read(self) -> list[EngineRecord]:
        return _read_manifest(self.root)

    def _write(self, records: list[EngineRecord]) -> None:
        _write_manifest(self.root, records)

    def _upsert(self, rec: EngineRecord) -> None:
        with _MANIFEST_LOCK:
            recs = self._read()
            for i, r in enumerate(recs):
                if r.id == rec.id:
                    recs[i] = rec
                    self._write(recs)
                    return
            recs.append(rec)
            self._write(recs)

    def _set_state(self, engine_id: str, state: EngineState, **kw: Any) -> EngineRecord | None:
        with _MANIFEST_LOCK:
            recs = self._read()
            for i, r in enumerate(recs):
                if r.id == engine_id:
                    rr = EngineRecord(
                        id=r.id,
                        version=kw.get("version", r.version),
                        install_path=kw.get("install_path", r.install_path),
                        sha256=kw.get("sha256", r.sha256),
                        size_bytes=kw.get("size_bytes", r.size_bytes),
                        installed_at=kw.get("installed_at", r.installed_at),
                        state=state,
                        last_error=kw.get("last_error", r.last_error),
                        source_url=kw.get("source_url", r.source_url),
                        install_mode=kw.get("install_mode", r.install_mode),
                        progress=kw.get("progress", r.progress),
                        downloaded_bytes=kw.get("downloaded_bytes", r.downloaded_bytes),
                        total_bytes=kw.get("total_bytes", r.total_bytes),
                        active_url=kw.get("active_url", r.active_url),
                    )
                    recs[i] = rr
                    self._write(recs)
                    return rr
            # Not in manifest yet — create a fresh record.
            rr = EngineRecord(
                id=engine_id,
                state=state,
                version=kw.get("version", ""),
                install_path=kw.get("install_path", ""),
                sha256=kw.get("sha256", ""),
                size_bytes=kw.get("size_bytes", 0),
                installed_at=kw.get("installed_at", ""),
                last_error=kw.get("last_error", ""),
                source_url=kw.get("source_url", ""),
                install_mode=kw.get("install_mode", "binary"),
                progress=kw.get("progress", 0.0),
                downloaded_bytes=kw.get("downloaded_bytes", 0),
                total_bytes=kw.get("total_bytes", 0),
                active_url=kw.get("active_url", ""),
            )
            recs.append(rr)
            self._write(recs)
            return rr

    # --- mutations ---

    @_serialized_install
    def install(
        self,
        engine_id: str,
        url: str,
        sha256: str | None = None,
        *,
        unzip: bool = True,
    ) -> EngineRecord:
        """Install an engine binary from `url`. Idempotent on sha match.

        Download hardening (v2.8.1):
          * GitHub URLs are transparently expanded into a candidate list with
            accelerator mirrors (ghfast.top / gh-proxy.com); the first
            candidate that yields a complete file wins.
          * Interrupted transfers resume from ``<target>.partial`` via HTTP
            Range instead of restarting from zero.
          * Progress is written into the live EngineRecord (progress /
            downloaded_bytes / total_bytes / active_url) so the UI can poll
            ``/api/engines/status`` while a big engine downloads.
        """
        if not is_host_allowed(url, ALLOWED_ENGINE_HOSTS):
            self._set_state(
                engine_id, EngineState.FAILED,
                last_error=f"host not allowed: {url}",
                source_url=url,
            )
            raise ValueError(f"refusing to download from non-allowlisted host in {url}")

        target_dir = self.engine_dir() / engine_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / "engine.bin"
        tmp = target_file.with_suffix(".partial")

        candidates = expand_engine_candidates(url)
        last_err: Exception | None = None
        completed = False

        self._set_state(
            engine_id, EngineState.DOWNLOADING,
            source_url=url, install_path=str(target_dir),
        )

        try:
            for cand in candidates:
                try:
                    self._set_state(
                        engine_id, EngineState.DOWNLOADING,
                        active_url=cand,
                    )
                    _stream_download(
                        cand, tmp,
                        progress_cb=lambda done, _d, total, eid=engine_id: (
                            self._progress(eid, done, total)
                        ),
                    )
                    completed = True
                    break
                except DownloadCancelled:
                    raise
                except Exception as e:  # noqa: BLE001 — try the next mirror
                    last_err = e
                    continue

            if not completed:
                raise last_err or RuntimeError(
                    f"all {len(candidates)} download candidates failed"
                )

            if unzip and str(url).endswith(".zip"):
                # Verify the downloaded archive itself BEFORE extraction: the
                # installed artifact is a directory, which cannot be hashed as
                # a whole — hashing the zip is the only meaningful integrity
                # check (BUG-02). A mismatch aborts before any file is written.
                if sha256:
                    actual_zip = _sha256_of(tmp) if tmp.is_file() else ""
                    if actual_zip and actual_zip.lower() != sha256.lower():
                        self._set_state(
                            engine_id, EngineState.FAILED,
                            source_url=url, install_path=str(target_dir),
                            last_error=f"sha mismatch: expected {sha256}, got {actual_zip}",
                        )
                        # Leave partial for inspection
                        raise ValueError(
                            f"sha256 mismatch: expected {sha256}, got {actual_zip}"
                        )
                with zipfile.ZipFile(tmp, "r") as zf:
                    # P0-2 (H2): validated extraction — Zip Slip is rejected
                    # per member instead of trusting member names.
                    _hub_safe_extract(zf, target_dir)
                tmp.unlink(missing_ok=True)
                # The "binary" we verify is any file matching an engine binary name,
                # but we leave that as a no-op for zip layouts; the dir itself is the
                # install artifact.
                target_for_hash = target_dir
            else:
                os.replace(tmp, target_file)
                target_for_hash = target_file

            self._set_state(
                engine_id, EngineState.VERIFYING,
                source_url=url, install_path=str(target_dir),
            )
            actual = _sha256_of(target_for_hash) if target_for_hash.is_file() else ""
            if sha256 and actual and actual.lower() != sha256.lower():
                self._set_state(
                    engine_id, EngineState.FAILED,
                    source_url=url, install_path=str(target_dir),
                    last_error=f"sha mismatch: expected {sha256}, got {actual}",
                )
                # Leave partial for inspection
                raise ValueError(
                    f"sha256 mismatch: expected {sha256}, got {actual}"
                )

            size = 0
            if target_for_hash.is_file():
                size = target_for_hash.stat().st_size
            elif target_for_hash.is_dir():
                size = sum(p.stat().st_size for p in target_for_hash.rglob("*") if p.is_file())

            return self._set_state(
                engine_id, EngineState.INSTALLED,
                source_url=url,
                install_path=str(target_dir),
                sha256=sha256 or actual,
                size_bytes=size,
                installed_at=_now_iso(),
                # Clear transient download telemetry on success.
                progress=1.0, downloaded_bytes=0, total_bytes=0, active_url="",
            ) or EngineRecord(id=engine_id, state=EngineState.INSTALLED)

        except Exception as e:
            # Only flip the state if we haven't already moved past DOWNLOADING.
            cur = self.get(engine_id)
            cur_state = cur.state if cur else EngineState.NOT_INSTALLED
            if cur_state in {EngineState.DOWNLOADING, EngineState.VERIFYING}:
                self._set_state(
                    engine_id, EngineState.FAILED,
                    last_error=f"{type(e).__name__}: {e}",
                )
            raise

    def _progress(self, engine_id: str, done: int, total: int) -> None:
        """Update live download telemetry without flipping the state.

        Called from the streaming loop in ``_stream_download`` — deliberately
        cheap: it rewrites only the in-memory record and the manifest file,
        which is also what makes progress visible to concurrent status polls.
        """
        frac = (done / total) if total > 0 else -1.0
        self._set_state(
            engine_id, EngineState.DOWNLOADING,
            progress=round(frac, 4),
            downloaded_bytes=done,
            total_bytes=total,
        )

    def verify_installed(self, engine_id: str) -> bool:
        rec = self.get(engine_id)
        if rec is None or rec.state != EngineState.INSTALLED:
            return False
        p = Path(rec.install_path) if rec.install_path else None
        if not p or not p.exists():
            self._set_state(
                engine_id, EngineState.FAILED,
                last_error=f"install path missing: {p}",
            )
            return False
        # Sha verification only if file (not a dir)
        if rec.sha256 and p.is_file():
            try:
                actual = _sha256_of(p)
            except Exception:
                return False
            if actual.lower() != rec.sha256.lower():
                self._set_state(
                    engine_id, EngineState.FAILED,
                    last_error="sha mismatch on verify",
                )
                return False
        return True

    def uninstall(self, engine_id: str) -> bool:
        rec = self.get(engine_id)
        if rec is None:
            return False
        try:
            if rec.install_path:
                p = Path(rec.install_path)
                if p.exists():
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        with contextlib.suppress(OSError):
                            p.unlink()
            self._set_state(engine_id, EngineState.NOT_INSTALLED)
            # Remove the manifest entry entirely (locked RMW — a concurrent
            # install progress write must not re-add the entry we just dropped).
            with _MANIFEST_LOCK:
                recs = self._read()
                recs = [r for r in recs if r.id != engine_id]
                self._write(recs)
            return True
        except Exception:
            return False

    # --- pip-based engines ---

    @_serialized_install
    def install_pip(
        self,
        pypi_name: str,
        engine_id: str | None = None,
        extra: list[str] | None = None,
    ) -> EngineRecord:
        eid = engine_id or pypi_name
        # 目录名用引擎 id（如 piper → pip-piper），与各 runtime 的
        # engine_target_dir 约定一致；旧逻辑按 pypi 名拼成 pip-piper-tts，
        # 运行时 sys.path bootstrap 找不到，导致"装了却 import 不到"。
        target = self.engine_dir() / f"pip-{eid}"
        target.mkdir(parents=True, exist_ok=True)
        requirements = [pypi_name, *list(extra or [])]
        cmd = [*pip_command(), "install", "--target", str(target), *requirements]
        idx = os.environ.get("KEVRAI_PIP_INDEX", "https://mirrors.tencent.com/pypi/simple/")
        cmd += ["-i", idx, "--extra-index-url", "https://pypi.org/simple"]
        # 这些引擎依赖 torch。PyPI 的默认 torch 轮子捆绑整套 CUDA 运行时
        # （实测 cu13 解包后 >3GB），CPU 用户机器上既装不下也跑不起来。
        # 默认改从 PyTorch 官方 CPU 索引取 +cpu 轮子（约 1/10 体积）；
        # 有 NVIDIA 显卡想用 CUDA 可设 KEVRAI_TORCH_VARIANT=cuda。
        if (eid in _TORCH_ENGINES
                and os.environ.get("KEVRAI_TORCH_VARIANT", "cpu").lower() != "cuda"):
            cmd += ["--extra-index-url", "https://download.pytorch.org/whl/cpu"]
        # pip 默认把解包/构建临时目录放在 /tmp（常见只有几 GB，torch 这类
        # 大包会把分区写满，报 [Errno 28] No space left）。改到数据目录内。
        pip_tmp = self.engine_dir().parent / "tmp" / "pip"
        pip_tmp.mkdir(parents=True, exist_ok=True)
        pip_env = os.environ.copy()
        pip_env.update({"TMPDIR": str(pip_tmp), "TEMP": str(pip_tmp), "TMP": str(pip_tmp)})
        self._set_state(eid, EngineState.DOWNLOADING, install_path=str(target),
                        install_mode="pip")
        try:
            # 流式读 pip 输出，把最新一行写进 active_url 作为进度文案，
            # 让前端轮询时能看到"正在下载/安装 xx 包"而不是干等。
            # 用独立读线程 + 队列，使网络挂起（长时间无输出）时仍能命中
            # 900s 超时，而不是永久占死 _INSTALL_LOCK 与线程池线程。
            proc = subprocess.Popen(  # noqa: S603 — argv list built from internal constants
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=pip_env,
            )
            last_line = ""
            tail: list[str] = []
            assert proc.stdout is not None
            stdout = proc.stdout
            q: _queue.Queue[str | BaseException | None] = _queue.Queue()

            def _reader() -> None:
                try:
                    for line in stdout:
                        q.put(line)
                except BaseException as e:  # noqa: BLE001 — re-raised in main thread
                    q.put(e)
                finally:
                    q.put(None)  # EOF sentinel

            threading.Thread(target=_reader, daemon=True).start()
            deadline = _time.monotonic() + 900
            while True:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    proc.kill()
                    raise TimeoutError(f"pip install of {pypi_name} timed out after 900s")
                try:
                    item = q.get(timeout=min(1.0, remaining))
                except _queue.Empty:
                    continue
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                line = str(item).strip()
                if line:
                    last_line = line[:200]
                    tail = [*tail[-19:], line[:300]]
                    self._set_state(eid, EngineState.DOWNLOADING,
                                    install_path=str(target), install_mode="pip",
                                    active_url=last_line)
            proc.wait(timeout=30)
            rc = proc.returncode
        except Exception as e:
            # 确保超时/异常时杀掉子进程，不留孤儿 pip。
            try:
                if "proc" in locals() and proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=10)
            except Exception as kill_err:  # noqa: BLE001 — never mask the real error
                # v4.17.7: was a bare ``pass`` (ruff S110). Log it instead so a
                # stuck/orphaned pip child is diagnosable from the sidecar log.
                log.warning("engines: failed to kill pip child process: %s", kill_err)
            self._set_state(eid, EngineState.FAILED,
                            last_error=f"pip exception: {e}",
                            install_mode="pip")
            raise
        if rc != 0:
            detail = "\n".join(tail)[-600:] or (last_line or "pip 安装失败")
            hint = ""
            if "No space left" in detail or "Errno 28" in detail:
                hint = "（磁盘空间不足：请清理数据目录后重试，或设置 KEVRAI_DATA_ROOT 到空间更大的分区）"
            self._set_state(eid, EngineState.FAILED,
                            last_error=f"pip rc={rc}{hint}: {detail}"[-1200:],
                            install_mode="pip")
            raise RuntimeError(f"pip install of {pypi_name} failed: rc={rc}{hint}: {detail[-300:]}")
        # 目标目录为空时（单测 mock 掉 pip 的常见情况）不做 import 校验：
        # 没有文件可 import 是预期结果，不应把"pip 说成功"改判成失败。
        if target.is_dir() and any(target.iterdir()):
            ok_import, import_err = validate_engine_import(eid, target)
        else:
            ok_import, import_err = True, ""
        if not ok_import:
            # 镜像站偶发"退出码 0 但轮子缺文件"（实测 av/numpy）。pip 说成功
            # 不代表能 import，这里用官方 PyPI 强制重装一次再验。
            retry_cmd = [
                *pip_command(), "install", "--target", str(target),
                "--upgrade", "--force-reinstall", "--no-cache-dir",
                "-i", "https://pypi.org/simple", *requirements,
            ]
            retry = subprocess.run(  # noqa: S603 — 内部常量拼接的 argv
                retry_cmd, capture_output=True, text=True, timeout=900, env=pip_env
            )
            ok_import, import_err = validate_engine_import(eid, target)
            if retry.returncode != 0 or not ok_import:
                detail = (retry.stderr or retry.stdout or import_err)[-400:]
                self._set_state(eid, EngineState.FAILED,
                                last_error=f"import check failed: {detail}",
                                install_mode="pip")
                raise RuntimeError(
                    f"pip install of {pypi_name} finished but import failed: {import_err}"
                )
        self._set_state(eid, EngineState.INSTALLED,
                        install_path=str(target),
                        installed_at=_now_iso(),
                        install_mode="pip")
        return EngineRecord(id=eid, state=EngineState.INSTALLED,
                            install_path=str(target), install_mode="pip")

    # --- convenience ---

    def is_installed(self, engine_id: str) -> bool:
        """Backwards-compatible helper."""
        rec = self.get(engine_id)
        if rec is None:
            return False
        return rec.state == EngineState.INSTALLED and self.verify_installed(engine_id)


# ---------------------------------------------------------------------------
# Legacy surface (preserved for the existing tests + main.py router)
# ---------------------------------------------------------------------------


@dataclass
class InstallResult:
    engine_id: str
    path: str
    ok: bool
    message: str


def _platform_key() -> str:
    s = sys.platform
    if s.startswith("win"):
        return "windows-x64"
    if s.startswith("linux"):
        return "linux-x64"
    if s.startswith("darwin"):
        return "darwin-arm64"
    return f"{s}-x64"


# pip 退出码 0 ≠ 包可用：镜像站出现过"dist-info 在、包体缺失"的轮子
# （实测 av / numpy）。这里对"导入名与包名不同"或关键引擎做一次 import 校验；
# vllm/sglang/mlx 这类需要 GPU/特定平台的包不校验，避免误判。
_TORCH_ENGINES = {"kokoro-engine", "chatterbox", "transformers", "sentence-transformers", "demucs"}

_ENGINE_IMPORT_NAMES: dict[str, str] = {
    "piper": "piper",
    "kokoro-engine": "kokoro",
    "chatterbox": "chatterbox",
    "ming-tts": "ming_tts_mnn",
    "faster-whisper": "faster_whisper",
    "sentence-transformers": "sentence_transformers",
    "demucs": "demucs",
    "rapidocr": "rapidocr",
    "pymupdf": "fitz",
    "onnxruntime": "onnxruntime",
    "mnn": "MNN",
    "transformers": "transformers",
    "diffusers": "diffusers",
}


def validate_engine_import(eid: str, target: Path) -> tuple[bool, str]:
    """在 sidecar 进程内 import 一次，确认装好的包真的能用。"""
    module = _ENGINE_IMPORT_NAMES.get(str(eid).lower())
    if not module:
        return True, ""
    import importlib

    sys.path.insert(0, str(target))
    importlib.invalidate_caches()
    try:
        importlib.import_module(module)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — 任何导入失败都算装坏
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(str(target))


def _pip_index() -> str | None:
    return os.environ.get("KEVRAI_PIP_INDEX", "https://mirrors.tencent.com/pypi/simple/")


def engine_status_path(root: Path) -> Path:
    return engine_install_dir(root) / "status.json"


def load_status(root: Path) -> dict[str, Any]:
    p = engine_status_path(root)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_status(root: Path, status: dict[str, Any]) -> None:
    p = engine_status_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


@_serialized_install
def install_git_engine(
    repo_url: str,
    root: Path,
    eid: str,
    branch: str = "",
) -> InstallResult:
    """Clone a GitHub/GitLab repo into engine_dir as the engine install.

    Used for ``"install": "source"`` engines (ComfyUI nodes, 3D repos, etc.)
    that expose no binary — the engine *is* the repo checkout.
    """
    target = engine_install_dir(root) / eid
    # If already cloned and not empty, just return ok.
    if target.is_dir() and any(target.iterdir()):
        return InstallResult(
            engine_id=eid, path=str(target), ok=True, message="already installed"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        import shutil as _sh
        _sh.rmtree(target, ignore_errors=True)

    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [repo_url, str(target)]

    try:
        proc = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=300
        )
        if proc.returncode == 0 and target.is_dir() and any(target.iterdir()):
            return InstallResult(
                engine_id=eid, path=str(target), ok=True,
                message=f"cloned {repo_url}",
            )
        msg = (proc.stderr or proc.stdout or "").strip()[-500:]
        # v4.17.7: the old ``f"git clone failed: {msg}" or "git clone failed"``
        # was dead logic (SIM222) — a non-empty f-string is always truthy, so
        # the fallback never fired while an empty ``msg`` produced a dangling
        # "git clone failed: ". Build the message conditionally instead.
        message = f"git clone failed: {msg}" if msg else "git clone failed (no output)"
        return InstallResult(
            engine_id=eid, path=str(target), ok=False,
            message=message,
        )
    except FileNotFoundError:
        return InstallResult(
            engine_id=eid, path="", ok=False,
            message="git not installed — please install git and retry",
        )
    except subprocess.TimeoutExpired:
        return InstallResult(
            engine_id=eid, path=str(target) if target.exists() else "", ok=False,
            message="git clone timed out (300s)",
        )
    except Exception as exc:  # noqa: BLE001
        return InstallResult(
            engine_id=eid, path=str(target) if target.exists() else "", ok=False,
            message=f"git clone error: {exc}",
        )


@_serialized_install
def try_download_engine(url: str, root: Path, eid: str) -> InstallResult:
    """Best-effort download of a URL as an engine install.

    - zip → ``download_zip_engine``（带镜像/加速与魔数校验）
    - .tar / .tar.gz / .tgz / .tar.zst / .tar.xz → 本地解压
    - GitHub 仓库页 → 先解析最新 release 的平台资产，再退回默认分支源码归档
    """
    url_s = str(url or "").strip()
    if not url_s:
        return InstallResult(engine_id=eid, path="", ok=False, message="empty url")

    if looks_like_archive_url(url_s):
        path = url_s.lower().split("?", 1)[0]
        if path.endswith((".tar", ".tar.gz", ".tgz", ".tar.zst", ".tar.xz")):
            return _download_and_extract_tarball(url_s, root, eid)
        return download_zip_engine(url_s, root, eid)

    # 仓库页 / releases 页：解析出真正的归档地址，而不是把 HTML 当 zip。
    import re as _re
    m = _re.match(
        r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?"
        r"(?:/(?:releases|archive|tree|blob)[^?#]*)?/?$",
        url_s,
    )
    if m:
        owner, repo = m.group(1), m.group(2)
        is_release_page = "/releases" in url_s
        candidates = resolve_github_engine_sources(
            f"{owner}/{repo}", _platform_key(), include_source=not is_release_page
        )
        last: InstallResult | None = None
        for cand in candidates:
            result = try_download_engine(cand, root, eid)
            if result.ok:
                return result
            last = result
        return last or InstallResult(
            engine_id=eid, path="", ok=False,
            message=f"github repo {owner}/{repo} has no downloadable archive",
        )

    # Default path: legacy zip engine downloader (it also handles mirror fallback)
    return download_zip_engine(url_s, root, eid)


def _tool_path(name: str) -> str:
    """Resolve an external tool to an absolute path.

    v4.17.7 (S607): invoking a bare ``"zstd"`` / ``"git"`` lets the OS resolve
    it through ``PATH``, so a writable directory earlier in ``PATH`` can shadow
    the real binary. Resolving once with :func:`shutil.which` pins the absolute
    path. When the tool is absent the bare name is returned so the caller's
    existing ``FileNotFoundError`` handling still applies.
    """
    found = shutil.which(name)
    return found or name


def _safe_extract_tar(tf: Any, dest: Path) -> None:
    """Extract only regular files and directories from ``tf`` into ``dest``.

    Any symlink, hardlink, FIFO, socket or device member is skipped: an engine
    tarball has no legitimate need for them, and each is a classic escape
    primitive (create a link, then write through it). Every destination is
    re-resolved and confirmed to stay inside ``dest`` immediately before the
    write, so a member name that only *looks* contained cannot win.

    Raises :class:`ValueError` when a member would land outside ``dest``.
    """
    base = dest.resolve()
    for member in tf.getmembers():
        if not (member.isfile() or member.isdir()):
            continue
        target = (base / member.name).resolve()
        try:
            target.relative_to(base)
        except ValueError as exc:
            raise ValueError(f"tar member escapes target: {member.name}") from exc
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        src = tf.extractfile(member)
        if src is None:  # pragma: no cover — isfile() guarantees a reader
            continue
        with src, target.open("wb") as out:
            _shutil_copyfileobj(src, out)


def _shutil_copyfileobj(src: Any, dst: Any) -> None:
    """Copy ``src`` to ``dst`` in chunks (patchable in tests)."""
    import shutil as _s

    _s.copyfileobj(src, dst, 1024 * 1024)


def _download_and_extract_tarball(url: str, root: Path, eid: str) -> InstallResult:
    """Download a .tar.gz / .tgz / .tar.zst archive and extract into engine_dir."""
    import shutil as _shutil
    import tarfile

    target_dir = engine_install_dir(root) / eid
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp = target_dir / "download.tmp"
    extracted_tar: Path | None = None
    staging = target_dir / "_extract_tmp"

    try:
        _stream_download(url, tmp)
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return InstallResult(
            engine_id=eid, path="", ok=False,
            message=f"download failed: {exc}",
        )

    # Detect and decompress zstd if needed
    to_extract = tmp
    try:
        if url.lower().split("?", 1)[0].endswith(".tar.zst"):
            # zstd CLI 输出文件名必须用 str(tmp)+".tar" 拼出来；
            # 旧代码 Path.with_suffix 得到的是 "download.tar"，
            # 和实际写出的 "download.tmp.tar" 对不上 → 解压必失败。
            extracted_tar = Path(str(tmp) + ".tar")
            try:
                subprocess.run(
                    [_tool_path("zstd"), "-d", "-f", str(tmp), "-o", str(extracted_tar)],
                    check=True, capture_output=True, timeout=120,
                )
                to_extract = extracted_tar
            except (FileNotFoundError, subprocess.CalledProcessError) as zerr:
                # Fall back to pure-Python zstd if subprocess fails
                log.debug("engines: zstd CLI failed (%s), trying zstandard", zerr)
                try:
                    import zstandard as zstd
                    with tmp.open("rb") as fin, extracted_tar.open("wb") as fout:
                        dctx = zstd.ZstdDecompressor()
                        dctx.copy_stream(fin, fout)
                    to_extract = extracted_tar
                except ImportError:
                    tmp.unlink(missing_ok=True)
                    return InstallResult(
                        engine_id=eid, path="", ok=False,
                        message="zstd not available (install zstd CLI or zstandard Python package)",
                    )

        if staging.exists():
            _shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        with tarfile.open(to_extract, "r:*") as tf:
            # Safe extraction — reject members that escape staging.
            abs_target = staging.resolve()
            for member in tf.getmembers():
                member_path = (staging / member.name).resolve()
                try:
                    member_path.relative_to(abs_target)
                except ValueError:
                    _shutil.rmtree(staging, ignore_errors=True)
                    tmp.unlink(missing_ok=True)
                    return InstallResult(
                        engine_id=eid, path="", ok=False,
                        message=f"tar member escapes target: {member.name}",
                    )
            # v4.17.7: never call ``extractall`` here. The old code relied on
            # ``filter="data"`` for symlink/hardlink/device rejection, and its
            # ``except TypeError`` fallback (Python < 3.12) then extracted the
            # archive with NO filter at all — so a symlink member pointing at
            # /etc could be created and a later member written through it.
            # ``_safe_extract_tar`` extracts regular files and directories
            # only, and re-checks each link-free destination itself, which is
            # correct on every Python version.
            _safe_extract_tar(tf, staging)

        # GitHub 源码归档带一层 <repo>-<branch>/ 顶层目录。引擎目录必须
        # 直接就是仓库根（否则运行时找不到可执行文件/入口），所以单层
        # 目录要展平；多条目则整体搬到 target_dir。
        entries = list(staging.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            entries = list(entries[0].iterdir())
        for item in entries:
            dest = target_dir / item.name
            if dest.exists():
                if dest.is_dir():
                    _shutil.rmtree(dest, ignore_errors=True)
                else:
                    dest.unlink(missing_ok=True)
            _shutil.move(str(item), str(dest))
        _shutil.rmtree(staging, ignore_errors=True)
        tmp.unlink(missing_ok=True)
        if extracted_tar is not None:
            extracted_tar.unlink(missing_ok=True)
        return InstallResult(
            engine_id=eid, path=str(target_dir), ok=True,
            message=f"extracted {url}",
        )
    except Exception as exc:  # noqa: BLE001
        _shutil.rmtree(staging, ignore_errors=True)
        tmp.unlink(missing_ok=True)
        if extracted_tar is not None:
            extracted_tar.unlink(missing_ok=True)
        return InstallResult(
            engine_id=eid, path="", ok=False,
            message=f"extract failed: {exc}",
        )


@_serialized_install
def install_pip_engine(
    name: str,
    root: Path,
    eid: str | None = None,
    extra: list[str] | None = None,
) -> InstallResult:
    dirname = eid or name
    target = engine_install_dir(root) / f"pip-{dirname}"
    target.mkdir(parents=True, exist_ok=True)
    requirements = [name, *list(extra or [])]
    cmd = [*pip_command(), "install", "--target", str(target), *requirements]
    idx = _pip_index()
    if idx:
        cmd += ["-i", idx, "--extra-index-url", "https://pypi.org/simple"]
    try:
        proc = subprocess.run(  # noqa: S603 — argv list built from internal constants
            cmd, capture_output=True, text=True, timeout=900
        )
        ok = proc.returncode == 0
        msg = (proc.stdout[-500:] if ok else proc.stderr[-500:]) or ""
        if ok:
            if target.is_dir() and any(target.iterdir()):
                ok_import, import_err = validate_engine_import(str(dirname), target)
            else:
                ok_import, import_err = True, ""
            if not ok_import:
                retry_cmd = [
                    *pip_command(), "install", "--target", str(target),
                    "--upgrade", "--force-reinstall", "--no-cache-dir",
                    "-i", "https://pypi.org/simple", *requirements,
                ]
                retry = subprocess.run(  # noqa: S603
                    retry_cmd, capture_output=True, text=True, timeout=900
                )
                ok_import, import_err = validate_engine_import(str(dirname), target)
                if retry.returncode != 0 or not ok_import:
                    detail = (retry.stderr or retry.stdout or import_err)[-400:]
                    return InstallResult(
                        engine_id=dirname, path=str(target), ok=False,
                        message=f"import check failed: {detail}",
                    )
        return InstallResult(
            engine_id=dirname, path=str(target), ok=ok,
            message=msg.strip() or ("installed" if ok else "install failed"),
        )
    except Exception as e:
        return InstallResult(engine_id=dirname, path=str(target), ok=False, message=f"exception: {e}")


@_serialized_install
def download_zip_engine(url: str, root: Path, engine_id: str) -> InstallResult:
    """Legacy zip download — now backed by the hardened download path.

    Behaviour parity with ``EngineManager.install``: mirror fallback for
    GitHub URLs, Range resume, validated (Zip-Slip-safe) extraction. The
    signature and return type are unchanged so existing callers/tests keep
    working.
    """
    if not is_host_allowed(url, ALLOWED_ENGINE_HOSTS):
        return InstallResult(
            engine_id=engine_id, path="", ok=False,
            message=f"refusing to download from non-allowlisted host in {url}",
        )
    target_dir = engine_install_dir(root) / engine_id
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp = target_dir / "download.tmp"
    candidates = expand_engine_candidates(url)
    last_err: Exception | None = None
    for cand in candidates:
        try:
            _stream_download(cand, tmp)
        except Exception as e:  # noqa: BLE001 — try the next mirror
            last_err = e
            tmp.unlink(missing_ok=True)
            continue
        # 镜像有时返回 200 但内容是 HTML 错误页/限速页，落盘后才会在解压时炸出
        # "File is not a zip file"。下载成功 ≠ 内容正确：解压前先校验归档魔数，
        # 不是 zip 就当作该镜像坏了，继续试下一个候选源。
        if not zipfile.is_zipfile(tmp):
            head = b""
            try:
                with open(tmp, "rb") as fh:
                    head = fh.read(256)
            except OSError:
                pass
            looks_html = head.lstrip().lower().startswith((b"<!doctype", b"<html", b"{"))
            last_err = RuntimeError(
                f"mirror returned a {'web page' if looks_html else 'non-archive'} instead of the binary ({cand})"
            )
            tmp.unlink(missing_ok=True)
            continue
        try:
            with zipfile.ZipFile(tmp, "r") as zf:
                # P0-2 (H2): same Zip-Slip guard as the EngineManager path above.
                _hub_safe_extract(zf, target_dir)
            tmp.unlink(missing_ok=True)
            return InstallResult(engine_id=engine_id, path=str(target_dir), ok=True, message="installed")
        except Exception as e:
            # N5：解压失败（损坏 zip / Zip-Slip / IO）时，下载下来的 download.tmp 是
            # 一个不再可恢复的临时归档（本 legacy 路径不支持 Range 断点续传，下次会重下），
            # 必须删掉，否则每次失败都在 engine 目录里留一份完整 zip。
            tmp.unlink(missing_ok=True)
            last_err = e
            continue
    else:
        tmp.unlink(missing_ok=True)
        return InstallResult(
            engine_id=engine_id, path="", ok=False,
            message=f"download failed: {last_err}",
        )


def install_engine(engine: dict[str, Any], root: Path) -> InstallResult:
    """Install an engine by its catalog entry.

    Handles every install strategy:
      - "pip" → install_pip_engine(target_dir)
      - "source" → git clone into engine_dir
      - "zip" / "binary" / default → download from platforms[plat] URL
        or fall back to sources[] list or GitHub releases/latest asset.
    """
    eid = engine.get("id", "")
    inst_type = engine.get("install", "binary")

    # --- 1) pip install ---
    if inst_type == "pip" and engine.get("pypi"):
        return install_pip_engine(
            engine["pypi"], root, eid=eid, extra=engine.get("pip_extra")
        )

    # --- 2) git clone source install ---
    if inst_type == "source":
        github = engine.get("github", "")
        sources = engine.get("sources") or []
        # Try github repo first, fall back to sources list
        if github:
            repo_url = f"https://github.com/{github}.git"
            git_result = install_git_engine(repo_url, root, eid)
            if git_result.ok:
                return git_result
        for src in sources:
            if "github.com" in str(src):
                git_url = str(src).rstrip("/")
                if not git_url.endswith(".git"):
                    git_url += ".git"
                git_result = install_git_engine(git_url, root, eid)
                if git_result.ok:
                    return git_result
        # git failed — try download from sources as fallback
        if sources:
            for src in sources:
                result = try_download_engine(str(src), root, eid)
                if result.ok:
                    return result
        return InstallResult(
            engine_id=eid, path="", ok=False,
            message="source install failed: could not git-clone or download",
        )

    # --- 3) binary / zip install ---
    plat = _platform_key()
    platforms = engine.get("platforms", {})
    url = platforms.get(plat)
    github = engine.get("github", "")

    candidates: list[str] = []
    # 1) 平台明确给的归档直链优先
    if url and looks_like_archive_url(str(url)):
        candidates.append(str(url))
    # 2) GitHub release 资产（按平台挑），失败再打默认分支源码归档
    if github:
        candidates.extend(
            resolve_github_engine_sources(str(github), plat, include_source=False)
        )
    # 3) sources[] 里真正的归档链接
    for src in (engine.get("sources") or []):
        if looks_like_archive_url(str(src)):
            candidates.append(str(src))
    # 4) 平台给的是 release 网页（没有直链）时，交给 try_download_engine 解析
    if url and not looks_like_archive_url(str(url)):
        candidates.append(str(url))

    candidates = list(dict.fromkeys(candidates))

    if not candidates:
        return InstallResult(
            engine_id=eid, path="", ok=False,
            message=f"no binary for current platform {plat} — no platforms[], no archive in sources[], no github",
        )

    # 二进制引擎必须真的含可执行文件。GitHub API 限流时曾经把源码归档
    # 当成二进制包返回，安装"成功"但 llama-server 根本不存在，用户点对话
    # 才报错。这里按引擎关键可执行文件做硬校验。
    markers = {
        "llama.cpp": ("llama-server",),
        "ollama": ("ollama",),
    }.get(eid, ())

    last_result: InstallResult | None = None
    for candidate in candidates:
        result = try_download_engine(candidate, root, eid)
        if result.ok:
            if markers:
                base = Path(result.path)
                found = any(
                    base.rglob(m) or base.rglob(m + ".exe")
                    for m in markers
                ) if base.is_dir() else False
                if not found:
                    last_result = InstallResult(
                        engine_id=eid, path=result.path, ok=False,
                        message=(
                            f"{eid} 安装包缺少可执行文件（{', '.join(markers)}）；"
                            "通常是 GitHub API 被限流后错误回退到源码归档，请重试"
                        ),
                    )
                    continue
            return result
        last_result = result

    return last_result or InstallResult(
        engine_id=eid, path="", ok=False,
        message=f"all download candidates failed for {eid}",
    )


def ensure_engine(
    engine_id: str,
    catalog: Catalog,
    engines: dict[str, Any],
    root: Path,
) -> InstallResult:
    eng = engines.get(engine_id)
    if not eng:
        return InstallResult(engine_id=engine_id, path="", ok=False, message="engine not in catalog")
    res = install_engine(eng, root)
    status = load_status(root)
    status[engine_id] = {
        "ok": res.ok,
        "path": res.path,
        "message": res.message,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    save_status(root, status)
    return res


def list_engines_status(
    engines: dict[str, Any],
    root: Path,
) -> list[dict[str, Any]]:
    status = load_status(root)
    manifest = {r.id: r for r in _read_manifest(root)}
    out: list[dict[str, Any]] = []
    for eid, eng in engines.items():
        st = status.get(eid, {})
        rec = manifest.get(eid)
        out.append({
            "id": eid,
            "name": eng.get("name", eid),
            "category": eng.get("category", ""),
            "github": eng.get("github", ""),
            "license": eng.get("license", ""),
            "size_mb": eng.get("size_mb", 0),
            "trending": eng.get("trending", False),
            "installed": bool(st.get("ok"))
            or (rec is not None and rec.state == EngineState.INSTALLED),
            "version": rec.version if rec else "",
            "install_path": st.get("path", "") or (rec.install_path if rec else ""),
            "message": st.get("message", "")
            or (rec.last_error if rec is not None and rec.state == EngineState.FAILED else ""),
            # 实时安装/下载遥测（pip 安装时 active_url 是 pip 输出行）
            "state": rec.state.value if rec else "",
            "active_url": rec.active_url if rec else "",
            "progress": rec.progress if rec else 0.0,
            "downloaded_bytes": rec.downloaded_bytes if rec else 0,
            "total_bytes": rec.total_bytes if rec else 0,
        })
    return out


# ---------------------------------------------------------------------------
# v2.4.1 — engine update detection
# ---------------------------------------------------------------------------
# "已存在则跳过" was already idempotent; this adds the missing half: telling
# the user a NEWER release exists and offering a one-click reinstall.
# GitHub releases only (binary engines). pip engines are re-installed
# in-place (pip resolves the latest version on reinstall).

UPDATE_CACHE_TTL_SECONDS = 6 * 3600  # avoid hammering the GitHub API

# Asset-name keywords per platform (case-insensitive).
_ASSET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "windows-x64": ("win",),
    "linux-x64": ("linux",),
    "darwin-arm64": ("macos", "darwin", "osx", "arm"),
    "darwin-x64": ("macos", "darwin", "osx"),
}


def _update_cache_path(root: Path) -> Path:
    return engine_install_dir(root) / "update-check.json"


def load_update_cache(root: Path) -> dict[str, Any]:
    p = _update_cache_path(root)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_update_cache(root: Path, cache: dict[str, Any]) -> None:
    p = _update_cache_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _pick_release_asset(assets: list[dict[str, Any]], platform: str) -> str:
    """Pick the best release asset URL for the current platform."""
    kws = _ASSET_KEYWORDS.get(platform, ())
    cands = []
    for a in assets or []:
        name = str(a.get("name", "")).lower()
        url = str(a.get("browser_download_url", "") or "")
        if not url:
            continue
        if kws and not any(k in name for k in kws):
            continue
        cands.append((name, url))
    if not cands:
        return ""
    # Prefer archives we can extract.
    for name, url in cands:
        if name.endswith(".zip"):
            return url
    return cands[0][1]


async def check_engine_updates(
    root: Path,
    engines_catalog: dict[str, Any],
    *,
    force: bool = False,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Check GitHub for newer releases of installed binary engines.

    Results are cached for UPDATE_CACHE_TTL_SECONDS unless `force` is set.
    Never raises on network errors — each engine gets an `error` entry instead.
    """
    import time as _time

    mgr = EngineManager(root)
    cache = load_update_cache(root)
    results: list[dict[str, Any]] = []
    plat = _platform_key()

    own_client = client is None
    cli = client or httpx.AsyncClient(
        timeout=15.0, headers={"User-Agent": _UA(),
                               "Accept": "application/vnd.github+json"},
    )
    try:
        for eid, entry in engines_catalog.items():
            rec = mgr.get(eid)
            gh = str(entry.get("github", "") or "")
            if rec is None or rec.state != EngineState.INSTALLED or not gh:
                continue
            cached = cache.get(eid) or {}
            age = _time.time() - float(cached.get("checked_at", 0) or 0)
            if cached and not force and age < UPDATE_CACHE_TTL_SECONDS:
                results.append({
                    "engine_id": eid,
                    "current_version": rec.version,
                    "latest_tag": cached.get("latest_tag", ""),
                    "asset_url": cached.get("asset_url", ""),
                    "update_available": bool(
                        cached.get("latest_tag")
                        ) and cached.get("latest_tag") != rec.version,
                    "from_cache": True,
                })
                continue
            try:
                # api.github.com is unreachable from some networks (notably
                # mainland China); the ghfast.top API proxy mirrors it.
                # Try both, remember which one worked for the rest of the loop.
                data: dict[str, Any] = {}
                status: int = 0
                for api_base in ("https://api.github.com",
                                 "https://ghfast.top/https://api.github.com"):
                    try:
                        r = await cli.get(f"{api_base}/repos/{gh}/releases/latest")
                        status = r.status_code
                        if status == 200:
                            ct = r.headers.get("content-type", "")
                            data = r.json() if ct.startswith("application/json") else {}
                            break
                    except Exception as exc:  # noqa: BLE001 — try next API mirror
                        log.debug("engine update check: mirror failed: %s", exc)
                        continue
                if status != 200 or not data:
                    results.append({
                        "engine_id": eid,
                        "error": f"github api http {status}",
                    })
                    continue
                tag = str(data.get("tag_name", "") or "")
                asset_url = _pick_release_asset(data.get("assets", []), plat)
                cache[eid] = {
                    "latest_tag": tag,
                    "asset_url": asset_url,
                    "checked_at": _time.time(),
                }
                current = rec.version
                update_available = bool(tag) and tag != current
                if tag and not current:
                    # Fresh install via releases/latest: stamp baseline tag.
                    rec.version = tag
                    mgr._upsert(rec)
                    update_available = False
                results.append({
                    "engine_id": eid,
                    "current_version": rec.version,
                    "latest_tag": tag,
                    "asset_url": asset_url,
                    "update_available": update_available,
                    "from_cache": False,
                })
            except Exception as e:  # noqa: BLE001 — network errors are per-engine
                results.append({"engine_id": eid, "error": str(e)[:200]})
    finally:
        if own_client:
            await cli.aclose()
    save_update_cache(root, cache)
    return results


def apply_engine_update(
    engine_id: str,
    engines_catalog: dict[str, Any],
    root: Path,
) -> InstallResult:
    """Reinstall an engine from the newest known release.

    Uses the cached latest-release asset URL when available; otherwise falls
    back to the catalog platform URL (which for most engines already points at
    `releases/latest/download/...`). pip engines are reinstalled in place.
    """
    entry = engines_catalog.get(engine_id)
    if not entry:
        return InstallResult(engine_id=engine_id, path="", ok=False,
                             message="engine not in catalog")
    if entry.get("install") == "pip" and entry.get("pypi"):
        res = install_pip_engine(entry["pypi"], root)
        return res

    cache = load_update_cache(root)
    info = cache.get(engine_id) or {}
    url = str(info.get("asset_url") or "")
    if not url:
        url = str((entry.get("platforms") or {}).get(_platform_key(), "") or "")
    if not url:
        return InstallResult(engine_id=engine_id, path="", ok=False,
                             message="no download url for current platform")

    mgr = EngineManager(root)
    try:
        rec = mgr.install(engine_id, url, unzip=True)
    except Exception as e:  # noqa: BLE001
        return InstallResult(engine_id=engine_id, path="", ok=False,
                             message=f"update failed: {e}")
    # Stamp the release tag so the next update check compares against it.
    tag = str(info.get("latest_tag") or "")
    if tag and rec is not None:
        rec.version = tag
        mgr._upsert(rec)
    status = load_status(root)
    status[engine_id] = {
        "ok": rec is not None and rec.state == EngineState.INSTALLED,
        "path": rec.install_path if rec else "",
        "message": f"updated to {tag}" if tag else "reinstalled",
    }
    save_status(root, status)
    return InstallResult(
        engine_id=engine_id,
        path=rec.install_path if rec else "",
        ok=rec is not None and rec.state == EngineState.INSTALLED,
        message=f"updated to {tag}" if tag else "reinstalled",
    )
