"""TTS / ASR 引擎增强测试：Kokoro 与 SenseVoice 接入。

核心风险是**引擎误判**：用 Piper 的路径去加载 Kokoro 音色（或用
faster-whisper 去加载 SenseVoice 权重），报错信息会完全指不到真正原因。
所以判定函数的边界是本文件的重点。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import asr_runtime as a  # noqa: E402
from app import piper_runtime as p  # noqa: E402


# ---------------------------------------------------------------------------
# 1. TTS：Kokoro 音色判定
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("voice", [
    "kokoro:af_heart",   # 显式前缀
    "af_heart",          # 裸音色名（美式英语女声）
    "zf_xiaobei",        # 普通话女声
    "am_michael",        # 美式英语男声
])
def test_kokoro_voice_recognised(voice):
    assert p.is_kokoro_voice(voice) is True


@pytest.mark.parametrize("voice", [
    "en_US-libritts_r-medium",   # Piper 标准音色
    "zh_CN-huayan-medium",       # Piper 中文音色
    "",
    "random",
    "abc_def",                   # 首段 3 字符，不是 Kokoro 形态
    "a_heart",                   # 首段 1 字符
])
def test_piper_voice_not_mistaken_for_kokoro(voice):
    """Piper 音色绝不能被判成 Kokoro —— 判错会用错引擎加载，报错无法定位。"""
    assert p.is_kokoro_voice(voice) is False


def test_kokoro_prefix_stripped():
    assert p.kokoro_voice_id("kokoro:af_heart") == "af_heart"
    assert p.kokoro_voice_id("af_heart") == "af_heart"


def test_kokoro_sample_rate_is_24k():
    """Kokoro 输出 24kHz，与 Piper 的 22kHz 不同，不能混用。"""
    assert p.KOKORO_SAMPLE_RATE == 24000


def test_kokoro_lang_codes_include_chinese():
    """普通话必须支持 —— 这是本项目的主力场景。"""
    assert "z" in p.KOKORO_LANG_CODES
    assert "普通话" in p.KOKORO_LANG_CODES["z"]


# ---------------------------------------------------------------------------
# 2. ASR：引擎分流
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("repo", [
    "FunAudioLLM/SenseVoiceSmall",
    "iic/SenseVoiceSmall",
    "Qwen/Qwen3-ASR-0.6B",
    "Qwen/Qwen3-ASR-1.7B",
])
def test_sensevoice_repos_route_to_sensevoice(repo):
    assert a.engine_for_model(repo) == a.ENGINE_SENSEVOICE


@pytest.mark.parametrize("repo", [
    "Systran/faster-whisper-small",
    "Systran/faster-whisper-large-v3",
    "mobiuslabsgmbh/faster-whisper-large-v2",
    "",
])
def test_whisper_repos_stay_on_whisper(repo):
    """未登记的按 Whisper 处理 —— 保持既有行为，不能破坏现有部署。"""
    assert a.engine_for_model(repo) == a.ENGINE_WHISPER


def test_engine_detection_is_case_insensitive():
    assert a.engine_for_model("funaudiollm/sensevoicesmall") == a.ENGINE_SENSEVOICE


def test_engine_detection_by_local_dir():
    """本地目录只有最后一段时也要能识别。"""
    assert a.engine_for_model("SenseVoiceSmall") == a.ENGINE_SENSEVOICE


def test_sensevoice_languages_include_chinese_and_cantonese():
    langs = a.SENSEVOICE_LANGS
    assert "zh" in langs
    assert "yue" in langs, "粤语是 SenseVoice 相对 Whisper 的关键优势"


def test_capabilities_reports_both_engines():
    """UI 要能分别看到两个引擎是否可用。"""
    caps = a.capabilities()
    assert "engines" in caps
    assert a.ENGINE_WHISPER in caps["engines"]
    assert a.ENGINE_SENSEVOICE in caps["engines"]


def test_capabilities_lists_missing_deps_when_absent():
    """依赖缺失时必须列出缺什么，而不是只给个 False。"""
    caps = a.capabilities()
    sv = caps["engines"][a.ENGINE_SENSEVOICE]
    if not sv["installed"]:
        assert sv["missing_deps"], "未安装却没说缺哪个依赖"


# ---------------------------------------------------------------------------
# 3. SenseVoice 文本提取
# ---------------------------------------------------------------------------

def test_sensevoice_text_strips_tags():
    """funasr 返回带 <|zh|><|NEUTRAL|><|Speech|> 标签，必须剥掉。"""
    res = [{"text": "<|zh|><|NEUTRAL|><|Speech|><|woitn|>你好世界"}]
    assert a.AsrManager._extract_sensevoice_text(res) == "你好世界"


def test_sensevoice_text_handles_dict():
    assert a.AsrManager._extract_sensevoice_text({"text": "hello"}) == "hello"


def test_sensevoice_text_handles_empty():
    assert a.AsrManager._extract_sensevoice_text([]) == ""
    assert a.AsrManager._extract_sensevoice_text(None) == ""


def test_sensevoice_text_plain_string():
    assert a.AsrManager._extract_sensevoice_text("plain") == "plain"


# ---------------------------------------------------------------------------
# 4. 音频落地
# ---------------------------------------------------------------------------

def test_materialise_audio_from_bytes(tmp_path):
    data = b"RIFF" + b"\x00" * 100
    out = a.AsrManager._materialise_audio(data)
    assert Path(out).is_file()
    assert Path(out).read_bytes() == data


def test_materialise_audio_passthrough_path(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    assert a.AsrManager._materialise_audio(str(f)) == f
