"""Model router — selects and invokes the reasoning LLM for the agent.

Inspired by OpenClaw's model-agnostic design (switch models via config, route
simple queries to cheap models). For Kevrai Omni, the agent prefers the
locally-loaded MNN model (same runtime used by the drama agent). If no LLM is
loaded, the router returns a structured "not ready" status and the agent falls
back to a deterministic rule-based mode for simple tool-only queries.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("kevrai.agent")


class ModelRouter:
    """Routes agent reasoning to an available local LLM."""

    def __init__(self) -> None:
        self._mnn_available: bool | None = None
        self._last_model_name: str = ""

    def _check_mnn(self) -> tuple[bool, str]:
        """Check if MNN runtime is available and a model is loaded."""
        try:
            from .. import mnn_runtime
            status = mnn_runtime.status()
            loaded = bool(status.get("loaded"))
            name = str(status.get("model_name") or "")
            self._last_model_name = name
            return loaded, name
        except Exception as e:
            log.debug("MNN runtime check failed: %s", e)
            return False, ""

    def is_ready(self) -> tuple[bool, str]:
        """Return (ready, model_name). Caches briefly to avoid repeated checks."""
        ready, name = self._check_mnn()
        return ready, name

    def chat(
        self,
        prompt: str,
        system: str = "",
        max_new_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Invoke the reasoning LLM.

        Returns {"ok": True, "text": ...} on success, or
        {"ok": False, "error": ..., "error_type": "LlmNotReady"} when no
        model is loaded.
        """
        ready, name = self.is_ready()
        if not ready:
            return {
                "ok": False,
                "error": (
                    "对话 AI 未就绪：请先在「MNN 引擎」页加载一个对话模型 "
                    "（或在 AI 引擎页安装 MNN 引擎，再到模型市场下载 LLM）。"
                    "在未加载模型时，Agent 仍可执行搜索/推荐/硬件查询等工具操作。"
                ),
                "error_type": "LlmNotReady",
                "model_name": name,
            }
        try:
            from .. import mnn_runtime
            full = f"{system}\n\n{prompt}" if system else prompt
            res = mnn_runtime.chat(full, max_new_tokens=max_new_tokens)
            text = str(res.get("text") or "").strip()
            if not text:
                return {"ok": False, "error": "LLM returned empty text", "error_type": "EmptyOutput"}
            return {"ok": True, "text": text, "model_name": name}
        except Exception as e:
            log.exception("agent LLM call failed")
            return {"ok": False, "error": str(e), "error_type": type(e).__name__}
