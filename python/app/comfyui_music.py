"""ComfyUI 音乐生成工作流模板（ACE-Step 1.5）。

为什么单独成模块，而不是塞进 ``comfyui_runtime``：
音乐图的节点体系（音频潜变量、音频 VAE、文本编码器是 Qwen 而非 SD CLIP）
与图像/视频图差异极大，混在一起会让两边都难读。

**所有节点名均来自 ComfyUI 官方文档核实**，不靠记忆拼：
``TextEncodeAceStepAudio1.5`` / ``EmptyAceStep1.5LatentAudio`` /
``ModelSamplingAuraFlow`` / ``VAEDecodeAudio`` / ``SaveAudioMP3`` /
``DualCLIPLoader`` / ``UNETLoader`` / ``KSampler`` / ``ConditioningZeroOut``。

已知且必须如实说明的限制：
* ``SaveAudioMP3`` 已被官方标记为 deprecated，未来可能移除。
  这里仍使用它，因为它是官方 ACE-Step 工作流验证过的产物节点；
  若用户环境已移除该节点，ComfyUI 会直接报节点不存在，我们如实透传。
* turbo 蒸馏版**不用 CFG**（cfg=1），负向条件由 ``ConditioningZeroOut``
  给出空条件。这是官方工作流的做法，不是省略。
"""

from __future__ import annotations

from typing import Any

from .comfyui_runtime import fix_randomize_seeds

# ---------------------------------------------------------------------------
# 已核实的取值域（超出这些范围 ComfyUI 会拒绝或静默截断）
# ---------------------------------------------------------------------------

#: TextEncodeAceStepAudio1.5 的 duration 允许 0-2000，
#: 但 EmptyAceStep1.5LatentAudio 的 seconds 只允许 1-1000。
#: 真正生效的上限是 **1000**，且两个节点必须用同一个值，
#: 否则 latent 长度与条件里的时长不一致，出来的音频时长跟你想的不一样。
MIN_DURATION_S = 1.0
MAX_DURATION_S = 1000.0

MIN_BPM, MAX_BPM = 10, 300
TIMESIGNATURES = ("2", "3", "4", "6")
LANGUAGES = (
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en", "es", "fa",
    "fi", "fr", "he", "hi", "hr", "ht", "hu", "id", "is", "it", "ja", "ko",
    "la", "lt", "ms", "ne", "nl", "no", "pa", "pl", "pt", "ro", "ru", "sa",
    "sk", "sr", "sv", "sw", "ta", "te", "th", "tl", "tr", "uk", "ur", "vi",
    "yue", "zh", "unknown",
)
_KEY_ROOTS = ("C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G",
              "G#", "Ab", "A", "A#", "Bb", "B")
KEYSCALES = tuple(f"{r} {m}" for r in _KEY_ROOTS for m in ("major", "minor"))

#: 官方工作流里验证过的 turbo 版文件组合。
ACE15_TURBO_FILES = {
    "unet_name": "acestep_v1.5_xl_turbo_bf16.safetensors",
    "vae_name": "ace_1.5_vae.safetensors",
    "clip_name1": "qwen_0.6b_ace15.safetensors",
    "clip_name2": "qwen_1.7b_ace15.safetensors",
}

#: 采样参数的取值范围（来自官方文档）。
PARAM_RANGES = {
    "cfg_scale": (0.0, 100.0),
    "temperature": (0.0, 2.0),
    "top_p": (0.0, 2000.0),
    "top_k": (0, 100),
    "min_p": (0.0, 1.0),
    "shift": (0.0, 100.0),
    "denoise": (0.0, 1.0),
}


class MusicTemplateError(ValueError):
    """模板参数不合法或模板不存在。"""


def list_templates() -> list[dict[str, Any]]:
    """列出可用模板。"""
    return [
        {
            "id": "ace-step-1.5-turbo",
            "label": "ACE-Step 1.5 XL Turbo（官方默认值）",
            "engine": "comfyui",
            "model": "ace-step-1.5",
            "steps": 8,
            "cfg": 1.0,
            "files": dict(ACE15_TURBO_FILES),
            "note": (
                "8 步出图，蒸馏版不开 CFG（cfg=1）。"
                "文件名为官方工作流验证值；若你的模型文件名不同，"
                "请用 ace-step-1.5-custom 自行指定。"
            ),
        },
        {
            "id": "ace-step-1.5-custom",
            "label": "ACE-Step 1.5（自定义文件名/参数）",
            "engine": "comfyui",
            "model": "ace-step-1.5",
            "steps": 8,
            "cfg": 1.0,
            "files": dict(ACE15_TURBO_FILES),
            "note": (
                "与 turbo 同一张图，但 unet/vae/clip 文件名、步数、"
                "CFG、shift 全部由调用方指定。用于非 turbo 检查点或改名过的文件。"
            ),
        },
    ]


def _check_range(name: str, value: float, lo: float, hi: float) -> float:
    v = float(value)
    if not (lo <= v <= hi):
        raise MusicTemplateError(f"{name} 必须在 [{lo}, {hi}] 之间，当前为 {value!r}")
    return v


def _check_choice(name: str, value: str, allowed: tuple[str, ...]) -> str:
    v = str(value)
    if v not in allowed:
        raise MusicTemplateError(
            f"{name} 必须是 {list(allowed)} 之一，当前为 {value!r}"
        )
    return v


def build_music_workflow(
    template_id: str = "ace-step-1.5-turbo",
    *,
    tags: str = "",
    lyrics: str = "",
    duration_s: float = 120.0,
    bpm: int = 120,
    seed: int = 0,
    steps: int | None = None,
    cfg: float | None = None,
    language: str = "en",
    keyscale: str = "C major",
    timesignature: str = "4",
    generate_audio_codes: bool = True,
    cfg_scale: float = 2.0,
    temperature: float = 0.85,
    top_p: float = 0.9,
    top_k: int = 0,
    min_p: float = 0.0,
    shift: float = 1.73,
    denoise: float = 1.0,
    sampler_name: str = "euler",
    scheduler: str = "simple",
    filename_prefix: str = "audio/kevrai-music",
    quality: str = "V0",
    unet_name: str | None = None,
    vae_name: str | None = None,
    clip_name1: str | None = None,
    clip_name2: str | None = None,
) -> dict[str, Any]:
    """按 ComfyUI API 格式构建一张 ACE-Step 1.5 音乐生成图。

    返回 ``{"template": ..., "workflow": {...}, "seed_fixed": n, "output_node": ...}``。
    ``workflow`` 可直接 POST 给 ``/prompt``。
    """
    known = {t["id"]: t for t in list_templates()}
    spec = known.get(template_id)
    if spec is None:
        raise MusicTemplateError(
            f"未知模板：{template_id!r}（可用：{sorted(known)}）"
        )

    if not str(tags).strip():
        raise MusicTemplateError(
            "tags 不能为空 —— 它是风格/情绪/乐器的描述，是音乐生成的唯一必要输入。"
        )
    duration = _check_range("duration_s", duration_s, MIN_DURATION_S, MAX_DURATION_S)
    bpm = int(_check_range("bpm", bpm, MIN_BPM, MAX_BPM))
    language = _check_choice("language", language, LANGUAGES)
    keyscale = _check_choice("keyscale", keyscale, KEYSCALES)
    timesignature = _check_choice("timesignature", timesignature, TIMESIGNATURES)
    _check_choice("quality", quality, ("V0", "128k", "320k"))
    # 显式取值，不用 locals() 反射 —— 反射在这里能跑但一改参数名就静默失效。
    numeric = {
        "cfg_scale": cfg_scale,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "min_p": min_p,
        "shift": shift,
        "denoise": denoise,
    }
    for pname, val in numeric.items():
        _check_range(pname, val, *PARAM_RANGES[pname])

    steps = int(steps) if steps is not None else int(spec["steps"])
    if steps < 1:
        raise MusicTemplateError(f"steps 必须 >= 1，当前为 {steps}")
    cfg = float(cfg) if cfg is not None else float(spec["cfg"])
    _check_range("cfg", cfg, 0.0, 100.0)

    files = dict(spec["files"])
    files["unet_name"] = unet_name or files["unet_name"]
    files["vae_name"] = vae_name or files["vae_name"]
    files["clip_name1"] = clip_name1 or files["clip_name1"]
    files["clip_name2"] = clip_name2 or files["clip_name2"]

    # 节点编号固定，方便按 id 定位；ComfyUI 只要求 id 唯一。
    workflow: dict[str, Any] = {
        "1": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": files["clip_name1"],
                "clip_name2": files["clip_name2"],
                "type": "ace",
                "device": "default",
            },
        },
        "2": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": files["vae_name"]},
        },
        "3": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": files["unet_name"],
                "weight_dtype": "default",
            },
        },
        # flow 类模型需要重新调噪声调度；ACE-Step 官方工作流就带这一节点。
        "4": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["3", 0], "shift": shift, "sampling": "flow"},
        },
        "5": {
            "class_type": "EmptyAceStep1.5LatentAudio",
            "inputs": {"seconds": duration, "batch_size": 1},
        },
        "6": {
            "class_type": "TextEncodeAceStepAudio1.5",
            "inputs": {
                "clip": ["1", 0],
                "tags": tags,
                "lyrics": lyrics,
                "seed": int(seed),
                "bpm": bpm,
                # 必须与节点 5 的 seconds 一致，否则条件与潜变量长度对不上。
                "duration": duration,
                "timesignature": timesignature,
                "language": language,
                "keyscale": keyscale,
                "generate_audio_codes": bool(generate_audio_codes),
                "cfg_scale": float(cfg_scale),
                "temperature": float(temperature),
                "top_p": float(top_p),
                "top_k": int(top_k),
                "min_p": float(min_p),
            },
        },
        # turbo 蒸馏版刻意不用 CFG：负向给空条件。这是官方工作流的做法。
        "7": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["6", 0]},
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
                "seed": int(seed),
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": float(denoise),
            },
        },
        "9": {
            "class_type": "VAEDecodeAudio",
            "inputs": {"samples": ["8", 0], "vae": ["2", 0]},
        },
        "10": {
            "class_type": "SaveAudioMP3",
            "inputs": {
                "audio": ["9", 0],
                "filename_prefix": filename_prefix,
                "quality": quality,
            },
        },
    }

    # ComfyUI 导出的 randomize 开关会让同样输入产出不同结果，且不报错。
    # 复用图像侧已有的修正逻辑，保证"同参数同结果"。
    workflow, fixed = fix_randomize_seeds(workflow)

    return {
        "template": template_id,
        "workflow": workflow,
        "seed_fixed": fixed,
        "output_node": "10",
        "duration_s": duration,
        "notes": [
            "SaveAudioMP3 官方已标记 deprecated，未来可能移除；"
            "若你的 ComfyUI 已移除该节点会直接报节点不存在。",
            f"duration 上限 {MAX_DURATION_S:.0f} 秒来自 EmptyAceStep1.5LatentAudio "
            "（1-1000），而非文本编码节点（0-2000）；两者已同步为同一值。",
            "turbo 蒸馏版 cfg 固定为 1（不做 classifier-free guidance）；"
            "非 turbo 检查点请改用 ace-step-1.5-custom 并把 cfg 调到 2 以上。",
        ],
    }
