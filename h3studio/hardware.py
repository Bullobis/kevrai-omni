# -*- coding: utf-8 -*-
"""
hardware.py — 多芯片硬件检测与最优策略自动配置
=================================================
支持检测：
  · NVIDIA GPU（CUDA，nvidia-smi 优先）
  · AMD GPU（ROCm：torch 视其为 cuda/HIP；Linux 官方支持，Windows 有限）
  · 华为昇腾 NPU（torch_npu，DiffSynth 官方 npu 设备抽象已核实）
  · Intel Arc（torch.xpu / IPEX，实验性）
  · DirectML（Windows AMD 兜底，对 H3 管线为实验性）
  · 纯 CPU（不推荐跑 H3）

检测依据均为真实接口，未验证的组合会在 notes 中如实标注。
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────
# 后端常量
# ─────────────────────────────────────────────────────────────
BACKEND_NVIDIA = "cuda_nvidia"      # NVIDIA CUDA（完整支持，官方验证）
BACKEND_ROCM = "cuda_rocm"          # AMD ROCm（torch 视角仍是 cuda）
BACKEND_NPU = "npu_ascend"          # 华为昇腾（torch_npu）
BACKEND_XPU = "xpu_intel"           # Intel Arc（IPEX，实验性）
BACKEND_DIRECTML = "directml"       # Windows DirectML（实验性）
BACKEND_CPU = "cpu"                 # 纯 CPU（不推荐）

BACKEND_LABELS = {
    BACKEND_NVIDIA: "NVIDIA CUDA",
    BACKEND_ROCM: "AMD ROCm",
    BACKEND_NPU: "华为昇腾 NPU",
    BACKEND_XPU: "Intel Arc (XPU)",
    BACKEND_DIRECTML: "DirectML",
    BACKEND_CPU: "CPU",
}

# 各后端对 H3 内置推理的支持状态（核实于 2026-08-07）
BACKEND_SUPPORT = {
    BACKEND_NVIDIA: "完整支持",
    BACKEND_ROCM: "支持（Linux ROCm 官方路线；Windows 下 AMD 卡 ROCm 覆盖有限）",
    BACKEND_NPU: "支持（DiffSynth 官方 NPU 设备抽象；需安装 torch-npu；昇腾已完成 H3 Day-0 适配）",
    BACKEND_XPU: "实验性（未在 H3 管线验证）",
    BACKEND_DIRECTML: "实验性（未在 H3 管线验证，建议改用 ROCm/ComfyUI）",
    BACKEND_CPU: "不推荐（速度极慢，仅能验证流程）",
}


@dataclass
class HardwareReport:
    backend: str = BACKEND_CPU
    backend_label: str = "CPU"
    support: str = BACKEND_SUPPORT[BACKEND_CPU]
    gpu_name: str = "未检测到加速设备"
    gpu_count: int = 0
    vram_total_gb: float = 0.0
    vram_free_gb: float = 0.0
    driver: str = ""
    cuda_ok: bool = False            # 泛指"torch 可见的加速设备可用"
    ram_total_gb: float = 0.0
    ram_free_gb: float = 0.0
    disk_free_gb: float = 0.0
    policy: str = "unsupported"      # max / high / balanced / low / ultra / unsupported
    policy_label: str = ""
    notes: list = field(default_factory=list)
    torch_device: str = "cpu"        # 传给 pipeline 的 device 字符串


# ─────────────────────────────────────────────────────────────
# 基础工具
# ─────────────────────────────────────────────────────────────
def _run(cmd, timeout=10):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, p.stdout.strip()
    except Exception:
        return False, ""


def _nvidia_smi_query(query: str) -> Optional[str]:
    exe = "nvidia-smi"
    if sys.platform.startswith("win"):
        cand = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                            "System32", "nvidia-smi.exe")
        if os.path.exists(cand):
            exe = cand
    ok, out = _run([exe, "--query-gpu=" + query, "--format=csv,noheader,nounits"])
    return out if ok else None


def detect_memory() -> dict:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {"total_gb": vm.total / (1024 ** 3), "free_gb": vm.available / (1024 ** 3)}
    except ImportError:
        if sys.platform.startswith("win"):
            try:
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
                return {
                    "total_gb": stat.ullTotalPhys / (1024 ** 3),
                    "free_gb": stat.ullAvailPhys / (1024 ** 3),
                }
            except Exception:
                pass
        return {"total_gb": 0.0, "free_gb": 0.0}


# ─────────────────────────────────────────────────────────────
# 后端探测（按优先级：NVIDIA → ROCm → 昇腾 → Intel → DirectML → CPU）
# ─────────────────────────────────────────────────────────────
def _probe_nvidia(rep: HardwareReport) -> bool:
    out = _nvidia_smi_query("name,memory.total,memory.free,driver_version")
    if not out:
        return False
    lines = [l for l in out.splitlines() if l.strip()]
    if not lines:
        return False
    parts = [p.strip() for p in lines[0].split(",")]
    rep.backend = BACKEND_NVIDIA
    rep.gpu_name = parts[0] if parts else "NVIDIA GPU"
    try:
        rep.vram_total_gb = float(parts[1]) / 1024.0
        rep.vram_free_gb = float(parts[2]) / 1024.0
    except (ValueError, IndexError):
        pass
    if len(parts) >= 4:
        rep.driver = parts[3]
    rep.gpu_count = len(lines)
    rep.cuda_ok = True
    rep.torch_device = "cuda"
    return True


def _probe_torch_backends(rep: HardwareReport) -> bool:
    """需要导入 torch 的探测（ROCm/昇腾/Intel/DirectML）。"""
    try:
        import torch
    except ImportError:
        return False

    # 1) torch.cuda 可用：NVIDIA 或 AMD ROCm（HIP）
    try:
        if torch.cuda.is_available():
            i = torch.cuda.current_device()
            prop = torch.cuda.get_device_properties(i)
            free, total = torch.cuda.mem_get_info(i)
            is_rocm = bool(getattr(torch.version, "hip", None))
            rep.backend = BACKEND_ROCM if is_rocm else BACKEND_NVIDIA
            rep.gpu_name = prop.name
            rep.gpu_count = torch.cuda.device_count()
            rep.vram_total_gb = total / (1024 ** 3)
            rep.vram_free_gb = free / (1024 ** 3)
            rep.cuda_ok = True
            rep.torch_device = "cuda"
            return True
    except Exception:
        pass

    # 2) 华为昇腾 NPU（torch_npu）
    try:
        if importlib.util.find_spec("torch_npu") is not None and torch.npu.is_available():
            rep.backend = BACKEND_NPU
            rep.cuda_ok = True
            rep.torch_device = "npu"
            try:
                prop = torch.npu.get_device_properties(0)
                rep.gpu_name = getattr(prop, "name", "Ascend NPU")
                rep.vram_total_gb = getattr(prop, "total_memory", 0) / (1024 ** 3)
            except Exception:
                rep.gpu_name = "Ascend NPU"
            # npu-smi 补充信息（如有）
            ok, out = _run(["npu-smi", "info", "-l"], timeout=10)
            if ok and out:
                for line in out.splitlines():
                    if "910" in line or "310" in line:
                        rep.gpu_name = line.strip()[:60]
                        break
            rep.gpu_count = max(1, torch.npu.device_count())
            return True
    except Exception:
        pass

    # 3) Intel Arc（IPEX / torch.xpu）
    try:
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            rep.backend = BACKEND_XPU
            rep.cuda_ok = True
            rep.torch_device = "xpu"
            try:
                prop = torch.xpu.get_device_properties(0)
                rep.gpu_name = getattr(prop, "name", "Intel Arc GPU")
            except Exception:
                rep.gpu_name = "Intel Arc GPU"
            rep.gpu_count = torch.xpu.device_count()
            return True
    except Exception:
        pass

    # 4) DirectML（Windows AMD 兜底，实验性）
    try:
        import torch_directml  # noqa: F401
        if torch_directml.is_available():
            rep.backend = BACKEND_DIRECTML
            rep.cuda_ok = True
            rep.torch_device = torch_directml.device_name(0)
            rep.gpu_name = f"DirectML: {torch_directml.device_name(0)}"
            return True
    except Exception:
        pass

    return False


# ─────────────────────────────────────────────────────────────
# 策略分档（按后端 + 显存）
# ─────────────────────────────────────────────────────────────
POLICY_LABELS = {
    "max": "旗舰档：可运行 BF16 原版 / NF4 全速",
    "high": "高性能档：NF4 全速（CPU 卸载）",
    "balanced": "均衡档：NF4 标准（自动显存管理）",
    "low": "低显存档：磁盘流式加载（建议 SSD）",
    "ultra": "极限档：磁盘直载（速度较慢）",
    "unsupported": "未检测到可用加速设备",
}


def choose_policy(backend: str, vram_total_gb: float, vram_free_gb: float) -> str:
    v = vram_total_gb if vram_total_gb > 0 else vram_free_gb
    if backend == BACKEND_CPU:
        return "unsupported"
    if v >= 48:
        return "max"
    if v >= 24:
        return "high"
    if v >= 12:
        return "balanced"
    if v >= 7.5:
        return "low"
    if v >= 1:
        return "ultra"
    # 显存未知但设备可用（如部分 NPU）→ 均衡档兜底
    return "balanced"


def probe_all(path_for_disk: str = None) -> HardwareReport:
    rep = HardwareReport()

    # 优先 nvidia-smi（快且不依赖 torch）
    if not _probe_nvidia(rep):
        _probe_torch_backends(rep)

    rep.backend_label = BACKEND_LABELS.get(rep.backend, rep.backend)
    rep.support = BACKEND_SUPPORT.get(rep.backend, "")

    mem = detect_memory()
    rep.ram_total_gb = round(mem["total_gb"], 1)
    rep.ram_free_gb = round(mem["free_gb"], 1)

    try:
        p = path_for_disk or str(Path.home())
        du = shutil.disk_usage(p)
        rep.disk_free_gb = round(du.free / (1024 ** 3), 1)
    except Exception:
        pass

    rep.vram_total_gb = round(rep.vram_total_gb, 1)
    rep.vram_free_gb = round(rep.vram_free_gb, 1)
    rep.policy = choose_policy(rep.backend, rep.vram_total_gb, rep.vram_free_gb)
    rep.policy_label = POLICY_LABELS.get(rep.policy, "")

    # ── 提示与建议（如实标注验证状态）──
    if rep.backend == BACKEND_CPU:
        rep.notes.append("未检测到 NVIDIA/AMD/昇腾/Intel 加速设备。H3 本地推理需要加速硬件。")
    elif rep.backend == BACKEND_ROCM:
        rep.notes.append("AMD ROCm 环境：Linux 下为官方支持路线；NF4 量化依赖 bitsandbytes 的 ROCm 支持。")
        if sys.platform.startswith("win"):
            rep.notes.append("Windows 下 AMD 卡的 ROCm 覆盖范围有限，如遇算子报错可考虑 Linux 双系统或 ComfyUI 方案。")
    elif rep.backend == BACKEND_NPU:
        rep.notes.append("华为昇腾 NPU：DiffSynth 官方支持；华为已完成 MiniMax H3 的 Day-0 适配。")
    elif rep.backend == BACKEND_XPU:
        rep.notes.append("Intel Arc：H3 管线未经验证，属实验性支持，遇到问题建议改用 NVIDIA/昇腾或 ComfyUI。")
    elif rep.backend == BACKEND_DIRECTML:
        rep.notes.append("DirectML 为兜底路线，H3 管线未经验证；AMD 用户建议优先 ROCm（Linux）或 ComfyUI。")

    if rep.ram_total_gb and rep.ram_total_gb < 16 and rep.backend != BACKEND_CPU:
        rep.notes.append(f"系统内存 {rep.ram_total_gb}GB 偏低，建议 ≥16GB（NF4 流畅体验建议 32GB）。")
    if rep.policy in ("low", "ultra") and rep.disk_free_gb and rep.disk_free_gb < 100:
        rep.notes.append("低显存模式依赖硬盘流式加载，请确保模型目录所在磁盘剩余 ≥100GB 且为 SSD。")
    return rep


# ─────────────────────────────────────────────────────────────
# 显存管理配置（DiffSynth ModelConfig 参数，设备名参数化）
# ─────────────────────────────────────────────────────────────
def vram_config_for(policy: str, accel_device: str = "cuda"):
    """
    返回 (vram_config_dict, vram_limit_mode)
    vram_limit_mode: "auto" → 运行期取空闲显存-2GB；"zero" → 0（极限磁盘直载）
    accel_device: "cuda"（NVIDIA/ROCm）/ "npu"（昇腾）等
    """
    bf16 = "bfloat16"
    if policy in ("max", "high", "balanced"):
        cfg = {
            "offload_dtype": bf16, "offload_device": "cpu",
            "onload_dtype": bf16, "onload_device": "cpu",
            "preparing_dtype": bf16, "preparing_device": accel_device,
            "computation_dtype": bf16, "computation_device": accel_device,
        }
        return cfg, "auto"
    if policy == "low":
        cfg = {
            "offload_dtype": "disk", "offload_device": "disk",
            "onload_dtype": bf16, "onload_device": "cpu",
            "preparing_dtype": bf16, "preparing_device": accel_device,
            "computation_dtype": bf16, "computation_device": accel_device,
        }
        return cfg, "auto"
    # ultra：官方 8GB 内存极限配置
    cfg = {
        "offload_dtype": "disk", "offload_device": "disk",
        "onload_dtype": "disk", "onload_device": "disk",
        "preparing_dtype": "disk", "preparing_device": "disk",
        "computation_dtype": bf16, "computation_device": accel_device,
    }
    return cfg, "zero"
