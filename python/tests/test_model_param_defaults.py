"""Per-model parameter defaults (Local Dream 三级优先级) 的回归测试。

重点守三条线：
  1. 优先级逐字段生效，且 source 如实标注；
  2. 外部值一律不信任——越界 clamp、未知枚举丢弃、NaN/inf 拒绝；
  3. 任何坏输入都不抛异常，且不静默丢弃（要有 rejected 记账）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import model_param_defaults as m  # noqa: E402
from app.gen_params import PARAMS  # noqa: E402


def test_global_defaults_when_nothing_provided() -> None:
    r = m.resolve_defaults()
    assert r.sources == {k: "global" for k in PARAMS}
    assert r.values["temperature"] == PARAMS["temperature"].default
    assert r.clamped == {} and r.rejected == {}


def test_scenario_overrides_global_per_field() -> None:
    """场景只覆盖它声明的字段，其余保持全局。"""
    r = m.resolve_defaults(scenario="coding")
    assert r.sources["temperature"] == "scenario"
    # coding 没声明 context_length 时它应留在 global
    declared = {"temperature", "top_p", "max_tokens"}
    for k in declared:
        assert r.sources[k] == "scenario", k
    for k in set(PARAMS) - declared:
        assert r.sources[k] == "global", k


def test_model_config_beats_scenario() -> None:
    """模型自带推荐 > 场景 > 全局。"""
    r = m.resolve_defaults(
        scenario="coding",
        model_config={"temperature": 1.4},
    )
    assert r.sources["temperature"] == "model"
    assert r.values["temperature"] == 1.4
    # 未被模型覆盖的字段仍走场景
    assert r.sources["max_tokens"] == "scenario"


def test_override_beats_model_config() -> None:
    r = m.resolve_defaults(
        scenario="coding",
        model_config={"temperature": 1.4},
        overrides={"temperature": 0.1},
    )
    assert r.values["temperature"] == 0.1
    assert r.sources["temperature"] == "override"


def test_out_of_range_is_clamped_not_dropped() -> None:
    """越界不静默丢弃，也不照单全收：clamp 后记账。"""
    hi = PARAMS["temperature"].max
    r = m.resolve_defaults(model_config={"temperature": 999.0})
    assert r.values["temperature"] == hi
    assert r.clamped["temperature"]["asked"] == 999.0
    assert r.clamped["temperature"]["used"] == hi


def test_negative_below_min_is_clamped_up() -> None:
    lo = PARAMS["temperature"].min
    r = m.resolve_defaults(model_config={"temperature": -5.0})
    assert r.values["temperature"] == lo
    assert r.clamped["temperature"]["asked"] == -5.0


def test_nan_and_inf_are_rejected() -> None:
    """NaN/inf 绝不能进推理参数，也不能进 JSON。"""
    for bad in (float("nan"), float("inf"), float("-inf")):
        r = m.resolve_defaults(model_config={"temperature": bad})
        assert "temperature" in r.rejected
        assert r.values["temperature"] == PARAMS["temperature"].default


def test_non_numeric_string_is_rejected() -> None:
    r = m.resolve_defaults(model_config={"temperature": "很热"})
    assert "temperature" in r.rejected
    assert r.values["temperature"] == PARAMS["temperature"].default


def test_enum_whitelist() -> None:
    r = m.resolve_defaults(model_config={"scheduler": "dpm_karras"})
    assert r.values["scheduler"] == "dpm_karras"

    bad = m.resolve_defaults(model_config={"scheduler": "不存在的采样器"})
    assert bad.rejected.get("scheduler") == "不存在的采样器"
    assert "scheduler" not in bad.values


def test_enum_is_case_insensitive() -> None:
    r = m.resolve_defaults(model_config={"scheduler": "DPM_Karras"})
    assert r.values["scheduler"] == "dpm_karras"


def test_unknown_key_is_recorded_not_silently_kept() -> None:
    r = m.resolve_defaults(model_config={"这参数不存在": 1})
    assert "这参数不存在" in r.rejected
    assert "这参数不存在" not in r.values


def test_null_means_not_specified_falls_through() -> None:
    """null 表示这一级没指定，必须落到下一级（Local Dream 的核心约定）。"""
    r = m.resolve_defaults(scenario="coding", model_config={"temperature": None})
    assert r.sources["temperature"] == "scenario"


def test_unknown_scenario_does_not_raise() -> None:
    r = m.resolve_defaults(scenario="压根没有这个场景")
    assert r.error is not None
    assert r.values["temperature"] == PARAMS["temperature"].default


def test_read_model_config_missing_dir_returns_none(tmp_path: Path) -> None:
    assert m.read_model_config(tmp_path / "nope") is None
    assert m.read_model_config(None) is None


def test_read_model_config_bad_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / m.CONFIG_FILENAME).write_text("{not json", encoding="utf-8")
    assert m.read_model_config(tmp_path) is None


def test_read_model_config_non_object_returns_none(tmp_path: Path) -> None:
    (tmp_path / m.CONFIG_FILENAME).write_text("[1,2,3]", encoding="utf-8")
    assert m.read_model_config(tmp_path) is None


def test_read_model_config_roundtrip(tmp_path: Path) -> None:
    (tmp_path / m.CONFIG_FILENAME).write_text(
        json.dumps({"temperature": 0.25}), encoding="utf-8"
    )
    raw = m.read_model_config(tmp_path)
    assert raw == {"temperature": 0.25}
    r = m.resolve_defaults(model_config=raw)
    assert r.values["temperature"] == 0.25
    assert r.sources["temperature"] == "model"


def test_describe_resolution_is_human_readable() -> None:
    r = m.resolve_defaults(model_config={"temperature": 999.0, "scheduler": "zzz"})
    lines = m.describe_resolution(r)
    assert len(lines) == 2
    assert any("压到" in s for s in lines)
    assert any("不认识" in s for s in lines)


def test_to_dict_is_json_safe() -> None:
    r = m.resolve_defaults(scenario="creative", model_config={"temperature": 0.5})
    json.dumps(r.to_dict(), ensure_ascii=False)  # 不允许抛异常
