"""Neuron3-Misc 审计回归测试 — ``app.converter``。

审计范围（Project K-Cortex 第三切片）：格式探测 / 转换边界 / 路径穿越 / 资源限制 / 异常。
本组测试全部用 mock 替换子进程，绝不真实调用 MNNConvert / git / 转换脚本。

覆盖的经证据确认的修复：
  1. ``_worker_mnnconvert`` 在运行子进程前必须创建输出父目录（与其余 4 个 worker
     一致），否则嵌套输出路径会因目录缺失而误报"未生成 .mnn 文件"。
  2. ``_append_log`` 对任务日志做有界裁剪，防止冗长转换输出导致单任务内存无界增长。

同时固化已审计确认"无问题"的防御行为，防止后续回退。
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from app import converter as C


# ---------------------------------------------------------------------------
# Fix 1: mnnconvert worker must create the output parent directory up front
# ---------------------------------------------------------------------------
def test_mnnconvert_creates_dst_parent_before_subprocess(tmp_path: Path):
    src = tmp_path / "in.onnx"
    src.write_bytes(b"onnx-payload")
    dst = tmp_path / "nested" / "dir" / "out.mnn"  # parent does NOT exist yet

    observed: dict[str, object] = {}

    def fake_run(t, cmd, **kw):
        observed["parent_exists"] = dst.parent.is_dir()
        observed["cmd"] = cmd
        # 模拟 MNNConvert 写出产物（目录此时必须已存在）
        dst.write_bytes(b"mnn-binary")
        return 0, ""

    task = C.ConvertTask(
        id="t1", kind=C.KIND_ONNX_TO_MNN, src=str(src), dst=str(dst),
        options={"converter": "echo-mnnconvert"},
    )
    with mock.patch.object(C, "_run_subprocess", side_effect=fake_run):
        result = C._worker_mnnconvert(task)

    assert observed["parent_exists"] is True, (
        "输出父目录必须在子进程启动前创建（对齐 hf_to_gguf 等 worker 的 mkdir 行为）"
    )
    assert result["format"] == "mnn"
    assert result["mnn_file"] == str(dst)


def test_mnnconvert_missing_src_raises_clear_error(tmp_path: Path):
    task = C.ConvertTask(
        id="t2", kind=C.KIND_ONNX_TO_MNN,
        src=str(tmp_path / "nope.onnx"), dst=str(tmp_path / "o.mnn"),
        options={"converter": "echo"},
    )
    with pytest.raises(RuntimeError, match="源模型文件不存在"):
        C._worker_mnnconvert(task)


# ---------------------------------------------------------------------------
# Fix 2: task log is bounded (memory control)
# ---------------------------------------------------------------------------
def test_append_log_is_bounded_to_cap():
    task = C.ConvertTask(id="t3", kind="k", src="s", dst="d")
    n = 5000
    for i in range(n):
        C._append_log(task, f"line-{i}")
    assert len(task.log_lines) <= C._LOG_LINES_CAP, (
        "冗长转换输出不得让单任务日志无界增长"
    )
    # 保留的是最近的日志（展示窗口取末尾 200）
    assert "line-4999" in task.log_lines[-1]
    state = C._task_state(task)
    assert len(state["log"]) <= 200


# ---------------------------------------------------------------------------
# 已审计确认的防御行为（固化，防止回退）
# ---------------------------------------------------------------------------
def test_hf_workers_reject_src_without_config_json(tmp_path: Path):
    # 格式探测：HF 系列 worker 不靠扩展名，而是要求目录里有 config.json。
    bad_src = tmp_path / "plain-dir"
    bad_src.mkdir()  # 故意不放 config.json
    task = C.ConvertTask(
        id="t4", kind=C.KIND_HF_TO_GGUF, src=str(bad_src), dst=str(tmp_path / "o.gguf"),
    )
    with pytest.raises(RuntimeError, match="缺少 config.json"):
        C._worker_hf_to_gguf(task)


def test_hf_workers_reject_missing_src_dir(tmp_path: Path):
    task = C.ConvertTask(
        id="t5", kind=C.KIND_HF_TO_MNN,
        src=str(tmp_path / "does-not-exist"), dst=str(tmp_path / "out"),
    )
    with pytest.raises(RuntimeError, match="源模型目录不存在"):
        C._worker_hf_to_mnn(task)


def test_start_convert_rejects_unknown_kind_synchronously():
    # kind 校验必须在登记任务/起线程之前同步抛出（ValueError 映射为 HTTP 400）。
    with pytest.raises(ValueError, match="不支持的转换类型"):
        C.start_convert("not-a-real-kind", "/x", "/y")


def test_cancel_task_unknown_id_returns_false():
    assert C.cancel_task("does-not-exist") is False
