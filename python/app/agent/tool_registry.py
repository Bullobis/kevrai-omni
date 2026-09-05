"""Tool registry — pluggable tool system for the agent.

Inspired by OpenClaw's pluggable Skills, but simpler: each tool is a
callable with a name, description, JSON-schema parameters, and an execution
function that receives a ToolContext (giving access to catalog, hardware,
memory, etc.). Tools are registered in a central registry and dispatched by
the agent's ReAct loop.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolContext:
    """Context passed to every tool execution.

    Gives tools access to the sidecar's subsystems without hard-coding
    imports inside each tool.
    """
    catalog: Any = None
    engines_catalog: Any = None
    hardware_info: dict[str, Any] = field(default_factory=dict)
    memory: Any = None
    settings: Any = None
    models_dir: Any = None
    app_root: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Tool:
    """A single agent tool."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema subset
    handler: Callable[[dict[str, Any], ToolContext], dict[str, Any]]
    category: str = "general"

    def to_spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "category": self.category,
        }

    def execute(self, params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        try:
            result = self.handler(params, ctx)
            if not isinstance(result, dict):
                result = {"result": result}
            return {"ok": True, **result}
        except Exception as e:
            return {"ok": False, "error": str(e), "error_type": type(e).__name__}


class ToolRegistry:
    """Central registry of agent tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", tool.name):
            raise ValueError(f"invalid tool name: {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[dict[str, Any]]:
        return [t.to_spec() for t in self._tools.values()]

    def list_names(self) -> list[str]:
        return sorted(self._tools.keys())

    def execute(self, name: str, params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            return {"ok": False, "error": f"unknown tool: {name}", "error_type": "UnknownTool"}
        return tool.execute(params, ctx)

    def build_tool_prompt_block(self) -> str:
        """Render a human-readable block of all tools for the LLM system prompt."""
        lines = []
        for name in sorted(self._tools):
            t = self._tools[name]
            params_desc = []
            props = (t.parameters or {}).get("properties", {}) or {}
            required = set((t.parameters or {}).get("required", []) or [])
            for pname, pspec in props.items():
                ptype = pspec.get("type", "any")
                pdesc = pspec.get("description", "")
                req = " (required)" if pname in required else " (optional)"
                params_desc.append(f"    - {pname}: {ptype}{req} — {pdesc}")
            lines.append(f"  {name}: {t.description}")
            if params_desc:
                lines.extend(params_desc)
        return "\n".join(lines)


def parse_tool_call(text: str) -> tuple[str, dict[str, Any]] | None:
    """Parse a tool call from LLM output.

    Supports two formats:
    1. ``Action: tool_name|{"key": "value"}``
    2. ``Action: tool_name(param1=value1, param2=value2)``

    Returns (tool_name, params_dict) or None if no tool call found.
    """
    # Format 1: name|json
    m = re.search(
        r"Action\s*:\s*([a-z][a-z0-9_]{1,63})\s*\|\s*(\{.*?\})\s*(?:\n|$)",
        text, re.S | re.I,
    )
    if m:
        name = m.group(1).lower()
        try:
            params = json.loads(m.group(2))
            if isinstance(params, dict):
                return name, params
        except json.JSONDecodeError:
            pass

    # Format 2: name(key=value, ...)
    m = re.search(
        r"Action\s*:\s*([a-z][a-z0-9_]{1,63})\s*\(([^)]*)\)",
        text, re.I,
    )
    if m:
        name = m.group(1).lower()
        raw = m.group(2).strip()
        params: dict[str, Any] = {}
        if raw:
            for pair in re.split(r",(?=(?:[^\"']*[\"'][^\"']*[\"'])*[^\"']*$)", raw):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("\"'")
                    # try numeric / bool conversion
                    if v.lower() in ("true", "false"):
                        params[k] = v.lower() == "true"
                    else:
                        try:
                            params[k] = int(v)
                        except ValueError:
                            try:
                                params[k] = float(v)
                            except ValueError:
                                params[k] = v
        return name, params

    return None


def extract_final_answer(text: str) -> str:
    """Extract the final answer from LLM output.

    Looks for 'Final Answer:' marker; if not found, returns the text after
    the last 'Thought:' or the whole text.
    """
    m = re.search(r"Final\s*Answer\s*:\s*(.+?)(?:\n\s*\n|\Z)", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    # Fallback: text after last Thought:
    parts = re.split(r"Thought\s*:", text, flags=re.I)
    if len(parts) > 1:
        return parts[-1].strip()
    return text.strip()
