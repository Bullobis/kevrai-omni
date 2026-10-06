"""Model router — selects and invokes the reasoning LLM for the agent.

The agent is model-agnostic: the reasoning "brain" can be

* a locally-loaded MNN model (default, same runtime as the drama agent), or
* a small HuggingFace causal LM run through Transformers on CPU
  (``llm_runtime``), which needs no separate server.

If no brain is configured/available, :meth:`is_ready` reports not-ready and the
agent falls back to its deterministic rule-based mode for simple tool queries.

The chosen brain is persisted as a small JSON state file so it survives sidecar
restarts.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("kevrai.agent")


class ModelRouter:
    """Routes agent reasoning to a configured local LLM backend."""

    def __init__(self, state_path: str | Path | None = None) -> None:
        self._state_path = Path(state_path) if state_path else None
        # brain spec: None (auto → MNN) or ("mnn", None) /
        # ("transformers", "owner/repo").
        self._backend: tuple[str, str | None] = self._load_state()
        self._last_model_name: str = ""

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------
    def _load_state(self) -> tuple[str, str | None]:
        if not self._state_path or not self._state_path.exists():
            return (None, None)
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            kind = str(data.get("type") or "").strip()
            repo = data.get("repo")
            if kind == "transformers" and isinstance(repo, str) and "/" in repo:
                return ("transformers", repo)
            if kind == "mnn":
                return ("mnn", None)
        except Exception as e:  # noqa: BLE001
            log.warning("brain state unreadable, ignoring: %s", e)
        return (None, None)

    def _save_state(self) -> None:
        if not self._state_path:
            return
        kind, repo = self._backend
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"type": kind or "auto"}
        if kind == "transformers":
            payload["repo"] = repo
        self._state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def configure(self, spec: str | None) -> None:
        """Set the brain from a compact spec.

        ``None``/``""``/``"auto"`` → auto (MNN if loaded, otherwise rule mode),
        ``"mnn"`` → force MNN,
        ``"transformers:owner/repo"`` → run that HF causal LM on CPU.
        """
        spec = (spec or "").strip()
        if spec in ("", "auto"):
            self._backend = (None, None)
        elif spec == "mnn":
            self._backend = ("mnn", None)
        elif spec.startswith("transformers:"):
            repo = spec.split(":", 1)[1].strip()
            if "/" not in repo:
                raise ValueError("transformers brain needs an 'owner/repo' id")
            self._backend = ("transformers", repo)
        else:
            raise ValueError(f"unknown brain spec: {spec!r}")
        self._save_state()

    @property
    def backend(self) -> tuple[str | None, str | None]:
        return self._backend

    # ------------------------------------------------------------------
    # MNN
    # ------------------------------------------------------------------
    def _check_mnn(self) -> tuple[bool, str]:
        try:
            from .. import mnn_runtime

            status = mnn_runtime.status()
            loaded = bool(status.get("loaded"))
            name = str(status.get("model_name") or "")
            return loaded, name
        except Exception as e:  # noqa: BLE001
            log.debug("MNN runtime check failed: %s", e)
            return False, ""

    # ------------------------------------------------------------------
    def is_ready(self) -> tuple[bool, str]:
        """Return (ready, model_name).

        A Transformers brain reports ready as soon as it is configured — the
        weights download/load lazily on first chat, so readiness must not pay
        that cost. MNN/auto uses the actual loaded-state check.
        """
        kind, repo = self._backend
        if kind == "transformers":
            name = f"transformers:{repo}"
            self._last_model_name = name
            return True, name
        loaded, name = self._check_mnn()
        if kind == "mnn":
            self._last_model_name = name
            return loaded, name
        # auto
        self._last_model_name = name
        return loaded, name

    # ------------------------------------------------------------------
    def chat(
        self,
        prompt: str,
        system: str = "",
        max_new_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Invoke the reasoning LLM; returns ``{"ok": ..., "text": ...}``."""
        kind, repo = self._backend
        full = f"{system}\n\n{prompt}" if system else prompt

        if kind == "transformers":
            try:
                from ..llm_runtime import TextBrainManager

                manager = TextBrainManager()
                text = (manager.generate(repo or "", prompt, system=system,
                                         max_new_tokens=max_new_tokens)
                        or "").strip()
                if not text:
                    return {"ok": False, "error": "LLM returned empty text",
                            "error_type": "EmptyOutput"}
                return {"ok": True, "text": text,
                        "model_name": f"transformers:{repo}"}
            except Exception as e:  # noqa: BLE001
                log.warning("transformers brain chat failed: %s", e)
                return {"ok": False, "error": str(e),
                        "error_type": type(e).__name__}

        # MNN (forced or auto).
        ready, name = self._check_mnn()
        if not ready:
            return {
                "ok": False,
                "error": (
                    "对话 AI 未就绪：可在 Agent 页选择一个本地「大脑」模型进入自主模式，"
                    "或在「MNN 引擎」页加载对话模型。未就绪时 Agent 仍可执行确定性工具操作。"
                ),
                "error_type": "LlmNotReady",
                "model_name": name,
            }
        try:
            from .. import mnn_runtime

            res = mnn_runtime.chat(full, max_new_tokens=max_new_tokens)
            text = str(res.get("text") or "").strip()
            if not text:
                return {"ok": False, "error": "LLM returned empty text",
                        "error_type": "EmptyOutput"}
            return {"ok": True, "text": text, "model_name": name}
        except Exception as e:  # noqa: BLE001
            log.exception("agent LLM call failed")
            return {"ok": False, "error": str(e),
                    "error_type": type(e).__name__}
