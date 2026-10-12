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

import gc
import logging
import threading
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


class _StopOnSignal:
    """Transformers StoppingCriteria that trips when a cancel signal is set."""

    def __init__(self, signal) -> None:
        self._signal = signal
        self.tripped = False

    def __call__(self, input_ids, scores, **kwargs) -> bool:
        try:
            if self._signal():
                self.tripped = True
                return True
        except Exception as e:  # noqa: BLE001  never let a faulty signal stall decode
            log.debug("brain stop signal raised, ignoring: %s", e)
        return False


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
        self._torch: Any = None
        self._tf: Any = None
        # Loading and generation are not safe to run concurrently on one model;
        # serialize brain use across agent sessions/threads.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def _open(self, repo: str) -> None:
        if self._model is not None and self._repo == repo:
            return
        if not isinstance(repo, str) or "/" not in repo:
            raise BrainParamError("brain model must be an 'owner/name' repo id")
        try:
            tf = import_transformers(self.data_root)
            import torch  # engine torch, resolvable after the path bootstrap

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
        self._tf = tf
        self._tokenizer = tokenizer
        self._model = model
        self._repo = repo

    # ------------------------------------------------------------------
    def _context_size(self) -> int:
        """Best-effort maximum context the loaded model supports."""
        candidates: list[Any] = [
            getattr(self._tokenizer, "model_max_length", None),
            getattr(getattr(self._model, "config", None),
                    "max_position_embeddings", None),
        ]
        for cand in candidates:
            if cand is None:
                continue
            try:
                value = int(cand)
            except (TypeError, ValueError):
                continue
            # Tokenizers sometimes expose a huge sentinel (e.g. 1e30).
            if 128 <= value <= 2_000_000:
                return value
        return 4096

    def generate(
        self,
        repo: str,
        prompt: str,
        *,
        system: str = "",
        max_new_tokens: int = 1024,
        should_stop=None,
    ) -> str:
        if not prompt or not str(prompt).strip():
            raise BrainParamError("prompt is empty")
        max_new_tokens = int(max_new_tokens)
        if not (1 <= max_new_tokens <= 4096):
            raise BrainParamError("max_new_tokens must be within [1,4096]")
        with self._lock:
            self._open(repo)
            try:
                torch = self._torch
                tokenizer = self._tokenizer
                sys_text = (
                    f"{system}\n\n" if system and str(system).strip() else ""
                )
                full_text = f"{sys_text}{prompt}"
                ids = tokenizer(full_text)["input_ids"]
                budget = self._context_size() - max_new_tokens
                if budget < 64:
                    raise BrainParamError(
                        "max_new_tokens too large for the model context window"
                    )
                if len(ids) > budget:
                    # Keep the (short) system prefix plus the most recent
                    # scratchpad — drop the oldest intermediate steps so the
                    # trailing "Thought:" anchor survives.
                    if sys_text:
                        sys_ids = tokenizer(sys_text)["input_ids"]
                        if len(sys_ids) < budget:
                            p_ids = tokenizer(
                                prompt, add_special_tokens=False
                            )["input_ids"]
                            ids = sys_ids + p_ids[-(budget - len(sys_ids)):]
                        else:
                            ids = sys_ids[-budget:]
                    else:
                        ids = ids[-budget:]
                    log.info(
                        "brain prompt exceeded context; truncated to %d tokens",
                        len(ids),
                    )
                input_ids = torch.tensor([ids], dtype=torch.long)
                # Cooperative mid-generation stop: check the cancel signal after
                # each token so Stop is responsive even within one long CPU turn.
                stopping = None
                if should_stop is not None:
                    criteria = _StopOnSignal(should_stop)
                    stopping = self._tf.StoppingCriteriaList([criteria])
                # The agent pre-assembles a full ReAct prompt (system
                # instructions + scratchpad, ending in "Thought: "), so we
                # continue it as a raw completion. Wrapping an instruct model's
                # chat template would make it answer conversationally instead
                # of emitting the next Thought/Action. A mild repetition
                # penalty keeps greedy decoding from falling into token loops.
                with torch.no_grad():
                    output = self._model.generate(
                        input_ids,
                        max_new_tokens=max_new_tokens,
                        do_sample=False,
                        stopping_criteria=stopping,
                        repetition_penalty=1.1,
                    )
                generated = output[0][len(ids):]
                return tokenizer.decode(
                    generated, skip_special_tokens=True
                ).strip()
            except BrainError:
                raise
            except Exception as e:  # noqa: BLE001
                raise BrainModelError(f"大脑生成失败: {e}") from e

    # ------------------------------------------------------------------
    def unload(self) -> None:
        """Release the loaded model/tokenizer (used between compare runs so we
        never hold several models in RAM at once)."""
        with self._lock:
            self._model = None
            self._tokenizer = None
            self._repo = None
            self._torch = None
            self._tf = None
        gc.collect()

    def answer(
        self,
        repo: str,
        question: str,
        *,
        system: str = "",
        max_new_tokens: int = 512,
    ) -> str:
        """Answer a standalone user question using the model's chat template
        (as opposed to ``generate``, which continues a raw ReAct scratchpad)."""
        if not question or not str(question).strip():
            raise BrainParamError("question is empty")
        max_new_tokens = int(max_new_tokens)
        if not (1 <= max_new_tokens <= 4096):
            raise BrainParamError("max_new_tokens must be within [1,4096]")
        with self._lock:
            self._open(repo)
            try:
                torch = self._torch
                tok = self._tokenizer
                messages: list[dict[str, str]] = []
                if system and str(system).strip():
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": question})
                prompt_text = tok.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
                ids = tok(prompt_text, add_special_tokens=False)["input_ids"]
                budget = self._context_size() - max_new_tokens
                if budget < 64:
                    raise BrainParamError(
                        "max_new_tokens too large for the model context window"
                    )
                if len(ids) > budget:
                    ids = ids[-budget:]
                input_ids = torch.tensor([ids], dtype=torch.long)
                with torch.no_grad():
                    output = self._model.generate(
                        input_ids,
                        max_new_tokens=max_new_tokens,
                        do_sample=False,
                    )
                generated = output[0][len(ids):]
                return strip_reasoning(
                    tok.decode(generated, skip_special_tokens=True)
                )
            except BrainError:
                raise
            except Exception as e:  # noqa: BLE001
                raise BrainModelError(f"回答生成失败: {e}") from e


COMPARE_MAX_MODELS = 4


def strip_reasoning(text: str) -> str:
    """Drop a Qwen3-style ``<think>…</think>`` reasoning block, leaving the
    final answer. If no closing tag is present the text is returned as-is."""
    s = str(text or "")
    if "</think>" in s:
        s = s.split("</think>", 1)[1]
    return s.strip()


def compare_answers(
    manager: TextBrainManager,
    repos: list[str],
    question: str,
    *,
    system: str = "",
    max_new_tokens: int = 512,
) -> list[dict[str, Any]]:
    """Ask one question to several local models and return their answers.

    Models run sequentially and are unloaded after each (the host may have only
    a few GB of RAM). A failure for one model is captured, not raised, so the
    other answers still come back."""
    if not isinstance(repos, (list, tuple)) or not repos:
        raise BrainParamError("select at least one model to compare")
    if len(repos) > COMPARE_MAX_MODELS:
        raise BrainParamError(
            f"at most {COMPARE_MAX_MODELS} models can be compared at once"
        )
    if not question or not str(question).strip():
        raise BrainParamError("question is empty")
    results: list[dict[str, Any]] = []
    for repo in repos:
        try:
            ans = manager.answer(
                repo, question, system=system,
                max_new_tokens=max_new_tokens,
            )
            results.append({"repo": repo, "answer": ans, "error": None})
        except BrainError as e:
            results.append({"repo": repo, "answer": "", "error": str(e)})
        finally:
            unload = getattr(manager, "unload", None)
            if callable(unload):
                unload()  # real unload frees the model and never raises
    return results
