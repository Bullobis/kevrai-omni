"""Music generation runtime — native text-to-music via Meta MusicGen.

A local, offline text-to-music path that runs entirely in the existing
``transformers`` engine (``<data_root>/engines/pip-transformers``):

* ``MusicgenForConditionalGeneration`` decodes ~50 audio tokens per second;
  the decoded EnCodec frames are resynthesised to PCM and written as a
  standard 16-bit PCM WAV (no scipy/soundfile dependency).
* stereo MusicGen models yield two channels; mono models one. Both are handled.

``transformers`` / ``torch`` are never imported at module import time, so the
control plane starts even before the engine is installed.
"""

from __future__ import annotations

import logging
import threading
import wave
from pathlib import Path
from typing import Any

from .settings import default_data_root

log = logging.getLogger(__name__)

ENGINE_ID = "transformers"
_PIP_DIR = "pip-transformers"

#: MusicGen's EnCodec decoder emits 50 frames per second of audio.
TOKENS_PER_SECOND = 50
DEFAULT_SAMPLING_RATE = 32000
MIN_DURATION_S = 1
MAX_DURATION_S = 60

#: Catalog id -> HF repo (verified against the HuggingFace Hub; see ADR).
KNOWN_MODELS: dict[str, str] = {
    "musicgen-small": "facebook/musicgen-small",
    "musicgen-medium": "facebook/musicgen-medium",
    "musicgen-large": "facebook/musicgen-large",
    "musicgen-stereo": "facebook/musicgen-stereo-large",
}


class MusicError(Exception):
    """Base class for music-generation failures."""


class MusicEngineMissing(MusicError):
    """The transformers engine (torch/transformers) is not installed."""


class MusicModelError(MusicError):
    """The model could not be loaded, or audio synthesis failed."""


class MusicParamError(MusicError):
    """A caller-supplied parameter was invalid."""


def engine_target_dir(data_root: Path | None = None) -> Path:
    """Directory the transformers engine is installed into (``pip --target``)."""
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
    """Import and return the ``transformers`` module from the engine target.

    Raises :class:`MusicEngineMissing` with an actionable message when the
    engine or its native backend (torch) cannot load.
    """
    _bootstrap_sys_path(data_root)
    try:
        import numpy as _np  # noqa: F401 — fully init numpy before accelerate
        import transformers  # type: ignore

        return transformers
    except Exception as e:  # noqa: BLE001 — surface any native load failure
        raise MusicEngineMissing(
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
    """Return a local model directory when the weights exist, else ``repo``."""
    local = _hub_model_dir(repo, data_root, download_dir)
    has_config = (local / "config.json").exists()
    weight_markers = (
        "model.safetensors", "pytorch_model.bin",
        "model-00001-of-00002.safetensors",
    )
    has_weights = any((local / m).exists() for m in weight_markers)
    if has_config and has_weights:
        return local
    return repo


def _pcm16(audio: Any) -> bytes:
    """Interleave float32 ``[channels, samples]`` into 16-bit PCM bytes."""
    import numpy as np

    arr = np.asarray(audio, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr[None, :]
    # Clamp to the int16 range before quantising.
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).round().astype("<i2")
    # Interleave channels: [channels, samples] -> [samples, channels]
    interleaved = pcm.T.reshape(-1)
    return interleaved.tobytes()


def write_wav(path: str | Path, audio: Any, sampling_rate: int) -> str:
    """Write ``audio`` (``[channels, samples]`` float) to a 16-bit PCM WAV."""
    import numpy as np

    arr = np.asarray(audio, dtype=np.float32)
    channels = 1 if arr.ndim == 1 else int(arr.shape[0])
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(int(sampling_rate))
        wf.writeframes(_pcm16(arr))
    return str(out)


class MusicManager:
    """Loads/caches MusicGen models and synthesises music from text."""

    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = (
            Path(data_root) if data_root is not None else default_data_root()
        )
        self._cache: dict[str, tuple[Any, Any]] = {}
        self._lock = threading.Lock()

    def resolve_repo(self, model_id_or_repo: str) -> str:
        """Map a catalog id to its repo; pass a repo slug straight through."""
        mid = (model_id_or_repo or "").strip()
        if not mid:
            raise MusicParamError("model 不能为空")
        return KNOWN_MODELS.get(mid, mid)

    def _load(self, source: str | Path) -> tuple[Any, Any]:
        tf = import_transformers(self.data_root)
        try:
            processor = tf.AutoProcessor.from_pretrained(source)
            model = tf.MusicgenForConditionalGeneration.from_pretrained(source)
        except Exception as e:  # noqa: BLE001
            raise MusicModelError(f"音乐模型加载失败：{e}") from e
        model.eval()
        return model, processor

    def load(self, model_id_or_repo: str) -> tuple[Any, Any, str]:
        """Resolve, load (cached) and return ``(model, processor, repo)``."""
        repo = self.resolve_repo(model_id_or_repo)
        source = resolve_model_source(repo, self.data_root)
        key = str(source)
        with self._lock:
            bundle = self._cache.get(key)
            if bundle is None:
                bundle = self._load(source)
                self._cache[key] = bundle
        model, processor = bundle
        return model, processor, repo

    def synthesize(
        self,
        model_id_or_repo: str,
        prompt: str,
        *,
        duration_s: float = 15.0,
        guidance_scale: float = 3.0,
        seed: int | None = None,
        do_sample: bool = True,
    ) -> dict[str, Any]:
        """Synthesise a clip; returns audio ``[channels, samples]`` + metadata."""
        import time

        prompt = (prompt or "").strip()
        if not prompt:
            raise MusicParamError("prompt 不能为空")
        try:
            duration_s = float(duration_s)
        except (TypeError, ValueError) as e:
            raise MusicParamError("duration_s 必须是数字") from e
        if not (MIN_DURATION_S <= duration_s <= MAX_DURATION_S):
            raise MusicParamError(
                f"duration_s 必须在 {MIN_DURATION_S}-{MAX_DURATION_S} 秒之间"
            )
        try:
            guidance_scale = float(guidance_scale)
        except (TypeError, ValueError) as e:
            raise MusicParamError("guidance_scale 必须是数字") from e
        if not (0.0 < guidance_scale <= 20.0):
            raise MusicParamError("guidance_scale 必须在 0-20 之间")

        started = time.monotonic()
        model, processor, repo = self.load(model_id_or_repo)
        max_new_tokens = max(1, int(round(duration_s * TOKENS_PER_SECOND)))
        inputs = processor(text=[prompt], padding=True, return_tensors="pt")
        if seed is not None:
            import torch

            torch.manual_seed(int(seed))
        try:
            audio_values = model.generate(
                **inputs,
                do_sample=bool(do_sample),
                guidance_scale=guidance_scale,
                max_new_tokens=max_new_tokens,
            )
        except MusicError:
            raise
        except Exception as e:  # noqa: BLE001 — synthesis failures
            raise MusicModelError(f"音乐合成失败：{e}") from e

        audio = audio_values[0].detach().cpu().float().numpy()
        sr = int(getattr(model.config.audio_encoder, "sampling_rate",
                         DEFAULT_SAMPLING_RATE))
        return {
            "audio": audio,
            "sampling_rate": sr,
            "repo": repo,
            "requested_s": duration_s,
            "elapsed_s": round(time.monotonic() - started, 3),
        }

    def generate_to_file(
        self,
        model_id_or_repo: str,
        prompt: str,
        out_path: str | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Synthesise and write a WAV; returns metadata with ``audio_path``."""
        res = self.synthesize(model_id_or_repo, prompt, **kwargs)
        audio_path = write_wav(
            out_path, res["audio"], res["sampling_rate"]
        )
        return {
            "audio_path": audio_path,
            "sampling_rate": res["sampling_rate"],
            "repo": res["repo"],
            "requested_s": res["requested_s"],
            "elapsed_s": res["elapsed_s"],
        }


def capabilities(data_root: Path | None = None) -> dict[str, Any]:
    """Report engine availability and the MusicGen models it can run."""
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
        "duration_range_s": [MIN_DURATION_S, MAX_DURATION_S],
        "models": [
            {"id": mid, "repo": repo} for mid, repo in KNOWN_MODELS.items()
        ],
    }
