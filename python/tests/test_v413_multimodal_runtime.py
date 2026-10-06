"""Tests for the generic transformers multimodal runtime (fake injection)."""
from __future__ import annotations

import contextlib
import sys
import types

import pytest

from app import multimodal_runtime as mm

# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class FakeIds:
    shape = (1, 3)


class FakeProcessor:
    def apply_chat_template(self, messages, add_generation_prompt=True):
        return "RENDERED-PROMPT"

    def __call__(self, text, images=None, return_tensors=None):
        return {"input_ids": FakeIds()}

    def decode(self, tokens, skip_special_tokens=True):
        return "GEN-TOKENS"


class FailingProcessor(FakeProcessor):
    def apply_chat_template(self, messages, add_generation_prompt=True):
        raise RuntimeError("no template")


class FakeRow:
    def __getitem__(self, sl):
        return "GEN-TOKENS"


class FakeOutput:
    def __getitem__(self, i):
        return FakeRow()


class FakeModel:
    def __init__(self):
        self.evaled = False

    def eval(self):
        self.evaled = True
        return self

    def generate(self, **kwargs):
        return FakeOutput()


class BoomModel(FakeModel):
    def generate(self, **kwargs):
        raise RuntimeError("kernel exploded")


class FakeStreamer:
    def __init__(self, *a, **k):
        self._items = iter(["Hello", " world"])

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._items)


def _fake_transformers(model=None, processor=None):
    model = model or FakeModel()

    def _load_model(*a, **k):
        if isinstance(model, Exception):
            raise model
        return model

    return types.SimpleNamespace(
        AutoProcessor=types.SimpleNamespace(
            from_pretrained=lambda *a, **k: processor or FakeProcessor()),
        AutoTokenizer=types.SimpleNamespace(
            from_pretrained=lambda *a, **k: object()),
        AutoModelForImageTextToText=types.SimpleNamespace(
            from_pretrained=_load_model),
        TextIteratorStreamer=FakeStreamer,
    )


@pytest.fixture
def fake_torch(monkeypatch):
    torch = types.SimpleNamespace(
        no_grad=lambda: contextlib.nullcontext())
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


@pytest.fixture
def mgr(monkeypatch, tmp_path, fake_torch):
    manager = mm.MultimodalManager(tmp_path)
    monkeypatch.setattr(
        mm, "import_transformers",
        lambda data_root=None: _fake_transformers())
    return manager


def make_png(tmp_path):
    p = tmp_path / "pic.png"
    # 1x1 red PNG (minimal).
    p.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
            "53de0000000c4944415408d763f8ffff3f0005fe02fe7cc8b266000000004945"
            "4e44ae426082"))
    return str(p)


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def test_resolve_repo_known():
    m = mm.MultimodalManager.__new__(mm.MultimodalManager)
    assert m.resolve_repo("janus-pro-7b") == "deepseek-ai/Janus-Pro-7B"
    assert m.resolve_repo("org/custom") == "org/custom"


def test_resolve_repo_empty():
    m = mm.MultimodalManager.__new__(mm.MultimodalManager)
    with pytest.raises(mm.MultimodalParamError):
        m.resolve_repo("  ")


# --------------------------------------------------------------------------
# Chat (non-stream)
# --------------------------------------------------------------------------

def test_chat_text_only(mgr):
    res = mgr.chat("smolvlm-256", "What is this?")
    assert res["text"] == "GEN-TOKENS"
    assert res["multimodal"] is False
    assert res["elapsed_s"] >= 0


def test_chat_with_image(monkeypatch, tmp_path, fake_torch):
    manager = mm.MultimodalManager(tmp_path)
    monkeypatch.setattr(
        mm, "import_transformers",
        lambda data_root=None: _fake_transformers())
    img = make_png(tmp_path)
    res = manager.chat("smolvlm-256", "describe", images=[img])
    assert res["multimodal"] is True


def test_chat_empty_inputs(mgr):
    with pytest.raises(mm.MultimodalParamError):
        mgr.chat("smolvlm-256", "   ")


def test_chat_missing_image(mgr):
    with pytest.raises(mm.MultimodalParamError):
        mgr.chat("smolvlm-256", "x", images=["/nope/missing.png"])


def test_chat_generation_failure(monkeypatch, tmp_path, fake_torch):
    manager = mm.MultimodalManager(tmp_path)
    monkeypatch.setattr(
        mm, "import_transformers",
        lambda data_root=None: _fake_transformers(model=BoomModel()))
    with pytest.raises(mm.MultimodalModelError):
        manager.chat("smolvlm-256", "hi")


def test_chat_template_fallback(monkeypatch, tmp_path, fake_torch):
    manager = mm.MultimodalManager(tmp_path)
    monkeypatch.setattr(
        mm, "import_transformers",
        lambda data_root=None: _fake_transformers(
            processor=FailingProcessor()))
    res = manager.chat("smolvlm-256", "hi")
    assert res["text"] == "GEN-TOKENS"


def test_model_load_failure(monkeypatch, tmp_path, fake_torch):
    manager = mm.MultimodalManager(tmp_path)
    monkeypatch.setattr(
        mm, "import_transformers",
        lambda data_root=None: _fake_transformers(
            model=RuntimeError("unknown arch")))
    with pytest.raises(mm.MultimodalModelError):
        manager.chat("smolvlm-256", "hi")


# --------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------

def test_chat_stream(monkeypatch, tmp_path, fake_torch):
    manager = mm.MultimodalManager(tmp_path)
    monkeypatch.setattr(
        mm, "import_transformers",
        lambda data_root=None: _fake_transformers())
    pieces = list(manager.chat_stream("smolvlm-256", "hi"))
    assert "".join(pieces) == "Hello world"


def test_chat_stream_empty_input(mgr):
    with pytest.raises(mm.MultimodalParamError):
        list(mgr.chat_stream("smolvlm-256", ""))


# --------------------------------------------------------------------------
# Capabilities
# --------------------------------------------------------------------------

def test_capabilities_installed(monkeypatch, tmp_path):
    target = mm.engine_target_dir(tmp_path)
    target.mkdir(parents=True)
    monkeypatch.setattr(mm, "engine_target_dir", lambda root=None: target)
    # metadata.version would fail; cap probe stays non-fatal → version None.
    cap = mm.capabilities(tmp_path)
    assert cap["installed"] is True
    assert cap["engine"] == "transformers"
    ids = [m["id"] for m in cap["models"]]
    assert "janus-pro-7b" in ids


def test_capabilities_not_installed(tmp_path):
    cap = mm.capabilities(tmp_path)
    assert cap["installed"] is False
