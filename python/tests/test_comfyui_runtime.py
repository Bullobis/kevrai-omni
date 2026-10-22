"""ComfyUI 通用后端测试。

重点覆盖调研中发现的四个生产坑，尤其是 seed 随机化 —— 它不报错、
只让结果飘忽，是最难排查的一类。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import comfyui_runtime as c  # noqa: E402


def _wf(**seed_kwargs: object) -> dict[str, object]:
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 12345,
                "steps": 20,
                **seed_kwargs,  # type: ignore[arg-type]
            },
        },
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}},
    }


# ---------------------------------------------------------------------------
# 1. seed 随机化修正（坑一）
# ---------------------------------------------------------------------------

def test_randomize_seed_is_fixed():
    wf = _wf(control_after_generate="randomize")
    fixed, n = c.fix_randomize_seeds(wf)
    assert n == 1, "应该修正 1 处"
    assert fixed["3"]["inputs"]["control_after_generate"] == "fixed"


def test_already_fixed_not_counted():
    wf = _wf(control_after_generate="fixed")
    _fixed, n = c.fix_randomize_seeds(wf)
    assert n == 0


def test_no_control_field_not_counted():
    """有 seed 但没有 randomize 标记 → 不动。"""
    wf = _wf()
    _fixed, n = c.fix_randomize_seeds(wf)
    assert n == 0


def test_no_seed_node_untouched():
    wf = {"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}}
    fixed, n = c.fix_randomize_seeds(wf)
    assert n == 0
    assert fixed["1"]["inputs"]["image"] == "a.png"


def test_original_workflow_not_mutated():
    """不能改原对象 —— 调用方可能还要用它做别的事。"""
    wf = _wf(control_after_generate="randomize")
    c.fix_randomize_seeds(wf)
    assert wf["3"]["inputs"]["control_after_generate"] == "randomize"


def test_multiple_nodes_fixed():
    wf = {
        "3": {"class_type": "KSampler",
              "inputs": {"seed": 1, "control_after_generate": "randomize"}},
        "5": {"class_type": "KSampler",
              "inputs": {"noise_seed": 2, "control_after_generate": "randomize"}},
    }
    _fixed, n = c.fix_randomize_seeds(wf)
    assert n == 2


def test_empty_workflow_safe():
    fixed, n = c.fix_randomize_seeds({})
    assert fixed == {} and n == 0


def test_non_dict_node_passthrough():
    """节点不是 dict 时不能崩。"""
    fixed, n = c.fix_randomize_seeds({"1": "garbage"})
    assert fixed["1"] == "garbage" and n == 0


# ---------------------------------------------------------------------------
# 2. 超时按任务类别区分（坑四相关）
# ---------------------------------------------------------------------------

def test_video_timeout_much_longer_than_image():
    """视频几十秒到几分钟，图像只有几秒 —— 同一个超时必然有一边不合适。"""
    assert c.DEFAULT_TIMEOUTS["video"] > c.DEFAULT_TIMEOUTS["image"] * 5


def test_all_queue_classes_have_timeout():
    for cls in c.QUEUE_CLASSES:
        assert cls in c.DEFAULT_TIMEOUTS, f"{cls} 缺少默认超时"


# ---------------------------------------------------------------------------
# 3. 错误处理：ComfyUI 的拒绝原因必须透传
# ---------------------------------------------------------------------------

def test_empty_workflow_rejected():
    client = c.ComfyUIClient()
    with pytest.raises(c.ComfyUIError) as ei:
        client.submit({})
    assert "工作流为空" in str(ei.value)


def test_view_url_contains_params():
    client = c.ComfyUIClient("http://127.0.0.1:8188")
    url = client.view_url("a.png", subfolder="out")
    assert "a.png" in url
    assert "out" in url


def test_base_url_trailing_slash_normalised():
    client = c.ComfyUIClient("http://127.0.0.1:8188/")
    assert client.base_url == "http://127.0.0.1:8188"


def test_unavailable_is_false_when_no_server():
    """连不上不该抛，只返回 False —— 能力探测必须不致命。"""
    client = c.ComfyUIClient("http://127.0.0.1:59999", timeout=1.0)
    assert client.is_available() is False


def test_capabilities_reports_error_not_crash():
    caps = c.capabilities("http://127.0.0.1:59999")
    assert caps["available"] is False
    assert "error" in caps


def test_free_returns_false_instead_of_raising():
    """释放显存失败不能让整个流程挂掉。"""
    client = c.ComfyUIClient("http://127.0.0.1:59999", timeout=1.0)
    assert client.free() is False


def test_interrupt_returns_false_instead_of_raising():
    client = c.ComfyUIClient("http://127.0.0.1:59999", timeout=1.0)
    assert client.interrupt() is False


def test_warmup_returns_false_instead_of_raising():
    """预热是优化不是必需，失败不能抛。"""
    client = c.ComfyUIClient("http://127.0.0.1:59999", timeout=1.0)
    assert client.warmup(_wf(), timeout_s=0.2) is False


def test_upload_missing_file_rejected(tmp_path):
    client = c.ComfyUIClient()
    with pytest.raises(c.ComfyUIError) as ei:
        client.upload_image(tmp_path / "nope.png")
    assert "不存在" in str(ei.value)


def test_context_manager_closes():
    with c.ComfyUIClient() as client:
        assert client is not None
