"""LTX-2.5 video generation runtime.

A self-contained task manager for Lightricks LTX-2.5 text-to-video /
image-to-video generation on top of ``diffusers``. It is deliberately
defensive:

* Heavy deps (``torch`` / ``diffusers`` / ``transformers`` / ``imageio``)
  are imported lazily, so the module imports cleanly on a machine without a
  GPU stack (the test suite and the catalog UI do not need torch).
* All user-supplied parameters are validated up-front with explicit bounds;
  invalid input raises :class:`LtxParamError` before any heavy work starts.
* A single-flight task queue (one generation at a time, like the model
  converter) with progress callbacks, cancellation, and status snapshots.
* Output is written to ``<data_root>/outputs/ltx/`` as an MP4 (or a GIF
  fallback when no MP4 writer is available).

The actual pipeline class name varies across diffusers releases, so the
loader tries a list of known class names and reports a clear
:class:`LtxEngineMissing` if none is present.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LtxError(Exception):
    """Base error for the LTX runtime."""


class LtxParamError(LtxError):
    """Invalid generation parameters."""


class LtxEngineMissing(LtxError):
    """torch / diffusers (or the LTX pipeline class) is not installed."""


class LtxBusyError(LtxError):
    """Another generation task is already running."""


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------

# Hardware-tuned presets. Each preset clamps resolution / frames / steps to
# something the target VRAM class can actually handle.
# v2.4.1 注：LTX-2.5 官方最低显存要求为 16GB（22B 完整权重）。16GB 以下的
# 档位只能依赖蒸馏/低比特量化权重，属于实验性配置，可能显存不足——note 字段
# 会在 UI 中原样展示，不得夸大为官方支持。
PRESETS: dict[str, dict[str, Any]] = {
    "ultra": {
        "label": "极致质量 (24GB+)",
        "width": 1280, "height": 720, "num_frames": 161,
        "num_inference_steps": 40, "guidance_scale": 3.5, "vram_gb": 24,
        "note": "推荐 24GB 及以上显存",
    },
    "quality": {
        "label": "高质量 (16GB)",
        "width": 1024, "height": 576, "num_frames": 121,
        "num_inference_steps": 30, "guidance_scale": 3.0, "vram_gb": 16,
        "note": "16GB 为官方最低显存要求",
    },
    "balanced": {
        "label": "平衡 (12GB·实验)",
        "width": 768, "height": 432, "num_frames": 97,
        "num_inference_steps": 25, "guidance_scale": 3.0, "vram_gb": 12,
        "note": "低于官方最低 16GB：需蒸馏量化权重，可能显存不足",
    },
    "speed": {
        "label": "速度优先 (8GB·实验)",
        "width": 512, "height": 320, "num_frames": 65,
        "num_inference_steps": 20, "guidance_scale": 2.5, "vram_gb": 8,
        "note": "低于官方最低 16GB：需蒸馏量化权重，可能显存不足",
    },
    "draft": {
        "label": "草稿 (4GB·实验)",
        "width": 384, "height": 256, "num_frames": 33,
        "num_inference_steps": 12, "guidance_scale": 2.0, "vram_gb": 4,
        "note": "低于官方最低 16GB：需蒸馏量化权重，可能显存不足",
    },
}

# LTX requires (frames - 1) divisible by 8 (temporal VAE compression).
def _normalize_frames(n: Any) -> int:
    n = _as_strict_int(n, "num_frames")
    if n < 9:
        raise LtxParamError("num_frames must be >= 9")
    if n > 257:
        raise LtxParamError("num_frames must be <= 257")
    # Round to 8k+1
    return ((n - 1) // 8) * 8 + 1


def _validate_dim(name: str, v: Any, *, lo: int, hi: int, mod: int = 32) -> int:
    v = _as_strict_int(v, name)
    if v < lo or v > hi:
        raise LtxParamError(f"{name} must be between {lo} and {hi}, got {v}")
    if v % mod != 0:
        # auto-round to nearest multiple of mod rather than rejecting
        v = max(lo, min(hi, round(v / mod) * mod))
    return int(v)


def _as_strict_int(v: Any, name: str) -> int:
    """Reject bools and non-integral floats; accept ints and integer-valued floats."""
    if isinstance(v, bool):
        raise LtxParamError(f"{name} must be an integer, got bool")
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if not v.is_integer():
            raise LtxParamError(f"{name} must be an integer, got {v}")
        return int(v)
    try:
        return int(v)
    except (TypeError, ValueError):
        raise LtxParamError(f"{name} must be an integer, got {v!r}")


@dataclass
class LtxParams:
    mode: str = "t2v"                  # t2v | i2v
    prompt: str = ""
    negative_prompt: str = ""  # 默认无负面提示词（用户偏好：不内置）
    model_id: str = "Lightricks/LTX-2.5"
    preset: str = "balanced"
    width: int = 768
    height: int = 432
    num_frames: int = 97
    num_inference_steps: int = 25
    guidance_scale: float = 3.0
    seed: int = -1                     # -1 = random
    image_path: str = ""               # required for i2v
    strength: float = 0.85             # i2v only
    fps: int = 24
    output_format: str = "mp4"         # mp4 | gif
    enable_vae_slicing: bool = True
    enable_model_cpu_offload: bool = False

    def validate(self) -> None:
        if self.mode not in ("t2v", "i2v"):
            raise LtxParamError(f"mode must be t2v or i2v, got {self.mode!r}")
        if not self.prompt or not self.prompt.strip():
            raise LtxParamError("prompt must not be empty")
        if len(self.prompt) > 2000:
            raise LtxParamError("prompt too long (max 2000 chars)")
        if len(self.negative_prompt) > 2000:
            raise LtxParamError("negative_prompt too long (max 2000 chars)")
        if self.preset not in PRESETS:
            raise LtxParamError(f"unknown preset: {self.preset!r}")
        self.width = _validate_dim("width", self.width, lo=256, hi=1920)
        self.height = _validate_dim("height", self.height, lo=256, hi=1080)
        self.num_frames = _normalize_frames(self.num_frames)
        self.num_inference_steps = _as_strict_int(self.num_inference_steps, "num_inference_steps")
        if not (1 <= self.num_inference_steps <= 100):
            raise LtxParamError("num_inference_steps must be 1..100")
        if not (0.1 <= float(self.guidance_scale) <= 20.0):
            raise LtxParamError("guidance_scale must be 0.1..20")
        self.seed = _as_strict_int(self.seed, "seed")
        if not (-1 <= self.seed <= 2**31 - 1):
            raise LtxParamError("seed out of range")
        self.fps = _as_strict_int(self.fps, "fps")
        if not (1 <= self.fps <= 60):
            raise LtxParamError("fps must be 1..60")
        if self.output_format not in ("mp4", "gif"):
            raise LtxParamError("output_format must be mp4 or gif")
        if not (0.1 <= float(self.strength) <= 1.0):
            raise LtxParamError("strength must be 0.1..1.0")
        if self.mode == "i2v":
            if not self.image_path:
                raise LtxParamError("image_path is required for i2v mode")
            if not Path(self.image_path).expanduser().is_file():
                raise LtxParamError(f"image not found: {self.image_path}")


# ---------------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------------

class TaskState(str, Enum):
    QUEUED = "queued"
    LOADING = "loading"
    RUNNING = "running"
    SAVING = "saving"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class LtxTask:
    id: str
    params: LtxParams
    state: TaskState = TaskState.QUEUED
    progress: float = 0.0          # 0..1
    step: int = 0
    total_steps: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    output_path: str = ""
    error: str = ""
    elapsed_s: float = 0.0
    seed_used: int = -1
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state.value,
            "progress": round(self.progress, 4),
            "step": self.step,
            "total_steps": self.total_steps,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_s": round(self.elapsed_s, 2),
            "output_path": self.output_path,
            "error": self.error,
            "seed_used": self.seed_used,
            "mode": self.params.mode,
            "prompt": self.params.prompt,
            "preset": self.params.preset,
            "width": self.params.width,
            "height": self.params.height,
            "num_frames": self.params.num_frames,
            "fps": self.params.fps,
        }

    def request_cancel(self) -> None:
        self._cancel.set()


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class LtxManager:
    """Single-flight LTX-2.5 generation manager."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._tasks: dict[str, LtxTask] = {}
        self._active: str | None = None
        self._thread: threading.Thread | None = None

    # --- queries ---

    def get(self, task_id: str) -> LtxTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        return [t.snapshot() for t in sorted(
            self._tasks.values(), key=lambda x: x.created_at, reverse=True
        )]

    def active(self) -> dict[str, Any] | None:
        t = self._tasks.get(self._active) if self._active else None
        return t.snapshot() if t else None

    # --- mutations ---

    def start(self, params: LtxParams) -> LtxTask:
        params.validate()
        with self._lock:
            if self._active and self._tasks.get(self._active) and \
                    self._tasks[self._active].state in (TaskState.QUEUED, TaskState.LOADING, TaskState.RUNNING):
                raise LtxBusyError("another LTX generation is already running")
            tid = uuid.uuid4().hex[:12]
            task = LtxTask(id=tid, params=params, total_steps=params.num_inference_steps)
            self._tasks[tid] = task
            self._active = tid
            self._thread = threading.Thread(
                target=self._run_safe, args=(task,), daemon=True, name=f"ltx-{tid}"
            )
            self._thread.start()
            return task

    def cancel(self, task_id: str) -> bool:
        t = self._tasks.get(task_id)
        if not t:
            return False
        if t.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED):
            return False
        t.request_cancel()
        return True

    # --- worker ---

    def _run_safe(self, task: LtxTask) -> None:
        try:
            self._run(task)
        except Exception as e:  # noqa: BLE001
            task.state = TaskState.FAILED
            task.error = f"{type(e).__name__}: {e}"
            task.finished_at = time.time()
            task.elapsed_s = max(0.0, task.finished_at - task.started_at)
        finally:
            with self._lock:
                if self._active == task.id:
                    self._active = None

    def _run(self, task: LtxTask) -> None:
        p = task.params
        task.started_at = time.time()
        task.state = TaskState.LOADING

        # Seed resolution
        import random
        if p.seed < 0:
            task.seed_used = random.randint(0, 2**31 - 1)
        else:
            task.seed_used = int(p.seed)

        pipe = _load_pipeline(p.model_id, p.mode, task)
        if task._cancel.is_set():
            task.state = TaskState.CANCELLED
            task.finished_at = time.time()
            return

        task.state = TaskState.RUNNING
        frames = _generate(pipe, p, task)
        if task._cancel.is_set():
            task.state = TaskState.CANCELLED
            task.finished_at = time.time()
            return

        task.state = TaskState.SAVING
        out = self._write_output(task, frames)
        task.output_path = str(out)
        task.progress = 1.0
        task.state = TaskState.DONE
        task.finished_at = time.time()
        task.elapsed_s = max(0.0, task.finished_at - task.started_at)

    def _write_output(self, task: LtxTask, frames: Any) -> Path:
        p = task.params
        stem = f"ltx25_{task.id}_{int(task.started_at)}"
        ext = "mp4" if p.output_format == "mp4" else "gif"
        out = self.output_root / f"{stem}.{ext}"
        _write_video(frames, out, fps=p.fps, fmt=p.output_format)
        return out


# ---------------------------------------------------------------------------
# Heavy-lifting helpers (lazy imports)
# ---------------------------------------------------------------------------

def _load_pipeline(model_id: str, mode: str, task: LtxTask) -> Any:
    """Load the LTX-2.5 diffusers pipeline. Tries known class names."""
    try:
        import torch  # noqa: F401
        from diffusers import DiffusionPipeline
    except ImportError as e:
        raise LtxEngineMissing(
            "LTX-2.5 需要先安装推理引擎：pip install torch diffusers transformers accelerate imageio imageio-ffmpeg"
        ) from e

    # LTX-2.5 ships under different class names across diffusers versions.
    t2v_classes = ["LTX2VideoPipeline", "LTXVideoPipeline", "LTXPipeline"]
    i2v_classes = ["LTX2ImageToVideoPipeline", "LTXImageToVideoPipeline"]
    names = i2v_classes if mode == "i2v" else t2v_classes

    last_err: Exception | None = None
    pipe = None
    for cls_name in names:
        try:
            import diffusers
            cls = getattr(diffusers, cls_name, None)
            if cls is None:
                continue
            pipe = cls.from_pretrained(model_id, torch_dtype=_autocast_dtype())
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    if pipe is None:
        # Fall back to DiffusionPipeline auto-detection.
        try:
            from diffusers import DiffusionPipeline
            pipe = DiffusionPipeline.from_pretrained(model_id, torch_dtype=_autocast_dtype())
        except Exception as e:  # noqa: BLE001
            raise LtxEngineMissing(
                f"无法加载 LTX-2.5 管线（尝试了 {names}）：{last_err or e}"
            ) from e

    _optimize_pipe(pipe, task.params)
    return pipe


def _autocast_dtype() -> Any:
    import torch
    if torch.cuda.is_available():
        return torch.float16
    return torch.float32


def _optimize_pipe(pipe: Any, p: LtxParams) -> None:
    """Apply memory optimizations based on settings."""
    try:
        if p.enable_model_cpu_offload:
            pipe.enable_model_cpu_offload()
        elif p.enable_vae_slicing and hasattr(pipe, "enable_vae_slicing"):
            pipe.enable_vae_slicing()
        if hasattr(pipe, "enable_attention_slicing"):
            pipe.enable_attention_slicing()
    except Exception:  # noqa: BLE001
        pass


def _generate(pipe: Any, p: LtxParams, task: LtxTask) -> Any:
    """Run the pipeline, updating progress on each step."""
    import torch

    def _callback(pipe_obj, step: int, _ts, kwargs):
        task.step = int(step) + 1
        task.progress = min(0.99, (step + 1) / max(1, p.num_inference_steps))
        if task._cancel.is_set():
            raise _Cancelled()
        return kwargs

    generator = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu")
    generator = generator.manual_seed(task.seed_used)

    call_kwargs: dict[str, Any] = dict(
        prompt=p.prompt,
        negative_prompt=p.negative_prompt or None,
        width=p.width,
        height=p.height,
        num_frames=p.num_frames,
        num_inference_steps=p.num_inference_steps,
        guidance_scale=p.guidance_scale,
        generator=generator,
        callback_on_step_end=_callback,
    )

    if p.mode == "i2v":
        from diffusers.utils import load_image
        image = load_image(p.image_path).resize((p.width, p.height))
        call_kwargs["image"] = image
        call_kwargs["strength"] = p.strength

    result = pipe(**call_kwargs)
    frames = getattr(result, "frames", None)
    if frames is None:
        # Some pipelines return a tuple/list
        frames = result[0] if isinstance(result, (list, tuple)) else result
    # Normalize to a list of numpy frames
    if hasattr(frames, "cpu"):
        frames = frames.cpu().numpy()
    if isinstance(frames, list) and frames and hasattr(frames[0], "cpu"):
        frames = [f.cpu().numpy() for f in frames]
    return frames


class _Cancelled(Exception):
    """Raised inside the step callback to abort generation."""


def _write_video(frames: Any, out: Path, *, fps: int, fmt: str) -> None:
    """Write frames to MP4 (imageio-ffmpeg) or GIF."""
    try:
        import numpy as np
        import imageio
    except ImportError as e:
        raise LtxEngineMissing("写出视频需要 imageio/imageio-ffmpeg") from e

    # Normalize frames to a list of HxWx3 uint8 arrays
    arr = list(frames)
    if arr and hasattr(arr[0], "astype"):
        arr = [f for f in arr]
    if arr and getattr(arr[0], "dtype", None) is not None and arr[0].dtype != "uint8":
        arr = [(np.clip(f, 0, 1) * 255).astype("uint8") if float(f.max()) <= 1.0
               else f.astype("uint8") for f in arr]

    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "gif":
        imageio.mimsave(str(out), arr, fps=fps, loop=0)
    else:
        try:
            writer = imageio.get_writer(str(out), fps=fps, codec="libx264",
                                        quality=8, macro_block_size=1)
            for f in arr:
                writer.append_data(f)
            writer.close()
        except Exception:  # noqa: BLE001
            # ffmpeg unavailable — fall back to mp4v via imageio-ffmpeg or gif
            try:
                writer = imageio.get_writer(str(out), fps=fps)
                for f in arr:
                    writer.append_data(f)
                writer.close()
            except Exception:
                gif_out = out.with_suffix(".gif")
                imageio.mimsave(str(gif_out), arr, fps=fps, loop=0)


# ---------------------------------------------------------------------------
# Capabilities descriptor (served to the UI)
# ---------------------------------------------------------------------------

def capabilities() -> dict[str, Any]:
    """Return a JSON-serializable descriptor of LTX-2.5 capabilities."""
    engine_ready = False
    engine_error = ""
    cuda = False
    try:
        import torch  # noqa: F401
        import diffusers  # noqa: F401
        engine_ready = True
        cuda = bool(torch.cuda.is_available())
    except ImportError as e:
        engine_error = str(e)
    return {
        "model": "Lightricks/LTX-2.5",
        "engine_ready": engine_ready,
        "engine_error": engine_error,
        "cuda_available": cuda,
        "modes": [
            {"id": "t2v", "label": "文生视频 (Text-to-Video)"},
            {"id": "i2v", "label": "图生视频 (Image-to-Video)"},
        ],
        "presets": [
            {"id": k, **{kk: vv for kk, vv in v.items()}} for k, v in PRESETS.items()
        ],
        "limits": {
            "width": [256, 1920], "height": [256, 1080],
            "num_frames": [9, 257], "num_inference_steps": [1, 100],
            "guidance_scale": [0.1, 20.0], "fps": [1, 60],
            "strength": [0.1, 1.0],
        },
        "outputs_dir": "",  # filled by the API layer
        "install_hint": "pip install torch diffusers transformers accelerate imageio imageio-ffmpeg",
    }
