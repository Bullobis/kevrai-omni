"""Regression tests for Project K-Cortex Neuron-Runtime hardening.

Covers the bugs found in the 2026-09-24 audit of ``ltx_runtime`` /
``mnn_runtime`` / ``drama``. All tests are offline: torch / diffusers /
imageio / the real MNN C++ engine are never imported — heavy modules are
replaced by fakes via monkeypatch.

Coverage map (see audit report for file:line evidence):
  L1  ltx: cancel mid-generation must end CANCELLED, not FAILED
  L1b ltx: early cancel (after load) also records elapsed_s
  L2  ltx: mp4->gif fallback returns the path that was actually written,
           and removes the partial .mp4
  L3  ltx: in-memory task history is bounded (_MAX_TASKS)
  M1  mnn: streaming poll loop has a wall-clock timeout; the global lock is
           released after timeout instead of hanging forever
  M2  mnn: a successful chat clears the stale error from a previous failure
  M3  mnn: an error raised inside the background generate() thread is
           recorded into _STATE["error"]
  D1  drama: non-numeric LLM fields (scene_id / duration_s / shot_id) fall
           back to defaults instead of crashing the pipeline with ValueError
"""
from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ltx_runtime, mnn_runtime  # noqa: E402
from app.ltx_runtime import (  # noqa: E402
    LtxManager,
    LtxParams,
    TaskState,
    _Cancelled,
    _write_video,
)
from app.drama import (  # noqa: E402
    LlmOutputError,
    _normalize_script,
    _safe_int,
    build_storyboard,
)


def _ltx_params(**kw):
    base = dict(prompt="a cat on the moon, cinematic")
    base.update(kw)
    p = LtxParams(**base)
    p.validate()
    return p


def _wait_terminal(mgr, tid, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = mgr.get(tid)
        if t is None or t.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED):
            return t
        time.sleep(0.02)
    return mgr.get(tid)


# ---------------------------------------------------------------------------
# L1 — cancellation classification
# ---------------------------------------------------------------------------

def test_cancel_during_generation_is_cancelled_not_failed(tmp_path, monkeypatch):
    """The step callback raises _Cancelled; the manager must map it to CANCELLED."""
    monkeypatch.setattr(ltx_runtime, "_load_pipeline", lambda *a, **k: object())

    def fake_generate(pipe, p, task):
        task._cancel.set()          # user pressed cancel mid-run
        raise _Cancelled()          # what diffusers does inside the callback

    monkeypatch.setattr(ltx_runtime, "_generate", fake_generate)

    mgr = LtxManager(tmp_path / "out")
    task = mgr.start(_ltx_params())
    t = _wait_terminal(mgr, task.id)
    assert t.state == TaskState.CANCELLED, f"state={t.state} error={t.error}"
    assert t.error == ""
    assert t.elapsed_s >= 0.0


def test_cancel_after_load_records_elapsed(tmp_path, monkeypatch):
    """Cancel flag set while loading → CANCELLED branch, elapsed_s populated."""

    def load_then_cancel(model_id, mode, task):
        task._cancel.set()
        return object()

    monkeypatch.setattr(ltx_runtime, "_load_pipeline", load_then_cancel)

    mgr = LtxManager(tmp_path / "out")
    task = mgr.start(_ltx_params())
    t = _wait_terminal(mgr, task.id)
    assert t.state == TaskState.CANCELLED
    assert t.elapsed_s >= 0.0


# ---------------------------------------------------------------------------
# L2 — output path on fallback
# ---------------------------------------------------------------------------

def test_write_video_fallback_returns_gif_and_removes_partial_mp4(tmp_path, monkeypatch):
    out = tmp_path / "clip.mp4"
    out.write_bytes(b"partial mp4 bytes")

    fake_imageio = types.SimpleNamespace()
    written: list[str] = []

    def boom_writer(*a, **k):
        raise RuntimeError("no ffmpeg available")

    def fake_mimsave(path, arr, **k):
        written.append(path)

    fake_imageio.get_writer = boom_writer
    fake_imageio.mimsave = fake_mimsave
    monkeypatch.setitem(sys.modules, "imageio", fake_imageio)

    frames = [types.SimpleNamespace(dtype="uint8")]
    result = _write_video(frames, out, fps=24, fmt="mp4")

    assert result == out.with_suffix(".gif")
    assert written == [str(result)]
    assert not out.exists(), "partial mp4 must be removed on gif fallback"


def test_write_video_gif_format_returns_same_path(tmp_path, monkeypatch):
    out = tmp_path / "clip.gif"
    fake_imageio = types.SimpleNamespace()
    written: list[str] = []
    fake_imageio.mimsave = lambda path, arr, **k: written.append(path)
    monkeypatch.setitem(sys.modules, "imageio", fake_imageio)

    result = _write_video([types.SimpleNamespace(dtype="uint8")], out,
                          fps=24, fmt="gif")
    assert result == out
    assert written == [str(out)]


# ---------------------------------------------------------------------------
# L3 — bounded task history
# ---------------------------------------------------------------------------

def test_task_history_is_bounded(tmp_path, monkeypatch):
    """More than _MAX_TASKS finished tasks → oldest terminal ones are pruned."""
    mgr = LtxManager(tmp_path / "out")
    mgr._MAX_TASKS = 5  # shrink for a fast test

    def fast_run(self, task):
        task.started_at = time.time()
        task.state = TaskState.DONE
        task.finished_at = time.time()

    monkeypatch.setattr(LtxManager, "_run", fast_run)

    ids = []
    for i in range(12):
        t = mgr.start(_ltx_params(prompt=f"job {i}"))
        ids.append(t.id)
        # wait until the worker finished so the busy guard releases
        deadline = time.time() + 5
        while time.time() < deadline and mgr.active() is not None:
            time.sleep(0.005)

    assert len(mgr._tasks) <= 5
    # the most recent tasks survive; the oldest were pruned
    assert mgr.get(ids[-1]) is not None
    assert mgr.get(ids[0]) is None


# ---------------------------------------------------------------------------
# MNN — streaming timeout / error state
# ---------------------------------------------------------------------------

class _FakeLlm:
    """Minimal stand-in for the opaque MNN.llm.Llm object."""

    def __init__(self, generate_raises=None, block_forever=False):
        self._generate_raises = generate_raises
        self._block_forever = block_forever
        self.generate_called = threading.Event()

    def apply_chat_template(self, msg):
        return msg.get("content", "")

    def set_config(self, cfg):
        return None

    def response(self, prompt, stream):
        return "hello from fake mnn"

    def generate_init(self, prompt):
        return None

    def get_context(self):
        return {"generate_str": "partial"}

    def generate(self):
        self.generate_called.set()
        if self._generate_raises is not None:
            raise self._generate_raises
        if self._block_forever:
            threading.Event().wait()  # never set → simulates engine deadlock


@pytest.fixture
def isolated_mnn(monkeypatch):
    """Point mnn_runtime at a fake engine and restore global state after."""
    saved_llm = mnn_runtime._LLM
    saved_error = mnn_runtime._STATE.get("error", "")
    yield monkeypatch
    mnn_runtime._LLM = saved_llm
    mnn_runtime._STATE["error"] = saved_error


def test_stream_timeout_releases_lock(isolated_mnn):
    fake = _FakeLlm(block_forever=True)
    isolated_mnn.setattr(mnn_runtime, "_LLM", fake)
    isolated_mnn.setattr(mnn_runtime, "_STREAM_GENERATION_TIMEOUT_S", 0.05)

    gen = mnn_runtime.chat_stream("hi")
    with pytest.raises(TimeoutError) as exc:
        list(gen)
    assert "超时" in str(exc.value)
    assert "超时" in mnn_runtime._STATE["error"]

    # The global lock MUST be released after the timeout — otherwise one hung
    # generation bricks every subsequent MNN call.
    assert mnn_runtime._LOCK.acquire(timeout=1.0), "global lock leaked after timeout"
    mnn_runtime._LOCK.release()


def test_chat_success_clears_stale_error(isolated_mnn):
    isolated_mnn.setattr(mnn_runtime, "_LLM", _FakeLlm())
    mnn_runtime._STATE["error"] = "previous failure"

    res = mnn_runtime.chat("hello")
    assert res["text"] == "hello from fake mnn"
    assert mnn_runtime._STATE["error"] == ""


def test_chat_multimodal_success_clears_stale_error(isolated_mnn):
    isolated_mnn.setattr(mnn_runtime, "_LLM", _FakeLlm())
    mnn_runtime._STATE["error"] = "previous failure"

    res = mnn_runtime.chat_multimodal("hello")
    assert res["text"] == "hello from fake mnn"
    assert mnn_runtime._STATE["error"] == ""


def test_stream_generate_error_is_recorded(isolated_mnn):
    fake = _FakeLlm(generate_raises=RuntimeError("c++ boom"))
    isolated_mnn.setattr(mnn_runtime, "_LLM", fake)

    with pytest.raises(RuntimeError, match="c\\+\\+ boom"):
        list(mnn_runtime.chat_stream("hi"))
    assert "c++ boom" in mnn_runtime._STATE["error"]


# ---------------------------------------------------------------------------
# D1 — drama numeric-field tolerance
# ---------------------------------------------------------------------------

def test_safe_int():
    assert _safe_int(None, 4) == 4
    assert _safe_int("fast", 4) == 4
    assert _safe_int("7", 4) == 7
    assert _safe_int(3.7, 4) == 3
    assert _safe_int(True, 4) == 1
    assert _safe_int(0, 4) == 0


def _script_obj(bad_numbers=False):
    sid = "one" if bad_numbers else 1
    dur = "fast" if bad_numbers else 4
    return {
        "title": "t",
        "scenes": [{
            "scene_id": sid,
            "location": "路口",
            "shots": [{
                "shot_id": "x" if bad_numbers else 1,
                "duration_s": dur,
                "dialogue": "你好",
            }],
        }],
    }


def test_normalize_script_tolerates_garbage_numbers():
    script = _normalize_script(_script_obj(bad_numbers=True))
    # no ValueError raised; duration clamped into [2, 8]
    dur = script["scenes"][0]["shots"][0]["duration_s"]
    assert 2 <= dur <= 8
    assert script["scenes"][0]["scene_id"] == 1  # fell back to default


def test_normalize_script_normal_numbers_ok():
    script = _normalize_script(_script_obj(bad_numbers=False))
    assert script["scenes"][0]["shots"][0]["duration_s"] == 4


def test_build_storyboard_tolerates_bad_scene_id():
    script = _normalize_script(_script_obj(bad_numbers=True))
    sb = build_storyboard(script)  # must not raise
    assert sb["shot_count"] == 1


def test_normalize_script_empty_scenes_raises_llm_output_error():
    with pytest.raises(LlmOutputError):
        _normalize_script({"title": "x", "scenes": []})
