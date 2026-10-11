"""Tests for native MusicGen text-to-music runtime, agent tool and catalog."""
from __future__ import annotations

import json
import types
from pathlib import Path

import numpy as np
import pytest

from app import music_runtime as mr
from app.agent.tool_registry import ToolContext
from app.agent.tools import media_engine_tools as met

CATALOG = (
    Path(__file__).resolve().parents[2] / "catalog" / "models.json"
)


# ---------------------------------------------------------------------------
# Fakes (no torch needed)
# ---------------------------------------------------------------------------

class FakeAudio:
    def __init__(self, a):
        self.a = a

    def detach(self):
        return self

    def cpu(self):
        return self

    def float(self):
        return self

    def numpy(self):
        return self.a


class FakeAudioValues:
    def __init__(self, a):
        self.a = a

    def __getitem__(self, i):
        return FakeAudio(self.a[i])


class FakeProcessor:
    def __call__(self, **kw):
        return {"input_values": np.zeros((1, 4))}


class FakeModel:
    config = types.SimpleNamespace(
        audio_encoder=types.SimpleNamespace(sampling_rate=32000)
    )

    def __init__(self, channels=2):
        self.channels = channels
        self.last = {}

    def generate(self, **kw):
        self.last = kw
        n = kw["max_new_tokens"]
        return FakeAudioValues(np.zeros((1, self.channels, n), dtype=np.float32))


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """A MusicManager whose load() returns a fake stereo model."""
    mgr = mr.MusicManager(tmp_path)
    model = FakeModel(channels=2)
    proc = FakeProcessor()

    def _load(self, mid):
        return model, proc, self.resolve_repo(mid)

    monkeypatch.setattr(mr.MusicManager, "load", _load)
    return mgr, model


# ---------------------------------------------------------------------------
# WAV helpers
# ---------------------------------------------------------------------------

def test_write_wav_stereo(tmp_path):
    t = np.linspace(0, 0.25, 800, endpoint=False)
    audio = np.stack([0.4 * np.sin(2 * np.pi * 440 * t),
                      0.4 * np.sin(2 * np.pi * 660 * t)])
    import wave

    p = mr.write_wav(tmp_path / "s.wav", audio, 32000)
    with wave.open(p) as w:
        assert w.getnchannels() == 2
        assert w.getframerate() == 32000
        assert w.getsampwidth() == 2
        assert w.getnframes() == 800


def test_write_wav_mono(tmp_path):
    audio = np.zeros(400, dtype=np.float32)
    import wave

    p = mr.write_wav(tmp_path / "m.wav", audio, 16000)
    with wave.open(p) as w:
        assert w.getnchannels() == 1


def test_pcm_clips_overflow(tmp_path):
    # Values beyond +/-1 must be clamped, not wrap around.
    audio = np.full(10, 5.0, dtype=np.float32)
    pcm = np.frombuffer(mr._pcm16(audio), dtype="<i2")
    assert pcm.max() == 32767


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

def test_capabilities_missing_engine(tmp_path):
    cap = mr.capabilities(tmp_path)
    assert cap["installed"] is False
    assert cap["engine"] == "transformers"
    assert len(cap["models"]) == 4
    assert cap["duration_range_s"] == [1, 60]


def test_capabilities_installed(tmp_path):
    (tmp_path / "engines" / "pip-transformers").mkdir(parents=True)
    cap = mr.capabilities(tmp_path)
    assert cap["installed"] is True


# ---------------------------------------------------------------------------
# Resolution / validation
# ---------------------------------------------------------------------------

def test_resolve_repo():
    mgr = mr.MusicManager("/none")
    assert mgr.resolve_repo("musicgen-small") == "facebook/musicgen-small"
    assert mgr.resolve_repo("some/repo") == "some/repo"
    with pytest.raises(mr.MusicParamError):
        mgr.resolve_repo("   ")


def test_synthesize_empty_prompt(patched):
    mgr, _ = patched
    with pytest.raises(mr.MusicParamError):
        mgr.synthesize("musicgen-small", "  ")


def test_synthesize_bad_duration(patched):
    mgr, _ = patched
    for bad in (0, 999):
        with pytest.raises(mr.MusicParamError):
            mgr.synthesize("musicgen-small", "a song", duration_s=bad)


def test_synthesize_bad_guidance(patched):
    mgr, _ = patched
    with pytest.raises(mr.MusicParamError):
        mgr.synthesize("musicgen-small", "a song", guidance_scale=99)


# ---------------------------------------------------------------------------
# Synthesis with a fake model
# ---------------------------------------------------------------------------

def test_synthesize_token_budget(patched):
    mgr, model = patched
    res = mgr.synthesize("musicgen-small", "lofi beats", duration_s=10)
    # 10 s * 50 tokens/s == 500 new tokens.
    assert model.last["max_new_tokens"] == 500
    assert model.last["guidance_scale"] == 3.0
    assert res["sampling_rate"] == 32000
    assert res["repo"] == "facebook/musicgen-small"
    # Stereo audio: [channels, samples].
    assert res["audio"].shape[0] == 2
    assert res["audio"].shape[1] == 500


def test_generate_to_file(patched):
    mgr, _ = patched
    out = Path(mgr.data_root) / "clips" / "a.wav"
    res = mgr.generate_to_file("musicgen-small", "jazz", out, duration_s=4)
    assert Path(res["audio_path"]).exists()
    assert res["sampling_rate"] == 32000


# ---------------------------------------------------------------------------

class FakeMusic:
    def generate_to_file(self, model, prompt, out_path, **kw):
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFF")
        return {"audio_path": str(p), "sampling_rate": 32000,
                "repo": "facebook/musicgen-small", "requested_s": 15,
                "elapsed_s": 1.0}


@pytest.fixture
def agent_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(met, "default_data_root", lambda: tmp_path)
    c = ToolContext()
    c.extra["_media_managers"] = {"music": FakeMusic()}
    return c


def test_agent_generate_music(agent_ctx):
    out = met.generate_music.execute(
        {"prompt": "calm piano", "duration_s": 8}, agent_ctx
    )
    assert out["ok"] and Path(out["audio_path"]).exists()
    assert out["sampling_rate"] == 32000


def test_agent_generate_music_no_prompt(agent_ctx):
    out = met.generate_music.execute({}, agent_ctx)
    assert "error" in out


def test_catalog_musicgen_entries():
    data = json.loads(CATALOG.read_text())
    by_id = {m["id"]: m for m in data["models"]}
    for mid in ("musicgen-small", "musicgen-medium",
                "musicgen-large", "musicgen-stereo"):
        m = by_id[mid]
        assert "transformers" in m["engine"]
        assert m["size_gb"] > 0
        assert m["license"] == "CC-BY-NC-4.0"
        assert len(m["sources"]) >= 2
        assert m["hardware"]["disk_gb"] > 0
