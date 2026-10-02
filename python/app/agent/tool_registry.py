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
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# The C json scanner recurses once per nesting level, so a deeply nested
# payload ("{\"a\":" * 5000) raises RecursionError instead of
# JSONDecodeError. Python's default limit is 1000, which a legitimate model
# response can exceed, so we parse deep payloads under a temporarily raised
# limit and restore it in a finally block. The ceiling keeps a pathological
# payload from exhausting the C stack (8 MB default) — see _MAX_JSON_DEPTH.
_MAX_JSON_DEPTH = 4000
_MAX_RECURSION_LIMIT = 8000


def _loads_deep(payload: str) -> Any:
    """``json.loads`` that tolerates nesting deeper than the recursion limit.

    Raises the interpreter limit just for this call, bounded by the payload's
    own bracket depth so a pathological input cannot drive the limit to the
    maximum. Depth beyond ``_MAX_JSON_DEPTH`` still raises RecursionError,
    which callers must handle — this widens the accepted range, it does not
    remove the bound.
    """
    limit = sys.getrecursionlimit()
    if limit >= _MAX_RECURSION_LIMIT:
        return json.loads(payload)
    # +200 covers json's own frames on top of the nesting depth.
    needed = min(payload.count("{") + payload.count("[") + 200, _MAX_RECURSION_LIMIT)
    if needed <= limit:
        return json.loads(payload)
    sys.setrecursionlimit(needed)
    try:
        return json.loads(payload)
    finally:
        sys.setrecursionlimit(limit)


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
        if tool.name in self._tools:
            warnings.warn(
                f"ToolRegistry: overwriting existing tool {tool.name!r}",
                UserWarning,
                stacklevel=2,
            )
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


def _iter_balanced_prefixes(rest: str):
    """Yield ``rest`` prefixes that end where its brackets balance.

    Scanning is string-aware so braces inside JSON string values are not
    counted. Prefixes are produced outermost-first (a closing brace at depth
    0), so the first one that parses as a dict is the whole object.
    """
    depth = 0
    in_str = False
    escaped = False
    for i, ch in enumerate(rest):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                yield rest[: i + 1]
            elif depth < 0:
                # Closing bracket before any opener — nothing left to try.
                return


def parse_tool_call(text: str) -> tuple[str, dict[str, Any]] | None:
    """Parse a tool call from LLM output.

    Supports two formats:
    1. ``Action: tool_name|{"key": "value"}``
    2. ``Action: tool_name(param1=value1, param2=value2)``

    Returns (tool_name, params_dict) or None if no tool call found.
    """
    # Format 1: name|json
    m = re.search(
        r"Action\s*:\s*([a-z][a-z0-9_]{1,63})\s*\|\s*(\{.*)",
        text, re.S | re.I,
    )
    if m:
        name = m.group(1).lower()
        rest = m.group(2)
        # The JSON object may be followed by free-text commentary on the same
        # line (a small local LLM occasionally appends "# search for ...").
        # Stopping at the first '}' breaks nested objects, so we try each
        # prefix that ends where the brackets balance, outermost first, and
        # take the first that parses. Tracking depth ourselves (rather than
        # retrying json.loads at every '}') keeps this linear: the naive walk
        # re-parsed the whole prefix per '}', which is quadratic and cost
        # seconds on the deeply nested payloads test_fuzz.py throws at it.
        for candidate in _iter_balanced_prefixes(rest):
            try:
                params = _loads_deep(candidate)
            except json.JSONDecodeError:
                continue
            except RecursionError:
                # Nested past the depth ceiling: a malformed tool call must
                # never take down the caller, so fall through to the next
                # supported format.
                break
            if isinstance(params, dict):
                return name, params
            break

    # Format 2: name(key=value, ...)
    m = re.search(
        r"Action\s*:\s*([a-z][a-z0-9_]{1,63})\s*\(([^)]*)\)",
        text, re.I,
    )
    if m:
        name = m.group(1).lower()
        raw = m.group(2).strip()
        # `params` is already annotated in the Format 1 branch above; reusing the
        # name without repeating the annotation keeps mypy from reporting a
        # `no-redef` in the same function scope.
        params = {}
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

    Looks for 'Final Answer:' marker; if not found, tries the Chinese markers
    the system prompt also tells the model to emit (最终答案 / 最终回答, with
    either full- or half-width colon). As a last resort returns the text after
    the last 'Thought:' or the whole text.
    """
    m = re.search(r"Final\s*Answer\s*:\s*(.+?)(?:\n\s*\n|\Z)", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    # Chinese markers (full-width or half-width colon).
    m = re.search(r"(?:最终答案|最终回答)\s*[:：]\s*(.+?)(?:\n\s*\n|\Z)", text, re.S)
    if m:
        return m.group(1).strip()
    # Fallback: text after last Thought:
    parts = re.split(r"Thought\s*:", text, flags=re.I)
    if len(parts) > 1:
        return parts[-1].strip()
    return text.strip()
