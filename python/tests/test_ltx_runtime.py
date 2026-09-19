"""Tests for the LTX-2.5 runtime (app.ltx_runtime).

These tests cover parameter validation, preset resolution, task lifecycle,
cancellation, and the capabilities descriptor — all without requiring
torch/diffusers or a GPU. The actual heavy pipeline is exercised only when
the engine is installed (skipped otherwise).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ltx_runtime import (  # noqa: E402
    PRESETS,
    LtxBusyError,
    LtxManager,
    LtxParamError,
    LtxParams,
    TaskState,
    capabilities,
)

# ---------- parameter validation ----------

def _make_params(**kw):
    base = dict(prompt="a cat playing piano on the moon, cinematic")
    base.update(kw)
    return LtxParams(**base)


def test_default_params_valid():
    p = _make_params()
    p.validate()  # should not raise
    assert p.preset == "balanced"
    # frames normalized to 8k+1
    assert (p.num_frames - 1) % 8 == 0


def test_empty_prompt_rejected():
    with pytest.raises(LtxParamError):
        _make_params(prompt="   ").validate()


def test_prompt_too_long():
    with pytest.raises(LtxParamError):
        _make_params(prompt="x" * 2001).validate()


def test_invalid_mode():
    with pytest.raises(LtxParamError):
        _make_params(mode="x2v").validate()


def test_invalid_preset():
    with pytest.raises(LtxParamError):
        _make_params(preset="mega").validate()


def test_dimensions_rounded_to_multiple_of_32():
    p = _make_params(width=770, height=430)
    p.validate()
    assert p.width % 32 == 0
    assert p.height % 32 == 0


def test_dimensions_bounds():
    with pytest.raises(LtxParamError):
        _make_params(width=10).validate()
    with pytest.raises(LtxParamError):
        _make_params(width=99999).validate()


def test_frames_bounds():
    with pytest.raises(LtxParamError):
        _make_params(num_frames=1).validate()
    with pytest.raises(LtxParamError):
        _make_params(num_frames=999).validate()


def test_frames_normalized_to_8k_plus_1():
    p = _make_params(num_frames=100)
    p.validate()
    assert p.num_frames == 97  # (100-1)//8*8+1


def test_steps_bounds():
    with pytest.raises(LtxParamError):
        _make_params(num_inference_steps=0).validate()
    with pytest.raises(LtxParamError):
        _make_params(num_inference_steps=200).validate()


def test_guidance_bounds():
    with pytest.raises(LtxParamError):
        _make_params(guidance_scale=0).validate()
    with pytest.raises(LtxParamError):
        _make_params(guidance_scale=99).validate()


def test_i2v_requires_image():
    with pytest.raises(LtxParamError):
        _make_params(mode="i2v").validate()


def test_i2v_missing_image_file(tmp_path):
    missing = tmp_path / "nope.png"
    with pytest.raises(LtxParamError):
        _make_params(mode="i2v", image_path=str(missing)).validate()


def test_i2v_with_image_ok(tmp_path):
    img = tmp_path / "ref.png"
    img.write_bytes(b"\x89PNG\r\n")
    p = _make_params(mode="i2v", image_path=str(img))
    p.validate()
    assert p.mode == "i2v"


def test_seed_bounds():
    with pytest.raises(LtxParamError):
        _make_params(seed=-(2**32)).validate()


def test_strength_bounds():
    with pytest.raises(LtxParamError):
        _make_params(strength=0).validate()
    with pytest.raises(LtxParamError):
        _make_params(strength=2).validate()


def test_output_format():
    with pytest.raises(LtxParamError):
        _make_params(output_format="avi").validate()
    p = _make_params(output_format="gif")
    p.validate()


# ---------- presets ----------

def test_all_presets_valid():
    for pid, preset in PRESETS.items():
        p = LtxParams(
            prompt="test", preset=pid,
            width=preset["width"], height=preset["height"],
            num_frames=preset["num_frames"],
            num_inference_steps=preset["num_inference_steps"],
            guidance_scale=preset["guidance_scale"],
        )
        p.validate()


# ---------- capabilities ----------

def test_capabilities_descriptor():
    cap = capabilities()
    assert cap["model"] == "Lightricks/LTX-2.5"
    assert "t2v" in [m["id"] for m in cap["modes"]]
    assert "i2v" in [m["id"] for m in cap["modes"]]
    assert isinstance(cap["engine_ready"], bool)
    assert "balanced" in [p["id"] for p in cap["presets"]]
    assert "limits" in cap


# ---------- task lifecycle (without torch: fails fast at LOADING) ----------

def test_manager_starts_task_and_reports_failure_without_engine(tmp_path):
    mgr = LtxManager(tmp_path / "out")
    task = mgr.start(_make_params())
    assert task.state in (TaskState.QUEUED, TaskState.LOADING, TaskState.FAILED)
    # Wait for the worker to finish (it should fail fast if torch is missing)
    deadline = time.time() + 30
    while time.time() < deadline:
        snap = mgr.get(task.id)
        if snap.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED):
            break
        time.sleep(0.1)
    snap = mgr.get(task.id)
    # If torch/diffusers are not installed, we expect FAILED with LtxEngineMissing
    # semantics in the error message. If they ARE installed, the pipeline load
    # may fail for other reasons (no model weights), but the task must still
    # reach a terminal state without crashing the manager.
    assert snap.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED)
    if snap.state == TaskState.FAILED:
        assert snap.error  # error message populated


def test_manager_single_flight(tmp_path, monkeypatch):
    """A second start while one task is still active must raise LtxBusyError.

    The first revision of this test called start() twice back-to-back and
    expected the busy error, which made it timing-dependent: the worker runs on
    a daemon thread, and on a fast machine it could reach a terminal state
    before the second start() was evaluated.  _run_safe clears _active in its
    finally block, so once the worker finished the second start was legitimately
    allowed — the assertion then failed with "DID NOT RAISE" on CI while passing
    locally.  That is a flaw in the test, not in the manager: the manager's
    contract is "at most one *active* task", not "at most one task ever".

    Pin the worker inside _run with an event so the busy state is guaranteed to
    still hold when the second start() runs, then release it.
    """
    import threading

    entered = threading.Event()
    release = threading.Event()

    real_run = LtxManager._run

    def blocked_run(self, task):
        entered.set()
        # Hold the task in RUNNING until the test allows it to finish.
        release.wait(timeout=10)
        return real_run(self, task)

    monkeypatch.setattr(LtxManager, "_run", blocked_run)

    mgr = LtxManager(tmp_path / "out")
    try:
        first = mgr.start(_make_params())
        # Wait until the worker has actually entered _run, so _active is set
        # and the task is in a non-terminal state.
        assert entered.wait(timeout=10), "worker never started"

        with pytest.raises(LtxBusyError):
            mgr.start(_make_params(prompt="second"))

        # The first task is still the active one — the rejected start must not
        # have displaced it.
        assert mgr.get(first.id) is not None
        assert mgr.active() is not None
        assert mgr.active()["id"] == first.id
    finally:
        release.set()

    # Once the worker is allowed to finish, _active is cleared and a new start
    # is accepted again — the busy guard must not latch permanently.
    deadline = time.time() + 10
    while time.time() < deadline and mgr.active() is not None:
        time.sleep(0.05)
    assert mgr.active() is None
    second = mgr.start(_make_params(prompt="third"))
    assert second.id != first.id


def test_manager_cancel(tmp_path):
    mgr = LtxManager(tmp_path / "out")
    task = mgr.start(_make_params())
    assert mgr.cancel(task.id) in (True, False)
    # Wait for terminal state
    deadline = time.time() + 30
    while time.time() < deadline:
        snap = mgr.get(task.id)
        if snap.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED):
            break
        time.sleep(0.1)
    snap = mgr.get(task.id)
    assert snap.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED)


def test_manager_cancel_unknown_task(tmp_path):
    mgr = LtxManager(tmp_path / "out")
    assert mgr.cancel("does-not-exist") is False


def test_manager_list_tasks(tmp_path):
    mgr = LtxManager(tmp_path / "out")
    task = mgr.start(_make_params())
    tasks = mgr.list_tasks()
    assert len(tasks) >= 1
    assert tasks[0]["id"] == task.id


def test_snapshot_contains_required_fields(tmp_path):
    mgr = LtxManager(tmp_path / "out")
    task = mgr.start(_make_params())
    snap = mgr.get(task.id).snapshot()
    for key in ("id", "state", "progress", "prompt", "width", "height",
                "num_frames", "fps", "elapsed_s"):
        assert key in snap


# ---------- extreme / adversarial inputs ----------

@pytest.mark.parametrize("evil", [
    "'; DROP TABLE models;--",
    "<script>alert(1)</script>",
    "${jndi:ldfn://x}",
    "../../../etc/passwd",
    "\x00\x01\x02",
    "🚀" * 500,
])
def test_adversarial_prompts(evil):
    if len(evil) > 2000:
        with pytest.raises(LtxParamError):
            _make_params(prompt=evil).validate()
    else:
        p = _make_params(prompt=evil)
        p.validate()  # must not raise
        assert p.prompt == evil


def test_very_large_dimensions_auto_clamped():
    p = _make_params(width=10000, height=10000)
    # width/height exceed hi -> LtxParamError
    with pytest.raises(LtxParamError):
        p.validate()


def test_negative_fps():
    with pytest.raises(LtxParamError):
        _make_params(fps=-5).validate()


def test_float_steps_rejected():
    with pytest.raises(LtxParamError):
        _make_params(num_inference_steps=1.5).validate()
