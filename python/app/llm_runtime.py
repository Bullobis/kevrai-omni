"""Text-LLM "brain" runtime for the autonomous agent (transformers, CPU).

The agent's ReAct loop needs a reasoning model. MNN is one backend (see
``mnn_runtime``); this module adds a backend that loads a small HuggingFace
causal LM directly through Transformers (the same pip environment as the
multimodal runtime), so the agent can plan and call tools fully on-device
without a separate server.

Heavy and optional: the model is downloaded and loaded lazily on the first
chat, never just to report readiness.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .multimodal_runtime import import_transformers
from .settings import default_data_root

log = logging.getLogger("kevrai.brain")


class BrainError(RuntimeError):
    """Base error for the text brain."""


class BrainEngineMissing(BrainError):
    """The Transformers engine is not installed."""


class BrainModelError(BrainError):
    """The model could not be loaded or run."""


class BrainParamError(BrainError):
    """Invalid parameters supplied to the brain."""


# Curated, verified small instruct models suitable as an on-device brain.
# (repo -> human label). Sizes are the published safetensors totals.
BRAIN_MODELS: dict[str, str] = {
    "HuggingFaceTB/SmolLM2-135M-Instruct": "SmolLM2 135M (lightest)",
    "HuggingFaceTB/SmolLM2-360M-Instruct": "SmolLM2 360M (balanced)",
    "Qwen/Qwen3-0.6B": "Qwen3 0.6B (stronger)",
}


class TextBrainManager:
    """Loads and runs a small causal LM via Transformers."""

    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = Path(data_root) if data_root is not None else default_data_root()
        self._repo: str | None = None
        self._model: Any = None
        self._tokenizer: Any = None

    # ------------------------------------------------------------------
    def _open(self, repo: str) -> None:
        if self._model is not None and self._repo == repo:
            return
        if not isinstance(repo, str) or "/" not in repo:
            raise BrainParamError("brain model must be an 'owner/name' repo id")
        try:
            tf = import_transformers(self.data_root)
            torch = __import__("torch")
            tokenizer = tf.AutoTokenizer.from_pretrained(repo)
            model = tf.AutoModelForCausalLM.from_pretrained(
                repo, torch_dtype="auto"
            )
            model.eval()
        except ImportError as e:
            raise BrainEngineMissing(
                "Transformers 引擎未安装：请先在「多模态对话」页安装引擎。"
            ) from e
        except Exception as e:  # noqa: BLE001
            raise BrainModelError(f"无法加载大脑模型 {repo}: {e}") from e
        self._torch = torch
        self._tokenizer = tokenizer
        self._model = model
        self._repo = repo

    # ------------------------------------------------------------------
    def generate(
        self,
        repo: str,
        prompt: str,
        *,
        system: str = "",
        max_new_tokens: int = 1024,
    ) -> str:
        if not prompt or not str(prompt).strip():
            raise BrainParamError("prompt is empty")
        max_new_tokens = int(max_new_tokens)
        if not (1 <= max_new_tokens <= 4096):
            raise BrainParamError("max_new_tokens must be within [1,4096]")
        self._open(repo)
        try:
            # The agent pre-assembles a full ReAct prompt (system instructions +
            # scratchpad, ending in "Thought: "), so we continue it as a raw
            # completion. Wrapping an instruct model's chat template here would
            # make it answer conversationally instead of emitting the next
            # Thought/Action. A mild repetition penalty keeps greedy decoding
            # from falling into token loops on weak models.
            text = f"{system}\n\n{prompt}" if system and str(system).strip() else prompt
            inputs = self._tokenizer(text, return_tensors="pt")
            input_len = int(inputs["input_ids"].shape[1])
            with self._torch.no_grad():
                output = self._model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    repetition_penalty=1.1,
                )
            generated = output[0][input_len:]
            return self._tokenizer.decode(generated, skip_special_tokens=True).strip()
        except Exception as e:  # noqa: BLE001
            raise BrainModelError(f"大脑生成失败: {e}") from e
