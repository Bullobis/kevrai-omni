"""音乐引擎增强测试：ACE-Step 接入与许可如实标注。

核心是两件事：
1. ACE-Step 必须能被**正确识别**（识别错了会走 MusicGen 的加载路径，
   而报错信息完全指不到真正原因）
2. 许可信息必须如实，未登记的模型绝不能默认成"可商用"
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import music_runtime as m  # noqa: E402

# ---------------------------------------------------------------------------
# 1. ACE-Step 识别
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src", [
    "ACE-Step/Ace-Step1.5",   # HF 上的真实写法
    "ace-step-1.5",           # catalog id / 本地目录
    "ACE_Step/x",             # 下划线变体
    "/models/ACE-Step-1.5",   # 本地绝对路径
    "ace-step",               # 最短形态
])
def test_ace_step_recognised(src):
    assert m._is_ace_step(src) is True


@pytest.mark.parametrize("src", [
    "facebook/musicgen-small",
    "musicgen-large",
    "MiniMax/MiniMax-Music3",
    "",
    "some/other-model",
])
def test_non_ace_not_recognised(src):
    assert m._is_ace_step(src) is False


def test_ace_step_is_case_insensitive():
    """大小写不敏感 —— HF 官方写法就是首字母大写的 ACE-Step。"""
    assert m._is_ace_step("ACE-Step/Ace-Step1.5")
    assert m._is_ace_step("ace-step/Ace-Step1.5")


def test_windows_path_recognised():
    """Windows 反斜杠路径也要能识别。"""
    assert m._is_ace_step("C:\\models\\ACE-Step-1.5")


# ---------------------------------------------------------------------------
# 2. 许可如实标注
# ---------------------------------------------------------------------------

def test_ace_step_is_commercial():
    info = m.license_info("ace-step-1.5")
    assert info["license"] == "MIT"
    assert info["commercial"] == "true"


def test_musicgen_is_noncommercial():
    """MusicGen 是 CC BY-NC —— 不能因为它"能跑"就说它能商用。"""
    for mid in ("musicgen-small", "musicgen-medium",
                "musicgen-large", "musicgen-stereo"):
        info = m.license_info(mid)
        assert info["license"] == "CC BY-NC 4.0"
        assert info["commercial"] == "false"


def test_music3_warns_about_hardware_and_license():
    """Music3 的门槛必须写清楚：非商用 + 约 57GB + 双 GPU。"""
    info = m.license_info("minimax-music3")
    assert info["commercial"] == "false"
    assert "57GB" in info["note"]
    assert "双 GPU" in info["note"]


def test_music3_docs_disagreement_is_noted():
    """HF 卡与 GitHub README 对最低配置说法不一致 —— 这是事实，不能抹平。"""
    note = m.license_info("minimax-music3")["note"]
    assert "不一致" in note


def test_unknown_model_is_unknown_not_commercial():
    """未登记模型绝不能默认成可商用 —— 那是法律风险。"""
    info = m.license_info("totally-unknown-model")
    assert info["commercial"] == "unknown"
    assert info["license"] == "unknown"


def test_empty_model_id_is_unknown():
    assert m.license_info("")["commercial"] == "unknown"


def test_license_info_by_repo_slug():
    """用仓库全名查询也要命中，不能只认 catalog id。"""
    assert m.license_info("ACE-Step/Ace-Step1.5")["commercial"] == "true"


# ---------------------------------------------------------------------------
# 3. 时长上限按模型区分
# ---------------------------------------------------------------------------

def test_ace_step_allows_longer_than_musicgen():
    assert m.MAX_DURATION_ACE_STEP_S > m.MAX_DURATION_MUSICGEN_S


def test_ace_step_max_is_ten_minutes():
    """官方支持到 10 分钟。"""
    assert m.MAX_DURATION_ACE_STEP_S == 600


def test_duration_rejected_beyond_model_limit():
    mgr = m.MusicManager()
    with pytest.raises(m.MusicParamError) as ei:
        mgr.synthesize("musicgen-small", "test", duration_s=500)
    assert "60" in str(ei.value)


def test_duration_within_ace_limit_accepted_by_validation():
    """ACE-Step 的 500 秒应当通过校验（不因 MusicGen 的 60 秒上限被拒）。

    这里只验证"校验通过"，实际生成会因缺依赖失败 —— 那属于另一层。
    """
    mgr = m.MusicManager()
    with pytest.raises(Exception) as ei:
        mgr.synthesize("ace-step-1.5", "test", duration_s=500)
    msg = str(ei.value)
    # 不能是因为"必须在 1-60 秒之间"这类校验错误
    assert "必须在" not in msg, f"ACE-Step 被 MusicGen 的时长限制误伤: {msg}"


# ---------------------------------------------------------------------------
# 4. 模型登记完整性
# ---------------------------------------------------------------------------

def test_ace_step_registered():
    assert "ace-step-1.5" in m.KNOWN_MODELS
    assert m.KNOWN_MODELS["ace-step-1.5"] == "ACE-Step/Ace-Step1.5"


def test_every_known_model_has_license_entry():
    """每个登记模型都必须有许可条目 —— 没有就是漏标，等于误导。"""
    for mid in m.KNOWN_MODELS:
        assert mid in m.LICENSE_WARNINGS, f"{mid} 缺少许可标注"


def test_capabilities_reports_licenses_per_model():
    """每个模型返回体里必须自带许可 —— UI 不可能只展示模型不展示许可。"""
    caps = m.capabilities()
    by_id = {x["id"]: x for x in caps["models"]}
    assert by_id["ace-step-1.5"]["commercial"] == "true"
    assert by_id["musicgen-small"]["commercial"] == "false"


def test_capabilities_reports_both_duration_ranges():
    """MusicGen 与 ACE-Step 的时长上限不同，必须分别给出。"""
    caps = m.capabilities()
    assert caps["duration_range_s"][1] == m.MAX_DURATION_MUSICGEN_S
    assert caps["duration_range_ace_step_s"][1] == m.MAX_DURATION_ACE_STEP_S
