# -*- coding: utf-8 -*-
"""
config.py — 应用设置（持久化到 JSON）与主题定义
"""

import json
import os
from pathlib import Path

APP_DIR_NAME = "H3Studio"


def app_data_dir() -> Path:
    """用户数据目录（Windows: %USERPROFILE%\\H3Studio）"""
    home = Path.home()
    d = home / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


DEFAULT_SETTINGS = {
    # 模型与输出
    "models_dir": str(app_data_dir() / "models"),
    "outputs_dir": str(app_data_dir() / "outputs"),
    "loras_dir": str(app_data_dir() / "loras"),

    # 生成默认参数
    "default_steps": 50,
    "default_ratio": "16:9",
    "default_duration": 5,
    "default_resolution": "768p",
    "default_seed": -1,          # -1 = 随机

    # 高级推理参数（H3 官方默认值，均已核实）
    "cfg_scale": 1.0,            # H3 为 CFG 蒸馏模型，默认 1.0
    "flow_shift": 12.0,          # 视频模态 flow matching 时间步偏移
    "audio_flow_shift": 3.0,     # 音频模态时间步偏移
    "tiled_vae": True,           # 分块 VAE 解码（大幅省显存）
    "tile_size": 256,
    "tile_overlap": 64,
    "rand_device": "cpu",        # 噪声生成设备（cpu 结果跨显卡一致）

    # 引擎设置
    "vram_budget_gb": -1,        # -1 = 自动（可用显存-2GB）；>0 = 手动预算
    "offload_mode": "auto",      # auto / cpu（强制内存卸载）/ disk（强制磁盘流式）
    "torch_threads": -1,         # -1 = 默认

    # 输出设置
    "output_prefix": "h3",
    "save_metadata": True,

    # 下载设置
    "probe_sample_mb": 4,        # 测速采样大小

    # 外观
    "theme": "aurora",           # classic / techblue / aurora / forest（见 THEMES）
    "accent_color": "#3b82f6",
    "glass_opacity": 82,         # 50~95
    "font_scale": 100,           # %

    # 下载
    "preferred_source": "auto",  # auto / modelscope / hf / hf_mirror
    "preferred_source_last_best": "modelscope",  # 最近一次测速的综合最优源
    "download_retries": 5,

    # 引导
    "first_run_done": False,

    # 协议
    "license_accepted": False,
    "license_accepted_at": "",
}

# ─────────────────────────────────────────────────────────────
# 主题（背景基色 / 文本色 / 面板色调，accent 单独可调）
# ─────────────────────────────────────────────────────────────
THEMES = {
    "classic": {
        "label": "经典黑",
        "bg_base": "#050507",
        "bg_aurora_a": "#16161d",
        "bg_aurora_b": "#0b0b10",
        "text": "#f2f2f5",
        "text_dim": "#9a9aa5",
        "default_accent": "#e8e8ee",
    },
    "techblue": {
        "label": "科技蓝",
        "bg_base": "#070d1a",
        "bg_aurora_a": "#0d2b52",
        "bg_aurora_b": "#071224",
        "text": "#e6eefb",
        "text_dim": "#8ba3c7",
        "default_accent": "#38bdf8",
    },
    "aurora": {
        "label": "极光紫",
        "bg_base": "#0b0716",
        "bg_aurora_a": "#341b63",
        "bg_aurora_b": "#101c3f",
        "text": "#f1ecff",
        "text_dim": "#a394c9",
        "default_accent": "#a855f7",
    },
    "forest": {
        "label": "翡翠绿",
        "bg_base": "#05100c",
        "bg_aurora_a": "#0c3d2c",
        "bg_aurora_b": "#082033",
        "text": "#e9f7f0",
        "text_dim": "#8fb5a4",
        "default_accent": "#34d399",
    },
}


class Settings:
    """简单的 JSON 持久化设置。"""

    def __init__(self, path: Path = None):
        self.path = path or (app_data_dir() / "settings.json")
        self._data = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                for k, v in stored.items():
                    if k not in DEFAULT_SETTINGS:
                        continue
                    # 类型校验：防止手改 settings.json 导致运行期崩溃
                    if type(v) is not type(DEFAULT_SETTINGS[k]):
                        continue
                    self._data[k] = v
        except Exception:
            pass

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get(self, key, default=None):
        return self._data.get(key, DEFAULT_SETTINGS.get(key, default))

    def set(self, key, value, autosave=True):
        self._data[key] = value
        if autosave:
            self.save()

    def theme_def(self):
        t = self.get("theme", "aurora")
        return THEMES.get(t, THEMES["aurora"])

    def ensure_dirs(self):
        for k in ("models_dir", "outputs_dir", "loras_dir"):
            Path(self.get(k)).mkdir(parents=True, exist_ok=True)
