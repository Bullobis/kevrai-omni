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
    LtxBusyError,
    LtxEngineMissing,
    LtxManager,
    LtxParamError,
    LtxParams,
    PRESETS,
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


def test_manager_single_flight(tmp_path):
    mgr = LtxManager(tmp_path / "out")
    mgr.start(_make_params())
    # Second start while first is active must raise LtxBusyError
    with pytest.raises(LtxBusyError):
        mgr.start(_make_params(prompt="second"))


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
