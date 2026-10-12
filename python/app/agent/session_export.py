"""Render a single Agent conversation as Markdown."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

ROLE_HEADINGS = {
    "user": "🧑 用户",
    "assistant": "🤖 助手",
    "system": "⚙️ 系统",
}


def _role_heading(role: str) -> str:
    return ROLE_HEADINGS.get(role, f"💬 {role or 'unknown'}")


def render_session_markdown(
    title: str,
    messages: list[dict[str, Any]],
    *,
    created_at: float | None = None,
) -> str:
    """Build a Markdown transcript.

    Tool calls are rendered as collapsible details with their (JSON) params;
    tool results are not stored separately, so only the assistant content and
    recorded tool invocations are included.
    """
    import datetime as _dt

    lines: list[str] = [f"# {title or '未命名会话'}", ""]
    if created_at:
        when = _dt.datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M")
        lines += [f"_导出时间：{when}_", ""]
    for m in messages:
        role = m.get("role") or ""
        lines += [f"## {_role_heading(role)}", ""]
        tool = m.get("tool_name") or ""
        params = m.get("tool_params") or ""
        content = m.get("content") or ""
        if content:
            lines += [content, ""]
        if tool:
            lines += ["<details>", f"<summary>🛠️ 工具：{tool}</summary>", ""]
            if params:
                lines += ["```json", params, "```", ""]
            lines += ["</details>", ""]
    return "\n".join(lines).rstrip() + "\n"


def export_session(
    memory: Any,
    session_id: str,
    *,
    get_session: Callable[[str], dict[str, Any] | None] | None = None,
    get_messages: Callable[..., list[dict[str, Any]]] | None = None,
) -> str:
    """Load a session from memory and render it as Markdown.

    Raises KeyError if the session does not exist.
    """
    getter = get_session or (lambda sid: memory.get_session(sid))
    msgs_getter = get_messages or (lambda **kw: memory.get_messages(session_id, **kw))
    session = getter(session_id)
    if not session:
        raise KeyError(session_id)
    messages = msgs_getter(limit=10000)
    return render_session_markdown(
        session.get("title") or session_id,
        messages,
        created_at=session.get("created_at"),
    )
