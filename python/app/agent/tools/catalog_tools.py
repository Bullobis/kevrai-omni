"""Agent tools — catalog and model-management tools.

Each tool wraps an existing Kevrai Omni subsystem (catalog search, model
detail, hardware-aware recommendation, installed models) so the agent can
orchestrate them through natural language.
"""
from __future__ import annotations

from typing import Any

from ..tool_registry import Tool, ToolContext


# ---------------------------------------------------------------------------
# search_models
# ---------------------------------------------------------------------------
# Chinese keyword → category mapping (for natural-language queries)
_CN_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "llm": ["大语言", "对话", "聊天", "文本", "写作", "翻译", "llm", "语言模型", "问答"],
    "image": ["图像", "图片", "文生图", "画图", "绘画", "image", "出图"],
    "audio": ["音频", "音乐", "music", "声音", "配乐", "bgm", "音效", "作曲", "编曲"],
    "video": ["视频", "video", "文生视频", "短片", "动画", "影片"],
    "tts": ["语音", "tts", "配音", "朗读", "语音合成", "克隆声音"],
    "3d": ["3d", "三维", "建模", "3d生成"],
    "superres": ["超分", "superres", "放大", "清晰度", "超分辨率", "修复"],
    "vision": ["视觉", "vision", "识别", "检测"],
}


def _detect_category_from_query(query: str) -> str | None:
    """Detect a model category from a natural-language (Chinese or English) query."""
    ql = query.lower()
    for cat, keywords in _CN_CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in ql:
                return cat
    return None


def _search_models(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    query = str(params.get("query") or "").strip()
    category = params.get("category")
    limit = int(params.get("limit") or 10)
    limit = max(1, min(limit, 50))

    if not query and not category:
        return {"results": [], "count": 0, "hint": "请提供搜索关键词或类别"}

    catalog = ctx.catalog
    if catalog is None:
        return {"error": "catalog not available", "count": 0}

    # Auto-detect category from natural-language query if not specified
    detected_category = _detect_category_from_query(query) if query else None
    effective_category = category or detected_category

    def _match(m_dict: dict[str, Any], cat: str | None, q: str) -> bool:
        if cat and (m_dict.get("category") or "") != cat:
            return False
        if not q:
            return True
        ql = q.lower()
        haystack = (
            str(m_dict.get("name", "")) + " " + str(m_dict.get("description", ""))
            + " " + str(m_dict.get("id", "")) + " " + str(m_dict.get("repo", ""))
            + " " + " ".join(m_dict.get("tags") or [])
            + " " + " ".join(m_dict.get("engine") or [])
        ).lower()
        return ql in haystack

    # First pass: with effective category + query substring
    results = []
    for m in getattr(catalog, "models", []) or []:
        d = m.model_dump() if hasattr(m, "model_dump") else m.__dict__
        if _match(d, effective_category, query):
            results.append({
                "id": d.get("id"),
                "name": d.get("name"),
                "category": d.get("category"),
                "description": str(d.get("description", ""))[:200],
                "size_gb": d.get("size_gb"),
                "engine": d.get("engine"),
                "license": d.get("license"),
            })
            if len(results) >= limit:
                break

    # Fallback: if nothing found but we have an effective category (from params
    # or auto-detection), return all models in that category — the user's intent
    # was category-level browsing rather than a specific model name.
    if not results and effective_category:
        for m in getattr(catalog, "models", []) or []:
            d = m.model_dump() if hasattr(m, "model_dump") else m.__dict__
            if (d.get("category") or "") == effective_category:
                results.append({
                    "id": d.get("id"),
                    "name": d.get("name"),
                    "category": d.get("category"),
                    "description": str(d.get("description", ""))[:200],
                    "size_gb": d.get("size_gb"),
                    "engine": d.get("engine"),
                    "license": d.get("license"),
                })
                if len(results) >= limit:
                    break

    return {
        "results": results,
        "count": len(results),
        "query": query,
        "category": effective_category,
        "detected_category": detected_category,
    }


search_models = Tool(
    name="search_models",
    description="Search the model catalog by keyword or category. Use this to find models for a specific task (e.g. 'music generation', 'image', '8GB LLM').",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keyword (model name, description, tag, engine)."},
            "category": {"type": "string", "description": "Optional category filter: llm, tts, video, image, superres, audio, 3d, vision."},
            "limit": {"type": "integer", "description": "Max results to return (default 10, max 50)."},
        },
        "required": [],
    },
    handler=_search_models,
    category="catalog",
)


# ---------------------------------------------------------------------------
# model_info
# ---------------------------------------------------------------------------
def _model_info(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    model_id = str(params.get("model_id") or "").strip()
    if not model_id:
        return {"error": "model_id is required"}

    catalog = ctx.catalog
    if catalog is None:
        return {"error": "catalog not available"}

    for m in getattr(catalog, "models", []) or []:
        d = m.model_dump() if hasattr(m, "model_dump") else m.__dict__
        if d.get("id") == model_id:
            return {
                "id": d.get("id"),
                "name": d.get("name"),
                "category": d.get("category"),
                "description": d.get("description"),
                "repo": d.get("repo"),
                "size_gb": d.get("size_gb"),
                "engine": d.get("engine"),
                "license": d.get("license"),
                "tags": d.get("tags"),
                "modality": d.get("modality"),
                "hardware": d.get("hardware"),
                "trending": d.get("trending"),
            }
    return {"error": f"model {model_id} not found", "model_id": model_id}


model_info = Tool(
    name="model_info",
    description="Get detailed information about a specific model by its ID. Use after search_models to inspect a model's hardware requirements, license, and supported engines.",
    parameters={
        "type": "object",
        "properties": {
            "model_id": {"type": "string", "description": "The model ID (e.g. 'minimax-music3', 'granite-4.2-8b')."},
        },
        "required": ["model_id"],
    },
    handler=_model_info,
    category="catalog",
)


# ---------------------------------------------------------------------------
# recommend_models
# ---------------------------------------------------------------------------
def _recommend_models(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    category = params.get("category")
    limit = int(params.get("limit") or 5)
    limit = max(1, min(limit, 20))

    hw = ctx.hardware_info or {}
    if not hw:
        return {"error": "hardware info not available — call check_hardware first", "results": []}

    catalog = ctx.catalog
    if catalog is None:
        return {"error": "catalog not available", "results": []}

    from ...recommend import recommend as _rec
    models_dicts = [
        (m.model_dump() if hasattr(m, "model_dump") else m.__dict__)
        for m in getattr(catalog, "models", []) or []
    ]
    recs = _rec(models_dicts, hw, limit=limit, category=category)
    out = []
    for r in recs:
        out.append({
            "id": r.get("id"),
            "name": r.get("name"),
            "category": r.get("category"),
            "size_gb": r.get("size_gb"),
            "fit": (r.get("recommendation") or {}).get("fit"),
            "score": (r.get("recommendation") or {}).get("score"),
            "reasons": (r.get("recommendation") or {}).get("reasons", []),
        })
    return {"results": out, "count": len(out), "category": category}


recommend_models = Tool(
    name="recommend_models",
    description="Recommend models that fit the local hardware (VRAM, RAM, disk). Use this when the user asks 'what model can I run?' or wants suggestions for their machine. Requires check_hardware to have been called.",
    parameters={
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "Optional category filter (llm, image, audio, video, etc.)."},
            "limit": {"type": "integer", "description": "Max recommendations (default 5, max 20)."},
        },
        "required": [],
    },
    handler=_recommend_models,
    category="catalog",
)


# ---------------------------------------------------------------------------
# list_installed
# ---------------------------------------------------------------------------
def _list_installed(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    try:
        from ...importer import load_local_registry
        models_dir = ctx.models_dir
        if models_dir is None:
            return {"error": "models_dir not configured", "installed": []}
        local = load_local_registry(models_dir)
        return {"installed": local, "count": len(local)}
    except Exception as e:
        return {"error": str(e), "installed": []}


list_installed = Tool(
    name="list_installed",
    description="List models that are already downloaded and installed locally. Use this to check whether a model is available before suggesting download or generation.",
    parameters={"type": "object", "properties": {}, "required": []},
    handler=_list_installed,
    category="catalog",
)


# ---------------------------------------------------------------------------
# list_categories
# ---------------------------------------------------------------------------
def _list_categories(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    catalog = ctx.catalog
    if catalog is None:
        return {"error": "catalog not available", "categories": []}
    counts: dict[str, int] = {}
    for m in getattr(catalog, "models", []) or []:
        cat = getattr(m, "category", "unknown") or "unknown"
        counts[cat] = counts.get(cat, 0) + 1
    return {
        "categories": [
            {"id": c, "label": _CATEGORY_LABELS.get(c, c), "count": n}
            for c, n in sorted(counts.items(), key=lambda x: -x[1])
        ],
        "total": sum(counts.values()),
    }


_CATEGORY_LABELS = {
    "llm": "大语言模型 / LLM",
    "tts": "语音合成 / TTS",
    "video": "视频生成 / Video",
    "image": "图像生成 / Image",
    "superres": "超分辨率 / Super-Resolution",
    "audio": "音频生成 / Audio",
    "3d": "3D 生成 / 3D",
    "vision": "视觉工具 / Vision",
    "pending": "待官方开源 / Pending",
}


list_categories = Tool(
    name="list_categories",
    description="List all model categories in the catalog with their model counts. Use this to understand what types of models are available.",
    parameters={"type": "object", "properties": {}, "required": []},
    handler=_list_categories,
    category="catalog",
)
