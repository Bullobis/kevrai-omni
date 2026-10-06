"""ASR runtime — Faster Whisper (CTranslate2) speech-to-text.

Follows the dedicated-runtime pattern of :mod:`ltx_runtime` /
:mod:`mnn_runtime`:

* locate the pip-installed ``faster-whisper`` engine target and put it on
  ``sys.path`` (engines are installed with ``pip install --target``);
* resolve a model from a hub-downloaded directory when present, otherwise
  pass the HuggingFace repo id and let faster-whisper fetch it;
* transcribe audio and return segments, detected language and duration.

The module never imports faster-whisper/ctranslate2 at import time, so the
control plane starts even before the engine is installed.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .settings import default_data_root

log = logging.getLogger("kevrai.asr")

ENGINE_ID = "faster-whisper"
_PIP_DIR = "pip-faster-whisper"

# Whisper's 99 supported languages (ISO-639-1), used only to validate input.
_WHISPER_LANGS = {
    "en", "zh", "de", "es", "ru", "ko", "fr", "ja", "pt", "tr", "pl", "ca",
    "nl", "ar", "sv", "it", "id", "hi", "fi", "vi", "he", "uk", "el", "ms",
    "cs", "ro", "da", "hu", "ta", "no", "th", "ur", "hr", "bg", "lt", "la",
    "mi", "ml", "cy", "sk", "te", "fa", "lv", "bn", "sr", "az", "sl", "kn",
    "et", "mk", "br", "eu", "is", "hy", "ne", "mn", "bs", "kk", "sq", "sw",
    "gl", "mr", "pa", "si", "km", "sn", "yo", "so", "af", "oc", "ka", "be",
    "tg", "sd", "gu", "am", "yi", "lo", "uz", "fo", "ht", "ps", "tk", "nn",
    "mt", "sa", "lb", "my", "bo", "tl", "mg", "as", "tt", "haw", "ln", "ha",
    "ba", "jw", "su", "yue",
}


class AsrError(Exception):
    """Base error for the ASR runtime."""


class AsrEngineMissing(AsrError):
    """Raised when the faster-whisper engine is not installed/importable."""


class AsrModelError(AsrError):
    """Raised when a model cannot be loaded or audio cannot be decoded."""


class AsrParamError(AsrError):
    """Raised for invalid transcription parameters."""


@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "start": self.start, "end": self.end, "text": self.text}


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


def import_faster_whisper(data_root: Path | None = None) -> Any:
    """Import and return the ``faster_whisper`` module.

    Raises :class:`AsrEngineMissing` with an actionable message when the
    engine is absent or its native backend (ctranslate2) cannot load.
    """
    _bootstrap_sys_path(data_root)
    try:
        import faster_whisper  # type: ignore

        return faster_whisper
    except Exception as e:  # noqa: BLE001 — surface any native load failure
        raise AsrEngineMissing(
            "Faster Whisper 引擎未安装或无法加载，请先在引擎页安装 faster-whisper"
            f"（底层 CTranslate2）。原始错误：{e}"
        ) from e


def _hub_model_dir(repo: str, data_root: Path | None = None,
                   download_dir: Path | None = None) -> Path:
    """Where the hub downloader stores ``repo``'s files."""
    root = Path(data_root) if data_root is not None else default_data_root()
    dl = Path(download_dir) if download_dir is not None else root / "downloads"
    safe = repo.strip("/").replace("/", "__")
    return dl / "hub" / "hf" / safe


def resolve_model_source(repo: str, data_root: Path | None = None,
                         download_dir: Path | None = None) -> "str | Path":
    """Return a local model directory when the weights exist, else ``repo``.

    A CTranslate2 directory is considered complete when it has both
    ``model.bin`` and ``config.json``.
    """
    local = _hub_model_dir(repo, data_root, download_dir)
    if (local / "model.bin").exists() and (local / "config.json").exists():
        return local
    return repo


def _default_compute_type(faster_whisper: Any) -> str:
    """Pick a sensible compute type for the available device."""
    try:
        import ctranslate2  # type: ignore

        if "cuda" in ctranslate2.get_supported_compute_types("cuda"):
            return "float16"
    except Exception:  # noqa: BLE001
        pass
    return "int8"


def _format_timestamp(seconds: float) -> str:
    ms = int(round((seconds - int(seconds)) * 1000))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _to_srt(segments: list[Segment]) -> str:
    out = []
    for seg in segments:
        out.append(str(seg.id + 1))
        out.append(f"{_format_timestamp(seg.start)} --> {_format_timestamp(seg.end)}")
        out.append(seg.text.strip())
        out.append("")
    return "\n".join(out).strip() + "\n"


def _to_vtt(segments: list[Segment]) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        a = _format_timestamp(seg.start).replace(",", ".")
        b = _format_timestamp(seg.end).replace(",", ".")
        lines.append(f"{a} --> {b}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


class AsrManager:
    """Loads and caches Whisper models and runs transcriptions."""

    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = Path(data_root) if data_root is not None else default_data_root()
        self._cache: dict[tuple[str, str, str], Any] = {}
        self._lock = threading.Lock()

    def _cache_key(self, source: Any, device: str, compute_type: str) -> tuple[str, str, str]:
        return (str(source), device, compute_type)

    def load(self, repo: str, *, device: str = "auto",
             compute_type: str = "auto") -> tuple[Any, Any, str, str]:
        """Return ``(model, faster_whisper, resolved_source, compute_type)``."""
        fw = import_faster_whisper(self.data_root)
        source = resolve_model_source(repo, self.data_root)
        if device == "auto":
            try:
                import ctranslate2  # type: ignore

                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:  # noqa: BLE001
                device = "cpu"
        if compute_type == "auto":
            compute_type = _default_compute_type(fw)
        key = self._cache_key(source, device, compute_type)
        with self._lock:
            model = self._cache.get(key)
            if model is None:
                try:
                    model = fw.WhisperModel(str(source), device=device,
                                           compute_type=compute_type)
                except Exception as e:  # noqa: BLE001
                    raise AsrModelError(f"无法加载模型 {repo}：{e}") from e
                self._cache[key] = model
        return model, fw, str(source), compute_type

    def transcribe(self, repo: str, audio: "str | Path | bytes", *,
                   language: str | None = None, task: str = "transcribe",
                   beam_size: int = 5, temperature: float | list[float] = 0.0,
                   vad_filter: bool = True, word_timestamps: bool = False,
                   device: str = "auto", compute_type: str = "auto") -> dict[str, Any]:
        """Transcribe ``audio`` (path or raw bytes) with model ``repo``."""
        if not isinstance(repo, str) or "/" not in repo:
            raise AsrParamError("model must be an 'owner/name' repo id")
        if language is not None and language.lower() not in _WHISPER_LANGS:
            raise AsrParamError(f"unsupported language code: {language!r}")
        if task not in {"transcribe", "translate"}:
            raise AsrParamError("task must be 'transcribe' or 'translate'")
        beam_size = int(beam_size)
        if beam_size < 1:
            raise AsrParamError("beam_size must be >= 1")

        tmp_path: Path | None = None
        if isinstance(audio, (bytes, bytearray)):
            import tempfile

            fd, tmp = tempfile.mkstemp(suffix=".audio")
            os.close(fd)
            Path(tmp).write_bytes(bytes(audio))
            tmp_path = Path(tmp)
            audio_arg: Any = tmp
        else:
            audio_arg = str(audio)
            if not Path(audio_arg).exists():
                raise AsrParamError(f"audio file not found: {audio_arg}")

        model, _fw, source, ctype = self.load(
            repo, device=device, compute_type=compute_type
        )
        try:
            segments_iter, info = model.transcribe(
                audio_arg,
                language=(language.lower() if language else None),
                task=task,
                beam_size=beam_size,
                temperature=temperature,
                vad_filter=vad_filter,
                word_timestamps=word_timestamps,
            )
            segs = [
                Segment(id=i, start=float(s.start), end=float(s.end), text=s.text)
                for i, s in enumerate(segments_iter)
            ]
        except AsrError:
            raise
        except Exception as e:  # noqa: BLE001 — decode/inference failures
            raise AsrModelError(f"转写失败：{e}") from e
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        text = "".join(s.text for s in segs).strip()
        return {
            "text": text,
            "language": getattr(info, "language", None),
            "language_probability": float(getattr(info, "language_probability", 0.0) or 0.0),
            "duration": float(getattr(info, "duration", 0.0) or 0.0),
            "segments": [s.as_dict() for s in segs],
            "model": repo,
            "source": source,
            "device": device if device != "auto" else device,
            "compute_type": ctype,
            "task": task,
        }


def render_response(result: dict[str, Any], response_format: str) -> "tuple[str, str]":
    """Render a transcription result into ``(body, media_type)``."""
    segs = [Segment(**{k: s[k] for k in ("id", "start", "end", "text")})
            for s in result["segments"]]
    fmt = (response_format or "json").lower()
    if fmt == "text":
        return result["text"], "text/plain; charset=utf-8"
    if fmt == "srt":
        return _to_srt(segs), "application/x-subrip; charset=utf-8"
    if fmt == "vtt":
        return _to_vtt(segs), "text/vtt; charset=utf-8"
    if fmt in {"verbose_json", "verbose"}:
        import json

        return json.dumps(result, ensure_ascii=False), "application/json"
    if fmt == "json":
        import json

        return (json.dumps({"text": result["text"]}, ensure_ascii=False),
                "application/json")
    raise AsrParamError(f"unsupported response_format: {response_format!r}")


def capabilities(data_root: Path | None = None) -> dict[str, Any]:
    """Report engine/device availability for the UI."""
    target = engine_target_dir(data_root)
    installed = target.is_dir()
    cuda = False
    compute_types: list[str] = []
    fw_version: str | None = None
    if installed:
        try:
            fw = import_faster_whisper(data_root)
            fw_version = getattr(fw, "__version__", None)
            import ctranslate2  # type: ignore

            cuda = ctranslate2.get_cuda_device_count() > 0
            if cuda:
                compute_types = sorted(ctranslate2.get_supported_compute_types("cuda"))
        except Exception:  # noqa: BLE001
            pass
    return {
        "engine": ENGINE_ID,
        "installed": installed,
        "engine_dir": str(target),
        "cuda_available": cuda,
        "cuda_compute_types": compute_types,
        "version": fw_version,
    }
