"""Agent skill tools — short-drama studio (drama_studio skill).

Bridges the general-purpose Kevrai Agent to the dedicated short-drama pipeline
in :mod:`app.drama` (brainstorm → structured script → storyboard → multimodal
render plan). The screenwriting *methodology* (two story modes, beat system,
director-style anchors, scene registry, four-stage deliverables) lives in
``app.drama``; these tools expose it to the ReAct agent as callable tools.
"""
from __future__ import annotations

from typing import Any

from ..tool_registry import Tool, ToolContext


def _drama():
    # Lazy import keeps the agent package importable even if drama deps shift.
    from ... import drama
    return drama


# ---------------------------------------------------------------------------
# drama_storycraft — deterministic methodology reference (no LLM required)
# ---------------------------------------------------------------------------
def _storycraft(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    d = _drama()
    ref = d.storycraft_reference()
    mode = str(params.get("mode") or "").strip().lower()
    out: dict[str, Any] = {"reference": ref}
    if mode in ref["modes"]:
        out["selected_mode"] = ref["modes"][mode]
        out["selected_mode_id"] = mode
    # Compact cheat-sheet so a small local LLM is not flooded by the full KB.
    out["mode_cheatsheet"] = {
        mid: {"label": m["label"], "acts": [a["name"] for a in m["acts"]]}
        for mid, m in ref["modes"].items()
    }
    out["beat_values"] = ref["beats"]
    return out


drama_storycraft = Tool(
    name="drama_storycraft",
    description=(
        "获取专业短剧编剧方法论（两种剧作基调的结构、情绪节拍取值、导演/动画风格锚点库、"
        "题材库、四段产物要求）。在创作短剧/微电影/剧情短片剧本前先调用，以选择 mode 与风格锚点。"
        "可选参数 mode=micro_film 或 hook_drama 以返回该基调的完整结构。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "description": "可选：micro_film（微电影三幕式）或 hook_drama（短视频钩子驱动）",
            },
        },
        "required": [],
    },
    handler=_storycraft,
    category="creation",
)


# ---------------------------------------------------------------------------
# drama_brainstorm — LLM guided questions
# ---------------------------------------------------------------------------
def _brainstorm(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    d = _drama()
    topic = str(params.get("topic") or "").strip()
    if not topic:
        return {"error": "topic is required"}
    mode = d._normalize_mode(params.get("mode"))
    return d.brainstorm(topic, mode=mode)


drama_brainstorm = Tool(
    name="drama_brainstorm",
    description=(
        "短剧第一步：给定创意主题与剧作基调，返回方向锚定与 5 个递进式引导问题。"
        "需要已加载对话 AI（MNN）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "创意主题（一句话）"},
            "mode": {"type": "string", "description": "micro_film 或 hook_drama，默认 micro_film"},
        },
        "required": ["topic"],
    },
    handler=_brainstorm,
    category="creation",
)


# ---------------------------------------------------------------------------
# drama_compose_script — LLM structured script
# ---------------------------------------------------------------------------
def _compose_script(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    d = _drama()
    topic = str(params.get("topic") or "").strip()
    if not topic:
        return {"error": "topic is required"}
    angle = str(params.get("angle") or "")
    answers = params.get("answers") or {}
    mode = d._normalize_mode(params.get("mode"))
    style_anchor = str(params.get("style_anchor") or "")
    script = d.generate_script(topic, angle, answers, mode=mode, style_anchor=style_anchor)
    return {
        "script": script,
        "title": script.get("title"),
        "shot_count": script.get("shot_count"),
        "est_duration_s": script.get("est_duration_s"),
        "scene_registry_count": len(script.get("scene_registry") or []),
    }


drama_compose_script = Tool(
    name="drama_compose_script",
    description=(
        "短剧第二步：基于主题、头脑风暴结论与用户回答，产出结构化剧本 JSON"
        "（含剧情梗概、人物小传、场景登记清单、分场分镜、情绪节拍、风格锚点）。"
        "需要已加载对话 AI。mode 取 micro_film/hook_drama。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "创意主题"},
            "angle": {"type": "string", "description": "头脑风暴方向锚定（可空）"},
            "answers": {"type": "object", "description": "引导问题回答（可空）"},
            "mode": {"type": "string", "description": "micro_film 或 hook_drama"},
            "style_anchor": {"type": "string", "description": "导演/动画流派锚点（可空，由 AI 自选）"},
        },
        "required": ["topic"],
    },
    handler=_compose_script,
    category="creation",
)


# ---------------------------------------------------------------------------
# drama_storyboard — deterministic field completion
# ---------------------------------------------------------------------------
def _storyboard(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    d = _drama()
    script = params.get("script")
    if not isinstance(script, dict):
        return {"error": "script object is required"}
    sb = d.build_storyboard(script)
    return {"storyboard": sb, "shot_count": sb.get("shot_count")}


drama_storyboard = Tool(
    name="drama_storyboard",
    description=(
        "短剧第三步：把结构化剧本规则化补齐为分镜表（为每镜补 3D 资产提示、TTS 文本/音色、"
        "统一风格与音乐提示）。纯规则计算，不调用 LLM。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "script": {"type": "object", "description": "drama_compose_script 返回的 script 对象"},
        },
        "required": ["script"],
    },
    handler=_storyboard,
    category="creation",
)


# ---------------------------------------------------------------------------
# drama_render_plan — deterministic per-shot multimodal instruction cards
# ---------------------------------------------------------------------------
def _render_plan(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    d = _drama()
    storyboard = params.get("storyboard")
    if not isinstance(storyboard, dict):
        return {"error": "storyboard object is required"}
    model_choices = params.get("model_choices") or {}
    if not isinstance(model_choices, dict):
        return {"error": "model_choices must be an object"}
    if ctx.catalog is None:
        return {"error": "catalog not available"}
    plan = d.render_plan(storyboard, model_choices, ctx.catalog)
    return {"plan": plan, "shot_count": plan.get("shot_count"), "choices": plan.get("choices")}


drama_render_plan = Tool(
    name="drama_render_plan",
    description=(
        "短剧第四步：为选定的 image/scene3d/tts/music/video 模型生成逐镜头渲染指令卡。"
        "model_choices 形如 {\"image\": 模型ID, \"tts\": 模型ID}，缺省环节跳过。纯规则计算。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "storyboard": {"type": "object", "description": "drama_storyboard 返回的 storyboard 对象"},
            "model_choices": {"type": "object", "description": "各模态模型 ID 映射，可省略不使用的模态"},
        },
        "required": ["storyboard"],
    },
    handler=_render_plan,
    category="creation",
)


DRAMA_TOOLS = [
    drama_storycraft,
    drama_brainstorm,
    drama_compose_script,
    drama_storyboard,
    drama_render_plan,
]
