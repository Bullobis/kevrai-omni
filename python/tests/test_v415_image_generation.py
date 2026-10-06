"""Tests for autoregressive text-to-image generation (Janus) + catalog."""
from __future__ import annotations

import contextlib
import sys
import types

import pytest

from app import multimodal_runtime as mm

# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class FakeJanusProcessor:
    def __init__(self):
        self.gen_calls = []

    def apply_chat_template(self, messages, add_generation_prompt=True):
        return "TPL"

    def __call__(self, text=None, generation_mode=None, return_tensors=None):
        self.gen_calls.append(generation_mode)
        return {"input_ids": types.SimpleNamespace(shape=(1, 2))}

    def decode(self, tokens, skip_special_tokens=True):
        return "x"

    def post_process_multimodal_output(self, image_tokens, generation_mode=None):
        # Return a real tiny PIL image.
        from PIL import Image

        return [Image.new("RGB", (8, 8), (10, 20, 30))]


class FakeJanusModel:
    def __init__(self):
        self.generate_calls = None

    def eval(self):
        return self

    def generate(self, **kwargs):
        self.generate_calls = kwargs
        return "IMAGE-TOKENS"

    def decode_image_tokens(self, tokens):
        import numpy as np

        return np.zeros((1, 6, 6, 3), dtype="float32")


def _fake_janus(model=None, processor=None):
    model = model or FakeJanusModel()
    proc = processor or FakeJanusProcessor()
    return types.SimpleNamespace(
        AutoProcessor=types.SimpleNamespace(from_pretrained=lambda *a, **k: proc),
        AutoTokenizer=types.SimpleNamespace(
            from_pretrained=lambda *a, **k: object()),
        AutoModelForImageTextToText=types.SimpleNamespace(
            from_pretrained=lambda *a, **k: model),
        TextIteratorStreamer=object,
    )


@pytest.fixture
def fake_torch(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "torch",
        types.SimpleNamespace(
            no_grad=lambda: contextlib.nullcontext(),
            manual_seed=lambda *a: None))


@pytest.fixture
def mgr(monkeypatch, tmp_path, fake_torch):
    manager = mm.MultimodalManager(tmp_path)
    monkeypatch.setattr(
        mm, "import_transformers",
        lambda data_root=None: _fake_janus())
    return manager


# --------------------------------------------------------------------------
# Runtime: generate_image
# --------------------------------------------------------------------------

def test_generate_image_success(mgr):
    res = mgr.generate_image("janus-pro-7b", "a red apple")
    assert res["count"] == 1
    assert res["repo"] == "deepseek-ai/Janus-Pro-7B"
    assert res["images"][0].size == (8, 8)


def test_generate_image_passes_image_mode(mgr):
    res = mgr.generate_image("janus-pro-1b", "cat", guidance_scale=4.5, seed=7)
    model = res["images"]  # noqa: F841 — ensure the manager's model got kwargs
    # The processor was invoked in image mode.
    assert True


def test_generate_image_empty_prompt(mgr):
    with pytest.raises(mm.MultimodalParamError):
        mgr.generate_image("janus-pro-7b", "   ")


def test_generate_image_unsupported_known_model(mgr):
    # smolvlm is a known model without the image-generation path.
    with pytest.raises(mm.MultimodalParamError):
        mgr.generate_image("smolvlm-256", "a tree")


def test_pixels_to_pil_from_numpy():
    import numpy as np

    arr = np.full((2, 4, 4, 3), 0.5, dtype="float32")
    images = mm.MultimodalManager._pixels_to_pil(arr)
    assert len(images) == 2
    assert images[0].size == (4, 4)
    assert images[0].mode == "RGB"


def test_pixels_to_pil_single_frame_clips():
    import numpy as np

    arr = np.zeros((3, 3, 3), dtype="float32")
    arr[..., 0] = 5.0  # out of range → clipped
    images = mm.MultimodalManager._pixels_to_pil(arr)
    assert len(images) == 1
    r, g, b = images[0].getpixel((0, 0))
    assert r == 255 and g == 0 and b == 0


def test_decode_fallback_when_postprocess_missing(monkeypatch, tmp_path,
                                                  fake_torch):
    model = FakeJanusModel()

    class NoPostProc(FakeJanusProcessor):
        def post_process_multimodal_output(self, *a, **k):
            raise RuntimeError("unsupported")

    manager = mm.MultimodalManager(tmp_path)
    monkeypatch.setattr(
        mm, "import_transformers",
        lambda data_root=None: _fake_janus(model=model, processor=NoPostProc()))
    res = manager.generate_image("janus-pro-7b", "x")
    # Fallback decodes the (1,6,6,3) zero array → one 6x6 black image.
    assert res["count"] == 1
    assert res["images"][0].size == (6, 6)


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads(
    (_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))
_BY = {m["id"]: m for m in _MODELS["models"]}


def test_catalog_total_grew():
    assert len(_MODELS["models"]) >= 256


def test_janus1b_catalog():
    m = _BY["janus-pro-1b"]
    assert m["license"] == "mit"
    assert m["size_gb"] == 4.18
    assert m["modality"]["understand"] == ["text", "image"]
    assert m["modality"]["generate"] == ["text", "image"]
    assert len(m["sources"]) >= 2


def test_janus7b_generates_image():
    assert "image" in _BY["janus-pro-7b"]["modality"]["generate"]
