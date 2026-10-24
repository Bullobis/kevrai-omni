"""ComfyUI 图片工作流模板（SD1.5/SDXL 文生图）测试。

节点连线对照 ComfyUI 官方 default SD1.5 API 示例：
CheckpointLoaderSimple -> [0]MODEL/[1]CLIP/[2]VAE，KSampler 串 positive/negative/
latent，最后 VAEDecode -> SaveImage。这里重点校验：
  * 连线引用正确（CLIP/模型/VAE 的输出槽位号）
  * 取值域校验（宽高须为 8 倍数、范围、steps/cfg、batch）
  * seed<=0 变成正随机值并回填，保证同参数同结果可复现
  * 必填项缺失时抛出 ImageTemplateError（路由层转 400）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import comfyui_image as ci  # noqa: E402


def _build(**kw):
    base = dict(ckpt_name="sd15.safetensors", prompt="a cat")
    base.update(kw)
    return ci.build_image_workflow(**base)


def test_list_templates_has_two_families():
    ids = [t["id"] for t in ci.list_templates()]
    assert "sd15-txt2img" in ids
    assert "sdxl-txt2img" in ids
    for t in ci.list_templates():
        assert t["width"] >= 256
        assert t["height"] >= 256
        assert t["resolutions"]


def test_sd15_workflow_node_wiring_matches_official_example():
    b = _build()
    w = b["workflow"]
    # checkpoint 加载器
    assert w["4"]["class_type"] == "CheckpointLoaderSimple"
    assert w["4"]["inputs"]["ckpt_name"] == "sd15.safetensors"
    # 正向/负向都引用 checkpoint 的 CLIP 输出（槽位 1）
    assert w["6"]["inputs"]["clip"] == ["4", 1]
    assert w["7"]["inputs"]["clip"] == ["4", 1]
    # KSampler 的模型来自 checkpoint 槽位 0
    assert w["3"]["inputs"]["model"] == ["4", 0]
    # VAEDecode 的 VAE 来自 checkpoint 槽位 2
    assert w["8"]["inputs"]["vae"] == ["4", 2]
    # KSampler 串起正/负/潜空间
    assert w["3"]["inputs"]["positive"] == ["6", 0]
    assert w["3"]["inputs"]["negative"] == ["7", 0]
    assert w["3"]["inputs"]["latent_image"] == ["5", 0]
    # VAEDecode -> SaveImage
    assert w["8"]["inputs"]["samples"] == ["3", 0]
    assert w["9"]["inputs"]["images"] == ["8", 0]
    assert b["output_node"] == "9"


def test_seed_zero_becomes_positive_and_is_reproducible_input():
    b1 = _build(seed=0)
    b2 = _build(seed=0)
    # seed=0 被替换成正随机值，且回填进 params（供前端展示/复现）
    assert b1["params"]["seed"] > 0
    assert b1["workflow"]["3"]["inputs"]["seed"] == b1["params"]["seed"]
    # 用回填的正种子再构建，两次工作流完全一致（可复现）
    b3 = _build(seed=b1["params"]["seed"])
    assert b3["workflow"] == b1["workflow"]
    # 不同 seed=0 通常得到不同值（随机性）；不强求绝对不同，只校验正
    assert b2["params"]["seed"] > 0


def test_negative_prompt_default_is_applied():
    b = _build()
    assert b["params"]["negative_prompt"] == ci.DEFAULT_NEGATIVE
    # 自定义负向覆盖默认
    b2 = _build(negative_prompt="bad")
    assert b2["params"]["negative_prompt"] == "bad"


def test_missing_ckpt_raises():
    with pytest.raises(ci.ImageTemplateError):
        ci.build_image_workflow(prompt="x")
    with pytest.raises(ci.ImageTemplateError):
        ci.build_image_workflow(ckpt_name="   ", prompt="x")


def test_empty_prompt_raises():
    with pytest.raises(ci.ImageTemplateError):
        ci.build_image_workflow(ckpt_name="x.safetensors", prompt="   ")


def test_width_must_be_multiple_of_8():
    with pytest.raises(ci.ImageTemplateError):
        _build(width=513)
    _build(width=512)  # 8 倍数通过


def test_width_out_of_range():
    with pytest.raises(ci.ImageTemplateError):
        _build(width=16)
    with pytest.raises(ci.ImageTemplateError):
        _build(width=100000)


def test_steps_and_cfg_range():
    with pytest.raises(ci.ImageTemplateError):
        _build(steps=0)
    with pytest.raises(ci.ImageTemplateError):
        _build(steps=1000)
    with pytest.raises(ci.ImageTemplateError):
        _build(cfg=100)
    _build(steps=20, cfg=7.0)


def test_batch_range():
    with pytest.raises(ci.ImageTemplateError):
        _build(batch_size=0)
    with pytest.raises(ci.ImageTemplateError):
        _build(batch_size=99)
    b = _build(batch_size=4)
    assert b["workflow"]["5"]["inputs"]["batch_size"] == 4


def test_unknown_template_raises():
    with pytest.raises(ci.ImageTemplateError):
        ci.build_image_workflow("flux-turbo", ckpt_name="x", prompt="y")


def test_sdxl_template_defaults_1024():
    b = ci.build_image_workflow("sdxl-txt2img", ckpt_name="sdxl.safetensors",
                                prompt="a cat")
    assert b["params"]["width"] == 1024
    assert b["params"]["height"] == 1024
    assert b["params"]["cfg"] == 6.0
