"""Per-model run availability — the single truth for "can I actually use it?"

The catalog's ``engine`` field describes what the *upstream model* supports,
not what this app has already wired. Treating the two as the same produced the
recurring "download works, Run button does nothing" complaint. This module
turns catalog metadata into an explicit, testable verdict:

* ``native``      — an in-process runtime exists and the downloaded files match
                    the format it needs (llama.cpp GGUF, MNN repo, Transformers
                    directory, Diffusers pipeline, etc.);
* ``external``    — a supported external service can run it (ComfyUI, vLLM,
                    SGLang), but the user must start/point that service;
* ``unsupported`` — no adapter exists yet. The card stays in the market
                    because the weights are genuinely open; the UI must say
                    "temporarily unavailable" instead of pretending.

Nothing here performs network I/O or imports torch. It is safe to call while
rendering a list of 259 cards.
"""
from __future__ import annotations

from typing import Any

#: Engines with a real in-process adapter and the UI action they map to.
NATIVE_ENGINES: dict[str, dict[str, str]] = {
    "llama.cpp": {"action": "chat", "label": "llama.cpp 对话"},
    "mnn": {"action": "chat", "label": "MNN 对话"},
    "transformers": {"action": "chat", "label": "Transformers 本地对话"},
    "diffusers": {"action": "image", "label": "Diffusers 本地出图"},
    "sentence-transformers": {"action": "embedding", "label": "向量嵌入"},
    "faster-whisper": {"action": "asr", "label": "语音转写"},
    "demucs": {"action": "separation", "label": "Demucs 分轨"},
    "audio-separator": {"action": "separation", "label": "RoFormer 人声分离"},
    "piper": {"action": "tts", "label": "Piper 语音合成"},
    "ltx-video": {"action": "video", "label": "LTX 视频生成"},
    "trellis": {"action": "3d", "label": "TRELLIS 3D"},
    "triposr": {"action": "3d", "label": "TripoSR 3D"},
    "hunyuan3d": {"action": "3d", "label": "Hunyuan3D"},
    "direct3d-s2": {"action": "3d", "label": "Direct3D-S2"},
    "partcrafter": {"action": "3d", "label": "PartCrafter"},
    "triposg": {"action": "3d", "label": "TripoSG"},
    "hunyuanworld": {"action": "3d", "label": "HunyuanWorld"},
    "sam3d": {"action": "3d", "label": "SAM 3D"},
    "pixal3d": {"action": "3d", "label": "Pixal3D"},
    "4danyone": {"action": "3d", "label": "4DAnyone"},
}

#: Engines that need a separately started service.
EXTERNAL_ENGINES: dict[str, dict[str, str]] = {
    "vllm": {"service": "vLLM", "label": "vLLM / OpenAI 兼容服务"},
    "sglang": {"service": "SGLang", "label": "SGLang / OpenAI 兼容服务"},
    "sglang-omni": {"service": "SGLang-Omni", "label": "SGLang-Omni 服务"},
    "comfyui": {"service": "ComfyUI", "label": "ComfyUI 工作流"},
    "ollama": {"service": "Ollama", "label": "Ollama 服务"},
    "mlx": {"service": "MLX", "label": "Apple MLX（仅 macOS）"},
    "fish-speech": {"service": "Fish Speech", "label": "Fish Speech"},
    "f5-tts": {"service": "F5-TTS", "label": "F5-TTS"},
    "cosyvoice": {"service": "CosyVoice", "label": "CosyVoice"},
    "spark-tts": {"service": "Spark-TTS", "label": "Spark-TTS"},
    "indextts": {"service": "IndexTTS", "label": "IndexTTS"},
    "gpt-sovits": {"service": "GPT-SoVITS", "label": "GPT-SoVITS"},
    "comfyui-fl-minimaxmusic3": {"service": "ComfyUI", "label": "ComfyUI-FL-MiniMaxMusic3"},
}


#: Dedicated adapters that are not selected by the generic engine label.
SPECIAL_NATIVE: dict[str, dict[str, str]] = {
    "moss-tts-1.5": {"engine": "tts-runtime", "action": "tts", "label": "MOSS-TTS 本地语音"},
    "dots-tts-soar": {"engine": "tts-runtime", "action": "tts", "label": "dots.tts 本地语音"},
    "voxcpm2": {"engine": "tts-runtime", "action": "tts", "label": "VoxCPM2 本地语音"},
    "qwen3-tts-1.7b-customvoice": {"engine": "tts-runtime", "action": "tts", "label": "Qwen3-TTS 本地语音"},
    "orpheus-3b": {"engine": "tts-runtime", "action": "tts", "label": "Orpheus 本地语音"},
    "real-esrgan": {"engine": "realesrgan", "action": "superres", "label": "Real-ESRGAN 超分"},
    "4x-ultrasharp": {"engine": "realesrgan", "action": "superres", "label": "4x-UltraSharp 超分"},
    "insightface": {"engine": "insightface", "action": "faces", "label": "InsightFace 人脸"},
    "yolov10": {"engine": "yolov10", "action": "detect", "label": "YOLOv10 检测"},
    "pp-ocrv6-det": {"engine": "paddleocr", "action": "ocr", "label": "PP-OCRv6 检测组件"},
    "pp-ocrv6-rec": {"engine": "paddleocr", "action": "ocr", "label": "PP-OCRv6 识别组件"},
    "pyannote-diarization-community-1": {"engine": "diarization", "action": "diarize", "label": "pyannote 分轨"},
    "nemotron-3-diarization": {"engine": "diarization", "action": "diarize", "label": "Nemotron 分轨"},
    "qwen2-audio-7b": {"engine": "qwen2-audio", "action": "audio-chat", "label": "Qwen2-Audio 对话"},
    "gliner2.5": {"engine": "gliner2", "action": "decide", "label": "GLiNER2.5 决策"},
    "laya-decision": {"engine": "laya", "action": "decide", "label": "Laya 决策"},
    # These are real in-process PiperManager voices (pip engines), not
    # external services; availability.py previously contradicted the runtime.
    "kokoro-82m": {"engine": "kokoro-engine", "action": "tts", "label": "Kokoro 本地语音"},
    "chatterbox": {"engine": "chatterbox", "action": "tts", "label": "Chatterbox 本地语音"},
}

SPECIAL_EXTERNAL: dict[str, dict[str, str]] = {
    "auk": {"engine": "sglang-omni", "service": "SGLang-Omni", "label": "AuK / SGLang-Omni"},
    "breeze-tts-2": {"engine": "breeze-tts", "service": "Breeze TTS API", "label": "Breeze TTS 官方服务"},
    "magenta-realtime-2": {"engine": "magenta-rt", "service": "Magenta RT", "label": "Magenta RealTime 服务"},
    "clm-v0.1-8b": {"engine": "contrastive-lm", "service": "Contrastive-LM", "label": "CLM 服务"},
}

#: Music models with a dedicated in-process runtime (``music_runtime``).
MUSIC_NATIVE_IDS: frozenset[str] = frozenset({
    "preview-music", "musicgen-small", "musicgen-medium", "musicgen-large",
    "musicgen-stereo", "ace-step-1.5", "minimax-music3",
})

#: Video models with the dedicated LTX task runtime.
LTX_NATIVE_IDS: frozenset[str] = frozenset({"ltx-2.5"})


def _engines(model: Any) -> list[str]:
    raw = getattr(model, "engine", None)
    if raw is None and isinstance(model, dict):
        raw = model.get("engine")
    if isinstance(raw, str):
        raw = [raw]
    return [str(e).strip() for e in (raw or []) if str(e).strip()]


def _attr(model: Any, key: str, default: Any = "") -> Any:
    if isinstance(model, dict):
        return model.get(key, default)
    return getattr(model, key, default)


def _compatible_native(model: Any, engine: str) -> tuple[bool, str]:
    """Check the file/repo prerequisite for an in-process adapter.

    These checks are intentionally conservative: a llama.cpp label without a
    GGUF repo cannot load, and an MNN label without an MNN-format repo cannot
    load. Returning ``False`` here is better than letting the UI promise a
    Run button that immediately errors.
    """
    category = str(_attr(model, "category", ""))
    model_id = str(_attr(model, "id", ""))
    if engine in ("llama.cpp",):
        if not str(_attr(model, "gguf_repo", "")).strip():
            return False, "缺少 GGUF 仓库（只有原始权重）"
    elif engine == "mnn":
        if not str(_attr(model, "mnn_repo", "")).strip():
            return False, "缺少 MNN 转换仓库"
    elif engine == "transformers":
        # Causal-LM chat for text/vision-language models. Standard TTS models
        # still need a dedicated speech endpoint, so they stay unsupported.
        if category not in ("llm", "vision"):
            return False, "Transformers 本地对话仅覆盖 LLM/视觉语言模型"
    elif engine == "diffusers":
        # One generic DiffusionPipeline adapter now covers images, video and
        # audio. It is still model-specific at load time, but there is a real
        # endpoint rather than a dead label.
        if category not in ("image", "superres", "video", "audio"):
            return False, "Diffusers 通用管线仅覆盖图像/超分/视频/音频"
    elif engine in ("sentence-transformers",):
        if category != "embedding":
            return False, "仅嵌入模型"
    elif engine in ("faster-whisper",):
        if category != "audio":
            return False, "仅音频转写模型"
    elif engine in ("demucs", "audio-separator"):
        if category != "audio":
            return False, "仅音频分离模型"
    elif engine == "piper":
        if category != "tts":
            return False, "仅 Piper 语音模型"
    elif engine in ("ltx-video",):
        if not model_id.startswith("ltx"):
            return False, "仅 LTX 视频模型"
    elif engine in NATIVE_ENGINES and category == "3d":
        return True, ""
    return True, ""


def _native_label(engine: str, category: str) -> tuple[str, str]:
    """Return (action, label) for a native engine in a model category."""
    if engine == "diffusers":
        if category == "video":
            return "video", "Diffusers 本地视频"
        if category == "audio":
            return "audio", "Diffusers 本地音频"
        return "image", "Diffusers 本地出图"
    if engine == "transformers" and category == "tts":
        return "tts", "Transformers 本地语音"
    info = NATIVE_ENGINES[engine]
    return info["action"], info["label"]


CONVERSION_HINTS: dict[str, dict[str, str]] = {
    "mnn": {"kind": "hf-to-mnn", "label": "HF → MNN 转换后由 MNN 运行"},
    "llama.cpp": {"kind": "hf-to-gguf", "label": "HF → GGUF 转换后由 llama.cpp 运行"},
    "mlx": {"kind": "hf-to-mlx", "label": "HF → MLX 转换后由 MLX 运行"},
    "onnxruntime": {"kind": "torch-to-onnx", "label": "PyTorch → ONNX 转换后运行"},
}


def _conversion_hint(engines: list[str], native: list[dict[str, str]]) -> dict[str, str] | None:
    """Offer a format conversion when the label exists but files do not.

    This is the "聚合" part: a model does not have to be shipped in one
    runtime's exact format. If the upstream repo is HF/torch weights, the app
    converter can produce GGUF/MNN/MLX/ONNX and then use the matching engine.
    """
    if native:
        return None
    for engine in engines:
        hint = CONVERSION_HINTS.get(engine)
        if hint:
            return dict(hint)
    return None


def describe(model: Any) -> dict[str, Any]:
    """Return a JSON-ready availability verdict for one catalog entry."""
    engines = _engines(model)
    category = str(_attr(model, "category", ""))
    model_id = str(_attr(model, "id", ""))
    native: list[dict[str, str]] = []
    # Dedicated runtimes are not engine-name driven; add them first so a
    # generic "diffusers" or "transformers" label does not hide the fact that
    # the app has a purpose-built page for this model.
    if model_id in MUSIC_NATIVE_IDS:
        native.append({
            "engine": "music-runtime", "action": "music",
            "label": "本地音乐生成",
        })
    if model_id in LTX_NATIVE_IDS:
        native.append({
            "engine": "ltx-video", "action": "video",
            "label": "LTX 视频生成",
        })
    if model_id in SPECIAL_NATIVE:
        native.append(dict(SPECIAL_NATIVE[model_id]))
    for engine in engines:
        if engine not in NATIVE_ENGINES:
            continue
        ok, reason = _compatible_native(model, engine)
        if ok:
            action, label = _native_label(engine, category)
            if not any(x["engine"] == engine for x in native):
                native.append({
                    "engine": engine,
                    "action": action,
                    "label": label,
                })
    external: list[dict[str, str]] = []
    if model_id in SPECIAL_EXTERNAL:
        special = SPECIAL_EXTERNAL[model_id]
        external.append({
            "engine": special["engine"],
            "service": special["service"],
            "label": special["label"],
        })
    for engine in engines:
        if engine in EXTERNAL_ENGINES:
            service = EXTERNAL_ENGINES[engine]["service"]
            if not any(x["service"] == service for x in external):
                external.append({
                    "engine": engine,
                    "service": service,
                    "label": EXTERNAL_ENGINES[engine]["label"],
                })

    if native:
        status = "native"
        reason = ""
    elif external:
        status = "external"
        reason = "需要先启动外部引擎：" + "、".join(x["service"] for x in external)
    else:
        status = "unsupported"
        reason = "当前版本还没有该引擎的加载适配器"
    return {
        "status": status,
        "category": category,
        "engines": engines,
        "native": native,
        "external": external,
        "reason": reason,
        "conversion": None if external else _conversion_hint(engines, native),
        "downloadable": bool(str(_attr(model, "repo", "")).strip()),
    }


def summary(models: list[Any]) -> dict[str, Any]:
    """Count availability across a catalog, grouped by status/category."""
    counts = {"native": 0, "external": 0, "unsupported": 0}
    by_category: dict[str, dict[str, int]] = {}
    for m in models:
        d = describe(m)
        counts[d["status"]] += 1
        bucket = by_category.setdefault(d["category"], {
            "native": 0, "external": 0, "unsupported": 0,
        })
        bucket[d["status"]] += 1
    return {"total": len(models), "counts": counts, "by_category": by_category}
