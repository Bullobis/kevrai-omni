"""Tests for the Demucs separation runtime (app.demucs_runtime).

No demucs/torch required: the engine is faked and injected through
``import_demucs``. Covers path resolution, the output manifest, parameter
validation, the multipart/local routes, the stem stream route and traversal
protection.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import demucs_runtime as drt  # noqa: E402

# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeTensor:
    def __init__(self, channels, samples):
        self.shape = (channels, samples)

    def dim(self):
        return len(self.shape)


class FakeSeparator:
    def __init__(self, model="htdemucs", device="cpu", **kw):
        self.model = model
        self.device = device
        self.samplerate = 44100

    def separate_audio_file(self, path):
        stems = {n: FakeTensor(2, 44100) for n in ("drums", "bass", "other", "vocals")}
        if self.model == "htdemucs_6s":
            stems["piano"] = FakeTensor(2, 44100)
            stems["guitar"] = FakeTensor(2, 44100)
        return FakeTensor(2, 44100), stems


def fake_save_audio(tensor, path, sr):
    Path(path).write_bytes(b"RIFFwav")


FAKE_API = types.SimpleNamespace(Separator=FakeSeparator, save_audio=fake_save_audio)


# --------------------------------------------------------------------------
# Helpers + capabilities
# --------------------------------------------------------------------------


def test_engine_target_dir(tmp_path):
    p = drt.engine_target_dir(tmp_path)
    assert p == tmp_path / "engines" / "pip-demucs"


def test_capabilities_not_installed(tmp_path):
    cap = drt.capabilities(tmp_path)
    assert cap["installed"] is False and cap["engine"] == "demucs"
    assert cap["models"] == list(drt.KNOWN_MODELS)


def test_bootstrap_adds_path(tmp_path):
    target = tmp_path / "engines" / "pip-demucs"
    target.mkdir(parents=True)
    drt._bootstrap_sys_path(tmp_path)
    assert str(target) in sys.path


# --------------------------------------------------------------------------
# Validation + manifest
# --------------------------------------------------------------------------


def _mgr(monkeypatch, tmp_path):
    mgr = drt.DemucsManager(tmp_path)
    monkeypatch.setattr(drt, "import_demucs",
                        lambda data_root=None: FAKE_API)
    return mgr


def _audio(tmp_path):
    p = tmp_path / "song.mp3"
    p.write_bytes(b"ID3data")
    return p


def test_separate_manifest(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    out = mgr.separate("htdemucs", _audio(tmp_path))
    assert out["sample_rate"] == 44100
    assert [s["name"] for s in out["stems"]] == ["bass", "drums", "other", "vocals"]
    for stem in out["stems"]:
        assert Path(stem["path"]).is_file()
        assert stem["duration_s"] == 1.0


def test_separate_6s(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    out = mgr.separate("htdemucs_6s", _audio(tmp_path))
    names = {s["name"] for s in out["stems"]}
    assert {"piano", "guitar"} <= names


def test_bad_model(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(drt.DemucsParamError):
        mgr.separate("", _audio(tmp_path))


def test_bad_shifts_high(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(drt.DemucsParamError):
        mgr.separate("htdemucs", _audio(tmp_path), shifts=21)


def test_bad_overlap(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(drt.DemucsParamError):
        mgr.separate("htdemucs", _audio(tmp_path), overlap=1.0)


def test_missing_input(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(drt.DemucsParamError):
        mgr.separate("htdemucs", tmp_path / "nope.mp3")


def test_model_load_failure(monkeypatch, tmp_path):
    class BoomApi(types.SimpleNamespace):
        def Separator(self, *a, **kw):
            raise RuntimeError("native crash")

    mgr = drt.DemucsManager(tmp_path)
    monkeypatch.setattr(drt, "import_demucs",
                        lambda data_root=None: BoomApi(Separator=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))))
    with pytest.raises(drt.DemucsModelError):
        mgr.separate("htdemucs", _audio(tmp_path))


def test_separate_runtime_failure(monkeypatch, tmp_path):
    class BoomSep(FakeSeparator):
        def separate_audio_file(self, path):
            raise RuntimeError("boom")

    api = types.SimpleNamespace(Separator=BoomSep, save_audio=fake_save_audio)
    mgr = drt.DemucsManager(tmp_path)
    monkeypatch.setattr(drt, "import_demucs", lambda data_root=None: api)
    with pytest.raises(drt.DemucsModelError):
        mgr.separate("htdemucs", _audio(tmp_path))


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from app import main

    monkeypatch.setattr(drt, "import_demucs", lambda data_root=None: FAKE_API)
    main.app.state.demucs = drt.DemucsManager(tmp_path)
    return TestClient(main.app)


def test_route_capabilities(client):
    r = client.get("/api/separation/capabilities")
    assert r.status_code == 200 and r.json()["engine"] == "demucs"


def test_route_multipart(client):
    r = client.post(
        "/api/separation/separate",
        files={"file": ("song.mp3", b"ID3", "audio/mpeg")},
        data={"model": "htdemucs"},
    )
    assert r.status_code == 200
    assert len(r.json()["stems"]) == 4


def test_route_local(client, tmp_path):
    song = _audio(tmp_path)
    r = client.post("/api/separation/separate-local",
                    json={"audio_path": str(song), "model": "htdemucs"})
    assert r.status_code == 200 and r.json()["sample_rate"] == 44100


def test_route_stream(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from app import main

    monkeypatch.setattr(main, "APP_ROOT", tmp_path)
    job = tmp_path / "separated" / "job1"
    job.mkdir(parents=True)
    (job / "vocals.wav").write_bytes(b"RIFF")
    r = TestClient(main.app).get(
        "/api/separation/stream", params={"job_id": "job1", "stem": "vocals.wav"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"


def test_route_stream_traversal(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from app import main

    monkeypatch.setattr(main, "APP_ROOT", tmp_path)
    r = TestClient(main.app).get(
        "/api/separation/stream",
        params={"job_id": "../../etc", "stem": "passwd"})
    assert r.status_code in (400, 404)


def test_route_engine_missing(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from app import main

    def boom(data_root=None):
        raise drt.DemucsEngineMissing("not installed")

    monkeypatch.setattr(drt, "import_demucs", boom)
    main.app.state.demucs = drt.DemucsManager(tmp_path)
    r = TestClient(main.app).post(
        "/api/separation/separate-local",
        json={"audio_path": str(_audio(tmp_path))})
    assert r.status_code == 503
