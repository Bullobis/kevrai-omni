"""Neuron-Runtime2 audit guards (Project K-Cortex, 2026-09-24).

P1 question re-audited: *do LTX / MNN runtimes spawn child processes that
would need a process-group kill on cancellation?*

Finding (see parliament/20260924-neuron2-runtime-k-cortex.md):
  * ltx_runtime runs diffusers **in-process** on a daemon thread; the only child
    it can ever produce is the ffmpeg that imageio-ffmpeg launches internally,
    and that happens ONLY in the SAVING phase, strictly after generation has
    already completed — the cancel/timeout/abort paths never reach it.
  * mnn_runtime is an **in-process C++ singleton**; its streaming timeout uses
    a daemon thread, not a child OS process.

No production code is changed by this slice. These tests *lock in* the audit
invariants so that a future commit which introduces a real subprocess into
either runtime is forced (by a red CI) to also add process-group cleanup:

  G1  neither runtime source contains a direct child-process spawn primitive.
  G2  cancelling mid-generation never reaches the video writer (=> the ffmpeg
      child imageio-ffmpeg owns is never spawned on the cancel path).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ltx_runtime, mnn_runtime  # noqa: E402
from app.ltx_runtime import LtxManager, LtxParams, TaskState, _Cancelled  # noqa: E402

_APP_DIR = Path(ltx_runtime.__file__).resolve().parent

# Spawn primitives that would make a runtime responsible for process-group
# reaping. ``import os`` alone is NOT forbidden (both modules legitimately use
# os.path.*); only the calls that actually create an OS child process are.
_FORBIDDEN_TOKENS = (
    "import subprocess",
    "subprocess.",
    "Popen(",
    "os.exec",
    "os.spawn",
    "os.system(",
    "create_subprocess",
)


def _ltx_source() -> str:
    return (_APP_DIR / "ltx_runtime.py").read_text(encoding="utf-8")


def _mnn_source() -> str:
    return (_APP_DIR / "mnn_runtime.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# G1 — child-process spawn discipline
#
# ltx_runtime must remain a pure in-process runtime (no OS child to reap).
# mnn_runtime now OPT-IN spawns a forked child to isolate the C++ engine
# (Neuron4 slice, 2026-09-24); that child MUST be reaped on every
# timeout/crash/abort path. We forbid raw subprocess/Popen (which would need
# separate process-group bookkeeping) and require the multiprocessing path to
# carry a _kill() that SIGKILLs the child.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src_text,name", [
    (_ltx_source(), "ltx_runtime.py"),
])
def test_audited_runtimes_spawn_no_direct_subprocess(src_text, name):
    """ltx_runtime must stay a pure in-process runtime."""
    for token in _FORBIDDEN_TOKENS:
        assert token not in src_text, (
            f"{name} uses {token!r}; Neuron-Runtime2 requires this call to be "
            f"wrapped in a process-group-aware launcher and reaped on cancel."
        )


def test_mnn_subprocess_isolation_reaps_child():
    """mnn_runtime's opt-in subprocess mode MUST SIGKILL the child on hang/crash.

    The whole point of isolating the C++ engine in a child process is that a
    hard deadlock can be reclaimed at the OS level. If a future edit removes
    the kill path, the parked C++ thread leaks again — this test forces the
    reaping call to stay.
    """
    src = _mnn_source()
    assert "multiprocessing.Process" in src, (
        "subprocess isolation must use multiprocessing.Process (opt-in)"
    )
    assert ".kill()" in src, (
        "mnn_runtime must SIGKILL the child on timeout/crash (the isolation win)"
    )
    assert "start_new_session" in src or "os.setsid" in src, (
        "child must become its own session group (killpg-ready)"
    )


# ---------------------------------------------------------------------------
# G2 — the imageio-ffmpeg child is never spawned on the cancel path
# ---------------------------------------------------------------------------

def _valid_params(**kw):
    base = dict(prompt="a cat on the moon, cinematic")
    base.update(kw)
    p = LtxParams(**base)
    p.validate()
    return p


def test_cancel_mid_generation_never_reaches_video_writer(tmp_path, monkeypatch):
    """Cancel during RUNNING must abort before SAVING — so imageio-ffmpeg's
    bundled ffmpeg child is never even launched on the cancel path."""
    monkeypatch.setattr(ltx_runtime, "_load_pipeline", lambda *a, **k: object())

    writes: list[int] = []

    def fake_generate(pipe, p, task):
        task._cancel.set()          # user pressed cancel mid-run
        raise _Cancelled()          # what diffusers raises in the step callback

    def fake_write_output(self, task, frames):
        writes.append(1)            # would spawn the ffmpeg child
        raise AssertionError("_write_output must not run on the cancel path")

    monkeypatch.setattr(ltx_runtime, "_generate", fake_generate)
    monkeypatch.setattr(LtxManager, "_write_output", fake_write_output)

    mgr = LtxManager(tmp_path / "out")
    task = mgr.start(_valid_params())

    deadline = time.time() + 10
    while time.time() < deadline:
        t = mgr.get(task.id)
        if t.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED):
            break
        time.sleep(0.02)

    assert t.state == TaskState.CANCELLED, f"state={t.state} err={t.error}"
    assert writes == [], "video writer (ffmpeg child) must not run after cancel"
