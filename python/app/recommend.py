"""Model recommender — match catalog models against local hardware.

Rating logic (per model, using its `hardware` field when present):
    * perfect — 推荐显存/内存都充足（含 20% 余量），磁盘够
    * good    — 介于 min 与推荐之间（量化/换挡可跑）
    * tight   — 勉强可行（深度量化 / 重度 offload，不推荐日常使用）
    * no      — 完全不可行

Ranking: fit 优先 → trending/新模型加权 → 体积适中加分（下载时间与带宽挂钩）。
"""
from __future__ import annotations

from typing import Any

_FIT_ORDER = {"perfect": 0, "good": 1, "tight": 2, "no": 3}


def _model_hw(m: dict[str, Any]) -> dict[str, Any]:
    hw = m.get("hardware") or {}
    return {
        "vram": float(hw.get("vram_gb") or 0),
        "min_vram": float(hw.get("min_vram_gb") or 0),
        "ram": float(hw.get("ram_gb") or 0),
        "disk": float(hw.get("disk_gb") or m.get("size_gb") or 0),
        "notes": hw.get("notes") or "",
    }


def _effective_vram(hw_sys: dict[str, Any]) -> float:
    """可当作显存使用的容量：独显显存 + Apple 统一内存特殊处理。"""
    if hw_sys.get("gpu_vendor") == "apple":
        # Apple Silicon 统一内存：GPU 可用约 70% 物理内存
        return float(hw_sys.get("ram_total_gb") or 0) * 0.7
    vram = float(hw_sys.get("gpu_best_vram_gb") or 0)
    if vram <= 0:
        return 0.0
    return vram


def rate_model(m: dict[str, Any], hw_sys: dict[str, Any]) -> dict[str, Any]:
    """Return {fit, reasons[]} for one model against the hardware snapshot."""
    mhw = _model_hw(m)
    vram = _effective_vram(hw_sys)
    ram = float(hw_sys.get("ram_total_gb") or 0)
    disk_free = float((hw_sys.get("disk") or {}).get("free_gb") or 0)

    need_vram = mhw["vram"]
    min_vram = mhw["min_vram"] or mhw["vram"] * 0.6
    need_ram = mhw["ram"] or mhw["disk"] * 1.2
    need_disk = mhw["disk"] or float(m.get("size_gb") or 0)

    reasons: list[str] = []

    # --- GPU/显存 ---
    if vram >= need_vram > 0:
        reasons.append(f"显存 {vram:g}GB ≥ 推荐 {need_vram:g}GB")
        vfit = "perfect"
    elif vram >= min_vram > 0:
        reasons.append(f"显存 {vram:g}GB 介于最低 {min_vram:g}~推荐 {need_vram:g}GB（量化可跑）")
        vfit = "good"
    elif need_ram > 0 and ram >= need_ram * 1.1:
        # 显存不够/无独显，但内存充足 → CPU 推理可行（GGUF/MNN 的核心场景）
        if ram >= need_ram * 1.5:
            reasons.append(f"内存 {ram:g}GB 充足（≥{need_ram:g}GB 推荐），可走 CPU 推理")
            vfit = "good"
        else:
            reasons.append(f"内存 {ram:g}GB 达标（{need_ram:g}GB 推荐），CPU 推理可用但余量不大")
            vfit = "tight"
    elif need_vram <= 0:
        vfit = "good"
    else:
        reasons.append(f"显存与内存均不足（需 ≥{min_vram:g}GB 显存或 ≥{need_ram:g}GB 内存）")
        vfit = "no"

    # --- 内存（纯 CPU 场景兜底）---
    if vfit != "no" and need_ram > 0 and ram < need_ram * 0.6:
        vfit = "tight" if vfit == "perfect" else vfit
        reasons.append(f"内存 {ram:g}GB 低于推荐 {need_ram:g}GB")

    # --- 磁盘 ---
    disk_note = ""
    if need_disk > 0:
        if disk_free >= need_disk * 1.2:
            disk_note = f"磁盘剩余 {disk_free:g}GB 充足"
        elif disk_free >= need_disk:
            disk_note = f"磁盘剩余 {disk_free:g}GB 刚好够（建议预留更多空间）"
            if vfit == "perfect":
                vfit = "good"
        else:
            vfit = "no"
            disk_note = f"磁盘剩余 {disk_free:g}GB < 需 {need_disk:g}GB"

    fit = vfit
    # CPU-only 机器跑大 LLM：至少 tight 起步
    if not hw_sys.get("has_discrete_gpu") and m.get("category") == "llm":
        if fit == "perfect" and min_vram > 16:
            fit = "good"
            reasons.append("无独显：大模型将走 CPU 推理，速度受限")

    return {
        "fit": fit,
        "reasons": reasons,
        "disk_note": disk_note,
        "need": {"vram_gb": need_vram, "ram_gb": need_ram, "disk_gb": need_disk},
    }


def recommend(
    models: list[dict[str, Any]],
    hw_sys: dict[str, Any],
    limit: int = 12,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Ranked recommendation list. Excludes `pending` models (未开源)."""
    scored: list[tuple[int, float, dict[str, Any], dict[str, Any]]] = []
    bw = float(hw_sys.get("bandwidth_mbps") or 0)

    for m in models:
        if category and m.get("category") != category:
            continue
        if m.get("category") == "pending":
            continue
        if not (m.get("engine") or []):
            continue  # 未指派引擎的模型不可执行

        r = rate_model(m, hw_sys)
        if r["fit"] == "no":
            continue

        # 排序分：fit 为主，新模型/trending 加权，体积适中加分
        score = 100 - _FIT_ORDER[r["fit"]] * 25
        if m.get("trending"):
            score += 10
        # 类别偏好：LLM 是主场景，视频次之
        if m.get("category") == "llm":
            score += 15
        elif m.get("category") == "video":
            score += 8
        size = float(m.get("size_gb") or 0)
        # 带宽慢 → 小模型加分（下载可行性）
        if bw and bw < 50 and size <= 20:
            score += 8
        elif bw and bw < 50 and size > 200:
            score -= 10
        # 中等体积（5-25GB）普遍最实用
        if 5 <= size <= 25:
            score += 6
        # 多引擎支持（可选 llama.cpp 或 MNN）加分
        if len(m.get("engine") or []) > 1:
            score += 4

        scored.append((score, -size, m, r))

    scored.sort(key=lambda t: (-t[0], t[1]))
    out = []
    for score, _neg, m, r in scored[:max(1, limit)]:
        d = dict(m)
        d["recommendation"] = {
            "score": score,
            "fit": r["fit"],
            "reasons": r["reasons"],
            "disk_note": r["disk_note"],
            "need": r["need"],
        }
        out.append(d)
    return out
