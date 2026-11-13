"""Unit-level checks for the generic media adapters (no model weights)."""
from __future__ import annotations

import wave

import numpy as np
import pytest
from PIL import Image

from app import media_runtime


def test_write_audio_produces_a_valid_wav(tmp_path):
    out = tmp_path / "a.wav"
    media_runtime._write_audio(np.zeros(1600, dtype=np.float32), 16000, out)
    with wave.open(str(out), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 1600


def test_frames_to_array_accepts_pil_list():
    frames = [Image.new("RGB", (8, 8), "red"), Image.new("RGB", (8, 8), "blue")]
    arr = media_runtime._frames_to_array(frames)
    assert len(arr) == 2
    assert arr[0].size == (8, 8)


def test_frames_to_array_accepts_channel_first_video_tensor():
    # (C, F, H, W) is what many diffusers video pipelines return.
    frames = np.zeros((3, 4, 16, 16), dtype=np.float32)
    frames[:, 1] = 1.0
    arr = media_runtime._frames_to_array(frames)
    assert len(arr) == 4
    assert arr[0].size == (16, 16)


def test_generate_video_rejects_bad_format_before_importing_torch():
    with pytest.raises(media_runtime.MediaParamError):
        media_runtime.generate_video("m", "p", output_format="webm")


def test_generate_audio_requires_prompt():
    with pytest.raises(media_runtime.MediaParamError):
        media_runtime.generate_audio("m", "  ")


def test_call_with_compatible_kwargs_falls_back():
    class Pipe:
        def __call__(self, **kwargs):
            if "audio_length_in_s" in kwargs:
                raise TypeError("unexpected keyword")
            return {"ok": True, "args": kwargs}

    out = media_runtime._call_with_compatible_kwargs(
        Pipe(), [{"audio_length_in_s": 5}, {"duration": 5}]
    )
    assert out["ok"] is True
    assert out["args"] == {"duration": 5}
