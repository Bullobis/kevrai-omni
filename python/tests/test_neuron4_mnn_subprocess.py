"""Neuron-MNN 4th slice: subprocess isolation integration tests.

The C++ MNN engine can hard-deadlock during generate(); in-process that leaves
a parked daemon thread. This suite exercises the opt-in subprocess isolation
(``KEVRAI_MNN_SUBPROCESS=1``) end-to-end with a FAKE engine — no real model,
no GPU, fully offline.

Coverage:
  * child start / ready handshake
  * load -> status -> chat -> stream roundtrip over the pipe
  * exception type round-trips across the pipe (MnnEngineMissing / ValueError / ...)
  * child SIGKILL (segfault/OOM) -> parent marks failed, next load respawns
  * child hang (generate() blocks forever) -> parent timeout SIGKILLs the child
    (the whole point: the parked C++ thread dies with the OS process)
  * clean shutdown leaves no lingering child process

Default (env unset) behavior is covered by test_neuron_runtime.py; these tests
only touch the opt-in path.
"""
from __future__ import annotations

import os
import signal
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import mnn_runtime  # noqa: E402

# ---------------------------------------------------------------------------
# Fake MNN engine (lives inside the forked child; never touches the parent).
# ---------------------------------------------------------------------------

class _FakeChildLlm:
    """Stand-in for the opaque MNN.llm.Llm object, running in the child."""

    def __init__(self, cfg_path: str, behavior: str = "normal"):
        self._behavior = behavior
        self._cfg = cfg_path
        self._prompt = ""

    def load(self):
        pass

    def reset(self):
        pass

    def apply_chat_template(self, msg):
        if isinstance(msg, dict):
            return msg.get("content", "")
        return str(msg)

    def set_config(self, cfg):
        pass

    def response(self, prompt, stream):
        if self._behavior == "error":
            raise RuntimeError("fake boom")
        if self._behavior == "value_error":
            raise ValueError("fake bad input")
        return "fake response: " + str(prompt)

    def generate_init(self, prompt):
        self._prompt = prompt

    def get_context(self):
        return {"generate_str": "partial"}

    def generate(self):
        if self._behavior == "hang":
            threading.Event().wait()  # simulate C++ deadlock forever


def _install_fake_engine(monkeypatch, behavior: str = "normal"):
    """Monkeypatch ``_import_llm`` in the parent; the forked child inherits it."""
    def _fake_create(cfg_path: str):
        return _FakeChildLlm(cfg_path, behavior=behavior)

    monkeypatch.setattr(mnn_runtime, "_import_llm", lambda: _fake_create)


@pytest.fixture
def subprocess_mode(monkeypatch, tmp_path):
    """Enable subprocess mode + write a config.json model dir + reset singleton."""
    monkeypatch.setenv(mnn_runtime._SUBPROCESS_ENV_VAR, "1")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    mnn_runtime._reset_client_for_tests()
    yield tmp_path
    mnn_runtime._reset_client_for_tests()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_default_mode_is_inprocess(monkeypatch):
    """Without the env var, subprocess mode must be OFF (default path untouched)."""
    monkeypatch.delenv(mnn_runtime._SUBPROCESS_ENV_VAR, raising=False)
    assert mnn_runtime._subprocess_mode() is False


def test_child_indicator_disables_recursion(monkeypatch):
    """Inside the child (indicator env set), subprocess mode is always off."""
    monkeypatch.setenv(mnn_runtime._CHILD_INDICATOR_VAR, "1")
    monkeypatch.setenv(mnn_runtime._SUBPROCESS_ENV_VAR, "1")
    assert mnn_runtime._subprocess_mode() is False


def test_load_status_chat_roundtrip(subprocess_mode, monkeypatch):
    _install_fake_engine(monkeypatch, "normal")
    st = mnn_runtime.load_model(subprocess_mode, "fake-model")
    assert st["loaded"] is True
    assert st["model_name"] == "fake-model"

    st2 = mnn_runtime.status()
    assert st2["loaded"] is True
    assert st2["model_name"] == "fake-model"

    res = mnn_runtime.chat("hello")
    assert res["text"] == "fake response: hello"
    assert res["chars"] > 0


def test_chat_stream_yields_deltas(subprocess_mode, monkeypatch):
    _install_fake_engine(monkeypatch, "normal")
    mnn_runtime.load_model(subprocess_mode, "fake-model")
    out = list(mnn_runtime.chat_stream("hi"))
    assert out[-1] == ("", True), "last frame must be the finished marker"
    assert any(d for d, _ in out), "must yield at least one text delta"


def test_runtime_error_propagates(subprocess_mode, monkeypatch):
    _install_fake_engine(monkeypatch, "error")
    mnn_runtime.load_model(subprocess_mode, "fake-model")
    with pytest.raises(RuntimeError, match="fake boom"):
        mnn_runtime.chat("hello")


def test_value_error_propagates(subprocess_mode, monkeypatch):
    _install_fake_engine(monkeypatch, "value_error")
    mnn_runtime.load_model(subprocess_mode, "fake-model")
    with pytest.raises(ValueError, match="fake bad input"):
        mnn_runtime.chat("hello")


def test_unload_clears_loaded_state(subprocess_mode, monkeypatch):
    _install_fake_engine(monkeypatch, "normal")
    mnn_runtime.load_model(subprocess_mode, "fake-model")
    mnn_runtime.unload_model()
    st = mnn_runtime.status()
    assert st["loaded"] is False


def test_child_crash_detected_and_respawns(subprocess_mode, monkeypatch):
    """SIGKILL the child mid-idle: parent must mark failed, next load respawns."""
    _install_fake_engine(monkeypatch, "normal")
    mnn_runtime.load_model(subprocess_mode, "fake-model")
    client = mnn_runtime._get_client()
    pid = client._proc.pid

    os.kill(pid, signal.SIGKILL)
    client._proc.join(timeout=5)
    assert client._proc.exitcode is not None

    # status must not hang; it should report the child is gone
    st = mnn_runtime.status()
    assert st["loaded"] is False

    # next load respawns a fresh child and works
    st2 = mnn_runtime.load_model(subprocess_mode, "fake-model")
    assert st2["loaded"] is True
    res = mnn_runtime.chat("after restart")
    assert "after restart" in res["text"]


def test_hang_timeout_sigkills_child(subprocess_mode, monkeypatch):
    """Child generate() blocks forever: parent timeout must SIGKILL the child,
    not leave a parked thread. This is the core win over in-process mode."""
    _install_fake_engine(monkeypatch, "hang")
    monkeypatch.setattr(mnn_runtime, "_SUBPROCESS_STREAM_TIMEOUT_S", 0.3)
    mnn_runtime.load_model(subprocess_mode, "fake-model")
    client = mnn_runtime._get_client()
    pid = client._proc.pid

    with pytest.raises(TimeoutError):
        list(mnn_runtime.chat_stream("hi"))

    # the OS process must be gone (not merely a released Python lock)
    assert client._proc is None or not client._proc.is_alive()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # reaping check: signal 0 on a dead PID raises

    # next load respawns a fresh, healthy child
    st = mnn_runtime.load_model(subprocess_mode, "fake-model")
    assert st["loaded"] is True


def test_clean_shutdown_no_lingering_child(subprocess_mode, monkeypatch):
    _install_fake_engine(monkeypatch, "normal")
    mnn_runtime.load_model(subprocess_mode, "fake-model")
    client = mnn_runtime._get_client()
    pid = client._proc.pid

    mnn_runtime._reset_client_for_tests()  # sends shutdown + kills
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
