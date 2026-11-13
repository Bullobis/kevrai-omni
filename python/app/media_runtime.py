"""Generic Diffusers video / audio adapters.

The catalog contains many models whose upstream label is just ``diffusers``:
Wan 2.2, HunyuanVideo, OpenSora, CogVideoX, Stable Audio, AudioLDM2, etc.
Before this module the app had no endpoint for them, so "downloadable" was
mistaken for "runnable". This is the missing bridge: load a local
``model_index.json`` directory (or a repo id) through the standard
``DiffusionPipeline`` factory, call the pipeline's documented arguments, and
write a real media file.

It is deliberately generic. Different pipelines expose slightly different
keyword names (``num_frames`` vs ``video_length``, ``audio_length_in_s`` vs
``audio_length_s``); the caller-facing API is normalized, and the module
degrades with a clear error instead of pretending every architecture works.
"""
from __future__ import annotations

import logging
import time
import wave
from pathlib import Path
from typing import Any

log = logging.getLogger("kevrai.media")


class MediaError(RuntimeError):
    """Base error for generic media generation."""


class MediaEngineMissing(MediaError):
    """torch / diffusers / imageio are not installed."""


class MediaModelError(MediaError):
    """The pipeline could not be loaded or did not return media."""


class MediaParamError(MediaError):
    """Invalid request values."""


def _imports() -> tuple[Any, Any]:
    try:
        import torch  # type: ignore
        from diffusers import DiffusionPipeline  # type: ignore
        return torch, DiffusionPipeline
    except ImportError as e:  # pragma: no cover - depends on optional wheels
        raise MediaEngineMissing(
            "视频/音频生成需要 torch + diffusers：请先安装推理引擎"
        ) from e


def _load_pipeline(
    source: str | Path, *, allow_any: bool = True,
) -> Any:
    """Load a local diffusers directory or a Hub repo.

    ``allow_any`` is kept for symmetry with the image endpoint, but this
    module is only called for video/audio categories; a wrong architecture
    fails inside diffusers with a readable error.
    """
    torch, diffusion_pipeline = _imports()
    repo = str(source).strip()
    if not repo:
        raise MediaParamError("model 不能为空")
    local = Path(repo).expanduser()
    if local.is_dir() and not (local / "model_index.json").is_file():
        raise MediaParamError(
            f"本地目录 {repo} 没有 model_index.json，不是 diffusers 管线"
        )
    cuda = bool(getattr(getattr(torch, "cuda", None), "is_available", lambda: False)())
    dtype = torch.float16 if cuda else torch.float32
    kwargs: dict[str, Any] = {"torch_dtype": dtype}
    if local.is_dir():
        kwargs["local_files_only"] = True
    try:
        pipe = diffusion_pipeline.from_pretrained(repo, **kwargs)
    except TypeError:
        # Older pipelines reject torch_dtype / local_files_only; retry with
        # only the arguments every version understands.
        kwargs.pop("torch_dtype", None)
        kwargs.pop("local_files_only", None)
        pipe = diffusion_pipeline.from_pretrained(repo, **kwargs)
    pipe = pipe.to("cuda" if cuda else "cpu")
    return pipe


def _generator(torch: Any, seed: int | None) -> Any:
    device = "cuda" if bool(
        getattr(getattr(torch, "cuda", None), "is_available", lambda: False)()
    ) else "cpu"
    gen = torch.Generator(device=device)
    if seed is not None:
        gen = gen.manual_seed(int(seed))
    return gen


def _call_with_compatible_kwargs(pipe: Any, variants: list[dict[str, Any]]) -> Any:
    """Call a pipeline with the first keyword set it accepts.

    Diffusers pipelines are not uniform. We try the most specific signature
    first, then progressively drop optional keys; a TypeError from a strict
    signature is the signal to try the next one, not to fail the task.
    """
    last: Exception | None = None
    for kwargs in variants:
        try:
            return pipe(**kwargs)
        except TypeError as e:
            last = e
            continue
    if last is not None:
        raise MediaModelError(f"管线参数不兼容：{last}") from last
    raise MediaModelError("管线没有可用的参数组合")


def _frames_to_array(frames: Any) -> list[Any]:
    import numpy as np
    from PIL import Image

    if frames is None:
        raise MediaModelError("管线没有返回视频帧")
    if hasattr(frames, "detach"):
        frames = frames.detach().cpu().numpy()
    if isinstance(frames, np.ndarray) and frames.ndim == 5:
        frames = frames[0]
    if isinstance(frames, np.ndarray) and frames.ndim == 4:
        frames = list(frames)
    out = []
    for f in frames:
        if hasattr(f, "detach"):
            f = f.detach().cpu().numpy()
        if isinstance(f, np.ndarray):
            f = np.clip(f, 0, 255).astype("uint8") if f.max() > 1 else (f * 255).astype("uint8")
            out.append(Image.fromarray(f))
        else:
            out.append(f.convert("RGB") if hasattr(f, "convert") else Image.fromarray(np.asarray(f)))
    if not out:
        raise MediaModelError("管线返回了空视频帧")
    return out


def _write_video(frames: Any, out: Path, *, fps: int) -> Path:
    try:
        import imageio
        import numpy as np
    except ImportError as e:  # pragma: no cover
        raise MediaEngineMissing("写出视频需要 imageio：pip install imageio imageio-ffmpeg") from e
    imgs = _frames_to_array(frames)
    out.parent.mkdir(parents=True, exist_ok=True)
    arr = [np.asarray(x.convert("RGB")) for x in imgs]
    try:
        imageio.mimsave(str(out), arr, fps=max(1, int(fps)), codec="libx264", quality=8)
    except Exception:  # noqa: BLE001 - codec wheel may be absent
        imageio.mimsave(str(out), arr, fps=max(1, int(fps)))
    return out


def _write_audio(samples: Any, sampling_rate: int, out: Path) -> Path:
    try:
        import numpy as np
    except ImportError as e:  # pragma: no cover
        raise MediaEngineMissing("写出音频需要 numpy") from e
    if hasattr(samples, "detach"):
        samples = samples.detach().cpu().numpy()
    arr = np.asarray(samples).squeeze()
    if arr.ndim > 1:
        arr = arr[0]
    if arr.size == 0:
        raise MediaModelError("管线返回了空音频")
    if arr.dtype.kind == "f":
        arr = np.clip(arr, -1.0, 1.0)
        arr = (arr * 32767).astype("int16")
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(max(8000, int(sampling_rate)))
        wf.writeframes(arr.tobytes())
    return out


def generate_video(
    model: str,
    prompt: str,
    *,
    image_path: str | None = None,
    width: int = 512,
    height: int = 512,
    num_frames: int = 16,
    fps: int = 8,
    num_inference_steps: int = 20,
    guidance_scale: float = 5.0,
    seed: int | None = None,
    output_format: str = "mp4",
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Generate a short video with a standard diffusers pipeline."""
    prompt = (prompt or "").strip()
    if not prompt and not image_path:
        raise MediaParamError("prompt 不能为空")
    if output_format not in ("mp4", "gif"):
        raise MediaParamError("output_format 必须是 mp4 或 gif")
    width = max(256, min(int(width), 2048)) // 8 * 8
    height = max(256, min(int(height), 2048)) // 8 * 8
    frames_n = max(4, min(int(num_frames), 257))
    steps = max(1, min(int(num_inference_steps), 100))
    started = time.monotonic()
    torch, _ = _imports()
    pipe = _load_pipeline(model)
    kwargs_base: dict[str, Any] = {
        "num_inference_steps": steps,
        "guidance_scale": max(1.0, float(guidance_scale)),
        "generator": _generator(torch, seed),
    }
    if prompt:
        kwargs_base["prompt"] = prompt
    if image_path:
        try:
            from PIL import Image
            kwargs_base["image"] = Image.open(image_path).convert("RGB")
        except Exception as e:  # noqa: BLE001
            raise MediaParamError(f"无法读取输入图片：{e}") from e
    # Different families name the frame count differently.
    variants = [
        {**kwargs_base, "num_frames": frames_n, "height": height, "width": width},
        {**kwargs_base, "video_length": frames_n, "height": height, "width": width},
        {**kwargs_base, "num_frames": frames_n},
        {**kwargs_base, "video_length": frames_n},
        kwargs_base,
    ]
    out = _call_with_compatible_kwargs(pipe, variants)
    frames = getattr(out, "frames", None)
    if frames is None:
        frames = getattr(out, "video", None)
    if frames is None and isinstance(out, (list, tuple)):
        frames = out
    root = Path(output_dir or Path.cwd() / "outputs" / "media")
    stem = f"video-{int(time.time())}"
    path = root / f"{stem}.{output_format}"
    saved = _write_video(frames, path, fps=fps)
    return {
        "ok": True, "engine": "diffusers", "kind": "video",
        "output_path": str(saved), "frames": len(_frames_to_array(frames)),
        "width": width, "height": height, "fps": max(1, int(fps)),
        "elapsed_s": round(time.monotonic() - started, 2),
    }


def generate_audio(
    model: str,
    prompt: str,
    *,
    duration_s: float = 10.0,
    num_inference_steps: int = 50,
    guidance_scale: float = 3.0,
    seed: int | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Generate audio with a standard diffusers audio pipeline."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise MediaParamError("prompt 不能为空")
    duration = max(1.0, min(float(duration_s), 120.0))
    steps = max(1, min(int(num_inference_steps), 200))
    started = time.monotonic()
    torch, _ = _imports()
    pipe = _load_pipeline(model)
    base = {
        "prompt": prompt,
        "num_inference_steps": steps,
        "guidance_scale": max(0.0, float(guidance_scale)),
        "generator": _generator(torch, seed),
    }
    out = _call_with_compatible_kwargs(pipe, [
        {**base, "audio_length_in_s": duration},
        {**base, "audio_length_s": duration},
        {**base, "duration": duration},
        base,
    ])
    audios = getattr(out, "audios", None)
    if audios is None:
        audios = getattr(out, "audio", None)
    if audios is None and isinstance(out, (list, tuple)):
        audios = out
    if isinstance(audios, list):
        if not audios:
            raise MediaModelError("管线没有返回音频")
        samples = audios[0]
    else:
        samples = audios
    rate = int(getattr(out, "sampling_rate", None) or getattr(out, "sample_rate", None) or 16000)
    root = Path(output_dir or Path.cwd() / "outputs" / "media")
    path = root / f"audio-{int(time.time())}.wav"
    saved = _write_audio(samples, rate, path)
    return {
        "ok": True, "engine": "diffusers", "kind": "audio",
        "output_path": str(saved), "sampling_rate": rate,
        "elapsed_s": round(time.monotonic() - started, 2),
    }
