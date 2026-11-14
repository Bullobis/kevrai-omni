#!/usr/bin/env python3
"""Build the per-model capability manifest in catalog/models.json.

Evidence order:
1. Hugging Face ``pipeline_tag`` and repository tags (official hub metadata).
2. Hugging Face model-card README mentions (only for fields we can prove).
3. Curated catalog tags / category fallback, explicitly marked as inferred.

The manifest is deliberately conservative: a control is listed only when
there is evidence for it.  Models that share a category are not assumed to
share a form.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "models.json"
HF_CACHE = Path("/tmp/hf_meta.json")
README_CACHE = Path("/tmp/hf_readmes.json")
READMES: dict[str, str] = {}

PIPELINE_TASKS = {
    "text-to-image": "text-to-image",
    "image-to-image": "image-to-image",
    "image-to-video": "image-to-video",
    "text-to-video": "text-to-video",
    "video-to-video": "video-to-video",
    "image-text-to-video": "image-to-video",
    "text-to-audio": "text-to-audio",
    "audio-to-audio": "audio-to-audio",
    "text-to-speech": "text-to-speech",
    "automatic-speech-recognition": "asr",
    "image-to-3d": "image-to-3d",
    "text-to-3d": "text-to-3d",
    "mask-generation": "segmentation",
    "zero-shot-image-classification": "image-classification",
    "object-detection": "detection",
    "image-text-to-text": "image-understanding",
    "audio-text-to-text": "audio-understanding",
    "image-to-text": "image-captioning",
    "feature-extraction": "embedding",
    "image-feature-extraction": "embedding",
    "sentence-similarity": "embedding",
    "text-ranking": "embedding",
    "text-generation": "text-generation",
    "text-classification": "text-classification",
    "any-to-any": "text-generation",
    "voice-activity-detection": "voice-activity-detection",
}

# Models whose standard Diffusers pipeline and/or model card demonstrates a
# negative prompt.  Audio/TTS/ASR models are intentionally absent.
NEGATIVE_PROMPT = {
    "sd15-base", "sdxl-base", "sd3.5-large", "kolors", "hunyuanimage-3.0",
    "hidream-i1-fast", "controlnet-canny", "qwen-image", "qwen-image-2.1",
    "qwen-image-edit-2511", "minimax-h3-image", "z-image-turbo",
    "wan2.2-t2v", "wan2.2-i2v", "hunyuanvideo", "hunyuanvideo-1.5",
    "ltx-video", "ltx-2.3", "ltx-2.5", "opensora-2.0", "cogvideox-5b",
    "mochi-1", "lingbot-video", "pyramid-flow", "framepack-i2v",
}


def _tts_clone() -> dict:
    return {
        "tasks": ["text-to-speech", "voice-clone"],
        "inputs": {
            "text": {"required": True},
            "audio": {"required": False, "role": "reference"},
        },
        "controls": ["prompt", "audio", "voice", "speed", "format"],
    }


def _tts_plain() -> dict:
    return {
        "tasks": ["text-to-speech"],
        "inputs": {"text": {"required": True}},
        "controls": ["prompt", "voice", "speed", "format"],
    }


# Facts that cannot be read reliably from hub tags alone.  These are the
# explicit overrides used by the builder; everything else is inferred from
# pipeline_tag + repository tags.
OVERRIDES: dict[str, dict] = {
    # Video ---------------------------------------------------------------
    "wan2.2-t2v": {
        "tasks": ["text-to-video"],
        "inputs": {"text": {"required": True}},
    },
    "wan2.2-i2v": {
        "tasks": ["image-to-video"],
        "inputs": {
            "text": {"required": False},
            "image": {"required": True, "role": "first_frame", "max": 1},
        },
    },
    "wan2.2-s2v": {
        "tasks": ["image-to-video", "audio-to-video"],
        "inputs": {
            "image": {"required": True, "role": "character", "max": 1},
            "audio": {"required": True, "role": "driving"},
            "text": {"required": False},
        },
        "outputs": {"video": {"format": "mp4", "audio": True}},
    },
    "wan2.2-animate": {
        "tasks": ["image-to-video", "video-to-video"],
        "inputs": {
            "image": {"required": True, "role": "character", "max": 1},
            "video": {"required": True, "role": "driving"},
            "text": {"required": False},
        },
    },
    "wan2.2-animate2": {
        "tasks": ["image-to-video", "video-to-video"],
        "inputs": {
            "image": {"required": True, "role": "character", "max": 1},
            "video": {"required": True, "role": "driving"},
        },
    },
    "svd-xt": {
        "tasks": ["image-to-video"],
        "inputs": {"image": {"required": True, "role": "first_frame", "max": 1}},
        "controls": ["image", "duration", "fps", "motion", "format"],
    },
    "framepack-i2v": {
        "tasks": ["image-to-video"],
        "inputs": {
            "image": {"required": True, "role": "first_frame", "max": 1},
            "text": {"required": False},
        },
    },
    "pyramid-flow": {
        "tasks": ["text-to-video", "image-to-video"],
        "inputs": {
            "text": {"required": False},
            "image": {"required": False, "role": "first_frame", "max": 1},
        },
        "requires_one_of": ["text", "image"],
    },
    "hunyuanvideo": {
        "tasks": ["text-to-video", "image-to-video"],
        "inputs": {
            "text": {"required": True},
            "image": {"required": False, "role": "first_frame", "max": 1},
        },
    },
    "hunyuanvideo-1.5": {
        "tasks": ["text-to-video", "image-to-video"],
        "inputs": {
            "text": {"required": True},
            "image": {"required": False, "role": "first_frame", "max": 1},
        },
    },
    "ltx-2.3": {
        "tasks": ["text-to-video", "image-to-video", "video-to-video"],
        "inputs": {
            "text": {"required": True},
            "image": {"required": False, "role": "first_frame", "max": 1},
            "audio": {"required": False, "role": "reference"},
            "video": {"required": False, "role": "source"},
        },
        "outputs": {"video": {"format": "mp4", "audio": True}},
    },
    "ltx-2.5": {
        "tasks": ["text-to-video", "image-to-video"],
        "inputs": {
            "text": {"required": True},
            "image": {"required": False, "role": "first_frame", "max": 1},
        },
    },
    "minimax-h3-omni": {
        "tasks": ["text-to-video", "image-to-video"],
        "inputs": {
            "text": {"required": False},
            "image": {"required": False, "role": "reference", "max": 1},
            "audio": {"required": False, "role": "reference"},
            "video": {"required": False, "role": "reference"},
        },
        "outputs": {"video": {"format": "mp4", "audio": True}},
    },
    "minimax-h3": {
        "tasks": ["text-to-video", "image-to-video"],
        "inputs": {
            "text": {"required": True},
            "image": {"required": False, "role": "first_frame", "max": 1},
        },
    },
    "open-h3-regenerate-2k": {
        "tasks": ["video-to-video"],
        "inputs": {
            "video": {"required": True, "role": "source"},
            "text": {"required": False},
        },
    },
    # Image ---------------------------------------------------------------
    "controlnet-canny": {
        "tasks": ["image-to-image"],
        "inputs": {
            "text": {"required": True},
            "image": {"required": True, "role": "control", "max": 1},
        },
    },
    "qwen-image": {
        "tasks": ["text-to-image"],
        "inputs": {"text": {"required": True}},
    },
    "qwen-image-2.1": {
        "tasks": ["text-to-image"],
        "inputs": {"text": {"required": True}},
    },
    "qwen-image-2.1-viggle-turbo": {
        "tasks": ["text-to-image"],
        "inputs": {"text": {"required": True}},
    },
    "flux1-kontext-dev": {
        "tasks": ["image-to-image"],
        "inputs": {
            "text": {"required": True},
            "image": {"required": True, "role": "reference", "max": 1},
        },
    },
    "qwen-image-edit-2511": {
        "tasks": ["image-to-image"],
        "inputs": {
            "text": {"required": True},
            "image": {"required": True, "role": "reference", "max": 1},
        },
    },
    "krea-2-turbo": {
        "tasks": ["image-to-image"],
        "inputs": {
            "text": {"required": True},
            "image": {"required": True, "role": "reference", "max": 1},
        },
    },
    "minimax-h3-image": {
        "tasks": ["text-to-image"],
        "inputs": {
            "text": {"required": True},
            "image": {"required": False, "role": "reference", "max": 1},
        },
    },
    # 3D ------------------------------------------------------------------
    "shap-e": {
        "tasks": ["text-to-3d", "image-to-3d"],
        "inputs": {
            "text": {"required": False},
            "image": {"required": False, "role": "reference", "max": 1},
        },
        "requires_one_of": ["text", "image"],
    },
    "instant-mesh": {"tasks": ["image-to-3d"], "inputs": {"image": {"required": True, "role": "reference", "max": 1}}},
    "midi-3d": {"tasks": ["image-to-3d"], "inputs": {"image": {"required": True, "role": "reference", "max": 1}}},
    "hunyuan3d-2": {
        "tasks": ["image-to-3d", "text-to-3d"],
        "inputs": {
            "image": {"required": True, "role": "reference", "max": 1},
            "text": {"required": False},
        },
    },
    "trellis-image": {"tasks": ["image-to-3d"], "inputs": {"image": {"required": True, "role": "reference", "max": 1}}},
    "trellis-text-xlarge": {"tasks": ["text-to-3d"], "inputs": {"text": {"required": True}}},
    "stable-fast-3d": {"tasks": ["image-to-3d"], "inputs": {"image": {"required": True, "role": "reference", "max": 1}}},
    "apple-sharp": {"tasks": ["image-to-3d"], "inputs": {"image": {"required": True, "role": "reference", "max": 1}}},
    "triposr": {"tasks": ["image-to-3d"], "inputs": {"image": {"required": True, "role": "reference", "max": 1}}},
    "direct3d-s2": {"tasks": ["image-to-3d"], "inputs": {"image": {"required": True, "role": "reference", "max": 1}}},
    "partcrafter": {"tasks": ["image-to-3d"], "inputs": {"image": {"required": True, "role": "reference", "max": 1}}},
    "triposg": {"tasks": ["image-to-3d"], "inputs": {"image": {"required": True, "role": "reference", "max": 1}}},
    "sam-3d": {"tasks": ["image-to-3d"], "inputs": {"image": {"required": True, "role": "reference", "max": 1}}},
    "sam-3d-body": {"tasks": ["image-to-3d"], "inputs": {"image": {"required": True, "role": "reference", "max": 1}}},
    "pixal3d": {"tasks": ["image-to-3d"], "inputs": {"image": {"required": True, "role": "reference", "max": 1}}},
    "triposplat": {
        "tasks": ["image-to-3d"],
        "inputs": {"image": {"required": True, "role": "reference", "max": 1}},
        "outputs": {"3d": {"format": ["ply", "splat"]}},
    },
    "stable-spa3d": {
        "tasks": ["image-to-3d"],
        "inputs": {"image": {"required": True, "role": "reference", "max": 1}},
        "outputs": {"3d": {"format": ["ply"]}},
    },
    "4danyone": {
        "tasks": ["image-to-3d", "video-to-video"],
        "inputs": {
            "image": {"required": True, "role": "reference", "max": 1},
            "video": {"required": False, "role": "reference"},
        },
    },
    # Audio / music -------------------------------------------------------
    "musicgen-small": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "duration", "format"],
    },
    "musicgen-medium": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "duration", "format"],
    },
    "musicgen-large": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "duration", "format"],
    },
    "musicgen-stereo": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "duration", "format"],
    },
    "stable-audio-open": {
        "tasks": ["music-generation", "text-to-audio"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "duration", "steps", "cfg", "seed", "format"],
    },
    "stable-audio-3-medium": {
        "tasks": ["music-generation", "text-to-audio"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "duration", "steps", "cfg", "seed", "format"],
    },
    "audioldm2": {
        "tasks": ["text-to-audio"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "duration", "steps", "seed", "format"],
    },
    "diffrhythm": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "lyrics", "duration", "seed", "format"],
    },
    "ace-step-1.5": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "lyrics", "duration", "steps", "cfg", "seed", "format"],
    },
    "acestep-v15-base": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "lyrics", "duration", "steps", "cfg", "seed", "format"],
    },
    "minimax-music3": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "lyrics", "duration", "steps", "seed", "format"],
    },
    "minimax-music3-comfyui": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "lyrics", "duration", "steps", "seed", "format"],
    },
    "minimax-music3-gguf": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "lyrics", "duration", "steps", "seed", "format"],
    },
    "minimax-music3-turbo-fp8": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "lyrics", "duration", "steps", "seed", "format"],
    },
    "minimax-music3-w4a8-comfyui": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "lyrics", "duration", "steps", "seed", "format"],
    },
    "minimax-music3-lora-fiona-crapple": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "lyrics", "duration", "steps", "seed", "format"],
    },
    "minimax-music3-latent-refiner": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "lyrics", "duration", "steps", "seed", "format"],
    },
    "minimax-music3-mlx": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "lyrics", "duration", "steps", "seed", "format"],
    },
    "magenta-realtime-2": {
        "tasks": ["music-generation"],
        "inputs": {
            "text": {"required": True},
            "audio": {"required": False, "role": "reference"},
        },
        "controls": ["prompt", "audio", "duration", "format"],
    },
    "moss-soundeffect-2": {
        "tasks": ["sound-effects"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "duration", "steps", "seed", "format"],
    },
    "yue2-3b": {
        "tasks": ["music-generation"], "inputs": {"text": {"required": True}},
        "controls": ["prompt", "lyrics", "duration", "format"],
    },
    # Vision / OCR --------------------------------------------------------
    "sam3": {
        "tasks": ["segmentation"],
        "inputs": {"image": {"required": True, "role": "reference", "max": 1},
                   "video": {"required": False, "role": "reference"},
                   "text": {"required": False}},
        "outputs": {"mask": {}},
        "controls": ["image", "video", "text_prompt", "format"],
    },
    "clip-vit-l": {
        "tasks": ["image-classification"],
        "inputs": {"image": {"required": True, "role": "reference", "max": 1},
                   "text": {"required": False}},
        "outputs": {"labels": {}, "embedding": {}},
        "controls": ["image", "labels", "format"],
    },
    "pp-ocrv6-det": {
        "tasks": ["ocr", "text-detection"],
        "inputs": {"image": {"required": True, "role": "source", "max": 1}},
        "outputs": {"labels": {}},
        "controls": ["image", "format"],
    },
    "pp-ocrv6-rec": {
        "tasks": ["ocr", "text-recognition"],
        "inputs": {"image": {"required": True, "role": "source", "max": 1}},
        "outputs": {"text": {}},
        "controls": ["image", "format"],
    },
    "paddleocr-vl": {
        "tasks": ["ocr", "image-understanding"],
        "inputs": {"image": {"required": True, "role": "source", "max": 1},
                   "text": {"required": False}},
        "outputs": {"text": {}},
        "controls": ["image", "prompt", "format"],
    },
    "yolov10": {
        "tasks": ["detection"],
        "inputs": {"image": {"required": True, "role": "source", "max": 1}},
        "outputs": {"labels": {}},
        "controls": ["image", "format"],
    },
    # TTS / voice ---------------------------------------------------------
    "cosyvoice2": _tts_clone(), "cosyvoice3": _tts_clone(),
    "fish-speech-1.5": _tts_clone(), "f5-tts": _tts_clone(),
    "spark-tts": _tts_clone(), "indextts": _tts_clone(), "gpt-sovits": _tts_clone(),
    "chatterbox": _tts_clone(), "moss-tts-1.5": _tts_clone(),
    "dots-tts-soar": _tts_clone(), "auk": _tts_clone(), "indextts-2.5": _tts_clone(),
    "fish-speech-s2-pro": _tts_clone(), "voxcpm2": _tts_clone(),
    "qwen3-tts-1.7b-customvoice": _tts_clone(), "orpheus-3b": _tts_clone(),
    "breeze-tts-2": _tts_clone(), "kokoro-82m": _tts_plain(),
    "piper-en-lessac": _tts_plain(), "piper-en-amy": _tts_plain(), "piper-zh-huayan": _tts_plain(),
}

CATEGORY_DEFAULT = {
    "llm": "text-generation",
    "image": "text-to-image",
    "video": "text-to-video",
    "audio": "text-to-audio",
    "3d": "image-to-3d",
    "tts": "text-to-speech",
    "vision": "image-understanding",
    "embedding": "embedding",
    "superres": "upscale",
}


def _lower_tags(m: dict, hf: dict | None) -> set[str]:
    tags = set()
    for t in (m.get("tags") or []):
        tags.add(str(t).lower())
    for t in ((hf or {}).get("tags") or []):
        tags.add(str(t).lower())
    return tags


def _tasks_from_hf(m: dict, hf: dict | None, tags: set[str]) -> list[str]:
    tasks: list[str] = []
    pipeline = (hf or {}).get("pipeline_tag")
    mapped = PIPELINE_TASKS.get(pipeline or "")
    if mapped:
        tasks.append(mapped)

    def add(x: str) -> None:
        if x not in tasks:
            tasks.append(x)

    name = m["id"].lower()
    if any(x in tags for x in ("text-to-video", "text2video", "t2v")):
        add("text-to-video")
    if any(x in tags for x in ("image-to-video", "img2video", "i2v")):
        add("image-to-video")
    if "s2v" in tags or "s2v" in name:
        add("image-to-video")
        add("audio-to-video")
    if any(x in tags for x in ("video-to-video", "v2v")):
        add("video-to-video")
    if any(x in tags for x in ("text to image", "text-to-image", "t2i")):
        add("text-to-image")
    if any(x in tags for x in ("image editing", "image-to-image", "inpainting", "img2img")):
        add("image-to-image")
    if any(x in tags for x in ("tts", "text to speech", "text-to-speech")):
        add("text-to-speech")
    if any(x in tags for x in ("voice clone", "voice cloning", "clone")):
        add("voice-clone")
    if any(x in tags for x in ("asr", "speech recognition", "speech-to-text")):
        add("asr")
    if "diarization" in tags:
        add("diarization")
    if "separation" in tags or "source separation" in tags:
        add("source-separation")
    if any(x in tags for x in ("sfx", "sound effect", "sound-effect")):
        add("sound-effects")
    if any(x in tags for x in ("music", "song", "text to music", "music generation")):
        if "asr" not in tasks and "text-to-speech" not in tasks:
            add("music-generation")
    if "ocr" in tags:
        add("ocr")
    if "segmentation" in tags:
        add("segmentation")
    if "detection" in tags:
        add("detection")
    if "embedding" in tags:
        add("embedding")
    if any(x in tags for x in ("upscale", "super resolution", "super-resolution")):
        add("upscale")
    if m.get("category") == "3d" or "3d" in tags:
        if any(x in tags for x in ("text-to-3d", "text to 3d", "text3d")):
            add("text-to-3d")
        else:
            add("image-to-3d")
    if m.get("category") == "vision" and any(x in tags for x in ("vision", "image", "video")):
        add("image-understanding")
    return tasks


def _infer_tasks(m: dict, hf: dict | None, tags: set[str]) -> list[str]:
    tasks = _tasks_from_hf(m, hf, tags)
    if not tasks:
        tasks = [CATEGORY_DEFAULT.get(m.get("category"), "text-generation")]
    return list(dict.fromkeys(tasks))


def _default_outputs(tasks: list[str], m: dict, hf: dict | None, tags: set[str]) -> dict:
    outs: dict = {}
    for task in tasks:
        if task in ("text-to-image", "image-to-image", "upscale", "controlnet"):
            outs.setdefault("image", {"format": "png"})
        if task in ("text-to-video", "image-to-video", "video-to-video"):
            outs["video"] = {"format": "mp4", "audio": False}
        if task in ("text-to-audio", "audio-to-audio", "music-generation", "sound-effects", "text-to-speech", "voice-clone", "source-separation"):
            outs["audio"] = {"format": "wav"}
        if task in ("text-to-3d", "image-to-3d"):
            outs["3d"] = {"format": "glb"}
        if task in ("text-generation", "asr", "diarization", "image-captioning", "ocr", "image-understanding", "audio-understanding", "video-understanding", "text-classification", "voice-activity-detection"):
            outs["text"] = {}
        if task == "embedding":
            outs["embedding"] = {}
        if task == "segmentation":
            outs["mask"] = {}
        if task in ("detection", "image-classification"):
            outs["labels"] = {}
    # A generic audio/video model must at least have an output.
    if not outs:
        cat = m.get("category")
        if cat == "image":
            outs["image"] = {"format": "png"}
        elif cat == "video":
            outs["video"] = {"format": "mp4", "audio": False}
        elif cat in ("audio", "tts"):
            outs["audio"] = {"format": "wav"}
        elif cat == "3d":
            outs["3d"] = {"format": "glb"}
        else:
            outs["text"] = {}
    # video audio flag from tags / known audio-video models
    if "video" in outs:
        tag_text = " ".join(sorted(tags))
        if any(x in tag_text for x in ("text-to-audio-video", "image-to-audio-video", "audio-to-video", "音画同步", "lip sync", "lipsync")):
            outs["video"]["audio"] = True
    return outs


def _default_inputs(m: dict, tasks: list[str], hf: dict | None, tags: set[str]) -> dict:
    inputs: dict = {}
    name = m["id"].lower()

    if any(x in tasks for x in ("text-to-image", "image-to-image", "text-to-video", "text-to-audio", "music-generation", "sound-effects", "text-to-speech", "voice-clone", "text-to-3d", "text-generation", "text-classification", "embedding", "image-captioning")):
        inputs["text"] = {"required": True}
    if any(x in tasks for x in ("image-to-image", "image-to-video", "image-to-3d", "upscale", "segmentation", "detection", "ocr", "image-classification", "controlnet")):
        inputs["image"] = {"required": True, "role": "reference", "max": 1}
    if any(x in tasks for x in ("audio-to-audio", "audio-to-video", "asr", "diarization", "source-separation", "voice-clone")):
        inputs["audio"] = {"required": True, "role": "reference"}
    if any(x in tasks for x in ("video-to-video",)):
        inputs["video"] = {"required": True, "role": "source"}

    # Optional text for image/video/3D conditioning models.
    if "text" not in inputs and any(x in tasks for x in ("image-to-video", "image-to-3d", "video-to-video", "audio-to-video")):
        inputs["text"] = {"required": False}

    # Multimodal LLMs / VLMs / audio LMs.
    modality = m.get("modality") or {}
    understand = set(modality.get("understand") or [])
    if "image" in understand and "image" not in inputs:
        inputs["image"] = {"required": False, "role": "reference", "max": 4}
    if "audio" in understand and "audio" not in inputs:
        inputs["audio"] = {"required": False, "role": "reference"}
    if "video" in understand and "video" not in inputs:
        inputs["video"] = {"required": False, "role": "reference"}
    if "text" not in inputs and m.get("category") == "llm":
        inputs["text"] = {"required": True}
    if "text" not in inputs and m.get("category") == "vision":
        inputs["text"] = {"required": False}

    # Video conditioning models.
    if name in ("wan2.2-animate", "wan2.2-animate2", "4danyone"):
        inputs.setdefault("image", {"required": True, "role": "character", "max": 1})
        inputs.setdefault("video", {"required": True, "role": "driving"})
    if name == "wan2.2-s2v":
        inputs["image"] = {"required": True, "role": "character", "max": 1}
        inputs["audio"] = {"required": True, "role": "driving"}
        inputs["text"] = {"required": False}
    if name == "svd-xt":
        inputs = {"image": {"required": True, "role": "first_frame", "max": 1}}
    if name == "framepack-i2v":
        inputs = {"image": {"required": True, "role": "first_frame", "max": 1}, "text": {"required": False}}

    # TTS voice cloning: audio is optional reference, not required input.
    if "voice-clone" in tasks:
        inputs["audio"] = {"required": False, "role": "reference"}

    # Video-with-audio models.
    if name in ("ltx-2.3", "minimax-h3-omni"):
        inputs.setdefault("image", {"required": False, "role": "first_frame", "max": 1})
        inputs.setdefault("text", {"required": True})
        inputs.setdefault("audio", {"required": False, "role": "reference"})
        inputs.setdefault("video", {"required": False, "role": "reference"})
    if name == "wan2.2-s2v" or name == "minimax-h3-omni":
        pass

    return inputs


def _default_controls(m: dict, tasks: list[str], inputs: dict, outputs: dict, tags: set[str]) -> list[str]:
    controls: list[str] = []
    if "text" in inputs:
        controls.append("prompt")
        if m["id"] in NEGATIVE_PROMPT:
            controls.append("negative_prompt")
    if "image" in inputs:
        controls.append("image")
    if "audio" in inputs:
        controls.append("audio")
    if "video" in inputs:
        controls.append("video")
    if "video" in outputs:
        controls.extend(["duration", "resolution", "fps", "format"])
    if "image" in outputs:
        controls.extend(["resolution", "format"])
    if "audio" in outputs:
        controls.extend(["duration", "format"])
    if "3d" in outputs:
        controls.append("format")
    if "video" in outputs or "image" in outputs or "3d" in outputs:
        if m.get("category") in ("image", "video", "3d"):
            controls.extend(["steps", "cfg", "seed"])
    if "audio" in outputs and m.get("category") == "audio":
        controls.append("seed")
    if "image" in outputs:
        controls.append("batch")
    if "text-to-speech" in tasks or "voice-clone" in tasks:
        controls.extend(["voice", "speed"])
    if "asr" in tasks:
        controls.append("language")
    if "music-generation" in tasks:
        if any(x in tags for x in ("lyrics", "song", "text2music")):
            controls.append("lyrics")
        controls.append("seed")
    # De-duplicate, preserve order.
    return list(dict.fromkeys(controls))


def build_one(m: dict, hf: dict | None) -> dict:
    tags = _lower_tags(m, hf)
    override = OVERRIDES.get(m["id"], {})

    tasks = list(override.get("tasks") or _infer_tasks(m, hf, tags))
    inputs = override.get("inputs") or _default_inputs(m, tasks, hf, tags)
    outputs = override.get("outputs") or _default_outputs(tasks, m, hf, tags)
    if "video" in outputs and m["id"] == "ltx-2.3":
        outputs["video"]["audio"] = True
    if "video" in outputs and m["id"] == "minimax-h3-omni":
        outputs["video"]["audio"] = True
    if "video" in outputs and m["id"] == "wan2.2-s2v":
        outputs["video"]["audio"] = True

    controls = list(override.get("controls") or _default_controls(m, tasks, inputs, outputs, tags))
    controls = list(dict.fromkeys(controls))

    source = "catalog-tags"
    confidence = "inferred"
    if hf and hf.get("pipeline_tag"):
        source = f"huggingface:{hf.get('repo')} (pipeline_tag={hf['pipeline_tag']})"
        confidence = "official-pipeline"
    readme = (READMES.get(m["id"]) or "").lower()
    if "negative_prompt" in readme or "negative prompt" in readme:
        if "negative_prompt" not in controls and "prompt" in controls:
            controls.append("negative_prompt")
        confidence = "official-readme"
    if m["id"] in NEGATIVE_PROMPT and "prompt" in controls:
        if "negative_prompt" not in controls:
            controls.append("negative_prompt")
        confidence = "official-pipeline+standard-cfg"
    if override:
        confidence = "curated-override" if confidence == "inferred" else confidence

    cap = {
        "tasks": tasks,
        "inputs": inputs,
        "outputs": outputs,
        "controls": controls,
        "source": source,
        "confidence": confidence,
    }
    if override.get("requires_one_of"):
        cap["requires_one_of"] = override["requires_one_of"]
    # Sort input keys deterministically.
    cap["inputs"] = {k: cap["inputs"][k] for k in ("text", "image", "audio", "video") if k in cap["inputs"]}
    return cap


def main() -> int:
    global READMES
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    hf = json.loads(HF_CACHE.read_text(encoding="utf-8")) if HF_CACHE.exists() else {}
    READMES = json.loads(README_CACHE.read_text(encoding="utf-8")) if README_CACHE.exists() else {}
    count = 0
    for m in data["models"]:
        m["capability"] = build_one(m, hf.get(m["id"]))
        count += 1
    data["capability_schema"] = {
        "version": 1,
        "updated": "2026-11-14",
        "description": "每个模型独立声明任务、输入、输出和支持的控制项；前端按此渲染表单，不再按类目共用一套字段。",
    }
    CATALOG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote capability for {count} models")
    return 0


if __name__ == "__main__":
    sys.exit(main())
