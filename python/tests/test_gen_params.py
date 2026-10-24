"""Tests for app.gen_params — generation parameters in plain language."""
from __future__ import annotations

import pytest

from app.gen_params import (
    PARAMS,
    SCENARIOS,
    band,
    catalogue,
    default_values,
    describe_scenario,
    explain,
    explain_all,
    scenario_values,
)

# ---------------------------------------------------------------------------
# definitions
# ---------------------------------------------------------------------------

def test_every_param_has_plain_language_fields():
    for key, s in PARAMS.items():
        assert s.key == key
        for field in ("label", "meaning", "low_text", "high_text", "fix_text"):
            assert getattr(s, field), f"{key}.{field} 不能为空"


def test_defaults_within_bounds():
    for key, s in PARAMS.items():
        assert s.min <= s.default <= s.max, f"{key} 默认值越界"


def test_param_labels_are_plain_language_not_jargon():
    """标签必须是人话——不能把 temperature 直接当标签。"""
    for key, s in PARAMS.items():
        assert s.label != key, f"{key} 的标签不该是参数名本身"
        assert not s.label.isascii(), f"{key} 的标签应是中文人话"


def test_temperature_default_is_cherry_baseline():
    """Cherry Studio 的实测基线：temperature 默认 0.7。"""
    assert PARAMS["temperature"].default == 0.7


def test_essential_params_present():
    for key in ("temperature", "top_p", "context_length", "max_tokens", "tool_rounds"):
        assert key in PARAMS


# ---------------------------------------------------------------------------
# clamping / bands
# ---------------------------------------------------------------------------

def test_clamp_respects_bounds():
    s = PARAMS["temperature"]
    assert s.clamp(-5) == s.min
    assert s.clamp(999) == s.max
    assert s.clamp(0.5) == 0.5


def test_band_low_mid_high():
    s = PARAMS["temperature"]  # 0..2
    assert band(s, 0.0) == "低"
    assert band(s, 1.0) == "中"
    assert band(s, 2.0) == "高"


def test_band_clamps_out_of_range():
    s = PARAMS["temperature"]
    assert band(s, -100) == "低"
    assert band(s, 100) == "高"


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------

def test_explain_unknown_param_raises():
    with pytest.raises(KeyError):
        explain("nope", 1.0)


def test_explain_shape():
    e = explain("temperature", 0.7)
    for k in ("key", "label", "value", "default", "min", "max",
              "band", "meaning", "consequence", "fix_text", "is_default"):
        assert k in e


def test_explain_marks_default():
    assert explain("temperature", 0.7)["is_default"] is True
    assert explain("temperature", 1.5)["is_default"] is False


def test_explain_clamps_value():
    e = explain("temperature", 99)
    assert e["value"] == PARAMS["temperature"].max


def test_explain_low_band_shows_low_text():
    e = explain("temperature", 0.0)
    assert e["band"] == "低"
    assert e["consequence"] == PARAMS["temperature"].low_text


def test_explain_high_band_shows_high_text():
    e = explain("temperature", 2.0)
    assert e["band"] == "高"
    assert e["consequence"] == PARAMS["temperature"].high_text


def test_explain_mid_band_is_not_empty():
    e = explain("temperature", 1.0)
    assert e["band"] == "中"
    assert e["consequence"]


def test_explain_all_covers_every_param():
    out = explain_all()
    assert {o["key"] for o in out} == set(PARAMS)


def test_explain_all_applies_overrides():
    out = explain_all({"temperature": 0.1})
    temp = next(o for o in out if o["key"] == "temperature")
    assert temp["value"] == 0.1
    assert temp["is_default"] is False


def test_explain_all_defaults_when_empty():
    out = explain_all({})
    assert all(o["is_default"] for o in out)


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------

def test_scenario_values_unknown_raises():
    with pytest.raises(KeyError):
        scenario_values("nope")


def test_scenario_fills_unspecified_with_defaults():
    v = scenario_values("coding")
    assert set(v) == set(PARAMS)
    # coding 没指定 tool_rounds → 用默认值
    assert v["tool_rounds"] == PARAMS["tool_rounds"].default


def test_coding_is_deterministic():
    v = scenario_values("coding")
    assert v["temperature"] <= 0.3, "写代码要低温度，保证可复现"


def test_creative_is_high_temperature():
    v = scenario_values("creative")
    assert v["temperature"] >= 1.0


def test_extraction_is_zero_temperature():
    """精确抽取必须零发挥。"""
    assert scenario_values("extraction")["temperature"] == 0.0


def test_translation_is_faithful():
    assert scenario_values("translation")["temperature"] <= 0.4


def test_chat_matches_defaults():
    """日常对话档应与默认值一致——新用户不用改就能用。"""
    v = scenario_values("chat")
    for key in ("temperature", "top_p", "max_tokens"):
        assert v[key] == PARAMS[key].default


def test_all_scenario_values_within_bounds():
    for name in SCENARIOS:
        v = scenario_values(name)
        for key, val in v.items():
            s = PARAMS[key]
            assert s.min <= val <= s.max, f"{name}.{key}={val} 越界"


def test_describe_scenario_shape():
    d = describe_scenario("coding")
    assert set(d) >= {"key", "label", "hint", "values", "changed"}


def test_describe_scenario_lists_what_changed():
    d = describe_scenario("coding")
    keys = {c["key"] for c in d["changed"]}
    assert "temperature" in keys
    for c in d["changed"]:
        assert c["from"] == PARAMS[c["key"]].default
        assert c["to"] != c["from"]
        assert c["why"]


def test_describe_scenario_unknown_raises():
    with pytest.raises(KeyError):
        describe_scenario("nope")


# ---------------------------------------------------------------------------
# catalogue (frontend payload)
# ---------------------------------------------------------------------------

def test_catalogue_shape():
    c = catalogue()
    assert len(c["params"]) == len(PARAMS)
    assert len(c["scenarios"]) == len(SCENARIOS)
    assert c["note"]


def test_catalogue_params_carry_bounds_for_sliders():
    for p in catalogue()["params"]:
        assert p["min"] < p["max"]
        assert p["min"] <= p["default"] <= p["max"]
        assert p["step"] > 0


def test_catalogue_note_encourages_using_defaults():
    """Cherry Studio 的做法：不确定就先用默认值。"""
    assert "默认值" in catalogue()["note"]


def test_default_values_match_specs():
    d = default_values()
    assert d == {k: s.default for k, s in PARAMS.items()}
