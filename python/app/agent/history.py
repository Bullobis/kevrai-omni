"""Agent 历史块的整理与裁剪。

从 Cherry Studio 学到的两条硬规则（读其 aiCore/context 源码得到）：

1. **历史必须以 user 开头**（``ensureValidHistory`` 的第三步）。
   Cherry 的原文：如果第一条非 system 消息不是 user，就插入一个合成 user
   占位。原因是 LLM API 要求对话以用户轮开始；一条孤立的 assistant 消息
   会让模型以为"自己刚才说过话但没被问"，从而接错上下文。
   本项目里 ``get_recent_messages(n)`` 是按条数取最后 N 条，N 的奇偶不同
   会让首条有时是 user 有时是 assistant —— 这是真实存在的问题，不是理论。

2. **裁剪要保留头尾，且必须留下可见标记**（``truncator.ts``）。
   Cherry 默认 ``headChars=0``、``tailChars=1000``：结尾保留得多，因为
   工具结果的**结论**通常在末尾。并且它一定写入
   ``--- truncated (N lines, M chars total) ---`` 这样的标记，绝不做
   无声截断。它还有一条防递归守卫：已经带标记的内容不再二次裁剪。

   本项目原来是 ``content[:500]`` —— 只留头、不留尾、不留任何标记。
   后果：模型拿到一段看起来完整、实际上被砍掉的内容，既看不到结论，
   也无从察觉被截断。这是"不是语法错误但很严重"的典型。
"""
from __future__ import annotations

from typing import Any

# 裁剪标记。刻意做成显眼的字符串，便于模型与用户都看得出来。
TRUNCATED_MARKER = "…[已截断 {omitted} 字]…"

# 默认值与 Cherry 的 truncator 对齐：结尾比开头保留得多。
DEFAULT_HEAD_CHARS = 200
DEFAULT_TAIL_CHARS = 500


def truncate_content(
    text: str,
    limit: int = 700,
    head_chars: int = DEFAULT_HEAD_CHARS,
    tail_chars: int = DEFAULT_TAIL_CHARS,
) -> str:
    """裁剪一段文本，保留头尾并留下可见标记。

    - 未超限 → 原样返回（不新增标记）。
    - 已超限 → ``head + 标记 + tail``。
    - 已经含标记 → 原样返回，避免二次裁剪把标记本身也吃掉（Cherry 的
      ``PERSISTED_OUTPUT_TAG`` 守卫，同一件事）。
    """
    if not text:
        return text
    if len(text) <= limit:
        return text
    # 防递归：已是裁剪产物就不再裁。
    if "…[已截断" in text:
        return text

    # 头尾预算加起来不能超过总上限，否则等于没裁。
    head = max(0, min(int(head_chars), limit))
    tail = max(0, min(int(tail_chars), limit - head))
    if head + tail >= len(text):
        return text

    omitted = len(text) - head - tail
    return (
        text[:head]
        + TRUNCATED_MARKER.format(omitted=omitted)
        + (text[len(text) - tail:] if tail else "")
    )


def group_into_turns(messages: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """把扁平消息切成"轮"。

    与 Cherry 的 ``groupIntoTurns`` 同思路，按 role 分：
    - ``assistant`` + 紧跟其后的所有 ``tool`` 消息 = 一整轮（原子，不可拆）
    - 其余（user / system / 无 tool 的 assistant）= 单条一轮

    返回 ``(start, end)`` 半开区间列表。
    """
    turns: list[tuple[int, int]] = []
    i = 0
    while i < len(messages):
        if messages[i].get("role") == "assistant":
            start = i
            i += 1
            while i < len(messages) and messages[i].get("role") == "tool":
                i += 1
            turns.append((start, i))
        else:
            turns.append((i, i + 1))
            i += 1
    return turns


def ensure_starts_with_user(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """丢弃开头的非 user 消息，保证第一条是 user。

    Cherry 的做法是**插入**一个合成占位 user；这里改成**丢弃开头的孤儿
    assistant**。两个选择都满足"必须以 user 开头"这条不变式，但丢弃更
    诚实：那半轮对话本来就不完整，编一句占位话反而给模型假信息。
    """
    for idx, m in enumerate(messages):
        if m.get("role") == "system":
            continue
        if m.get("role") == "user":
            return messages[idx:]
        # 首条非 system 是 assistant → 它是被截断出来的孤儿，连同它之后
        # 紧跟的 tool 结果一起丢掉。
        return messages[idx + 1:] if idx + 1 < len(messages) else []
    return messages


def drop_incomplete_trailing_turn(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """丢掉结尾未完成的半轮（只有 user、还没有 assistant 回答）。

    保留它的话模型会以为"用户在等我回答"而去续写，而不是理解这是历史。
    Cherry 用 ``preserveRecentMessages`` 的轮边界来保证这一点，这里等价。
    """
    if messages and messages[-1].get("role") == "user":
        return messages[:-1]
    return messages


def build_history_block(
    messages: list[dict[str, Any]],
    *,
    limit: int = 700,
    head_chars: int = DEFAULT_HEAD_CHARS,
    tail_chars: int = DEFAULT_TAIL_CHARS,
) -> str:
    """把消息列表渲染成给模型的文本历史块。

    依次做三件事：保证以 user 开头 → 丢掉结尾半轮 → 逐条带标记裁剪。
    """
    msgs = ensure_starts_with_user(list(messages or []))
    msgs = drop_incomplete_trailing_turn(msgs)

    lines: list[str] = []
    for m in msgs:
        role = m.get("role", "")
        content = truncate_content(
            str(m.get("content", "")), limit=limit,
            head_chars=head_chars, tail_chars=tail_chars,
        )
        if role == "user":
            lines.append(f"用户: {content}")
        elif role == "assistant":
            lines.append(f"助手: {content}")
        elif role == "tool":
            name = m.get("tool_name") or "tool"
            lines.append(f"工具({name}): {content}")
    return "\n".join(lines)
