"""Kevrai Omni Agent — general-purpose AI agent layer.

Inspired by OpenClaw's architecture (gateway + runtime separation, pluggable
tools, local persistent memory, model-agnostic routing, ReAct reasoning) but
customised for Kevrai Omni's local model-management context.

The agent runs inside the Python sidecar (FastAPI), uses a local LLM (MNN
runtime) as its reasoning brain when available, operates tools that wrap the
existing catalog / hardware / download / engine subsystems, and persists
conversation history and user preferences to a local SQLite database.
"""
from __future__ import annotations

from .agent import Agent, AgentSession, AgentStep, AgentResult
from .memory import AgentMemory
from .tool_registry import ToolRegistry, Tool, ToolContext
from .model_router import ModelRouter

__all__ = [
    "Agent",
    "AgentSession",
    "AgentStep",
    "AgentResult",
    "AgentMemory",
    "ToolRegistry",
    "Tool",
    "ToolContext",
    "ModelRouter",
]
