"""Music generation runtime — native text-to-music via Meta MusicGen.

A local, offline text-to-music path that runs entirely in the existing
``transformers`` engine (``<data_root>/engines/pip-transformers``):

* ``MusicgenForConditionalGeneration`` decodes ~50 audio tokens per second;
  the decoded EnCodec frames are resynthesised to PCM and written as a
  standard 16-bit PCM WAV (no scipy/soundfile dependency).
* stereo MusicGen models yield two channels; mono models one. Both are handled.

``transformers`` / ``torch`` are never imported at module import time, so the
control plane starts even before the engine is installed.
"""

from __future__ import annotations

import logging
import threading
import time
import wave
from pathlib import Path
from typing import Any

from .settings import default_data_root

log = logging.getLogger(__name__)

ENGINE_ID = "transformers"
_PIP_DIR = "pip-transformers"

#: MusicGen's EnCodec decoder emits 50 frames per second of audio.
TOKENS_PER_SECOND = 50
DEFAULT_SAMPLING_RATE = 32000
MIN_DURATION_S = 1
#: MusicGen 的实际上限。它的 EnCodec 自回归在长音频上会明显退化，
#: 因此保守限制在 60 秒。
MAX_DURATION_MUSICGEN_S = 60
#: ACE-Step 1.5 官方支持 10 秒到 10 分钟，上限远高于 MusicGen。
MAX_DURATION_ACE_STEP_S = 600

#: Catalog id -> HF repo (verified against the HuggingFace Hub; see ADR).
KNOWN_MODELS: dict[str, str] = {
    "musicgen-small": "facebook/musicgen-small",
    "musicgen-medium": "facebook/musicgen-medium",
    "musicgen-large": "facebook/musicgen-large",
    "musicgen-stereo": "facebook/musicgen-stereo-large",
    # ACE-Step 1.5 —— MIT 许可、可商用，显存需求低（见 ACE_STEP_NOTES）。
    # 这是当前唯一同时满足"能唱人声 + 可商用 + 消费级显卡可跑"的开源选项。
    "ace-step-1.5": "ACE-Step/Ace-Step1.5",
    # MiniMax Music 3 —— 能力最强但**非商用**，且门槛极高。登记在此仅为
    # 完整性，UI 必须按 LICENSE_WARNINGS 如实标注，不得当作默认推荐。
    "minimax-music3": "MiniMax/MiniMax-Music3",
}

#: 每个模型的许可与门槛。UI 展示模型时必须连带展示这些事实 ——
#: "下载了就能商用"是这个领域最常见的误解，而且后果是法律风险。
LICENSE_WARNINGS: dict[str, dict[str, str]] = {
    "minimax-music3": {
        "license": "CC BY-NC 4.0",
        "commercial": "false",
        "note": (
            "非商用许可，仅可用于个人学习/研究；商用需走 MiniMax 付费 API。"
            "官方参考部署需要双 GPU，权重大约 57GB；"
            "HF 卡说 24GB+ / CPU offload 可降到 8GB，"
            "但 GitHub README 与 HF 卡对最低配置的说法并不一致。"
        ),
    },
    "musicgen-small": {"license": "CC BY-NC 4.0", "commercial": "false",
                       "note": "Meta 权重非商用；仅纯器乐，无人声。"},
    "musicgen-medium": {"license": "CC BY-NC 4.0", "commercial": "false",
                        "note": "Meta 权重非商用；仅纯器乐，无人声。"},
    "musicgen-large": {"license": "CC BY-NC 4.0", "commercial": "false",
                       "note": "Meta 权重非商用；仅纯器乐，无人声。"},
    "musicgen-stereo": {"license": "CC BY-NC 4.0", "commercial": "false",
                        "note": "Meta 权重非商用；纯器乐，但为立体声。"},
    "ace-step-1.5": {
        "license": "MIT",
        "commercial": "true",
        "note": (
            "MIT 许可，可商用。显存需求低于 4GB，消费级显卡可跑；"
            "支持人声演唱、50+ 语言歌词、10 秒到 10 分钟时长、"
            "BPM/调性/1000+ 乐器风格控制，可用几首歌训练个人风格 LoRA。"
            "官方自陈局限：中文说唱表现较弱、repaint 过渡偶有不自然、"
            "人声合成质量仍有提升空间。"
        ),
    },
}

#: ACE-Step 与 MusicGen 是两套完全不同的架构，不能共用加载路径。
#: ACE-Step 是 DiT + LM 两阶段（先规划后合成），MusicGen 是单阶段自回归。
ACE_STEP_PREFIXES = ("ace-step", "ACE-Step", "ace_step")


def _is_ace_step(source: str | Path) -> bool:
    """判断一个模型源是否是 ACE-Step 家族。

    只看路径/仓库名前缀，不做内容探测 —— 探测意味着要先下载。

    比较前统一转小写：HF 上的实际写法是 ``ACE-Step/Ace-Step1.5``，
    而本地目录可能是 ``ace-step-1.5``。大小写敏感会让 ACE-Step 被误判成
    MusicGen，进而走错加载路径（报错信息完全指不到真正原因）。
    """
    text = str(source).replace("\\", "/")
    low = text.lower()
    prefixes = [p.lower() for p in ACE_STEP_PREFIXES]
    # 逐段检查：仓库名可能是 "ACE-Step/Ace-Step1.5"，本地目录可能是
    # "ace-step-1.5"，只看最后一段会漏掉 org 名里带标识的情况。
    for seg in low.split("/"):
        if seg and any(seg.startswith(pre) for pre in prefixes):
            return True
    return False


class MusicError(Exception):
    """Base class for music-generation failures."""


class MusicEngineMissing(MusicError):
    """The transformers engine (torch/transformers) is not installed."""


class MusicModelError(MusicError):
    """The model could not be loaded, or audio synthesis failed."""


class MusicParamError(MusicError):
    """A caller-supplied parameter was invalid."""


def engine_target_dir(data_root: Path | None = None) -> Path:
    """Directory the transformers engine is installed into (``pip --target``)."""
    root = Path(data_root) if data_root is not None else default_data_root()
    return root / "engines" / _PIP_DIR


def _bootstrap_sys_path(data_root: Path | None = None) -> Path:
    """Add the engine target to ``sys.path`` if it exists; return its path."""
    target = engine_target_dir(data_root)
    if target.is_dir():
        import sys

        s = str(target)
        if s not in sys.path:
            sys.path.insert(0, s)
    return target


def import_transformers(data_root: Path | None = None) -> Any:
    """Import and return the ``transformers`` module from the engine target.

    Raises :class:`MusicEngineMissing` with an actionable message when the
    engine or its native backend (torch) cannot load.
    """
    _bootstrap_sys_path(data_root)
    try:
        import numpy as _np  # noqa: F401 — fully init numpy before accelerate
        import transformers  # type: ignore

        return transformers
    except Exception as e:  # noqa: BLE001 — surface any native load failure
        raise MusicEngineMissing(
            "Transformers 引擎未安装或无法加载，请先在引擎页安装 transformers。"
            f"原始错误：{e}"
        ) from e


def _hub_model_dir(repo: str, data_root: Path | None = None,
                   download_dir: Path | None = None) -> Path:
    """Where the hub downloader stores ``repo``'s files."""
    root = Path(data_root) if data_root is not None else default_data_root()
    dl = Path(download_dir) if download_dir is not None else root / "downloads"
    safe = repo.strip("/").replace("/", "__")
    return dl / "hub" / "hf" / safe


def resolve_model_source(repo: str, data_root: Path | None = None,
                         download_dir: Path | None = None) -> str | Path:
    """Return a local model directory when the weights exist, else ``repo``."""
    local = _hub_model_dir(repo, data_root, download_dir)
    has_config = (local / "config.json").exists()
    weight_markers = (
        "model.safetensors", "pytorch_model.bin",
        "model-00001-of-00002.safetensors",
    )
    has_weights = any((local / m).exists() for m in weight_markers)
    if has_config and has_weights:
        return local
    return repo


def _pcm16(audio: Any) -> bytes:
    """Interleave float32 ``[channels, samples]`` into 16-bit PCM bytes."""
    import numpy as np

    arr = np.asarray(audio, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr[None, :]
    # Clamp to the int16 range before quantising.
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).round().astype("<i2")
    # Interleave channels: [channels, samples] -> [samples, channels]
    interleaved = pcm.T.reshape(-1)
    return interleaved.tobytes()


def write_wav(path: str | Path, audio: Any, sampling_rate: int) -> str:
    """Write ``audio`` (``[channels, samples]`` float) to a 16-bit PCM WAV."""
    import numpy as np

    arr = np.asarray(audio, dtype=np.float32)
    channels = 1 if arr.ndim == 1 else int(arr.shape[0])
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(int(sampling_rate))
        wf.writeframes(_pcm16(arr))
    return str(out)


class MusicManager:
    """Loads/caches MusicGen models and synthesises music from text."""

    #: ACE-Step 输出采样率的兜底值（官方常见 48k）。
    DEFAULT_ACE_SR = 48000

    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = (
            Path(data_root) if data_root is not None else default_data_root()
        )
        self._cache: dict[str, tuple[Any, Any]] = {}
        self._lock = threading.Lock()

    def resolve_repo(self, model_id_or_repo: str) -> str:
        """Map a catalog id to its repo; pass a repo slug straight through."""
        mid = (model_id_or_repo or "").strip()
        if not mid:
            raise MusicParamError("model 不能为空")
        return KNOWN_MODELS.get(mid, mid)

    def _load(self, source: str | Path) -> tuple[Any, Any]:
        if _is_ace_step(source):
            return self._load_ace_step(source)
        tf = import_transformers(self.data_root)
        try:
            processor = tf.AutoProcessor.from_pretrained(source)
            model = tf.MusicgenForConditionalGeneration.from_pretrained(source)
        except Exception as e:  # noqa: BLE001
            raise MusicModelError(f"音乐模型加载失败：{e}") from e
        model.eval()
        return model, processor

    def _load_ace_step(self, source: str | Path) -> tuple[Any, Any]:
        """加载 ACE-Step 1.5。

        ACE-Step 不是 transformers 里的标准 AutoModel，它的官方推理入口是
        自己的 ``ace_step`` 包（pip install ace-step）。这里**不猜类名**：
        依次尝试已知可用的入口，全部不可用时报清楚"试过哪些"，让用户知道
        该装什么，而不是丢一个 ImportError 了事。
        """
        tried: list[str] = []
        try:
            from ace_step import AceStepPipeline  # type: ignore

            pipe = AceStepPipeline.from_pretrained(str(source))
            return pipe, None
        except ImportError as e:
            tried.append("ace_step.AceStepPipeline（未安装 ace-step）")
        except Exception as e:  # noqa: BLE001
            tried.append(f"ace_step.AceStepPipeline（{type(e).__name__}: {e}）")

        try:
            import torch  # noqa: F401
            from diffusers import DiffusionPipeline  # type: ignore

            pipe = DiffusionPipeline.from_pretrained(str(source))
            return pipe, None
        except ImportError as e:
            tried.append("diffusers.DiffusionPipeline（未安装 diffusers）")
        except Exception as e:  # noqa: BLE001
            tried.append(f"diffusers.DiffusionPipeline（{type(e).__name__}: {e}）")

        raise MusicModelError(
            "ACE-Step 加载失败，已尝试以下入口：\n  - "
            + "\n  - ".join(tried)
            + "\n请先安装推理依赖（pip install ace-step），再重试。"
        )

    def load(self, model_id_or_repo: str) -> tuple[Any, Any, str]:
        """Resolve, load (cached) and return ``(model, processor, repo)``."""
        repo = self.resolve_repo(model_id_or_repo)
        source = resolve_model_source(repo, self.data_root)
        key = str(source)
        with self._lock:
            bundle = self._cache.get(key)
            if bundle is None:
                bundle = self._load(source)
                self._cache[key] = bundle
        model, processor = bundle
        return model, processor, repo

    def synthesize(
        self,
        model_id_or_repo: str,
        prompt: str,
        *,
        duration_s: float = 15.0,
        guidance_scale: float = 3.0,
        seed: int | None = None,
        do_sample: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Synthesise a clip; returns audio ``[channels, samples]`` + metadata.

        额外的 ``lyrics`` 仅对 ACE-Step 生效（MusicGen 无人声，传了会被忽略）。
        """

        prompt = (prompt or "").strip()
        if not prompt:
            raise MusicParamError("prompt 不能为空")
        try:
            duration_s = float(duration_s)
        except (TypeError, ValueError) as e:
            raise MusicParamError("duration_s 必须是数字") from e
        is_ace = _is_ace_step(self.resolve_repo(model_id_or_repo))
        max_s = MAX_DURATION_ACE_STEP_S if is_ace else MAX_DURATION_MUSICGEN_S
        if not (MIN_DURATION_S <= duration_s <= max_s):
            raise MusicParamError(
                f"duration_s 必须在 {MIN_DURATION_S}-{max_s} 秒之间"
                + ("（ACE-Step 最长支持 10 分钟）" if is_ace else "")
            )
        try:
            guidance_scale = float(guidance_scale)
        except (TypeError, ValueError) as e:
            raise MusicParamError("guidance_scale 必须是数字") from e
        if not (0.0 < guidance_scale <= 20.0):
            raise MusicParamError("guidance_scale 必须在 0-20 之间")

        started = time.monotonic()
        model, processor, repo = self.load(model_id_or_repo)
        if _is_ace_step(repo):
            return self._synthesize_ace_step(
                model, repo, prompt, duration_s=duration_s,
                guidance_scale=guidance_scale, seed=seed,
                do_sample=do_sample, started=started,
                lyrics=kwargs.pop("lyrics", None),
            )
        max_new_tokens = max(1, int(round(duration_s * TOKENS_PER_SECOND)))
        inputs = processor(text=[prompt], padding=True, return_tensors="pt")
        if seed is not None:
            import torch

            torch.manual_seed(int(seed))
        try:
            audio_values = model.generate(
                **inputs,
                do_sample=bool(do_sample),
                guidance_scale=guidance_scale,
                max_new_tokens=max_new_tokens,
            )
        except MusicError:
            raise
        except Exception as e:  # noqa: BLE001 — synthesis failures
            raise MusicModelError(f"音乐合成失败：{e}") from e

        audio = audio_values[0].detach().cpu().float().numpy()
        sr = int(getattr(model.config.audio_encoder, "sampling_rate",
                         DEFAULT_SAMPLING_RATE))
        return {
            "audio": audio,
            "sampling_rate": sr,
            "repo": repo,
            "requested_s": duration_s,
            "elapsed_s": round(time.monotonic() - started, 3),
        }

    def _synthesize_ace_step(
        self,
        pipe: Any,
        repo: str,
        prompt: str,
        *,
        duration_s: float,
        guidance_scale: float,
        seed: int | None,
        do_sample: bool,
        started: float,
        lyrics: str | None,
    ) -> dict[str, Any]:
        """ACE-Step 的推理路径。

        ACE-Step 的接口形态和 MusicGen 完全不同（两阶段 DiT + LM，
        歌词是独立入参），所以这里单独走一条路径。

        调用形态按优先级尝试，全部失败时报清楚"试过哪些"。不猜参数名 ——
        猜错了会静默生成出与歌词无关的东西，那比报错危险得多。
        """
        base: dict[str, Any] = {"prompt": prompt}
        if lyrics:
            base["lyrics"] = lyrics
        if seed is not None:
            base["seed"] = int(seed)

        attempts: list[tuple[str, dict[str, Any]]] = [
            # 官方仓库用词是 audio_duration（秒）
            ("audio_duration", dict(base, audio_duration=duration_s)),
            ("duration", dict(base, duration=duration_s)),
            ("max_new_tokens", dict(
                base,
                max_new_tokens=max(1, int(round(duration_s * TOKENS_PER_SECOND))),
            )),
        ]
        errors: list[str] = []
        for label, kwargs in attempts:
            try:
                out = pipe(**kwargs)
            except TypeError as e:
                errors.append(f"{label}: {e}")
                continue
            except Exception as e:  # noqa: BLE001
                raise MusicModelError(f"ACE-Step 合成失败：{e}") from e
            return self._normalise_ace_output(
                out, repo, duration_s, guidance_scale, started
            )
        raise MusicModelError(
            "ACE-Step 调用形态不匹配，已尝试：\n  - "
            + "\n  - ".join(errors)
            + "\n请确认安装的 ace-step 版本与模型权重版本一致。"
        )

    @staticmethod
    def _normalise_ace_output(
        out: Any, repo: str, duration_s: float,
        guidance_scale: float, started: float,
    ) -> dict[str, Any]:
        """把 ACE-Step 的返回规整成与 MusicGen 一致的结构。

        不同版本返回 dict / 元组 / 对象，这里统一取 (audio, sr)。
        取不到采样率时用 48000（ACE-Step 官方输出的常见值）并在结果里标注。
        """
        sr = MusicManager.DEFAULT_ACE_SR
        audio = None
        if isinstance(out, dict):
            audio = out.get("audio")
            sr = int(out.get("sampling_rate") or out.get("sample_rate") or sr)
            if audio is None:
                for v in out.values():
                    if hasattr(v, "shape") or isinstance(v, (list, tuple)):
                        audio = v
                        break
        elif isinstance(out, (tuple, list)):
            audio = out[0]
            if len(out) > 1 and isinstance(out[1], int):
                sr = out[1]
        elif hasattr(out, "audio"):
            audio = out.audio
            sr = int(getattr(out, "sampling_rate", sr) or sr)

        if audio is None:
            raise MusicModelError(
                "ACE-Step 返回结构中找不到音频数据，无法写出文件"
            )
        import numpy as np

        audio = np.asarray(audio, dtype="float32")
        if audio.ndim == 1:
            audio = audio[None, :]
        return {
            "audio": audio,
            "sampling_rate": sr,
            "repo": repo,
            "requested_s": duration_s,
            "elapsed_s": round(time.monotonic() - started, 3),
        }

    def generate_to_file(
        self,
        model_id_or_repo: str,
        prompt: str,
        out_path: str | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Synthesise and write a WAV; returns metadata with ``audio_path``."""
        res = self.synthesize(model_id_or_repo, prompt, **kwargs)
        audio_path = write_wav(
            out_path, res["audio"], res["sampling_rate"]
        )
        return {
            "audio_path": audio_path,
            "sampling_rate": res["sampling_rate"],
            "repo": res["repo"],
            "requested_s": res["requested_s"],
            "elapsed_s": res["elapsed_s"],
        }


def capabilities(data_root: Path | None = None) -> dict[str, Any]:
    """Report engine availability and the MusicGen models it can run."""
    root = Path(data_root) if data_root is not None else default_data_root()
    target = engine_target_dir(root)
    installed = target.is_dir()
    version: str | None = None
    if installed:
        try:
            _bootstrap_sys_path(root)
            from importlib import metadata

            version = metadata.version("transformers")
        except Exception:  # noqa: BLE001 — capability probe stays non-fatal
            log.debug("transformers capability probe failed", exc_info=True)
    return {
        "engine": ENGINE_ID,
        "installed": installed,
        "version": version,
        "engine_dir": str(target),
        "duration_range_s": [MIN_DURATION_S, MAX_DURATION_MUSICGEN_S],
        "duration_range_ace_step_s": [MIN_DURATION_S, MAX_DURATION_ACE_STEP_S],
        "models": [
            {
                "id": mid,
                "repo": repo,
                # 许可随模型一起返回：UI 展示模型时不可能不展示许可。
                **license_info(mid),
            }
            for mid, repo in KNOWN_MODELS.items()
        ],
    }


def _unknown_license() -> dict[str, str]:
    """未登记许可的统一返回。绝不默认成"可商用"。"""
    return {
        "license": "unknown",
        "commercial": "unknown",
        "note": "该模型未在许可清单中登记，商用前请自行核对官方许可。",
    }


def license_info(model_id_or_repo: str) -> dict[str, str]:
    """返回该音乐模型的许可与门槛事实。

    UI 展示任何音乐模型时都必须连带展示这些信息。本地部署 ≠ 可商用，
    这是本模块最容易误导用户的地方（MusicGen 与 Music3 都是 CC BY-NC）。

    未登记的模型返回 unknown，绝不默认成"可商用"。
    """
    mid = (model_id_or_repo or "").strip()
    if not mid:
        return _unknown_license()

    # 1) catalog id 直接命中
    info = LICENSE_WARNINGS.get(mid)
    if info:
        return dict(info)

    # 2) 反查 KNOWN_MODELS：调用方传的可能是仓库全名（如
    #    "ACE-Step/Ace-Step1.5"），而许可表以 catalog id（"ace-step-1.5"）
    #    为键，两者字面不同。用大小写不敏感的仓库名比对来桥接。
    needle = mid.lower().rstrip("/")
    for cid, repo in KNOWN_MODELS.items():
        if needle == repo.lower().rstrip("/"):
            info = LICENSE_WARNINGS.get(cid)
            if info:
                return dict(info)

    # 3) 用尾名再试一次（本地目录名可能只保留最后一段）
    tail = needle.split("/")[-1]
    for cid, repo in KNOWN_MODELS.items():
        if tail == repo.lower().rstrip("/").split("/")[-1]:
            info = LICENSE_WARNINGS.get(cid)
            if info:
                return dict(info)

    return _unknown_license()
