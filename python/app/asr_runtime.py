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

import contextlib
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


#: ASR 后端引擎。faster-whisper 走 CTranslate2；sensevoice 走 funasr。
ENGINE_WHISPER = "faster-whisper"
ENGINE_SENSEVOICE = "sensevoice"

#: 模型仓库 -> 应使用的引擎。
#:
#: 判定不能靠"名字里有没有 whisper"这种模糊匹配 —— 判错会用一个引擎去加载
#: 另一个引擎的权重，报错信息完全指不到真正原因。
MODEL_ENGINE: dict[str, str] = {
    "FunAudioLLM/SenseVoiceSmall": ENGINE_SENSEVOICE,
    "iic/SenseVoiceSmall": ENGINE_SENSEVOICE,
    "Qwen/Qwen3-ASR-0.6B": ENGINE_SENSEVOICE,
    "Qwen/Qwen3-ASR-1.7B": ENGINE_SENSEVOICE,
}

#: SenseVoice 系列支持的语言（阿里官方列出）。
SENSEVOICE_LANGS = {
    "auto": "自动检测",
    "zh": "普通话",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "yue": "粤语",
}

#: SenseVoice 需要的推理依赖。装了哪个就用哪个，都不装就明确说要装哪个。
SENSEVOICE_DEPS = ("funasr", "modelscope")


def engine_for_model(repo: str) -> str:
    """判定一个仓库该用哪个 ASR 引擎。未登记的按 Whisper 处理（既有行为）。"""
    key = (repo or "").strip().lower().rstrip("/")
    for known, eng in MODEL_ENGINE.items():
        if key == known.lower():
            return eng
    # 尾名匹配：本地目录可能只有最后一段
    tail = key.split("/")[-1]
    for known, eng in MODEL_ENGINE.items():
        if tail == known.lower().split("/")[-1]:
            return eng
    return ENGINE_WHISPER


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
                         download_dir: Path | None = None) -> str | Path:
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
    except Exception:  # noqa: BLE001 — CUDA probe is best-effort, fall through to CPU
        log.debug("cuda compute-type probe failed; defaulting to int8", exc_info=True)
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
        """Return ``(model, engine_module, resolved_source, compute_type)``.

        SenseVoice 走独立路径：它不走 CTranslate2，也没有 compute_type
        概念，硬套 Whisper 的参数会直接崩。
        """
        if engine_for_model(repo) == ENGINE_SENSEVOICE:
            source = resolve_model_source(repo, self.data_root)
            key = (str(source), "sensevoice", "")
            with self._lock:
                model = self._cache.get(key)
                if model is None:
                    model = self._load_sensevoice(repo, source)
                    self._cache[key] = model
            return model, None, str(source), "default"
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

    def _load_sensevoice(self, repo: str, source: Any) -> Any:
        """加载 SenseVoice / Qwen3-ASR。

        走 funasr（阿里官方推理库）。依赖缺失时明确说要装什么，
        而不是抛一个裸 ImportError —— 用户看不懂 ModuleNotFoundError
        和本项目的关系。
        """
        try:
            from funasr import AutoModel  # type: ignore
        except ImportError as e:
            raise AsrEngineMissing(
                f"SenseVoice 需要推理依赖 funasr：pip install funasr"
                f"（原始错误：{e}）"
            ) from e
        try:
            return AutoModel(model=str(source), trust_remote_code=True)
        except Exception as e:  # noqa: BLE001
            raise AsrModelError(f"无法加载 SenseVoice 模型 {repo}：{e}") from e

    def transcribe_sensevoice(
        self,
        model: Any,
        audio: str | Path | bytes,
        *,
        language: str | None = None,
    ) -> dict[str, Any]:
        """用 SenseVoice 转写，返回与 Whisper 路径一致的结构。

        两个引擎的返回字段不同（funasr 给的是带 ITN 的文本列表），
        这里统一规整，让上层不需要区分引擎。
        """
        path = self._materialise_audio(audio)
        kwargs: dict[str, Any] = {"input": str(path)}
        lang = (language or "auto").lower()
        kwargs["language"] = lang if lang in SENSEVOICE_LANGS else "auto"
        try:
            res = model.generate(**kwargs)
        except TypeError:
            # 不同 funasr 版本的参数名不完全一致，退回最小调用。
            try:
                res = model.generate(input=str(path))
            except Exception as e:  # noqa: BLE001
                raise AsrModelError(f"SenseVoice 转写失败：{e}") from e
        except Exception as e:  # noqa: BLE001
            raise AsrModelError(f"SenseVoice 转写失败：{e}") from e

        text = self._extract_sensevoice_text(res)
        return {
            "text": text,
            "language": kwargs["language"],
            "duration": None,
            "segments": [],
            "engine": ENGINE_SENSEVOICE,
        }

    @staticmethod
    def _extract_sensevoice_text(res: Any) -> str:
        """从 funasr 返回里取文本。

        funasr 的返回是 list[dict]，文本在 "text" 键里，且可能带 ITN 标记
        （如 "<|zh|><|NEUTRAL|><|Speech|> 你好"），需要剥掉这些标签。
        """
        import re

        raw = ""
        if isinstance(res, (list, tuple)) and res:
            first = res[0]
            if isinstance(first, dict):
                raw = str(first.get("text") or "")
            else:
                raw = str(first)
        elif isinstance(res, dict):
            raw = str(res.get("text") or "")
        else:
            raw = str(res or "")
        # 剥掉 <|...|> 形式的标签
        return re.sub(r"<\|[^|]*\|>", "", raw).strip()

    @staticmethod
    def _materialise_audio(audio: str | Path | bytes) -> Path:
        """把 bytes 落盘成临时文件；路径原样返回。"""
        if isinstance(audio, (bytes, bytearray)):
            import tempfile

            fd, tmp = tempfile.mkstemp(suffix=".wav")
            with os.fdopen(fd, "wb") as fh:
                fh.write(bytes(audio))
            return Path(tmp)
        return Path(audio)

    def transcribe(self, repo: str, audio: str | Path | bytes, *,
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
                with contextlib.suppress(OSError):
                    tmp_path.unlink()

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


def render_response(result: dict[str, Any], response_format: str) -> tuple[str, str]:
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
        except Exception:  # noqa: BLE001 — capability probe must stay non-fatal
            log.debug("faster-whisper capability probe failed", exc_info=True)

    # SenseVoice 可用性单独探测：它依赖 funasr，和 faster-whisper 无关。
    sensevoice_ready = False
    sensevoice_missing: list[str] = []
    for dep in SENSEVOICE_DEPS:
        try:
            __import__(dep)
            sensevoice_ready = True
            break
        except ImportError:
            sensevoice_missing.append(dep)
    if sensevoice_ready:
        sensevoice_missing = []

    return {
        "engine": ENGINE_ID,
        "installed": installed,
        "engine_dir": str(target),
        "cuda_available": cuda,
        "cuda_compute_types": compute_types,
        "version": fw_version,
        "engines": {
            ENGINE_WHISPER: {"installed": installed},
            ENGINE_SENSEVOICE: {
                "installed": sensevoice_ready,
                "missing_deps": sensevoice_missing,
                "languages": sorted(SENSEVOICE_LANGS),
            },
        },
    }
