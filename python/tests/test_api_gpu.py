"""Regression: ``app.gpu._detect_ascend`` must kill its child on timeout.

The nvidia/amd detectors already ``proc.kill()`` on ``asyncio.TimeoutError``;
the ascend detector swallowed the timeout without killing, leaking a hanging
``npu-smi`` process.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import gpu as gpu_mod


class _TimeoutProc:
    """Fake asyncio subprocess whose communicate() raises TimeoutError."""

    def __init__(self) -> None:
        self.killed = False
        self.returncode = -1

    async def communicate(self) -> tuple[bytes, bytes]:
        raise asyncio.TimeoutError()

    def kill(self) -> None:
        self.killed = True


@pytest.mark.asyncio
async def test_detect_ascend_kills_child_on_timeout(monkeypatch):
    proc = _TimeoutProc()

    async def _fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(gpu_mod.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(gpu_mod.shutil, "which", lambda name: "/usr/local/npu-smi")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    out = await gpu_mod._detect_ascend()

    assert out == []
    assert proc.killed is True, "a hanging npu-smi child must be killed, not leaked"


@pytest.mark.asyncio
async def test_gpu_route_caches_detection(monkeypatch):
    """``/api/gpu`` must not re-fork the vendor probes on every call.

    ``app.gpu.detect`` spawns up to four subprocesses with 3-5s timeouts, and
    the renderer asks for GPU info on each window focus. The route therefore
    caches the payload for ``_GPU_CACHE_TTL_S``; only ``?refresh=1`` re-probes.
    """
    from app import main as app_main

    calls = {"n": 0}

    async def _fake_detect():
        calls["n"] += 1
        return []

    monkeypatch.setattr(app_main, "detect_gpus", _fake_detect)
    monkeypatch.setattr(app_main, "_GPU_CACHE", {"ts": 0.0, "data": None})

    with TestClient(app_main.app) as c:
        first = c.get("/api/gpu")
        second = c.get("/api/gpu")
        forced = c.get("/api/gpu?refresh=1")

    assert first.status_code == 200, first.text
    assert first.json() == second.json(), "cached payload must match the first call"
    assert calls["n"] == 2, f"expected probe on first call and on ?refresh=1 only, got {calls['n']}"
    assert forced.status_code == 200, forced.text
