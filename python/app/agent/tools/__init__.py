"""Agent tools package — built-in tools and pluggable skill bundles.

v2.7.0 exposed a flat list of 11 tools via :data:`ALL_TOOLS` and
:func:`build_default_registry`; both are preserved unchanged for backwards
compatibility.

v2.8.0 groups tools (and new creative tool packs) into *skills* via
:data:`BUILTIN_SKILLS` and :func:`build_skill_manager`. A skill is an
enableable bundle of tools plus optional system-prompt guidance; the
:class:`~app.agent.skill.SkillManager` materialises the active registry.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..skill import Skill, SkillManager
from ..tool_registry import ToolRegistry
from .catalog_tools import (
    list_categories,
    list_installed,
    model_info,
    recommend_models,
    search_models,
)
from .drama_tools import DRAMA_TOOLS
from .media_prompt_tools import MEDIA_PROMPT_TOOLS
from .system_tools import (
    check_hardware,
    download_model,
    generate_text,
    get_preferences,
    list_engines,
    set_preference,
)
from .writing_tools import WRITING_TOOLS

# ---------------------------------------------------------------------------
# v2.7.0 flat tool list (unchanged — used by build_default_registry & tests)
# ---------------------------------------------------------------------------
ALL_TOOLS = [
    search_models,
    model_info,
    recommend_models,
    list_installed,
    list_categories,
    check_hardware,
    list_engines,
    download_model,
    generate_text,
    get_preferences,
    set_preference,
]


def build_default_registry() -> ToolRegistry:
    """Build and return a ToolRegistry with all v2.7.0 built-in tools."""
    reg = ToolRegistry()
    for t in ALL_TOOLS:
        reg.register(t)
    return reg


# ---------------------------------------------------------------------------
# v2.8.0 pluggable skill bundles
# ---------------------------------------------------------------------------
CORE_SKILL = Skill(
    id="core",
    name="核心助手",
    description="Agent 基础能力：读取/记住用户偏好、调用本地对话模型生成文本。必备，不可关闭。",
    icon="⚙️",
    category="core",
    required=True,
    default_enabled=True,
    tools=[get_preferences, set_preference, generate_text],
    guidance=(
        "用户表达反复出现的偏好时用 set_preference 记住，回答前可用 get_preferences 查看；"
        "需要本地对话模型进行开放式文本生成时用 generate_text（需先在 MNN 引擎页加载模型）。"
    ),
)

CATALOG_SKILL = Skill(
    id="model_catalog",
    name="模型市场检索",
    description="搜索模型、查看详情、按硬件推荐、列出已安装模型与类别。",
    icon="🗂️",
    category="model",
    default_enabled=True,
    tools=[search_models, model_info, recommend_models, list_installed, list_categories],
    guidance=(
        "涉及找模型时：不确定存在就先 search_models，再用 model_info 看详情；"
        "推荐前应先（用 local_system 技能的 check_hardware）了解显存/内存；"
        "list_installed 查看本地已装模型，list_categories 查看全部类别。"
    ),
)

SYSTEM_SKILL = Skill(
    id="local_system",
    name="本机环境与下载",
    description="检测硬件、列出推理引擎安装状态、给出模型下载计划。",
    icon="🖥️",
    category="model",
    default_enabled=True,
    tools=[check_hardware, list_engines, download_model],
    guidance=(
        "推荐大模型或判断能否运行前必须先 check_hardware；"
        "list_engines 检查所需引擎是否安装；download_model 只返回下载计划，"
        "实际下载由界面下载队列执行，不要假装已开始下载。"
    ),
)

DRAMA_SKILL = Skill(
    id="drama_studio",
    name="短剧创作工坊",
    description="专业短剧/微电影流水线：编剧方法论、头脑风暴、结构化剧本、分镜表、多模态渲染计划。",
    icon="🎬",
    category="creation",
    default_enabled=True,
    tools=DRAMA_TOOLS,
    guidance=(
        "用户要做短剧/微电影/剧情短片时按顺序：先 drama_storycraft 选基调"
        "（micro_film 微电影三幕式 / hook_drama 短视频钩子驱动）与风格锚点；"
        "再 drama_brainstorm → drama_compose_script → drama_storyboard → drama_render_plan。"
        "剧本须含场景登记清单与情绪节拍，风格锚点要具体到导演/动画流派。"
    ),
)

WRITING_SKILL = Skill(
    id="writing_studio",
    name="写作工坊",
    description="大纲、润色、摘要、翻译：离线可用的结构化写作辅助，加载对话 AI 后可一键扩写。",
    icon="✍️",
    category="creation",
    default_enabled=False,  # opt-in: 用户在技能库中选择添加
    tools=WRITING_TOOLS,
    guidance=(
        "写作类需求：writing_outline 出大纲、writing_polish 润色改写、writing_summary 摘要、"
        "writing_translate 翻译；未加载 LLM 时它们返回确定性骨架/抽取结果与提示词，"
        "用户需要成品文本时置 use_llm=true。"
    ),
)

MEDIA_PROMPT_SKILL = Skill(
    id="media_prompt_studio",
    name="多模态提示词工坊",
    description="为图像/视频/音乐生成编译提示词包：正向提示词 + 负面提示词 + 主题一致性约束（JSON）。",
    icon="🎨",
    category="creation",
    default_enabled=False,  # opt-in
    tools=MEDIA_PROMPT_TOOLS,
    guidance=(
        "生成图/视频/音乐前，用 build_image_prompt / build_video_prompt / build_music_prompt "
        "产出包含 positive、negative、theme_constraints 的完整提示词包；务必保留负面提示词与"
        "一致性约束以防止变脸、闪烁、乱码文字与风格漂移。"
    ),
)

# Registration order = display order in the skill library UI.
BUILTIN_SKILLS: list[Skill] = [
    CORE_SKILL,
    CATALOG_SKILL,
    SYSTEM_SKILL,
    DRAMA_SKILL,
    WRITING_SKILL,
    MEDIA_PROMPT_SKILL,
]


def build_skill_manager(
    state_path: str | Path | None = None,
    extra_skills: list[Skill] | None = None,
) -> SkillManager:
    """Build the SkillManager over all built-in skills.

    ``state_path`` is the JSON file that persists which non-required skills are
    disabled. Pass ``None`` for an in-memory manager (tests / ephemeral use).

    ``extra_skills`` (v2.9.0) are skills imported through the skill hub. They are
    deliberately kept **outside** :data:`BUILTIN_SKILLS`: the built-in set is a
    frozen contract (see ``tests/test_v280_skills.py``) and user imports must
    never mutate it. ``None`` reproduces the previous behaviour exactly.
    """
    skills = list(BUILTIN_SKILLS)
    if extra_skills:
        skills.extend(extra_skills)
    return SkillManager(skills, state_path=state_path)


def all_skill_tools() -> list[Any]:
    """Every tool exposed by any built-in skill (diagnostics/tests)."""
    tools: list[Any] = []
    for sk in BUILTIN_SKILLS:
        tools.extend(sk.tools)
    return tools


__all__ = [
    "ALL_TOOLS",
    "BUILTIN_SKILLS",
    "build_default_registry",
    "build_skill_manager",
    "all_skill_tools",
    "search_models",
    "model_info",
    "recommend_models",
    "list_installed",
    "list_categories",
    "check_hardware",
    "list_engines",
    "download_model",
    "generate_text",
    "get_preferences",
    "set_preference",
]
