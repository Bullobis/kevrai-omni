"""Hardware detection — CPU / RAM / GPU / disk / network bandwidth.

Extends gpu.py with a full system snapshot used by the recommender:
    * CPU model, physical cores, max frequency
    * Total RAM (psutil, /proc/meminfo fallback)
    * Free disk on the data root
    * GPU list (delegates to gpu.detect)
    * Network bandwidth estimate (small ranged download probe, best-effort)

All probes are wrapped — a failure degrades to defaults, never raises.
"""
from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .gpu import detect as detect_gpus


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------

def _cpu_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "name": platform.processor() or "CPU",
        "physical_cores": os.cpu_count() or 1,
        "logical_cores": os.cpu_count() or 1,
        "mhz_max": 0,
        "avx2": False,
        "avx512": False,
    }
    try:
        if sys_platform_is_linux():
            model = ""
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                flags = ""
                for line in f:
                    if line.startswith("model name") and not model:
                        model = line.split(":", 1)[1].strip()
                    if line.startswith("flags") and not flags:
                        flags = line.split(":", 1)[1]
                    if model and flags:
                        break
            if model:
                info["name"] = model
            if flags:
                info["avx2"] = " avx2 " in f" {flags} "
                info["avx512"] = " avx512f " in f" {flags} "
            try:
                with open("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", encoding="utf-8") as f:
                    info["mhz_max"] = int(f.read().strip()) // 1000
            except OSError:
                pass
        elif sys_platform_is_mac():
            import subprocess
            out = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], stderr=subprocess.DEVNULL
            ).decode().strip()
            if out:
                info["name"] = out
    except Exception:
        pass
    return info


def sys_platform_is_linux() -> bool:
    return sys_platform().startswith("linux")


def sys_platform_is_mac() -> bool:
    return sys_platform() == "darwin"


def sys_platform() -> str:
    return platform.system().lower() if hasattr(platform, "system") else ""


def _physical_cores() -> int:
    try:
        import psutil
        return psutil.cpu_count(logical=False) or os.cpu_count() or 1
    except Exception:
        return os.cpu_count() or 1


# ---------------------------------------------------------------------------
# RAM
# ---------------------------------------------------------------------------

def _total_ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        pass
    # /proc/meminfo fallback (linux)
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    kb = int(line.split()[1])
                    return round(kb / (1024 ** 2), 1)
    except Exception:
        pass
    # Windows: ctypes GlobalMemoryStatusEx
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return round(stat.ullTotalPhys / (1024 ** 3), 1)
    except Exception:
        return 8.0


# ---------------------------------------------------------------------------
# Disk
# ---------------------------------------------------------------------------

def _disk_info(path: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(str(path))
        return {
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "total_gb": round(usage.total / (1024 ** 3), 1),
        }
    except Exception:
        return {"free_gb": 0.0, "total_gb": 0.0}


# ---------------------------------------------------------------------------
# Bandwidth probe (best-effort, bounded)
# ---------------------------------------------------------------------------

_BW_PROBE_URLS = (
    # Small files on well-known CDNs; we only need a few hundred KB.
    "https://speed.cloudflare.com/__down?bytes=2000000",
    "https://hf-mirror.com/Qwen/Qwen2.5-0.5B-Instruct/resolve/main/README.md",
)


async def _measure_bandwidth_mbps(timeout_s: float = 4.0) -> float:
    """Rough downstream estimate. Returns 0.0 on failure (never raises)."""
    import urllib.request

    for url in _BW_PROBE_URLS:
        try:
            t0 = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": "KevraiStudio/2.3"})
            received = 0

            def _run() -> int:
                nonlocal received
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        received += len(chunk)
                        if time.monotonic() - t0 > timeout_s:
                            break
                return received

            received = await asyncio.to_thread(_run)
            elapsed = time.monotonic() - t0
            if received > 100_000 and elapsed > 0.05:
                return round(received * 8 / elapsed / 1_000_000, 1)
        except Exception:
            continue
    return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _bandwidth_tier(mbps: float) -> str:
    if mbps <= 0:
        return "unknown"
    if mbps < 20:
        return "slow"        # < 20 Mbps：几十 GB 模型要下很久
    if mbps < 100:
        return "medium"      # 20-100 Mbps
    return "fast"            # >= 100 Mbps


def _score(hw: dict[str, Any]) -> int:
    """0-100 综合硬件评分（LLM 推理导向）。"""
    ram = hw["ram_total_gb"]
    vram = hw["gpu_total_vram_gb"]
    cores = hw["cpu"]["physical_cores"]
    score = 10
    score += min(25, int(ram / 2))          # 50GB RAM 封顶 25
    score += min(30, int(vram))             # 30GB VRAM 封顶 30（权重最高）
    score += min(15, cores * 1.5)           # 10 核封顶 15
    if hw["cpu"]["avx512"]:
        score += 5
    elif hw["cpu"]["avx2"]:
        score += 3
    if hw.get("gpu_vendor") in ("nvidia", "apple"):
        score += 10
    return max(1, min(100, int(score)))


async def detect_hardware(data_root: Path) -> dict[str, Any]:
    """Full hardware snapshot. Never raises."""
    cpu = _cpu_info()
    cpu["physical_cores"] = _physical_cores()

    gpus = await detect_gpus()
    gpu_total_vram_gb = 0.0
    best_gpu = None
    for g in gpus:
        if g.vram_mb and g.vram_mb > (best_gpu.vram_mb if best_gpu else 0):
            best_gpu = g
        gpu_total_vram_gb += g.vram_mb / 1024
    has_real_gpu = any(g.vendor != "cpu" for g in gpus)

    bandwidth_mbps = await _measure_bandwidth_mbps()

    hw = {
        "cpu": cpu,
        "ram_total_gb": _total_ram_gb(),
        "gpus": [g.model_dump() for g in gpus],
        "gpu_count": len(gpus),
        "gpu_total_vram_gb": round(gpu_total_vram_gb, 1),
        "gpu_vendor": (best_gpu.vendor if best_gpu else "cpu"),
        "gpu_name": (best_gpu.name if best_gpu else cpu["name"]),
        "gpu_best_vram_gb": round((best_gpu.vram_mb / 1024) if best_gpu else 0, 1),
        "has_discrete_gpu": has_real_gpu,
        "disk": _disk_info(data_root),
        "bandwidth_mbps": bandwidth_mbps,
        "bandwidth_tier": _bandwidth_tier(bandwidth_mbps),
        "platform": f"{platform.system()} {platform.machine()}",
    }
    hw["score"] = _score(hw)
    return hw
