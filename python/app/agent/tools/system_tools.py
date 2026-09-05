"""Agent tools — system and hardware tools.

Wraps hardware detection, engine management, model download, and text
generation so the agent can inspect the local environment and take action.
"""
from __future__ import annotations

from typing import Any

from ..tool_registry import Tool, ToolContext


# ---------------------------------------------------------------------------
# check_hardware
# ---------------------------------------------------------------------------
def _check_hardware(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    hw = ctx.hardware_info or {}
    # Guard against ctx.hardware_info being a coroutine (if caller mistakenly
    # assigned the result of an async call without awaiting it).
    if hasattr(hw, "send"):  # coroutine duck-typing
        hw = {}
    if not hw:
        try:
            import asyncio as _aio
            import concurrent.futures
            from pathlib import Path as _Path
            from ...hardware import detect_hardware
            path = ctx.models_dir or ctx.app_root or _Path(".")
            def _detect_sync():
                return _aio.run(detect_hardware(_Path(path)))
            try:
                _loop = _aio.get_event_loop()
                if _loop.is_running():
                    # Running event loop (e.g. FastAPI sidecar): run detection
                    # in a worker thread with its own event loop.
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        hw = pool.submit(_detect_sync).result(timeout=30)
                else:
                    hw = _detect_sync()
            except RuntimeError:
                hw = _detect_sync()
            ctx.hardware_info = hw
        except Exception as e:
            return {"error": f"hardware detection failed: {e}", "hardware": {}}
    # Return a concise summary
    gpu = hw.get("gpu") or {}
    gpu_vendor = hw.get("gpu_vendor", "unknown")
    vram = hw.get("gpu_best_vram_gb", 0)
    ram = hw.get("ram_total_gb", 0)
    disk = (hw.get("disk") or {}).get("free_gb", 0)
    has_discrete = bool(hw.get("has_discrete_gpu", False))
    return {
        "gpu_vendor": gpu_vendor,
        "gpu_name": gpu.get("name", "") if isinstance(gpu, dict) else "",
        "vram_gb": vram,
        "ram_total_gb": ram,
        "disk_free_gb": disk,
        "has_discrete_gpu": has_discrete,
        "bandwidth_mbps": hw.get("bandwidth_mbps", 0),
        "summary": (
            f"GPU: {gpu_vendor} (VRAM {vram}GB), RAM: {ram}GB, "
            f"Disk free: {disk}GB, Discrete GPU: {'yes' if has_discrete else 'no'}"
        ),
    }


check_hardware = Tool(
    name="check_hardware",
    description="Detect and report local hardware (GPU VRAM, RAM, free disk, bandwidth). Always call this before recommending large models or suggesting generation.",
    parameters={"type": "object", "properties": {}, "required": []},
    handler=_check_hardware,
    category="system",
)


# ---------------------------------------------------------------------------
# list_engines
# ---------------------------------------------------------------------------
def _list_engines(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    try:
        from ...engines import list_engines_status
        engines_cat = ctx.engines_catalog or {}
        app_root = ctx.app_root
        if app_root is None:
            return {"error": "app_root not configured", "engines": []}
        statuses = list_engines_status(engines_cat, app_root)
        out = []
        for e in statuses:
            out.append({
                "id": e.get("id"),
                "name": e.get("name"),
                "installed": e.get("installed"),
                "version": e.get("version"),
                "category": e.get("category"),
            })
        installed = [e for e in out if e.get("installed")]
        return {"engines": out, "count": len(out), "installed_count": len(installed)}
    except Exception as e:
        return {"error": str(e), "engines": []}


list_engines = Tool(
    name="list_engines",
    description="List all inference engines and their installation status. Use this to check whether an engine needed for a model (e.g. llama.cpp, MNN, ComfyUI) is installed.",
    parameters={"type": "object", "properties": {}, "required": []},
    handler=_list_engines,
    category="system",
)


# ---------------------------------------------------------------------------
# download_model
# ---------------------------------------------------------------------------
def _download_model(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Provide download guidance for a model.

    The actual download is triggered via the sidecar's download endpoint
    (managed by the Electron renderer's download module). The agent returns
    a structured download plan that the UI can execute, rather than spawning
    downloads directly (which would bypass the user's download queue and
    progress UI).
    """
    model_id = str(params.get("model_id") or "").strip()
    if not model_id:
        return {"error": "model_id is required"}

    catalog = ctx.catalog
    if catalog is None:
        return {"error": "catalog not available"}

    for m in getattr(catalog, "models", []) or []:
        d = m.model_dump() if hasattr(m, "model_dump") else m.__dict__
        if d.get("id") == model_id:
            repo = d.get("repo") or ""
            sources = d.get("sources") or []
            size_gb = d.get("size_gb", 0)
            engines = d.get("engine") or []
            gated = bool(d.get("gated"))
            return {
                "model_id": model_id,
                "name": d.get("name"),
                "repo": repo,
                "size_gb": size_gb,
                "engines": engines,
                "gated": gated,
                "sources": sources[:3],
                "download_plan": (
                    f"在「模型市场」中找到 {d.get('name')}，点击「安装」开始下载。"
                    f"预计大小 {size_gb}GB。"
                    + ("该模型为 gated 仓库，需先在 HuggingFace 接受许可并在设置中配置 HF Token。" if gated else "")
                    + (f"需要先安装引擎：{', '.join(engines)}。" if engines else "")
                ),
                "note": "下载由 UI 下载队列管理（支持断点续传、进度显示、取消），Agent 不直接启动下载以避免绕过用户确认。",
            }
    return {"error": f"model {model_id} not found", "model_id": model_id}


download_model = Tool(
    name="download_model",
    description="Get a download plan for a model (repo, size, required engines, gated status). The actual download is triggered by the user in the UI download queue.",
    parameters={
        "type": "object",
        "properties": {
            "model_id": {"type": "string", "description": "The model ID to download."},
        },
        "required": ["model_id"],
    },
    handler=_download_model,
    category="system",
)


# ---------------------------------------------------------------------------
# generate_text
# ---------------------------------------------------------------------------
def _generate_text(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Generate text using the locally loaded MNN LLM.

    This is the same runtime used by the drama agent. If no model is loaded,
    returns a helpful error with setup instructions.
    """
    prompt = str(params.get("prompt") or "").strip()
    max_new_tokens = int(params.get("max_new_tokens") or 1024)
    max_new_tokens = max(64, min(max_new_tokens, 8192))

    if not prompt:
        return {"error": "prompt is required"}

    try:
        from ... import mnn_runtime
        status = mnn_runtime.status()
        if not status.get("loaded"):
            return {
                "error": "对话 AI 未就绪：请先在「MNN 引擎」页加载一个对话模型。",
                "error_type": "LlmNotReady",
            }
        res = mnn_runtime.chat(prompt, max_new_tokens=max_new_tokens)
        text = str(res.get("text") or "").strip()
        return {
            "text": text,
            "model_name": status.get("model_name", ""),
            "tokens_requested": max_new_tokens,
        }
    except Exception as e:
        return {"error": str(e), "error_type": type(e).__name__}


generate_text = Tool(
    name="generate_text",
    description="Generate text using the locally loaded LLM (MNN runtime). Use this for writing, brainstorming, translation, or any text generation task. Requires a dialogue model to be loaded in the MNN engine page.",
    parameters={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The text generation prompt."},
            "max_new_tokens": {"type": "integer", "description": "Max tokens to generate (default 1024, max 8192)."},
        },
        "required": ["prompt"],
    },
    handler=_generate_text,
    category="generation",
)


# ---------------------------------------------------------------------------
# get_preferences / set_preference
# ---------------------------------------------------------------------------
def _get_preferences(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    mem = ctx.memory
    if mem is None:
        return {"preferences": {}, "error": "memory not available"}
    return {"preferences": mem.get_all_preferences()}


get_preferences = Tool(
    name="get_preferences",
    description="Retrieve all stored user preferences (hardware profile, favorite models, quality settings, etc.). Use this to personalise recommendations.",
    parameters={"type": "object", "properties": {}, "required": []},
    handler=_get_preferences,
    category="memory",
)


def _set_preference(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    key = str(params.get("key") or "").strip()
    value = params.get("value")
    if not key:
        return {"error": "key is required"}
    if len(key) > 128:
        return {"error": "key too long (max 128 chars)"}
    mem = ctx.memory
    if mem is None:
        return {"error": "memory not available"}
    mem.set_preference(key, value)
    return {"ok": True, "key": key, "value": value}


set_preference = Tool(
    name="set_preference",
    description="Store a user preference (e.g. favorite model, quality preset, hardware tier). Use this when the user expresses a recurring preference that should be remembered.",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Preference key (e.g. 'favorite_llm', 'quality_preset', 'hardware_tier')."},
            "value": {"type": "string", "description": "Preference value."},
        },
        "required": ["key", "value"],
    },
    handler=_set_preference,
    category="memory",
)
