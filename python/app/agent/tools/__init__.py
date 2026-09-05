"""Agent tools package — registers all built-in tools."""
from __future__ import annotations

from ..tool_registry import ToolRegistry
from .catalog_tools import (
    search_models,
    model_info,
    recommend_models,
    list_installed,
    list_categories,
)
from .system_tools import (
    check_hardware,
    list_engines,
    download_model,
    generate_text,
    get_preferences,
    set_preference,
)

ALL_TOOLS = [
    search_models,
    model_info,
    recommend_models,
    list_installed,
    list_categories,
    check_hardware,
    list_engines,
    download_model,
    generate_text,
    get_preferences,
    set_preference,
]


def build_default_registry() -> ToolRegistry:
    """Build and return a ToolRegistry with all built-in tools registered."""
    reg = ToolRegistry()
    for t in ALL_TOOLS:
        reg.register(t)
    return reg


__all__ = [
    "ALL_TOOLS",
    "build_default_registry",
    "search_models",
    "model_info",
    "recommend_models",
    "list_installed",
    "list_categories",
    "check_hardware",
    "list_engines",
    "download_model",
    "generate_text",
    "get_preferences",
    "set_preference",
]
