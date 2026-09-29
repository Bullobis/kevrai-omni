"""Regression tests for ToolRegistry duplicate-registration warning.

Guards against the historical behaviour where `register()` silently overwrote
an existing tool with the same name, giving no signal that the previous
implementation was replaced.
"""

from __future__ import annotations

import warnings

import pytest

from app.agent.tool_registry import Tool, ToolRegistry


def _make_tool(name: str, desc: str = "test tool") -> Tool:
    return Tool(
        name=name,
        description=desc,
        parameters={"type": "object", "properties": {}},
        handler=lambda params, ctx: {"ok": True, "name": name},
    )


class TestRegisterDuplicateWarning:
    def test_first_register_no_warning(self):
        reg = ToolRegistry()
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning → exception
            reg.register(_make_tool("alpha"))
        assert reg.get("alpha") is not None

    def test_duplicate_register_emits_warning(self):
        reg = ToolRegistry()
        reg.register(_make_tool("alpha", desc="first"))
        with pytest.warns(UserWarning, match="overwriting existing tool.*alpha"):
            reg.register(_make_tool("alpha", desc="second"))

    def test_duplicate_overwrite_takes_effect(self):
        reg = ToolRegistry()
        first = _make_tool("alpha", desc="first")
        second = _make_tool("alpha", desc="second")
        reg.register(first)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            reg.register(second)
        assert reg.get("alpha") is second
        assert reg.get("alpha").description == "second"

    def test_distinct_names_no_warning(self):
        reg = ToolRegistry()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            reg.register(_make_tool("alpha"))
            reg.register(_make_tool("beta"))
            reg.register(_make_tool("gamma"))
        assert reg.list_names() == ["alpha", "beta", "gamma"]

    def test_warning_contains_tool_name(self):
        reg = ToolRegistry()
        reg.register(_make_tool("my_tool"))
        with pytest.warns(UserWarning) as record:
            reg.register(_make_tool("my_tool"))
        assert len(record) >= 1
        assert "my_tool" in str(record[0].message)

    def test_invalid_name_still_raises_before_warning(self):
        reg = ToolRegistry()
        with pytest.raises(ValueError, match="invalid tool name"):
            reg.register(_make_tool("Invalid-Name!"))
        # Nothing registered.
        assert reg.list_names() == []
