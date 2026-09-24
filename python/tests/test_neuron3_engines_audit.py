"""Neuron3-Engines slice audit regression tests.

Covers the two bugs confirmed by code evidence during the K-Cortex third-slice
audit (Project K-Cortex, Neuron-Engines):

  * BUG-A (mnn_catalog): ``list_mnn_files`` / ``_MIRRORS`` must never direct
    enumeration or generated download URLs at ``hf-cdn.sufy.com`` — a
    typosquat/phishing clone that SECURITY.md hard-blocks at every entry point.

  * BUG-B (engines): ``EngineManager.install`` / ``install_pip`` and the legacy
    ``download_zip_engine`` / ``install_pip_engine`` are serialized by a global
    install mutex. Without it, two concurrent installs interleave chunks into
    the same ``.partial`` blob and clobber each other's manifest record.

All network is mocked — no real download is ever performed.
"""
from __future__ import annotations

import threading
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload: bytes = b"", status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=None, response=None
            )

    def json(self):
        import json
        return json.loads(self._payload) if self._payload else {}


def _make_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


# ---------------------------------------------------------------------------
# BUG-A: mnn_catalog must never use the hard-blocked phishing mirror
# ---------------------------------------------------------------------------

def test_mirrors_exclude_hard_blocked_sufy():
    """SECURITY.md hard-blocks hf-cdn.sufy.com at every entry point."""
    from app.mnn_catalog import _MIRRORS

    for m in _MIRRORS:
        assert "sufy" not in m, f"blocked phishing mirror still in _MIRRORS: {m}"


def test_list_mnn_files_never_requests_sufy():
    """Even when ModelScope fails, the HF fallback loop must not hit sufy.

    We mock httpx.Client so ModelScope raises (forcing the HF mirror walk) and
    every HF mirror returns an empty file list. We then assert that none of the
    URLs the enumerator contacted contains the blocked host.
    """
    import json

    import app.mnn_catalog as mc

    requested: list[str] = []

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url: str):
            requested.append(url)
            # ModelScope: raise to force fall-through to HF mirrors.
            if "modelscope.cn" in url:
                raise RuntimeError("modelscope down (mocked)")
            # HF mirrors: return an empty file list so the loop advances.
            return _FakeResponse(json.dumps([]).encode())

    with patch("httpx.Client", _FakeClient), pytest.raises(RuntimeError):
        # All mirrors fail → the function raises RuntimeError.
        mc.list_mnn_files("taobao-mnn/Qwen2.5-Coder-7B-Instruct-MNN")

    assert requested, "no URLs were requested — test is vacuous"
    for url in requested:
        assert "sufy" not in url, f"enumerator contacted blocked host: {url}"


def test_list_mnn_files_generated_hf_url_excludes_sufy():
    """When ModelScope succeeds, the per-file ``hf_url`` fallback must not point
    at the blocked host (it used ``_MIRRORS[0]`` before the fix)."""
    import json

    import app.mnn_catalog as mc

    ms_payload = json.dumps({
        "Data": {
            "Files": [
                {"Type": "blob", "Path": "model.mnn", "Size": 123},
            ]
        }
    }).encode()

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url: str):
            if "modelscope.cn" in url:
                return _FakeResponse(ms_payload)
            return _FakeResponse(b"{}")

    with patch("httpx.Client", _FakeClient):
        files = mc.list_mnn_files("taobao-mnn/Qwen2.5-Coder-7B-Instruct-MNN")

    assert files, "expected ModelScope to return one file"
    for f in files:
        assert "sufy" not in f["hf_url"], f"generated hf_url points at blocked host: {f['hf_url']}"
        assert f["hf_url"].startswith("https://hf-mirror.com/"), f["hf_url"]


# ---------------------------------------------------------------------------
# BUG-B: install mutex serializes installs and releases on every exit path
# ---------------------------------------------------------------------------

def test_install_holds_lock_while_downloading(tmp_path: Path):
    """``install`` must hold the global install mutex for the whole download."""
    from app.engines import _INSTALL_LOCK, EngineManager

    mgr = EngineManager(tmp_path)
    zip_path = tmp_path / "e.zip"
    _make_zip(zip_path, {"run.sh": b"#!/bin/sh\n"})

    lock_seen_during_download: list[bool] = []

    import app.engines as eng_mod

    def fake_stream(url, tmp, **kw):
        # The install mutex must be held the moment we stream.
        lock_seen_during_download.append(_INSTALL_LOCK.locked())
        Path(tmp).write_bytes(zip_path.read_bytes())

    with patch.object(eng_mod, "_stream_download", side_effect=fake_stream):
        mgr.install("hold-lock", "https://github.com/x/y/releases/v1/e.zip")

    assert lock_seen_during_download, "_stream_download was never called"
    assert lock_seen_during_download[0] is True, "install did not hold the mutex during download"
    # Released after the call returns.
    assert not _INSTALL_LOCK.locked(), "mutex leaked after install returned"


def test_lock_released_when_install_raises(tmp_path: Path):
    """If install raises, the mutex must still be released (no deadlock)."""
    from app.engines import _INSTALL_LOCK, EngineManager

    mgr = EngineManager(tmp_path)

    # First install: host-not-allowed raises ValueError.
    with pytest.raises(ValueError):
        mgr.install("bad-host", "https://evil.example.com/x.zip")

    # The lock must NOT be held after the exception.
    assert not _INSTALL_LOCK.locked(), "mutex leaked after a raising install"

    # A subsequent install must be able to acquire it (not deadlocked).
    zip_path = tmp_path / "e2.zip"
    _make_zip(zip_path, {"run.sh": b"#!/bin/sh\n"})

    import app.engines as eng_mod

    def fake_stream(url, tmp, **kw):
        Path(tmp).write_bytes(zip_path.read_bytes())

    with patch.object(eng_mod, "_stream_download", side_effect=fake_stream):
        rec = mgr.install("good-host", "https://github.com/x/y/releases/v1/e.zip")
    assert rec.state.value == "installed"


def test_concurrent_installs_do_not_clobber_manifest(tmp_path: Path):
    """Two threads installing two DIFFERENT engines must both end up in the
    manifest (the read-modify-write race previously dropped one record)."""
    from app.engines import EngineManager, EngineState

    mgr = EngineManager(tmp_path)
    zip_path = tmp_path / "e.zip"
    _make_zip(zip_path, {"run.sh": b"#!/bin/sh\n"})

    import app.engines as eng_mod

    def fake_stream(url, tmp, **kw):
        Path(tmp).write_bytes(zip_path.read_bytes())

    # Patch ONCE in the main thread — patching/unpatching from inside each
    # worker would race on the module attribute.
    with patch.object(eng_mod, "_stream_download", side_effect=fake_stream):
        def worker(eid: str):
            return mgr.install(eid, f"https://github.com/x/y/releases/v1/{eid}.zip")

        t1 = threading.Thread(target=worker, args=("eng-A",))
        t2 = threading.Thread(target=worker, args=("eng-B",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

    recs = {r.id: r for r in mgr.list_installed()}
    assert "eng-A" in recs, "eng-A record was clobbered by concurrent install"
    assert "eng-B" in recs, "eng-B record was clobbered by concurrent install"
    assert recs["eng-A"].state == EngineState.INSTALLED
    assert recs["eng-B"].state == EngineState.INSTALLED


def test_lock_serializes_not_parallel(tmp_path: Path):
    """Prove the mutex actually serializes: at most one download streams at a
    time. Without the lock, both threads would overlap in the critical section."""
    import time

    from app.engines import _INSTALL_LOCK, EngineManager

    mgr = EngineManager(tmp_path)
    zip_path = tmp_path / "e.zip"
    _make_zip(zip_path, {"run.sh": b"#!/bin/sh\n"})

    active = 0
    max_concurrent = 0

    import app.engines as eng_mod

    def fake_stream(url, tmp, **kw):
        nonlocal active, max_concurrent
        active += 1
        max_concurrent = max(max_concurrent, active)
        time.sleep(0.05)  # hold the lock briefly so overlap is measurable
        active -= 1
        Path(tmp).write_bytes(zip_path.read_bytes())

    with patch.object(eng_mod, "_stream_download", side_effect=fake_stream):
        t1 = threading.Thread(
            target=lambda: mgr.install("ser-A", "https://github.com/x/y/a.zip")
        )
        t2 = threading.Thread(
            target=lambda: mgr.install("ser-B", "https://github.com/x/y/b.zip")
        )
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

    assert max_concurrent == 1, (
        f"installs ran concurrently (max={max_concurrent}) — mutex not serializing"
    )
    assert not _INSTALL_LOCK.locked()
