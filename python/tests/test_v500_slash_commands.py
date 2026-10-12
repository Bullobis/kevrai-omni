"""Tests for Agent slash commands."""

from __future__ import annotations

from app.agent.slash_commands import (
    SLASH_COMMANDS,
    help_text,
    parse_slash,
    resolve_slash,
)


def test_parse_non_slash_returns_none():
    assert parse_slash("普通问题") is None
    assert parse_slash("") is None


def test_parse_bare_slash_is_help():
    assert parse_slash("/") == ("help", "")
    assert parse_slash("  /  ") == ("help", "")


def test_parse_command_and_arg():
    assert parse_slash("/search qwen") == ("search", "qwen")
    assert parse_slash("/recommend") == ("recommend", "")
    # command is lowercased
    assert parse_slash("/MUSIC happy song") == ("music", "happy song")


def test_resolve_actionable_without_arg():
    ok, text = resolve_slash("/recommend")
    assert ok is True
    assert text == SLASH_COMMANDS["recommend"][0]


def test_resolve_actionable_substitutes_arg():
    ok, text = resolve_slash("/search qwen 中文")
    assert ok is True
    assert text == "搜索模型 qwen 中文"


def test_resolve_command_requiring_arg_but_missing():
    ok, text = resolve_slash("/search")
    assert ok is False
    assert "需要一个参数" in text


def test_resolve_help():
    ok, text = resolve_slash("/help")
    assert ok is False
    assert text == help_text()


def test_resolve_unknown_command_shows_help():
    ok, text = resolve_slash("/nope")
    assert ok is False
    assert "未识别命令" in text
    assert "/recommend" in text


def test_help_lists_every_command():
    out = help_text()
    for cmd in SLASH_COMMANDS:
        assert f"/{cmd}" in out


def test_non_slash_passes_through():
    ok, text = resolve_slash("你好")
    assert ok is True
    assert text == "你好"
