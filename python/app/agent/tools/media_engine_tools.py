"""Agent tools that drive the on-device media runtimes.

These tools let the agent actually *run* the engines shipped in recent
releases — speech recognition, embeddings, source separation, speech
synthesis, and multimodal understanding/generation — instead of only
planning. Each tool persists its heavy artifacts (vectors, audio, images) to
the data directory and returns their paths, so one tool's output can be fed
to the next to form a workflow (e.g. transcribe → embed; prompt → speak).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ...settings import default_data_root
from ..tool_registry import Tool, ToolContext

# Default repos (verified in the catalog).
_WHISPER_REPO = "Systran/faster-whisper-large-v3"
_EMBED_REPO = "BAAI/bge-m3"
_DEMUCS_MODEL = "htdemucs"
_PIPER_VOICE = "en_US-amy-medium"


def _artifact_dir() -> Path:
    d = default_data_root() / "agent_artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manager(ctx: ToolContext, key: str, ctor: Any) -> Any:
    """Return a cached runtime manager, created once per ToolContext."""
    store = ctx.extra.setdefault("_media_managers", {})
    if key not in store:
        store[key] = ctor()
    return store[key]


def _engine_missing(e: Exception) -> bool:
    return "EngineMissing" in type(e).__name__


# ---------------------------------------------------------------------------
# 1. Speech recognition
# ---------------------------------------------------------------------------
def _asr_transcribe(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    audio_path = str(params.get("audio_path") or "").strip()
    if not audio_path:
        return {"error": "audio_path is required"}
    if not Path(audio_path).exists():
        return {"error": f"audio file not found: {audio_path}"}
    repo = str(params.get("model") or _WHISPER_REPO).strip()
    language = params.get("language") or None
    try:
        from ...asr_runtime import AsrManager

        mgr = _manager(ctx, "asr", AsrManager)
        res = mgr.transcribe(repo, audio_path, language=language)
        return {
            "text": res.get("text", ""),
            "language": res.get("language"),
            "segment_count": len(res.get("segments") or []),
            "model": repo,
        }
    except Exception as e:  # noqa: BLE001
        hint = "请先在「语音转写」页安装 Faster Whisper 引擎。" if _engine_missing(e) else ""
        return {"error": f"{e} {hint}".strip(), "error_type": type(e).__name__}


asr_transcribe = Tool(
    name="asr_transcribe",
    description="Transcribe a local audio/video file to text using an on-device Whisper model. Returns the recognized text and detected language.",
    parameters={
        "type": "object",
        "properties": {
            "audio_path": {"type": "string", "description": "Absolute path to the audio or video file."},
            "model": {"type": "string", "description": f"Whisper repo (default {_WHISPER_REPO})."},
            "language": {"type": "string", "description": "Optional ISO language code, e.g. 'en' or 'zh'."},
        },
        "required": ["audio_path"],
    },
    handler=_asr_transcribe,
    category="media",
)


# ---------------------------------------------------------------------------
# 2. Embeddings
# ---------------------------------------------------------------------------
def _embed_text(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    texts = params.get("texts")
    if isinstance(texts, str):
        texts = [texts]
    if not isinstance(texts, list) or not texts or not all(
        isinstance(t, str) and t.strip() for t in texts
    ):
        return {"error": "texts must be a non-empty string or list of strings"}
    repo = str(params.get("model") or _EMBED_REPO).strip()
    try:
        from ...embedding_runtime import EmbeddingManager

        mgr = _manager(ctx, "embedding", EmbeddingManager)
        res = mgr.embed(repo, texts, normalize_embeddings=True)
        # Persist vectors (often large) to a JSON sidecar instead of inlining.
        out = _artifact_dir() / f"embeddings_{int(time.time()*1000)}.json"
        out.write_text(
            json.dumps(
                {"model": repo, "dimensions": res["dimensions"],
                 "embeddings": res["embeddings"]},
                ensure_ascii=False),
            encoding="utf-8")
        return {
            "count": len(texts),
            "dimensions": res["dimensions"],
            "vectors_path": str(out),
            "model": repo,
        }
    except Exception as e:  # noqa: BLE001
        hint = "请先在「向量嵌入」页安装 Sentence Transformers 引擎。" if _engine_missing(e) else ""
        return {"error": f"{e} {hint}".strip(), "error_type": type(e).__name__}


embed_text = Tool(
    name="embed_text",
    description="Compute embedding vectors for one or more texts with an on-device sentence-transformers model. Vectors are saved to a JSON file (path returned) for downstream similarity/retrieval use.",
    parameters={
        "type": "object",
        "properties": {
            "texts": {"type": "array", "items": {"type": "string"},
                      "description": "One string or a list of strings to embed."},
            "model": {"type": "string", "description": f"Embedding repo (default {_EMBED_REPO})."},
        },
        "required": ["texts"],
    },
    handler=_embed_text,
    category="media",
)


# ---------------------------------------------------------------------------
# 3. Source separation
# ---------------------------------------------------------------------------
def _separate_audio(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    audio_path = str(params.get("audio_path") or "").strip()
    if not audio_path:
        return {"error": "audio_path is required"}
    if not Path(audio_path).exists():
        return {"error": f"audio file not found: {audio_path}"}
    model = str(params.get("model") or _DEMUCS_MODEL).strip()
    try:
        from ...demucs_runtime import DemucsManager

        mgr = _manager(ctx, "demucs", DemucsManager)
        res = mgr.separate(model, Path(audio_path))
        return {
            "stems": [s.get("path") for s in res.get("stems", [])],
            "stem_names": [s.get("name") for s in res.get("stems", [])],
            "output_dir": res.get("output_dir"),
            "model": model,
        }
    except Exception as e:  # noqa: BLE001
        hint = "请先在「音源分离」页安装 Demucs 引擎。" if _engine_missing(e) else ""
        return {"error": f"{e} {hint}".strip(), "error_type": type(e).__name__}


separate_audio = Tool(
    name="separate_audio",
    description="Separate a local music file into stems (vocals, drums, bass, other) using an on-device Demucs model. Returns the path of each stem WAV.",
    parameters={
        "type": "object",
        "properties": {
            "audio_path": {"type": "string", "description": "Absolute path to the music file."},
            "model": {"type": "string", "description": f"Demucs model name (default {_DEMUCS_MODEL})."},
        },
        "required": ["audio_path"],
    },
    handler=_separate_audio,
    category="media",
)


# ---------------------------------------------------------------------------
# 4. Speech synthesis
# ---------------------------------------------------------------------------
def _tts_speak(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    text = str(params.get("text") or "").strip()
    if not text:
        return {"error": "text is required"}
    voice = str(params.get("voice") or _PIPER_VOICE).strip()
    length_scale = params.get("length_scale")
    try:
        from ...piper_runtime import PiperManager

        mgr = _manager(ctx, "piper", PiperManager)
        kw: dict[str, Any] = {}
        if length_scale is not None:
            kw["length_scale"] = float(length_scale)
        res = mgr.synthesize(voice, text, **kw)
        return {
            "audio_path": res.get("path"),
            "duration_s": res.get("duration_s"),
            "voice": voice,
        }
    except Exception as e:  # noqa: BLE001
        hint = "请先在「本地语音合成」页安装 Piper 引擎与语音。" if _engine_missing(e) else ""
        return {"error": f"{e} {hint}".strip(), "error_type": type(e).__name__}


tts_speak = Tool(
    name="tts_speak",
    description="Synthesize speech from text fully on-device with a Piper voice. Returns the path of the generated WAV.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to speak."},
            "voice": {"type": "string", "description": f"Piper voice id (default {_PIPER_VOICE})."},
            "length_scale": {"type": "number", "description": "Speech pace; <1 is faster, >1 slower (default 1)."},
        },
        "required": ["text"],
    },
    handler=_tts_speak,
    category="media",
)


# ---------------------------------------------------------------------------
# 5. Multimodal image understanding
# ---------------------------------------------------------------------------
def _vision_ask(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    question = str(params.get("question") or "").strip()
    if not question:
        return {"error": "question is required"}
    model = str(params.get("model") or "smolvlm-256").strip()
    image_path = params.get("image_path")
    images = None
    if image_path:
        image_path = str(image_path).strip()
        if not Path(image_path).exists():
            return {"error": f"image file not found: {image_path}"}
        images = [image_path]
    try:
        from ...multimodal_runtime import MultimodalManager

        mgr = _manager(ctx, "multimodal", MultimodalManager)
        res = mgr.chat(model, question, images=images)
        return {"text": res.get("text", ""), "model": model,
                "multimodal": res.get("multimodal", bool(images))}
    except Exception as e:  # noqa: BLE001
        hint = "请先在「多模态对话」页安装 Transformers 引擎。" if _engine_missing(e) else ""
        return {"error": f"{e} {hint}".strip(), "error_type": type(e).__name__}


vision_ask = Tool(
    name="vision_ask",
    description="Answer a question about an image (or plain text) using an on-device multimodal model. Returns the answer text.",
    parameters={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question to ask."},
            "image_path": {"type": "string", "description": "Optional absolute path to an image."},
            "model": {"type": "string", "description": "Multimodal model id (default smolvlm-256)."},
        },
        "required": ["question"],
    },
    handler=_vision_ask,
    category="media",
)


# ---------------------------------------------------------------------------
# 6. Text-to-image
# ---------------------------------------------------------------------------
def _gen_image(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    prompt = str(params.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt is required"}
    model = str(params.get("model") or "janus-pro-7b").strip()
    try:
        from ...multimodal_runtime import MultimodalManager

        mgr = _manager(ctx, "multimodal", MultimodalManager)
        num_images = int(params.get("num_images") or 1)
        kw: dict[str, Any] = {"num_images": num_images}
        if params.get("guidance_scale") is not None:
            kw["guidance_scale"] = float(params["guidance_scale"])
        if params.get("seed") is not None and params.get("seed") != "":
            kw["seed"] = int(params["seed"])
        res = mgr.generate_image(model, prompt, **kw)
        out_dir = _artifact_dir()
        paths: list[str] = []
        for i, img in enumerate(res.get("images", [])):
            p = out_dir / f"agentimg_{int(time.time()*1000)}_{i}.png"
            img.save(p)
            paths.append(str(p))
        return {"images": paths, "count": len(paths), "model": model}
    except Exception as e:  # noqa: BLE001
        hint = "请先在「多模态对话」页安装 Transformers 引擎与 Janus 模型。" if _engine_missing(e) else ""
        return {"error": f"{e} {hint}".strip(), "error_type": type(e).__name__}


gen_image = Tool(
    name="gen_image",
    description="Generate images from a text prompt on-device with an autoregressive Janus model. Returns the path of each generated PNG.",
    parameters={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Image description."},
            "model": {"type": "string", "description": "Image model id (default janus-pro-7b)."},
            "num_images": {"type": "integer", "description": "Number of images (default 1)."},
            "guidance_scale": {"type": "number", "description": "CFG guidance (default 5)."},
            "seed": {"type": "integer", "description": "Optional seed for reproducibility."},
        },
        "required": ["prompt"],
    },
    handler=_gen_image,
    category="media",
)


MEDIA_ENGINE_TOOLS = [
    asr_transcribe,
    embed_text,
    separate_audio,
    tts_speak,
    vision_ask,
    gen_image,
]
