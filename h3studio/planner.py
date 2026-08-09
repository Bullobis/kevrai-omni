# -*- coding: utf-8 -*-
"""
planner.py — 最优方案规划器
============================
输入硬件检测报告，输出"速度 × 质量 × 成本"最优的本地部署方案：
推荐模型版本、分辨率、步数、卸载策略、加速 LoRA 建议、预期速度参考。

速度参考数据来源（全部为已核实的公开实测/官方信息，展示时须标注）：
- RTX 5090 32G：480P/5s ≈ 80 秒；720P/10s ≈ 9 分钟（B站 梨花 实测，2026-08-03）
- RTX 5070：480P/5s ≈ 3 分钟+（B站 阿ban 实测，2026-08-03）
- RTX 4080S 16G：768P 5~7s 稳定（今日头条实测，2026-08-06）
- RTX 4090 24G + 64G 内存：15s 视频 14~22 分钟（今日头条实测，2026-08-06）
- RTX 3060 12G + 32G：480P/5s ≈ 9 分钟（B站 ComfyUI 团队实测，2026-08-03）
- 官方 API 定价对照：2K 分辨率 0.8 元/秒（MiniMax 官方发布页，2026-07-31）
"""

from dataclasses import dataclass, field

from . import hardware as hw


@dataclass
class OptimalPlan:
    ok: bool = False
    # 推荐模型
    bundle_id: str = ""
    bundle_name: str = ""
    reason: str = ""
    # 生成参数建议
    resolution: str = "768p"
    steps: int = 50
    offload_mode: str = "auto"
    vram_budget_gb: float = -1
    suggest_turbo_lora: bool = False
    # 展示信息
    speed_ref: str = ""          # 预期速度参考（标注来源）
    quality_note: str = ""
    cost_note: str = ""
    warnings: list = field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# 已实测显卡速度参照表（仅收录有公开实测数据的型号）
# ─────────────────────────────────────────────────────────────
BENCHMARKED_CARDS = [
    ("5090", "480P/5s ≈ 80 秒；720P/10s ≈ 9 分钟", "B站实测 2026-08"),
    ("5080", "接近 5090 档，480P/5s 约 2 分钟级", "按算力档位推断"),
    ("5070", "480P/5s ≈ 3 分钟+", "B站实测 2026-08"),
    ("4090", "480P 抽卡很快；15s 长视频约 14~22 分钟", "社区实测 2026-08"),
    ("4080", "768P 5~7s 稳定", "社区实测 2026-08"),
    ("4070", "480P/5s 约 3~5 分钟", "按显存/算力档位推断"),
    ("4060", "480P/5s 约 5~8 分钟（16G 版更从容）", "按显存/算力档位推断"),
    ("3090", "接近 4080 档", "按算力档位推断"),
    ("3060", "480P/5s ≈ 9 分钟", "B站 ComfyUI 团队实测 2026-08"),
]


def _speed_ref_for(gpu_name: str) -> str:
    g = (gpu_name or "").lower()
    for key, desc, src in BENCHMARKED_CARDS:
        if key in g:
            return f"{desc}（{src}）"
    return "该型号暂无公开实测，速度以首次生成为准（建议先跑 480P 试探）"


def make_plan(rep: "hw.HardwareReport") -> OptimalPlan:
    """按硬件报告生成最优方案。"""
    plan = OptimalPlan()

    # 成本说明对所有后端通用（本地部署核心卖点）
    plan.cost_note = ("本地生成 0 元/条（仅电费）。对照：MiniMax 官方 API 2K 定价 0.8 元/秒"
                      "（官方发布页 2026-07-31），一条 10 秒视频约 8 元——本地部署量大从优。")

    if rep is None or rep.policy == "unsupported":
        plan.warnings.append("未检测到加速硬件，无法给出本地推理方案。")
        return plan

    plan.ok = True
    vram = rep.vram_total_gb
    backend = rep.backend

    # ── 按后端分支 ──
    if backend == hw.BACKEND_NPU:
        plan.bundle_id = "nf4_full"
        plan.bundle_name = "NF4 量化版 · 双分区（FL2VA + Ref2VA）"
        plan.reason = "昇腾 NPU 为 DiffSynth 官方支持后端（Day-0 适配），NF4 量化显存友好，双分区解锁全部生成模式。"
        plan.resolution = "768p"
        plan.quality_note = "NF4 为官方量化，画质损失小；768p 为开源版最高短边。"
        plan.speed_ref = "昇腾平台暂无公开的 H3 消费级实测数据，请以首次生成为准。"
        return plan

    if backend in (hw.BACKEND_XPU, hw.BACKEND_DIRECTML):
        plan.bundle_id = "comfy_pruned_int8_fl2va"
        plan.bundle_name = "ComfyUI · FL2VA Pruned INT8 套件"
        plan.reason = ("当前后端对内置引擎为实验性支持。更稳妥的路线是用 ComfyUI 运行 H3"
                       "（ComfyUI 对多硬件兼容最好），本软件下载套件后按 README 指引接入 ComfyUI。")
        plan.resolution = "480p"
        plan.quality_note = "INT8 剪枝版画质良好；480P 起步，稳定后再提分辨率。"
        plan.speed_ref = _speed_ref_for(rep.gpu_name)
        plan.warnings.append(f"{hw.BACKEND_LABELS.get(backend, backend)} 路线未在 H3 内置管线验证，属实验性。")
        return plan

    # ── NVIDIA CUDA / AMD ROCm：按显存档位选内置引擎方案 ──
    if vram >= 48:
        plan.bundle_id = "bf16_fl2va"
        plan.bundle_name = "官方原版 BF16 · FL2VA（可另下 Ref2VA）"
        plan.reason = "显存 ≥48GB：直接上全精度 BF16 原版，画质上限最高。"
        plan.resolution = "768p"
        plan.steps = 50
        plan.quality_note = "BF16 全精度 = 开源版权限内最高画质。"
    elif vram >= 16:
        plan.bundle_id = "nf4_full"
        plan.bundle_name = "NF4 量化版 · 双分区（FL2VA + Ref2VA）"
        plan.reason = "显存 16~48GB：NF4 双分区一步到位，全部生成模式可用，速度画质均衡。"
        plan.resolution = "768p"
        plan.quality_note = "NF4 官方量化，画质损失小；768p 为开源版最高短边。"
    elif vram >= 8:
        plan.bundle_id = "nf4_fl2va"
        plan.bundle_name = "NF4 量化版 · FL2VA"
        plan.reason = "显存 8~16GB：NF4 FL2VA 最省显存，文生视频/首尾帧可用；想要全模态参考后续可补下 Ref2VA。"
        plan.resolution = "480p"
        plan.steps = 50
        plan.suggest_turbo_lora = True
        plan.quality_note = "建议 480P 预览档抽卡，满意后再出 768P（设置里可切换）。"
    else:
        plan.bundle_id = "gguf_fl2va_q4km"
        plan.bundle_name = "社区 GGUF · FL2VA Q4_K_M（ComfyUI-GGUF 用）"
        plan.reason = "显存 <8GB：内置引擎会非常吃力，推荐走 ComfyUI + GGUF 量化（12GB 显存档也可用）。"
        plan.resolution = "480p"
        plan.warnings.append("低显存设备建议搭配 32GB 以上内存与 NVMe SSD。")

    plan.speed_ref = _speed_ref_for(rep.gpu_name)
    if plan.suggest_turbo_lora and not plan.speed_ref.startswith("该型号"):
        plan.speed_ref += "；叠加 InstantX Turbo 4 步 LoRA 可再提速（适合快速抽卡）。"
    return plan


# ─────────────────────────────────────────────────────────────
# 生成页三档预设（与规划器联动）
# ─────────────────────────────────────────────────────────────
PRESETS = {
    "speed": {
        "label": "⚡ 速度优先",
        "resolution": "480p",
        "steps": 4,            # 配合 Turbo LoRA
        "need_turbo": True,
        "tip": "需先在「嵌入模型」启用 InstantX Turbo LoRA（市场可下，851MB）。4 步快速抽卡，构图满意再切均衡档。",
    },
    "balanced": {
        "label": "⚖ 均衡推荐",
        "resolution": "480p",
        "steps": 50,
        "need_turbo": False,
        "tip": "480P + 50 步：出片快、适合试提示词。满意后切质量档出正式片。",
    },
    "quality": {
        "label": "✨ 质量优先",
        "resolution": "768p",
        "steps": 50,
        "need_turbo": False,
        "tip": "768P 标准档（开源版最高短边）+ 50 步，正式出片用。",
    },
}
