"""Tests for the Piper TTS runtime (app.piper_runtime).

Piper/onnxruntime are faked and injected through ``import_piper`` and
``_download``; covers voice resolution, the output manifest, parameter
validation, the JSON route, the stream route and its job-id validation.
"""

from __future__ import annotations

import sys
import types
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import piper_runtime as prt  # noqa: E402

# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeConfig:
    def __init__(self):
        self.sample_rate = 22050
        self.length_scale = 1.0


class FakeVoice:
    def __init__(self):
        self.config = FakeConfig()

    def synthesize_wav(self, text, wav_writer, syn_config=None):
        wav_writer.setframerate(22050)
        wav_writer.setsampwidth(2)
        wav_writer.setnchannels(1)
        # 0.5 s of silence.
        wav_writer.writeframes(b"\x00\x00" * (22050 // 2))
        return None

    # Kept to exercise the legacy fallback path in one dedicated test.
    def synthesize(self, text, wav_writer):
        self.synthesize_wav(text, wav_writer)


FAKE_PIPER = types.SimpleNamespace(
    __version__="1.8.0",
    config=types.SimpleNamespace(SynthesisConfig=lambda **k: object()),
    PiperVoice=types.SimpleNamespace(load=lambda *a, **k: FakeVoice()),
)


# --------------------------------------------------------------------------
# Helpers + capabilities
# --------------------------------------------------------------------------


def test_engine_target_dir(tmp_path):
    assert prt.engine_target_dir(tmp_path) == tmp_path / "engines" / "pip-piper"


def test_capabilities_not_installed(tmp_path):
    cap = prt.capabilities(tmp_path)
    assert cap["installed"] is False and cap["engine"] == "piper"
    assert len(cap["voices"]) == len(prt.KNOWN_VOICES)


def _mgr(monkeypatch, tmp_path):
    mgr = prt.PiperManager(tmp_path)
    monkeypatch.setattr(prt, "import_piper", lambda data_root=None: FAKE_PIPER)

    def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"onnx" if dest.suffix == ".onnx" else b"{}")
        return dest

    monkeypatch.setattr(prt, "_download", fake_download)
    return mgr


# --------------------------------------------------------------------------
# Manifest + validation
# --------------------------------------------------------------------------


def test_synthesize_manifest(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    out = mgr.synthesize("en_US-lessac-medium", "Hello world.")
    assert Path(out["path"]).is_file()
    assert out["sample_rate"] == 22050
    assert out["duration_s"] == 0.5
    with wave.open(out["path"]) as rd:
        assert rd.getframerate() == 22050


def test_empty_text(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(prt.PiperParamError):
        mgr.synthesize("en_US-lessac-medium", "   ")


def test_text_too_long(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(prt.PiperParamError):
        mgr.synthesize("en_US-lessac-medium", "x" * (prt.MAX_TEXT_LEN + 1))


def test_bad_length_scale(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(prt.PiperParamError):
        mgr.synthesize("en_US-lessac-medium", "hi", length_scale=9.0)


def test_unknown_voice(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(prt.PiperParamError):
        mgr.synthesize("fr_FR-nope", "bonjour")


def test_voice_load_failure(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise RuntimeError("onnx crash")

    bad = types.SimpleNamespace(PiperVoice=types.SimpleNamespace(load=boom))
    monkeypatch.setattr(prt, "import_piper", lambda data_root=None: bad)
    with pytest.raises(prt.PiperVoiceError):
        mgr.synthesize("en_US-lessac-medium", "hi")


def test_synthesize_failure(monkeypatch, tmp_path):
    class BoomVoice(FakeVoice):
        def synthesize_wav(self, text, wav_writer, syn_config=None):
            raise RuntimeError("decode error")

    piper = types.SimpleNamespace(
        config=types.SimpleNamespace(SynthesisConfig=lambda **k: object()),
        PiperVoice=types.SimpleNamespace(load=lambda *a, **k: BoomVoice()),
    )
    mgr = _mgr(monkeypatch, tmp_path)
    monkeypatch.setattr(prt, "import_piper", lambda data_root=None: piper)
    with pytest.raises(prt.PiperVoiceError):
        mgr.synthesize("en_US-lessac-medium", "hi")


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from app import main

    monkeypatch.setattr(prt, "import_piper", lambda data_root=None: FAKE_PIPER)

    def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"onnx" if dest.suffix == ".onnx" else b"{}")
        return dest

    monkeypatch.setattr(prt, "_download", fake_download)
    main.app.state.piper = prt.PiperManager(tmp_path)
    return TestClient(main.app)


def test_route_capabilities(client):
    r = client.get("/api/tts-piper/capabilities")
    assert r.status_code == 200 and r.json()["engine"] == "piper"


def test_route_synthesize(client):
    r = client.post(
        "/api/tts-piper/synthesize", json={"voice_id": "en_US-lessac-medium", "text": "Hello there."}
    )
    assert r.status_code == 200 and r.json()["sample_rate"] == 22050


def test_route_stream(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from app import main

    monkeypatch.setattr(main, "APP_ROOT", tmp_path)
    d = tmp_path / "tts" / "piper"
    d.mkdir(parents=True)
    (d / "job1.wav").write_bytes(b"RIFF")
    r = TestClient(main.app).get("/api/tts-piper/stream", params={"job_id": "job1"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"


def test_route_stream_bad_job(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from app import main

    r = TestClient(main.app).get("/api/tts-piper/stream", params={"job_id": "../../etc"})
    assert r.status_code == 400


def test_route_engine_missing(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from app import main

    def boom(data_root=None):
        raise prt.PiperEngineMissing("not installed")

    monkeypatch.setattr(prt, "import_piper", boom)
    main.app.state.piper = prt.PiperManager(tmp_path)
    r = TestClient(main.app).post(
        "/api/tts-piper/synthesize", json={"voice_id": "en_US-lessac-medium", "text": "hi"}
    )
    assert r.status_code == 503


# --------------------------------------------------------------------------
# 引擎缺失时不得先去下载语音模型
# --------------------------------------------------------------------------


def test_engine_missing_short_circuits_before_voice_download(monkeypatch, tmp_path):
    """引擎没装时，必须先报 503，**一次下载都不该发起**。

    守的是 ``synthesize()`` 里的调用顺序：``import_piper()`` 必须在
    ``load_voice()`` 之前。反过来的话，引擎缺失会先撞上语音下载失败，
    变成 422 "download failed"——报错指向网络，真正原因（没装引擎）
    被掩盖，用户还白等一次几十 MB 的下载。

    这里用 spy 记录 load_voice 是否被调用：没被调用 = 确实短路了。
    """
    calls: list[str] = []

    def boom(data_root=None):
        raise prt.PiperEngineMissing("not installed")

    def spy_load_voice(voice_id, use_cuda=False):
        calls.append(voice_id)
        raise AssertionError("load_voice 不应被调用：引擎缺失应先短路")

    monkeypatch.setattr(prt, "import_piper", boom)
    monkeypatch.setattr(prt.PiperManager, "load_voice", spy_load_voice)

    mgr = prt.PiperManager(tmp_path)
    with pytest.raises(prt.PiperEngineMissing):
        mgr.synthesize("en_US-lessac-medium", "hello")

    assert calls == [], f"引擎缺失时仍发起了语音下载/加载：{calls}"


def test_engine_present_still_proceeds_to_voice_load(monkeypatch, tmp_path):
    """反向验证：引擎装了的情况下，仍然要走到 load_voice。

    只测"短路"是不够的——如果把 import_piper 提前后又不走后续逻辑，
    上一条测试也会过，但功能就废了。这条确保正向路径没被破坏。
    """
    calls: list[str] = []

    monkeypatch.setattr(prt, "import_piper", lambda data_root=None: types.SimpleNamespace())
    monkeypatch.setattr(
        prt.PiperManager, "load_voice",
        lambda self, voice_id, use_cuda=False: (calls.append(voice_id) or object()),
    )
    # 后续的 wave 写出会失败，这里只关心"是否走到了 load_voice"
    mgr = prt.PiperManager(tmp_path)
    with pytest.raises(Exception):  # noqa: B017 - 后面步骤失败无所谓
        mgr.synthesize("en_US-lessac-medium", "hello")

    assert calls == ["en_US-lessac-medium"], f"引擎可用时未走到 load_voice：{calls}"
