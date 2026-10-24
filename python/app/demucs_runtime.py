"""Music source-separation runtime via Demucs (Meta, hybrid transformer).

Follows the dedicated-runtime pattern of :mod:`asr_runtime` /
:mod:`embedding_runtime`:

* locate the pip-installed ``demucs`` engine target and put it on ``sys.path``
  (engines are installed with ``pip install --target``);
* build a cached ``demucs.api.Separator`` for the chosen pretrained model;
* separate an uploaded/local track into stems, write WAV files under a per-job
  output directory and return a manifest. The stems are streamed back to the
  renderer through the ``/api/separation/stream`` route (FileResponse).

The module never imports demucs/torch at import time, so the control plane
starts even before the engine is installed.
"""
from __future__ import annotations

import logging
import threading
import time
import wave
from pathlib import Path
from typing import Any

from .settings import default_data_root

log = logging.getLogger("kevrai.demucs")

ENGINE_ID = "demucs"
_PIP_DIR = "pip-demucs"

# Pretrained models shipped in the catalog (see demucs/remote/*.yaml).
KNOWN_MODELS = ("htdemucs", "htdemucs_ft", "htdemucs_6s")
# Stem names produced by the 4-source models; htdemucs_6s adds piano + guitar.
FOUR_STEMS = ("drums", "bass", "other", "vocals")

# ---------------------------------------------------------------------------
# RoFormer 系列（BS-RoFormer / Mel-Band RoFormer）—— 人声分离质量显著更高
# ---------------------------------------------------------------------------
# 为什么不能只留 Demucs：人声隔离这一项上 RoFormer 系列比 htdemucs_ft 高约
# 4-5 dB SDR（公开评测：BS-RoFormer 12.9、Mel-Band RoFormer 13.3，
# htdemucs_ft 8.3）。代价是 RoFormer 的人声模型只出 2 轨（人声/伴奏），
# 做不出鼓/贝斯分轨 —— 所以正确用法是**两者并存**，而不是替换：
#   要 4 轨分轨      -> Demucs
#   要最干净的人声   -> RoFormer
ROFORMER_ENGINE_ID = "audio-separator"
_ROFORMER_PIP_DIR = "pip-audio-separator"
#: audio-separator 的 PyPI 包名（含 GPU 额外依赖）。
ROFORMER_PIP_NAME = "audio-separator[gpu]"

#: 模型 id -> 事实。只登记**核实过文件名**的模型；未核实的一律不写死文件名，
#: 因为写错文件名的后果是用户点了之后下载失败，而报错完全指不到原因。
ROFORMER_MODELS: dict[str, dict[str, Any]] = {
    "bs-roformer-vocals": {
        "label": "BS-RoFormer · 人声（viperx）",
        "filename": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "kind": "separate",
        "stems": ["vocals", "instrumental"],
        "sdr_vocals": 12.97,
        "size_mb": 396,
        "license": "MIT",
        "best_for": "人声最干净，SDR 最高；仅出人声/伴奏 2 轨。",
    },
    "melband-roformer-karaoke": {
        "label": "Mel-Band RoFormer · 卡拉OK（aufr33+viperx）",
        "filename": "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
        "kind": "separate",
        "stems": ["lead_vocals", "karaoke"],
        "sdr_vocals": 10.20,
        "size_mb": 913,
        "license": "MIT",
        "best_for": "只抠主唱，保留和声与伴奏；做伴奏带最合适。",
    },
    "melband-roformer-dereverb": {
        "label": "Mel-Band RoFormer · 去混响（anvuew）",
        "filename": "dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt",
        "kind": "enhance",
        "stems": ["dereverb"],
        "sdr_vocals": 19.17,
        "size_mb": None,
        "license": "MIT",
        "best_for": "去掉房间混响，不是分轨；用于录音修复。",
    },
    "mdx-inst-hq3": {
        "label": "MDX-Net · Inst HQ3（快速）",
        "filename": "UVR-MDX-NET-Inst_HQ_3.onnx",
        "kind": "separate",
        "stems": ["vocals", "instrumental"],
        "sdr_vocals": None,
        "size_mb": 64,
        "license": "unknown",
        "best_for": "又快又均衡，低配机器首选；质量不如 RoFormer。",
    },
    "kim-inst": {
        "label": "Kim Inst（最快）",
        "filename": "Kim_Inst.onnx",
        "kind": "separate",
        "stems": ["vocals", "instrumental"],
        "sdr_vocals": None,
        "size_mb": 67,
        "license": "unknown",
        "best_for": "体积最小、速度最快；适合批量与实时预览。",
    },
}

#: 许可未登记时的统一说明。绝不默认成"可商用"。
_UNKNOWN_LICENSE_NOTE = (
    "该模型许可未登记。python-audio-separator 引擎本身是 MIT，"
    "但各模型许可由原作者分别声明，商用前请自行核对模型卡。"
)

#: 与 Demucs 的关键差异，UI 必须展示，否则用户会用错模型。
ROFORMER_NOTES = (
    "RoFormer 系列在人声隔离上比 Demucs 高约 4-5 dB SDR，"
    "但人声模型只出「人声/伴奏」2 轨，做不出鼓/贝斯分轨。"
    "要 4 轨请选 Demucs，要最干净的人声请选 RoFormer —— 两者是互补而非替代。"
)


class DemucsError(Exception):
    """Base error for the separation runtime."""


class DemucsEngineMissing(DemucsError):
    """Raised when the demucs engine is not installed/loadable."""


class DemucsModelError(DemucsError):
    """Raised when a model cannot load or separation fails."""


class DemucsParamError(DemucsError):
    """Raised for invalid separation parameters."""


def engine_target_dir(data_root: Path | None = None) -> Path:
    """Directory the engine is installed into (``pip --target``)."""
    root = Path(data_root) if data_root is not None else default_data_root()
    return root / "engines" / _PIP_DIR


def roformer_engine_dir(data_root: Path | None = None) -> Path:
    """Directory the audio-separator engine is installed into."""
    root = Path(data_root) if data_root is not None else default_data_root()
    return root / "engines" / _ROFORMER_PIP_DIR


def is_roformer_model(model: str) -> bool:
    """判断模型 id 是否走 audio-separator 引擎。"""
    return (model or "").strip() in ROFORMER_MODELS


def all_models() -> list[dict[str, Any]]:
    """Demucs 与 RoFormer 合并后的可选模型清单（含引擎归属）。"""
    out: list[dict[str, Any]] = []
    for m in KNOWN_MODELS:
        out.append({
            "id": m,
            "engine": ENGINE_ID,
            "kind": "separate",
            "stems": list(FOUR_STEMS) + (["piano", "guitar"] if m == "htdemucs_6s" else []),
            "label": f"Demucs · {m}",
            "best_for": "4 轨（鼓/贝斯/其他/人声）分轨。",
        })
    for mid, spec in ROFORMER_MODELS.items():
        out.append({
            "id": mid,
            "engine": ROFORMER_ENGINE_ID,
            "kind": spec["kind"],
            "stems": list(spec["stems"]),
            "label": spec["label"],
            "best_for": spec["best_for"],
            "sdr_vocals": spec["sdr_vocals"],
            "size_mb": spec["size_mb"],
            "license": spec["license"],
        })
    return out


def import_audio_separator(data_root: Path | None = None) -> Any:
    """导入 ``audio_separator.separator.Separator``。

    与 Demucs 是**两套独立引擎**：Demucs 自带 4 轨分轨，
    audio-separator 走 UVR 系列模型（RoFormer / MDX / VR）。
    """
    target = roformer_engine_dir(data_root)
    if target.is_dir():
        import sys

        s = str(target)
        if s not in sys.path:
            sys.path.insert(0, s)
    try:
        from audio_separator.separator import Separator  # type: ignore

        return Separator
    except Exception as e:  # noqa: BLE001
        raise DemucsEngineMissing(
            f"RoFormer 分离需要 {ROFORMER_PIP_NAME} 引擎，请先在引擎页安装。"
            f"原始错误：{e}"
        ) from e


def _bootstrap_sys_path(data_root: Path | None = None) -> Path:
    """Add the engine target to ``sys.path`` if it exists; return its path."""
    target = engine_target_dir(data_root)
    if target.is_dir():
        import sys

        s = str(target)
        if s not in sys.path:
            sys.path.insert(0, s)
    return target


def import_demucs(data_root: Path | None = None) -> Any:
    """Import and return the ``demucs.api`` module.

    Raises :class:`DemucsEngineMissing` with an actionable message when the
    engine or its native backend (torch) cannot load.
    """
    _bootstrap_sys_path(data_root)
    try:
        from demucs import api  # type: ignore

        return api
    except Exception as e:  # noqa: BLE001 — surface any native load failure
        raise DemucsEngineMissing(
            "Demucs 引擎未安装或无法加载，请先在引擎页安装 demucs。"
            f"原始错误：{e}"
        ) from e


class DemucsManager:
    """Builds cached separators and runs source separation."""

    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = Path(data_root) if data_root is not None else default_data_root()
        self._cache: dict[tuple[str, str], Any] = {}
        self._lock = threading.Lock()

    def _select_device(self) -> str:
        try:
            import torch  # type: ignore

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:  # noqa: BLE001 — torch always ships with the engine
            return "cpu"

    def load(self, model: str, *, device: str = "auto",
             shifts: int = 1, overlap: float = 0.25,
             jobs: int = 0) -> tuple[Any, str]:
        """Return a cached ``Separator`` and the device in use."""
        api = import_demucs(self.data_root)
        if device == "auto":
            device = self._select_device()
        key = (model, device)
        with self._lock:
            sep = self._cache.get(key)
            if sep is None:
                try:
                    sep = api.Separator(
                        model=model, device=device, shifts=int(shifts),
                        overlap=float(overlap), split=True, jobs=int(jobs),
                        progress=False,
                    )
                except Exception as e:  # noqa: BLE001
                    raise DemucsModelError(f"无法加载分离模型 {model}：{e}") from e
                self._cache[key] = sep
        return sep, device

    def _separate_roformer(self, model: str, input_path: Path, *,
                           output_format: str = "WAV") -> dict[str, Any]:
        """用 audio-separator 跑 RoFormer / MDX 模型。

        audio-separator 自己负责下载模型并直接产出成品文件，
        所以这里不接管"写 WAV"，只负责整理产物清单。
        """
        spec = ROFORMER_MODELS[model]
        Separator = import_audio_separator(self.data_root)

        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + model
        out_dir = self.data_root / "separated" / job_id
        out_dir.mkdir(parents=True, exist_ok=True)

        # 不同版本的 Separator 构造参数不完全一致，先按完整参数试，
        # 参数不兼容再退回无参构造 —— 与其猜签名，不如让代码自己适配。
        try:
            sep = Separator(output_dir=str(out_dir), output_format=output_format)
        except TypeError:
            log.warning("Separator 构造参数不兼容，退回默认构造", exc_info=True)
            sep = Separator()

        try:
            sep.load_model(model_filename=spec["filename"])
        except Exception as e:  # noqa: BLE001
            raise DemucsModelError(
                f"无法加载分离模型 {model}（{spec['filename']}）：{e}"
            ) from e

        try:
            produced = sep.separate(str(input_path))
        except Exception as e:  # noqa: BLE001
            raise DemucsModelError(f"RoFormer 音源分离失败：{e}") from e

        if not produced:
            raise DemucsModelError(f"分离未产出任何文件：{model}")

        stems: list[dict[str, Any]] = []
        for item in produced:
            p = Path(str(item))
            if not p.is_file():
                continue
            stems.append({
                "name": _roformer_stem_name(p.name, spec),
                "file": p.name,
                "path": str(p),
                "size_bytes": p.stat().st_size,
                "duration_s": round(_wav_duration_s(p) or 0.0, 3),
                "channels": _wav_channels(p) or 0,
            })
        if not stems:
            raise DemucsModelError(
                f"分离返回了 {len(produced)} 个条目但都不是可读文件：{model}")
        stems.sort(key=lambda s: s["name"])
        return {
            "job_id": job_id,
            "model": model,
            "engine": ROFORMER_ENGINE_ID,
            "kind": spec["kind"],
            "device": "cuda" if _cuda_available() else "cpu",
            "sample_rate": None,  # 由模型自身决定，探测不出来就不编
            "output_dir": str(out_dir),
            "stems": stems,
            "license": spec["license"],
            "license_note": (
                _UNKNOWN_LICENSE_NOTE if spec["license"] == "unknown" else ""
            ),
        }

    def separate(self, model: str, input_path: Path, *,
                 device: str = "auto", shifts: int = 1,
                 overlap: float = 0.25, jobs: int = 0) -> dict[str, Any]:
        """Separate ``input_path`` and write WAV stems; return a manifest."""
        if not isinstance(model, str) or not model.strip():
            raise DemucsParamError("model is required")
        model = model.strip()
        if is_roformer_model(model):
            # RoFormer 与 Demucs 参数体系完全不同（没有 shifts/overlap/jobs）。
            # 这里不静默忽略：用户调了参数却没生效是最难自查的一类问题，
            # 所以把被忽略的参数明确写进结果里。
            result = self._separate_roformer(model, Path(input_path))
            ignored = [
                name for name, val, dflt in (
                    ("shifts", shifts, 1), ("overlap", overlap, 0.25),
                    ("jobs", jobs, 0),
                ) if val != dflt
            ]
            if ignored:
                result["warning"] = (
                    f"RoFormer 引擎不使用 {('、'.join(ignored))} 参数，已忽略。"
                    "这些是 Demucs 的调参项，对 RoFormer 无效。"
                )
            return result
        if not isinstance(shifts, int) or shifts < 0 or shifts > 20:
            raise DemucsParamError("shifts must be between 0 and 20")
        if not isinstance(overlap, (int, float)) or not 0.0 <= float(overlap) < 1.0:
            raise DemucsParamError("overlap must be in [0, 1)")
        in_path = Path(input_path)
        if not in_path.is_file():
            raise DemucsParamError(f"audio file not found: {in_path}")

        api = import_demucs(self.data_root)
        sep, used_device = self.load(
            model, device=device, shifts=shifts, overlap=overlap, jobs=jobs)
        job_id = time.strftime("%Y%m%d-%H%M%S")
        out_dir = self.data_root / "separated" / job_id
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            _, stems = sep.separate_audio_file(in_path)
        except Exception as e:  # noqa: BLE001
            raise DemucsModelError(f"音源分离失败：{e}") from e

        sr = int(sep.samplerate)
        stem_manifest: list[dict[str, Any]] = []
        for name, wav_tensor in stems.items():
            out_path = out_dir / f"{name}.wav"
            try:
                api.save_audio(wav_tensor, str(out_path), sr)
            except Exception:  # noqa: BLE001 — fallback pure-python WAV writer
                _write_wav(out_path, wav_tensor, sr)
            stem_manifest.append({
                "name": name,
                "file": out_path.name,
                "path": str(out_path),
                "size_bytes": out_path.stat().st_size if out_path.exists() else 0,
                "duration_s": round(wav_tensor.shape[-1] / sr, 3),
                "channels": int(wav_tensor.shape[0]) if wav_tensor.dim() > 1 else 1,
            })
        stem_manifest.sort(key=lambda s: s["name"])
        return {
            "job_id": job_id,
            "model": model,
            "device": used_device,
            "sample_rate": sr,
            "output_dir": str(out_dir),
            "stems": stem_manifest,
        }


def _roformer_stem_name(filename: str, spec: dict[str, Any]) -> str:
    """从 audio-separator 的产物名推出可读的轨道名。

    audio-separator 的命名形如 ``song_(Vocals).wav``。带上括号后缀是它的
    固定约定，这里只做"去掉括号取内部词"的还原，**不**去猜第几轨。
    """
    stem = Path(filename).stem
    if "(" in stem and ")" in stem:
        inner = stem.rsplit("(", 1)[-1].rsplit(")", 1)[0].strip()
        if inner:
            return inner.lower().replace(" ", "_")
    return stem.lower().replace(" ", "_")


def _wav_duration_s(path: Path) -> float | None:
    """标准库读 WAV 时长；非 WAV 返回 None。"""
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
        return frames / float(rate) if rate > 0 else None
    except Exception:  # noqa: BLE001 — 非 WAV / 损坏 / 权限
        return None


def _wav_channels(path: Path) -> int | None:
    """标准库读 WAV 声道数；非 WAV 返回 None。"""
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnchannels()
    except Exception:  # noqa: BLE001
        return None


def _cuda_available() -> bool:
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False


def _write_wav(path: Path, wav_tensor: Any, sr: int) -> None:
    """Fallback 16-bit PCM WAV writer (no soundfile/torchaudio needed)."""
    try:
        import numpy as np  # type: ignore

        arr = wav_tensor.detach().cpu().numpy()
    except Exception:  # noqa: BLE001 — last resort: cannot encode
        return
    if arr.ndim == 1:
        arr = arr[None, :]
    # (channels, samples) -> interleaved (samples, channels)
    arr = np.clip(arr.T, -1.0, 1.0)
    pcm = (arr * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(arr.shape[1])
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def capabilities(data_root: Path | None = None) -> dict[str, Any]:
    """Report engine/device availability and the selectable models."""
    target = engine_target_dir(data_root)
    installed = target.is_dir()
    cuda = False
    demucs_version: str | None = None
    if installed:
        try:
            _bootstrap_sys_path(data_root)
            import demucs  # type: ignore

            demucs_version = getattr(demucs, "__version__", None)
            if not demucs_version:
                from importlib import metadata

                demucs_version = metadata.version("demucs")
            import torch  # type: ignore

            cuda = bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001 — capability probe must stay non-fatal
            log.debug("demucs capability probe failed", exc_info=True)
    rof_dir = roformer_engine_dir(data_root)
    rof_installed = rof_dir.is_dir()
    rof_version: str | None = None
    if rof_installed:
        try:
            from importlib import metadata

            rof_version = metadata.version("audio-separator")
        except Exception:  # noqa: BLE001 — capability probe must stay non-fatal
            log.debug("audio-separator version probe failed", exc_info=True)
    return {
        "engine": ENGINE_ID,
        "installed": installed,
        "engine_dir": str(target),
        "cuda_available": cuda,
        "version": demucs_version,
        "models": list(KNOWN_MODELS),
        # RoFormer 走独立引擎，必须单独报告，否则 UI 会把没装的引擎
        # 当成"可选模型"展示出来，用户点了才知道跑不了。
        "roformer": {
            "engine": ROFORMER_ENGINE_ID,
            "installed": rof_installed,
            "engine_dir": str(rof_dir),
            "version": rof_version,
            "pip": ROFORMER_PIP_NAME,
            "notes": ROFORMER_NOTES,
            "models": [
                {
                    "id": mid,
                    "label": spec["label"],
                    "kind": spec["kind"],
                    "stems": list(spec["stems"]),
                    "sdr_vocals": spec["sdr_vocals"],
                    "size_mb": spec["size_mb"],
                    "license": spec["license"],
                    "best_for": spec["best_for"],
                }
                for mid, spec in ROFORMER_MODELS.items()
            ],
        },
        "all_models": all_models(),
    }
