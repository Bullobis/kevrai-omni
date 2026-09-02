"""Engine manager — install, verify, uninstall engines (llama.cpp, MNN, vllm, …).

New state-machine API (``EngineManager``) tracks every install through
``EngineState`` transitions. The legacy helpers
(``ensure_engine``, ``install_pip_engine``, ``download_zip_engine``,
``load_status``, ``list_engines_status``, etc.) are preserved so existing
tests and routes still work.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
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

CHUNK_SIZE = 1 << 20  # 1 MiB


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
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
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
        recs = self._read()
        for i, r in enumerate(recs):
            if r.id == rec.id:
                recs[i] = rec
                self._write(recs)
                return
        recs.append(rec)
        self._write(recs)

    def _set_state(self, engine_id: str, state: EngineState, **kw: Any) -> EngineRecord | None:
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
        )
        recs.append(rr)
        self._write(recs)
        return rr

    # --- mutations ---

    def install(
        self,
        engine_id: str,
        url: str,
        sha256: str | None = None,
        *,
        expected_size: int | None = None,
        unzip: bool = True,
    ) -> EngineRecord:
        """Install an engine binary from `url`. Idempotent on sha match."""
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

        self._set_state(
            engine_id, EngineState.DOWNLOADING,
            source_url=url, install_path=str(target_dir),
        )
        try:
            with httpx.Client(follow_redirects=True, timeout=60.0) as client, \
                    client.stream("GET", url) as r:
                r.raise_for_status()
                with tmp.open("wb") as fh:
                    for chunk in r.iter_bytes(CHUNK_SIZE):
                        fh.write(chunk)
            if unzip and url.endswith(".zip"):
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
                    zf.extractall(target_dir)
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
                        try:
                            p.unlink()
                        except OSError:
                            pass
            self._set_state(engine_id, EngineState.NOT_INSTALLED)
            # Remove the manifest entry entirely
            recs = self._read()
            recs = [r for r in recs if r.id != engine_id]
            self._write(recs)
            return True
        except Exception:
            return False

    # --- pip-based engines ---

    def install_pip(self, pypi_name: str, engine_id: str | None = None) -> EngineRecord:
        eid = engine_id or pypi_name
        target = self.engine_dir() / f"pip-{pypi_name}"
        target.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "pip", "install", "--target", str(target), pypi_name]
        idx = os.environ.get("KEVRAI_PIP_INDEX", "https://mirrors.tencent.com/pypi/simple/")
        cmd += ["-i", idx, "--extra-index-url", "https://pypi.org/simple"]
        self._set_state(eid, EngineState.DOWNLOADING, install_path=str(target),
                        install_mode="pip")
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=900
            )
        except Exception as e:
            self._set_state(eid, EngineState.FAILED,
                            last_error=f"pip exception: {e}",
                            install_mode="pip")
            raise
        if proc.returncode != 0:
            self._set_state(eid, EngineState.FAILED,
                            last_error=(proc.stderr or "")[-300:],
                            install_mode="pip")
            raise RuntimeError(f"pip install of {pypi_name} failed: rc={proc.returncode}")
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


def install_pip_engine(name: str, root: Path) -> InstallResult:
    target = engine_install_dir(root) / f"pip-{name}"
    target.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "install", "--target", str(target), name]
    idx = _pip_index()
    if idx:
        cmd += ["-i", idx, "--extra-index-url", "https://pypi.org/simple"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        ok = proc.returncode == 0
        msg = (proc.stdout[-500:] if ok else proc.stderr[-500:]) or ""
        return InstallResult(
            engine_id=name, path=str(target), ok=ok,
            message=msg.strip() or ("installed" if ok else "install failed"),
        )
    except Exception as e:
        return InstallResult(engine_id=name, path=str(target), ok=False, message=f"exception: {e}")


def download_zip_engine(url: str, root: Path, engine_id: str) -> InstallResult:
    if not is_host_allowed(url, ALLOWED_ENGINE_HOSTS):
        return InstallResult(
            engine_id=engine_id, path="", ok=False,
            message=f"refusing to download from non-allowlisted host in {url}",
        )
    target_dir = engine_install_dir(root) / engine_id
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp = target_dir / "download.tmp"
    try:
        with httpx.Client(follow_redirects=True, timeout=60.0) as client, \
                client.stream("GET", url) as r:
            r.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        with zipfile.ZipFile(tmp, "r") as zf:
            zf.extractall(target_dir)
        tmp.unlink(missing_ok=True)
        return InstallResult(engine_id=engine_id, path=str(target_dir), ok=True, message="installed")
    except Exception as e:
        return InstallResult(engine_id=engine_id, path="", ok=False, message=f"download failed: {e}")


def install_engine(engine: dict[str, Any], root: Path) -> InstallResult:
    eid = engine.get("id", "")
    if engine.get("install") == "pip" and engine.get("pypi"):
        return install_pip_engine(engine["pypi"], root)

    plat = _platform_key()
    platforms = engine.get("platforms", {})
    url = platforms.get(plat)
    if not url:
        return InstallResult(
            engine_id=eid, path="", ok=False,
            message=f"no binary for current platform {plat}",
        )
    return download_zip_engine(url, root, eid)


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
            "message": st.get("message", ""),
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
    client: "httpx.AsyncClient | None" = None,
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
        timeout=15.0, headers={"User-Agent": "kevrai-omni/2.4.1",
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
                r = await cli.get(f"https://api.github.com/repos/{gh}/releases/latest")
                if r.status_code != 200:
                    results.append({
                        "engine_id": eid,
                        "error": f"github api http {r.status_code}",
                    })
                    continue
                data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
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
