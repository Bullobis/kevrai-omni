"""MNN official pre-converted model marketplace.

The MNN team publishes pre-converted (int4-quantized, ready-to-run) MNN models
on HuggingFace (`taobao-mnn` org) and ModelScope (`MNN` org). This module
ships a curated list (verified 2026-08) plus live file enumeration with
mirror fallback so the downloader always has a reachable source.
"""
from __future__ import annotations

from typing import Any

# Mirror prefixes tried in order for both API listing and file downloads.
# More sources = better reachability from CN networks (user preference).
_MIRRORS = (
    "https://hf-cdn.sufy.com",     # verified reachable from CN (small files OK)
    "https://hf-mirror.com",
    "https://hf-mirror.us",
    "https://hf-cn-mirror.com",
    "https://huggingface.co",
)

# ModelScope hosts the same pre-converted models under the `MNN` org —
# fast, stable, and Range-friendly inside CN. Preferred for downloads.
_MS_API = "https://modelscope.cn/api/v1/models"
_MS_RESOLVE = "https://modelscope.cn/models"
_MS_ORG = "MNN"
_SKIP_SUFFIXES = (".md", ".gitattributes", ".png", ".jpg")


def _ms_name(repo: str) -> str:
    """taobao-mnn/Qwen3.6-27B-MNN → MNN/Qwen3.6-27B-MNN"""
    return f"{_MS_ORG}/{repo.split('/', 1)[1]}" if "/" in repo else f"{_MS_ORG}/{repo}"

# ---------------------------------------------------------------------------
# Curated marketplace (taobao-mnn org, verified via HF API 2026-08-25)
# ---------------------------------------------------------------------------

MARKET: list[dict[str, Any]] = [
    {
        "id": "qwen3.5-2b-dflash-mnn",
        "repo": "taobao-mnn/Qwen3.5-2B-Dflash",
        "name": "Qwen3.5 2B Dflash",
        "size_gb": 1.6,
        "quant": "int4",
        "category": "llm",
        "trending": True,
        "description": "2026-07 官方最新：2B 主模型 + Dflash 投机解码草稿，端侧解码速度数倍提升，老笔记本/手机首选。",
        "hardware": {"vram_gb": 3, "min_vram_gb": 0, "ram_gb": 6, "disk_gb": 2,
                     "notes": "2GB 内存即可跑；纯 CPU 高速"},
    },
    {
        "id": "qwen3.6-27b-mnn",
        "repo": "taobao-mnn/Qwen3.6-27B-MNN",
        "name": "Qwen3.6 27B (MNN int4)",
        "size_gb": 16.0,
        "quant": "int4",
        "category": "llm",
        "trending": True,
        "description": "Qwen3.6 27B 官方 MNN 预转换：int4 量化 16GB，MNN CPU 后端速度显著优于 llama.cpp 同级。",
        "hardware": {"vram_gb": 4, "min_vram_gb": 0, "ram_gb": 20, "disk_gb": 16,
                     "notes": "MNN CPU 4 线程即可运行；16GB 内存机器流畅"},
    },
    {
        "id": "qwen3.6-35b-a3b-mnn",
        "repo": "taobao-mnn/Qwen3.6-35B-A3B-MNN",
        "name": "Qwen3.6 35B A3B (MNN int4)",
        "size_gb": 19.0,
        "quant": "int4",
        "category": "llm",
        "trending": True,
        "description": "35B MoE / 3B 激活官方 MNN 版：激活参数少，端侧吞吐极高。",
        "hardware": {"vram_gb": 4, "min_vram_gb": 0, "ram_gb": 24, "disk_gb": 19,
                     "notes": "MoE 激活 3B：8GB 内存即可高吞吐"},
    },
    {
        "id": "qwen3.5-9b-mnn",
        "repo": "taobao-mnn/Qwen3.5-9B-MNN",
        "name": "Qwen3.5 9B (MNN int4)",
        "size_gb": 6.0,
        "quant": "int4",
        "category": "llm",
        "description": "Qwen3.5 9B 官方 MNN 预转换：小钢炮能力接近上代 27B。",
        "hardware": {"vram_gb": 3, "min_vram_gb": 0, "ram_gb": 10, "disk_gb": 6,
                     "notes": "8GB 内存机器流畅"},
    },
    {
        "id": "qwen3.5-4b-mnn",
        "repo": "taobao-mnn/Qwen3.5-4B-MNN",
        "name": "Qwen3.5 4B (MNN int4)",
        "size_gb": 2.8,
        "quant": "int4",
        "category": "llm",
        "description": "Qwen3.5 4B 官方 MNN 预转换：轻薄本/迷你主机友好。",
        "hardware": {"vram_gb": 2, "min_vram_gb": 0, "ram_gb": 6, "disk_gb": 3,
                     "notes": "4GB 内存可跑"},
    },
    {
        "id": "gemma-4-e4b-mnn",
        "repo": "taobao-mnn/gemma-4-E4B-it-MNN",
        "name": "Gemma 4 E4B (MNN int4)",
        "size_gb": 3.2,
        "quant": "int4",
        "category": "llm",
        "trending": True,
        "description": "Google Gemma 4 高效架构 E4B（2026-04 发布）官方 MNN 预转换。",
        "hardware": {"vram_gb": 2, "min_vram_gb": 0, "ram_gb": 6, "disk_gb": 4,
                     "notes": "E 系列主打低功耗设备"},
    },
    {
        "id": "gemma-4-26b-a4b-mnn",
        "repo": "taobao-mnn/gemma-4-26B-A4B-it-MNN",
        "name": "Gemma 4 26B A4B (MNN int4)",
        "size_gb": 15.0,
        "quant": "int4",
        "category": "llm",
        "description": "Gemma 4 MoE：26B 总参 / 4B 激活，质量与速度兼得。",
        "hardware": {"vram_gb": 4, "min_vram_gb": 0, "ram_gb": 18, "disk_gb": 15,
                     "notes": "MoE 激活 4B"},
    },
    {
        "id": "gemma-4-31b-mnn",
        "repo": "taobao-mnn/gemma-4-31B-it-MNN",
        "name": "Gemma 4 31B (MNN int4)",
        "size_gb": 18.0,
        "quant": "int4",
        "category": "llm",
        "description": "Gemma 4 旗舰 dense 31B 官方 MNN 预转换。",
        "hardware": {"vram_gb": 4, "min_vram_gb": 0, "ram_gb": 24, "disk_gb": 18,
                     "notes": "16GB 内存起"},
    },
    {
        "id": "lfm2.5-8b-a1b-mnn",
        "repo": "taobao-mnn/LFM2.5-8B-A1B-MNN",
        "name": "LFM2.5 8B A1B (MNN)",
        "size_gb": 5.0,
        "quant": "int4",
        "category": "llm",
        "trending": True,
        "description": "Liquid AI 混合卷积架构（2026-06 官方 MNN 版）：1B 激活，decode 速度同级最快。",
        "hardware": {"vram_gb": 2, "min_vram_gb": 0, "ram_gb": 8, "disk_gb": 5,
                     "notes": "速度怪兽；6GB 内存可跑"},
    },
    {
        "id": "lfm2.5-230m-mnn",
        "repo": "taobao-mnn/LFM2.5-230M-MNN",
        "name": "LFM2.5 230M (MNN)",
        "size_gb": 0.2,
        "quant": "int4",
        "category": "llm",
        "description": "230M 超微模型：树莓派/老手机可跑，秒级加载。",
        "hardware": {"vram_gb": 1, "min_vram_gb": 0, "ram_gb": 1, "disk_gb": 1,
                     "notes": "任意设备可跑"},
    },
    {
        "id": "minicpm5-1b-mnn",
        "repo": "taobao-mnn/MiniCPM5-1B-MNN",
        "name": "MiniCPM5 1B (MNN)",
        "size_gb": 0.9,
        "quant": "int4",
        "category": "llm",
        "description": "面壁 MiniCPM5（2026-05）：1B 端侧旗舰，中文能力强。",
        "hardware": {"vram_gb": 1, "min_vram_gb": 0, "ram_gb": 3, "disk_gb": 1,
                     "notes": "2GB 内存可跑"},
    },
    {
        "id": "qwen2.5-coder-7b-mnn",
        "repo": "taobao-mnn/Qwen2.5-Coder-7B-Instruct-MNN",
        "name": "Qwen2.5 Coder 7B (MNN)",
        "size_gb": 4.5,
        "quant": "int4",
        "category": "llm",
        "description": "代码补全专用：离线 IDE 代码助手，MNN 版低延迟。",
        "hardware": {"vram_gb": 2, "min_vram_gb": 0, "ram_gb": 8, "disk_gb": 5,
                     "notes": "离线 FIM 补全"},
    },
    {
        "id": "deepseek-r1-1.5b-mnn",
        "repo": "taobao-mnn/DeepSeek-R1-1.5B-Qwen-MNN",
        "name": "DeepSeek R1 1.5B (MNN)",
        "size_gb": 1.1,
        "quant": "int4",
        "category": "llm",
        "description": "R1 蒸馏 1.5B：端侧推理链（CoT）体验模型。",
        "hardware": {"vram_gb": 1, "min_vram_gb": 0, "ram_gb": 3, "disk_gb": 2,
                     "notes": "带思考链输出"},
    },
    {
        "id": "gui-owl-1.5-8b-mnn",
        "repo": "taobao-mnn/GUI-Owl-1.5-8B-Instruct-MNN",
        "name": "GUI-Owl 1.5 8B (MNN)",
        "size_gb": 5.5,
        "quant": "int4",
        "category": "vision",
        "description": "GUI 屏幕理解智能体（2026-03）：截图问答 / 界面元素定位。",
        "hardware": {"vram_gb": 3, "min_vram_gb": 0, "ram_gb": 10, "disk_gb": 6,
                     "notes": "多模态截图理解"},
    },
    {
        "id": "glm-ocr-mnn",
        "repo": "taobao-mnn/GLM-OCR-MNN",
        "name": "GLM-OCR (MNN)",
        "size_gb": 1.5,
        "quant": "int4",
        "category": "vision",
        "description": "智谱 OCR 专用模型：端侧图片文字识别。",
        "hardware": {"vram_gb": 1, "min_vram_gb": 0, "ram_gb": 4, "disk_gb": 2,
                     "notes": "离线 OCR"},
    },
]



# 模型能力标注（对话/视觉模型）——供 OpenClaw 阅读技能与前端能力展示使用
MODALITY: dict[str, dict] = {
    'qwen3.5-2b-dflash-mnn': {'multimodal': False, 'understand': [], 'generate': ['text'], 'notes': '端侧文本模型'},
    'qwen3.6-27b-mnn': {'multimodal': False, 'understand': [], 'generate': ['text'], 'notes': '端侧文本模型'},
    'qwen3.6-35b-a3b-mnn': {'multimodal': False, 'understand': [], 'generate': ['text'], 'notes': '端侧文本模型'},
    'qwen3.5-9b-mnn': {'multimodal': False, 'understand': [], 'generate': ['text'], 'notes': '端侧文本模型'},
    'qwen3.5-4b-mnn': {'multimodal': False, 'understand': [], 'generate': ['text'], 'notes': '端侧文本模型'},
    'gemma-4-e4b-mnn': {'multimodal': False, 'understand': [], 'generate': ['text'], 'notes': '端侧文本模型'},
    'gemma-4-26b-a4b-mnn': {'multimodal': False, 'understand': [], 'generate': ['text'], 'notes': '端侧文本模型'},
    'gemma-4-31b-mnn': {'multimodal': False, 'understand': [], 'generate': ['text'], 'notes': '端侧文本模型'},
    'lfm2.5-8b-a1b-mnn': {'multimodal': False, 'understand': [], 'generate': ['text'], 'notes': '端侧文本模型'},
    'lfm2.5-230m-mnn': {'multimodal': False, 'understand': [], 'generate': ['text'], 'notes': '超微端侧文本模型'},
    'minicpm5-1b-mnn': {'multimodal': False, 'understand': [], 'generate': ['text'], 'notes': '端侧文本模型；MiniCPM-V 系视觉版需另行引入'},
    'qwen2.5-coder-7b-mnn': {'multimodal': False, 'understand': [], 'generate': ['text'], 'notes': '代码补全专用文本模型'},
    'deepseek-r1-1.5b-mnn': {'multimodal': False, 'understand': [], 'generate': ['text'], 'notes': '端侧推理文本模型'},
    'gui-owl-1.5-8b-mnn': {'multimodal': True, 'understand': ['image'], 'generate': ['text'], 'notes': 'GUI 屏幕理解：截图问答/界面元素定位'},
    'glm-ocr-mnn': {'multimodal': True, 'understand': ['image'], 'generate': ['text'], 'notes': 'OCR 专用：端侧图片文字识别'},
}


def market_list() -> list[dict[str, Any]]:
    """Static curated list (offline-safe), merged with modality annotation."""
    items = [dict(e) for e in MARKET]
    for e in items:
        md = MODALITY.get(e["id"])
        if md is not None:
            e["modality"] = md
        else:
            e["modality"] = {"multimodal": False, "understand": [], "generate": [], "notes": "未标注"}
    return items


def get_entry(entry_id: str) -> dict[str, Any] | None:
    for e in MARKET:
        if e["id"] == entry_id:
            return e
    return None


# ---------------------------------------------------------------------------
# Live file enumeration (with mirror fallback)
# ---------------------------------------------------------------------------

def list_mnn_files(repo: str) -> list[dict[str, Any]]:
    """Enumerate files of a pre-converted MNN model repo.

    Strategy: ModelScope first (fast & Range-friendly inside CN), then HF
    mirrors. Each entry carries the primary resolve URL plus the HF fallback
    so the downloader can rotate sources per file.
    """
    import httpx

    headers = {"User-Agent": "KevraiStudio/2.3.0"}
    last_err: Exception | None = None

    # ---- 1) ModelScope ----
    ms_repo = _ms_name(repo)
    try:
        url = f"{_MS_API}/{ms_repo}/repo/files?Revision=master&Root="
        with httpx.Client(timeout=15.0, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            r.raise_for_status()
            files = (r.json().get("Data") or {}).get("Files") or []
        out = []
        for item in files:
            if item.get("Type") != "blob":
                continue
            path = item.get("Path", "")
            if path.startswith(".") or path.endswith(_SKIP_SUFFIXES):
                continue
            out.append({
                "path": path,
                "size": int(item.get("Size") or 0),
                "repo": repo,
                "url": f"{_MS_RESOLVE}/{ms_repo}/resolve/master/{path}",
                "hf_url": f"{_MIRRORS[0]}/{repo}/resolve/main/{path}",
            })
        if out:
            return out
    except Exception as e:  # noqa: BLE001 — fall through to HF mirrors
        last_err = e

    # ---- 2) HF mirrors ----
    for base in _MIRRORS:
        url = f"{base}/api/models/{repo}/tree/main?recursive=true"
        try:
            with httpx.Client(timeout=15.0, follow_redirects=True, headers=headers) as client:
                r = client.get(url)
                r.raise_for_status()
                page = r.json()
            out = []
            for item in page:
                if item.get("type") == "file":
                    path = item.get("path", "")
                    if path.startswith(".") or path.endswith(_SKIP_SUFFIXES):
                        continue
                    out.append({
                        "path": path,
                        "size": item.get("size", 0),
                        "repo": repo,
                        "url": f"{base}/{repo}/resolve/main/{path}",
                        "hf_url": f"{base}/{repo}/resolve/main/{path}",
                    })
            if out:
                return out
        except Exception as e:  # noqa: BLE001 — try next mirror
            last_err = e
            continue
    if last_err is not None:
        raise RuntimeError(f"所有镜像均不可达：{last_err}")
    return []
