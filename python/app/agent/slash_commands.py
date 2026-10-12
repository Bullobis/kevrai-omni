"""Slash commands for the Agent composer.

Typing ``/`` in the Agent input opens a menu of short commands. Each command
maps to a *canonical natural-language query* that the existing rule-based
fallback already handles — so slash commands reuse the well-tested tool paths
instead of duplicating tool logic.

This module is deliberately side-effect free and easy to unit test.
"""

from __future__ import annotations

# command -> (template, human description). "{arg}" is replaced with the text
# the user typed after the command.
SLASH_COMMANDS: dict[str, tuple[str, str]] = {
    "help": ("", "列出所有斜杠命令"),
    "search": ("搜索模型 {arg}", "按关键词搜索模型"),
    "recommend": ("推荐一个适合我硬件的模型", "按用途/硬件推荐模型"),
    "hardware": ("我能跑什么模型", "硬件适配检测与建议"),
    "install": ("帮我安装模型 {arg}", "安装指定模型"),
    "engines": ("怎么安装推理引擎", "安装/管理推理引擎"),
    "mirror": ("怎么换国内镜像", "切换国内下载镜像"),
    "drama": ("帮我写一个短剧剧本", "短剧创作工坊"),
    "prompt": ("帮我写图像提示词 {arg}", "提示词工坊"),
    "image": ("生成图片 {arg}", "文生图"),
    "vision": ("帮我理解图片 {arg}", "图片理解（视觉问答）"),
    "music": ("生成一段背景音乐 {arg}", "生成音乐"),
    "transcribe": ("语音转文字", "语音识别（ASR）"),
    "separate": ("人声和伴奏分离", "音源分离"),
    "tts": ("把文字转成语音 {arg}", "语音合成（TTS）"),
    "translate": ("翻译 {arg}", "双栏翻译"),
}

HELP_COMMAND = "help"


def parse_slash(message: str) -> tuple[str, str] | None:
    """Split a leading "/command arg ..." into (command, arg).

    Returns None when the message is not a slash command.
    """
    text = (message or "").strip()
    if not text.startswith("/"):
        return None
    body = text[1:].strip()
    if not body:
        return (HELP_COMMAND, "")
    head, _, arg = body.partition(" ")
    return (head.strip().lower(), arg.strip())


def help_text() -> str:
    """Render the canonical help listing (used by /help and unknown commands)."""
    lines = ["可用斜杠命令（在输入框输入 / 触发）："]
    for cmd, (_tpl, desc) in SLASH_COMMANDS.items():
        lines.append(f"  /{cmd:<11} {desc}")
    return "\n".join(lines)


def resolve_slash(message: str) -> tuple[bool, str]:
    """Resolve a slash command.

    Returns ``(handled, text)``:
    - for an actionable command: ``(True, canonical_query)``;
    - for /help or an unknown command: ``(False, help_answer)`` where the help
      answer is returned to the user directly.
    """
    parsed = parse_slash(message)
    if parsed is None:
        return (True, message or "")
    command, arg = parsed
    if command == HELP_COMMAND:
        return (False, help_text())
    entry = SLASH_COMMANDS.get(command)
    if entry is None:
        return (False, f"未识别命令：/{command}\n\n{help_text()}")
    template, _desc = entry
    if "{arg}" in template:
        resolved = template.replace("{arg}", arg).strip()
        # Commands that require an argument but got none: ask for one rather
        # than silently running an empty query.
        if not arg:
            return (
                False,
                f"/{command} 需要一个参数。\n用法：/{command} <内容>\n\n"
                f"{SLASH_COMMANDS[command][1]}。",
            )
    else:
        resolved = template.strip()
    return (True, resolved)
