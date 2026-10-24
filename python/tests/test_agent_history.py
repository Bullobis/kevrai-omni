"""app/agent/history.py 的行为测试。

每条都对应 Cherry Studio aiCore/context 源码里的一条不变式，或是本项目
原来 `content[:500]` 造成的真实缺陷。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.agent.history import (  # noqa: E402
    build_history_block,
    drop_incomplete_trailing_turn,
    ensure_starts_with_user,
    group_into_turns,
    truncate_content,
)

# ---------------------------------------------------------------------------
# truncate_content：保留头尾 + 显式标记
# ---------------------------------------------------------------------------

def test_short_text_untouched():
    assert truncate_content("你好", limit=700) == "你好"


def test_over_limit_keeps_tail():
    """结尾必须保留 —— 工具结果的结论通常在末尾（Cherry tailChars 默认
    比 headChars 大，正是这个理由）。"""
    text = "A" * 100 + "结论在这里"
    out = truncate_content(text, limit=50, head_chars=10, tail_chars=20)
    assert "结论在这里" in out, "结尾被丢掉了，模型看不到结论"


def test_over_limit_has_visible_marker():
    """不能无声截断 —— 必须留可见标记，否则模型以为内容是完整的。"""
    out = truncate_content("B" * 5000, limit=100, head_chars=20, tail_chars=20)
    assert "[已截断" in out
    assert "4960" in out, "标记里应写明省略了多少字"


def test_already_truncated_is_not_re_truncated():
    """防递归：已带标记的内容不再裁剪（Cherry 的 PERSISTED_OUTPUT_TAG 守卫）。"""
    once = truncate_content("C" * 5000, limit=100, head_chars=20, tail_chars=20)
    twice = truncate_content(once, limit=100, head_chars=20, tail_chars=20)
    assert once == twice, "二次裁剪会把标记本身也吃掉，形成标记套标记"


def test_empty_text_is_safe():
    assert truncate_content("", limit=10) == ""


def test_head_tail_budget_never_exceeds_limit():
    """头尾预算加起来不能超过 limit，否则等于没裁。"""
    out = truncate_content("D" * 1000, limit=100, head_chars=400, tail_chars=400)
    assert len(out) < 1000, "头尾预算失控，裁剪失效"


# ---------------------------------------------------------------------------
# ensure_starts_with_user：历史必须以 user 开头
# ---------------------------------------------------------------------------

def test_leading_assistant_is_dropped():
    """真实场景：取最后 N 条时首条可能是 assistant（N 奇偶不同）。
    孤立的 assistant 会让模型以为自己没被问就先说了话。"""
    msgs = [
        {"role": "assistant", "content": "孤儿回答"},
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "答案"},
    ]
    out = ensure_starts_with_user(msgs)
    assert out[0]["role"] == "user"
    assert "孤儿回答" not in " ".join(m["content"] for m in out)


def test_already_starting_with_user_is_unchanged():
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    assert ensure_starts_with_user(msgs) == msgs


def test_system_prefix_is_skipped():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
    ]
    out = ensure_starts_with_user(msgs)
    assert out[0]["role"] == "user"


def test_all_system_returns_as_is():
    msgs = [{"role": "system", "content": "s"}]
    assert ensure_starts_with_user(msgs) == msgs


# ---------------------------------------------------------------------------
# drop_incomplete_trailing_turn：结尾不留半轮
# ---------------------------------------------------------------------------

def test_trailing_user_without_answer_is_dropped():
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "未回答的问题"},
    ]
    out = drop_incomplete_trailing_turn(msgs)
    assert len(out) == 2, "结尾的半轮会让模型去续写，而不是当作历史"


def test_complete_history_unchanged():
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    assert drop_incomplete_trailing_turn(msgs) == msgs


# ---------------------------------------------------------------------------
# group_into_turns：assistant + 其 tool 结果是原子单元
# ---------------------------------------------------------------------------

def test_assistant_plus_tool_results_is_one_turn():
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "调工具"},
        {"role": "tool", "content": "r1"},
        {"role": "tool", "content": "r2"},
        {"role": "assistant", "content": "总结"},
    ]
    turns = group_into_turns(msgs)
    # user / (assistant+2 tool) / assistant
    assert turns == [(0, 1), (1, 4), (4, 5)]


def test_turns_cover_all_messages():
    msgs = [
        {"role": "user", "content": "1"},
        {"role": "assistant", "content": "2"},
        {"role": "tool", "content": "3"},
    ]
    turns = group_into_turns(msgs)
    assert turns[0][0] == 0
    assert turns[-1][1] == len(msgs), "轮划分必须覆盖全部消息，不能漏"


# ---------------------------------------------------------------------------
# build_history_block 端到端
# ---------------------------------------------------------------------------

def test_block_starts_with_user_even_when_given_orphan():
    msgs = [
        {"role": "assistant", "content": "孤儿"},
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "答案"},
    ]
    out = build_history_block(msgs)
    assert out.startswith("用户:"), out


def test_block_never_ends_with_unanswered_user():
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]
    out = build_history_block(msgs)
    assert "q2" not in out


def test_block_truncates_with_marker_not_silently():
    long = "X" * 3000
    out = build_history_block(
        [{"role": "user", "content": "短"}, {"role": "assistant", "content": long}]
    )
    assert "[已截断" in out, "长内容必须留标记，不能无声砍掉"
    assert "短" in out


def test_block_empty_input():
    assert build_history_block([]) == ""
    assert build_history_block(None) == ""


def test_block_includes_tool_turns():
    msgs = [
        {"role": "user", "content": "查一下"},
        {"role": "assistant", "content": "好"},
        {"role": "tool", "content": "结果", "tool_name": "web_search"},
        {"role": "assistant", "content": "找到了"},
    ]
    out = build_history_block(msgs)
    assert "工具(web_search): 结果" in out
