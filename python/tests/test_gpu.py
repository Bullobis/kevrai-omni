"""Tests for app.gpu — mock subprocess.run / asyncio.subprocess.

We never shell out to a real `nvidia-smi` / `rocm-smi` in CI; instead we
inject fake return values via ``unittest.mock.patch``.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from app.gpu import (
    GPUInfo,
    _parse_nvidia_csv,
    detect,
    detect_sync,
)


# ---------- helpers ----------

class _FakeAsyncProc:
    """Async subprocess that yields a fixed stdout."""

    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout.encode("utf-8"), b""


class _FakeAsyncProcTimeout:
    async def communicate(self) -> tuple[bytes, bytes]:
        raise asyncio.TimeoutError()

    def kill(self) -> None:
        pass


def _patched_create_subprocess_exec(stdout: str, returncode: int = 0):
    """Return a coroutine that replaces ``asyncio.create_subprocess_exec``."""

    async def _factory(*args, **kwargs):
        return _FakeAsyncProc(stdout=stdout, returncode=returncode)

    return _factory


# ---------- parse ----------

def test_parse_nvidia_csv_basic():
    csv = (
        "0, NVIDIA GeForce RTX 4090, 24576, 555.85, 8.9, GPU-abc\n"
        "1, NVIDIA GeForce RTX 2080,  8192, 555.85, 7.5, GPU-def\n"
    )
    out = _parse_nvidia_csv(csv)
    assert len(out) == 2
    assert out[0].vendor == "nvidia"
    assert out[0].vram_mb == 24576
    assert out[0].compute_capability == "8.9"
    assert out[1].name.startswith("NVIDIA")


def test_parse_nvidia_csv_handles_short_lines():
    # Pydantic/info gracefully handles malformed lines
    out = _parse_nvidia_csv("garbage")
    assert out == []


# ---------- async detect() ----------

@pytest.mark.asyncio
async def test_detect_falls_back_to_cpu_on_no_tools(monkeypatch):
    """If every vendor-detector returns [] we should still get a CPU fallback."""
    # Force all detectors to return []
    async def _nope_nvidia():
        return []

    async def _nope_amd():
        return []

    async def _nope_apple():
        return []

    async def _nope_ascend():
        return []

    monkeypatch.setattr("app.gpu._detect_nvidia", _nope_nvidia)
    monkeypatch.setattr("app.gpu._detect_amd", _nope_amd)
    monkeypatch.setattr("app.gpu._detect_apple", _nope_apple)
    monkeypatch.setattr("app.gpu._detect_ascend", _nope_ascend)

    gpus = await detect()
    assert len(gpus) >= 1, "should at least return a CPU fallback"
    assert gpus[0].vendor in {"cpu", "nvidia", "amd", "apple", "ascend"}


@pytest.mark.asyncio
async def test_detect_nvidia_parses(monkeypatch):
    csv = "0, Test GPU, 12288, 123.45, 8.6, GPU-xyz\n"

    async def _replace_exec(*args, **kwargs):
        return _FakeAsyncProc(stdout=csv, returncode=0)

    # Pretend nvidia-smi exists at a known location
    monkeypatch.setattr("app.gpu._first_existing", lambda *_: "/bin/nvidia-smi")
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", _replace_exec
    )

    out = await detect()
    nvidia = [g for g in out if g.vendor == "nvidia"]
    assert nvidia, "should detect at least one nvidia gpu"
    assert nvidia[0].vram_mb == 12288


@pytest.mark.asyncio
async def test_detect_handles_subprocess_timeout(monkeypatch):
    async def _replace_exec_timeout(*args, **kwargs):
        return _FakeAsyncProcTimeout()

    monkeypatch.setattr("app.gpu._first_existing", lambda *_: "/bin/nvidia-smi")
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", _replace_exec_timeout
    )

    # Should not raise
    gpus = await detect()
    assert isinstance(gpus, list)


@pytest.mark.asyncio
async def test_detect_handles_missing_binary(monkeypatch):
    # Force the helper to find nothing
    monkeypatch.setattr("app.gpu._first_existing", lambda *_: None)
    gpus = await detect()
    assert isinstance(gpus, list)


@pytest.mark.asyncio
async def test_detect_handles_exceptions_in_detectors(monkeypatch):
    """A detector that raises must not crash the whole `detect()`."""

    async def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr("app.gpu._detect_nvidia", _raise)
    monkeypatch.setattr("app.gpu._detect_amd", _raise)
    monkeypatch.setattr("app.gpu._detect_apple", _raise)
    monkeypatch.setattr("app.gpu._detect_ascend", _raise)

    gpus = await detect()
    # CPU fallback at minimum
    assert any(g.vendor == "cpu" for g in gpus)


# ---------- sync wrapper ----------

def test_detect_sync_returns_list(monkeypatch):
    async def _nope():
        return []

    monkeypatch.setattr("app.gpu._detect_nvidia", _nope)
    monkeypatch.setattr("app.gpu._detect_amd", _nope)
    monkeypatch.setattr("app.gpu._detect_apple", _nope)
    monkeypatch.setattr("app.gpu._detect_ascend", _nope)
    out = detect_sync()
    assert isinstance(out, list)
    assert len(out) >= 1


def test_detect_sync_swallows_exceptions(monkeypatch):
    """If detection raises, detect_sync() must return [] gracefully."""
    async def _raise():
        raise RuntimeError("nope")

    monkeypatch.setattr("app.gpu.detect", _raise)
    out = detect_sync()
    assert out == []


# ---------- model shape ----------

def test_gpuinfo_serde_roundtrip():
    g = GPUInfo(vendor="nvidia", name="X", vram_mb=8192, driver_version="1.0",
                compute_capability="8.6", index=3, uuid="abc")
    d = g.model_dump()
    assert d["vendor"] == "nvidia"
    g2 = GPUInfo(**d)
    assert g2.uuid == "abc"
    assert g2.vram_mb == 8192
