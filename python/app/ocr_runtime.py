"""OCR runtime backed by RapidOCR (ONNX Runtime CPU), installed on demand.

RapidOCR (https://github.com/RapidAI/RapidOCR, Apache-2.0) bundles small
detection/recognition models and runs fully offline on CPU. Like the other
optional engines it is installed with ``pip --target`` into
``<data_root>/engines/pip-rapidocr`` and loaded by prepending that directory
to ``sys.path``. When it is not installed, callers get an actionable
:class:`OCREngineMissing` instead of a raw import error.

Public API:
    manager = OCRManager(data_root)
    text = manager.extract(image_bytes)   # recognised lines joined with \\n
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from .settings import default_data_root

log = logging.getLogger("kevrai.ocr")

ENGINE_ID = "rapidocr"
_PIP_DIR = "pip-rapidocr"


class OCRError(Exception):
    """Base error for the OCR runtime."""


class OCREngineMissing(OCRError):
    """Raised when the RapidOCR engine is not installed/loadable."""


class OCRModelError(OCRError):
    """Raised when an image cannot be read or OCR fails at inference."""


class OCRParamError(OCRError):
    """Raised for invalid OCR input."""


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


def import_rapidocr(data_root: Path | None = None) -> Any:
    """Import and return the ``rapidocr`` module.

    Raises :class:`OCREngineMissing` with an actionable message when the
    engine or its native backend (onnxruntime) cannot load.
    """
    _bootstrap_sys_path(data_root)
    try:
        import rapidocr  # type: ignore

        return rapidocr
    except Exception as e:  # noqa: BLE001 — surface any native load failure
        raise OCREngineMissing(
            "RapidOCR 引擎未安装或无法加载，请先在引擎页安装 RapidOCR"
            f"（含 onnxruntime）。原始错误：{e}"
        ) from e


def _decode_image(image_bytes: bytes) -> Any:
    """Decode image bytes into an RGB numpy array via Pillow."""
    if not image_bytes:
        raise OCRParamError("image content is empty")
    try:
        import io

        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        return np.asarray(img.convert("RGB"))
    except OCRParamError:
        raise
    except Exception as e:  # noqa: BLE001 — unknown / corrupt image format
        raise OCRModelError(
            f"无法读取该图片（格式不支持或已损坏）：{e}"
        ) from e


class OCRManager:
    """Owns a cached RapidOCR engine; safe to share (load/generate serialized)."""

    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = Path(data_root) if data_root is not None else default_data_root()
        self._engine: Any = None
        self._lock = threading.Lock()

    def _get_engine(self) -> Any:
        with self._lock:
            if self._engine is None:
                rapidocr = import_rapidocr(self.data_root)
                try:
                    self._engine = rapidocr.RapidOCR()
                except Exception as e:  # noqa: BLE001 — model/backend init failure
                    raise OCRModelError(
                        f"RapidOCR 初始化失败（模型或 onnxruntime 异常）：{e}"
                    ) from e
            return self._engine

    def extract(self, image_bytes: bytes) -> str:
        """Recognise text in an image; return lines joined with newlines.

        An image with no recognised text returns an empty string (callers can
        decide how to handle it).
        """
        arr = _decode_image(image_bytes)
        engine = self._get_engine()
        try:
            with self._lock:
                result = engine(arr)
        except OCREngineMissing:
            raise
        except Exception as e:  # noqa: BLE001 — inference failure
            raise OCRModelError(f"OCR 识别失败：{e}") from e

        txts = getattr(result, "txts", None)
        if not txts:
            return ""
        lines = [str(t).strip() for t in txts if str(t).strip()]
        return "\n".join(lines)
