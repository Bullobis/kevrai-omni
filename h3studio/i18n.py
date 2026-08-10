# -*- coding: utf-8 -*-
"""
i18n.py — 中英文双语支持
=========================
语言选择规则（用户指定）：
  1. 跟随系统语言：中文系统 → 中文；英文系统 → 英文
  2. 系统语言既不是中文也不是英文 → 默认英文
  3. 设置页可手动覆盖（auto / zh / en）
"""

import locale


def detect_system_lang() -> str:
    """返回 'zh' 或 'en'。中文系统→zh；其他一切情况→en。"""
    loc = ""
    try:
        loc = locale.getlocale()[0] or ""
    except Exception:
        pass
    if not loc:
        try:
            loc = locale.getdefaultlocale()[0] or ""
        except Exception:
            loc = ""
    loc = (loc or "").lower()
    if loc.startswith("zh"):
        return "zh"
    return "en"


_current_lang = None


def set_lang(lang: str):
    """lang: 'auto' / 'zh' / 'en'"""
    global _current_lang
    if lang == "auto":
        _current_lang = detect_system_lang()
    elif lang in ("zh", "en"):
        _current_lang = lang
    else:
        _current_lang = "en"


def lang() -> str:
    if _current_lang is None:
        set_lang("auto")
    return _current_lang


# ─────────────────────────────────────────────
# 翻译表（zh / en）
# ─────────────────────────────────────────────
_TR = {
    # 窗口与导航
    "app_title": ("Kevrai-Omni", "Kevrai-Omni"),
    "app_subtitle": ("全能创作工作站 · by Bullobis", "Omni Creation Studio · by Bullobis"),
    "nav_generate": ("🎬  视频生成", "🎬  Video"),
    "nav_image": ("🖼️  图片生成", "🖼️  Image"),
    "nav_market": ("🏪  模型市场", "🏪  Model Market"),
    "nav_custom": ("🧩  DIY 打包", "🧩  DIY Pack"),
    "nav_library": ("📦  我的模型", "📦  My Models"),
    "nav_gallery": ("🖼  作品库", "🖼  Gallery"),
    "nav_help": ("📖  帮助教程", "📖  Help"),
    "nav_settings": ("⚙  设置", "⚙  Settings"),
    "status_ready": ("就绪", "Ready"),
    "hw_detecting": ("硬件检测中…", "Detecting hardware…"),
    "hw_done": ("硬件检测完成", "Hardware detected"),
    "first_run_tip": ("👋 首次使用？点左侧「📖 帮助教程」，三步上手！",
                      "👋 First time? Click '📖 Help' on the left for a 3-step guide!"),

    # 视频生成页
    "gen_prompt_title": ("提示词", "Prompt"),
    "gen_prompt_hint": ("用自然语言描述画面与声音，可用「图1」「视频1」「音频1」指代下方参考素材；台词请写进提示词。",
                        "Describe the scene and sound naturally. Reference materials as 'Image 1', 'Video 1'. Put dialogue in the prompt."),
    "gen_tpl_title": ("💡 提示词模板（点一下自动填入，适合新手）",
                      "💡 Prompt templates (click to fill, beginner-friendly)"),
    "gen_params_title": ("生成参数", "Generation Parameters"),
    "gen_ratio": ("画面比例", "Aspect Ratio"),
    "gen_duration": ("视频时长", "Duration"),
    "gen_res": ("分辨率档", "Resolution"),
    "gen_res_480": ("480p · 快速预览（更快出片）", "480p · Fast preview"),
    "gen_res_640": ("640p · 均衡", "640p · Balanced"),
    "gen_res_768": ("768p · 标准（H3-Base 默认）", "768p · Standard (H3-Base default)"),
    "gen_fps_badge": ("24 FPS 固定 · 32kHz 立体声", "24 FPS fixed · 32kHz stereo"),
    "gen_steps": ("采样步数", "Steps"),
    "gen_seed": ("随机种子", "Seed"),
    "gen_seed_tip": ("-1 表示每次随机", "-1 = random each time"),
    "gen_count": ("生成数量", "Batch"),
    "gen_count_tip": ("一次生成多个不同种子的版本，方便挑片（依次生成，耗时成倍）",
                      "Generate multiple seed variants (sequential)"),
    "gen_dice_tip": ("随机一个种子", "Random seed"),
    "gen_ref_title": ("参考素材导入", "Reference Materials"),
    "gen_ref_drop": ("点击选择文件，或将 GIF / MP4 / MP3 / 图片 拖拽到这里",
                     "Click to select, or drag GIF / MP4 / MP3 / images here"),
    "gen_ref_remove": ("移除选中", "Remove"),
    "gen_ref_clear": ("清空", "Clear"),
    "gen_retake_none": ("源视频：未选择", "Source video: not selected"),
    "gen_retake_keep_audio": ("保留原音轨", "Keep original audio"),
    "gen_retake_pick": ("选择源视频…", "Choose source video…"),
    "gen_mode_title": ("生成模式", "Mode"),
    "gen_mode_t2va": ("文生视频", "Text→Video"),
    "gen_mode_t2va_d": ("纯文字生成带声音的视频", "Text to video with native audio"),
    "gen_mode_first": ("首帧 → 视频", "First Frame"),
    "gen_mode_first_d": ("提供 1 张首帧图片", "Provide 1 first-frame image"),
    "gen_mode_last": ("尾帧 → 视频", "Last Frame"),
    "gen_mode_last_d": ("提供 1 张尾帧图片", "Provide 1 last-frame image"),
    "gen_mode_fl": ("首尾帧 → 视频", "First+Last"),
    "gen_mode_fl_d": ("提供首、尾 2 张图片", "Provide first & last frames"),
    "gen_mode_ref2va": ("全模态参考", "Omni Reference"),
    "gen_mode_ref2va_d": ("≤9 图 + ≤3 视频 + ≤3 音频混合参考", "≤9 images + ≤3 videos + ≤3 audios"),
    "gen_mode_audio": ("音频驱动", "Audio Driven"),
    "gen_mode_audio_d": ("用音频（台词/音乐）驱动画面", "Drive video with audio"),
    "gen_mode_retake": ("视频编辑", "Video Edit"),
    "gen_mode_retake_d": ("基于源视频重生成指定区间", "Regenerate video segments"),
    "gen_lora_title": ("嵌入模型（LoRA）", "Embedded Model (LoRA)"),
    "gen_lora_import": ("＋导入", "+Import"),
    "gen_lora_import_tip": ("导入 .safetensors/.bin/.pt 社区微调或加速模型",
                            "Import community LoRA files"),
    "gen_lora_none": ("不使用", "None"),
    "gen_lora_strength": ("强度", "Strength"),
    "gen_start": ("▶  开始生成", "▶  Generate"),
    "gen_cancel": ("取消生成", "Cancel"),
    "gen_phase_ready": ("就绪", "Ready"),
    "gen_phase_starting": ("启动中…", "Starting…"),
    "gen_preview": ("预览最新结果", "Preview Latest"),
    "gen_frames_out": ("输出", "Output"),
    "gen_frames_info": ("输出 %(size)s · %(frames)d 帧（%(secs).1fs @24fps，帧数按 17n+5 对齐）",
                        "Output %(size)s · %(frames)d frames (%(secs).1fs @24fps, aligned to 17n+5)"),

    # 图片生成页
    "img_title": ("图片生成", "Image Generation"),
    "img_prompt_hint": ("描述你想生成的图片。推荐：Z-Image-Turbo（快速，8 步出图）或 Qwen-Image-2512（高质量，中文文字渲染强）。图片模型的负向提示词有效，可填写不希望出现的内容。",
                        "Describe the image. Recommended: Z-Image-Turbo (fast, 8 steps) or Qwen-Image-2512 (high quality, great at Chinese text). Negative prompts work for image models."),
    "img_neg": ("负向提示词（图片模型有效）", "Negative Prompt (works for images)"),
    "img_model": ("图片模型", "Image Model"),
    "img_model_tip": ("在「模型市场」下载图片模型后自动识别",
                      "Download image models from Model Market"),
    "img_model_none": ("未检测到图片模型（请先在模型市场下载 Z-Image 或 Qwen-Image）",
                       "No image model found (download Z-Image or Qwen-Image first)"),
    "img_ratio": ("画面比例", "Aspect Ratio"),
    "img_res": ("分辨率", "Resolution"),
    "img_steps": ("采样步数", "Steps"),
    "img_cfg": ("引导强度 (CFG)", "Guidance (CFG)"),
    "img_seed": ("随机种子", "Seed"),
    "img_start": ("▶  生成图片", "▶  Generate Image"),
    "img_engine_tip": ("图片引擎：DiffSynth-Studio（与视频共用显存管理）",
                       "Engine: DiffSynth-Studio (shared VRAM management)"),

    # 市场页
    "mkt_plan_title": ("🎯 你的电脑最优方案（速度 × 质量 × 成本自动权衡）",
                       "🎯 Optimal plan for your PC (speed × quality × cost)"),
    "mkt_plan_dl": ("⬇ 一键下载推荐方案", "⬇ Download Recommended"),
    "mkt_plan_apply": ("应用推荐生成参数", "Apply Recommended Params"),
    "mkt_speed_title": ("下载源智能测速", "Smart Source Speed Test"),
    "mkt_speed_hint": ("真实采样测速：同时测量延迟与下载速度，综合评分（速度 75% + 延迟 25%），不以延迟论英雄",
                       "Real sampling: latency + throughput, score = 75% speed + 25% latency"),
    "mkt_speed_start": ("开始测速", "Start Test"),
    "mkt_speed_retest": ("重新测速", "Retest"),
    "mkt_speed_testing": ("测速中…", "Testing…"),
    "mkt_col_src": ("下载源", "Source"),
    "mkt_col_lat": ("延迟 (ms)", "Latency (ms)"),
    "mkt_col_spd": ("真实速度 (MB/s)", "Speed (MB/s)"),
    "mkt_col_score": ("综合评分", "Score"),
    "mkt_col_state": ("状态", "Status"),
    "mkt_untested": ("未测试", "Not tested"),
    "mkt_models_title": ("模型版本", "Models"),
    "mkt_filter_all": ("全部", "All"),
    "mkt_filter_video": ("🎬 视频模型", "🎬 Video"),
    "mkt_filter_image": ("🖼️ 图片模型", "🖼️ Image"),
    "mkt_filter_lora": ("⚡ LoRA 加速", "⚡ LoRA"),
    "mkt_filter_comfy": ("🔧 ComfyUI 专用", "🔧 ComfyUI Only"),
    "mkt_src_auto": ("自动（按测速结果）", "Auto (by speed test)"),
    "mkt_src_na": ("（未上架）", "(unavailable)"),
    "mkt_btn_dl": ("下载", "Download"),
    "mkt_btn_downloading": ("下载中…", "Downloading…"),
    "mkt_btn_done": ("已完成", "Done"),
    "mkt_btn_resume": ("继续下载", "Resume"),
    "mkt_btn_verify": ("重新校验", "Re-verify"),
    "mkt_state_done": ("✅ 已下载完整", "✅ Downloaded"),
    "mkt_state_partial": ("⏸ 已部分下载，继续下载将断点续传", "⏸ Partial, will resume"),
    "mkt_heat_tip": ("社区热度（下载量/好评数，数据日期见徽章）",
                     "Community heat (see badge date)"),
    "mkt_listing": ("共 %d 个模型（数据核实于 %s）", "%d models (verified %s)"),

    # DIY 页
    "diy_title": ("🧩 DIY 自定义打包（选组件 → 自动校验 → 下载）",
                  "🧩 DIY Custom Pack (pick → validate → download)"),
    "diy_engine": ("目标引擎", "Target Engine"),
    "diy_preset": ("快速预设", "Presets"),

    # 我的模型
    "lib_installed": ("已安装的模型", "Installed Models"),
    "lib_installed_hint": ("内置引擎可加载的模型会显示「加载」按钮；ComfyUI 专用模型请按卡片说明放入 ComfyUI 对应目录。",
                           "Built-in models show a 'Load' button; ComfyUI models follow card instructions."),
    "lib_empty": ("还没有安装任何模型。请前往「模型市场」下载（推荐 NF4 量化版）。",
                  "No models installed. Download from Model Market (NF4 recommended)."),
    "lib_lora_title": ("LoRA 嵌入模型（社区微调 / 加速）", "LoRA Models"),
    "lib_lora_import": ("导入 LoRA 文件…", "Import LoRA…"),
    "lib_lora_dir": ("打开目录", "Open Folder"),
    "lib_lora_hint": ("支持 .safetensors / .bin / .pt。导入后可在生成页「嵌入模型」中选择并调节强度。Turbo 类 LoRA 请配合 4 步采样使用。",
                      "Supports .safetensors/.bin/.pt. Select under 'Embedded Model'. Use Turbo LoRAs with 4 steps."),
    "lib_lora_remove": ("删除选中", "Delete"),
    "lib_custom": ("（DIY 自定义包）", "(DIY pack)"),
    "lib_load": ("加载", "Load"),

    # 作品库
    "gal_title": ("作品库", "Gallery"),
    "gal_open_dir": ("打开输出目录", "Open Output Folder"),
    "gal_refresh": ("刷新", "Refresh"),
    "gal_empty": ("还没有作品。去生成页创作第一个作品吧！",
                  "No works yet. Create your first on the Generate page!"),
    "gal_play": ("▶ 播放", "▶ Play"),

    # 设置页
    "set_theme": ("外观与个性化", "Appearance & Personalization"),
    "set_theme_label": ("主题", "Theme"),
    "set_accent": ("强调色", "Accent Color"),
    "set_accent_pick": ("自定义强调色…", "Custom accent…"),
    "set_font": ("界面字号", "Font Size"),
    "set_opacity": ("玻璃透明度", "Glass Opacity"),
    "set_lang_label": ("界面语言", "Language"),
    "set_lang_auto": ("跟随系统（非中英文默认英文）", "Follow system (EN if not zh/en)"),
    "set_lang_zh": ("中文", "Chinese"),
    "set_lang_en": ("English", "English"),
    "set_lang_note": ("切换语言立即生效并保存。", "Language applies immediately."),
    "set_dirs": ("文件目录", "Folders"),
    "set_dir_models": ("模型目录", "Models Folder"),
    "set_dir_outputs": ("作品输出目录", "Outputs Folder"),
    "set_dir_loras": ("LoRA 目录", "LoRA Folder"),
    "set_dir_browse": ("浏览…", "Browse…"),
    "set_dir_tip": ("提示：模型目录建议放在 NVMe 固态硬盘上，低显存模式会从硬盘流式加载权重。",
                    "Tip: put models on an NVMe SSD; low-VRAM mode streams from disk."),
    "set_engine": ("推理引擎 · 显存与性能", "Engine · VRAM & Performance"),
    "set_vram_budget": ("显存预算 (GB)", "VRAM Budget (GB)"),
    "set_vram_tip": ("-1 = 自动（可用显存 - 2GB）；手动填写则固定预算",
                     "-1 = auto (free VRAM - 2GB); manual = fixed budget"),
    "set_offload": ("卸载策略", "Offload Policy"),
    "set_offload_auto": ("自动（按显卡分档）", "Auto (by GPU tier)"),
    "set_offload_cpu": ("强制内存卸载（速度快，吃内存）", "Force RAM offload (fast)"),
    "set_offload_disk": ("强制磁盘流式（省内存，较慢）", "Force disk streaming (slower)"),
    "set_threads": ("CPU 线程数", "CPU Threads"),
    "set_threads_tip": ("-1 = 自动", "-1 = auto"),
    "set_adv": ("高级生成参数（默认值即 H3 官方推荐值）", "Advanced Parameters (H3 official defaults)"),
    "set_cfg": ("CFG 强度", "CFG Scale"),
    "set_cfg_tip": ("H3 为 CFG 蒸馏模型，默认 1.0；调高会增强提示词约束但可能失真",
                    "H3 is CFG-distilled, default 1.0"),
    "set_flow": ("视频 flow_shift", "Video flow_shift"),
    "set_aflow": ("音频 flow_shift", "Audio flow_shift"),
    "set_tiled": ("分块 VAE 解码（省显存，推荐开启）", "Tiled VAE decode (recommended)"),
    "set_tile_size": ("块大小", "Tile Size"),
    "set_tile_overlap": ("块重叠", "Tile Overlap"),
    "set_rand_dev": ("噪声设备", "Noise Device"),
    "set_rand_cpu": ("CPU（跨显卡结果一致，推荐）", "CPU (consistent, recommended)"),
    "set_rand_gpu": ("GPU（不同显卡结果不同）", "GPU (varies by GPU)"),
    "set_prefix": ("输出文件名前缀", "Output Prefix"),
    "set_meta": ("保存参数元数据 JSON", "Save metadata JSON"),
    "set_dl": ("下载设置", "Download Settings"),
    "set_dl_src": ("默认下载源", "Default Source"),
    "set_probe_mb": ("测速采样大小 (MB)", "Speed Test Sample (MB)"),
    "set_retries": ("下载重试次数", "Download Retries"),
    "set_save_engine": ("保存引擎设置", "Save Engine Settings"),
    "set_save_adv": ("保存高级参数", "Save Advanced"),
    "set_save_dl": ("保存下载设置", "Save Download Settings"),
    "set_saved": ("已保存", "Saved"),

    # 通用
    "common_sec": ("秒", "s"),
    "common_close": ("关闭", "Close"),
}


def tr(key: str) -> str:
    """按当前语言取文案；未知 key 返回 key 本身（便于发现漏翻译）。"""
    pair = _TR.get(key)
    if pair is None:
        return key
    return pair[0] if lang() == "zh" else pair[1]
