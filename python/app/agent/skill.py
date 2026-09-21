"""Pluggable skill system for the Kevrai Agent (v2.8.0).

A *skill* bundles a set of related :class:`Tool` objects with metadata and an
optional system-prompt ``guidance`` block. Skills can be enabled or disabled by
the user at runtime ("可以选择添加技能"); the active tool registry and the
agent's system prompt are rebuilt from the set of enabled skills.

Design goals:
- Backwards compatible with v2.7.0's flat :class:`ToolRegistry`: the manager
  can materialise a registry containing exactly the tools of enabled skills.
- Required/core skills can never be disabled.
- Enable/disable state persists as a small JSON file (``agent/skills.json``),
  independent of the SQLite conversation memory so it is trivial to inspect or
  reset. Unknown skill ids in the state file (e.g. after a downgrade) are
  ignored gracefully.
- Tool names must be globally unique across skills (a tool belongs to exactly
  one skill), enforced at construction time.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .tool_registry import Tool, ToolRegistry

log = logging.getLogger("kevrai.agent.skill")

_SKILL_ID_RE = re.compile(r"[a-z][a-z0-9_]{1,63}")


@dataclass
class Skill:
    """A bundle of tools plus metadata and optional prompt guidance."""

    id: str
    name: str
    description: str
    tools: list[Tool]
    version: str = "1.0.0"
    category: str = "general"          # core | model | creation | ...
    icon: str = "🧩"
    guidance: str = ""                 # injected into the LLM system prompt
    default_enabled: bool = True
    required: bool = False             # required skills cannot be disabled
    source: str = "builtin"            # builtin | imported (skill hub)
    path: str = ""                     # on-disk location for imported skills

    def __post_init__(self) -> None:
        if not _SKILL_ID_RE.fullmatch(self.id):
            raise ValueError(f"invalid skill id: {self.id!r}")
        # v2.9.0 — guidance-only skills are allowed. The Anthropic SKILL.md
        # format (which the skill hub imports) very often ships *no* executable
        # tooling at all: the markdown body alone is the guidance. What is
        # genuinely useless is a skill with neither tools nor guidance — it
        # would occupy a slot in the prompt and contribute nothing.
        if not self.tools and not self.guidance.strip():
            raise ValueError(
                f"skill {self.id!r} must provide at least one tool or non-empty guidance"
            )
        names = [t.name for t in self.tools]
        if len(names) != len(set(names)):
            raise ValueError(f"skill {self.id!r} has duplicate tool names: {names}")

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]

    def to_spec(self, enabled: bool) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "category": self.category,
            "icon": self.icon,
            "required": self.required,
            "default_enabled": self.default_enabled,
            "enabled": enabled,
            "tool_names": self.tool_names,
            "tool_count": len(self.tools),
            "has_guidance": bool(self.guidance),
            "source": self.source,
            "path": self.path,
        }


class SkillManager:
    """Owns the catalogue of skills and their enabled/disabled state."""

    def __init__(
        self,
        skills: list[Skill],
        state_path: str | Path | None = None,
    ) -> None:
        if not skills:
            raise ValueError("SkillManager requires at least one skill")
        self._skills: dict[str, Skill] = {}
        seen_tools: dict[str, str] = {}
        for sk in skills:
            if sk.id in self._skills:
                raise ValueError(f"duplicate skill id: {sk.id!r}")
            for t in sk.tools:
                if t.name in seen_tools:
                    raise ValueError(
                        f"tool {t.name!r} claimed by both skill "
                        f"{seen_tools[t.name]!r} and {sk.id!r}"
                    )
                seen_tools[t.name] = sk.id
            self._skills[sk.id] = sk

        self.state_path = Path(state_path) if state_path else None
        # Explicitly disabled skill ids (required skills are never stored here).
        self._disabled: set[str] = set()
        self._load_state()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_state(self) -> None:
        if not self.state_path or not self.state_path.exists():
            # Honour default_enabled when no state file exists yet.
            self._disabled = {
                sid for sid, sk in self._skills.items()
                if not sk.required and not sk.default_enabled
            }
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            disabled = data.get("disabled", []) if isinstance(data, dict) else []
            valid = {
                sid for sid in disabled
                if sid in self._skills and not self._skills[sid].required
            }
            self._disabled = set(valid)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("skill state unreadable, resetting: %s", e)
            self._disabled = {
                sid for sid, sk in self._skills.items()
                if not sk.required and not sk.default_enabled
            }

    def _save_state(self) -> None:
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"disabled": sorted(self._disabled)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.state_path)
        except OSError as e:
            log.warning("failed to persist skill state: %s", e)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get(self, skill_id: str) -> Skill:
        if skill_id not in self._skills:
            raise KeyError(f"unknown skill: {skill_id!r}")
        return self._skills[skill_id]

    def is_enabled(self, skill_id: str) -> bool:
        sk = self.get(skill_id)
        if sk.required:
            return True
        return skill_id not in self._disabled

    def list_skills(self) -> list[dict[str, Any]]:
        """Public specs ordered: required/core first, then category, then id."""
        def _key(item: tuple[str, Skill]) -> tuple[int, str, str]:
            sid, sk = item
            return (0 if sk.required else 1, sk.category, sid)
        out = []
        for sid, sk in sorted(self._skills.items(), key=_key):
            out.append(sk.to_spec(self.is_enabled(sid)))
        return out

    def active_skills(self) -> list[Skill]:
        return [sk for sid, sk in self._skills.items() if self.is_enabled(sid)]

    def disabled_ids(self) -> list[str]:
        return sorted(self._disabled)

    def tool_to_skill(self) -> dict[str, str]:
        return {t.name: sid for sid, sk in self._skills.items() for t in sk.tools}

    # ------------------------------------------------------------------
    # Enable / disable
    # ------------------------------------------------------------------
    def set_enabled(self, skill_id: str, enabled: bool) -> dict[str, Any]:
        sk = self.get(skill_id)
        if sk.required and not enabled:
            raise ValueError(f"skill {skill_id!r} is required and cannot be disabled")
        if enabled:
            self._disabled.discard(skill_id)
        else:
            self._disabled.add(skill_id)
        self._save_state()
        return sk.to_spec(self.is_enabled(skill_id))

    def enable(self, skill_id: str) -> dict[str, Any]:
        return self.set_enabled(skill_id, True)

    def disable(self, skill_id: str) -> dict[str, Any]:
        return self.set_enabled(skill_id, False)

    def toggle(self, skill_id: str) -> dict[str, Any]:
        return self.set_enabled(skill_id, not self.is_enabled(skill_id))

    def reset(self) -> None:
        """Restore default_enabled for every non-required skill and persist."""
        self._disabled = {
            sid for sid, sk in self._skills.items()
            if not sk.required and not sk.default_enabled
        }
        self._save_state()

    # ------------------------------------------------------------------
    # Materialise registry / prompt for the active skill set
    # ------------------------------------------------------------------
    def active_tools(self) -> list[Tool]:
        tools: list[Tool] = []
        for sk in self.active_skills():
            tools.extend(sk.tools)
        return tools

    def build_registry(self) -> ToolRegistry:
        """Build a fresh ToolRegistry containing only enabled skills' tools."""
        reg = ToolRegistry()
        for tool in self.active_tools():
            reg.register(tool)
        return reg

    def build_guidance_block(self) -> str:
        """Render enabled skills' guidance for the LLM system prompt."""
        blocks = []
        for sk in self.active_skills():
            if sk.guidance.strip():
                blocks.append(f"### 技能：{sk.name}\n{sk.guidance.strip()}")
        return "\n\n".join(blocks)

    def active_tool_names(self) -> list[str]:
        return sorted(t.name for t in self.active_tools())
