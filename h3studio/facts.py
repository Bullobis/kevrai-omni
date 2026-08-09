# -*- coding: utf-8 -*-
"""
facts.py — MiniMax H3 事实库（全部条目 2026-08-07 联网核实）
================================================================
原则：本文件里每一个数字、文件名、仓库路径都经过真实 API / 页面核验。
不确定的数据一律不写入；需要估算的地方显式标注 estimated=True。

核实来源摘要（详见 docs/调研报告-MiniMax-H3与引擎选型.md）：
- 官方博客      https://www.minimaxi.com/blog/minimax-h3           (2026-07-31 发布)
- 开源公告      百度百科 / 上海证券报 / 智东西                       (2026-08-03 开源)
- HF 官方仓库   huggingface.co/MiniMaxAI/MiniMax-H3                 (tree API 核实)
- 魔搭官方仓库  modelscope.cn/models/MiniMax/MiniMax-H3             (files API + resolve URL 实测 200)
- DiffSynth     github.com/modelscope/DiffSynth-Studio              (官方文档+示例代码+源码)
- NF4 量化仓库  DiffSynth-Studio/MiniMax-H3-NF4                     (files API 核尺寸)
- Comfy-Org     Comfy-Org/MiniMax-H3                                (tree API 核尺寸, HF+魔搭双镜像)
- GGUF 社区     Abiray/MiniMax-H3-GGUF                              (tree API 核尺寸)
- Turbo LoRA    InstantX/MiniMax-H3-Turbo-Lora-Diffusers            (tree API 核尺寸)
"""

VERIFIED_AT = "2026-08-07（2026-08-09 复核官方仓库新增资源）"

# ─────────────────────────────────────────────────────────────
# 1. 模型基本规格（官方发布页 + 多家媒体交叉核实）
# ─────────────────────────────────────────────────────────────
MODEL_INFO = {
    "name": "MiniMax H3",
    "developer": "MiniMax（稀宇科技）",
    "release_date": "2026-07-31",        # 正式发布
    "open_source_date": "2026-08-03",    # 权重开源
    "open_scope": "H3-Base（FL2VA + Ref2VA 两个分区）",
    "not_open": "H3-Context-IR、H3-Regenerate-2K 仅官方 API 提供（因此开源版最高输出短边 768 像素，2K 需 API）",
    "params": "DiT 主干 BF16 约 66.3GB + Qwen3-VL 32B 文本编码器 BF16 约 66.7GB（单分区）",
    "license": {
        "name": "MiniMax H3 Community License Agreement",
        "url": "https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE",
        "regions_excluded": ["美国", "欧盟", "英国", "韩国"],
        "note": "协议明确排除上述地区的使用权限；商用前务必阅读协议全文。",
    },
    "official_repos": {
        "huggingface": "MiniMaxAI/MiniMax-H3",
        "modelscope": "MiniMax/MiniMax-H3",
    },
}

# ─────────────────────────────────────────────────────────────
# 2. 生成能力规格（DiffSynth 官方文档 + 官方发布页核实）
# ─────────────────────────────────────────────────────────────
GENERATION_SPECS = {
    "fps": 24,                        # 帧率固定 24
    "audio_sample_rate": 32000,       # 32kHz 立体声（原生双声道）
    "duration_min_s": 4,
    "duration_max_s": 15,
    "height_divisor": 32,             # 高/宽必须是 32 的倍数
    "width_divisor": 32,
    "frame_rule": "num_frames 对齐到 17n+5（向上取整）",
    "default_height": 768,
    "default_width": 1344,
    "default_steps": 50,
    "cfg_note": "H3 为 CFG 蒸馏模型，negative_prompt 默认不起作用（cfg_scale 默认 1.0）",
    "languages": "中、英、日、韩、法、德等 11 种语言稳定支持",
}

# 输入数量硬限制（官方 GitHub 仓库 2026-08-09 核实）：
# Ref2VA 最多 9 图 + 3 视频 + 3 音频，合计 ≤12 个文件；
# 视频/音频每段 2~15 秒且各自总时长 ≤15 秒；音频必须伴随图像或视频，不能单独作为唯一输入
UPLOAD_LIMITS = {
    "ref2va": {
        "image": 9, "video": 3, "audio": 3, "total": 12,
        "clip_min_s": 2, "clip_max_s": 15, "clips_total_max_s": 15,
        "audio_requires_visual": True,
    },
    "fl2va_keyframes": 2,   # FL2VA 首尾帧模式最多 2 张关键帧图
}

# 官方提示词指南与技能（2026-08-09 核实于官方仓库 MiniMax-AI/MiniMax-H3）
OFFICIAL_PROMPT_RESOURCES = {
    "guides": [
        {"file": "官方提示词指南-文生与首尾帧-en.md",
         "label": "📖 官方指南 · 文生/首尾帧 (T2VA/I2VA/FL2VA/L2VA)",
         "source": "MiniMaxAI/MiniMax-H3 docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md"},
        {"file": "官方提示词指南-全模态参考-en.md",
         "label": "📖 官方指南 · 全模态参考 (Ref2VA)",
         "source": "MiniMaxAI/MiniMax-H3 docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md"},
    ],
    "official_skills_note": ("官方仓库另附 9 个提示词技能（极简产品广告/3D动画短剧/纸艺定格/品牌宣传/"
                             "MV字幕/合作游戏开场/拼贴科普/手绘实拍等），本软件模板库已参考其方法论。"),
}

# 宽高比 → 各分辨率档位的 (宽, 高)，全部为 32 的倍数（核验通过）
# 768p = H3-Base 默认短边；480p = 快速预览档；640p = 中间档
ASPECT_PRESETS = {
    #  ratio:   { "480p": (w,h), "640p": (w,h), "768p": (w,h) }
    "16:9":  {"480p": (832, 480),  "640p": (1152, 640), "768p": (1344, 768)},
    "9:16":  {"480p": (480, 832),  "640p": (640, 1152), "768p": (768, 1344)},
    "1:1":   {"480p": (480, 480),  "640p": (640, 640),  "768p": (768, 768)},
    "4:3":   {"480p": (640, 480),  "640p": (864, 640),  "768p": (1024, 768)},
    "3:4":   {"480p": (480, 640),  "640p": (640, 864),  "768p": (768, 1024)},
    "21:9":  {"480p": (1120, 480), "640p": (1504, 640), "768p": (1792, 768)},
}

# 支持的参考素材格式（UI 拖拽过滤用）
ACCEPT_IMAGE = [".png", ".jpg", ".jpeg", ".webp", ".bmp"]
ACCEPT_VIDEO = [".mp4", ".mov", ".mkv", ".webm", ".gif", ".avi"]
ACCEPT_AUDIO = [".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"]

# ─────────────────────────────────────────────────────────────
# 3. 下载源（只保留已验证的源）
# ─────────────────────────────────────────────────────────────
# resolve URL 模板均已实测（HTTP 200 + Accept-Ranges）：
#   modelscope: https://modelscope.cn/models/{repo}/resolve/master/{path}
#   hf:         https://huggingface.co/{repo}/resolve/main/{path}
#   hf-mirror:  https://hf-mirror.com/{repo}/resolve/main/{path}
DOWNLOAD_SOURCES = [
    {
        "key": "modelscope",
        "name": "魔搭 ModelScope",
        "host": "modelscope.cn",
        "base_url": "https://modelscope.cn",
        "resolve_tpl": "https://modelscope.cn/models/{repo}/resolve/master/{path}",
        "branch": "master",
        "tag": "国内官方合作源",
        "note": "阿里魔搭社区，MiniMax 官方同步上架，国内访问稳定。",
    },
    {
        "key": "hf_mirror",
        "name": "HF-Mirror 镜像",
        "host": "hf-mirror.com",
        "base_url": "https://hf-mirror.com",
        "resolve_tpl": "https://hf-mirror.com/{repo}/resolve/main/{path}",
        "branch": "main",
        "tag": "国内公益镜像",
        "note": "huggingface.co 的全量镜像，国内下载速度通常最快。",
    },
    {
        "key": "hf",
        "name": "HuggingFace 原站",
        "host": "huggingface.co",
        "base_url": "https://huggingface.co",
        "resolve_tpl": "https://huggingface.co/{repo}/resolve/main/{path}",
        "branch": "main",
        "tag": "海外官方源",
        "note": "模型首发源，海外网络首选。",
    },
]

GITHUB_SOURCE_NOTE = (
    "经核实，MiniMax H3 官方权重与主流量化版均托管在 HuggingFace / 魔搭，"
    "GitHub 上没有官方权重仓库（仅代码仓库），因此 GitHub 不列入自动下载源。"
)

# 测速探针文件（真实存在的小文件，约 7MB，足以测出真实带宽）
PROBE_FILES = {
    "modelscope": {"repo": "MiniMax/MiniMax-H3", "path": "FL2VA/processor/tokenizer.json"},
    "hf":         {"repo": "MiniMaxAI/MiniMax-H3", "path": "FL2VA/processor/tokenizer.json"},
    "hf_mirror":  {"repo": "MiniMaxAI/MiniMax-H3", "path": "FL2VA/processor/tokenizer.json"},
}

# ─────────────────────────────────────────────────────────────
# 4. 模型市场清单（每个文件的大小都经 files/tree API 核实）
# ─────────────────────────────────────────────────────────────
# engine 字段：
#   builtin  → 本软件内置 DiffSynth-Studio 引擎可直接推理
#   comfyui  → 供 ComfyUI 使用（本软件下载并归类，但不内置推理）
#   lora     → LoRA / 微调权重，可在内置引擎中叠加

BUNDLES = [
    # ═══════ ① 内置引擎默认：DiffSynth NF4 量化版 ═══════
    {
        "id": "nf4_fl2va",
        "name": "NF4 量化版 · FL2VA（文生视频 / 首尾帧）",
        "series": "DiffSynth NF4 量化（官方量化）",
        "engine": "builtin",
        "partition": "FL2VA",
        "precision": "NF4 (4-bit)",
        "size_gb": 34.4,
        "min_vram_gb": 8,
        "min_ram_gb": 16,
        "recommended": True,
        "desc": "官方 DiffSynth-Studio 出品的 4-bit 量化版，配合自动显存管理，8GB 显存即可运行。消费级显卡首选。",
        "files": [
            {"repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "minimax-h3-fl2va-nf4.safetensors",     "size_gb": 17.16, "dest": "minimax-h3-fl2va-nf4.safetensors"},
            {"repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "minimax-h3-text-encoder-nf4.safetensors", "size_gb": 15.33, "dest": "minimax-h3-text-encoder-nf4.safetensors"},
            {"repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "video_vae_nf4.safetensors",              "size_gb": 1.61,  "dest": "video_vae_nf4.safetensors"},
            {"repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "audio_vae_nf4.safetensors",              "size_gb": 0.28,  "dest": "audio_vae_nf4.safetensors"},
            {"repo": "MiniMax/MiniMax-H3",              "path": "FL2VA/processor/",                       "size_gb": 0.02,  "dest": "processor_fl2va/", "is_dir": True},
        ],
        # 哪些源有这个仓库（NF4 仓库 HF 与魔搭均已上架，核实通过）
        "source_repos": {"modelscope": "DiffSynth-Studio/MiniMax-H3-NF4", "hf": "DiffSynth-Studio/MiniMax-H3-NF4", "hf_mirror": "DiffSynth-Studio/MiniMax-H3-NF4"},
        # processor 在各源上的仓库名
        "processor_repos": {"modelscope": "MiniMax/MiniMax-H3", "hf": "MiniMaxAI/MiniMax-H3", "hf_mirror": "MiniMaxAI/MiniMax-H3"},
    },
    {
        "id": "nf4_full",
        "name": "NF4 量化版 · 双分区（FL2VA + Ref2VA 全能）",
        "series": "DiffSynth NF4 量化（官方量化）",
        "engine": "builtin",
        "partition": "FL2VA+Ref2VA",
        "precision": "NF4 (4-bit)",
        "size_gb": 51.5,
        "min_vram_gb": 8,
        "min_ram_gb": 16,
        "recommended": True,
        "desc": "在 FL2VA 基础上增加 Ref2VA 全模态参考分区：支持最多 9 图 + 3 视频 + 3 音频混合参考、视频编辑。想玩全能参考必下。",
        "files": [
            {"repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "minimax-h3-fl2va-nf4.safetensors",        "size_gb": 17.16, "dest": "minimax-h3-fl2va-nf4.safetensors"},
            {"repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "minimax-h3-ref2va-nf4.safetensors",       "size_gb": 17.16, "dest": "minimax-h3-ref2va-nf4.safetensors"},
            {"repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "minimax-h3-text-encoder-nf4.safetensors", "size_gb": 15.33, "dest": "minimax-h3-text-encoder-nf4.safetensors"},
            {"repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "video_vae_nf4.safetensors",                "size_gb": 1.61,  "dest": "video_vae_nf4.safetensors"},
            {"repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "audio_vae_nf4.safetensors",                "size_gb": 0.28,  "dest": "audio_vae_nf4.safetensors"},
            {"repo": "MiniMax/MiniMax-H3",              "path": "FL2VA/processor/",                         "size_gb": 0.02,  "dest": "processor_fl2va/", "is_dir": True},
            {"repo": "MiniMax/MiniMax-H3",              "path": "Ref2VA/processor/",                        "size_gb": 0.02,  "dest": "processor_ref2va/", "is_dir": True},
        ],
        "source_repos": {"modelscope": "DiffSynth-Studio/MiniMax-H3-NF4", "hf": "DiffSynth-Studio/MiniMax-H3-NF4", "hf_mirror": "DiffSynth-Studio/MiniMax-H3-NF4"},
        "processor_repos": {"modelscope": "MiniMax/MiniMax-H3", "hf": "MiniMaxAI/MiniMax-H3", "hf_mirror": "MiniMaxAI/MiniMax-H3"},
    },

    # ═══════ ② 官方 BF16 原版（高显存 / 数据中心）═══════
    {
        "id": "bf16_fl2va",
        "name": "官方原版 BF16 · FL2VA",
        "series": "MiniMax 官方原版（全精度）",
        "engine": "builtin",
        "partition": "FL2VA",
        "precision": "BF16",
        "size_gb": 144.1,
        "min_vram_gb": 48,
        "min_ram_gb": 64,
        "recommended": False,
        "desc": "全精度原版，画质上限最高。DiT 66.3GB + 文本编码器 66.7GB + 视频 VAE 10.4GB + 音频 VAE 0.6GB，建议 48GB 以上显存（A100/H100 级别）。",
        "files": [
            {"repo": "MiniMax/MiniMax-H3", "path": "FL2VA/transformer/",                 "size_gb": 66.28, "dest": "FL2VA/transformer/", "is_dir": True},
            {"repo": "MiniMax/MiniMax-H3", "path": "FL2VA/text_encoder/",                "size_gb": 66.73, "dest": "FL2VA/text_encoder/", "is_dir": True},
            {"repo": "MiniMax/MiniMax-H3", "path": "FL2VA/video_vae/source/model.safetensors", "size_gb": 10.42, "dest": "FL2VA/video_vae/source/model.safetensors"},
            {"repo": "MiniMax/MiniMax-H3", "path": "FL2VA/audio_vae/model.safetensors",  "size_gb": 0.61,  "dest": "FL2VA/audio_vae/model.safetensors"},
            {"repo": "MiniMax/MiniMax-H3", "path": "FL2VA/processor/",                   "size_gb": 0.02,  "dest": "processor_fl2va/", "is_dir": True},
        ],
        "source_repos": {"modelscope": "MiniMax/MiniMax-H3", "hf": "MiniMaxAI/MiniMax-H3", "hf_mirror": "MiniMaxAI/MiniMax-H3"},
    },
    {
        "id": "bf16_ref2va",
        "name": "官方原版 BF16 · Ref2VA",
        "series": "MiniMax 官方原版（全精度）",
        "engine": "builtin",
        "partition": "Ref2VA",
        "precision": "BF16",
        "size_gb": 144.1,
        "min_vram_gb": 48,
        "min_ram_gb": 64,
        "recommended": False,
        "desc": "全模态参考分区原版：9 图 + 3 视频 + 3 音频混合参考、视频编辑、动作迁移。建议 48GB 以上显存。",
        "files": [
            {"repo": "MiniMax/MiniMax-H3", "path": "Ref2VA/transformer/",                "size_gb": 66.28, "dest": "Ref2VA/transformer/", "is_dir": True},
            {"repo": "MiniMax/MiniMax-H3", "path": "Ref2VA/text_encoder/",               "size_gb": 66.73, "dest": "Ref2VA/text_encoder/", "is_dir": True},
            {"repo": "MiniMax/MiniMax-H3", "path": "Ref2VA/video_vae/source/model.safetensors", "size_gb": 10.42, "dest": "Ref2VA/video_vae/source/model.safetensors"},
            {"repo": "MiniMax/MiniMax-H3", "path": "Ref2VA/audio_vae/model.safetensors", "size_gb": 0.61,  "dest": "Ref2VA/audio_vae/model.safetensors"},
            {"repo": "MiniMax/MiniMax-H3", "path": "Ref2VA/processor/",                  "size_gb": 0.02,  "dest": "processor_ref2va/", "is_dir": True},
        ],
        "source_repos": {"modelscope": "MiniMax/MiniMax-H3", "hf": "MiniMaxAI/MiniMax-H3", "hf_mirror": "MiniMaxAI/MiniMax-H3"},
    },

    # ═══════ ③ Comfy-Org 量化套件（供 ComfyUI 使用）═══════
    {
        "id": "comfy_pruned_int8_fl2va",
        "name": "ComfyUI · FL2VA Pruned INT8 套件（16GB 显卡）",
        "series": "Comfy-Org 社区量化（ComfyUI 专用）",
        "engine": "comfyui",
        "partition": "FL2VA",
        "precision": "INT8 ConvRot（剪枝）",
        "size_gb": 42.5,
        "min_vram_gb": 16,
        "min_ram_gb": 32,
        "recommended": False,
        "desc": "ComfyUI 官方组织重打包：剪枝 INT8 主干 21GB + NVFP4-AWQ 文本编码器 15.7GB + VAE。ComfyUI v0.30.0+ 原生支持，RTX 4060 Ti 16G 可跑。",
        "files": [
            {"repo": "Comfy-Org/MiniMax-H3", "path": "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors", "size_gb": 20.97, "dest": "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors"},
            {"repo": "Comfy-Org/MiniMax-H3", "path": "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",        "size_gb": 15.69, "dest": "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"},
            {"repo": "Comfy-Org/MiniMax-H3", "path": "vae/minimax_h3_video_vae_fp16.safetensors",                          "size_gb": 5.21,  "dest": "vae/minimax_h3_video_vae_fp16.safetensors"},
            {"repo": "Comfy-Org/MiniMax-H3", "path": "vae/minimax_h3_audio_vae_fp32.safetensors",                          "size_gb": 0.61,  "dest": "vae/minimax_h3_audio_vae_fp32.safetensors"},
        ],
        "source_repos": {"modelscope": "Comfy-Org/MiniMax-H3", "hf": "Comfy-Org/MiniMax-H3", "hf_mirror": "Comfy-Org/MiniMax-H3"},
    },
    {
        "id": "comfy_pruned_int8_ref2va",
        "name": "ComfyUI · Ref2VA Pruned INT8 套件（16GB 显卡）",
        "series": "Comfy-Org 社区量化（ComfyUI 专用）",
        "engine": "comfyui",
        "partition": "Ref2VA",
        "precision": "INT8 ConvRot（剪枝）",
        "size_gb": 42.5,
        "min_vram_gb": 16,
        "min_ram_gb": 32,
        "recommended": False,
        "desc": "Ref2VA 全模态参考的 ComfyUI 剪枝 INT8 套件，尺寸构成与 FL2VA 版相同。",
        "files": [
            {"repo": "Comfy-Org/MiniMax-H3", "path": "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors", "size_gb": 20.97, "dest": "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors"},
            {"repo": "Comfy-Org/MiniMax-H3", "path": "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",         "size_gb": 15.69, "dest": "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"},
            {"repo": "Comfy-Org/MiniMax-H3", "path": "vae/minimax_h3_video_vae_fp16.safetensors",                           "size_gb": 5.21,  "dest": "vae/minimax_h3_video_vae_fp16.safetensors"},
            {"repo": "Comfy-Org/MiniMax-H3", "path": "vae/minimax_h3_audio_vae_fp32.safetensors",                           "size_gb": 0.61,  "dest": "vae/minimax_h3_audio_vae_fp32.safetensors"},
        ],
        "source_repos": {"modelscope": "Comfy-Org/MiniMax-H3", "hf": "Comfy-Org/MiniMax-H3", "hf_mirror": "Comfy-Org/MiniMax-H3"},
    },
    {
        "id": "comfy_pruned_fp8_fl2va",
        "name": "ComfyUI · FL2VA Pruned FP8 套件（24GB 显卡）",
        "series": "Comfy-Org 社区量化（ComfyUI 专用）",
        "engine": "comfyui",
        "partition": "FL2VA",
        "precision": "FP8 scaled（剪枝）",
        "size_gb": 42.5,
        "min_vram_gb": 24,
        "min_ram_gb": 32,
        "recommended": False,
        "desc": "FP8 剪枝版，画质略优于 INT8，适合 RTX 3090/4090 24GB。",
        "files": [
            {"repo": "Comfy-Org/MiniMax-H3", "path": "diffusion_models/minimax_h3_fl2va_pruned_fp8_scaled.safetensors", "size_gb": 20.96, "dest": "diffusion_models/minimax_h3_fl2va_pruned_fp8_scaled.safetensors"},
            {"repo": "Comfy-Org/MiniMax-H3", "path": "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",      "size_gb": 15.69, "dest": "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"},
            {"repo": "Comfy-Org/MiniMax-H3", "path": "vae/minimax_h3_video_vae_fp16.safetensors",                        "size_gb": 5.21,  "dest": "vae/minimax_h3_video_vae_fp16.safetensors"},
            {"repo": "Comfy-Org/MiniMax-H3", "path": "vae/minimax_h3_audio_vae_fp32.safetensors",                        "size_gb": 0.61,  "dest": "vae/minimax_h3_audio_vae_fp32.safetensors"},
        ],
        "source_repos": {"modelscope": "Comfy-Org/MiniMax-H3", "hf": "Comfy-Org/MiniMax-H3", "hf_mirror": "Comfy-Org/MiniMax-H3"},
    },

    # ═══════ ④ 社区 GGUF（ComfyUI-GGUF 插件用）═══════
    {
        "id": "gguf_fl2va_q4km",
        "name": "社区 GGUF · FL2VA Q4_K_M（12GB 显存档）",
        "series": "Abiray GGUF 社区量化（ComfyUI-GGUF 用）",
        "engine": "comfyui",
        "partition": "FL2VA",
        "precision": "GGUF Q4_K_M",
        "size_gb": 34.4,
        "min_vram_gb": 12,
        "min_ram_gb": 32,
        "recommended": False,
        "desc": "社区 GGUF 量化（Q4_K_M 主干 19.9GB + Q4_K_M 文本编码器 14.6GB），配合 ComfyUI-GGUF 插件使用，适合大内存主机。",
        "files": [
            {"repo": "Abiray/MiniMax-H3-GGUF", "path": "unet/MiniMax-H3-FL2VA-Q4_K_M.gguf",               "size_gb": 19.86, "dest": "unet/MiniMax-H3-FL2VA-Q4_K_M.gguf"},
            {"repo": "Abiray/MiniMax-H3-GGUF", "path": "text_encoders/qwen3vl_32b_minimax_h3-Q4_K_M.gguf", "size_gb": 14.58, "dest": "text_encoders/qwen3vl_32b_minimax_h3-Q4_K_M.gguf"},
        ],
        "source_repos": {"hf": "Abiray/MiniMax-H3-GGUF", "hf_mirror": "Abiray/MiniMax-H3-GGUF"},  # 仅 HF 系（社区仓库未上架魔搭）
    },

    {
        "id": "gguf_ref2va_q4km",
        "name": "社区 GGUF · Ref2VA Q4_K_M（ComfyUI-GGUF 用）",
        "series": "Abiray GGUF 社区量化（ComfyUI-GGUF 用）",
        "engine": "comfyui",
        "partition": "Ref2VA",
        "precision": "GGUF Q4_K_M",
        "size_gb": 34.4,
        "min_vram_gb": 12,
        "min_ram_gb": 32,
        "recommended": False,
        "desc": "Ref2VA 全模态参考分区的 GGUF 量化（19.85GB）+ Q4_K_M 文本编码器 GGUF（14.58GB），配合 ComfyUI-GGUF 插件使用。",
        "files": [
            {"repo": "Abiray/MiniMax-H3-GGUF", "path": "unet/MiniMax-H3-Ref2VA-Q4_K_M.gguf",              "size_gb": 19.85, "dest": "unet/MiniMax-H3-Ref2VA-Q4_K_M.gguf"},
            {"repo": "Abiray/MiniMax-H3-GGUF", "path": "text_encoders/qwen3vl_32b_minimax_h3-Q4_K_M.gguf", "size_gb": 14.58, "dest": "text_encoders/qwen3vl_32b_minimax_h3-Q4_K_M.gguf"},
        ],
        "source_repos": {"hf": "Abiray/MiniMax-H3-GGUF", "hf_mirror": "Abiray/MiniMax-H3-GGUF"},
    },

    # ═══════ ⑤ 社区微调 / 加速 LoRA ═══════
    {
        "id": "lora_instantx_turbo",
        "name": "InstantX Turbo 4 步加速 LoRA",
        "series": "社区微调 · 加速",
        "engine": "lora",
        "partition": "通用",
        "precision": "LoRA (BF16)",
        "size_gb": 0.85,
        "min_vram_gb": 0,
        "min_ram_gb": 0,
        "recommended": False,
        "desc": "InstantX 团队蒸馏的 4 步 Turbo LoRA（851MB）：把默认 50 步采样压缩到 4 步，适合快速预览构图。加载后请将采样步数设为 4。",
        "files": [
            {"repo": "InstantX/MiniMax-H3-Turbo-Lora-Diffusers", "path": "minimax_h3_turbo_4step_ckpt500_diffusers.safetensors", "size_gb": 0.85, "dest": "minimax_h3_turbo_4step.safetensors"},
        ],
        "source_repos": {"hf": "InstantX/MiniMax-H3-Turbo-Lora-Diffusers", "hf_mirror": "InstantX/MiniMax-H3-Turbo-Lora-Diffusers"},
    },
]


def get_bundle(bundle_id: str):
    for b in BUNDLES:
        if b["id"] == bundle_id:
            return b
    return None


def bundle_total_bytes(b: dict) -> int:
    return int(round(sum(f["size_gb"] for f in b["files"]) * 1e9))  # 托管站尺寸为十进制 GB


# ─────────────────────────────────────────────────────────────
# 5. 引擎信息（选型结论，详见调研报告）
# ─────────────────────────────────────────────────────────────
# 多芯片支持矩阵（核实于 2026-08-07）
# 依据：DiffSynth 官方设备抽象（CPU/CUDA/NPU）+ H3 开源日 16 家 Day-0 适配公告
BACKEND_MATRIX = [
    {"backend": "NVIDIA CUDA", "status": "完整支持", "note": "默认路线，NF4/BF16 全量化可选"},
    {"backend": "AMD ROCm", "status": "支持", "note": "Linux ROCm 官方路线（torch 视角为 cuda/HIP）；Windows 覆盖有限"},
    {"backend": "华为昇腾 NPU", "status": "支持", "note": "DiffSynth 官方 NPU 抽象 + torch-npu；昇腾已完成 H3 Day-0 适配"},
    {"backend": "Intel Arc/XPU", "status": "实验性", "note": "Intel 完成 H3 Day-0 适配（多卡 B70 方案）；单卡桌面端未在 H3 管线验证"},
    {"backend": "DirectML", "status": "实验性", "note": "Windows AMD 兜底，H3 管线未验证，建议 ROCm/ComfyUI"},
    {"backend": "ComfyUI 路线", "status": "通用", "note": "Comfy-Org/GGUF 量化包可配合 ComfyUI 在更多硬件上使用"},
]

# ─────────────────────────────────────────────────────────────
# DIY 自定义打包组件目录（全部文件与尺寸已核实，2026-08-09）
# engine: 该组件可用于哪个引擎；quant: 量化族（同族才能成套搭配）
# ─────────────────────────────────────────────────────────────
DIY_COMPONENTS = {
    "dit": [
        {"id": "nf4_fl2va", "name": "NF4 · FL2VA（DiffSynth 官方量化）", "size_gb": 17.16,
         "engine": "diffsynth", "partition": "FL2VA", "quant": "nf4", "min_vram_gb": 8,
         "repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "minimax-h3-fl2va-nf4.safetensors"},
        {"id": "nf4_ref2va", "name": "NF4 · Ref2VA（DiffSynth 官方量化）", "size_gb": 17.16,
         "engine": "diffsynth", "partition": "Ref2VA", "quant": "nf4", "min_vram_gb": 8,
         "repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "minimax-h3-ref2va-nf4.safetensors"},
        {"id": "bf16_fl2va", "name": "BF16 · FL2VA 官方原版（13 分片）", "size_gb": 66.28,
         "engine": "diffsynth", "partition": "FL2VA", "quant": "bf16_official", "min_vram_gb": 48,
         "repo": "MiniMax/MiniMax-H3", "path": "FL2VA/transformer/", "is_dir": True,
         "repo_hf": "MiniMaxAI/MiniMax-H3"},
        {"id": "bf16_ref2va", "name": "BF16 · Ref2VA 官方原版（13 分片）", "size_gb": 66.28,
         "engine": "diffsynth", "partition": "Ref2VA", "quant": "bf16_official", "min_vram_gb": 48,
         "repo": "MiniMax/MiniMax-H3", "path": "Ref2VA/transformer/", "is_dir": True,
         "repo_hf": "MiniMaxAI/MiniMax-H3"},
        {"id": "comfy_int8_fl2va", "name": "INT8 ConvRot · FL2VA（Comfy-Org）", "size_gb": 34.04,
         "engine": "comfyui", "partition": "FL2VA", "quant": "int8", "min_vram_gb": 24,
         "repo": "Comfy-Org/MiniMax-H3", "path": "diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors"},
        {"id": "comfy_int8_ref2va", "name": "INT8 ConvRot · Ref2VA（Comfy-Org）", "size_gb": 34.04,
         "engine": "comfyui", "partition": "Ref2VA", "quant": "int8", "min_vram_gb": 24,
         "repo": "Comfy-Org/MiniMax-H3", "path": "diffusion_models/minimax_h3_ref2va_int8_convrot.safetensors"},
        {"id": "comfy_pruned_int8_fl2va", "name": "Pruned INT8 · FL2VA（Comfy-Org）", "size_gb": 20.97,
         "engine": "comfyui", "partition": "FL2VA", "quant": "int8", "min_vram_gb": 16,
         "repo": "Comfy-Org/MiniMax-H3", "path": "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors"},
        {"id": "comfy_pruned_int8_ref2va", "name": "Pruned INT8 · Ref2VA（Comfy-Org）", "size_gb": 20.97,
         "engine": "comfyui", "partition": "Ref2VA", "quant": "int8", "min_vram_gb": 16,
         "repo": "Comfy-Org/MiniMax-H3", "path": "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors"},
        {"id": "comfy_pruned_fp8_fl2va", "name": "Pruned FP8 · FL2VA（Comfy-Org）", "size_gb": 20.96,
         "engine": "comfyui", "partition": "FL2VA", "quant": "fp8", "min_vram_gb": 24,
         "repo": "Comfy-Org/MiniMax-H3", "path": "diffusion_models/minimax_h3_fl2va_pruned_fp8_scaled.safetensors"},
        {"id": "gguf_q3_fl2va", "name": "GGUF Q3_K_M · FL2VA（Abiray）", "size_gb": 15.57,
         "engine": "comfyui", "partition": "FL2VA", "quant": "gguf", "min_vram_gb": 10,
         "repo": "Abiray/MiniMax-H3-GGUF", "path": "unet/MiniMax-H3-FL2VA-Q3_K_M.gguf"},
        {"id": "gguf_q4_fl2va", "name": "GGUF Q4_K_M · FL2VA（Abiray）", "size_gb": 19.86,
         "engine": "comfyui", "partition": "FL2VA", "quant": "gguf", "min_vram_gb": 12,
         "repo": "Abiray/MiniMax-H3-GGUF", "path": "unet/MiniMax-H3-FL2VA-Q4_K_M.gguf"},
        {"id": "gguf_q4_ref2va", "name": "GGUF Q4_K_M · Ref2VA（Abiray）", "size_gb": 19.85,
         "engine": "comfyui", "partition": "Ref2VA", "quant": "gguf", "min_vram_gb": 12,
         "repo": "Abiray/MiniMax-H3-GGUF", "path": "unet/MiniMax-H3-Ref2VA-Q4_K_M.gguf"},
    ],
    "text_encoder": [
        {"id": "nf4_te", "name": "NF4 文本编码器（DiffSynth 官方量化）", "size_gb": 15.33,
         "engine": "diffsynth", "quant": "nf4",
         "repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "minimax-h3-text-encoder-nf4.safetensors"},
        {"id": "bf16_te", "name": "BF16 文本编码器 官方原版（14 分片）", "size_gb": 66.73,
         "engine": "diffsynth", "quant": "bf16_official",
         "repo": "MiniMax/MiniMax-H3", "path": "FL2VA/text_encoder/", "is_dir": True,
         "repo_hf": "MiniMaxAI/MiniMax-H3"},
        {"id": "comfy_te_nvfp4", "name": "NVFP4-AWQ 文本编码器（Comfy-Org）", "size_gb": 15.69,
         "engine": "comfyui", "quant": "nvfp4",
         "repo": "Comfy-Org/MiniMax-H3", "path": "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"},
        {"id": "comfy_te_int8", "name": "INT8 文本编码器（Comfy-Org）", "size_gb": 27.14,
         "engine": "comfyui", "quant": "int8",
         "repo": "Comfy-Org/MiniMax-H3", "path": "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors"},
        {"id": "comfy_te_bf16", "name": "BF16 文本编码器（Comfy-Org）", "size_gb": 51.51,
         "engine": "comfyui", "quant": "bf16",
         "repo": "Comfy-Org/MiniMax-H3", "path": "text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors"},
        {"id": "gguf_te_q4", "name": "GGUF Q4_K_M 文本编码器（Abiray）", "size_gb": 14.58,
         "engine": "comfyui", "quant": "gguf",
         "repo": "Abiray/MiniMax-H3-GGUF", "path": "text_encoders/qwen3vl_32b_minimax_h3-Q4_K_M.gguf"},
    ],
    "video_vae": [
        {"id": "nf4_vvae", "name": "NF4 视频 VAE（DiffSynth）", "size_gb": 1.61,
         "engine": "diffsynth", "quant": "nf4",
         "repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "video_vae_nf4.safetensors"},
        {"id": "official_vvae", "name": "视频 VAE 官方原版（BF16）", "size_gb": 10.42,
         "engine": "diffsynth", "quant": "bf16_official",
         "repo": "MiniMax/MiniMax-H3", "path": "FL2VA/video_vae/source/model.safetensors",
         "repo_hf": "MiniMaxAI/MiniMax-H3"},
        {"id": "comfy_vvae_fp16", "name": "视频 VAE FP16（Comfy-Org）", "size_gb": 5.21,
         "engine": "comfyui", "quant": "fp16",
         "repo": "Comfy-Org/MiniMax-H3", "path": "vae/minimax_h3_video_vae_fp16.safetensors"},
    ],
    "audio_vae": [
        {"id": "nf4_avvae", "name": "NF4 音频 VAE（DiffSynth）", "size_gb": 0.28,
         "engine": "diffsynth", "quant": "nf4",
         "repo": "DiffSynth-Studio/MiniMax-H3-NF4", "path": "audio_vae_nf4.safetensors"},
        {"id": "official_avvae", "name": "音频 VAE 官方原版", "size_gb": 0.61,
         "engine": "diffsynth", "quant": "bf16_official",
         "repo": "MiniMax/MiniMax-H3", "path": "FL2VA/audio_vae/model.safetensors",
         "repo_hf": "MiniMaxAI/MiniMax-H3"},
        {"id": "comfy_avvae_fp32", "name": "音频 VAE FP32（Comfy-Org）", "size_gb": 0.61,
         "engine": "comfyui", "quant": "fp32",
         "repo": "Comfy-Org/MiniMax-H3", "path": "vae/minimax_h3_audio_vae_fp32.safetensors"},
    ],
    "processor": [
        {"id": "proc_fl2va", "name": "FL2VA Processor（分词/预处理配置）", "size_gb": 0.02,
         "engine": "diffsynth", "partition": "FL2VA", "quant": "any",
         "repo": "MiniMax/MiniMax-H3", "path": "FL2VA/processor/", "is_dir": True,
         "repo_hf": "MiniMaxAI/MiniMax-H3"},
        {"id": "proc_ref2va", "name": "Ref2VA Processor（分词/预处理配置）", "size_gb": 0.02,
         "engine": "diffsynth", "partition": "Ref2VA", "quant": "any",
         "repo": "MiniMax/MiniMax-H3", "path": "Ref2VA/processor/", "is_dir": True,
         "repo_hf": "MiniMaxAI/MiniMax-H3"},
    ],
    "lora": [
        {"id": "lora_instantx_turbo", "name": "InstantX Turbo 4 步加速 LoRA（可选）", "size_gb": 0.85,
         "engine": "diffsynth", "quant": "lora",
         "repo": "InstantX/MiniMax-H3-Turbo-Lora-Diffusers",
         "path": "minimax_h3_turbo_4step_ckpt500_diffusers.safetensors"},
    ],
}

ENGINE_INFO = {
    "name": "DiffSynth-Studio",
    "repo": "https://github.com/modelscope/DiffSynth-Studio",
    "license": "Apache-2.0",
    "pypi": "diffsynth>=2.1.0",
    "stars_verified_at": "2026-08-07（12,862 星，当日仍有提交）",
    "pipeline": "diffsynth.pipelines.minimax_h3_audio_video.MiniMaxH3Pipeline",
    "reason": "Windows 原生支持；NF4 量化最低约 8GB 显存；硬盘→内存→显存三级自动卸载；支持 LoRA 加载与训练；官方维护、当日更新。",
}
