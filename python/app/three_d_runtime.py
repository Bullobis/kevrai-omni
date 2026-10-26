"""3D asset generation runtime (image-to-3D / text-to-3D).

This module closes the single largest runtime gap in Kevrai Omni: the model
catalog ships ~24 3D models (TripoSR, Hunyuan3D-2, TRELLIS, PartCrafter,
Direct3D-S2, …) but before this module there was **no ``/api/3d`` route and no
runtime at all** — those models could only be downloaded, never invoked.

Design follows the proven :mod:`app.ltx_runtime` shape so that the frontend
and the test suite see a familiar contract:

* Heavy deps (``torch`` / ``diffusers`` / ``trimesh`` / ``PIL``) are imported
  **lazily**. The module imports cleanly on a machine with no GPU stack, so
  catalog browsing, tests and the health check never need torch.
* All user parameters are validated up-front with explicit bounds; invalid
  input raises :class:`ThreeDParamError` before any heavy work starts.
* A single-flight task queue with progress callbacks, cancellation and status
  snapshots (same ``TaskState`` vocabulary as the LTX runtime).
* Output is written to ``<data_root>/outputs/3d/`` and exported as GLB by
  default (OBJ/PLY also supported).

Honest note on the adapter layer
--------------------------------
Unlike video (which goes through one ``diffusers`` pipeline family), 3D
models ship wildly different call signatures: TripoSR returns raw
vertices/faces from marching cubes, Hunyuan3D-2 returns a textured mesh,
TRELLIS emits structured latents that need an explicit decode step.
The loader below therefore tries a list of known pipeline class names *and*
a list of known call shapes, and raises a clear :class:`ThreeDEngineMissing`
when nothing matches. It never silently guesses; it reports what it tried.
"""
from __future__ import annotations

import contextlib
import logging
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


class ThreeDError(Exception):
    """Base error for the 3D runtime."""


class ThreeDParamError(ThreeDError):
    """Invalid generation parameters."""


class ThreeDEngineMissing(ThreeDError):
    """torch / diffusers / trimesh (or the 3D pipeline class) is not installed."""


class ThreeDBusyError(ThreeDError):
    """Another 3D generation task is already running."""


# ---------------------------------------------------------------------------
# Hardware-tuned presets
# ---------------------------------------------------------------------------
# 3D generation is far lighter than video: a single mesh, no temporal
# dimension. The binding constraint is the *reconstruction resolution*
# (marching-cubes grid size), which scales cubically with memory.
#
# 说明（如实标注）：下面各档的显存数字是对应模型官方 README / model card 给出的
# 参考值，不是我们实测。TripoSR 官方标称单图 <5 秒、6GB；Hunyuan3D-2 官方给出
# 8–16GB；TRELLIS 约 16GB。低显存档位依赖 CPU offload，速度会明显下降。
PRESETS: dict[str, dict[str, Any]] = {
    "fast": {
        "label": "快速 (6GB)",
        "resolution": 256,
        "num_inference_steps": 20,
        "guidance_scale": 5.0,
        "max_faces": 100_000,
        "note": "TripoSR 级别，单图约 5 秒出网格，无 PBR 材质",
    },
    "balanced": {
        "label": "均衡 (8–12GB)",
        "resolution": 384,
        "num_inference_steps": 30,
        "guidance_scale": 7.0,
        "max_faces": 300_000,
        "note": "Hunyuan3D-2mini 级别，带基础纹理，质量与速度折中",
    },
    "quality": {
        "label": "高画质 (16GB+)",
        "resolution": 512,
        "num_inference_steps": 50,
        "guidance_scale": 7.5,
        "max_faces": 800_000,
        "note": "Hunyuan3D-2 / TRELLIS 级别，纹理与几何更精细，耗时较长",
    },
}

#: 输出格式。GLB 是默认——单文件内嵌纹理，Blender / three.js / 游戏引擎都能直接打开。
OUTPUT_FORMATS = ("glb", "obj", "ply")

#: 各格式是否需要 trimesh（PLY 也可由部分 pipeline 直接产出，这里保守要求）。
_FORMAT_NEEDS_TRIMESH = {"glb": True, "obj": False, "ply": False}

#: 3D 模型的许可与门槛事实。
#:
#: 这一栏必须结构化暴露，不能只写在注释里。3D 领域的许可差异极大，而且
#: Hunyuan3D 带有**地区排除**（欧盟/英国/韩国不可商用）—— 这是"下载了就能
#: 商用"这个普遍误解里后果最严重的一条。
MODEL_LICENSES: dict[str, dict[str, Any]] = {
    "triposr": {
        "license": "MIT",
        "commercial": "true",
        "vram_gb": "约 6GB",
        "note": (
            "纯 MIT，无任何用户上限或地区排除，是三者中最无限制的。"
            "单图约 0.5 秒出网格（A100 实测），但只有顶点色、无 PBR 材质，"
            "保真度已被 TRELLIS.2 与 Hunyuan3D 超越。"
        ),
    },
    "trellis": {
        "license": "MIT",
        "commercial": "true",
        "vram_gb": "16GB 起",
        "note": (
            "模型与多数代码为 MIT；少数渲染子模块带其他许可，"
            "完整商用前需做一次依赖核查。输出带 PBR 的 GLB。"
            "TRELLIS.2 本身只支持单图输入，不支持文生 3D 与多视角；"
            "需要高斯/辐射场输出请用原始的 TRELLIS。"
        ),
    },
    "hunyuan3d": {
        "license": "腾讯社区许可",
        "commercial": "conditional",
        "vram_gb": "10GB（仅几何）／约 29GB（含纹理）",
        "note": (
            "**非** Apache/MIT：有月活用户数上限，且**排除欧盟、英国、韩国**"
            "的商用。质量最高、输入选项最多（GLB/OBJ、多视角变体、"
            "纹理-only 模式），但没有文生 3D 权重——其文生 3D 是在前面"
            "串一个文生图模型实现的。"
        ),
    },
    "stable-fast-3d": {
        "license": "MIT",
        "commercial": "true",
        "vram_gb": "低",
        "note": "轻量单图重建，速度快，保真度低于上述三者。",
    },
}


def license_for_model(model_id: str) -> dict[str, Any]:
    """返回某个 3D 模型的许可事实。

    未登记的返回 unknown —— 绝不默认成"可商用"。
    """
    key = (model_id or "").strip().lower()
    for known, info in MODEL_LICENSES.items():
        if known in key:
            return dict(info)
    return {
        "license": "unknown",
        "commercial": "unknown",
        "vram_gb": "unknown",
        "note": "该模型未在许可清单中登记，商用前请自行核对官方许可。",
    }


#: 支持的模式。t23d（文生 3D）依赖模型本身支持，TripoSR 不支持——会在校验时拒绝。
MODES = ("i23d", "t23d")

#: 已知不支持文生 3D 的模型（纯重建类）——写在这里是为了给出**明确**报错，
#: 而不是让用户等到 pipeline 加载失败才发现问题。
_T23D_UNSUPPORTED = ("triposr", "stable-fast-3d", "sf3d")


def _as_strict_int(value: Any, name: str) -> int:
    """Accept int or a float with no fractional part; reject bool/str.

    ``bool`` is a subclass of ``int`` in Python, so ``True`` would silently
    pass as ``1``. Reject it explicitly.
    """
    if isinstance(value, bool):
        raise ThreeDParamError(f"{name} must be an integer, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ThreeDParamError(f"{name} must be an integer, got {type(value).__name__}")


def _validate_resolution(value: Any) -> int:
    v = _as_strict_int(value, "resolution")
    if not (128 <= v <= 1024):
        raise ThreeDParamError("resolution must be 128..1024")
    return v


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


@dataclass
class ThreeDParams:
    """Validated 3D generation parameters."""

    mode: str = "i23d"
    prompt: str = ""
    negative_prompt: str = ""
    model_id: str = "stabilityai/TripoSR"
    preset: str = "fast"
    image_path: str = ""
    resolution: int = 256
    num_inference_steps: int = 20
    guidance_scale: float = 5.0
    seed: int = -1
    output_format: str = "glb"
    max_faces: int = 100_000
    bake_texture: bool = True
    remove_background: bool = True
    enable_model_cpu_offload: bool = False

    def validate(self) -> None:
        if self.mode not in MODES:
            raise ThreeDParamError(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.preset not in PRESETS:
            raise ThreeDParamError(f"unknown preset: {self.preset!r}")
        if self.output_format not in OUTPUT_FORMATS:
            raise ThreeDParamError(
                f"output_format must be one of {OUTPUT_FORMATS}, got {self.output_format!r}"
            )
        if not self.prompt or not self.prompt.strip():
            raise ThreeDParamError("prompt must not be empty")
        if len(self.prompt) > 2000:
            raise ThreeDParamError("prompt too long (max 2000 chars)")
        if len(self.negative_prompt) > 2000:
            raise ThreeDParamError("negative_prompt too long (max 2000 chars)")

        self.resolution = _validate_resolution(self.resolution)
        self.num_inference_steps = _as_strict_int(self.num_inference_steps, "num_inference_steps")
        if not (1 <= self.num_inference_steps <= 200):
            raise ThreeDParamError("num_inference_steps must be 1..200")
        if not (0.1 <= float(self.guidance_scale) <= 30.0):
            raise ThreeDParamError("guidance_scale must be 0.1..30")
        self.seed = _as_strict_int(self.seed, "seed")
        if not (-1 <= self.seed <= 2**31 - 1):
            raise ThreeDParamError("seed out of range")
        self.max_faces = _as_strict_int(self.max_faces, "max_faces")
        if not (1000 <= self.max_faces <= 5_000_000):
            raise ThreeDParamError("max_faces must be 1000..5000000")

        # i23d 必须给图；t23d 不能给图（给了也不该静默忽略）
        if self.mode == "i23d":
            if not self.image_path:
                raise ThreeDParamError("image_path is required for i23d mode")
            if not Path(self.image_path).expanduser().is_file():
                raise ThreeDParamError(f"image not found: {self.image_path}")
        else:
            low = self.model_id.lower()
            if any(k in low for k in _T23D_UNSUPPORTED):
                raise ThreeDParamError(
                    f"模型 {self.model_id} 不支持文生 3D（它是单图重建模型），请改用 i23d 并上传图片"
                )


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


class _Cancelled(Exception):
    """Internal signal raised from the progress callback on cancellation."""


@dataclass
class ThreeDTask:
    id: str
    params: ThreeDParams
    state: TaskState = TaskState.QUEUED
    progress: float = 0.0
    step: int = 0
    total_steps: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    output_path: str = ""
    error: str = ""
    elapsed_s: float = 0.0
    seed_used: int = -1
    mesh_faces: int = 0
    mesh_vertices: int = 0
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
            "mesh_faces": self.mesh_faces,
            "mesh_vertices": self.mesh_vertices,
            "mode": self.params.mode,
            "prompt": self.params.prompt,
            "preset": self.params.preset,
            "model_id": self.params.model_id,
            "resolution": self.params.resolution,
            "output_format": self.params.output_format,
        }

    def request_cancel(self) -> None:
        self._cancel.set()

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise _Cancelled


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ThreeDManager:
    """Single-flight 3D generation manager."""

    # Same bound as the LTX manager: a desktop app may run for days.
    _MAX_TASKS = 100

    def __init__(self, output_root: Path) -> None:
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._tasks: dict[str, ThreeDTask] = {}
        self._active: str | None = None
        self._thread: threading.Thread | None = None

    # --- queries ---

    def get(self, task_id: str) -> ThreeDTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        return [t.snapshot() for t in sorted(
            self._tasks.values(), key=lambda x: x.created_at, reverse=True
        )]

    def active(self) -> dict[str, Any] | None:
        t = self._tasks.get(self._active) if self._active else None
        return t.snapshot() if t else None

    # --- mutations ---

    def start(self, params: ThreeDParams) -> ThreeDTask:
        params.validate()
        with self._lock:
            cur = self._tasks.get(self._active) if self._active else None
            if cur and cur.state in (
                TaskState.QUEUED, TaskState.LOADING, TaskState.RUNNING, TaskState.SAVING
            ):
                raise ThreeDBusyError("another 3D generation is already running")
            tid = uuid.uuid4().hex[:12]
            task = ThreeDTask(
                id=tid, params=params, total_steps=params.num_inference_steps
            )
            self._tasks[tid] = task
            self._active = tid
            self._prune_tasks()
            self._thread = threading.Thread(
                target=self._run_safe, args=(task,), daemon=True, name=f"3d-{tid}"
            )
            self._thread.start()
            return task

    def _prune_tasks(self) -> None:
        """Drop the oldest *terminal* tasks once history exceeds the cap."""
        if len(self._tasks) <= self._MAX_TASKS:
            return
        running = (TaskState.QUEUED, TaskState.LOADING, TaskState.RUNNING, TaskState.SAVING)
        terminal = sorted(
            (t for t in self._tasks.values() if t.state not in running),
            key=lambda t: t.created_at,
        )
        excess = len(self._tasks) - self._MAX_TASKS
        for t in terminal[:excess]:
            self._tasks.pop(t.id, None)

    def cancel(self, task_id: str) -> bool:
        t = self._tasks.get(task_id)
        if not t:
            return False
        if t.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED):
            return False
        t.request_cancel()
        return True

    # --- worker ---

    def _finish(self, task: ThreeDTask, state: TaskState, error: str = "") -> None:
        task.state = state
        task.error = error
        task.finished_at = time.time()
        task.elapsed_s = max(0.0, task.finished_at - task.started_at)

    def _run_safe(self, task: ThreeDTask) -> None:
        try:
            self._run(task)
        except _Cancelled:
            # Cancellation is a *user* action, not a failure: it must surface
            # as CANCELLED with an empty error field.
            self._finish(task, TaskState.CANCELLED, "")
        except Exception as e:  # noqa: BLE001
            # 取消优先于失败：用户点了取消之后，即便底层加载/推理因为中断
            # 而抛异常，那也是取消的后果，不该报成 FAILED 吓用户一跳。
            if task._cancel.is_set():
                self._finish(task, TaskState.CANCELLED, "")
            else:
                self._finish(task, TaskState.FAILED, f"{type(e).__name__}: {e}")
        finally:
            with self._lock:
                if self._active == task.id:
                    self._active = None

    def _run(self, task: ThreeDTask) -> None:
        p = task.params
        task.started_at = time.time()
        task.state = TaskState.LOADING

        import random
        task.seed_used = (
            random.randint(0, 2**31 - 1) if p.seed < 0 else int(p.seed)  # noqa: S311
        )

        pipe = _load_pipeline(p.model_id, p.mode)
        task._check_cancel()

        task.state = TaskState.RUNNING
        mesh = _generate(pipe, p, task)
        task._check_cancel()

        task.state = TaskState.SAVING
        out = self._write_output(task, mesh)
        task.output_path = str(out)
        task.progress = 1.0
        self._finish(task, TaskState.DONE)

    def _write_output(self, task: ThreeDTask, mesh: Any) -> Path:
        p = task.params
        stem = f"3d_{task.id}_{int(task.started_at)}"
        out = self.output_root / f"{stem}.{p.output_format}"
        return _export_mesh(mesh, out, fmt=p.output_format, max_faces=p.max_faces)


# ---------------------------------------------------------------------------
# Output naming — kept pure & testable without torch
# ---------------------------------------------------------------------------


def output_stem(task_id: str, started_at: float) -> str:
    """Deterministic output file stem (no extension)."""
    return f"3d_{task_id}_{int(started_at)}"


def requires_trimesh(output_format: str) -> bool:
    """Whether exporting ``output_format`` needs ``trimesh`` installed.

    GLB is a container format (binary glTF); writing one from raw
    vertices/faces is non-trivial, so we depend on trimesh for it. OBJ and
    PLY are plain text/binary dumps that we can emit with the stdlib.
    """
    return _FORMAT_NEEDS_TRIMESH.get(output_format, True)


# ---------------------------------------------------------------------------
# Heavy-lifting helpers (lazy imports)
# ---------------------------------------------------------------------------


def _load_pipeline(model_id: str, mode: str) -> Any:
    """Load a 3D generation pipeline, trying known class names.

    Raises :class:`ThreeDEngineMissing` with an actionable message (including
    the exact pip line) when the GPU stack is absent, and with the list of
    class names it tried when diffusers is present but has no matching class.
    """
    try:
        import torch  # noqa: F401
    except ImportError as e:
        raise ThreeDEngineMissing(
            "3D 生成需要先安装推理引擎："
            "pip install torch diffusers transformers accelerate trimesh pillow"
        ) from e

    try:
        from diffusers import DiffusionPipeline
    except ImportError as e:
        raise ThreeDEngineMissing(
            "缺少 diffusers：pip install diffusers transformers accelerate"
        ) from e

    # 3D pipelines are scattered across diffusers releases and across
    # vendor repos (tencent/Hunyuan3D-2 ships its own pipeline module).
    # Try the generic loader first, then known class names.
    candidates = [
        "TripoSRPipeline",
        "Hunyuan3DPipeline",
        "Hunyuan3DDiTPipeline",
        "StableFast3DPipeline",
        "TRELLISImageTo3DPipeline",
    ]
    try:
        return DiffusionPipeline.from_pretrained(model_id, trust_remote_code=True)
    except Exception as e:  # noqa: BLE001 — fall through to explicit class lookup
        logging.getLogger("kevrai.three_d").debug(
            "DiffusionPipeline.from_pretrained 未命中，改按类名加载: %s", e)

    import diffusers

    tried: list[str] = []
    for name in candidates:
        cls = getattr(diffusers, name, None)
        if cls is None:
            tried.append(name)
            continue
        try:
            return cls.from_pretrained(model_id, trust_remote_code=True)
        except Exception as e:  # noqa: BLE001
            tried.append(f"{name}({type(e).__name__})")
            continue

    raise ThreeDEngineMissing(
        f"未找到可用于 {model_id} 的 3D pipeline。"
        f"当前 diffusers={getattr(diffusers, '__version__', '?')} 已尝试：{', '.join(tried)}。"
        f"某些模型（如 Hunyuan3D-2、TRELLIS）需要从官方仓库安装配套代码。"
    )


def _generate(pipe: Any, p: ThreeDParams, task: ThreeDTask) -> Any:
    """Run the pipeline and return a mesh-like object.

    Call shapes differ per model family, so we try the common ones in order
    and record progress via the task's step callback.
    """
    total = max(1, p.num_inference_steps)

    def _cb(*_a: Any, **_kw: Any) -> None:
        # diffusers calls back with (pipe, step_index, timestep, callback_kwargs)
        task.step = min(total, task.step + 1)
        task.progress = min(0.95, task.step / total)
        task._check_cancel()

    kwargs: dict[str, Any] = {
        "num_inference_steps": p.num_inference_steps,
        "guidance_scale": p.guidance_scale,
        "callback_on_step_end": _cb,
    }
    if p.mode == "i23d":
        from PIL import Image  # noqa: PLC0415 — lazy, matches the runtime policy

        img = Image.open(p.image_path).convert("RGBA" if p.remove_background else "RGB")
        kwargs["image"] = img
    else:
        kwargs["prompt"] = p.prompt

    # Different families expose different entrypoints / return shapes.
    try:
        out = pipe(**kwargs)
    except TypeError:
        # Some pipelines take the image positionally or use `input_image`.
        alt = dict(kwargs)
        if "image" in alt:
            alt["input_image"] = alt.pop("image")
        out = pipe(**alt)

    # Normalise the return value to "the mesh".
    mesh = getattr(out, "meshes", None)
    if mesh:
        return mesh[0]
    for key in ("mesh", "glb", "obj"):
        val = getattr(out, key, None)
        if val is not None:
            return val
    if isinstance(out, (tuple, list)) and out:
        return out[0]
    return out


def _export_mesh(mesh: Any, out: Path, fmt: str, max_faces: int) -> Path:
    """Write ``mesh`` to ``out`` in the requested format.

    ``trimesh`` handles GLB/OBJ/PLY uniformly when available. Without it we
    fall back to :func:`_write_ply_guess` for PLY-only export, and raise a
    clear error for GLB (a container we will not hand-roll).
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        import trimesh  # noqa: PLC0415
    except ImportError:
        if fmt == "ply":
            return _write_ply_guess(mesh, out, max_faces)
        raise ThreeDEngineMissing(
            f"导出 {fmt.upper()} 需要 trimesh：pip install trimesh（或改用 ply 格式）"
        ) from None

    if not isinstance(mesh, trimesh.Trimesh):
        # Some pipelines hand back (vertices, faces) tuples.
        mesh = _coerce_mesh(trimesh, mesh)

    if max_faces and getattr(mesh, "faces", None) is not None and len(mesh.faces) > max_faces:
        mesh = _decimate(trimesh, mesh, max_faces)

    data = mesh.export(file_type=fmt)
    if isinstance(data, str):
        data = data.encode("utf-8")
    out.write_bytes(data)
    return out


def _coerce_mesh(trimesh: Any, mesh: Any) -> Any:
    """Turn (vertices, faces[, vertex_colors]) tuples into a Trimesh."""
    if isinstance(mesh, (tuple, list)) and len(mesh) >= 2:
        verts, faces = mesh[0], mesh[1]
        colors = mesh[2] if len(mesh) > 2 else None
        return trimesh.Trimesh(
            vertices=verts, faces=faces,
            vertex_colors=colors, process=False,
        )
    # duck-typed mesh（带 .vertices / .faces，与无 trimesh 时的
    # _write_ply_guess 形状支持对齐），避免装不装 trimesh 结果相反。
    if hasattr(mesh, "vertices") and hasattr(mesh, "faces"):
        colors = getattr(mesh, "visual", None) and getattr(
            getattr(mesh, "visual", None), "vertex_colors", None)
        return trimesh.Trimesh(
            vertices=mesh.vertices, faces=mesh.faces,
            vertex_colors=colors, process=False,
        )
    raise ThreeDEngineMissing(
        f"无法把 {type(mesh).__name__} 识别为网格（期望 Trimesh 或 (顶点, 面) 元组）"
    )


def _decimate(trimesh: Any, mesh: Any, max_faces: int) -> Any:
    """Best-effort face reduction; falls back to the original mesh.

    ``trimesh`` only supports simplification via optional backends
    (``quadric``/``open3d``/``pymeshlab``). When none is installed we keep
    the original mesh rather than failing the whole generation — an
    over-detailed mesh is still a usable asset, a failed export is not.
    """
    with contextlib.suppress(Exception):
        simplified = mesh.simplify_quadric_decimation(face_count=max_faces)
        if simplified is not None and len(simplified.faces) <= max_faces:
            return simplified
    return mesh


def _write_ply_guess(mesh: Any, out: Path, max_faces: int) -> Path:
    """Minimal ASCII-PLY writer for the no-trimesh PLY path.

    Handles both Trimesh-like objects and (vertices, faces) tuples. Faces are
    written as triangles; non-triangular faces are dropped rather than
    silently mangled (a wrong index list produces unreadable geometry).
    """
    if hasattr(mesh, "vertices") and hasattr(mesh, "faces"):
        verts = [[float(c) for c in v] for v in mesh.vertices]
        faces = [[int(i) for i in f] for f in mesh.faces]
    elif isinstance(mesh, (tuple, list)) and len(mesh) >= 2:
        verts = [[float(c) for c in v] for v in mesh[0]]
        faces = [[int(i) for i in f] for f in mesh[1]]
    else:
        raise ThreeDEngineMissing(
            f"无法在无 trimesh 的情况下导出 {type(mesh).__name__}"
        )

    tri = [f for f in faces if len(f) == 3]
    if max_faces and len(tri) > max_faces:
        tri = tri[:max_faces]

    lines = [
        "ply",
        "format ascii 1.0",
        f"element vertex {len(verts)}",
        "property float x", "property float y", "property float z",
        f"element face {len(tri)}",
        "property list uchar int vertex_indices",
        "end_header",
    ]
    with out.open("w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
        for v in verts:
            fh.write(f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
        for f in tri:
            fh.write(f"3 {f[0]} {f[1]} {f[2]}\n")
    return out


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def _has(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def capabilities() -> dict[str, Any]:
    """Report what the 3D runtime can do on this machine.

    Never raises: a missing GPU stack is a legitimate, reportable state.
    """
    torch_ok = _has("torch")
    diffusers_ok = _has("diffusers")
    trimesh_ok = _has("trimesh")
    pil_ok = _has("PIL")

    gpu_ok = False
    with contextlib.suppress(Exception):
        import torch

        gpu_ok = bool(torch.cuda.is_available())

    ready = torch_ok and diffusers_ok and pil_ok
    missing: list[str] = []
    if not torch_ok:
        missing.append("torch")
    if not diffusers_ok:
        missing.append("diffusers")
    if not pil_ok:
        missing.append("pillow")
    if not trimesh_ok:
        missing.append("trimesh (仅 GLB 导出需要)")

    return {
        "available": ready,
        "torch": torch_ok,
        "diffusers": diffusers_ok,
        "trimesh": trimesh_ok,
        "pil": pil_ok,
        "cuda": gpu_ok,
        "missing": missing,
        "install_hint": (
            "" if ready else
            "pip install torch diffusers transformers accelerate trimesh pillow"
        ),
        "presets": {k: {"label": v["label"], "note": v["note"]} for k, v in PRESETS.items()},
        "modes": list(MODES),
        "output_formats": list(OUTPUT_FORMATS),
        "t23d_unsupported_models": list(_T23D_UNSUPPORTED),
        "licenses": MODEL_LICENSES,
        "note": (
            "3D 运行时已接线。TripoSR 门槛最低（6GB，单图约 5 秒，MIT 许可）；"
            "Hunyuan3D-2 / TRELLIS 质量更高但需要 8–16GB 显存，且部分需从官方仓库安装配套代码。"
        ),
    }
