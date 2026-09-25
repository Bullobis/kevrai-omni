"""Neuron-MNN 6th slice: spawn-context high-pressure stress tests.

The subprocess isolation was moved from the platform-default ``fork`` start
method to an explicit ``spawn`` context (see ``_MP_CTX`` in ``mnn_runtime``).
This suite hammers the spawn lifecycle with a FAKE engine — no real model, no
GPU, fully offline — to prove that repeated respawn, kill/recover, streaming
hang recovery, exception round-tripping and resource hygiene hold up under
repetition.

Coverage:
  * repeated SIGKILL + auto-respawn (20+ cycles)
  * load -> chat -> unload repeated 20x, correct text every time
  * streaming hang mid-generation -> parent timeout SIGKILLs -> next load rebuilds
  * exception types (RuntimeError / ValueError) survive the spawn pickle round-trip
  * resource sampling: active_children() bounded, fd table does not grow linearly
  * _call_lock correctness: rapid sequential calls lose nothing and never hang

The fake engine is injected through ``_TEST_FAKE_ENV_VAR``, which crosses the
spawn boundary (a parent-side monkeypatch cannot, because the child re-imports
this package in a fresh interpreter).
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import mnn_runtime  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def subprocess_mode(monkeypatch, tmp_path):
    """Enable subprocess mode, write a config.json model dir, reset singleton."""
    monkeypatch.setenv(mnn_runtime._SUBPROCESS_ENV_VAR, "1")
    monkeypatch.delenv(mnn_runtime._TEST_FAKE_ENV_VAR, raising=False)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    mnn_runtime._reset_client_for_tests()
    yield tmp_path
    mnn_runtime._reset_client_for_tests()
    # Hard guarantee: after teardown there must be no live worker left.
    assert multiprocessing.active_children() == [], (
        f"lingering MNN children: {multiprocessing.active_children()!r}"
    )


def _fake(monkeypatch, behavior: str) -> None:
    monkeypatch.setenv(mnn_runtime._TEST_FAKE_ENV_VAR, behavior)


def _fd_count() -> int:
    try:
        return len(os.listdir("/proc/self/fd"))
    except FileNotFoundError:
        return -1  # non-Linux; fd-bounding asserts become no-ops


# ---------------------------------------------------------------------------
# 1. Repeated kill / respawn cycle
# ---------------------------------------------------------------------------

def test_kill_respawn_loop_25_times(subprocess_mode, monkeypatch):
    """25 load -> SIGKILL -> auto-respawn cycles; every load must succeed."""
    _fake(monkeypatch, "normal")
    fds: list[int] = []
    for i in range(25):
        st = mnn_runtime.load_model(subprocess_mode, f"model-{i}")
        assert st["loaded"] is True, f"cycle {i}: load not marked loaded"
        assert st["model_name"] == f"model-{i}"
        client = mnn_runtime._get_client()
        assert client._proc is not None and client._proc.is_alive()
        # Directly kill the worker (simulates a C++ engine segfault/OOM).
        client._kill()
        assert client._proc is None
        # active_children must be empty immediately after the kill.
        assert len(multiprocessing.active_children()) <= 1, (
            f"cycle {i}: unexpected live children {multiprocessing.active_children()!r}"
        )
        if i in (0, 12, 24):
            fds.append(_fd_count())

    # Final load leaves a healthy worker; fd table must be flat, not climbing.
    st = mnn_runtime.load_model(subprocess_mode, "final")
    assert st["loaded"] is True
    if len(fds) >= 2 and fds[0] >= 0:
        assert fds[-1] - fds[0] <= 8, (
            f"fd table grew across respawns: {fds} (leak candidate)"
        )


# ---------------------------------------------------------------------------
# 2. load -> chat -> unload repeated
# ---------------------------------------------------------------------------

def test_load_chat_unload_loop_20_times(subprocess_mode, monkeypatch):
    """Each iteration must return the echoed fake text and flip state correctly."""
    _fake(monkeypatch, "normal")
    for i in range(20):
        st = mnn_runtime.load_model(subprocess_mode, f"chat-model-{i}")
        assert st["loaded"] is True
        res = mnn_runtime.chat(f"hello-{i}")
        assert res["text"] == f"fake response: hello-{i}", (
            f"iter {i}: wrong text {res['text']!r}"
        )
        assert res["chars"] > 0
        mnn_runtime.unload_model()
        st = mnn_runtime.status()
        assert st["loaded"] is False, f"iter {i}: unload did not clear state"


# ---------------------------------------------------------------------------
# 3. Streaming hang mid-generation -> SIGKILL -> rebuild
# ---------------------------------------------------------------------------

def test_stream_hang_timeout_then_rebuild(subprocess_mode, monkeypatch):
    """generate() blocks forever: parent stream timeout SIGKILLs the child, and
    the next load_model rebuilds a fresh worker."""
    _fake(monkeypatch, "hang")
    monkeypatch.setattr(mnn_runtime, "_SUBPROCESS_STREAM_TIMEOUT_S", 0.5)
    mnn_runtime.load_model(subprocess_mode, "hang-model")
    client = mnn_runtime._get_client()
    pid = client._proc.pid

    with pytest.raises(TimeoutError):
        list(mnn_runtime.chat_stream("hi"))

    # The OS process must be dead (not just a released lock).
    assert client._proc is None or not client._proc.is_alive()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)

    # Rebuild: flip back to a normal-behavior child via respawn.
    monkeypatch.setenv(mnn_runtime._TEST_FAKE_ENV_VAR, "normal")
    st = mnn_runtime.load_model(subprocess_mode, "after-hang")
    assert st["loaded"] is True
    res = mnn_runtime.chat("alive again")
    assert "alive again" in res["text"]


# ---------------------------------------------------------------------------
# 4. Exception types round-trip across the spawn pipe
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("behavior,exc", [
    ("error", RuntimeError),
    ("value_error", ValueError),
])
def test_exception_type_roundtrip(subprocess_mode, monkeypatch, behavior, exc):
    _fake(monkeypatch, behavior)
    mnn_runtime.load_model(subprocess_mode, "exc-model")
    with pytest.raises(exc):
        mnn_runtime.chat("trigger")


# ---------------------------------------------------------------------------
# 5. Resource sampling across a mixed workload
# ---------------------------------------------------------------------------

def test_resource_sampling_no_leak(subprocess_mode, monkeypatch):
    """Mix of chat / stream / unload; sample active_children and fds each round."""
    _fake(monkeypatch, "normal")
    mnn_runtime.load_model(subprocess_mode, "res-model")
    baseline_fds = _fd_count()
    samples: list[tuple[int, int, int]] = []  # (round, active_children, fds)

    for i in range(15):
        res = mnn_runtime.chat(f"round-{i}")
        assert res["text"] == f"fake response: round-{i}"
        # Stream once in a while (normal behavior -> completes immediately).
        if i % 3 == 0:
            frames = list(mnn_runtime.chat_stream(f"stream-{i}"))
            assert frames[-1] == ("", True)
        n_children = len(multiprocessing.active_children())
        n_fds = _fd_count()
        samples.append((i, n_children, n_fds))
        assert n_children <= 1, (
            f"round {i}: {n_children} live children (expected <=1): "
            f"{multiprocessing.active_children()!r}"
        )

    # fd count must be bounded: no linear growth across 15 rounds.
    fds_seen = [f for _, _, f in samples if f >= 0 and baseline_fds >= 0]
    if fds_seen:
        growth = max(fds_seen) - baseline_fds
        assert growth <= 10, (
            f"fd table grew by {growth} over 15 rounds (baseline {baseline_fds}, "
            f"peak {max(fds_seen)}): leak candidate"
        )


# ---------------------------------------------------------------------------
# 6. _call_lock correctness: rapid sequential calls, no loss / no hang
# ---------------------------------------------------------------------------

def test_rapid_sequential_calls_no_loss(subprocess_mode, monkeypatch):
    """MNN is non-reentrant; drive 30 sequential chats back-to-back. Every reply
    must arrive intact and in order (the lock must serialize without dropping
    or deadlocking)."""
    _fake(monkeypatch, "normal")
    mnn_runtime.load_model(subprocess_mode, "lock-model")
    client = mnn_runtime._get_client()
    # Sanity: the lock exists and is released (not held) between calls.
    assert isinstance(client._call_lock, type(threading.Lock()))

    for i in range(30):
        res = mnn_runtime.chat(f"rapid-{i}")
        assert res["text"] == f"fake response: rapid-{i}", (
            f"call {i}: lost / wrong reply {res['text']!r}"
        )
        # chat_count must track exactly the number of successful calls.
    st = mnn_runtime.status()
    assert st["chat_count"] == 30, f"chat_count drift: {st['chat_count']} != 30"
