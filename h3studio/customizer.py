# -*- coding: utf-8 -*-
"""
customizer.py — DIY 自定义打包校验引擎
========================================
用户自选组件拼包时的兼容性守门员。规则全部来自已核实的模型/引擎事实：

硬性规则（违反即拒绝下载）：
  R1 引擎与组件匹配：DiffSynth 只能用它认可的量化文件（NF4 套件 / 官方 BF16 分片），
     ComfyUI 用 Comfy-Org / GGUF / NVFP4 等重打包文件；跨引擎混装一律拒绝。
  R2 量化族成套：主模型、文本编码器、VAE 必须同一量化族
     （NF4 全家桶 / 官方 BF16 全家桶），混搭组合未经验证，直接拒绝。
  R3 分区匹配：FL2VA 主模型必须配 FL2VA Processor，Ref2VA 同理。
  R4 显存可行性：估算最低显存超过用户实际显存过多 → 拒绝（防卡死/OOM）。
  R5 磁盘可行性：包体超过模型目录剩余空间 → 拒绝。

警告（允许下载但提示风险）：
  W1 显存紧张（略低于最低要求）。
  W2 内存 <16GB（卸载模式体验差）。
  W3 GGUF 组件需 ComfyUI-GGUF 插件。
"""

import time

from .facts import DIY_COMPONENTS


CATEGORIES = ["dit", "text_encoder", "video_vae", "audio_vae", "processor", "lora"]

CATEGORY_LABELS = {
    "dit": "主模型（DiT）",
    "text_encoder": "文本编码器",
    "video_vae": "视频 VAE",
    "audio_vae": "音频 VAE",
    "processor": "Processor（内置引擎必需）",
    "lora": "LoRA（可选）",
}

# 量化族成套要求：主模型 quant → 允许的文本编码器 quant / VAE quant
QUANT_FAMILIES = {
    # DiffSynth NF4 全家桶（官方量化，一一对应）
    "nf4": {"text_encoder": {"nf4"}, "video_vae": {"nf4"}, "audio_vae": {"nf4"}},
    # DiffSynth 官方 BF16 全家桶
    "bf16_official": {"text_encoder": {"bf16_official"},
                      "video_vae": {"bf16_official"}, "audio_vae": {"bf16_official"}},
}


def find_component(category: str, comp_id: str):
    for c in DIY_COMPONENTS.get(category, []):
        if c["id"] == comp_id:
            return c
    return None


def validate_pack(engine: str, selections: dict, hw=None, disk_free_gb: float = None):
    """
    engine: "diffsynth" | "comfyui"
    selections: {category: component_id or ""}
    返回 (errors, warnings)：errors 非空则禁止下载。
    """
    errors, warnings = [], []
    sel = {k: find_component(k, v) for k, v in selections.items() if v}

    dit = sel.get("dit")
    te = sel.get("text_encoder")
    vvae = sel.get("video_vae")
    avvae = sel.get("audio_vae")
    proc = sel.get("processor")

    # ── 基本完整性 ──
    if dit is None:
        errors.append("必须选择主模型（DiT）")
    if te is None:
        errors.append("必须选择文本编码器")
    if vvae is None:
        errors.append("必须选择视频 VAE")
    if avvae is None:
        errors.append("必须选择音频 VAE")
    if engine == "diffsynth" and proc is None:
        errors.append("内置引擎必须选择 Processor（分词/预处理配置）")

    # ── R1 引擎匹配 ──
    for cat, comp in sel.items():
        if comp is None:
            continue
        if comp["engine"] != engine:
            errors.append(
                f"「{comp['name']}」属于 {'内置引擎' if comp['engine'] == 'diffsynth' else 'ComfyUI'} "
                f"组件，与当前选择的引擎不兼容（R1）")

    if dit is not None and te is not None and vvae is not None:
        # ── R2 量化族成套 ──
        if engine == "diffsynth":
            family = QUANT_FAMILIES.get(dit["quant"])
            if family is None:
                errors.append(f"主模型量化「{dit['quant']}」不被内置引擎支持（R1）")
            else:
                if te["quant"] not in family["text_encoder"]:
                    errors.append("主模型与文本编码器量化格式不成套：内置引擎要求 NF4 全家桶或官方 BF16 全家桶（R2）")
                if vvae["quant"] not in family["video_vae"]:
                    errors.append("主模型与视频 VAE 量化格式不成套（R2）")
                if avvae is not None and avvae["quant"] not in family["audio_vae"]:
                    errors.append("主模型与音频 VAE 量化格式不成套（R2）")
        else:
            # ComfyUI：GGUF 主模型必须配 GGUF 文本编码器
            if dit["quant"] == "gguf" and te["quant"] != "gguf":
                errors.append("GGUF 主模型必须搭配 GGUF 文本编码器（ComfyUI-GGUF 插件要求）")
            if dit["quant"] in ("int8", "fp8", "bf16") and te["quant"] == "gguf":
                errors.append("非 GGUF 主模型不能搭配 GGUF 文本编码器")
            if dit["quant"] == "gguf":
                warnings.append("GGUF 组件需要在 ComfyUI 中安装 ComfyUI-GGUF 插件（W3）")

        # ── R3 分区匹配 ──
        if proc is not None and dit.get("partition") and proc.get("partition"):
            if proc["partition"] != dit["partition"]:
                errors.append(
                    f"主模型是 {dit['partition']} 分区，Processor 却是 {proc['partition']} 分区，"
                    "必须一致（R3）")

    # ── 硬件体检 ──
    if hw is not None:
        vram = getattr(hw, "vram_total_gb", 0)
        ram = getattr(hw, "ram_total_gb", 0)
        if dit is not None and dit.get("min_vram_gb") and vram > 0:
            need = dit["min_vram_gb"]
            if vram < need * 0.75:
                errors.append(
                    f"显存不足：该主模型最低需要 {need}GB 显存，你的设备约 {vram}GB。"
                    "强行运行会 OOM/卡死，请换更低量化版本（R4）")
            elif vram < need:
                warnings.append(
                    f"显存紧张：建议 {need}GB，实际 {vram}GB，可能需要磁盘卸载模式，速度会慢（W1）")
        if 0 < ram < 16:
            warnings.append(f"系统内存 {ram}GB 偏低，卸载模式体验会打折扣，建议 ≥16GB（W2）")

    # ── R5 磁盘体检（独立于硬件报告，任何时候都生效）──
    if disk_free_gb is not None:
        total = sum(c["size_gb"] for c in sel.values() if c)
        if total > disk_free_gb:
            errors.append(
                f"磁盘空间不足：本包约 {total:.1f}GB，模型目录剩余 {disk_free_gb:.1f}GB（R5）")

    return errors, warnings


def pack_total_size(selections: dict) -> float:
    total = 0.0
    for cat, cid in selections.items():
        c = find_component(cat, cid)
        if c:
            total += c["size_gb"]
    return round(total, 2)


def build_custom_bundle(engine: str, selections: dict, name: str = "") -> dict:
    """把合法的 DIY 选择构建成可下载的 bundle 字典（与 facts.BUNDLES 同构）。"""
    ts = time.strftime("%m%d_%H%M%S")
    bundle_id = f"custom_{engine}_{ts}"
    files = []
    for cat in CATEGORIES:
        c = find_component(cat, selections.get(cat, ""))
        if c is None:
            continue
        entry = {
            "repo": c["repo"], "path": c["path"], "size_gb": c["size_gb"],
            "dest": f"{cat}/{c['path'].split('/')[-1] if not c.get('is_dir') else ''}".rstrip("/"),
        }
        if c.get("is_dir"):
            entry["is_dir"] = True
            entry["dest"] = f"{cat}/"
        if c.get("repo_hf"):
            entry["repo_hf"] = c["repo_hf"]
        files.append(entry)

    return {
        "id": bundle_id,
        "name": (name.strip() or f"自定义包（{engine}）{time.strftime('%m-%d %H:%M')}"),
        "series": "DIY 自定义包",
        "engine": "builtin" if engine == "diffsynth" else "comfyui",
        "partition": next((find_component("dit", selections.get("dit", "")).get("partition", "")
                           for _ in [0]), ""),
        "precision": "自定义组合",
        "size_gb": pack_total_size(selections),
        "min_vram_gb": (find_component("dit", selections.get("dit", "")) or {}).get("min_vram_gb", 0),
        "min_ram_gb": 16,
        "recommended": False,
        "desc": "用户 DIY 组件包（已通过兼容性校验）",
        "files": files,
        # 留空 → 下载器按每个文件自带的 repo / repo_hf 选择仓库
        "source_repos": {},
    }
