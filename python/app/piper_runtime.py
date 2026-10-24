"""Runtime for the Piper lightweight neural text-to-speech engine.

Piper (``piper-tts``) runs VITS voices through ONNX Runtime: fast on CPU,
with each voice being an ``.onnx`` model plus an ``.onnx.json`` config.
Voices are downloaded from the ``rhasspy/piper-voices`` model repository.

The engine is installed into an isolated ``engines/pip-piper`` directory and
imported via a sys.path bootstrap, mirroring the other optional runtimes.
"""

from __future__ import annotations

import logging
import re
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


#: Chatterbox —— Resemble AI 的零样本声音克隆 TTS，MIT 许可、可商用。
#: 与 Kokoro 的分工：Kokoro 只有 54 个预设音色、无克隆；
#: 需要"用自己的声音说话"就必须走 Chatterbox。
#:
#: 三条必须如实告知用户的事实（写入 CHATTERBOX_NOTES，UI 必须展示）：
#: 1. 输出带 Perth 神经水印（不可听、抗 MP3 压缩与剪辑）。
#:    这是官方出于负责任 AI 的刻意设计，不是 bug，但用户有知情权。
#: 2. 原版 0.5B 模型**仅支持英语**。中文等多语言需 Multilingual 变体。
#: 3. pip 包名是 ``chatterbox-tts``，不是 ``chatterbox``。写错会装不上。
CHATTERBOX_VOICE_PREFIX = "chatterbox:"
CHATTERBOX_SAMPLE_RATE = 24000
CHATTERBOX_PIP_NAME = "chatterbox-tts"

#: 参考音频推荐时长（秒）。官方建议约 5 秒的干净单人声；
#: 质量远比长度重要 —— 背景音乐、混响、第二个人声都会被一起编码进
#: 说话人嵌入，然后被忠实重现。这里是提示，不是硬限制。
CHATTERBOX_REF_MIN_S = 5.0
CHATTERBOX_REF_MAX_S = 10.0
#: 超出这个范围直接拒绝：太短提取不出说话人特征，太长只会徒增噪声与耗时。
CHATTERBOX_REF_HARD_MIN_S = 1.0
CHATTERBOX_REF_HARD_MAX_S = 60.0

CHATTERBOX_NOTES = (
    "Chatterbox（MIT，可商用）支持 5 秒样本零克隆音色。"
    "注意：① 生成音频带 Perth 神经水印，不可听且抗剪辑；"
    "② 原版 0.5B 仅支持英语，多语言需 Multilingual 变体；"
    "③ 参考音频需干净单人声，背景音乐与混响会被一起克隆进去。"
)

#: 音色档案名白名单。参考音频会被写进文件系统，
#: 名字必须受限，否则 "..\\..\\evil" 之类会造成路径穿越。
SAFE_VOICE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def is_chatterbox_voice(voice_id: str) -> bool:
    """判断 voice_id 是否走 Chatterbox 引擎。

    只认显式前缀 ``chatterbox:<name>``，不像 Kokoro 那样做兜底猜测：
    Chatterbox 的 name 是用户自定义的档案名，没有任何可依据的命名规律，
    猜错会把 Piper/Kokoro 的音色送进克隆路径，报错完全指不到原因。
    """
    return (voice_id or "").strip().lower().startswith(CHATTERBOX_VOICE_PREFIX)


def chatterbox_voice_id(voice_id: str) -> str:
    """剥掉 ``chatterbox:`` 前缀，返回克隆档案名。"""
    vid = (voice_id or "").strip()
    if vid.lower().startswith(CHATTERBOX_VOICE_PREFIX):
        return vid[len(CHATTERBOX_VOICE_PREFIX):].strip()
    return vid


def cloned_voices_dir(data_root: Path | str) -> Path:
    """存放声音克隆参考音频的目录。"""
    return Path(data_root) / "voices" / "chatterbox"


def _wav_duration_s(path: Path) -> float | None:
    """用标准库读 WAV 时长。非 WAV 或读不了时返回 None。

    刻意不引入音频库：这里只需要一个"够用的时长检查"，
    而 mp3/flac 的时长探测要靠 soundfile/librosa，属于不必要的重依赖。
    读不出来就诚实返回 None，让调用方跳过时长检查，而不是猜一个数。
    """
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
        if rate <= 0:
            return None
        return frames / float(rate)
    except Exception:  # noqa: BLE001 — 不是 WAV / 文件损坏 / 权限问题
        return None


def list_cloned_voices(data_root: Path | str) -> list[dict[str, Any]]:
    """列出已保存的克隆音色档案。"""
    root = cloned_voices_dir(data_root)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(root.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        dur = _wav_duration_s(p)
        out.append({
            "name": p.stem,
            "id": f"{CHATTERBOX_VOICE_PREFIX}{p.stem}",
            "file": p.name,
            "path": str(p),
            "size_bytes": p.stat().st_size,
            "duration_s": round(dur, 3) if dur is not None else None,
            # 时长读不出来时明确说"未探测"，不要显示成 0 秒。
            "duration_known": dur is not None,
        })
    return out


def save_reference_sample(
    data_root: Path | str,
    name: str,
    data: bytes,
    suffix: str = ".wav",
) -> dict[str, Any]:
    """保存一份声音克隆参考音频。

    ``name`` 必须匹配 :data:`SAFE_VOICE_NAME` —— 这是防路径穿越的硬边界，
    因为 name 会被直接拼进文件系统路径。
    """
    clean = (name or "").strip()
    if not SAFE_VOICE_NAME.fullmatch(clean):
        raise PiperParamError(
            f"音色名不合法：{name!r}（只允许字母、数字、点、下划线、连字符，1-64 字符）"
        )
    if not data:
        raise PiperParamError("参考音频内容为空")
    ext = (suffix or "").strip().lower()
    if not ext.startswith("."):
        ext = "." + ext
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
        raise PiperParamError(f"不支持的音频扩展名：{suffix!r}")

    root = cloned_voices_dir(data_root)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{clean}{ext}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        tmp.write_bytes(data)
        tmp.replace(dest)
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise PiperParamError(f"保存参考音频失败：{e}") from e

    dur = _wav_duration_s(dest)
    warning = ""
    if dur is None:
        warning = "无法探测时长（非 WAV 或文件不可读），已跳过时长检查。"
    elif dur < CHATTERBOX_REF_HARD_MIN_S:
        warning = f"参考音频仅 {dur:.1f} 秒，过短会导致克隆效果差。"
    elif dur > CHATTERBOX_REF_HARD_MAX_S:
        warning = f"参考音频 {dur:.1f} 秒，过长不会提升效果，只会更慢、引入更多噪声。"
    elif not (CHATTERBOX_REF_MIN_S <= dur <= CHATTERBOX_REF_MAX_S):
        warning = (
            f"参考音频 {dur:.1f} 秒，官方推荐 {CHATTERBOX_REF_MIN_S:.0f}-"
            f"{CHATTERBOX_REF_MAX_S:.0f} 秒的干净单人声。"
        )
    return {
        "name": clean,
        "id": f"{CHATTERBOX_VOICE_PREFIX}{clean}",
        "path": str(dest),
        "size_bytes": dest.stat().st_size,
        "duration_s": round(dur, 3) if dur is not None else None,
        "duration_known": dur is not None,
        "warning": warning,
    }


def delete_reference_sample(data_root: Path | str, name: str) -> bool:
    """删除一份克隆档案。返回是否真的删掉了东西。"""
    clean = (name or "").strip()
    if not SAFE_VOICE_NAME.fullmatch(clean):
        raise PiperParamError(f"音色名不合法：{name!r}")
    root = cloned_voices_dir(data_root)
    removed = False
    for p in root.glob(f"{clean}.*") if root.is_dir() else []:
        if p.is_file():
            p.unlink(missing_ok=True)
            removed = True
    return removed


def resolve_reference_audio(
    data_root: Path | str,
    name: str,
    explicit: str | None = None,
) -> Path:
    """解析出参考音频的绝对路径。

    ``explicit`` 是调用方直接给出的路径。它必须落在 data_root 之内 ——
    否则任意文件都能被当作参考音频送进模型，这是本地服务也不能开的口子。
    """
    root = Path(data_root).resolve()
    if explicit:
        cand = Path(explicit)
        cand = (root / cand).resolve() if not cand.is_absolute() else cand.resolve()
        try:
            cand.relative_to(root)
        except ValueError as e:
            raise PiperParamError(
                f"参考音频路径必须位于数据目录内：{explicit}"
            ) from e
        if not cand.is_file():
            raise PiperParamError(f"参考音频不存在：{cand}")
        return cand

    clean = (name or "").strip()
    if not SAFE_VOICE_NAME.fullmatch(clean):
        raise PiperParamError(f"音色名不合法：{name!r}")
    d = cloned_voices_dir(root)
    if d.is_dir():
        for p in sorted(d.glob(f"{clean}.*")):
            if p.is_file():
                return p.resolve()
    raise PiperParamError(
        f"未找到克隆音色档案 {clean!r}。"
        f"请先上传一份参考音频（推荐 {CHATTERBOX_REF_MIN_S:.0f}-"
        f"{CHATTERBOX_REF_MAX_S:.0f} 秒的干净单人声）。"
    )


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


def _write_chatterbox_wav(out_path: Path, wav: Any, sample_rate: int) -> None:
    """把 Chatterbox 返回的张量写成 WAV。

    优先 torchaudio（模型本来就依赖它），失败再用 numpy + 标准库 wave。
    两条路都失败就直接抛 —— 宁可报错，也不留一个 0 字节的假 wav。
    """
    try:
        import torchaudio as ta  # type: ignore

        ta.save(str(out_path), wav, sample_rate)
        if out_path.is_file() and out_path.stat().st_size > 0:
            return
    except Exception:  # noqa: BLE001 — 回退到纯 numpy 写入
        LOG.debug("torchaudio save failed, falling back", exc_info=True)
    try:
        import numpy as np  # type: ignore

        arr = np.asarray(
            wav.detach().cpu().numpy() if hasattr(wav, "detach") else wav,
            dtype="float32",
        )
    except Exception as e:  # noqa: BLE001
        raise PiperVoiceError(f"无法读取 Chatterbox 输出的音频数据：{e}") from e
    if arr.ndim > 1:
        # (channels, samples) 或 (samples, channels) 都压成单声道/多声道交错
        arr = arr.squeeze()
    if arr.ndim > 1:
        arr = arr.T
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype("<i2")
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(pcm.shape[1] if pcm.ndim > 1 else 1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise PiperVoiceError("写出 WAV 失败：文件为空")


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

    # -- chatterbox（声音克隆） -------------------------------------------
    def _load_chatterbox(self, device: str = "auto") -> Any:
        """加载 Chatterbox 模型。依赖缺失时给出**准确的** pip 包名。"""
        try:
            from chatterbox.tts import ChatterboxTTS  # type: ignore
        except ImportError as e:
            raise PiperEngineMissing(
                f"Chatterbox 需要推理依赖：pip install {CHATTERBOX_PIP_NAME}"
                f"（原始错误：{e}）"
            ) from e
        if device == "auto":
            device = self._chatterbox_device()
        key = f"chatterbox:{device}"
        model = self._voices.get(key)
        if model is None:
            try:
                model = ChatterboxTTS.from_pretrained(device=device)
            except Exception as e:  # noqa: BLE001
                raise PiperVoiceError(
                    f"无法加载 Chatterbox（device={device}）：{e}"
                ) from e
            self._voices[key] = model
        return model

    @staticmethod
    def _chatterbox_device() -> str:
        """按可用性选 device。探测失败一律退回 cpu，绝不猜。"""
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                return "cuda"
            if getattr(torch.backends, "mps", None) is not None \
                    and torch.backends.mps.is_available():
                return "mps"
        except Exception:  # noqa: BLE001 — torch 没装 / 后端不可用
            LOG.debug("未检测到 CUDA/MPS，回退 CPU")
        return "cpu"

    def synthesize_chatterbox(
        self,
        voice_id: str,
        text: str,
        reference_audio: str | None = None,
        exaggeration: float = 0.5,
        cfg_weight: float = 0.5,
        device: str = "auto",
    ) -> dict[str, Any]:
        """用 Chatterbox 克隆音色合成语音。

        与 Kokoro/Piper 的关键差异：Chatterbox **必须有参考音频**，
        没有参考音频就没有"被克隆的音色"这回事。因此这里在加载模型之前
        先解析参考音频 —— 顺序反了会让用户等几分钟加载完才被告知缺素材。
        """
        name = chatterbox_voice_id(voice_id)
        if not name:
            raise PiperParamError(
                "Chatterbox 需要指定克隆档案名，写法：chatterbox:<名字>"
            )
        ref = resolve_reference_audio(self.data_root, name, reference_audio)

        for label, val in (("exaggeration", exaggeration), ("cfg_weight", cfg_weight)):
            if not isinstance(val, (int, float)) or not 0.0 <= float(val) <= 1.0:
                raise PiperParamError(f"{label} 必须在 [0, 1] 之间，当前为 {val!r}")

        model = self._load_chatterbox(device)
        used_device = "auto" if device == "auto" else device
        if used_device == "auto":
            used_device = self._chatterbox_device()

        try:
            wav = model.generate(
                text,
                audio_prompt_path=str(ref),
                exaggeration=float(exaggeration),
                cfg_weight=float(cfg_weight),
            )
        except TypeError as e:
            # 不同版本的 generate() 签名不同（早期版本不支持 exaggeration）。
            # 降级到最小签名再试一次，而不是直接把 TypeError 抛给用户。
            LOG.warning("chatterbox generate 签名不匹配，降级重试：%s", e)
            try:
                wav = model.generate(text, audio_prompt_path=str(ref))
            except Exception as e2:  # noqa: BLE001
                raise PiperVoiceError(f"Chatterbox 合成失败：{e2}") from e2
        except Exception as e:  # noqa: BLE001
            raise PiperVoiceError(f"Chatterbox 合成失败：{e}") from e

        if wav is None:
            raise PiperVoiceError("Chatterbox 未产出任何音频")

        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        out_dir = Path(self.data_root) / "tts" / "chatterbox"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{job_id}.wav"
        sample_rate = int(getattr(model, "sr", CHATTERBOX_SAMPLE_RATE) or CHATTERBOX_SAMPLE_RATE)
        _write_chatterbox_wav(out_path, wav, sample_rate)

        duration = _wav_duration_s(out_path) or 0.0
        return {
            "engine": "chatterbox",
            "job_id": job_id,
            "voice": voice_id,
            "path": str(out_path),
            "sample_rate": sample_rate,
            "duration_s": round(duration, 3),
            "size_bytes": out_path.stat().st_size if out_path.exists() else 0,
            "reference": str(ref),
            "exaggeration": float(exaggeration),
            "cfg_weight": float(cfg_weight),
            "device": used_device,
            # 水印事实随结果一起返回：用户有知情权，且这是官方刻意设计。
            "watermarked": True,
            "watermark_note": "输出含 Perth 神经水印（不可听、抗 MP3 压缩与剪辑）。",
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
        # Chatterbox 走克隆路径：必须有参考音频，且带水印。
        if is_chatterbox_voice(voice_id):
            return self.synthesize_chatterbox(voice_id, text)
        if len(text) > MAX_TEXT_LEN:
            raise PiperParamError(f"text exceeds {MAX_TEXT_LEN} characters")
        if length_scale is None:
            length_scale = 1.0
        length_scale = float(length_scale)
        if not (MIN_LENGTH_SCALE <= length_scale <= MAX_LENGTH_SCALE):
            raise PiperParamError(f"length_scale must be within [{MIN_LENGTH_SCALE},{MAX_LENGTH_SCALE}]")

        # **先确认引擎在，再去下载语音模型。**
        #
        # 顺序反了的后果很具体、也很容易被人忽略：``load_voice()`` 会联网
        # 下载几十 MB 的语音权重，而"Piper 引擎没装"这件事要等到后面的
        # ``import_piper()`` 才会暴露。于是引擎缺失本来应该是 503（明确的
        # "没装"），却会先撞上语音下载失败，变成 422 "download failed"——
        # 报错指向网络或文件，**真正的原因被完全掩盖**，用户还白等一次下载。
        #
        # 提前一次调用：装了就继续，没装立刻 503，不做任何下载。
        self.import_piper()

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
        "kokoro": {
            "engine": "kokoro",
            "installed": _module_available("kokoro"),
            "pip": "kokoro",
            "voices_hint": "af_heart 等预设音色（无克隆）",
        },
        "chatterbox": {
            "engine": "chatterbox",
            "installed": _module_available("chatterbox"),
            "pip": CHATTERBOX_PIP_NAME,
            "sample_rate": CHATTERBOX_SAMPLE_RATE,
            "ref_recommended_s": [CHATTERBOX_REF_MIN_S, CHATTERBOX_REF_MAX_S],
            "notes": CHATTERBOX_NOTES,
            "cloned_voices": list_cloned_voices(root),
        },
    }


def _module_available(name: str) -> bool:
    """探测一个可选推理包是否可导入。

    只做 import 探测，不实例化模型 —— capabilities 是给 UI 快速调用的，
    加载 0.5B 模型会让这个接口卡住几十秒。
    """
    try:
        import importlib

        importlib.import_module(name)
        return True
    except Exception:  # noqa: BLE001 — 未安装 / 依赖破损都算不可用
        return False
