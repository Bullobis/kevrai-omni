"""Tests for engine manager — dispatch logic, host whitelist, status I/O."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.catalog import load_catalog
from app.engines import (
    _platform_key,
    download_zip_engine,
    engine_install_dir,
    engine_status_path,
    install_pip_engine,
    load_status,
    save_status,
)


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    return tmp_path


def test_platform_key_is_known():
    assert _platform_key() in {"windows-x64", "linux-x64", "darwin-arm64"}


def test_status_roundtrip(tmp_root: Path):
    save_status(tmp_root, {"llama.cpp": {"ok": True, "path": "/x"}})
    s = load_status(tmp_root)
    assert s["llama.cpp"]["ok"] is True
    assert s["llama.cpp"]["path"] == "/x"


def test_download_zip_engine_refuses_blocked_host(tmp_root: Path):
    res = download_zip_engine("https://evil.example.com/x.zip", tmp_root, "evil")
    assert not res.ok
    assert "allowlist" in res.message.lower() or "refusing" in res.message.lower()


def test_download_zip_engine_refuses_random_host(tmp_root: Path):
    res = download_zip_engine("https://example.com/x.zip", tmp_root, "evil")
    assert not res.ok


def test_pip_install_dryrun(tmp_root: Path, monkeypatch, capsys):
    """We don't actually run pip here (slow / needs network); we just verify
    the function signature returns an InstallResult without crashing."""
    # monkeypatch subprocess.run to simulate success
    import subprocess
    class FakeProc:
        returncode = 0
        stdout = "Successfully installed"
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc())
    res = install_pip_engine("MNN", tmp_root)
    assert res.ok is True
    assert "MNN" in res.engine_id