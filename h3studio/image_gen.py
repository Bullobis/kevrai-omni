# -*- coding: utf-8 -*-
"""
image_gen.py — 图片生成引擎（DiffSynth-Studio）
=================================================
支持两类已核实的图片管线：
  · ZImagePipeline   — Tongyi-MAI/Z-Image-Turbo（8 步快速出图，消费级友好）
  · QwenImagePipeline — Qwen/Qwen-Image-2512（高质量，中文文字渲染强）

显存管理与视频引擎共用同一套 DiffSynth 三级卸载机制（vram_config + vram_limit）。
所有 API 均核实自 DiffSynth-Studio 官方源码（2026-08-10）。
"""

import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .hardware import vram_config_for


@dataclass
class ImageParams:
    prompt: str = ""
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    steps: int = 8               # Z-Image 默认 8 步；Qwen-Image 建议 30
    cfg_scale: float = 1.0       # Z-Image 默认 1.0；Qwen-Image 建议 4.0
    seed: int = -1
    output_dir: str = "outputs"
    output_prefix: str = "kevrai"


# 图片引擎支持的模型目录约定（与 facts.BUNDLES 的 dest 布局对应）
IMAGE_ENGINES = {
    "z_image": {
        "marker_dirs": ("transformer", "text_encoder", "vae"),
        "default_steps": 8,
        "default_cfg": 1.0,
    },
    "qwen_image": {
        "marker_dirs": ("transformer", "text_encoder", "vae"),
        "default_steps": 30,
        "default_cfg": 4.0,
    },
}


class ImageEngine:
    """管理图片管线加载与生成。与视频引擎互斥占用显存（生成前会互相卸载）。"""

    def __init__(self):
        self.pipe = None
        self.kind = None            # "z_image" / "qwen_image"
        self.loaded_dir = None

    @property
    def ready(self) -> bool:
        return self.pipe is not None

    # ─────────────────────────────────────────────
    # 加载
    # ─────────────────────────────────────────────
    def load(self, kind: str, bundle_dir: str, policy: str,
             vram_budget_gb: float = -1,
             progress_cb: Optional[Callable[[str], None]] = None):
        import torch

        def say(s):
            if progress_cb:
                progress_cb(s)

        bdir = Path(bundle_dir)
        eff_policy = policy
        vcfg, limit_mode = vram_config_for(eff_policy, accel_device="cuda")

        def _dt(v):
            return torch.bfloat16 if v == "bfloat16" else v

        mc_kwargs = {k: (_dt(v) if k.endswith("dtype") else v) for k, v in vcfg.items()}

        # 权重定位：transformer / text_encoder / vae 三个目录的分片
        import glob
        weight_paths = []
        for sub in ("transformer", "text_encoder", "vae"):
            shards = sorted(glob.glob(str(bdir / sub / "*.safetensors")))
            if shards:
                weight_paths.append(shards if len(shards) > 1 else shards[0])
        if not weight_paths:
            raise RuntimeError(f"未找到模型权重: {bdir}")

        tokenizer_dir = bdir / "tokenizer"

        say("加载图片模型…")
        self.unload()

        if kind == "z_image":
            from diffsynth.pipelines.z_image import ZImagePipeline, ModelConfig
            model_configs = [ModelConfig(path=p, **mc_kwargs) for p in weight_paths]
            tokenizer_cfg = None
            if tokenizer_dir.exists():
                tokenizer_cfg = ModelConfig(path=str(tokenizer_dir))
            vram_limit = self._calc_vram_limit(limit_mode, vram_budget_gb)
            self.pipe = ZImagePipeline.from_pretrained(
                torch_dtype=torch.bfloat16, device="cuda",
                model_configs=model_configs, tokenizer_config=tokenizer_cfg,
                vram_limit=vram_limit)
        elif kind == "qwen_image":
            from diffsynth.pipelines.qwen_image import QwenImagePipeline, ModelConfig
            model_configs = [ModelConfig(path=p, **mc_kwargs) for p in weight_paths]
            tokenizer_cfg = None
            if tokenizer_dir.exists():
                tokenizer_cfg = ModelConfig(path=str(tokenizer_dir))
            vram_limit = self._calc_vram_limit(limit_mode, vram_budget_gb)
            self.pipe = QwenImagePipeline.from_pretrained(
                torch_dtype=torch.bfloat16, device="cuda",
                model_configs=model_configs, tokenizer_config=tokenizer_cfg,
                vram_limit=vram_limit)
        else:
            raise RuntimeError(f"未知图片引擎类型: {kind}")

        self.kind = kind
        self.loaded_dir = str(bdir)
        say("图片模型加载完成")

    def _calc_vram_limit(self, limit_mode: str, vram_budget_gb: float):
        import torch
        if limit_mode == "zero":
            return 0
        if vram_budget_gb is not None and vram_budget_gb > 0:
            return float(vram_budget_gb)
        try:
            free_bytes = torch.cuda.mem_get_info("cuda")[1]
            return max(free_bytes / (1024 ** 3) - 2, 0)
        except Exception:
            return None

    # ─────────────────────────────────────────────
    # 生成
    # ─────────────────────────────────────────────
    def generate(self, params: ImageParams,
                 progress_cb: Optional[Callable[[int, int], None]] = None):
        """返回 {image_path, elapsed_s, seed}。"""
        if not self.ready:
            raise RuntimeError("图片模型尚未加载")

        seed = params.seed if params.seed is not None and params.seed >= 0 \
            else random.randint(0, 2 ** 31 - 1)

        # 进度包装（进度条钩子与视频引擎一致）
        class _ProgressIter:
            def __init__(self, it):
                self._it = iter(it)
                self._i = 0

            def __iter__(self):
                return self

            def __next__(self):
                v = next(self._it)
                self._i += 1
                if progress_cb:
                    progress_cb(self._i, params.steps)
                return v

        kwargs = dict(
            prompt=params.prompt,
            negative_prompt=params.negative_prompt or "",
            height=params.height, width=params.width,
            num_inference_steps=params.steps,
            cfg_scale=params.cfg_scale,
            seed=seed,
            progress_bar_cmd=_ProgressIter,
        )

        t0 = time.time()
        image = self.pipe(**kwargs)

        out_dir = Path(params.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        prefix = (params.output_prefix or "kevrai").strip() or "kevrai"
        image_path = out_dir / f"{prefix}_img_{ts}_{seed}.png"
        image.save(str(image_path))

        return {
            "image_path": str(image_path),
            "elapsed_s": round(time.time() - t0, 1),
            "seed": seed,
        }

    # ─────────────────────────────────────────────
    def unload(self):
        if self.pipe is not None:
            import gc
            del self.pipe
            self.pipe = None
            self.kind = None
            self.loaded_dir = None
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass


_IMAGE_ENGINE = ImageEngine()


def get_image_engine() -> ImageEngine:
    return _IMAGE_ENGINE
