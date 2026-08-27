"""AI 短剧生成 Agent（Drama Agent）。

参照 updream（B站对话式 AI 视频创作平台）范式实现本地版短剧创作流水线：
  创意头脑风暴 → 剧本生成 → 角色卡 → 分镜绘制 → 多模态渲染计划
支持多模态编排：图片（定妆/关键帧）、3D（场景/道具资产）、TTS（配音）、
音乐（BGM）、视频（可选成片）。

模型路由策略：用户可选择"对话 AI"（MNN 已加载模型 / MNN 市场模型）与
各环节生成模型（catalog 中 image / 3d / audio / tts / video / llm 类目）。
所有模型均按需下载，渲染执行复用现有下载器与引擎安装流程（本模块只生成
可执行的渲染指令卡，不直接调用外部推理进程）。

提示词工程参考：短剧分镜结构化 JSON（shot_type/camera/dialogue/visual
/duration_s），角色卡跨镜头注入保证一致性，镜头运动词表统一。
"""
from __future__ import annotations

import json
import re
from typing import Any

# 单镜头时长上限（秒），短剧节奏：前 3 秒钩子、情绪曲线、每镜 ≤8 秒
_MAX_SHOT_SECONDS = 8
_MAX_SCENES = 12
_MAX_SHOTS_PER_SCENE = 12
_MAX_CHARACTERS = 12


class DramaAgentError(RuntimeError):
    """短剧编排器可预期错误（映射为 4xx 或 409）。"""


class LlmNotReady(DramaAgentError):
    """对话 AI 未就绪（MNN 模型未加载）。"""


class LlmOutputError(DramaAgentError):
    """对话 AI 输出无法解析为结构化数据。"""


class UnknownModelError(DramaAgentError):
    """用户选择的模型 ID 不在 catalog 中。"""


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _clean_str(v: Any, max_len: int = 2000) -> str:
    s = str(v or "").strip()
    return s[:max_len]


def _try_parse_object(s: str) -> dict[str, Any] | None:
    """从字符串中提取首个完整 JSON 对象；失败返回 None。"""
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _extract_json(text: str) -> dict[str, Any]:
    """从 LLM 输出中提取 JSON 对象（容忍代码围栏/前后杂讯/多围栏）。"""
    t = text.strip()
    # 优先逐个尝试代码围栏块（容忍多个围栏/首个损坏）
    for block in re.findall(r"```(?:json)?\s*(.*?)\s*```", t, re.S):
        obj = _try_parse_object(block)
        if obj is not None:
            return obj
    # 无围栏：整体提取
    obj = _try_parse_object(t)
    if obj is not None:
        return obj
    raise LlmOutputError("对话 AI 未返回可解析的 JSON 对象")


def _compact_model(m: dict[str, Any]) -> dict[str, Any]:
    """从 catalog 条目提取前端下拉所需的精简字段。"""
    return {
        "id": m.get("id", ""),
        "name": m.get("name", ""),
        "category": m.get("category", ""),
        "engine": list(m.get("engine", []) or []),
        "size_gb": m.get("size_gb", 0),
        "license": m.get("license", ""),
        "description": m.get("description", ""),
        "sources": list(m.get("sources", []) or []),
    }


# ---------------------------------------------------------------------------
# 模型选项
# ---------------------------------------------------------------------------

_MODALITY_CATEGORIES = {
    "image": "image",
    "scene3d": "3d",
    "audio": "audio",
    "tts": "tts",
    "video": "video",
    "llm": "llm",
}


def drama_options(catalog: Any, mnn_market: Any, mnn_status: dict[str, Any]) -> dict[str, Any]:
    """返回短剧 Agent 各环节可选择的模型清单。

    - dialogue：对话 AI（模型市场中支持 MNN 引擎的 LLM + MNN 官方预转换市场；
      统一工作流 = 下载引擎 → 模型市场下载模型 → 选 MNN 引擎运行）
    - image / scene3d / audio / tts / video / llm：catalog 各模态模型
    """
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for cat in set(_MODALITY_CATEGORIES.values()):
        by_cat[cat] = []
    for m in getattr(catalog, "models", []) or []:
        cat = getattr(m, "category", "") or ""
        if cat in by_cat:
            by_cat[cat].append(_compact_model(m.model_dump() if hasattr(m, "model_dump") else m.__dict__))

    dialogue = []
    seen_repos: set[str] = set()

    # 1) 模型市场（catalog）中支持 MNN 引擎的 LLM 对话模型（engine 含 mnn，带官方 MNN 预转换仓库）
    for m in getattr(catalog, "models", []) or []:
        d = m.model_dump() if hasattr(m, "model_dump") else m.__dict__
        engines = list(d.get("engine", []) or [])
        if d.get("category") != "llm" or "mnn" not in engines:
            continue
        mnn_repo = str(d.get("mnn_repo", "") or "")
        entry = {
            "id": d.get("id", ""),
            "name": d.get("name", ""),
            "repo": mnn_repo or d.get("repo", ""),
            "size_gb": d.get("size_gb", 0),
            "quant": "",
            "category": "llm",
            "source": "catalog",
            "engine": engines,
            "mnn_repo": mnn_repo,
            "description": d.get("description", ""),
        }
        dialogue.append(entry)
        if entry["repo"]:
            seen_repos.add(entry["repo"])

    # 2) MNN 官方预转换市场（taobao-mnn）补充，按仓库去重
    for entry in (mnn_market.market_list() if mnn_market else []) or []:
        repo = entry.get("repo", "")
        if repo in seen_repos:
            continue
        dialogue.append({
            "id": entry.get("id", ""),
            "name": entry.get("name", ""),
            "repo": repo,
            "size_gb": entry.get("size_gb", 0),
            "quant": entry.get("quant", ""),
            "category": "mnn",
            "source": "mnn_market",
            "engine": ["mnn"],
            "mnn_repo": repo,
            "description": entry.get("description", ""),
        })
        if repo:
            seen_repos.add(repo)

    dialogue.sort(key=lambda x: (x.get("name", "")))

    return {
        "dialogue": dialogue,
        "dialogue_status": {
            "loaded": bool((mnn_status or {}).get("loaded")),
            "model_name": (mnn_status or {}).get("model_name", ""),
            "engine_available": bool((mnn_status or {}).get("engine_available")),
            "dialogue_count": len(dialogue),
        },
        "image": by_cat.get("image", []),
        "scene3d": by_cat.get("3d", []),
        "audio": by_cat.get("audio", []),
        "tts": by_cat.get("tts", []),
        "video": by_cat.get("video", []),
        "llm": by_cat.get("llm", []),
    }


def _validate_model_id(model_id: str | None, catalog: Any, category: str) -> dict[str, Any]:
    """校验并返回用户选择的模型条目（必须存在且类目匹配）。"""
    if not model_id:
        raise DramaAgentError(f"请选择 {category} 类模型")
    if not _MODEL_ID_RE.fullmatch(model_id or ""):
        raise DramaAgentError(f"非法模型 ID：{model_id!r}")
    for m in getattr(catalog, "models", []) or []:
        if getattr(m, "id", "") == model_id:
            d = m.model_dump() if hasattr(m, "model_dump") else m.__dict__
            if (d.get("category") or "") != category:
                raise DramaAgentError(f"模型 {model_id} 不属于 {category} 类目")
            return d
    raise UnknownModelError(f"catalog 中不存在模型：{model_id}")


# ---------------------------------------------------------------------------
# 对话 AI 调用（MNN 运行时）
# ---------------------------------------------------------------------------

def _call_llm(prompt: str, system: str, max_new_tokens: int = 2048) -> str:
    """调用 MNN 已加载模型进行推理。未加载时抛 LlmNotReady。"""
    try:
        from . import mnn_runtime
    except Exception as e:  # pragma: no cover
        raise LlmNotReady(f"MNN 运行时不可用：{e}") from e
    if not mnn_runtime.status().get("loaded"):
        raise LlmNotReady(
            "对话 AI 未就绪：请按顺序 ① 下载 MNN 引擎（AI 引擎页）→ ② 模型市场下载对话模型 "
            "→ ③ 在 MNN 引擎页选择 MNN 引擎加载运行"
        )
    full = f"{system}\n\n{prompt}" if system else prompt
    try:
        res = mnn_runtime.chat(full, max_new_tokens=max_new_tokens)
    except Exception as e:
        raise LlmNotReady(f"对话 AI 调用失败：{e}") from e
    text = str(res.get("text") or "").strip()
    if not text:
        raise LlmOutputError("对话 AI 返回了空结果")
    return text


# ---------------------------------------------------------------------------
# 创意头脑风暴（updream 式开放式引导）
# ---------------------------------------------------------------------------

_BRAINSTORM_SYSTEM = (
    "你是一位资深的 AI 短剧创意导演（参照 updream 的'商量着创作'模式）。"
    "用户会给出一个模糊创意，你需要像真人导演一样，用开放式提问帮助用户锚定故事方向。"
    "只输出 JSON 对象，不要任何多余文字。"
)

_BRAINSTORM_PROMPT = """请围绕用户的创意进行头脑风暴引导。

用户创意：{topic}

要求：
1. 输出一个 JSON 对象：{{"angle": "对创意的一句话解读/锚定方向", "questions": ["问题1", "问题2", "问题3", "问题4", "问题5"]}}
2. questions 是 5 个开放式问题，覆盖：题材/世界观、主角人设与动机、核心冲突/钩子、
   目标观众与平台调性、视觉风格与时长。问题要具体、可回答，帮助下一步生成剧本。
3. 用中文回答，问题之间要有递进关系。"""


def brainstorm(topic: str) -> dict[str, Any]:
    """创意头脑风暴：返回引导方向与 5 个开放式问题。"""
    topic = _clean_str(topic, 1000)
    if not topic:
        topic = "（用户尚未给出具体创意，请给出一个足够有吸引力的通用短剧方向，并引导用户细化）"
    text = _call_llm(
        _BRAINSTORM_PROMPT.format(topic=topic),
        _BRAINSTORM_SYSTEM,
        max_new_tokens=1024,
    )
    obj = _extract_json(text)
    angle = _clean_str(obj.get("angle"), 500)
    questions = obj.get("questions") or []
    if not isinstance(questions, list):
        questions = []
    questions = [_clean_str(q, 300) for q in questions if _clean_str(q, 300)]
    if not questions:
        raise LlmOutputError("对话 AI 未返回引导问题")
    return {"angle": angle, "questions": questions[:6], "topic": topic}


# ---------------------------------------------------------------------------
# 剧本生成（结构化 JSON）
# ---------------------------------------------------------------------------

_SCRIPT_SYSTEM = (
    "你是一位职业短剧编剧 + 分镜导演，擅长为竖屏短剧（单集 60~180 秒）创作"
    "高密度剧情。你输出严格的结构化 JSON 剧本，供下游多模态 AI 流水线执行。"
)

_SCRIPT_PROMPT = """请基于以下创意与头脑风暴结论，创作一集完整的 AI 短剧剧本。

创意/用户要求：{topic}
头脑风暴方向：{angle}
用户对引导问题的回答：
{answers}

输出 JSON 对象，严格遵循以下 schema（不要输出任何多余文字）：
{{
  "title": "剧名（吸睛、可传播）",
  "logline": "一句话梗概（含钩子）",
  "genre": "题材类型",
  "style": "视觉风格关键词（中文，如 赛博朋克/古风水墨/都市写实）",
  "music_mood": "全剧 BGM 氛围（如 悬疑紧张/甜蜜轻快）",
  "characters": [
    {{"name": "角色名", "age": "年龄段", "appearance": "外貌/服装特征（供文生图定妆）", "voice": "音色描述（供 TTS）", "personality": "性格标签"}}
  ],
  "scenes": [
    {{
      "scene_id": 1,
      "location": "场景地点",
      "time": "白天/夜晚/黄昏",
      "summary": "本场剧情摘要",
      "shots": [
        {{"shot_id": 1, "shot_type": "景别(特写/近景/中景/全景/远景)", "camera": "运镜(固定/推近/拉远/横移/环绕/手持/低角度)", "characters": ["角色名"], "action": "画面动作描述", "dialogue": "本镜台词（无则空字符串）", "visual_prompt": "画面视觉提示词（含场景/人物/光影/构图，供文生图）", "duration_s": 4}}
      ]
    }}
  ]
}}

硬性要求：
- 角色 1~{max_chars} 个，场景 1~{max_scenes} 个，每场 2~{max_shots} 个镜头
- 每镜 duration_s 在 2~{max_shot}s 之间，单集总时长 60~180 秒
- 第 1 镜必须是强钩子（冲突/悬念/反转）
- visual_prompt 必须具体到光影、构图、情绪；3D 场景可渲染
- dialogue 使用口语化、高冲突台词，中文"""


def _format_answers(answers: Any) -> str:
    if not answers:
        return "（用户未提供回答，请基于创意自行发挥）"
    if isinstance(answers, dict):
        lines = [f"- {k}：{v}" for k, v in answers.items()]
        return "\n".join(lines)
    if isinstance(answers, list):
        return "\n".join(f"- {a}" for a in answers)
    return str(answers)


def generate_script(topic: str, angle: str, answers: Any) -> dict[str, Any]:
    """生成结构化短剧剧本 JSON。"""
    topic = _clean_str(topic, 1000)
    if not topic:
        raise DramaAgentError("剧本主题不能为空")
    prompt = _SCRIPT_PROMPT.format(
        topic=topic,
        angle=_clean_str(angle, 500) or "（无）",
        answers=_format_answers(answers),
        max_chars=_MAX_CHARACTERS,
        max_scenes=_MAX_SCENES,
        max_shots=_MAX_SHOTS_PER_SCENE,
        max_shot=_MAX_SHOT_SECONDS,
    )
    text = _call_llm(prompt, _SCRIPT_SYSTEM, max_new_tokens=4096)
    obj = _extract_json(text)
    script = _normalize_script(obj)
    script["topic"] = topic
    return script


def _normalize_script(obj: dict[str, Any]) -> dict[str, Any]:
    """清洗 LLM 剧本输出：字段兜底 + 数量/时长钳制。"""
    title = _clean_str(obj.get("title"), 200) or "未命名短剧"
    logline = _clean_str(obj.get("logline"), 500)
    genre = _clean_str(obj.get("genre"), 100)
    style = _clean_str(obj.get("style"), 200)
    music_mood = _clean_str(obj.get("music_mood"), 200)

    characters: list[dict[str, str]] = []
    for c in (obj.get("characters") or [])[:_MAX_CHARACTERS]:
        if not isinstance(c, dict):
            continue
        name = _clean_str(c.get("name"), 100)
        if not name:
            continue
        characters.append({
            "name": name,
            "age": _clean_str(c.get("age"), 100),
            "appearance": _clean_str(c.get("appearance"), 500),
            "voice": _clean_str(c.get("voice"), 300),
            "personality": _clean_str(c.get("personality"), 300),
        })

    scenes = []
    for sc in (obj.get("scenes") or [])[:_MAX_SCENES]:
        if not isinstance(sc, dict):
            continue
        scene_id = int(sc.get("scene_id") or (len(scenes) + 1))
        shots = []
        for sh in (sc.get("shots") or [])[:_MAX_SHOTS_PER_SCENE]:
            if not isinstance(sh, dict):
                continue
            shot_type = _clean_str(sh.get("shot_type"), 100)
            camera = _clean_str(sh.get("camera"), 200)
            chars = sh.get("characters") or []
            if not isinstance(chars, list):
                chars = [chars]
            dur = int(sh.get("duration_s") or 4)
            dur = max(2, min(dur, _MAX_SHOT_SECONDS))
            shots.append({
                "shot_id": int(sh.get("shot_id") or (len(shots) + 1)),
                "scene_id": scene_id,
                "shot_type": shot_type or "中景",
                "camera": camera or "固定",
                "characters": [_clean_str(c, 100) for c in chars if _clean_str(c, 100)],
                "action": _clean_str(sh.get("action"), 500),
                "dialogue": _clean_str(sh.get("dialogue"), 500),
                "visual_prompt": _clean_str(sh.get("visual_prompt"), 1500),
                "duration_s": dur,
            })
        if not shots:
            continue
        scenes.append({
            "scene_id": scene_id,
            "location": _clean_str(sc.get("location"), 200),
            "time": _clean_str(sc.get("time"), 100),
            "summary": _clean_str(sc.get("summary"), 500),
            "shots": shots,
        })

    if not scenes:
        raise LlmOutputError("对话 AI 未返回有效场景/镜头")

    return {
        "title": title,
        "logline": logline,
        "genre": genre,
        "style": style,
        "music_mood": music_mood,
        "characters": characters,
        "scenes": scenes,
        "shot_count": sum(len(s["shots"]) for s in scenes),
        "est_duration_s": sum(sum(sh["duration_s"] for sh in s["shots"]) for s in scenes),
    }


# ---------------------------------------------------------------------------
# 分镜表（补齐多模态渲染所需字段）
# ---------------------------------------------------------------------------

def build_storyboard(script: dict[str, Any]) -> dict[str, Any]:
    """从剧本生成完整分镜表：为每个镜头补齐 3D / TTS / 音乐渲染字段。

    规则化补齐（不额外调用 LLM）：
    - scene3d_prompt：场景/道具 3D 资产描述
    - tts_text：台词（无台词时生成一句转场提示）
    - tts_voice：对应角色音色
    - style_tag：统一视觉风格
    """
    if not isinstance(script, dict) or not script.get("scenes"):
        raise DramaAgentError("剧本为空，请先生成剧本")

    style = _clean_str(script.get("style"), 200)
    music_mood = _clean_str(script.get("music_mood"), 200)
    char_voice: dict[str, str] = {}
    for c in script.get("characters") or []:
        name = _clean_str(c.get("name"), 100)
        if name:
            char_voice[name] = _clean_str(c.get("voice"), 300) or "自然女声"

    shots_out = []
    _scene_seq = 0
    for sc in script.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        _scene_seq += 1
        scene_id = int(sc.get("scene_id") or _scene_seq)
        scene_shots = sc.get("shots") or []
        if not isinstance(scene_shots, list):
            continue
        for sh in scene_shots:
            if not isinstance(sh, dict):
                continue
            dialogue = _clean_str(sh.get("dialogue"), 500)
            speakers = sh.get("characters") or []
            voice = ""
            for sp in speakers:
                if sp in char_voice:
                    voice = char_voice[sp]
                    break
            out = dict(sh)
            out["scene_id"] = scene_id
            out["scene3d_prompt"] = _build_3d_prompt(sc, sh)
            out["tts_text"] = dialogue or f"（转场音效/环境声，{_clean_str(sh.get('action'), 200)}）"
            out["tts_voice"] = voice
            out["style_tag"] = style
            out["music_hint"] = music_mood
            shots_out.append(out)
    if not shots_out:
        raise DramaAgentError("剧本中没有有效镜头，请先生成剧本")
    return {"script": script, "shots": shots_out, "shot_count": len(shots_out)}


def _build_3d_prompt(scene: dict[str, Any], shot: dict[str, Any]) -> str:
    parts = []
    loc = _clean_str(scene.get("location"), 200)
    if loc:
        parts.append(f"场景：{loc}")
    tm = _clean_str(scene.get("time"), 100)
    if tm:
        parts.append(tm)
    vis = _clean_str(shot.get("visual_prompt"), 1500)
    if vis:
        parts.append(f"画面：{vis}")
    return "；".join(parts) or "通用室内场景"


# ---------------------------------------------------------------------------
# 渲染计划（用户选择模型 → 每镜头多模态渲染指令卡）
# ---------------------------------------------------------------------------

def render_plan(
    storyboard: dict[str, Any],
    model_choices: dict[str, Any],
    catalog: Any,
) -> dict[str, Any]:
    """为用户选定的各模态模型生成逐镜头渲染计划。

    model_choices 形如：
    {"image": "flux1-dev", "scene3d": "hunyuan3d-2", "tts": "...",
     "music": "...", "video": "..."}  缺省环节不生成指令卡。
    """
    shots = (storyboard or {}).get("shots") or []
    if not shots:
        raise DramaAgentError("分镜为空，请先生成分镜表")

    choices: dict[str, dict[str, Any]] = {}
    _CAT_TO_CATALOG = {"scene3d": "3d", "music": "audio"}
    for cat in ("image", "scene3d", "tts", "music", "video"):
        mid = (model_choices or {}).get(cat)
        if not mid:
            continue
        # scene3d → catalog 的 3d 类目；music 走 audio 类目
        target_cat = _CAT_TO_CATALOG.get(cat, cat)
        choices[cat] = _validate_model_id(mid, catalog, target_cat)

    plan_shots = []
    for sh in shots:
        if not isinstance(sh, dict):
            continue
        modalities: dict[str, Any] = {}
        if "image" in choices:
            modalities["image"] = {
                "model_id": choices["image"]["id"],
                "model_name": choices["image"]["name"],
                "engine": choices["image"].get("engine", []),
                "size_gb": choices["image"].get("size_gb", 0),
                "prompt": _image_prompt(sh, choices["image"]),
            }
        if "scene3d" in choices:
            modalities["scene3d"] = {
                "model_id": choices["scene3d"]["id"],
                "model_name": choices["scene3d"]["name"],
                "engine": choices["scene3d"].get("engine", []),
                "size_gb": choices["scene3d"].get("size_gb", 0),
                "prompt": sh.get("scene3d_prompt") or _build_3d_prompt({"location": "", "time": ""}, sh),
            }
        if "tts" in choices:
            modalities["tts"] = {
                "model_id": choices["tts"]["id"],
                "model_name": choices["tts"]["name"],
                "engine": choices["tts"].get("engine", []),
                "size_gb": choices["tts"].get("size_gb", 0),
                "text": sh.get("tts_text") or "",
                "voice": sh.get("tts_voice") or "",
            }
        if "music" in choices:
            modalities["music"] = {
                "model_id": choices["music"]["id"],
                "model_name": choices["music"]["name"],
                "engine": choices["music"].get("engine", []),
                "size_gb": choices["music"].get("size_gb", 0),
                "prompt": f"{sh.get('music_hint') or '氛围音乐'}；镜头情绪：{sh.get('action') or sh.get('visual_prompt') or ''}".strip("；"),
            }
        if "video" in choices:
            modalities["video"] = {
                "model_id": choices["video"]["id"],
                "model_name": choices["video"]["name"],
                "engine": choices["video"].get("engine", []),
                "size_gb": choices["video"].get("size_gb", 0),
                "prompt": _video_prompt(sh),
                "duration_s": sh.get("duration_s", 4),
            }
        plan_shots.append({
            "shot_id": sh.get("shot_id"),
            "scene_id": sh.get("scene_id"),
            "shot_type": sh.get("shot_type"),
            "camera": sh.get("camera"),
            "dialogue": sh.get("dialogue", ""),
            "modalities": modalities,
        })

    if not plan_shots:
        raise DramaAgentError("分镜中没有有效镜头，请先生成分镜表")
    return {
        "choices": {cat: m["id"] for cat, m in choices.items()},
        "shots": plan_shots,
        "shot_count": len(plan_shots),
    }


def _image_prompt(shot: dict[str, Any], model: dict[str, Any]) -> str:
    """把分镜镜头翻译为文生图提示词（含统一画风标签）。"""
    parts = []
    style_tag = shot.get("style_tag") or ""
    if style_tag:
        parts.append(f"画风：{style_tag}")
    shot_type = shot.get("shot_type") or "中景"
    camera = shot.get("camera") or "固定"
    parts.append(f"景别：{shot_type}；运镜示意：{camera}")
    vis = shot.get("visual_prompt") or ""
    if vis:
        parts.append(vis)
    return "，".join(parts)


def _video_prompt(shot: dict[str, Any]) -> str:
    """文生视频/图生视频提示词：动作 + 运镜 + 时长。"""
    parts = []
    action = shot.get("action") or ""
    if action:
        parts.append(action)
    camera = shot.get("camera") or "固定"
    parts.append(f"镜头运动：{camera}")
    vis = shot.get("visual_prompt") or ""
    if vis:
        parts.append(vis)
    return "，".join(parts)
