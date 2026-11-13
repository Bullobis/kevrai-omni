"""Adapters for the catalog's specialized models.

These models do not fit a single generic pipeline:

* MOSS / Dots / VoxCPM / Qwen3-TTS each ship their own inference package;
* Real-ESRGAN / 4x-UltraSharp are RRDB ``.pth`` upscalers;
* InsightFace / YOLOv10 / PP-OCR are ONNX vision pipelines;
* pyannote / NeMo are diarization pipelines;
* GLiNER / Laya are decision heads, not causal LMs.

Every adapter is lazy: importing this module never imports torch, onnxruntime,
insightface, nemo or any TTS package. A caller gets a clear
``SpecialEngineMissing`` with the exact install hint instead of an
ImportError from deep inside a model load.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

from .media_runtime import MediaEngineMissing, _write_audio


class SpecialError(RuntimeError):
    """Base error for specialized adapters."""


class SpecialEngineMissing(SpecialError):
    """The optional upstream package is not installed."""


class SpecialModelError(SpecialError):
    """The model exists but could not be loaded/run."""


class SpecialParamError(SpecialError):
    """Invalid request values."""


def _require(module: str, hint: str) -> Any:
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as e:  # noqa: BLE001
        raise SpecialEngineMissing(f"缺少 {module}：{hint}") from e


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

_TTS_HINTS = {
    "moss": "pip install -e git+https://github.com/OpenMOSS/MOSS-TTS.git",
    "dots": "pip install git+https://github.com/studio-dots-ai/dots.tts.git",
    "voxcpm": "pip install voxcpm",
    "qwen3": "pip install qwen-tts",
}


def _moss_tts(source: str, text: str) -> tuple[Any, int]:
    tf = _require("transformers", "pip install transformers")
    torch = _require("torch", "pip install torch")
    processor = tf.AutoProcessor.from_pretrained(source, trust_remote_code=True)
    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    model = tf.AutoModel.from_pretrained(
        source, trust_remote_code=True, torch_dtype=dtype,
    ).to("cuda" if torch.cuda.is_available() else "cpu")
    model.eval()
    batch = processor(
        [processor.build_user_message(text=text)], mode="generation"
    )
    with torch.no_grad():
        out = model.generate(
            input_ids=batch["input_ids"].to(model.device),
            attention_mask=batch["attention_mask"].to(model.device),
            max_new_tokens=4096,
        )
    message = next(iter(processor.decode(out)))
    audio = message.audio_codes_list[0]
    return audio, int(processor.model_config.sampling_rate)


def _dots_tts(source: str, text: str, ref_audio: str,
              ref_text: str = "") -> tuple[Any, int]:
    dots = _require("dots_tts", _TTS_HINTS["dots"])
    if not ref_audio:
        raise SpecialParamError(
            "dots.tts-soar 是零样本声音克隆模型：必须提供参考音频 reference_audio"
        )
    runtime = dots.DotsTtsRuntime.from_pretrained(source, precision="bfloat16")
    result = runtime.generate(
        text=text, prompt_audio_path=ref_audio,
        prompt_text=ref_text, num_steps=10, guidance_scale=1.2,
    )
    return result["audio"], int(result["sample_rate"])


def _voxcpm_tts(source: str, text: str) -> tuple[Any, int]:
    voxcpm = _require("voxcpm", _TTS_HINTS["voxcpm"])
    model = voxcpm.VoxCPM.from_pretrained(source, load_denoiser=False)
    wav = model.generate(text=text, cfg_value=2.0, inference_timesteps=10)
    return wav, int(model.tts_model.sample_rate)


def _qwen3_tts(source: str, text: str, speaker: str, language: str,
               instruct: str) -> tuple[Any, int]:
    qwen = _require("qwen_tts", _TTS_HINTS["qwen3"])
    torch = _require("torch", "pip install torch")
    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    model = qwen.Qwen3TTSModel.from_pretrained(
        source, device_map="auto" if torch.cuda.is_available() else "cpu",
        dtype=dtype,
    )
    kwargs: dict[str, Any] = {
        "text": text,
        "language": language or "Auto",
    }
    if not speaker and "customvoice" in source.lower():
        speaker = "Vivian"
    if speaker:
        kwargs["speaker"] = speaker
    if instruct:
        kwargs["instruct"] = instruct
    wavs, sr = model.generate_custom_voice(**kwargs)
    return wavs[0], int(sr)


def _pipeline_tts(source: str, text: str) -> tuple[Any, int]:
    tf = _require("transformers", "pip install transformers")
    try:
        pipe = tf.pipeline("text-to-speech", model=source)
        out = pipe(text)
        return out["audio"], int(out.get("sampling_rate") or 16000)
    except Exception as e:  # noqa: BLE001
        raise SpecialModelError(
            f"该模型没有标准 text-to-speech 管线（需要官方推理包）：{e}"
        ) from e


def synthesize(
    model: str, text: str, *, output_dir: Path,
    speaker: str = "", language: str = "", instruct: str = "",
    reference_audio: str = "", reference_text: str = "",
) -> dict[str, Any]:
    """Synthesize text with the model's own runtime and return a WAV file."""
    text = (text or "").strip()
    if not text:
        raise SpecialParamError("text 不能为空")
    source = str(model or "").strip()
    if not source:
        raise SpecialParamError("model 不能为空")
    lower = source.lower()
    started = time.monotonic()
    if "moss" in lower:
        samples, rate = _moss_tts(source, text)
    elif "dots" in lower:
        samples, rate = _dots_tts(source, text, reference_audio, reference_text)
    elif "voxcpm" in lower:
        samples, rate = _voxcpm_tts(source, text)
    elif "qwen3-tts" in lower:
        samples, rate = _qwen3_tts(source, text, speaker, language, instruct)
    else:
        # Orpheus / Breeze and any standard Transformers TTS checkpoint.
        samples, rate = _pipeline_tts(source, text)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"tts-{int(time.time())}.wav"
    try:
        _write_audio(samples, rate, path)
    except MediaEngineMissing as e:
        raise SpecialEngineMissing(str(e)) from e
    return {
        "ok": True, "engine": "tts", "output_path": str(path),
        "sampling_rate": rate, "elapsed_s": round(time.monotonic() - started, 2),
    }


# ---------------------------------------------------------------------------
# Super resolution (Real-ESRGAN / 4x-UltraSharp)
# ---------------------------------------------------------------------------


def _find_pth(model_path: str) -> Path:
    p = Path(model_path).expanduser()
    if p.is_file():
        return p
    if p.is_dir():
        weights = sorted(p.rglob("*.pth"))
        if weights:
            return weights[0]
    raise SpecialParamError(
        f"{model_path} 下没有 .pth 权重；请先下载 Real-ESRGAN / 4x-UltraSharp"
    )


def upscale(model_path: str, image: bytes, *, outscale: int = 4) -> dict[str, Any]:
    """Upscale an image with a RRDB checkpoint and return a PNG data URL."""
    if not image:
        raise SpecialParamError("image content is empty")
    try:
        import cv2
        import numpy as np
        import torch
        from basicsr.archs.rrdbnet_arch import RRDBNet
        from realesrgan import RealESRGANer
    except ImportError as e:  # noqa: BLE001
        raise SpecialEngineMissing(
            "超分需要 realesrgan + basicsr + opencv-python："
            "pip install realesrgan basicsr opencv-python"
        ) from e
    weights = _find_pth(model_path)
    scale = 4 if "4x" in weights.name.lower() or "x4" in weights.name.lower() else 2
    network = RRDBNet(
        num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23,
        num_grow_ch=32, scale=scale,
    )
    upsampler = RealESRGANer(
        scale=scale, model_path=str(weights), model=network,
        tile=0, tile_pad=10, pre_pad=0, half=bool(torch.cuda.is_available()),
    )
    arr = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise SpecialParamError("无法解码图片")
    out, _ = upsampler.enhance(arr, outscale=max(scale, int(outscale)))
    ok, png = cv2.imencode(".png", out)
    if not ok:
        raise SpecialModelError("PNG 编码失败")
    return {
        "ok": True, "engine": "realesrgan",
        "scale": max(scale, int(outscale)),
        "width": int(out.shape[1]), "height": int(out.shape[0]),
        "data_url": "data:image/png;base64," + base64.b64encode(png.tobytes()).decode(),
    }


# ---------------------------------------------------------------------------
# Vision: InsightFace / YOLOv10
# ---------------------------------------------------------------------------


def faces(model_dir: str, image: bytes) -> dict[str, Any]:
    """Run InsightFace buffalo_l and return detections/embeddings."""
    if not image:
        raise SpecialParamError("image content is empty")
    insightface = _require(
        "insightface", "pip install insightface onnxruntime"
    )
    import cv2
    import numpy as np
    root = Path(model_dir).expanduser()
    if not root.is_dir():
        raise SpecialParamError(f"InsightFace 模型目录不存在：{model_dir}")
    # FaceAnalysis accepts a direct model directory (Path.is_dir() branch),
    # which is exactly the layout of a downloaded HF snapshot.
    app = insightface.app.FaceAnalysis(
        name=str(root), providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=-1, det_size=(640, 640))
    arr = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise SpecialParamError("无法解码图片")
    results = []
    for f in app.get(arr):
        results.append({
            "bbox": [round(float(x), 1) for x in f.bbox],
            "det_score": round(float(f.det_score), 4),
            "gender": int(f.gender) if getattr(f, "gender", None) is not None else None,
            "age": int(f.age) if getattr(f, "age", None) is not None else None,
        })
    return {"ok": True, "engine": "insightface", "count": len(results), "faces": results}


def detect(model_path: str, image: bytes, *, conf: float = 0.25) -> dict[str, Any]:
    """Run a YOLOv10 ONNX/PyTorch checkpoint through Ultralytics."""
    if not image:
        raise SpecialParamError("image content is empty")
    try:
        from ultralytics import YOLO
    except ImportError as e:  # noqa: BLE001
        raise SpecialEngineMissing(
            "YOLOv10 需要 ultralytics：pip install ultralytics onnxruntime"
        ) from e
    path = Path(model_path).expanduser()
    if path.is_dir():
        candidates = sorted(path.rglob("*.onnx")) + sorted(path.rglob("*.pt"))
        if not candidates:
            raise SpecialParamError(f"YOLO 目录中没有 .onnx/.pt 权重：{model_path}")
        path = candidates[0]
    if not path.exists():
        raise SpecialParamError(f"YOLO 权重不存在：{model_path}")
    import cv2
    import numpy as np
    arr = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise SpecialParamError("无法解码图片")
    model = YOLO(str(path))
    results = model.predict(arr, conf=max(0.0, min(float(conf), 1.0)), verbose=False)
    boxes = []
    for r in results:
        for b in r.boxes:
            boxes.append({
                "class": int(b.cls[0]),
                "conf": round(float(b.conf[0]), 4),
                "xyxy": [round(float(x), 1) for x in b.xyxy[0].tolist()],
            })
    return {"ok": True, "engine": "yolov10", "count": len(boxes), "boxes": boxes}


# ---------------------------------------------------------------------------
# Audio: Qwen2-Audio / diarization
# ---------------------------------------------------------------------------


def audio_chat(model: str, audio_path: str, question: str) -> dict[str, Any]:
    """Ask a Qwen2-Audio model about a local audio file."""
    if not audio_path:
        raise SpecialParamError("audio_path 不能为空")
    question = (question or "").strip() or "请描述这段音频。"
    tf = _require("transformers", "pip install transformers")
    torch = _require("torch", "pip install torch")
    processor = tf.AutoProcessor.from_pretrained(model)
    model_obj = tf.Qwen2AudioForConditionalGeneration.from_pretrained(
        model, torch_dtype="auto", device_map="auto",
    )
    conversation = [{"role": "user", "content": [
        {"type": "audio", "audio_url": audio_path},
        {"type": "text", "text": question},
    ]}]
    text = processor.apply_chat_template(
        conversation, add_generation_prompt=True, tokenize=False
    )
    import numpy as np
    sf = _require("soundfile", "pip install soundfile")
    wave, sample_rate = sf.read(audio_path, dtype="float32")
    target = int(processor.feature_extractor.sampling_rate)
    if sample_rate != target and len(wave):
        # Linear resampling keeps this adapter dependency-free; Qwen2-Audio
        # only needs 16 kHz mono input.
        idx = np.linspace(0, len(wave) - 1, int(len(wave) * target / sample_rate))
        wave = np.interp(idx, np.arange(len(wave)), wave).astype(np.float32)
    audios = [wave]
    inputs = processor(text=text, audios=audios, return_tensors="pt")
    inputs = {k: v.to(model_obj.device) for k, v in inputs.items()}
    with torch.no_grad():
        out = model_obj.generate(**inputs, max_new_tokens=256)
    answer = processor.batch_decode(
        out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True
    )[0]
    return {"ok": True, "engine": "qwen2-audio", "answer": answer.strip()}


def diarize(model: str, audio_path: str) -> dict[str, Any]:
    """Run pyannote or NeMo diarization and return speaker turns."""
    if not audio_path:
        raise SpecialParamError("audio_path 不能为空")
    lower = str(model).lower()
    if "nemotron" in lower:
        try:
            from nemo.collections.asr.models import SortformerEncLabelModel
        except ImportError as e:  # noqa: BLE001
            raise SpecialEngineMissing(
                "Nemotron 分轨需要 NeMo：pip install 'nemo-toolkit[asr]'"
            ) from e
        diar = SortformerEncLabelModel.from_pretrained(model)
        diar.eval()
        labels = diar.diarize(audio=[audio_path], batch_size=1)
        turns = []
        for item in (labels[0] if labels else []):
            if isinstance(item, dict):
                start = item.get("start", item.get("Start", 0))
                end = item.get("end", item.get("End", 0))
                speaker = item.get("speaker", item.get("Speaker", item.get("label", "")))
            elif isinstance(item, (list, tuple)) and len(item) >= 3:
                speaker, start, end = item[0], item[1], item[2]
            else:
                parts = str(item).replace("[", "").replace("]", "").split()
                if len(parts) < 3:
                    continue
                speaker, start, end = parts[-1], parts[0], parts[1]
            turns.append({
                "start": float(start) if start is not None else 0.0,
                "end": float(end) if end is not None else 0.0,
                "speaker": str(speaker),
            })
        return {"ok": True, "engine": "nemotron", "turns": turns}

    try:
        from pyannote.audio import Pipeline
    except ImportError as e:  # noqa: BLE001
        raise SpecialEngineMissing(
            "pyannote 分轨需要 pyannote.audio：pip install pyannote.audio"
        ) from e
    pipeline = Pipeline.from_pretrained(model)
    annotation = pipeline(audio_path)
    turns = [
        {"start": round(float(seg.start), 3), "end": round(float(seg.end), 3),
         "speaker": str(label)}
        for seg, _track, label in annotation.itertracks(yield_label=True)
    ]
    return {"ok": True, "engine": "pyannote", "turns": turns}


# ---------------------------------------------------------------------------
# Decision models: GLiNER / Laya
# ---------------------------------------------------------------------------


def decide(model: str, text: str, labels: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Run a GLiNER decision head over arbitrary labels."""
    text = (text or "").strip()
    if not text:
        raise SpecialParamError("text 不能为空")
    if not labels:
        raise SpecialParamError("labels 不能为空，例如 {'intent': ['refund', 'other']}")
    try:
        from gliner2 import AutoExtractor
    except ImportError as e:  # noqa: BLE001
        raise SpecialEngineMissing(
            "GLiNER2 需要 gliner2：pip install 'gliner2[local]'"
        ) from e
    extractor = AutoExtractor.from_pretrained(model)
    result = extractor.classify_text(text, labels)
    return {"ok": True, "engine": "gliner2", "result": result}


def laya_decide(model: str, text: str,
                questions: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run Laya's calibrated decision router."""
    text = (text or "").strip()
    if not text:
        raise SpecialParamError("text 不能为空")
    if not questions:
        raise SpecialParamError("questions 不能为空")
    try:
        from laya import Router
    except ImportError as e:  # noqa: BLE001
        raise SpecialEngineMissing("Laya 需要 laya：pip install laya") from e
    router = Router()
    result = router.predict(text, questions, model=model)
    return {"ok": True, "engine": "laya", "result": result}
