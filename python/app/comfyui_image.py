"""ComfyUI 图片生成工作流模板（Stable Diffusion 1.5 / SDXL 文生图）。

为什么单独成模块（与 ``comfyui_music`` 平级）：
``comfyui_runtime`` 只提供通用的「提交任意工作流 / 查结果 / 取图」能力，
但一张能出图的 SD 图要串 7 个节点、还要填对一长串参数。普通用户拿到的
只是一个裸 ``/prompt`` 接口，等于没法用。本模块把 ComfyUI 官方默认 SD
工作流固化成「提示词 + 少量人话参数」的模板，让没有 ComfyUI 基础的用户
也能一键出图，这也是参考 Local Dream（手机端本地 SD）的交互后定下的形态。

**节点名与连线均来自 ComfyUI 官方 API 示例（default SD1.5 workflow）核实**，
不靠记忆拼接：

    CheckpointLoaderSimple          -> [0] MODEL, [1] CLIP, [2] VAE
    CLIPTextEncode (positive)
    CLIPTextEncode (negative)
    EmptyLatentImage                -> width/height/batch_size
    KSampler                        -> 迭代去噪
    VAEDecode                       -> latent 转像素
    SaveImage                       -> 落盘 PNG

输出节点 ``SaveImage`` 的产物，由 ``comfyui_runtime`` 的 history/download_output
取回。采样器 / 调度器**不做硬枚举拒绝**：不同 ComfyUI 版本内置列表有增删，
强行白名单会误杀新版本节点，非法值由 ComfyUI 直接返回 400，路由层原样透传。

已知限制（如实说明，不掩饰）：
* 首版只固化**文生图**。图生图 / 局部重绘需要 LoadImage + VAEEncode，
  高级用户可直接把自己的工作流 JSON 交给 ``/api/comfyui/submit``。
* ``ckpt_name`` 必须是该 ComfyUI 实例 ``models/checkpoints`` 下真实存在的
  文件名，没有可用的全局默认值（每个人装的模型都不一样）。
"""

from __future__ import annotations

import random
from typing import Any

from .comfyui_runtime import fix_randomize_seeds

__all__ = [
    "ImageTemplateError",
    "list_templates",
    "build_image_workflow",
    "SD15_RESOLUTIONS",
    "SDXL_RESOLUTIONS",
]

# ---------------------------------------------------------------------------
# 已核实的取值域
# ---------------------------------------------------------------------------

#: 潜空间宽高必须是 8 的倍数（SDXL 习惯 64 的倍数，8 已满足）。
SIZE_STEP = 8
#: 合理的边长上下限，再小没有意义，再大容易直接 OOM。
MIN_SIDE = 256
MAX_SIDE = 4096
#: 一次最多出几张，避免一次把显存撑爆。
MIN_BATCH, MAX_BATCH = 1, 8
#: 步数与 CFG 的宽松区间。
MIN_STEPS, MAX_STEPS = 1, 100
MIN_CFG, MAX_CFG = 0.0, 30.0

#: 通用负向提示词（SD1.5 生态常用，仅作可改的默认值，不是质量承诺）。
DEFAULT_NEGATIVE = (
    "lowres, bad anatomy, bad hands, text, error, missing fingers, "
    "extra digit, fewer digits, cropped, worst quality, low quality, "
    "jpeg artifacts, signature, watermark, blurry"
)

#: SD1.5 在 512 分辨率训练；SDXL 在约 1024（面积 ~1 兆像素）训练。
SD15_RESOLUTIONS: tuple[tuple[int, int], ...] = (
    (512, 512),
    (512, 768),
    (768, 512),
    (768, 768),
)
SDXL_RESOLUTIONS: tuple[tuple[int, int], ...] = (
    (1024, 1024),
    (832, 1216),
    (1216, 832),
    (896, 1152),
    (1152, 896),
)


class ImageTemplateError(ValueError):
    """模板参数不合法或模板不存在。"""


def list_templates() -> list[dict[str, Any]]:
    """列出可用的图片模板及其默认参数（供前端直接渲染表单）。"""
    return [
        {
            "id": "sd15-txt2img",
            "label": "Stable Diffusion 1.5 文生图（512，省显存、速度快）",
            "engine": "comfyui",
            "model_family": "sd15",
            "mode": "txt2img",
            "width": 512,
            "height": 512,
            "resolutions": [list(r) for r in SD15_RESOLUTIONS],
            "steps": 20,
            "cfg": 7.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "default_negative": DEFAULT_NEGATIVE,
            "note": "SD1.5 在 512 分辨率上训练，出图快、显存占用低，适合入门。",
        },
        {
            "id": "sdxl-txt2img",
            "label": "Stable Diffusion XL 文生图（1024，细节更丰富）",
            "engine": "comfyui",
            "model_family": "sdxl",
            "mode": "txt2img",
            "width": 1024,
            "height": 1024,
            "resolutions": [list(r) for r in SDXL_RESOLUTIONS],
            "steps": 25,
            "cfg": 6.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "default_negative": DEFAULT_NEGATIVE,
            "note": "SDXL 在约 1024 分辨率训练，细节与构图更好，但更吃显存、更慢。",
        },
    ]


def _check_range(name: str, value: float, lo: float, hi: float) -> float:
    v = float(value)
    if not (lo <= v <= hi):
        raise ImageTemplateError(f"{name} 必须在 [{lo}, {hi}] 之间，当前为 {value!r}")
    return v


def _check_side(name: str, value: int) -> int:
    v = int(value)
    if v < MIN_SIDE or v > MAX_SIDE:
        raise ImageTemplateError(
            f"{name} 必须在 [{MIN_SIDE}, {MAX_SIDE}] 之间，当前为 {v}"
        )
    if v % SIZE_STEP != 0:
        raise ImageTemplateError(
            f"{name} 必须是 {SIZE_STEP} 的倍数（当前 {v}，最近可改为 "
            f"{round(v / SIZE_STEP) * SIZE_STEP}）"
        )
    return v


def _resolve_seed(seed: int) -> int:
    """seed<=0 视为「随机」，生成一个正值种子并返回，便于复现。"""
    try:
        s = int(seed)
    except (TypeError, ValueError) as e:
        raise ImageTemplateError(f"seed 必须是整数，当前为 {seed!r}") from e
    if s <= 0:
        # ComfyUI 种子是 64 位；留出余量，避免极端值在 JSON/节点侧出问题。
        # noqa 必须在**触发那一行**上，写在注释行里不生效（ruff 只认行尾注释）。
        s = random.randint(1, 2**53 - 1)  # noqa: S311 — 图像种子非密码学用途
    return s


def build_image_workflow(
    template_id: str = "sd15-txt2img",
    *,
    ckpt_name: str = "",
    prompt: str = "",
    negative_prompt: str | None = None,
    steps: int | None = None,
    cfg: float | None = None,
    sampler_name: str | None = None,
    scheduler: str | None = None,
    width: int | None = None,
    height: int | None = None,
    batch_size: int = 1,
    seed: int = 0,
    filename_prefix: str = "image/kevrai",
) -> dict[str, Any]:
    """按 ComfyUI API 格式构建一张标准 SD 文生图。

    返回 ``{"template", "workflow", "seed_fixed", "output_node",
    "params", "resolutions", "notes"}``；``workflow`` 可直接 POST 给
    ``/prompt``，也可拿去自行改节点。
    """
    known = {t["id"]: t for t in list_templates()}
    spec = known.get(template_id)
    if spec is None:
        raise ImageTemplateError(
            f"未知模板：{template_id!r}（可用：{sorted(known)}）"
        )

    ckpt = str(ckpt_name or "").strip()
    if not ckpt:
        raise ImageTemplateError(
            "ckpt_name 不能为空：请选择该 ComfyUI 实例 checkpoints 目录下的模型文件名。"
        )

    positive = str(prompt or "").strip()
    if not positive:
        raise ImageTemplateError("prompt（正向提示词）不能为空，否则没有可生成的内容。")

    negative = (
        DEFAULT_NEGATIVE if negative_prompt is None else str(negative_prompt)
    )

    steps_i = int(steps) if steps is not None else int(spec["steps"])
    _check_range("steps", steps_i, MIN_STEPS, MAX_STEPS)
    cfg_f = float(cfg) if cfg is not None else float(spec["cfg"])
    _check_range("cfg", cfg_f, MIN_CFG, MAX_CFG)

    sampler = str(sampler_name or spec["sampler_name"]).strip()
    sched = str(scheduler or spec["scheduler"]).strip()
    if not sampler:
        raise ImageTemplateError("sampler_name 不能为空")
    if not sched:
        raise ImageTemplateError("scheduler 不能为空")

    w = _check_side("width", width if width is not None else int(spec["width"]))
    h = _check_side("height", height if height is not None else int(spec["height"]))
    batch = int(_check_range("batch_size", batch_size, MIN_BATCH, MAX_BATCH))
    seed_i = _resolve_seed(seed)

    prefix = str(filename_prefix or "image/kevrai").strip()

    # 节点编号沿用官方默认 SD 图的编号，便于对照官方文档；连线见模块 docstring。
    workflow: dict[str, Any] = {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": w, "height": h, "batch_size": batch},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["4", 1], "text": positive},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["4", 1], "text": negative},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed_i,
                "steps": steps_i,
                "cfg": cfg_f,
                "sampler_name": sampler,
                "scheduler": sched,
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": prefix, "images": ["8", 0]},
        },
    }

    # 统一保证「同参数同结果」（我们本来就写死 seed，这里再兜底修正一次）。
    workflow, fixed = fix_randomize_seeds(workflow)

    params = {
        "ckpt_name": ckpt,
        "prompt": positive,
        "negative_prompt": negative,
        "steps": steps_i,
        "cfg": cfg_f,
        "sampler_name": sampler,
        "scheduler": sched,
        "width": w,
        "height": h,
        "batch_size": batch,
        "seed": seed_i,
        "filename_prefix": prefix,
    }

    return {
        "template": template_id,
        "mode": "txt2img",
        "workflow": workflow,
        "seed_fixed": fixed,
        "output_node": "9",
        "params": params,
        "resolutions": spec["resolutions"],
        "notes": [
            "首版仅固化文生图；图生图/局部重绘请用「高级工作流」提交自定义 JSON。",
            "ckpt_name 必须是该 ComfyUI checkpoints 目录下真实存在的文件名，"
            "可通过模型下拉（/object_info）获取。",
            "宽高必须为 8 的倍数；SD1.5 建议 512 档，SDXL 建议 1024 档，"
            "否则可能出图异常或显存暴涨。",
        ],
    }
