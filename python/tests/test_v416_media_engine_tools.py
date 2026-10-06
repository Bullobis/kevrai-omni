"""Tests for the agent media-engine tools (driving on-device runtimes)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.tool_registry import ToolContext
from app.agent.tools import media_engine_tools as met

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeAsr:
    def transcribe(self, repo, audio, *, language=None):
        return {"text": "hello world", "language": "en",
                "segments": [{"id": 0, "text": "hello world"}]}


class FakeEmbed:
    def embed(self, repo, texts, *, normalize_embeddings=False):
        return {"dimensions": 4, "embeddings": [[0.1, 0.2, 0.3, 0.4]
                                                for _ in texts]}


class FakeDemucs:
    def separate(self, model, input_path):
        return {"output_dir": "/out", "stems": [
            {"name": "vocals", "path": "/out/vocals.wav"},
            {"name": "drums", "path": "/out/drums.wav"}]}


class FakePiper:
    def synthesize(self, voice, text, length_scale=None):
        return {"path": "/out/a.wav", "duration_s": 1.2}


class FakeMultimodal:
    def chat(self, model, prompt, images=None, **kw):
        return {"text": "a red circle", "multimodal": bool(images)}

    def generate_image(self, model, prompt, **kw):
        from PIL import Image

        n = kw.get("num_images", 1)
        return {"images": [Image.new("RGB", (6, 6)) for _ in range(n)]}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(met, "default_data_root", lambda: tmp_path)
    c = ToolContext()
    c.extra["_media_managers"] = {
        "asr": FakeAsr(),
        "embedding": FakeEmbed(),
        "demucs": FakeDemucs(),
        "piper": FakePiper(),
        "multimodal": FakeMultimodal(),
    }
    return c


def _run(tool, params, ctx):
    return tool.execute(params, ctx)


# ---------------------------------------------------------------------------
# ASR
# ---------------------------------------------------------------------------

def test_asr_happy(ctx):
    audio = Path(str(met.default_data_root())) / "a.wav"
    audio.write_bytes(b"x")
    out = _run(met.asr_transcribe, {"audio_path": str(audio)}, ctx)
    assert out["ok"] and out["text"] == "hello world"
    assert out["language"] == "en"


def test_asr_missing_path(ctx):
    out = _run(met.asr_transcribe, {}, ctx)
    assert "error" in out and "audio_path" in out["error"]


def test_asr_nonexistent_file(ctx):
    out = _run(met.asr_transcribe, {"audio_path": "/nope/x.wav"}, ctx)
    assert "error" in out and "not found" in out["error"]


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

def test_embed_list(ctx):
    out = _run(met.embed_text, {"texts": ["cat", "kitten"]}, ctx)
    assert out["ok"] and out["count"] == 2 and out["dimensions"] == 4
    vec = Path(out["vectors_path"])
    assert vec.exists() and "embeddings" in vec.read_text()


def test_embed_single_string(ctx):
    out = _run(met.embed_text, {"texts": "hello"}, ctx)
    assert out["ok"] and out["count"] == 1


def test_embed_invalid(ctx):
    out = _run(met.embed_text, {"texts": ["", "  "]}, ctx)
    assert "error" in out


# ---------------------------------------------------------------------------
# Separation
# ---------------------------------------------------------------------------

def test_separate(ctx):
    audio = Path(str(met.default_data_root())) / "song.mp3"
    audio.write_bytes(b"x")
    out = _run(met.separate_audio, {"audio_path": str(audio)}, ctx)
    assert out["ok"] and out["stems"] == ["/out/vocals.wav", "/out/drums.wav"]
    assert out["stem_names"] == ["vocals", "drums"]


def test_separate_missing(ctx):
    out = _run(met.separate_audio, {}, ctx)
    assert "error" in out


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

def test_tts(ctx):
    out = _run(met.tts_speak, {"text": "hi there"}, ctx)
    assert out["ok"] and out["audio_path"] == "/out/a.wav"


def test_tts_empty(ctx):
    out = _run(met.tts_speak, {"text": "  "}, ctx)
    assert "error" in out


# ---------------------------------------------------------------------------
# Multimodal understand + generate
# ---------------------------------------------------------------------------

def test_vision_with_image(ctx):
    img = Path(str(met.default_data_root())) / "i.png"
    img.write_bytes(b"x")
    out = _run(met.vision_ask,
               {"question": "what shape?", "image_path": str(img)}, ctx)
    assert out["ok"] and out["text"] == "a red circle" and out["multimodal"]


def test_vision_text_only(ctx):
    out = _run(met.vision_ask, {"question": "hello"}, ctx)
    assert out["ok"] and not out["multimodal"]


def test_gen_image(ctx):
    out = _run(met.gen_image, {"prompt": "a tree", "num_images": 2}, ctx)
    assert out["ok"] and out["count"] == 2
    assert all(Path(p).exists() for p in out["images"])


def test_gen_image_empty(ctx):
    out = _run(met.gen_image, {"prompt": ""}, ctx)
    assert "error" in out


# ---------------------------------------------------------------------------
# Chaining: an output path is a valid input to the next tool
# ---------------------------------------------------------------------------

def test_chain_transcribe_then_embed(ctx):
    # 1) transcribe a real audio file
    audio = Path(str(met.default_data_root())) / "a.wav"
    audio.write_bytes(b"x")
    t = _run(met.asr_transcribe, {"audio_path": str(audio)}, ctx)
    # 2) feed recognized text into embeddings
    e = _run(met.embed_text, {"texts": [t["text"]]}, ctx)
    assert e["ok"] and e["count"] == 1 and Path(e["vectors_path"]).exists()


def test_media_tools_registered_in_skill():
    from app.agent.tools import BUILTIN_SKILLS

    skill = next(s for s in BUILTIN_SKILLS if s.id == "media_engines")
    names = [t.name for t in skill.tools]
    assert names == ["asr_transcribe", "embed_text", "separate_audio",
                     "tts_speak", "vision_ask", "gen_image"]
