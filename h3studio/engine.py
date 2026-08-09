# -*- coding: utf-8 -*-
"""
engine.py — DiffSynth-Studio MiniMax H3 推理引擎封装
=====================================================
仅使用经过核实的官方 API（DiffSynth-Studio 官方文档与源码，2026-08-07 验证）：

  from diffsynth.pipelines.minimax_h3_audio_video import MiniMaxH3Pipeline, ModelConfig
  from diffsynth.utils.data.audio_video import write_video_audio, read_video_audio
  from diffsynth.utils.data.audio import read_audio

  MiniMaxH3Pipeline.from_pretrained(torch_dtype, device, model_configs, processor_config, vram_limit)
  pipe(prompt=..., height=..., width=..., num_frames=..., num_inference_steps=..., seed=...,
       keyframes=[...], keyframe_indices=[...], references=[...], retake_video=..., ...)
  pipe.load_lora(pipe.dit, lora_config=path, alpha=...)

本模块不做任何"模拟生成"：引擎/模型缺失时明确报错，由 UI 引导用户安装。
"""

import gc
import glob
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from .hardware import vram_config_for


# ─────────────────────────────────────────────────────────────
# 生成参数
# ─────────────────────────────────────────────────────────────
@dataclass
class GenerationParams:
    prompt: str = ""
    negative_prompt: str = ""
    duration_s: int = 5              # 4~15
    width: int = 1344
    height: int = 768
    steps: int = 50
    seed: int = -1                   # -1 = 随机

    # 高级参数（H3 官方默认值）
    cfg_scale: float = 1.0
    flow_shift: float = 12.0
    audio_flow_shift: float = 3.0
    tiled: bool = True
    tile_size: int = 256
    tile_overlap: int = 64
    rand_device: str = "cpu"

    mode: str = "t2va"               # t2va / first / last / fl / ref2va / audio_driven / retake
    keyframe_paths: List[str] = field(default_factory=list)      # FL2VA 关键帧（≤2）
    references: List[dict] = field(default_factory=list)
    # references 元素: {"kind": "image"|"video"|"audio"|"video_audio", "path": str}

    retake_video_path: str = ""      # 视频编辑（Retake）源视频
    retake_start_s: float = 0.0      # 重生成区间（秒）
    retake_end_s: float = 0.0        # 0 = 整段重生成
    retake_keep_audio: bool = True   # 保留原音轨

    lora_path: str = ""
    lora_alpha: float = 1.0

    output_dir: str = "outputs"
    output_prefix: str = "h3"
    save_metadata: bool = True


# ─────────────────────────────────────────────────────────────
# 帧数对齐（官方规则：向上取整到 17n+5）
# ─────────────────────────────────────────────────────────────
def align_num_frames(duration_s: float) -> int:
    target = max(int(round(duration_s * 24)), 1)
    n = target
    while n % 17 != 5:
        n += 1
    return n


def detect_backend_quick() -> tuple:
    """
    快速识别加速后端。返回 (backend, torch_device)。
    backend ∈ hardware.BACKEND_*；无加速设备时返回 (BACKEND_CPU, "cpu")。
    """
    from . import hardware as hw
    try:
        import torch
    except ImportError:
        return hw.BACKEND_CPU, "cpu"
    try:
        if torch.cuda.is_available():
            is_rocm = bool(getattr(torch.version, "hip", None))
            return (hw.BACKEND_ROCM if is_rocm else hw.BACKEND_NVIDIA), "cuda"
    except Exception:
        pass
    try:
        import importlib.util
        if importlib.util.find_spec("torch_npu") is not None and torch.npu.is_available():
            return hw.BACKEND_NPU, "npu"
    except Exception:
        pass
    try:
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            return hw.BACKEND_XPU, "xpu"
    except Exception:
        pass
    try:
        import torch_directml  # noqa: F401
        if torch_directml.is_available():
            return hw.BACKEND_DIRECTML, "privateuseone"
    except Exception:
        pass
    return hw.BACKEND_CPU, "cpu"


def check_engine_ready() -> tuple:
    """返回 (可用?, 说明)。兼容 NVIDIA / AMD ROCm / 昇腾 NPU / Intel XPU / DirectML。"""
    from . import hardware as hw
    try:
        import torch  # noqa: F401
    except ImportError:
        return False, "未安装 PyTorch（torch）。请先运行一键打包脚本或按硬件安装对应版本（CUDA/ROCm/torch-npu）。"
    backend, device = detect_backend_quick()
    if backend == hw.BACKEND_CPU:
        return False, ("未检测到可用的加速设备（NVIDIA CUDA / AMD ROCm / 昇腾 NPU / Intel XPU）。"
                       "H3 本地推理需要加速硬件，纯 CPU 不推荐。")
    try:
        import diffsynth  # noqa: F401
    except ImportError:
        return False, "未安装 DiffSynth-Studio 引擎：pip install diffsynth>=2.1.0"
    try:
        import av  # noqa: F401
    except ImportError:
        return False, "缺少音视频依赖 PyAV：pip install av"
    try:
        import torchaudio  # noqa: F401
    except ImportError:
        return False, "缺少音频依赖 torchaudio：pip install torchaudio"
    return True, f"引擎就绪（{hw.BACKEND_LABELS.get(backend, backend)}）"


# ─────────────────────────────────────────────────────────────
# 引擎
# ─────────────────────────────────────────────────────────────
class H3Engine:
    """
    管理 MiniMaxH3Pipeline 的加载与生成。
    load() 成功后才能 generate()。切换分区（FL2VA↔Ref2VA）需重新 load()。
    """

    def __init__(self):
        self.pipe = None
        self.loaded_partition = None     # "FL2VA" / "Ref2VA"
        self.loaded_bundle_dir = None
        self.loaded_policy = None
        self._lora_applied = None

    # ── 是否已加载 ──
    @property
    def ready(self) -> bool:
        return self.pipe is not None

    # ─────────────────────────────────────────────
    # 加载
    # ─────────────────────────────────────────────
    def load(self, bundle_dir: str, bundle_id: str, partition: str,
             policy: str, progress_cb: Optional[Callable[[str], None]] = None,
             vram_budget_gb: float = -1, offload_mode: str = "auto",
             torch_threads: int = -1):
        """
        bundle_dir: 模型 bundle 的本地目录
        bundle_id : facts.BUNDLES 的 id（决定文件布局）
        partition : "FL2VA" / "Ref2VA"
        policy    : hardware.choose_policy 的结果
        vram_budget_gb: -1=自动（可用显存-2GB）；>0=手动预算
        offload_mode: auto=按 policy / cpu=强制内存卸载 / disk=强制磁盘流式
        torch_threads: CPU 线程数，-1=默认
        """
        ok, msg = check_engine_ready()
        if not ok:
            raise RuntimeError(msg)

        import torch
        from diffsynth.pipelines.minimax_h3_audio_video import MiniMaxH3Pipeline, ModelConfig

        if torch_threads and torch_threads > 0:
            try:
                torch.set_num_threads(int(torch_threads))
            except Exception:
                pass

        def say(s):
            if progress_cb:
                progress_cb(s)

        bundle_dir = Path(bundle_dir)
        # 后端设备（cuda=NVIDIA/ROCm，npu=昇腾，xpu=Intel，privateuseone=DirectML）
        backend, accel_device = detect_backend_quick()
        if accel_device == "cpu":
            raise RuntimeError("未检测到加速设备，无法加载模型")

        # 卸载策略覆盖
        eff_policy = policy
        if offload_mode == "cpu":
            eff_policy = "balanced"   # CPU 卸载配置
        elif offload_mode == "disk":
            eff_policy = "low"        # 磁盘流式配置
        vcfg, limit_mode = vram_config_for(eff_policy, accel_device=accel_device)

        # 字符串 dtype → torch dtype（"disk" 保持字符串，官方约定）
        def _dt(v):
            return torch.bfloat16 if v == "bfloat16" else v

        mc_kwargs = {k: (_dt(v) if k.endswith("dtype") else v) for k, v in vcfg.items()}

        say("定位模型文件…")
        weight_paths, processor_dir = self._locate_files(bundle_dir, bundle_id, partition)

        say("构建模型配置…")
        model_configs = [ModelConfig(path=p, **mc_kwargs) for p in weight_paths]
        processor_config = ModelConfig(path=str(processor_dir))

        # vram_limit：auto = 可用显存-2GB（官方示例做法）；zero = 0（极限磁盘直载）
        # 用户手动预算优先；无法探测设备内存时不设限（交由框架管理）
        if limit_mode == "zero":
            vram_limit = 0
        elif vram_budget_gb is not None and vram_budget_gb > 0:
            vram_limit = float(vram_budget_gb)
        else:
            vram_limit = None
            try:
                if accel_device.startswith("cuda"):
                    free_bytes = torch.cuda.mem_get_info("cuda")[1]
                    vram_limit = max(free_bytes / (1024 ** 3) - 2, 0)
                elif accel_device.startswith("npu"):
                    free_bytes = torch.npu.mem_get_info("npu")[1]
                    vram_limit = max(free_bytes / (1024 ** 3) - 2, 0)
            except Exception:
                vram_limit = None

        say("加载模型（首次加载需要较长时间，显存自动管理已启用）…")
        # 同一 pipeline 进程内切换分区：先释放旧模型
        self.unload()

        self.pipe = MiniMaxH3Pipeline.from_pretrained(
            torch_dtype=torch.bfloat16,
            device=accel_device,
            model_configs=model_configs,
            processor_config=processor_config,
            vram_limit=vram_limit,
        )
        self.loaded_partition = partition
        self.loaded_bundle_dir = str(bundle_dir)
        self.loaded_policy = policy
        self._lora_applied = None
        say("模型加载完成")

    def _locate_files(self, bundle_dir: Path, bundle_id: str, partition: str):
        """按 bundle 布局定位权重文件与 processor 目录。返回 (weight_paths, processor_dir)。"""
        bdir = bundle_dir

        if bundle_id == "nf4_fl2va" or bundle_id == "nf4_full":
            dit_name = ("minimax-h3-fl2va-nf4.safetensors" if partition == "FL2VA"
                        else "minimax-h3-ref2va-nf4.safetensors")
            weights = [
                bdir / dit_name,
                bdir / "minimax-h3-text-encoder-nf4.safetensors",
                bdir / "video_vae_nf4.safetensors",
                bdir / "audio_vae_nf4.safetensors",
            ]
            proc = bdir / ("processor_fl2va" if partition == "FL2VA" else "processor_ref2va")
        elif bundle_id in ("bf16_fl2va", "bf16_ref2va"):
            p = partition
            weights = (
                sorted(glob.glob(str(bdir / p / "transformer" / "model*.safetensors")))
                + sorted(glob.glob(str(bdir / p / "text_encoder" / "model*.safetensors")))
                + [str(bdir / p / "video_vae" / "source" / "model.safetensors"),
                   str(bdir / p / "audio_vae" / "model.safetensors")]
            )
            proc = bdir / ("processor_fl2va" if partition == "FL2VA" else "processor_ref2va")
        elif bundle_id.startswith("custom_"):
            # DIY 自定义包布局：dit/ text_encoder/ video_vae/ audio_vae/ processor/
            weights = (
                sorted(glob.glob(str(bdir / "dit" / "*.safetensors")))
                + sorted(glob.glob(str(bdir / "text_encoder" / "*.safetensors")))
                + sorted(glob.glob(str(bdir / "video_vae" / "*.safetensors")))
                + sorted(glob.glob(str(bdir / "audio_vae" / "*.safetensors")))
            )
            if not weights:
                raise RuntimeError("自定义包目录中未找到任何权重文件")
            proc = bdir / "processor"
        else:
            raise RuntimeError(f"bundle [{bundle_id}] 不是内置引擎可推理的模型（可能是 ComfyUI 专用或 LoRA）")

        missing = [str(w) for w in weights if not Path(str(w)).exists()]
        if missing:
            raise RuntimeError("缺少模型文件（请在模型市场完成下载）:\n" + "\n".join(missing[:6]))
        if not proc.exists():
            raise RuntimeError(f"缺少 processor 目录: {proc}")
        return weights, proc

    # ─────────────────────────────────────────────
    # LoRA（社区微调 / 加速模型）
    # ─────────────────────────────────────────────
    def apply_lora(self, lora_path: str, alpha: float = 1.0):
        """加载 LoRA 到 DiT（官方 load_lora 接口）。重复调用前请先 unload_lora。"""
        if not self.ready:
            raise RuntimeError("模型尚未加载")
        if self._lora_applied == (lora_path, alpha):
            return
        self.pipe.load_lora(self.pipe.dit, lora_config=lora_path, alpha=alpha)
        self._lora_applied = (lora_path, alpha)

    # ─────────────────────────────────────────────
    # 卸载
    # ─────────────────────────────────────────────
    def unload(self):
        if self.pipe is not None:
            del self.pipe
            self.pipe = None
            self.loaded_partition = None
            self._lora_applied = None
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                elif getattr(torch, "npu", None) is not None and torch.npu.is_available():
                    torch.npu.empty_cache()
                elif getattr(torch, "xpu", None) is not None and torch.xpu.is_available():
                    torch.xpu.empty_cache()
            except Exception:
                pass

    # ─────────────────────────────────────────────
    # 生成
    # ─────────────────────────────────────────────
    def generate(self, params: GenerationParams,
                 progress_cb: Optional[Callable[[int, int, str], None]] = None) -> dict:
        """
        progress_cb(step, total, phase)
        返回 {video_path, elapsed_s, num_frames, seed, log}
        """
        if not self.ready:
            raise RuntimeError("模型尚未加载")
        from PIL import Image
        from diffsynth.utils.data.audio_video import write_video_audio, read_video_audio
        from diffsynth.utils.data.audio import read_audio

        t0 = time.time()
        log = []
        p = params
        seed = p.seed if p.seed is not None and p.seed >= 0 else random.randint(0, 2 ** 31 - 1)
        num_frames = align_num_frames(p.duration_s)
        log.append(f"mode={p.mode} {p.width}x{p.height} {num_frames}frames steps={p.steps} seed={seed}")

        # 需要的分区检查
        need_ref = p.mode in ("ref2va", "audio_driven", "retake") or len(p.references) > 0
        if need_ref and self.loaded_partition != "Ref2VA":
            raise RuntimeError("当前任务需要 Ref2VA 分区，请加载包含 Ref2VA 的模型（如 NF4 双分区版）")

        kwargs = dict(
            prompt=p.prompt,
            negative_prompt=p.negative_prompt or " ",
            height=p.height, width=p.width,
            num_frames=num_frames,
            num_inference_steps=p.steps,
            seed=seed,
            cfg_scale=p.cfg_scale,
            flow_shift=p.flow_shift,
            audio_flow_shift=p.audio_flow_shift,
            tiled=p.tiled,
            tile_size=p.tile_size,
            tile_overlap=p.tile_overlap,
            rand_device=p.rand_device,
        )

        audio_sr = self.pipe.audio_vae.sample_rate
        duration_aligned = num_frames / 24.0

        # ── FL2VA 关键帧 ──
        if p.mode == "first" and p.keyframe_paths:
            kwargs["keyframes"] = [Image.open(p.keyframe_paths[0]).convert("RGB")]
            kwargs["keyframe_indices"] = [0]
        elif p.mode == "last" and p.keyframe_paths:
            kwargs["keyframes"] = [Image.open(p.keyframe_paths[0]).convert("RGB")]
            kwargs["keyframe_indices"] = [-1]
        elif p.mode == "fl" and len(p.keyframe_paths) >= 2:
            kwargs["keyframes"] = [Image.open(x).convert("RGB") for x in p.keyframe_paths[:2]]
            kwargs["keyframe_indices"] = [0, -1]

        # ── Ref2VA 参考 ──
        if p.references:
            refs = []
            for r in p.references:
                kind, path = r["kind"], r["path"]
                if kind == "image":
                    refs.append({"type": "image", "image": Image.open(path).convert("RGB")})
                elif kind == "video":
                    frames, _, _ = read_video_audio(
                        path, height=p.height, width=p.width, num_frames=num_frames,
                        fps=24, audio_sample_rate=audio_sr)
                    refs.append({"type": "video", "video": frames})
                elif kind == "audio":
                    wav, sr = read_audio(path, duration=duration_aligned,
                                         resample=True, resample_rate=audio_sr)
                    refs.append({"type": "audio", "audio": wav, "sample_rate": sr})
                elif kind == "video_audio":
                    frames, wav, sr = read_video_audio(
                        path, height=p.height, width=p.width, num_frames=num_frames,
                        fps=24, audio_sample_rate=audio_sr)
                    refs.append({"type": "video_audio", "video": frames,
                                 "audio": wav, "sample_rate": sr})
            kwargs["references"] = refs
            log.append(f"references={len(refs)}")

        # ── 视频编辑 Retake ──
        if p.mode == "retake" and p.retake_video_path:
            frames, wav, sr = read_video_audio(
                p.retake_video_path, height=p.height, width=p.width, num_frames=num_frames,
                fps=24, audio_sample_rate=audio_sr)
            kwargs["retake_video"] = frames
            if 0 < p.retake_end_s <= duration_aligned and p.retake_start_s < p.retake_end_s:
                f_start = max(0, int(p.retake_start_s * 24))
                f_end = int(p.retake_end_s * 24)
                kwargs["frame_regions_to_retake"] = [(f_start, f_end)]
                log.append(f"retake frames [{f_start},{f_end})")
            if p.retake_keep_audio:
                kwargs["retake_audio"] = wav
                kwargs["retake_audio_sample_rate"] = sr
                # 不传 seconds_regions_to_retake → 音轨整体保留

        # ── 进度包装 ──
        total_steps = p.steps

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
                    progress_cb(self._i, total_steps, "denoise")
                return v

        kwargs["progress_bar_cmd"] = _ProgressIter

        if progress_cb:
            progress_cb(0, total_steps, "encode")

        # LoRA
        if p.lora_path:
            self.apply_lora(p.lora_path, p.lora_alpha)

        # ── 执行 ──
        video, audio = self.pipe(**kwargs)

        if progress_cb:
            progress_cb(total_steps, total_steps, "decode")

        # ── 保存 ──
        out_dir = Path(p.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        prefix = (p.output_prefix or "h3").strip() or "h3"
        video_path = out_dir / f"{prefix}_{ts}_{seed}.mp4"
        write_video_audio(video=video, audio=audio, output_path=str(video_path),
                          fps=24, audio_sample_rate=audio_sr)

        # 元数据 sidecar（作品库用）
        if p.save_metadata:
            meta = {
                "prompt": p.prompt, "negative_prompt": p.negative_prompt,
                "mode": p.mode, "width": p.width, "height": p.height,
                "num_frames": num_frames, "steps": p.steps, "seed": seed,
                "cfg_scale": p.cfg_scale, "flow_shift": p.flow_shift,
                "audio_flow_shift": p.audio_flow_shift,
                "lora": p.lora_path or None, "lora_alpha": p.lora_alpha,
                "elapsed_s": round(time.time() - t0, 1),
                "created_at": ts,
            }
            try:
                (out_dir / f"{prefix}_{ts}_{seed}.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        log.append(f"done in {time.time() - t0:.1f}s → {video_path.name}")
        return {
            "video_path": str(video_path),
            "elapsed_s": round(time.time() - t0, 1),
            "num_frames": num_frames,
            "seed": seed,
            "log": "\n".join(log),
        }


# ─────────────────────────────────────────────
# 全局单例（UI 层使用）
# ─────────────────────────────────────────────
_ENGINE = H3Engine()


def get_engine() -> H3Engine:
    return _ENGINE
