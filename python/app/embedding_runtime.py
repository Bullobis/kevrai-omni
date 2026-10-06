"""Embedding runtime — text embeddings via sentence-transformers.

Follows the dedicated-runtime pattern of :mod:`asr_runtime` /
:mod:`ltx_runtime`:

* locate the pip-installed ``sentence-transformers`` engine target and put it
  on ``sys.path`` (engines are installed with ``pip install --target``);
* resolve a model from a hub-downloaded directory when present, otherwise pass
  the HuggingFace repo id and let sentence-transformers fetch it;
* encode one or more texts and return dense vectors plus an OpenAI-compatible
  ``/v1/embeddings`` payload.

The module never imports sentence-transformers/torch at import time, so the
control plane starts even before the engine is installed.
"""
from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path
from typing import Any

from .settings import default_data_root

log = logging.getLogger("kevrai.embedding")

ENGINE_ID = "sentence-transformers"
_PIP_DIR = "pip-sentence-transformers"
_MAX_BATCH = 2048


class EmbeddingError(Exception):
    """Base error for the embedding runtime."""


class EmbeddingEngineMissing(EmbeddingError):
    """Raised when the sentence-transformers engine is not installed/loadable."""


class EmbeddingModelError(EmbeddingError):
    """Raised when a model cannot be loaded."""


class EmbeddingParamError(EmbeddingError):
    """Raised for invalid embedding parameters/input."""


def engine_target_dir(data_root: Path | None = None) -> Path:
    """Directory the engine is installed into (``pip --target``)."""
    root = Path(data_root) if data_root is not None else default_data_root()
    return root / "engines" / _PIP_DIR


def _bootstrap_sys_path(data_root: Path | None = None) -> Path:
    """Add the engine target to ``sys.path`` if it exists; return its path."""
    target = engine_target_dir(data_root)
    if target.is_dir():
        import sys

        s = str(target)
        if s not in sys.path:
            # Prepend so the --target copy wins over any ambient install.
            sys.path.insert(0, s)
    return target


def import_sentence_transformers(data_root: Path | None = None) -> Any:
    """Import and return the ``sentence_transformers`` module.

    Raises :class:`EmbeddingEngineMissing` with an actionable message when the
    engine or its native backend (torch) cannot load.
    """
    _bootstrap_sys_path(data_root)
    try:
        import sentence_transformers  # type: ignore

        return sentence_transformers
    except Exception as e:  # noqa: BLE001 — surface any native load failure
        raise EmbeddingEngineMissing(
            "Sentence Transformers 引擎未安装或无法加载，请先在引擎页安装"
            f" sentence-transformers。原始错误：{e}"
        ) from e


def _hub_model_dir(repo: str, data_root: Path | None = None,
                   download_dir: Path | None = None) -> Path:
    """Where the hub downloader stores ``repo``'s files."""
    root = Path(data_root) if data_root is not None else default_data_root()
    dl = Path(download_dir) if download_dir is not None else root / "downloads"
    safe = repo.strip("/").replace("/", "__")
    return dl / "hub" / "hf" / safe


def resolve_model_source(repo: str, data_root: Path | None = None,
                         download_dir: Path | None = None) -> str | Path:
    """Return a local model directory when the weights exist, else ``repo``.

    A sentence-transformers directory is considered complete when it has a
    ``config.json`` and at least one weight file.
    """
    local = _hub_model_dir(repo, data_root, download_dir)
    has_config = (local / "config.json").exists() or (local / "modules.json").exists()
    weight_markers = (
        "model.safetensors", "pytorch_model.bin", "model.onnx",
        "openvino_model.bin",
    )
    has_weights = any((local / marker).exists() for marker in weight_markers)
    if has_config and has_weights:
        return local
    return repo


class EmbeddingManager:
    """Loads and caches SentenceTransformer models and encodes texts."""

    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = Path(data_root) if data_root is not None else default_data_root()
        self._cache: dict[tuple[str, str], Any] = {}
        self._lock = threading.Lock()

    def _select_device(self) -> str:
        try:
            import torch  # type: ignore

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:  # noqa: BLE001 — torch always ships with the engine
            return "cpu"

    def load(self, repo: str, *, device: str = "auto") -> tuple[Any, str, str]:
        """Return ``(model, resolved_source, device)``."""
        st = import_sentence_transformers(self.data_root)
        source = resolve_model_source(repo, self.data_root)
        if device == "auto":
            device = self._select_device()
        key = (str(source), device)
        with self._lock:
            model = self._cache.get(key)
            if model is None:
                try:
                    model = st.SentenceTransformer(str(source), device=device)
                except Exception as e:  # noqa: BLE001
                    raise EmbeddingModelError(f"无法加载嵌入模型 {repo}：{e}") from e
                self._cache[key] = model
        return model, str(source), device

    def _count_tokens(self, model: Any, texts: list[str]) -> int:
        """Best-effort prompt-token count using the model tokenizer."""
        tokenizer = getattr(model, "tokenizer", None)
        if tokenizer is not None:
            with contextlib.suppress(Exception):
                total = 0
                for text in texts:
                    total += len(tokenizer(text, add_special_tokens=True)["input_ids"])
                return int(total)
        # Fallback: rough 1 token ≈ 4 chars heuristic (never used when the
        # tokenizer is available, which is the normal sentence-transformers case).
        return sum(max(1, len(text) // 4) for text in texts)

    def embed(self, repo: str, texts: list[str], *,
              device: str = "auto",
              normalize_embeddings: bool = False) -> dict[str, Any]:
        """Encode ``texts`` with model ``repo`` and return vectors + metadata."""
        if not isinstance(repo, str) or "/" not in repo:
            raise EmbeddingParamError("model must be an 'owner/name' repo id")
        if not isinstance(texts, list) or not texts:
            raise EmbeddingParamError("input must be a non-empty list of strings")
        if len(texts) > _MAX_BATCH:
            raise EmbeddingParamError(f"batch exceeds the {_MAX_BATCH} limit")
        clean = [t for t in texts if isinstance(t, str) and t.strip()]
        if len(clean) != len(texts):
            raise EmbeddingParamError("every input must be a non-empty string")

        model, source, used_device = self.load(repo, device=device)
        try:
            vectors = model.encode(
                clean, normalize_embeddings=bool(normalize_embeddings)
            )
            dim = int(model.get_sentence_embedding_dimension())
        except Exception as e:  # noqa: BLE001 — encode failures
            raise EmbeddingModelError(f"生成嵌入失败：{e}") from e

        embeddings = [[float(x) for x in vec] for vec in vectors]
        if any(len(vec) != dim for vec in embeddings):
            raise EmbeddingModelError("embedding dimension mismatch")
        return {
            "model": repo,
            "source": source,
            "device": used_device,
            "dimensions": dim,
            "embeddings": embeddings,
            "prompt_tokens": self._count_tokens(model, clean),
            "max_seq_length": int(getattr(model, "max_seq_length", 0) or 0),
        }


def render_openai(result: dict[str, Any]) -> dict[str, Any]:
    """Shape an internal result into the OpenAI ``/v1/embeddings`` payload."""
    data = [
        {"object": "embedding", "index": i, "embedding": vec}
        for i, vec in enumerate(result["embeddings"])
    ]
    return {
        "object": "list",
        "data": data,
        "model": result["model"],
        "usage": {
            "prompt_tokens": result["prompt_tokens"],
            "total_tokens": result["prompt_tokens"],
        },
    }


def capabilities(data_root: Path | None = None) -> dict[str, Any]:
    """Report engine/device availability for the UI."""
    target = engine_target_dir(data_root)
    installed = target.is_dir()
    cuda = False
    st_version: str | None = None
    if installed:
        try:
            st = import_sentence_transformers(data_root)
            st_version = getattr(st, "__version__", None)
            import torch  # type: ignore

            cuda = bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001 — capability probe must stay non-fatal
            log.debug("sentence-transformers capability probe failed", exc_info=True)
    return {
        "engine": ENGINE_ID,
        "installed": installed,
        "engine_dir": str(target),
        "cuda_available": cuda,
        "version": st_version,
    }
