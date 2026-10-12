"""Agent persona presets.

A persona adds a short role/guidance block to the reasoning brain's system
prompt. It only affects answers produced by a reasoning model (rule-based mode
is deterministic and ignores it). The selected persona persists as a small JSON
state file, mirroring the brain selector.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# id -> (label, guidance injected into the system prompt)
PERSONAS: dict[str, dict[str, str]] = {
    "general": {
        "label": "通用助手",
        "guidance": "你是通用助手，回答清晰、准确、有条理。",
    },
    "coder": {
        "label": "代码助手",
        "guidance": (
            "你是资深软件工程师。回答以代码为核心，给出可直接运行的实现，"
            "标注关键步骤与潜在边界情况；代码块注明语言。"
        ),
    },
    "writer": {
        "label": "写作助手",
        "guidance": (
            "你是专业写作助手。注重结构、措辞与可读性，按文体调整语气，"
            "必要时先给提纲再给正文。"
        ),
    },
    "translator": {
        "label": "翻译润色",
        "guidance": (
            "你是翻译与文字润色专家。译文忠实、通顺、符合目标语言习惯；"
            "润色时保留原意并说明主要改动。"
        ),
    },
    "analyst": {
        "label": "数据分析",
        "guidance": (
            "你是数据分析师。结论基于给定数据，区分事实与推断，"
            "必要时给出计算过程与简明的表格或步骤。"
        ),
    },
    "creative": {
        "label": "创意伙伴",
        "guidance": (
            "你是富有想象力的创意伙伴。提供新颖、具体、可落地的点子，"
            "并乐于一次给出多个不同方向。"
        ),
    },
}

DEFAULT_PERSONA = "general"


def list_personas() -> list[dict[str, str]]:
    return [{"id": k, "label": v["label"], "guidance": v["guidance"]}
            for k, v in PERSONAS.items()]


def guidance_for(persona_id: str) -> str:
    p = PERSONAS.get(persona_id) or PERSONAS[DEFAULT_PERSONA]
    return p["guidance"]


class PersonaStore:
    """Loads / persists the selected persona id as a small JSON file."""

    def __init__(self, state_path: Path | str | None = None) -> None:
        self._state_path = Path(state_path) if state_path else None
        self._persona = self._load()

    def _load(self) -> str:
        if self._state_path and self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                pid = str(data.get("persona") or DEFAULT_PERSONA)
                if pid in PERSONAS:
                    return pid
            except Exception as e:  # fall back to default
                import logging
                logging.getLogger(__name__).warning("persona load failed: %s", e)
        return DEFAULT_PERSONA

    @property
    def persona(self) -> str:
        return self._persona

    def set(self, persona_id: str) -> str:
        if persona_id not in PERSONAS:
            raise ValueError(f"unknown persona: {persona_id!r}")
        self._persona = persona_id
        if self._state_path:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"persona": persona_id}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return self._persona


def build_persona_block(persona_id: str) -> str:
    """Render the persona block injected into the agent system prompt."""
    p = PERSONAS.get(persona_id) or PERSONAS[DEFAULT_PERSONA]
    return f"当前角色：{p['label']}。{p['guidance']}"


def export_persona(store: Any) -> dict[str, str]:
    pid = store.persona if hasattr(store, "persona") else DEFAULT_PERSONA
    return {"persona": pid}
