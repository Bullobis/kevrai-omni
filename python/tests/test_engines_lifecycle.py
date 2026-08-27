"""EngineManager lifecycle tests (install → verify → uninstall).

We avoid real network where possible. For tests that need a download we mock
``httpx.Client.stream`` so the unit test never reaches the public internet.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------- helpers ----------

class _FakeResponse:
    """Minimal stand-in for ``httpx.Response`` that the engine manager uses."""
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self, chunk_size: int = 0):
        yield self._body


def _make_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


# ---------- tests on the legacy surface (kept for backward-compat) ----------

def test_ensure_engine_unknown_id_returns_error(root: Path):
    from app.engines import ensure_engine
    res = ensure_engine("does-not-exist-zzz", catalog=None, engines={}, root=root)
    assert res.ok is False
    assert "not in catalog" in res.message


def test_legacy_status_roundtrip(root: Path):
    from app.engines import load_status, save_status
    save_status(root, {"llama.cpp": {"ok": True, "path": "/x"}})
    s = load_status(root)
    assert s["llama.cpp"]["ok"] is True


def test_pip_install_dryrun(root: Path, monkeypatch):
    """Mock subprocess.run to simulate a successful pip install."""
    from app.engines import install_pip_engine
    import subprocess

    class FakeProc:
        returncode = 0
        stdout = "Successfully installed MNN"
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc())

    res = install_pip_engine("MNN", root)
    assert res.ok is True
    assert "MNN" in res.engine_id


# ---------- tests on the new EngineManager ----------

def test_engine_manager_init_creates_dir(root: Path):
    from app.engines import EngineManager
    mgr = EngineManager(root)
    assert mgr.engine_dir().is_dir()
    assert (mgr.engine_dir() / "installed.json").exists() is False  # no manifest yet


def test_engine_manager_install_rejects_non_allowlisted_host(root: Path):
    from app.engines import EngineManager, EngineState
    mgr = EngineManager(root)
    with pytest.raises(ValueError) as exc:
        mgr.install("foo", "https://evil.com/x.zip")
    assert "allowlist" in str(exc.value).lower()
    # State must be FAILED in the manifest
    rec = mgr.get("foo")
    assert rec is not None
    assert rec.state == EngineState.FAILED
    assert "host not allowed" in rec.last_error


def test_engine_manager_install_rejects_blocked_mirror(root: Path):
    from app.engines import EngineManager
    mgr = EngineManager(root)
    with pytest.raises(ValueError):
        mgr.install("foo", "https://evil.example.com/x.zip")


def test_engine_manager_install_unzip_full_cycle(root: Path):
    """End-to-end: zip install → verify → uninstall. Uses a fake httpx."""
    from app.engines import EngineManager, EngineState

    mgr = EngineManager(root)
    target_zip = root / "fake-engine.zip"
    _make_zip(target_zip, {
        "engine.sh": b"#!/bin/sh\necho hello\n",
        "README.md": b"a friendly engine\n",
    })

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def stream(self, method, url):
            return _FakeResponse(target_zip.read_bytes())

    with patch("httpx.Client", FakeClient):
        rec = mgr.install(
            "fake-engine",
            "https://github.com/example/fake-engine/releases/download/v1/bin.zip",
        )

    assert rec.state == EngineState.INSTALLED
    assert (mgr.engine_dir() / "fake-engine" / "engine.sh").exists()

    # verify_installed must report True
    assert mgr.verify_installed("fake-engine") is True

    # Uninstall removes the install and the directory
    assert mgr.uninstall("fake-engine") is True
    assert not (mgr.engine_dir() / "fake-engine").exists()
    assert mgr.get("fake-engine") is None


def test_engine_manager_uninstall_when_no_record(root: Path):
    from app.engines import EngineManager
    mgr = EngineManager(root)
    # Uninstalling an engine that was never installed is a no-op (returns False)
    assert mgr.uninstall("never-installed") is False


def test_engine_manager_install_creates_engine_record(root: Path):
    from app.engines import EngineManager, EngineState

    mgr = EngineManager(root)
    target_zip = root / "fake-eng-2.zip"
    _make_zip(target_zip, {"bin.sh": b"#!/bin/sh\n"})

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def stream(self, method, url):
            return _FakeResponse(target_zip.read_bytes())

    with patch("httpx.Client", FakeClient):
        rec = mgr.install("eng-2", "https://github.com/x/y/releases/v1.zip")

    recs = mgr.list_installed()
    assert any(r.id == "eng-2" and r.state == EngineState.INSTALLED for r in recs)


def test_engine_manager_corrupt_manifest_falls_back(root: Path):
    """A corrupted installed.json should not crash the manager."""
    from app.engines import EngineManager
    (root / "engines").mkdir(parents=True, exist_ok=True)
    (root / "engines" / "installed.json").write_text("not json at all", encoding="utf-8")
    mgr = EngineManager(root)
    # Must return empty list, not raise
    assert mgr.list_installed() == []


def test_engine_manager_install_rejects_binary_with_bad_sha(root: Path):
    """If a sha is provided for a non-zip binary and the download doesn't
    match, the install fails and the state flips to FAILED."""
    from app.engines import EngineManager, EngineState

    mgr = EngineManager(root)
    bin_path = root / "engine.bin"
    bin_path.write_bytes(b"binary content here")

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def stream(self, method, url):
            return _FakeResponse(bin_path.read_bytes())

    with patch("httpx.Client", FakeClient):
        with pytest.raises(ValueError) as exc:
            mgr.install(
                "eng-bad",
                "https://github.com/x/y/releases/engine.bin",   # no .zip suffix → unzip=False path
                sha256="0" * 64,                                  # wrong hash
            )
    rec = mgr.get("eng-bad")
    assert rec is not None
    assert rec.state == EngineState.FAILED
    assert "sha" in rec.last_error.lower() or "mismatch" in rec.last_error.lower()


def test_engine_manager_verify_after_uninstall_returns_false(root: Path):
    from app.engines import EngineManager
    mgr = EngineManager(root)

    # Add a synthetic record pointing to a non-existent path
    from app.engines import EngineRecord, EngineState
    mgr._upsert(EngineRecord(
        id="synthetic",
        state=EngineState.INSTALLED,
        install_path=str(root / "nonexistent"),
    ))
    assert mgr.verify_installed("synthetic") is False
    # State flips to FAILED
    assert mgr.get("synthetic").state == EngineState.FAILED
