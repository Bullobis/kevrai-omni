"""GPU detection — nvidia-smi / rocm-smi / Apple Silicon / Ascend NPU.

All detection paths are wrapped in try/except. A failure of one vendor's
detector must never cause the whole `detect()` call to raise.
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


class GPUInfo(BaseModel):
    """A single GPU/NPU device."""

    vendor: str  # "nvidia" | "amd" | "apple" | "ascend" | "cpu"
    name: str
    vram_mb: int = 0
    driver_version: str = ""
    compute_capability: str = ""  # e.g. "8.9" for NVIDIA
    index: int = 0
    uuid: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Vendor-specific probes
# ---------------------------------------------------------------------------

_NVIDIA_QUERY = (
    "--query-gpu=index,name,memory.total,driver_version,"
    "compute_cap,uuid"
)
_NVIDIA_PATHS = (
    "/usr/bin/nvidia-smi",
    "/usr/local/bin/nvidia-smi",
    "/opt/nvidia/bin/nvidia-smi",
    r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
    r"C:\Windows\System32\nvidia-smi.exe",
)


def _first_existing(paths: tuple[str, ...]) -> str | None:
    for p in paths:
        try:
            if os.path.isfile(p):
                return p
        except OSError:
            continue
    return shutil.which("nvidia-smi")


def _parse_nvidia_csv(stdout: str) -> list[GPUInfo]:
    out: list[GPUInfo] = []
    for line in stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        try:
            vram = int(parts[2])  # MiB
        except ValueError:
            vram = 0
        out.append(
            GPUInfo(
                vendor="nvidia",
                index=idx,
                name=parts[1] or "NVIDIA GPU",
                vram_mb=vram,
                driver_version=parts[3],
                compute_capability=parts[4],
                uuid=parts[5],
            )
        )
    return out


async def _detect_nvidia() -> list[GPUInfo]:
    binpath = _first_existing(_NVIDIA_PATHS)
    if not binpath:
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            binpath, _NVIDIA_QUERY, "--format=csv,noheader",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        except asyncio.TimeoutError:
            proc.kill()
            return []
        if proc.returncode != 0:
            return []
        return _parse_nvidia_csv(stdout_b.decode("utf-8", errors="replace"))
    except (FileNotFoundError, PermissionError, OSError):
        return []


async def _detect_amd() -> list[GPUInfo]:
    """Try `rocm-smi --json`. Older ROCm doesn't support JSON; we also accept CSV."""
    binpath = shutil.which("rocm-smi") or "/opt/rocm/bin/rocm-smi"
    candidates = (binpath,) if os.path.isfile(binpath) else ()
    if not candidates:
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            candidates[0], "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        except asyncio.TimeoutError:
            proc.kill()
            return []
        if proc.returncode != 0:
            return []
        data = json.loads(stdout_b.decode("utf-8", errors="replace") or "{}")
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return []

    # rocm-smi JSON layout: {"card0": {"Name": "...", "VRAM Total Memory": "..."}}
    out: list[GPUInfo] = []
    for key, info in data.items():
        if not key.lower().startswith("card"):
            continue
        try:
            idx = int(re.sub(r"\D", "", key) or "0")
        except ValueError:
            idx = 0
        vram_raw = str(info.get("VRAM Total Memory", info.get("vram_total", "0")))
        m = re.search(r"(\d+)", vram_raw.replace(",", ""))
        vram_mb = int(m.group(1)) if m else 0
        # Some rocm versions report in bytes; if huge, convert.
        if vram_mb > 1_000_000:
            vram_mb = vram_mb // (1024 * 1024)
        out.append(
            GPUInfo(
                vendor="amd",
                index=idx,
                name=str(info.get("Name", info.get("name", "AMD GPU"))),
                vram_mb=vram_mb,
                driver_version=str(info.get("Driver Version", "")),
                raw=info if isinstance(info, dict) else {},
            )
        )
    return out


async def _detect_ascend() -> list[GPUInfo]:
    """Huawei Ascend NPU — `npu-smi info` is text."""
    binpath = shutil.which("npu-smi") or "/usr/local/Ascend/driver/tools/npu-smi"
    if not os.path.isfile(binpath):
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            binpath, "info", "-l",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return []
    if proc.returncode != 0:
        return []

    out: list[GPUInfo] = []
    text = stdout_b.decode("utf-8", errors="replace")
    # Heuristic parse: lines that look like "NPU ID    : 0" / "Memory ...
    npu_id: int | None = None
    name = "Ascend NPU"
    vram_mb = 0
    for line in text.splitlines():
        s = line.strip()
        if "NPU ID" in s and ":" in s:
            try:
                npu_id = int(s.split(":", 1)[1].strip())
            except ValueError:
                npu_id = 0
        if "Name" in s and ":" in s:
            name = s.split(":", 1)[1].strip() or name
        if re.search(r"Memory\s*\(MiB\)|HBM", s):
            try:
                vram_mb = int(re.findall(r"\d+", s)[0])
            except (ValueError, IndexError):
                pass
        if npu_id is not None and "Health" in s:
            out.append(
                GPUInfo(
                    vendor="ascend",
                    index=npu_id,
                    name=name,
                    vram_mb=vram_mb,
                )
            )
            npu_id = None
            name = "Ascend NPU"
            vram_mb = 0
    return out


async def _detect_apple() -> list[GPUInfo]:
    """Apple Silicon: integrate via system_profiler (mac only) or sysctl."""
    if sys.platform != "darwin":
        # On non-darwin, Apple GPU is unknown unless we want to call sysctl
        # (`machdep.cpu.brand_string`) for a hint, but that's not actually a
        # GPU and is misleading.
        return []

    # system_profiler returns "Metal Support: ..."

    async def _sp() -> str:
        proc = await asyncio.create_subprocess_exec(
            "system_profiler", "SPDisplaysDataType",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            return ""
        return stdout_b.decode("utf-8", errors="replace")

    try:
        text = await _sp()
    except (FileNotFoundError, OSError):
        return []

    # Parse "Chipset Model: Apple M2 Pro" lines and pairs.
    gpus: list[GPUInfo] = []
    idx = 0
    for block in re.split(r"\n\s*\n", text):
        name_m = re.search(r"Chipset Model:\s*(.+)", block)
        vram_m = re.search(r"VRAM \(Total\):\s*(\d+)\s*(\w+)", block)
        if not name_m:
            continue
        name = name_m.group(1).strip()
        vram_mb = 0
        if vram_m:
            v = int(vram_m.group(1))
            unit = vram_m.group(2).upper()
            vram_mb = v * 1024 if unit.startswith("GB") else v
        gpus.append(GPUInfo(vendor="apple", index=idx, name=name, vram_mb=vram_mb))
        idx += 1
    return gpus


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class _DetectResult:
    gpus: list[GPUInfo]


async def detect() -> list[GPUInfo]:
    """Detect all GPUs/NPUs. Never raises; failures degrade to `[]`."""
    results = await asyncio.gather(
        _detect_nvidia(),
        _detect_amd(),
        _detect_apple(),
        _detect_ascend(),
        return_exceptions=True,
    )
    out: list[GPUInfo] = []
    for r in results:
        if isinstance(r, BaseException):
            continue
        out.extend(r)
    if out:
        return out

    # Fallback: report the CPU so the UI never shows a totally empty list.
    cpu_name = _cpu_name()
    return [
        GPUInfo(
            vendor="cpu",
            name=cpu_name,
            vram_mb=0,
            raw={"fallback": True},
        )
    ]


def _cpu_name() -> str:
    try:
        if sys.platform == "darwin":
            out = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                stderr=subprocess.DEVNULL,
            ).decode().strip()
            return out or platform.processor() or "CPU"
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        pass
    return platform.processor() or "CPU"


# ---------------------------------------------------------------------------
# Sync convenience (for CLI / startup hooks)
# ---------------------------------------------------------------------------


def detect_sync() -> list[GPUInfo]:
    """Run the async detector in a new event loop."""
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(detect())
        finally:
            loop.close()
    except Exception:
        return []
