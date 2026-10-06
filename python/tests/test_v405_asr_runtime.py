"""Tests for the ASR runtime (app.asr_runtime) — Faster Whisper.

No faster-whisper/ctranslate2/GPU required: the engine is faked via a small
stub and injected through ``import_faster_whisper``. Covers path resolution,
response rendering, parameter validation, the transcribe happy path, and the
FastAPI routes (multipart + local).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import asr_runtime  # noqa: E402


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class _FakeInfo:
    def __init__(self, language="en", language_probability=0.99, duration=12.5):
        self.language = language
        self.language_probability = language_probability
        self.duration = duration


class _FakeSeg:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


class _FakeModel:
    def __init__(self, *a, **kw):
        self.kw = kw

    def transcribe(self, audio, **kw):
        segs = [
            _FakeSeg(0.0, 1.5, " Hello"),
            _FakeSeg(1.5, 3.0, " world."),
        ]
        return iter(segs), _FakeInfo()


class _FakeFW(types.ModuleType):
    __version__ = "1.2.1"
    WhisperModel = _FakeModel


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def test_engine_target_dir(tmp_path):
    p = asr_runtime.engine_target_dir(tmp_path)
    assert p == tmp_path / "engines" / "pip-faster-whisper"


def test_capabilities_not_installed(tmp_path):
    cap = asr_runtime.capabilities(tmp_path)
    assert cap["installed"] is False
    assert cap["engine"] == "faster-whisper"


def test_resolve_model_source_missing(tmp_path):
    out = asr_runtime.resolve_model_source(
        "Systran/faster-whisper-large-v3", tmp_path, tmp_path / "downloads"
    )
    assert out == "Systran/faster-whisper-large-v3"


def test_resolve_model_source_local(tmp_path):
    dl = tmp_path / "downloads"
    d = dl / "hub" / "hf" / "Systran__faster-whisper-large-v3"
    d.mkdir(parents=True)
    (d / "model.bin").write_bytes(b"x")
    (d / "config.json").write_text("{}")
    out = asr_runtime.resolve_model_source(
        "Systran/faster-whisper-large-v3", tmp_path, dl
    )
    assert out == d


def test_bootstrap_adds_path(tmp_path):
    target = tmp_path / "engines" / "pip-faster-whisper"
    target.mkdir(parents=True)
    asr_runtime._bootstrap_sys_path(tmp_path)
    assert str(target) in sys.path


# --------------------------------------------------------------------------
# Response rendering
# --------------------------------------------------------------------------


def _result():
    return {
        "text": "Hello world.",
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.5, "text": " Hello"},
            {"id": 1, "start": 1.5, "end": 3.0, "text": " world."},
        ],
    }


def test_render_json():
    body, media = asr_runtime.render_response(_result(), "json")
    assert "Hello world." in body and media == "application/json"


def test_render_text():
    body, media = asr_runtime.render_response(_result(), "text")
    assert body == "Hello world." and media.startswith("text/plain")


def test_render_srt():
    body, media = asr_runtime.render_response(_result(), "srt")
    assert "00:00:00,000 --> 00:00:01,500" in body and media.endswith("utf-8")


def test_render_vtt():
    body, media = asr_runtime.render_response(_result(), "vtt")
    assert body.startswith("WEBVTT") and "00:00:00.000 --> 00:00:01.500" in body


def test_render_verbose():
    body, media = asr_runtime.render_response(_result(), "verbose_json")
    assert '"segments"' in body


def test_render_bad_format():
    with pytest.raises(asr_runtime.AsrParamError):
        asr_runtime.render_response(_result(), "docx")


# --------------------------------------------------------------------------
# Parameter validation
# --------------------------------------------------------------------------


def _mgr(monkeypatch, tmp_path):
    mgr = asr_runtime.AsrManager(tmp_path)
    monkeypatch.setattr(asr_runtime, "import_faster_whisper",
                        lambda data_root=None: _FakeFW("fw"))
    return mgr


def test_bad_repo(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(asr_runtime.AsrParamError):
        mgr.transcribe("no-slug", b"x")


def test_bad_language(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(asr_runtime.AsrParamError):
        mgr.transcribe("a/b", b"x", language="xx")


def test_bad_task(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(asr_runtime.AsrParamError):
        mgr.transcribe("a/b", b"x", task="summarize")


def test_bad_beam(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(asr_runtime.AsrParamError):
        mgr.transcribe("a/b", b"x", beam_size=0)


def test_missing_audio_file(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(asr_runtime.AsrParamError):
        mgr.transcribe("a/b", str(tmp_path / "nope.wav"))


# --------------------------------------------------------------------------
# Transcribe happy path
# --------------------------------------------------------------------------


def test_transcribe_bytes(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    out = mgr.transcribe("Systran/faster-whisper-large-v3", b"RIFFxxxx")
    assert out["text"] == "Hello world."
    assert out["language"] == "en"
    assert out["duration"] == 12.5
    assert len(out["segments"]) == 2


def test_transcribe_path(monkeypatch, tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    mgr = _mgr(monkeypatch, tmp_path)
    out = mgr.transcribe("Systran/faster-whisper-large-v3", wav)
    assert out["segments"][0]["start"] == 0.0


def test_model_load_failure(monkeypatch, tmp_path):
    class Boom(_FakeFW):
        def WhisperModel(self, *a, **kw):
            raise RuntimeError("native crash")

    mgr = asr_runtime.AsrManager(tmp_path)
    monkeypatch.setattr(asr_runtime, "import_faster_whisper",
                        lambda data_root=None: Boom("fw"))
    with pytest.raises(asr_runtime.AsrModelError):
        mgr.transcribe("a/b", b"x")


# --------------------------------------------------------------------------
# FastAPI routes
# --------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from app import main

    fake_mgr = asr_runtime.AsrManager(tmp_path)
    monkeypatch.setattr(asr_runtime, "import_faster_whisper",
                        lambda data_root=None: _FakeFW("fw"))
    main.app.state.asr = fake_mgr
    return TestClient(main.app)


def test_route_capabilities(client):
    r = client.get("/api/asr/capabilities")
    assert r.status_code == 200 and r.json()["engine"] == "faster-whisper"


def test_route_transcriptions_multipart(client):
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", b"RIFFdata", "audio/wav")},
        data={"model": "Systran/faster-whisper-large-v3", "response_format": "json"},
    )
    assert r.status_code == 200
    assert r.json()["text"] == "Hello world."


def test_route_translations_multipart(client):
    r = client.post(
        "/v1/audio/translations",
        files={"file": ("a.wav", b"RIFFdata", "audio/wav")},
        data={"model": "Systran/faster-whisper-large-v3"},
    )
    assert r.status_code == 200


def test_route_transcribe_local(client, tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    r = client.post(
        "/api/asr/transcribe",
        json={"model": "Systran/faster-whisper-large-v3",
              "audio_path": str(wav), "response_format": "text"},
    )
    assert r.status_code == 200 and r.text == "Hello world."


def test_route_missing_file(client):
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", b"", "audio/wav")},
        data={"model": "a/b"},
    )
    assert r.status_code == 400


def test_route_engine_missing(monkeypatch):
    from starlette.testclient import TestClient

    from app import main

    def boom(data_root=None):
        raise asr_runtime.AsrEngineMissing("not installed")

    monkeypatch.setattr(asr_runtime, "import_faster_whisper", boom)
    main.app.state.asr = asr_runtime.AsrManager()
    r = TestClient(main.app).post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", b"RIFF", "audio/wav")},
        data={"model": "a/b"},
    )
    assert r.status_code == 503
