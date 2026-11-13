"""Deterministic offline preview engines.

These are **not** AI models and never claim to be. They exist so that the
video / image / 3D / music pages remain usable on machines without a GPU or
with too little RAM to load multi-GB checkpoints. Every artifact produced here
carries ``engine="preview"`` in its metadata and the UI should label it as a
preview.

Design goals:
* deterministic (same prompt + seed → same output), so bug reports reproduce;
* no network, no model download, only PIL / numpy / imageio / trimesh;
* bounded runtime (seconds, not minutes) even on a 2-core CPU.
"""
from __future__ import annotations

import hashlib
import math
import wave
from pathlib import Path
from typing import Any

PREVIEW_IMAGE_MODEL = "kevrai/preview-image"
PREVIEW_VIDEO_MODEL = "kevrai/preview-video"
PREVIEW_3D_MODEL = "kevrai/preview-3d"
PREVIEW_MUSIC_MODEL = "kevrai/preview-music"


def _seed_for(text: str, seed: int | None = None) -> int:
    if seed is not None and int(seed) >= 0:
        return int(seed)
    h = hashlib.sha256((text or "kevrai").encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _palette(seed: int) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    import colorsys
    hue = (seed % 360) / 360.0
    c1 = colorsys.hsv_to_rgb(hue, 0.62, 0.92)
    c2 = colorsys.hsv_to_rgb((hue + 0.42) % 1.0, 0.55, 0.45)
    c3 = colorsys.hsv_to_rgb((hue + 0.78) % 1.0, 0.40, 0.98)
    return tuple(int(v * 255) for v in c1), tuple(int(v * 255) for v in c2), tuple(int(v * 255) for v in c3)  # type: ignore[return-value]


def make_image(prompt: str, width: int = 768, height: int = 432,
               seed: int | None = None) -> Any:
    """Render an abstract poster-like RGB image (PIL.Image)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    width = max(64, min(int(width), 2048))
    height = max(64, min(int(height), 2048))
    s = _seed_for(prompt, seed)
    c1, c2, c3 = _palette(s)
    rng = np.random.default_rng(s)

    # Vertical gradient base.
    y = np.linspace(0, 1, height)[:, None]
    x = np.linspace(0, 1, width)[None, :]
    base = np.zeros((height, width, 3), dtype=np.float32)
    for i in range(3):
        base[..., i] = (c1[i] * (1 - y) + c2[i] * y) * (0.75 + 0.25 * x)

    # Soft orbs.
    img = Image.fromarray(np.clip(base, 0, 255).astype("uint8"), "RGB")
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(9):
        r = int(rng.integers(min(width, height) // 12, min(width, height) // 3))
        cx = int(rng.integers(-r, width + r))
        cy = int(rng.integers(-r, height + r))
        col = c3 if rng.random() > 0.5 else c1
        d.ellipse((cx - r, cy - r, cx + r, cy + r),
                  fill=(*col, int(rng.integers(45, 130))))
    layer = layer.filter(ImageFilter.GaussianBlur(radius=max(8, min(width, height) // 18)))
    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")

    # Foreground lines give the preview a designed-poster feel.
    d = ImageDraw.Draw(img)
    for i in range(5):
        yy = int(height * (0.15 + 0.16 * i)) + int(rng.integers(-8, 8))
        d.line((0, yy, width, yy - int(rng.integers(-30, 30))),
               fill=c3, width=max(1, width // 380))

    # Prompt caption (ASCII only: default PIL font has no CJK glyphs).
    caption = "".join(ch if ord(ch) < 128 else " " for ch in prompt).strip()
    caption = " ".join(caption.split())[:90] or "kevrai preview"
    try:
        font = ImageFont.load_default(size=max(12, width // 48))
    except Exception:  # noqa: BLE001 — older Pillow has no sized default font
        font = ImageFont.load_default()
    d.text((int(width * 0.055), int(height * 0.88)), caption, fill=(255, 255, 255), font=font)
    d.text((int(width * 0.055), int(height * 0.94)), "Kevrai preview engine",
           fill=(230, 230, 230), font=font)
    return img


def write_video(prompt: str, out_path: Path, *, width: int = 512, height: int = 288,
                num_frames: int = 48, fps: int = 12, seed: int | None = None,
                image_path: str = "") -> Path:
    """Render a short MP4/GIF from abstract frames or a supplied still image."""
    import numpy as np
    from PIL import Image, ImageDraw

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    width = max(64, min(int(width), 1280))
    height = max(64, min(int(height), 1280))
    num_frames = max(9, min(int(num_frames), 257))
    fps = max(4, min(int(fps), 60))
    s = _seed_for(prompt, seed)
    c1, c2, c3 = _palette(s)

    src = None
    if image_path and Path(image_path).exists():
        src = Image.open(image_path).convert("RGB")

    frames: list[Any] = []
    for i in range(num_frames):
        t = i / max(1, num_frames - 1)
        if src is not None:
            # Ken-Burns: slow zoom + horizontal pan.
            scale = 1.04 + 0.10 * t
            w, h = src.size
            crop_w, crop_h = int(w / scale), int(h / scale)
            left = int((w - crop_w) * (0.5 + 0.35 * math.sin(t * math.pi)))
            top = int((h - crop_h) * 0.5)
            frame = src.crop((left, top, left + crop_w, top + crop_h)).resize((width, height))
        else:
            y = np.linspace(0, 1, height)[:, None]
            x = np.linspace(0, 1, width)[None, :]
            arr = np.zeros((height, width, 3), dtype=np.float32)
            for k in range(3):
                arr[..., k] = (c1[k] * (1 - y) + c2[k] * y) * (0.7 + 0.3 * x)
            img = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGB")
            d = ImageDraw.Draw(img)
            # Sun / moon rising.
            cx = int(width * (0.18 + 0.64 * t))
            cy = int(height * (0.68 - 0.34 * math.sin(t * math.pi)))
            r = int(min(width, height) * 0.13)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c3)
            # Hills.
            for band in range(3):
                base_y = int(height * (0.72 + band * 0.09))
                pts = []
                for xx in range(0, width + 16, 16):
                    yy = base_y + int(18 * math.sin(xx / 55 + band * 1.7 + t * 3))
                    pts.append((xx, yy))
                d.line(pts, fill=c2, width=max(2, height // 70))
            d.text((12, height - 22), "Kevrai preview", fill=(255, 255, 255))
            frame = img
        frames.append(np.asarray(frame))

    try:
        import imageio
        if out_path.suffix.lower() == ".gif":
            imageio.mimsave(str(out_path), frames, fps=fps, loop=0)
        else:
            imageio.mimsave(str(out_path), frames, fps=fps, codec="libx264",
                            quality=7, macro_block_size=1)
        return out_path
    except Exception:  # noqa: BLE001 — fall back to GIF next to the target
        import imageio
        gif = out_path.with_suffix(".gif")
        imageio.mimsave(str(gif), frames, fps=fps, loop=0)
        return gif


def make_mesh(prompt: str, *, seed: int | None = None) -> Any:
    """Build a deterministic textured-ish icosphere mesh (trimesh.Trimesh)."""
    import numpy as np
    import trimesh

    s = _seed_for(prompt, seed)
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
    v = np.asarray(mesh.vertices)
    # Multi-frequency radial displacement → rock/asteroid look.
    phase = (s % 97) / 31.0
    disp = (0.10 * np.sin(v[:, 0] * 4.1 + phase)
            + 0.07 * np.cos(v[:, 1] * 6.3 + phase * 2)
            + 0.04 * np.sin(v[:, 2] * 9.7 + phase * 3))
    v = v * (1.0 + disp)[:, None]
    mesh.vertices = v
    c1, _, _ = _palette(s)
    mesh.visual.vertex_colors = np.tile(
        np.array([*c1, 255], dtype=np.uint8), (len(v), 1))
    mesh.fix_normals()
    return mesh


def write_mesh(prompt: str, out_path: Path, *, fmt: str = "glb",
               seed: int | None = None) -> Path:

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mesh = make_mesh(prompt, seed=seed)
    mesh.export(str(out_path), file_type=fmt)
    return out_path


def write_music(prompt: str, out_path: Path, *, duration_s: float = 10.0,
                seed: int | None = None) -> Path:
    """Write a deterministic stereo WAV: pad chords + arpeggio + soft pulse."""
    import numpy as np

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration_s = max(1.0, min(float(duration_s), 60.0))
    sr = 44100
    n = int(sr * duration_s)
    t = np.arange(n) / sr
    s = _seed_for(prompt, seed)
    rng = np.random.default_rng(s)

    # Chord progression derived from the prompt seed.
    roots = [220.0, 196.0, 261.63, 174.61]
    progression = [roots[(s + i) % len(roots)] for i in range(4)]
    chord_len = duration_s / 4.0
    left = np.zeros(n, dtype=np.float64)
    right = np.zeros(n, dtype=np.float64)

    for chord_idx, root in enumerate(progression):
        start = int(chord_idx * chord_len * sr)
        end = min(n, int((chord_idx + 1) * chord_len * sr))
        if end <= start:
            continue
        tt = t[start:end] - t[start]
        env = np.minimum(1.0, tt / 0.08) * np.exp(-tt * 0.16)
        freqs = [root, root * 1.5, root * 2.0, root * 2.5]
        for k, f in enumerate(freqs):
            detune = 1.0 + (0.003 * (k - 1.5))
            left[start:end] += 0.10 * env * np.sin(2 * np.pi * f * detune * tt + k)
            right[start:end] += 0.10 * env * np.sin(2 * np.pi * f * (2 - detune) * tt + k + 0.4)

    # Arpeggio pulse on eighths.
    step = int(sr * 0.25)
    for i in range(0, n, step):
        f = roots[(i // step + s) % len(roots)] * 2.0
        dur = min(step, n - i)
        tt = np.arange(dur) / sr
        env = np.exp(-tt * 6.0)
        tone = 0.16 * env * np.sin(2 * np.pi * f * tt)
        left[i:i + dur] += tone
        right[i:i + dur] += tone

    # Soft noise/percussion bed.
    noise = rng.normal(0, 1, n) * 0.02
    left += noise
    right += np.roll(noise, 137)

    # Normalize and write 16-bit PCM.
    peak = max(1e-6, float(max(np.abs(left).max(), np.abs(right).max())))
    gain = 0.85 / peak
    pcm = np.stack([left * gain, right * gain], axis=1)
    pcm16 = np.clip(pcm * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes(pcm16.tobytes())
    return out_path


def describe() -> dict[str, Any]:
    return {
        "image": PREVIEW_IMAGE_MODEL,
        "video": PREVIEW_VIDEO_MODEL,
        "three_d": PREVIEW_3D_MODEL,
        "music": PREVIEW_MUSIC_MODEL,
        "note": "offline deterministic preview engines; not AI models",
    }
