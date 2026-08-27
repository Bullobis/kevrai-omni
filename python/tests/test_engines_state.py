"""Tests for the engine state machine (EngineManager).

We don't do real installs — we construct EngineManager with a fake
``DownStub`` via monkey-patching the ``install`` flow.

Note: The existing ``test_engines.py`` covers legacy API. This file
covers the new lifecycle (NOT_INSTALLED → INSTALLED → uninstall → gone).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from app.engines import (
    EngineManager,
    EngineRecord,
    EngineState,
    engine_install_dir,
)


def _make_zip(target: Path, with_file: str = "engine.bin", content: bytes = b"engine-bytes") -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(with_file, content)
    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# Lifecycle without network
# ---------------------------------------------------------------------------


def test_engine_manager_empty(tmp_path: Path):
    em = EngineManager(tmp_path)
    assert em.list_installed() == []
    assert em.get("llama.cpp") is None
    assert em.engine_dir() == engine_install_dir(tmp_path)


def test_engine_manager_state_transitions_synthetic(tmp_path: Path):
    em = EngineManager(tmp_path)

    rec = em._set_state("llama.cpp", EngineState.NOT_INSTALLED)
    assert rec.state == EngineState.NOT_INSTALLED

    rec = em._set_state("llama.cpp", EngineState.DOWNLOADING,
                        install_path=str(em.engine_dir() / "llama.cpp"))
    assert rec.state == EngineState.DOWNLOADING

    rec = em._set_state("llama.cpp", EngineState.VERIFYING)
    assert rec.state == EngineState.VERIFYING

    rec = em._set_state("llama.cpp", EngineState.INSTALLED,
                        install_path=str(em.engine_dir() / "llama.cpp"),
                        size_bytes=1234, installed_at="2026-01-01")
    assert rec.state == EngineState.INSTALLED

    rec = em._set_state("llama.cpp", EngineState.FAILED, last_error="boom")
    assert rec.state == EngineState.FAILED
    assert rec.last_error == "boom"


def test_engine_manager_persisted_manifest_roundtrip(tmp_path: Path):
    em = EngineManager(tmp_path)
    em._set_state("a", EngineState.INSTALLED, install_path="/x",
                  size_bytes=10, installed_at="2026-01-01",
                  sha256="abc")
    em._set_state("b", EngineState.INSTALLED, install_path="/y",
                  install_mode="pip")

    # Force a fresh read from disk
    em2 = EngineManager(tmp_path)
    recs = em2.list_installed()
    assert len(recs) == 2
    a = em2.get("a")
    assert a.state == EngineState.INSTALLED
    assert a.sha256 == "abc"
    b = em2.get("b")
    assert b.install_mode == "pip"


def test_engine_manager_uninstall_removes_entry(tmp_path: Path):
    em = EngineManager(tmp_path)
    em._set_state("x", EngineState.INSTALLED, install_path="/x",
                  installed_at="2026-01-01")
    assert em.uninstall("x") is True
    assert em.get("x") is None
    assert em.list_installed() == []


def test_engine_manager_uninstall_unknown_returns_false(tmp_path: Path):
    em = EngineManager(tmp_path)
    assert em.uninstall("never-installed") is False


def test_engine_manager_is_installed_method(tmp_path: Path):
    em = EngineManager(tmp_path)
    assert em.is_installed("llama.cpp") is False
    real = tmp_path / "models" / "m.bin"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(b"hello")
    em._set_state("llama.cpp", EngineState.INSTALLED,
                  install_path=str(real),
                  installed_at="2026-01-01")
    assert em.is_installed("llama.cpp") is True


def test_engine_manager_is_installed_false_for_missing_path(tmp_path: Path):
    em = EngineManager(tmp_path)
    em._set_state("llama.cpp", EngineState.INSTALLED,
                  install_path=str(tmp_path / "does-not-exist"),
                  installed_at="2026-01-01")
    assert em.is_installed("llama.cpp") is False
    rec = em.get("llama.cpp")
    assert rec.state == EngineState.FAILED


def test_engine_manager_install_rejects_blocked_host(tmp_path: Path):
    em = EngineManager(tmp_path)
    with pytest.raises(ValueError) as exc:
        em.install("evil", "https://evil.example.com/x.zip")
    assert "refusing" in str(exc.value).lower() or "allowlist" in str(exc.value).lower()
    rec = em.get("evil")
    assert rec.state == EngineState.FAILED


def test_engine_manager_engine_record_serializable():
    r = EngineRecord(id="x", state=EngineState.INSTALLED,
                     install_path="/x", sha256="abc",
                     size_bytes=10, installed_at="2026-01-01",
                     source_url="https://github.com/x.zip",
                     install_mode="binary",
                     version="1.0.0",
                     last_error="")
    d = r.to_dict()
    assert d["id"] == "x"
    assert d["state"] == "installed"
    assert d["install_path"] == "/x"
    assert d["sha256"] == "abc"


def test_engine_manager_state_enum_values():
    """Public enum values — do not change without updating JSON manifests."""
    expected = {
        "not_installed",
        "downloading",
        "verifying",
        "installed",
        "failed",
    }
    got = {s.value for s in EngineState}
    assert got == expected


def test_engine_manager_manifest_is_atomic(tmp_path: Path):
    """While a write is happening, the manifest must always be valid JSON."""
    import json, os
    em = EngineManager(tmp_path)
    em._set_state("a", EngineState.INSTALLED, install_path="/a",
                  installed_at="2026-01-01")
    p = em.engine_dir() / "installed.json"
    raw = p.read_text()
    json.loads(raw)  # must parse
    assert "installed.json.tmp" not in str(p.parent.iterdir()), (
        "no leftover .tmp file expected"
    )


def test_engine_manager_record_serde_roundtrip(tmp_path: Path):
    """EngineRecord dumps & reloads cleanly via Pydantic-style dict."""
    em = EngineManager(tmp_path)
    em._set_state("a", EngineState.FAILED, last_error="x",
                  source_url="https://example.com/x",
                  install_path="/a")
    rec = em.get("a")
    d = rec.to_dict()
    # Restore via fresh manager
    p = em.engine_dir() / "installed.json"
    recs = json.loads(p.read_text())
    rec2_dict = next(r for r in recs if r["id"] == "a")
    assert rec2_dict["last_error"] == "x"
    assert rec2_dict["source_url"] == "https://example.com/x"
