"""Runtime for the Piper lightweight neural text-to-speech engine.

Piper (``piper-tts``) runs VITS voices through ONNX Runtime: fast on CPU,
with each voice being an ``.onnx`` model plus an ``.onnx.json`` config.
Voices are downloaded from the ``rhasspy/piper-voices`` model repository.

The engine is installed into an isolated ``engines/pip-piper`` directory and
imported via a sys.path bootstrap, mirroring the other optional runtimes.
"""

from __future__ import annotations

import logging
import sys
import time
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Any

LOG = logging.getLogger("kevrai.piper")

ENGINE_ID = "piper"
ENGINE_DIRNAME = "pip-piper"
VOICE_REPO_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
VOICE_REPO_ID = "rhasspy/piper-voices"

# id -> (label, language, repo-relative directory, file stem)
KNOWN_VOICES: list[dict[str, str]] = [
    {
        "id": "en_US-lessac-medium",
        "label": "English (US) · Lessac · medium",
        "lang": "en",
        "dir": "en/en_US/lessac/medium",
    },
    {
        "id": "en_US-amy-medium",
        "label": "English (US) · Amy · medium",
        "lang": "en",
        "dir": "en/en_US/amy/medium",
    },
    {
        "id": "zh_CN-huayan-medium",
        "label": "中文（普通话）· 华颜 · medium",
        "lang": "zh",
        "dir": "zh/zh_CN/huayan/medium",
    },
]
_VOICE_BY_ID = {v["id"]: v for v in KNOWN_VOICES}

MAX_TEXT_LEN = 5000
MIN_LENGTH_SCALE = 0.5
MAX_LENGTH_SCALE = 3.0


#: Kokoro —— 82M 参数、Apache-2.0 可商用、CPU 即可实时。
#: 相比 Piper（偏机械、22kHz），Kokoro 是 2026 年本地 TTS 的性价比首选。
#: 注意：Kokoro 无声音克隆，只有预设音色；需要克隆应另选 Chatterbox（MIT）。
KOKORO_VOICE_PREFIX = "kokoro:"

#: Kokoro 音色命名形如 "af_heart"：首字母是语言代码，第二个字母是性别。
#: 这里只登记常见音色用于校验，实际可用音色以 kokoro 包为准。
KOKORO_LANG_CODES = {
    "a": "美式英语",
    "b": "英式英语",
    "j": "日语",
    "z": "普通话",
    "e": "西班牙语",
    "f": "法语",
    "h": "印地语",
    "i": "意大利语",
    "p": "巴西葡萄牙语",
}

#: Kokoro 默认采样率（kokoro 包输出 24kHz）。
KOKORO_SAMPLE_RATE = 24000


def is_kokoro_voice(voice_id: str) -> bool:
    """判断一个 voice_id 是否属于 Kokoro 引擎。

    支持两种写法：显式前缀 ``kokoro:af_heart``，或直接写 ``af_heart``。
    后者只在它**不可能是 Piper 音色**时才成立 —— Piper 的音色 id 一律
    形如 ``en_US-libritts_r-medium``（含连字符且以语言地区开头），
    所以不含连字符、形如 ``xx_yyy`` 的按 Kokoro 处理是安全的。
    """
    vid = (voice_id or "").strip()
    if not vid:
        return False
    if vid.lower().startswith(KOKORO_VOICE_PREFIX):
        return True
    # Piper 音色一定带 "-" 且以语言代码开头（en_US / zh_CN ...）
    if "-" in vid:
        return False
    # Kokoro 形如 af_heart / zf_xiaobei：两段下划线分隔，首段 2 字符
    parts = vid.split("_")
    if len(parts) != 2 or len(parts[0]) != 2:
        return False
    return parts[0][0] in KOKORO_LANG_CODES


def kokoro_voice_id(voice_id: str) -> str:
    """剥掉 ``kokoro:`` 前缀，返回 kokoro 包认识的音色名。"""
    vid = (voice_id or "").strip()
    if vid.lower().startswith(KOKORO_VOICE_PREFIX):
        return vid[len(KOKORO_VOICE_PREFIX):].strip()
    return vid


class PiperError(RuntimeError):
    """Base error for the Piper runtime."""


class PiperEngineMissing(PiperError):
    """The piper-tts package is not installed."""


class PiperVoiceError(PiperError):
    """A voice could not be downloaded or loaded."""


class PiperParamError(PiperError):
    """Invalid synthesis parameters."""


def engine_target_dir(data_root: Path) -> Path:
    return Path(data_root) / "engines" / ENGINE_DIRNAME


def _voices_dir(data_root: Path) -> Path:
    return Path(data_root) / "voices" / "piper"


def _tts_dir(data_root: Path) -> Path:
    return Path(data_root) / "tts" / "piper"


def _bootstrap_sys_path(data_root: Path) -> Path:
    target = engine_target_dir(data_root)
    target.mkdir(parents=True, exist_ok=True)
    if str(target) not in sys.path:
        sys.path.insert(0, str(target))
    return target


def import_piper(data_root: Path | None = None):
    """Import piper from the isolated engine dir, raising if absent."""
    if data_root is not None:
        _bootstrap_sys_path(Path(data_root))
    try:
        import piper  # type: ignore

        return piper
    except Exception as exc:  # noqa: BLE001 — normalise to a typed error
        raise PiperEngineMissing("Piper text-to-speech engine is not installed.") from exc


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        # URL is always https (VOICE_REPO_BASE); S310 audited.
        with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as fh:  # noqa: S310
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
        tmp.replace(dest)
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise PiperVoiceError(f"download failed: {url}: {exc}") from exc
    return dest


class PiperManager:
    """Loads voices and synthesizes speech for one data root."""

    def __init__(self, data_root: Path | str):
        self.data_root = Path(data_root)
        self._voices: dict[str, Any] = {}

    # -- engine -------------------------------------------------------------
    def import_piper(self):
        return import_piper(self.data_root)

    def ensure_voice(self, voice_id: str) -> tuple[Path, Path]:
        if voice_id not in _VOICE_BY_ID:
            raise PiperParamError(f"unknown voice: {voice_id}")
        spec = _VOICE_BY_ID[voice_id]
        local_dir = _voices_dir(self.data_root) / spec["dir"]
        onnx = local_dir / f"{voice_id}.onnx"
        cfg = local_dir / f"{voice_id}.onnx.json"
        if not onnx.is_file():
            _download(VOICE_REPO_BASE + f"{spec['dir']}/{voice_id}.onnx", onnx)
        if not cfg.is_file():
            _download(VOICE_REPO_BASE + f"{spec['dir']}/{voice_id}.onnx.json", cfg)
        return onnx, cfg

    def load_voice(self, voice_id: str, use_cuda: bool = False):
        if voice_id in self._voices:
            return self._voices[voice_id]
        onnx, cfg = self.ensure_voice(voice_id)
        piper = self.import_piper()
        try:
            voice = piper.PiperVoice.load(str(onnx), config_path=str(cfg), use_cuda=use_cuda)
        except Exception as exc:  # noqa: BLE001
            raise PiperVoiceError(f"could not load voice {voice_id}: {exc}") from exc
        self._voices[voice_id] = voice
        return voice

    # -- kokoro ------------------------------------------------------------
    def _load_kokoro(self, lang_code: str) -> Any:
        """加载 Kokoro pipeline。依赖缺失时明确说要装什么。"""
        try:
            from kokoro import KPipeline  # type: ignore
        except ImportError as e:
            raise PiperEngineMissing(
                "Kokoro 需要推理依赖：pip install kokoro"
                f"（原始错误：{e}）"
            ) from e
        key = f"kokoro:{lang_code}"
        pipe = self._voices.get(key)
        if pipe is None:
            try:
                pipe = KPipeline(lang_code=lang_code)
            except Exception as e:  # noqa: BLE001
                raise PiperVoiceError(f"无法加载 Kokoro（{lang_code}）：{e}") from e
            self._voices[key] = pipe
        return pipe

    def synthesize_kokoro(self, voice_id: str, text: str) -> dict[str, Any]:
        """用 Kokoro 合成语音，返回与 Piper 路径一致的结构。

        Kokoro 的 KPipeline 按"段落生成器"返回，长文本会产出多段音频；
        这里把各段拼接成一条连续音频，避免上层拿到半截。
        """
        import numpy as np
        import wave

        voice = kokoro_voice_id(voice_id)
        lang_code = voice[0] if voice else "a"
        pipe = self._load_kokoro(lang_code)

        chunks: list[Any] = []
        try:
            for _gs, _ps, audio in pipe(text, voice=voice):
                chunks.append(np.asarray(audio, dtype="float32"))
        except Exception as e:  # noqa: BLE001
            raise PiperVoiceError(f"Kokoro 合成失败：{e}") from e

        if not chunks:
            raise PiperVoiceError("Kokoro 未产出任何音频")

        audio = chunks[0] if len(chunks) == 1 else np.concatenate(chunks)
        return {
            "audio": audio,
            "sample_rate": KOKORO_SAMPLE_RATE,
            "voice": voice_id,
            "engine": "kokoro",
        }

    # -- synthesis ----------------------------------------------------------
    def synthesize(
        self,
        voice_id: str,
        text: str,
        length_scale: float | None = None,
        use_cuda: bool = False,
    ) -> dict[str, Any]:
        if not text or not str(text).strip():
            raise PiperParamError("text is empty")
        text = str(text)
        # Kokoro 走独立路径：音色体系与采样率都和 Piper 不同，不能混用。
        if is_kokoro_voice(voice_id):
            return self.synthesize_kokoro(voice_id, text)
        if len(text) > MAX_TEXT_LEN:
            raise PiperParamError(f"text exceeds {MAX_TEXT_LEN} characters")
        if length_scale is None:
            length_scale = 1.0
        length_scale = float(length_scale)
        if not (MIN_LENGTH_SCALE <= length_scale <= MAX_LENGTH_SCALE):
            raise PiperParamError(f"length_scale must be within [{MIN_LENGTH_SCALE},{MAX_LENGTH_SCALE}]")

        voice = self.load_voice(voice_id, use_cuda=use_cuda)
        # Build a per-request synthesis config (length/speed). Optional knob:
        # stay non-fatal if the installed piper build lacks SynthesisConfig.
        syn_config = None
        try:
            piper_mod = self.import_piper()
            syn_config = piper_mod.config.SynthesisConfig(length_scale=length_scale)
        except Exception:  # noqa: BLE001
            LOG.debug("could not build piper SynthesisConfig", exc_info=True)

        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        out_dir = _tts_dir(self.data_root)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{job_id}.wav"

        started = time.monotonic()
        try:
            with wave.open(str(out_path), "wb") as wav_writer:
                # piper-tts >= 1.8: synthesize_wav sets the format and writes.
                if hasattr(voice, "synthesize_wav"):
                    voice.synthesize_wav(text, wav_writer, syn_config=syn_config)
                else:  # older piper: synthesize(text, wav) may be a generator.
                    result = voice.synthesize(text, wav_writer)
                    if (
                        result is not None
                        and hasattr(result, "__iter__")
                        and not isinstance(result, bytes | bytearray)
                    ):
                        for _ in result:
                            pass
        except PiperError:
            raise
        except Exception as exc:  # noqa: BLE001
            out_path.unlink(missing_ok=True)
            raise PiperVoiceError(f"synthesis failed: {exc}") from exc

        sample_rate = getattr(getattr(voice, "config", None), "sample_rate", 22050)
        with wave.open(str(out_path), "rb") as rd:
            frames = rd.getnframes()
            rate = rd.getframerate()
            size_bytes = rd.getnframes() * rd.getnchannels() * rd.getsampwidth()
        duration = frames / float(rate)
        LOG.info(
            "piper synthesized voice=%s chars=%d dur=%.2fs in %.1fs",
            voice_id,
            len(text),
            duration,
            time.monotonic() - started,
        )
        return {
            "engine": ENGINE_ID,
            "job_id": job_id,
            "voice": voice_id,
            "path": str(out_path),
            "sample_rate": sample_rate,
            "duration_s": round(duration, 3),
            "size_bytes": size_bytes,
            "length_scale": length_scale,
        }


def capabilities(data_root: Path | str) -> dict[str, Any]:
    """Report engine availability and the selectable voices."""
    root = Path(data_root)
    target = engine_target_dir(root)
    installed = target.is_dir()
    version: str | None = None
    if installed:
        try:
            _bootstrap_sys_path(root)
            import piper  # type: ignore

            version = getattr(piper, "__version__", None)
            if not version:
                from importlib import metadata

                version = metadata.version("piper-tts")
        except Exception:  # noqa: BLE001 — capability probe stays non-fatal
            LOG.debug("piper capability probe failed", exc_info=True)
    return {
        "engine": ENGINE_ID,
        "installed": installed,
        "engine_dir": str(target),
        "version": version,
        "voices": [{"id": v["id"], "label": v["label"], "lang": v["lang"]} for v in KNOWN_VOICES],
    }
