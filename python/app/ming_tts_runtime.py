"""Ming-3.0-tts-0.5B (MNN) — CPU 友好的中英文 TTS，支持语气控制。

通过独立 pip 包 ``ming-tts-mnn`` 推理（MNN 后端，无需 PyTorch/ONNX）。
模型按精度分档（int4/int8/fp16），首次调用自动从 HF 镜像下载到缓存目录。
"""
from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Any

import numpy as np


class MingTTSError(Exception):
    """Ming TTS 基础错误。"""


class _MingSingleton:
    model: Any = None
    precision: str | None = None


def models_cache_dir(data_root: Path | str) -> Path:
    d = Path(data_root) / "tts" / "ming"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_model(precision: str, data_root: Path | str):
    if _MingSingleton.model is not None and _MingSingleton.precision == precision:
        return _MingSingleton.model
    try:
        from ming_tts_mnn import MingTTSMNN  # type: ignore
    except ImportError as e:  # noqa: BLE001
        raise MingTTSError(
            "Ming TTS 引擎未安装：请在「引擎」页安装 ming-tts。"
        ) from e
    cache = str(models_cache_dir(data_root))
    try:
        _MingSingleton.model = MingTTSMNN.from_pretrained(
            precision, source="hf", cache_dir=cache, quiet=True
        )
    except Exception as e:  # noqa: BLE001
        raise MingTTSError(f"Ming TTS 模型加载失败：{e}") from e
    _MingSingleton.precision = precision
    return _MingSingleton.model


def synthesize(
    text: str,
    *,
    data_root: Path | str,
    precision: str = "int8",
    instruction: str | None = None,
    seed: int = 0,
) -> bytes:
    """合成文本，返回 44.1kHz 单声道 16-bit WAV 字节。"""
    text = (text or "").strip()
    if not text:
        raise MingTTSError("文本为空")
    model = _ensure_model(precision, data_root)
    try:
        wav = model.synthesize(text, instruction=instruction or None, seed=seed)
    except Exception as e:  # noqa: BLE001
        raise MingTTSError(f"Ming TTS 合成失败：{e}") from e
    arr = np.clip(np.asarray(wav, dtype="float32"), -1.0, 1.0)
    pcm = (arr * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
