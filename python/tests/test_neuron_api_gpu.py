"""Regression: ``app.gpu._detect_ascend`` must kill its child on timeout.

The nvidia/amd detectors already ``proc.kill()`` on ``asyncio.TimeoutError``;
the ascend detector swallowed the timeout without killing, leaking a hanging
``npu-smi`` process.
"""
from __future__ import annotations

import asyncio

import pytest

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
