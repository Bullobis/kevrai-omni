"""Coding-workspace tools.

All file operations go through :class:`WorkspaceRuntime`, which is a path
guard, not an OS sandbox. ``run_test`` uses a small command allowlist;
``run_command`` is the trusted-user shell mode and is always routed through
the Agent approval gate because the Tool is marked
``requires_confirmation=True``.
"""
from __future__ import annotations

from typing import Any

from ..tool_registry import Tool, ToolContext
from ..workspace import WorkspaceError, WorkspaceRuntime


def _workspace(ctx: ToolContext) -> WorkspaceRuntime:
    workspace = getattr(ctx, "workspace", None)
    if not isinstance(workspace, WorkspaceRuntime):
        raise WorkspaceError("workspace runtime is not configured")
    return workspace


def read_file(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return _workspace(ctx).read_file(
        str(params.get("path", "")),
        with_line_numbers=bool(params.get("with_line_numbers", False)),
    )


def list_dir(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return _workspace(ctx).list_dir(str(params.get("path") or "."))


def search_text(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return _workspace(ctx).search_text(
        str(params.get("query", "")),
        str(params.get("path") or "."),
    )


def write_file(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return _workspace(ctx).write_file(
        str(params.get("path", "")),
        str(params.get("content", "")),
    )


def edit_file(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return _workspace(ctx).edit_file(
        str(params.get("path", "")),
        str(params.get("old_string", "")),
        str(params.get("new_string", "")),
        replace_all=bool(params.get("replace_all", False)),
    )


def edit_lines(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return _workspace(ctx).edit_lines(
        str(params.get("path", "")),
        int(params.get("start_line", 0)),
        int(params["end_line"]) if params.get("end_line") is not None else None,
        str(params.get("new_text", "")),
        operation=str(params.get("operation") or "replace"),
        expected_text=params.get("expected_text"),
    )


def run_test(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return _workspace(ctx).run_test(
        str(params.get("command", "")),
        timeout_s=params.get("timeout_s"),
    )


def run_command(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return _workspace(ctx).run_command(
        str(params.get("command", "")),
        timeout_s=params.get("timeout_s"),
    )


CODING_WORKSPACE_TOOLS = [
    Tool(
        name="read_file",
        description=(
            "读取工作区内一个文本文件。只能使用相对路径；拒绝绝对路径、"
            "`..` 和 symlink 逃逸。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区的文件路径"},
                "with_line_numbers": {
                    "type": "boolean",
                    "description": "是否带行号返回；准备 edit_lines 时建议开启",
                },
            },
            "required": ["path"],
        },
        handler=read_file,
        category="coding",
        required_permission="read-only",
    ),
    Tool(
        name="list_dir",
        description="列出工作区内一个目录的直接子项（默认工作区根目录）。",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对工作区的目录路径，默认 .",
                },
            },
        },
        handler=list_dir,
        category="coding",
        required_permission="read-only",
    ),
    Tool(
        name="search_text",
        description=(
            "在工作区内递归搜索文本（不区分大小写），跳过 .git、node_modules、"
            "target 等目录；返回路径、行号和预览。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要搜索的字面文本"},
                "path": {
                    "type": "string",
                    "description": "搜索起始目录，默认 .",
                },
            },
            "required": ["query"],
        },
        handler=search_text,
        category="coding",
        required_permission="read-only",
    ),
    Tool(
        name="write_file",
        description=(
            "在工作区内创建或覆盖一个文本文件。执行前必须经过用户确认；"
            "不能写工作区外路径。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区的文件路径"},
                "content": {"type": "string", "description": "完整文件内容"},
            },
            "required": ["path", "content"],
        },
        handler=write_file,
        category="coding",
        required_permission="workspace-write",
        requires_confirmation=True,
    ),
    Tool(
        name="edit_file",
        description=(
            "在工作区内替换文件中的一段文本，返回替换次数和 diff。"
            "执行前必须经过用户确认。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区的文件路径"},
                "old_string": {"type": "string", "description": "要替换的原文"},
                "new_string": {"type": "string", "description": "替换后的文本"},
                "replace_all": {
                    "type": "boolean",
                    "description": "是否替换所有匹配，默认只替换第一处",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
        handler=edit_file,
        category="coding",
        required_permission="workspace-write",
        requires_confirmation=True,
    ),
    Tool(
        name="edit_lines",
        description=(
            "按 1-based 行号编辑工作区文件，不要求重写整个文件。"
            "operation=replace 替换 start_line..end_line；delete 删除该范围；"
            "insert_before / insert_after 在锚点行前后插入 new_text。"
            "执行前必须经过用户确认。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区的文件路径"},
                "start_line": {"type": "integer", "description": "1-based 起始行"},
                "end_line": {
                    "type": "integer",
                    "description": "1-based 结束行（含）；replace/delete 使用",
                },
                "new_text": {
                    "type": "string",
                    "description": "替换/插入的文本；delete 时可为空",
                },
                "operation": {
                    "type": "string",
                    "enum": ["replace", "delete", "insert_before", "insert_after"],
                    "description": "行操作类型，默认 replace",
                },
                "expected_text": {
                    "type": "string",
                    "description": "可选：期望选中的原文，用于防止行号漂移改错",
                },
            },
            "required": ["path", "start_line", "new_text"],
        },
        handler=edit_lines,
        category="coding",
        required_permission="workspace-write",
        requires_confirmation=True,
    ),
    Tool(
        name="run_test",
        description=(
            "在测试运行器模式下执行白名单测试命令（pytest、npm test、"
            "cargo test、go test、ruff check、mypy 等）。不是沙箱；"
            "会执行项目代码。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "测试命令，例如 pytest -q 或 npm test",
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "超时秒数，默认 120，最大 300",
                },
            },
            "required": ["command"],
        },
        handler=run_test,
        category="coding",
        required_permission="workspace-write",
    ),
    Tool(
        name="run_command",
        description=(
            "在完整 Shell 模式下执行任意命令。每次执行前都会展示命令并等待"
            "用户确认；没有网络、进程或文件系统隔离。只建议在明确知情时使用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的完整命令",
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "超时秒数，默认 120，最大 300",
                },
            },
            "required": ["command"],
        },
        handler=run_command,
        category="coding",
        required_permission="danger-full-access",
        requires_confirmation=True,
    ),
]
