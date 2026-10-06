"""Regression tests for the faster-whisper engine + Whisper catalog entries."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DATA = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))
_ENG = json.loads((_ROOT / "catalog" / "engines.json").read_text(encoding="utf-8"))
_BY_ID = {m["id"]: m for m in _DATA["models"]}
_ENG_BY_ID = {e["id"]: e for e in _ENG["engines"]}


def test_engine_present():
    e = _ENG_BY_ID["faster-whisper"]
    assert e["install"] == "pip"
    # pinned: faster-whisper 1.2.1 is incompatible with PyAV 19 (removed
    # metadata_errors kwarg), so pin the engine and constrain av<19.
    assert e["pypi"] == "faster-whisper==1.2.1"
    assert any(spec.startswith("av") and "<19" in spec for spec in e.get("pip_extra", []))
    assert e["license"] == "MIT"


def test_total_grew():
    assert len(_BY_ID) >= 240


def test_whisper_large_v3():
    m = _BY_ID["whisper-large-v3"]
    assert m["repo"] == "Systran/faster-whisper-large-v3"
    assert m["engine"] == ["faster-whisper"]
    assert m["modality"]["understand"] == ["audio"]
    assert m["modality"]["generate"] == ["text"]


def test_whisper_turbo():
    m = _BY_ID["whisper-large-v3-turbo"]
    assert m["repo"] == "dropbox-dash/faster-whisper-large-v3-turbo"
    assert m["size_gb"] == 1.62
    assert m["size_gb"] < _BY_ID["whisper-large-v3"]["size_gb"]


def test_install_pip_pins_extra_and_uses_eid(monkeypatch, tmp_path):
    """install_pip_engine must append pip_extra specs and name the target
    directory after the engine id (not the possibly-pinned requirement)."""
    from app import engines

    captured = {}

    class _Proc:
        returncode = 0
        stdout = "Successfully installed x"
        stderr = ""

    def _fake_run(cmd, **_kw):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(engines.subprocess, "run", _fake_run)
    res = engines.install_pip_engine(
        "faster-whisper==1.2.1", tmp_path,
        eid="faster-whisper", extra=["av>=13,<19"],
    )
    cmd = captured["cmd"]
    assert "faster-whisper==1.2.1" in cmd
    assert "av>=13,<19" in cmd
    # target dir is named after the engine id
    assert str(tmp_path / "engines" / "pip-faster-whisper") in " ".join(cmd)
    assert res.ok and res.engine_id == "faster-whisper"
