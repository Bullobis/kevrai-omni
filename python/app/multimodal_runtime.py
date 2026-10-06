"""Generic vision-language runtime backed by HuggingFace ``transformers``.

This runtime gives the catalog's ``transformers`` engine a real inference path
for native multimodal models (image + text -> text), such as SmolVLM, Janus
and MiniCPM-V. It follows the dedicated-runtime pattern used by
:mod:`asr_runtime`, :mod:`embedding_runtime` and :mod:`piper_runtime`:

* the engine is pip-installed with ``--target`` into
  ``<data_root>/engines/pip-transformers`` and prepended to ``sys.path``;
* weights already pulled by the hub downloader are used from disk, otherwise
  the repo id is passed through and transformers fetches them;
* a single generic code path builds the processor chat template, attaches the
  supplied images and decodes only the newly generated tokens. Family-specific
  quirks are absorbed in small adapter hooks rather than separate runtimes.

``transformers`` / ``torch`` are never imported at module import time, so the
control plane starts before the engine is installed.
"""
from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .settings import default_data_root

log = logging.getLogger("kevrai.multimodal")

ENGINE_ID = "transformers"
_PIP_DIR = "pip-transformers"

# Catalog model id -> upstream HuggingFace repo. Kept in sync with the catalog;
# used purely for the capabilities report and to resolve a catalog id to a repo.
KNOWN_MODELS: dict[str, str] = {
    "smolvlm-256": "HuggingFaceTB/SmolVLM-256M-Instruct",
    "janus-pro-7b": "deepseek-ai/Janus-Pro-7B",
    "minicpm-v-4.6": "openbmb/MiniCPM-V-4.6",
}

# Auto classes tried in order when loading a model; the first that recognises
# the checkpoint wins, keeping the loader open to future architectures.
_AUTO_MODEL_CLASSES = (
    "AutoModelForImageTextToText",
    "AutoModelForCausalLM",
    "AutoModelForConditionalGeneration",
)


class MultimodalError(Exception):
    """Base error for the multimodal runtime."""


class MultimodalEngineMissing(MultimodalError):
    """Raised when the transformers engine is not installed/loadable."""


class MultimodalModelError(MultimodalError):
    """Raised when a model/processor cannot be loaded."""


class MultimodalParamError(MultimodalError):
    """Raised for invalid chat parameters/input."""


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
            sys.path.insert(0, s)
    return target


def import_transformers(data_root: Path | None = None) -> Any:
    """Import and return the ``transformers`` module.

    Raises :class:`MultimodalEngineMissing` with an actionable message when the
    engine or its native backend (torch) cannot load.
    """
    _bootstrap_sys_path(data_root)
    try:
        # Eagerly, fully initialise numpy from the engine target first.
        # transformers lazily imports accelerate when an Auto* class is first
        # touched; accelerate's utils reference ``numpy._core.multiarray`` at
        # import time. If numpy is only half-initialised at that moment (the
        # import is triggered transitively during that lazy load), it raises
        # "module 'numpy._core' has no attribute 'multiarray'". Importing it
        # up front makes the later accelerate import deterministic.
        import numpy as _np  # noqa: F401
        import transformers  # type: ignore

        return transformers
    except Exception as e:  # noqa: BLE001 — surface any native load failure
        raise MultimodalEngineMissing(
            "Transformers 引擎未安装或无法加载，请先在引擎页安装 transformers。"
            f"原始错误：{e}"
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

    A directory is considered complete when it has ``config.json`` and at least
    one weight file.
    """
    local = _hub_model_dir(repo, data_root, download_dir)
    has_config = (local / "config.json").exists()
    weight_markers = (
        "model.safetensors", "pytorch_model.bin",
        "model-00001-of-00004.safetensors",
        "pytorch_model-00001-of-00002.bin",
    )
    has_weights = any((local / marker).exists() for marker in weight_markers)
    if has_config and has_weights:
        return local
    return repo


class MultimodalManager:
    """Loads/caches native multimodal models and runs image+text chats."""

    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = (
            Path(data_root) if data_root is not None else default_data_root()
        )
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()

    # -- resolution / loading ------------------------------------------------

    def resolve_repo(self, model_id_or_repo: str) -> str:
        """Map a catalog id to its repo; pass a repo slug straight through."""
        mid = (model_id_or_repo or "").strip()
        if not mid:
            raise MultimodalParamError("model 不能为空")
        return KNOWN_MODELS.get(mid, mid)

    def _load_bundle(self, source: str | Path) -> tuple[Any, Any, Any]:
        """Return (model, processor, tokenizer) for a repo or local directory."""
        tf = import_transformers(self.data_root)
        try:
            processor = tf.AutoProcessor.from_pretrained(
                source, trust_remote_code=True
            )
            tokenizer = tf.AutoTokenizer.from_pretrained(
                source, trust_remote_code=True
            )
        except Exception as e:  # noqa: BLE001
            raise MultimodalModelError(f"处理器加载失败：{e}") from e

        model: Any = None
        last_error: Exception | None = None
        for cname in _AUTO_MODEL_CLASSES:
            cls = getattr(tf, cname, None)
            if cls is None:
                continue
            try:
                model = cls.from_pretrained(source, trust_remote_code=True)
                break
            except Exception as e:  # noqa: BLE001 — try the next auto class
                last_error = e
        if model is None:
            raise MultimodalModelError(
                f"无法加载多模态模型（不被当前 transformers 识别）：{last_error}"
            )
        model.eval()
        return model, processor, tokenizer

    def load(self, model_id_or_repo: str) -> tuple[Any, Any, Any]:
        """Resolve, load (with cache) and return the model bundle."""
        repo = self.resolve_repo(model_id_or_repo)
        source = resolve_model_source(repo, self.data_root)
        key = str(source)
        with self._lock:
            bundle = self._cache.get(key)
            if bundle is None:
                bundle = self._load_bundle(source)
                self._cache[key] = bundle
        return bundle

    # -- input construction --------------------------------------------------

    @staticmethod
    def _open_images(images: list[str]) -> list[Any]:
        from PIL import Image

        opened: list[Any] = []
        for p in images:
            if not Path(p).exists():
                raise MultimodalParamError(f"图片不存在：{p}")
            opened.append(Image.open(p).convert("RGB"))
        return opened

    def _build_messages(
        self, prompt: str, hist: list[dict[str, str]], image_count: int
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for turn in hist or []:
            role = turn.get("role", "user")
            messages.append(
                {"role": role if role in ("user", "assistant") else "user",
                 "content": [{"type": "text", "text": turn.get("content", "")}]}
            )
        user_content: list[dict[str, Any]] = [
            {"type": "image"} for _ in range(image_count)
        ]
        user_content.append({"type": "text", "text": prompt})
        messages.append({"role": "user", "content": user_content})
        return messages

    def _build_inputs(
        self, processor: Any, messages: list[dict[str, Any]],
        images: list[Any]
    ) -> dict[str, Any]:
        try:
            prompt_text = processor.apply_chat_template(
                messages, add_generation_prompt=True
            )
        except Exception:  # noqa: BLE001 — fall back to a plain text join
            log.debug("apply_chat_template unsupported", exc_info=True)
            parts = [
                c.get("text", "")
                for m in messages for c in m.get("content", [])
                if c.get("type") == "text"
            ]
            prompt_text = "\n".join(p for p in parts if p)
        try:
            return processor(
                text=prompt_text,
                images=images if images else None,
                return_tensors="pt",
            )
        except Exception as e:  # noqa: BLE001
            raise MultimodalModelError(f"输入预处理失败：{e}") from e

    # -- inference -----------------------------------------------------------

    def chat(
        self,
        model_id_or_repo: str,
        prompt: str,
        hist: list[dict[str, str]] | None = None,
        images: list[str] | None = None,
        max_new_tokens: int = 512,
    ) -> dict[str, Any]:
        """Run a non-streaming image+text chat and return the answer text."""
        import time

        prompt = (prompt or "").strip()
        image_paths = images or []
        if not prompt and not image_paths:
            raise MultimodalParamError("prompt 与图片不能同时为空")
        for p in image_paths:
            if not Path(p).exists():
                raise MultimodalParamError(f"图片不存在：{p}")
        # Bootstrap the engine (and its numpy) BEFORE importing PIL here:
        # an ambient Pillow can pull in the ambient numpy and cache it, which
        # would then clash with the engine's numpy-2.x build of accelerate.
        model, processor, _ = self.load(model_id_or_repo)
        pil_images = self._open_images(image_paths)
        messages = self._build_messages(prompt, hist or [], len(pil_images))
        model_inputs = self._build_inputs(processor, messages, pil_images)

        import torch

        started = time.monotonic()
        try:
            with torch.no_grad():
                output = model.generate(
                    **model_inputs,
                    max_new_tokens=int(max_new_tokens),
                    do_sample=False,
                )
        except Exception as e:  # noqa: BLE001
            raise MultimodalModelError(f"生成失败：{e}") from e
        input_len = model_inputs["input_ids"].shape[1]
        generated = output[0][input_len:]
        text = processor.decode(generated, skip_special_tokens=True).strip()
        return {
            "text": text,
            "elapsed_s": round(time.monotonic() - started, 3),
            "multimodal": bool(pil_images),
        }

    def chat_stream(
        self,
        model_id_or_repo: str,
        prompt: str,
        hist: list[dict[str, str]] | None = None,
        images: list[str] | None = None,
        max_new_tokens: int = 512,
    ) -> Iterator[str]:
        """Yield answer text deltas as they are generated."""
        prompt = (prompt or "").strip()
        image_paths = images or []
        if not prompt and not image_paths:
            raise MultimodalParamError("prompt 与图片不能同时为空")
        for p in image_paths:
            if not Path(p).exists():
                raise MultimodalParamError(f"图片不存在：{p}")
        model, _processor, tokenizer = self.load(model_id_or_repo)
        pil_images = self._open_images(image_paths)
        messages = self._build_messages(prompt, hist or [], len(pil_images))
        model_inputs = self._build_inputs(_processor, messages, pil_images)

        tf = import_transformers(self.data_root)
        streamer = tf.TextIteratorStreamer(
            tokenizer, skip_prompt=True, skip_special_tokens=True
        )
        gen_kwargs = dict(
            model_inputs,
            max_new_tokens=int(max_new_tokens),
            do_sample=False,
            streamer=streamer,
        )

        def _run() -> None:
            with contextlib.suppress(Exception):
                model.generate(**gen_kwargs)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        for piece in streamer:
            if piece:
                yield piece


def capabilities(data_root: Path | None = None) -> dict[str, Any]:
    """Report engine availability and the multimodal models it can run."""
    root = Path(data_root) if data_root is not None else default_data_root()
    target = engine_target_dir(root)
    installed = target.is_dir()
    version: str | None = None
    if installed:
        try:
            _bootstrap_sys_path(root)
            from importlib import metadata

            version = metadata.version("transformers")
        except Exception:  # noqa: BLE001 — capability probe stays non-fatal
            log.debug("transformers capability probe failed", exc_info=True)
    return {
        "engine": ENGINE_ID,
        "installed": installed,
        "version": version,
        "engine_dir": str(target),
        "models": [
            {"id": mid, "repo": repo} for mid, repo in KNOWN_MODELS.items()
        ],
    }
