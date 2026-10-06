"""Music source-separation runtime via Demucs (Meta, hybrid transformer).

Follows the dedicated-runtime pattern of :mod:`asr_runtime` /
:mod:`embedding_runtime`:

* locate the pip-installed ``demucs`` engine target and put it on ``sys.path``
  (engines are installed with ``pip install --target``);
* build a cached ``demucs.api.Separator`` for the chosen pretrained model;
* separate an uploaded/local track into stems, write WAV files under a per-job
  output directory and return a manifest. The stems are streamed back to the
  renderer through the ``/api/separation/stream`` route (FileResponse).

The module never imports demucs/torch at import time, so the control plane
starts even before the engine is installed.
"""
from __future__ import annotations

import logging
import threading
import time
import wave
from pathlib import Path
from typing import Any

from .settings import default_data_root

log = logging.getLogger("kevrai.demucs")

ENGINE_ID = "demucs"
_PIP_DIR = "pip-demucs"

# Pretrained models shipped in the catalog (see demucs/remote/*.yaml).
KNOWN_MODELS = ("htdemucs", "htdemucs_ft", "htdemucs_6s")
# Stem names produced by the 4-source models; htdemucs_6s adds piano + guitar.
FOUR_STEMS = ("drums", "bass", "other", "vocals")


class DemucsError(Exception):
    """Base error for the separation runtime."""


class DemucsEngineMissing(DemucsError):
    """Raised when the demucs engine is not installed/loadable."""


class DemucsModelError(DemucsError):
    """Raised when a model cannot load or separation fails."""


class DemucsParamError(DemucsError):
    """Raised for invalid separation parameters."""


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


def import_demucs(data_root: Path | None = None) -> Any:
    """Import and return the ``demucs.api`` module.

    Raises :class:`DemucsEngineMissing` with an actionable message when the
    engine or its native backend (torch) cannot load.
    """
    _bootstrap_sys_path(data_root)
    try:
        from demucs import api  # type: ignore

        return api
    except Exception as e:  # noqa: BLE001 — surface any native load failure
        raise DemucsEngineMissing(
            "Demucs 引擎未安装或无法加载，请先在引擎页安装 demucs。"
            f"原始错误：{e}"
        ) from e


class DemucsManager:
    """Builds cached separators and runs source separation."""

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

    def load(self, model: str, *, device: str = "auto",
             shifts: int = 1, overlap: float = 0.25,
             jobs: int = 0) -> tuple[Any, str]:
        """Return a cached ``Separator`` and the device in use."""
        api = import_demucs(self.data_root)
        if device == "auto":
            device = self._select_device()
        key = (model, device)
        with self._lock:
            sep = self._cache.get(key)
            if sep is None:
                try:
                    sep = api.Separator(
                        model=model, device=device, shifts=int(shifts),
                        overlap=float(overlap), split=True, jobs=int(jobs),
                        progress=False,
                    )
                except Exception as e:  # noqa: BLE001
                    raise DemucsModelError(f"无法加载分离模型 {model}：{e}") from e
                self._cache[key] = sep
        return sep, device

    def separate(self, model: str, input_path: Path, *,
                 device: str = "auto", shifts: int = 1,
                 overlap: float = 0.25, jobs: int = 0) -> dict[str, Any]:
        """Separate ``input_path`` and write WAV stems; return a manifest."""
        if not isinstance(model, str) or not model.strip():
            raise DemucsParamError("model is required")
        if not isinstance(shifts, int) or shifts < 0 or shifts > 20:
            raise DemucsParamError("shifts must be between 0 and 20")
        if not isinstance(overlap, (int, float)) or not 0.0 <= float(overlap) < 1.0:
            raise DemucsParamError("overlap must be in [0, 1)")
        in_path = Path(input_path)
        if not in_path.is_file():
            raise DemucsParamError(f"audio file not found: {in_path}")

        api = import_demucs(self.data_root)
        sep, used_device = self.load(
            model, device=device, shifts=shifts, overlap=overlap, jobs=jobs)
        job_id = time.strftime("%Y%m%d-%H%M%S")
        out_dir = self.data_root / "separated" / job_id
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            _, stems = sep.separate_audio_file(in_path)
        except Exception as e:  # noqa: BLE001
            raise DemucsModelError(f"音源分离失败：{e}") from e

        sr = int(sep.samplerate)
        stem_manifest: list[dict[str, Any]] = []
        for name, wav_tensor in stems.items():
            out_path = out_dir / f"{name}.wav"
            try:
                api.save_audio(wav_tensor, str(out_path), sr)
            except Exception:  # noqa: BLE001 — fallback pure-python WAV writer
                _write_wav(out_path, wav_tensor, sr)
            stem_manifest.append({
                "name": name,
                "file": out_path.name,
                "path": str(out_path),
                "size_bytes": out_path.stat().st_size if out_path.exists() else 0,
                "duration_s": round(wav_tensor.shape[-1] / sr, 3),
                "channels": int(wav_tensor.shape[0]) if wav_tensor.dim() > 1 else 1,
            })
        stem_manifest.sort(key=lambda s: s["name"])
        return {
            "job_id": job_id,
            "model": model,
            "device": used_device,
            "sample_rate": sr,
            "output_dir": str(out_dir),
            "stems": stem_manifest,
        }


def _write_wav(path: Path, wav_tensor: Any, sr: int) -> None:
    """Fallback 16-bit PCM WAV writer (no soundfile/torchaudio needed)."""
    try:
        import numpy as np  # type: ignore

        arr = wav_tensor.detach().cpu().numpy()
    except Exception:  # noqa: BLE001 — last resort: cannot encode
        return
    if arr.ndim == 1:
        arr = arr[None, :]
    # (channels, samples) -> interleaved (samples, channels)
    arr = np.clip(arr.T, -1.0, 1.0)
    pcm = (arr * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(arr.shape[1])
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def capabilities(data_root: Path | None = None) -> dict[str, Any]:
    """Report engine/device availability and the selectable models."""
    target = engine_target_dir(data_root)
    installed = target.is_dir()
    cuda = False
    demucs_version: str | None = None
    if installed:
        try:
            _bootstrap_sys_path(data_root)
            import demucs  # type: ignore

            demucs_version = getattr(demucs, "__version__", None)
            if not demucs_version:
                from importlib import metadata

                demucs_version = metadata.version("demucs")
            import torch  # type: ignore

            cuda = bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001 — capability probe must stay non-fatal
            log.debug("demucs capability probe failed", exc_info=True)
    return {
        "engine": ENGINE_ID,
        "installed": installed,
        "engine_dir": str(target),
        "cuda_available": cuda,
        "version": demucs_version,
        "models": list(KNOWN_MODELS),
    }
