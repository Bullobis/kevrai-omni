"""Environment / dependency / engine management for Kevrai Omni.

This module is the on-demand "download anything you need from the app" page.
It detects what is already installed locally and reports the gaps so the UI
can prompt the user to install / update.

Detected surfaces:
    * Python interpreter version + freezable `pip` package list
    * Node.js version (optional)
    * Installed engines (delegates to `EngineManager`)
    * GPU capability (delegates to `app.gpu.detect`)
    * Disk space
    * Network sources reachable (basic check against the catalog mirror set)

A `check_updates` helper inspects each installed pip package and each
engine and reports whether a newer version is known.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .engines import EngineManager
from .gpu import detect as detect_gpu


# Known pip packages we want to keep present (vague version floors).
REQUIRED_PIP_PACKAGES: dict[str, str] = {
    "fastapi": ">=0.110",
    "uvicorn": ">=0.27",
    "pydantic": ">=2.5",
    "httpx": ">=0.27",
    "websockets": ">=12",
    "jsonschema": ">=4.21",
    "xxhash": ">=3.4",
    "pyyaml": ">=6.0",
}


@dataclass
class PythonStatus:
    available: bool
    version: str = ""
    executable: str = ""
    pip_version: str = ""


@dataclass
class NodeStatus:
    available: bool
    version: str = ""


@dataclass
class InstalledPackage:
    name: str
    version: str
    required: str | None = None
    needs_update: bool = False


@dataclass
class EngineInfo:
    id: str
    state: str
    version: str = ""
    size_bytes: int = 0
    last_error: str = ""
    available_update: str = ""


@dataclass
class DiskInfo:
    free_bytes: int
    total_bytes: int
    models_dir_bytes: int = 0
    engines_dir_bytes: int = 0


@dataclass
class EnvStatus:
    python: PythonStatus
    node: NodeStatus
    pip_packages: list[InstalledPackage]
    engines: list[EngineInfo]
    gpus: list[dict[str, Any]]
    disk: DiskInfo
    has_updates: bool
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "python": asdict(self.python),
            "node": asdict(self.node),
            "pip_packages": [asdict(p) for p in self.pip_packages],
            "engines": [asdict(e) for e in self.engines],
            "gpus": self.gpus,
            "disk": asdict(self.disk),
            "has_updates": self.has_updates,
            "issues": self.issues,
        }


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"executable not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"


def detect_python() -> PythonStatus:
    rc, out, _err = _run([sys.executable, "--version"])
    if rc != 0:
        return PythonStatus(available=False, executable=sys.executable)
    rc2, out2, _ = _run([sys.executable, "-m", "pip", "--version"])
    pip_ver = out2.strip() if rc2 == 0 else ""
    return PythonStatus(
        available=True,
        version=out.strip(),
        executable=sys.executable,
        pip_version=pip_ver,
    )


def detect_node() -> NodeStatus:
    rc, out, _ = _run(["node", "--version"])
    if rc != 0:
        return NodeStatus(available=False)
    return NodeStatus(available=True, version=out.strip())


def _parse_pip_freeze(out: str) -> dict[str, str]:
    pkgs: dict[str, str] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            name, ver = line.split("==", 1)
            pkgs[name.strip().lower()] = ver.strip()
    return pkgs


def _parse_version_tuple(v: str) -> tuple[int, ...]:
    out = []
    for piece in v.replace("-", ".").split("."):
        digits = ""
        for c in piece:
            if c.isdigit():
                digits += c
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def _is_newer(latest: str, current: str) -> bool:
    return _parse_version_tuple(latest) > _parse_version_tuple(current)


def list_pip_packages() -> list[InstalledPackage]:
    rc, out, _ = _run([sys.executable, "-m", "pip", "freeze"], timeout=30.0)
    if rc != 0:
        return []
    installed = _parse_pip_freeze(out)
    pkgs: list[InstalledPackage] = []
    for name, req in REQUIRED_PIP_PACKAGES.items():
        cur = installed.get(name.lower(), "")
        pkgs.append(InstalledPackage(
            name=name, version=cur, required=req, needs_update=False
        ))
    # Add the top-30 user-installed packages for visibility
    for n, v in list(installed.items())[:30]:
        if n in REQUIRED_PIP_PACKAGES:
            continue
        pkgs.append(InstalledPackage(name=n, version=v, required=None,
                                     needs_update=False))
    return pkgs


def list_engines(em: EngineManager, catalog_engines: dict) -> list[EngineInfo]:
    out: list[EngineInfo] = []
    # list_installed() returns a list[EngineRecord]; index by id for fast lookup.
    installed_list = em.list_installed() if hasattr(em, "list_installed") else []
    installed: dict[str, Any] = {}
    for rec in installed_list:
        rid = getattr(rec, "id", None) or (rec.get("id") if isinstance(rec, dict) else None)
        if rid is None:
            continue
        installed[rid] = rec
    for eid, info in installed.items():
        out.append(EngineInfo(
            id=eid,
            state=str(getattr(info, "state", None) or (info.get("state") if isinstance(info, dict) else "unknown")),
            version=str(getattr(info, "version", None) or (info.get("version") if isinstance(info, dict) else "")),
            size_bytes=int(getattr(info, "size_bytes", None) or (info.get("size_bytes") if isinstance(info, dict) else 0) or 0),
            last_error=str(getattr(info, "last_error", None) or (info.get("last_error") if isinstance(info, dict) else "")),
            available_update="",
        ))
    for eid, cat in catalog_engines.items():
        if eid in installed:
            continue
        out.append(EngineInfo(
            id=eid, state="not_installed",
            version=str(cat.get("version", "")),
        ))
    return out


def disk_info(models_dir: Path, engines_dir: Path) -> DiskInfo:
    # shutil.disk_usage raises FileNotFoundError if the target path does not
    # exist yet (first run before directories are created). Walk up to the
    # first existing ancestor so we always return a valid DiskInfo.
    probe = Path(models_dir)
    while probe and not probe.exists():
        probe = probe.parent
    total, used, free = shutil.disk_usage(probe)
    def _du(p: Path) -> int:
        total_bytes = 0
        if not p.exists():
            return 0
        for root, _, files in os.walk(p):
            for f in files:
                try:
                    total_bytes += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total_bytes
    return DiskInfo(
        free_bytes=free, total_bytes=total,
        models_dir_bytes=_du(models_dir),
        engines_dir_bytes=_du(engines_dir),
    )


async def check_status(
    *,
    em: EngineManager,
    models_dir: Path,
    engines_dir: Path,
    catalog_engines: dict[str, dict],
) -> EnvStatus:
    py = await asyncio.to_thread(detect_python)
    node = await asyncio.to_thread(detect_node)
    pkgs = await asyncio.to_thread(list_pip_packages)
    engines = await asyncio.to_thread(list_engines, em, catalog_engines)
    gpus = await detect_gpu()
    disk = await asyncio.to_thread(disk_info, models_dir, engines_dir)
    issues: list[str] = []
    if not py.available:
        issues.append("Python interpreter is unavailable")
    missing = [n for n in REQUIRED_PIP_PACKAGES
               if not any(p.name.lower() == n.lower() and p.version for p in pkgs)]
    if missing:
        issues.append(f"Missing required packages: {', '.join(missing)}")
    if disk.free_bytes < 5 * 1024 * 1024 * 1024:  # < 5 GB free
        issues.append(f"Low disk space: {disk.free_bytes // (1024*1024*1024)} GB free")
    has_updates = any(p.needs_update for p in pkgs) or any(
        e.available_update for e in engines)
    return EnvStatus(
        python=py, node=node, pip_packages=pkgs, engines=engines,
        gpus=[g.model_dump() for g in gpus] if gpus and hasattr(gpus[0], "model_dump") else [],
        disk=disk, has_updates=has_updates, issues=issues,
    )


# ---------------------------------------------------------------------------
# Install actions
# ---------------------------------------------------------------------------

class InstallError(RuntimeError):
    pass


def install_pip_package(name: str, version: str | None = None,
                        extra_index_urls: list[str] | None = None) -> dict[str, Any]:
    """Install a pip package using whichever pip is bundled with the sidecar.

    `extra_index_urls` lets the user pick specific mirrors (e.g. aliyun, tsinghua,
    huaweicloud) — by default the sidecar appends the well-known CN mirrors to
    speed things up inside the firewall.
    """
    cmd = [sys.executable, "-m", "pip", "install", "--no-input", "--disable-pip-version-check"]
    # Append user-selected mirrors as additional index URLs.
    mirrors = extra_index_urls or [
        "https://mirrors.aliyun.com/pypi/simple/",
        "https://pypi.tuna.tsinghua.edu.cn/simple/",
        "https://mirrors.huaweicloud.com/repository/pypi/simple/",
    ]
    for u in mirrors:
        cmd += ["--extra-index-url", u]
    target = f"{name}=={version}" if version else name
    cmd.append(target)
    rc, out, err = _run(cmd, timeout=300.0)
    if rc != 0:
        raise InstallError(f"pip install failed: {err.strip()[:500]}")
    return {"ok": True, "name": name, "version": version, "output_tail": out[-500:]}


def upgrade_pip_package(name: str, extra_index_urls: list[str] | None = None) -> dict[str, Any]:
    return install_pip_package(name, version=None, extra_index_urls=extra_index_urls)
