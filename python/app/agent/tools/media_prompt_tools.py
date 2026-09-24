"""Agent skill tools — media prompt studio (media_prompt skill).

Deterministic prompt-engineering tools that turn a short brief into a complete,
executable prompt package for image / video / music generation. Every result
is JSON and always contains three guardrail sections the project relies on:

- ``positive``        — composed positive prompt (Chinese, comma-separated)
- ``negative``        — curated negative prompt + caller-supplied negatives
- ``theme_constraints`` — hard consistency / safety constraints for the shot

No LLM is required, so these work in rule-based mode and are fully testable.
"""
from __future__ import annotations

from typing import Any

from ..tool_registry import Tool, ToolContext

# ---------------------------------------------------------------------------
# Curated banks
# ---------------------------------------------------------------------------
_QUALITY_TAGS = "高质量, 细节丰富, 专业级, 清晰对焦"

_IMAGE_NEGATIVE_BANK = [
    "低质量", "模糊", "噪点", "畸变", "多余手指", "肢体残缺", "解剖错误",
    "水印", "签名", "logo", "乱码文字", "变形", "过曝", "欠曝", "重复", "丑陋",
]

_VIDEO_NEGATIVE_BANK = [
    "画面闪烁", "抖动鬼影", "人物变脸", "身份漂移", "肢体融化", "多余肢体",
    "物体凭空出现或消失", "镜头跳变", "穿帮", "水印", "乱码字幕", "扭曲变形",
    "卡顿", "拖影过重", "不符合物理的运动",
]

_MUSIC_NEGATIVE_BANK = [
    "爆音", "削波失真", "节奏混乱", "跑调", "人声杂音", "突兀截断", "循环卡顿", "采样噪声",
]

_CAMERA_WORDS = {"固定", "推近", "拉远", "横移", "环绕", "手持", "低角度", "升镜", "降镜", "跟拍"}
_VALID_RATIOS = {"1:1", "3:4", "4:3", "9:16", "16:9", "21:9"}


def _clean(v: Any, max_len: int = 500) -> str:
    return str(v or "").strip()[:max_len]


def _as_int(value: Any, default: int, lo: int, hi: int, field: str) -> int:
    """Coerce an LLM-supplied integer parameter with safe clamping.

    Accepts int, float (truncated) and numeric strings; any other type raises
    a clean ``ValueError`` (surfaced by the registry as ``ok: False`` with an
    actionable message) instead of a bare ``int()`` traceback.
    """
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer, got {value!r}")
    try:
        n = value if isinstance(value, int) else int(float(str(value).strip()))
    except (ValueError, TypeError):
        raise ValueError(f"{field} must be an integer, got {value!r}")
    return max(lo, min(n, hi))


def _split_tags(v: Any) -> list[str]:
    if isinstance(v, list):
        raw = [str(x) for x in v]
    else:
        import re as _re
        raw = _re.split(r"[,，、;；]", str(v or ""))
    out, seen = [], set()
    for t in raw:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t[:80])
    return out


def _merge_negative(bank: list[str], extra: list[str]) -> tuple[str, list[str]]:
    merged, seen = [], set()
    for t in bank + extra:
        if t and t not in seen:
            seen.add(t)
            merged.append(t)
    return ", ".join(merged), merged


# ---------------------------------------------------------------------------
# Image prompt
# ---------------------------------------------------------------------------
def _build_image_prompt(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    subject = _clean(params.get("subject"))
    if not subject:
        return {"error": "subject is required"}
    scene = _clean(params.get("scene"))
    style = _clean(params.get("style"))
    mood = _clean(params.get("mood"))
    lighting = _clean(params.get("lighting"))
    composition = _clean(params.get("composition"))
    ratio = _clean(params.get("ratio")) or "16:9"
    if ratio not in _VALID_RATIOS:
        return {"error": f"invalid ratio {ratio!r}, choose from {sorted(_VALID_RATIOS)}"}
    extra_pos = _split_tags(params.get("extra_tags"))
    extra_neg = _split_tags(params.get("negative_tags"))

    pos_parts = [subject]
    for label, val in (("场景", scene), ("风格", style), ("光影", lighting),
                       ("构图", composition), ("情绪", mood)):
        if val:
            pos_parts.append(f"{label}:{val}")
    pos_parts.extend(extra_pos)
    pos_parts.append(_QUALITY_TAGS)
    negative, neg_list = _merge_negative(_IMAGE_NEGATIVE_BANK, extra_neg)

    constraints = [
        f"画幅比例固定为 {ratio}",
        "主体外观、服装、配色在系列图中保持一致",
        "不出现任何文字、水印、签名或乱码字符",
        "风格与光影全程统一，不混搭冲突画风",
    ]
    if style:
        constraints.append(f"严格保持风格锚点：{style}")
    return {
        "modality": "image",
        "positive": "，".join(pos_parts),
        "structured": {
            "subject": subject, "scene": scene, "style": style, "mood": mood,
            "lighting": lighting or "自然光影", "composition": composition or "中景",
            "quality": _QUALITY_TAGS, "extra_tags": extra_pos,
        },
        "negative": negative,
        "negative_tags": neg_list,
        "theme_constraints": constraints,
        "parameters": {"ratio": ratio},
    }


build_image_prompt = Tool(
    name="build_image_prompt",
    description=(
        "把简短画面需求编译为完整的文生图提示词包（JSON：positive 正向提示词、negative 负面提示词、"
        "theme_constraints 主题一致性约束、parameters）。用于定妆图/关键帧/场景图，保证跨图一致。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "画面主体（必填）"},
            "scene": {"type": "string", "description": "场景环境"},
            "style": {"type": "string", "description": "风格锚点"},
            "mood": {"type": "string", "description": "情绪氛围"},
            "lighting": {"type": "string", "description": "光影"},
            "composition": {"type": "string", "description": "构图/景别/镜头"},
            "ratio": {"type": "string", "description": "画幅 1:1/3:4/4:3/9:16/16:9/21:9，默认 16:9"},
            "extra_tags": {"type": "array", "description": "额外正向标签"},
            "negative_tags": {"type": "array", "description": "额外负面标签"},
        },
        "required": ["subject"],
    },
    handler=_build_image_prompt,
    category="creation",
)


# ---------------------------------------------------------------------------
# Video prompt
# ---------------------------------------------------------------------------
def _build_video_prompt(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    subject = _clean(params.get("subject"))
    action = _clean(params.get("action"))
    if not subject or not action:
        return {"error": "subject and action are required"}
    scene = _clean(params.get("scene"))
    style = _clean(params.get("style"))
    camera = _clean(params.get("camera")) or "固定"
    if camera not in _CAMERA_WORDS:
        # free-form camera descriptions are allowed but known words are preferred
        pass
    duration = _as_int(params.get("duration_s"), 5, 1, 15, "duration_s")
    extra_pos = _split_tags(params.get("extra_tags"))
    extra_neg = _split_tags(params.get("negative_tags"))

    pos_parts = [f"主体:{subject}", f"动作:{action}"]
    if scene:
        pos_parts.append(f"场景:{scene}")
    if style:
        pos_parts.append(f"风格:{style}")
    pos_parts.append(f"镜头运动:{camera}")
    pos_parts.extend(extra_pos)
    positive = "，".join(pos_parts)
    negative, neg_list = _merge_negative(_VIDEO_NEGATIVE_BANK, extra_neg)

    constraints = [
        f"单段时长 {duration}s，镜头运动明确为：{camera}",
        "主角面部、发型、服装、体型与参考/上一镜保持一致，禁止变脸",
        "动作连贯、符合物理，无突变、无瞬移、无物体凭空增减",
        "不生成随机字幕、水印、乱码 UI 或不可读文字",
        "首尾帧稳定，结尾不引入新主体或新信息",
    ]
    if style:
        constraints.append(f"色调与风格统一于：{style}")
    return {
        "modality": "video",
        "positive": positive,
        "negative": negative,
        "negative_tags": neg_list,
        "theme_constraints": constraints,
        "shot_plan": {
            "subject": subject, "action": action, "camera": camera,
            "scene": scene, "style": style,
        },
        "parameters": {"duration_s": duration},
    }


build_video_prompt = Tool(
    name="build_video_prompt",
    description=(
        "把镜头需求编译为文生/图生视频提示词包（JSON：positive、negative 防闪烁防变脸、"
        "theme_constraints 一致性约束、shot_plan、parameters.duration_s 1-15）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "镜头主体（必填）"},
            "action": {"type": "string", "description": "主体动作（必填，客观可拍）"},
            "camera": {"type": "string", "description": "运镜：固定/推近/拉远/横移/环绕/手持/低角度等"},
            "scene": {"type": "string", "description": "场景"},
            "style": {"type": "string", "description": "风格/色调"},
            "duration_s": {"type": "integer", "description": "时长 1-15 秒，默认 5"},
            "extra_tags": {"type": "array", "description": "额外正向标签"},
            "negative_tags": {"type": "array", "description": "额外负面标签"},
        },
        "required": ["subject", "action"],
    },
    handler=_build_video_prompt,
    category="creation",
)


# ---------------------------------------------------------------------------
# Music prompt
# ---------------------------------------------------------------------------
def _build_music_prompt(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    mood = _clean(params.get("mood"))
    if not mood:
        return {"error": "mood is required"}
    genre = _clean(params.get("genre"))
    instruments = _split_tags(params.get("instruments"))
    tempo = _as_int(params.get("tempo_bpm"), 90, 40, 220, "tempo_bpm")
    duration = _as_int(params.get("duration_s"), 30, 2, 600, "duration_s")
    extra_neg = _split_tags(params.get("negative_tags"))

    pos_parts = [f"情绪:{mood}"]
    if genre:
        pos_parts.append(f"曲风:{genre}")
    if instruments:
        pos_parts.append(f"乐器:{'/'.join(instruments)}")
    pos_parts.append(f"速度:{tempo}BPM")
    positive = "，".join(pos_parts)
    negative, neg_list = _merge_negative(_MUSIC_NEGATIVE_BANK, extra_neg)

    constraints = [
        f"速度恒定约 {tempo} BPM，全曲情绪统一于：{mood}",
        "配器不互相打架，人声（如非要求）不出现",
        "结构为 引子-发展-高潮-收束，结尾自然淡出不突兀截断",
        "循环段保持音高与节奏稳定",
    ]
    return {
        "modality": "music",
        "positive": positive,
        "negative": negative,
        "negative_tags": neg_list,
        "theme_constraints": constraints,
        "structure": ["intro", "build", "climax", "outro"],
        "parameters": {"tempo_bpm": tempo, "duration_s": duration,
                       "genre": genre, "instruments": instruments},
    }


build_music_prompt = Tool(
    name="build_music_prompt",
    description=(
        "把音乐需求编译为配乐提示词包（JSON：positive、negative 防爆音跑调、"
        "theme_constraints、structure 结构、parameters：BPM/时长/乐器）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "mood": {"type": "string", "description": "情绪氛围（必填）"},
            "genre": {"type": "string", "description": "曲风"},
            "instruments": {"type": "array", "description": "乐器列表"},
            "tempo_bpm": {"type": "integer", "description": "40-220，默认 90"},
            "duration_s": {"type": "integer", "description": "2-600 秒，默认 30"},
            "negative_tags": {"type": "array", "description": "额外负面标签"},
        },
        "required": ["mood"],
    },
    handler=_build_music_prompt,
    category="creation",
)


MEDIA_PROMPT_TOOLS = [build_image_prompt, build_video_prompt, build_music_prompt]
