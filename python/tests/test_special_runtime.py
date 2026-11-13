"""Validation-level checks for specialized adapters (no model weights)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app import special_runtime


def test_tts_requires_text():
    with pytest.raises(special_runtime.SpecialParamError):
        special_runtime.synthesize("m", " ", output_dir=Path("."))


def test_tts_writes_a_wav_with_a_stubbed_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(
        special_runtime, "_pipeline_tts",
        lambda source, text: (np.zeros(1600, dtype=np.float32), 16000),
    )
    out = special_runtime.synthesize(
        "canopylabs/orpheus-3b-0.1-ft", "hello", output_dir=tmp_path
    )
    assert out["sampling_rate"] == 16000
    assert Path(out["output_path"]).exists()


def test_superres_rejects_empty_image():
    with pytest.raises(special_runtime.SpecialParamError):
        special_runtime.upscale("model", b"")


def test_faces_rejects_empty_image():
    with pytest.raises(special_runtime.SpecialParamError):
        special_runtime.faces("model", b"")


def test_detect_rejects_empty_image():
    with pytest.raises(special_runtime.SpecialParamError):
        special_runtime.detect("model", b"")


def test_audio_chat_requires_path():
    with pytest.raises(special_runtime.SpecialParamError):
        special_runtime.audio_chat("model", "", "what?")


def test_diarize_requires_path():
    with pytest.raises(special_runtime.SpecialParamError):
        special_runtime.diarize("model", "")


def test_decide_requires_text_and_labels():
    with pytest.raises(special_runtime.SpecialParamError):
        special_runtime.decide("model", " ", {"intent": ["a"]})
    with pytest.raises(special_runtime.SpecialParamError):
        special_runtime.decide("model", "text", {})


def test_laya_requires_questions():
    with pytest.raises(special_runtime.SpecialParamError):
        special_runtime.laya_decide("model", "text", {})
