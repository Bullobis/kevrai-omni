"""Per-model generation-parameter defaults (the Local Dream lesson).

Local Dream（``data/ModelConfig.kt`` + ``data/GenerationDefaults.kt``）的关键设计：

* **三级优先级，逐字段合并**：内置代码默认值 > 模型目录 ``config.json``
  > 全局默认值。null 表示"这一级没指定，往下一级走"。
* **外部配置必须校验**：scheduler 只认白名单，steps/cfg 一律 ``coerceIn``
  到合法区间，解析失败返回 null（不崩、不猜）。

对 Kevrai 的意义：参数人话化（``gen_params``）目前只有"全局默认 + 场景档位"
两级。模型自带的推荐参数（比如某个视频模型官方建议 steps=20、CFG=3.5）
没有落脚点，用户只能用全局值硬套。

这里补上第三级，并沿用同一个核心约束：**外部值一律不信任**。

* 数值：clamp 到参数合法区间，越界的值不静默丢弃也不照单全收——
  clamp 后记一条 warning，让 UI 能解释"你要的 80 步被压到了 50 步"。
* 字符串：只认白名单（如 scheduler），未知值丢弃并记 warning。
* 解析失败：整个 config 视为"没有提供"，回落到下一级。绝不抛异常。

纯逻辑、零重型依赖（只用标准库 json），可离线单测。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .gen_params import PARAMS, SCENARIOS

log = logging.getLogger("kevrai.model_param_defaults")

#: 模型目录里可选的自带配置文件名。
CONFIG_FILENAME = "config.json"

#: 非数值型参数的合法取值白名单。key 是参数名，value 是允许值集合。
#: 不在 PARAMS 里的参数（如 scheduler）也在这里声明。
_ENUM_PARAMS: dict[str, tuple[str, ...]] = {
    "scheduler": (
        "dpm", "dpm_karras", "dpm_sde", "dpm_sde_karras",
        "euler", "euler_karras", "euler_a", "euler_a_karras",
        "lcm",
    ),
}


@dataclass
class DefaultsResolution:
    """一次默认值解析的完整结果，UI 可直接渲染。"""

    values: dict[str, float] = field(default_factory=dict)
    #: 每个值来自哪一级："model" / "scenario" / "global"
    sources: dict[str, str] = field(default_factory=dict)
    #: 被修正过的项，形如 {"steps": {"asked": 80.0, "used": 50.0}}
    clamped: dict[str, dict[str, float]] = field(default_factory=dict)
    #: 被丢弃的项（未知枚举值），形如 {"scheduler": "foo"}
    rejected: dict[str, Any] = field(default_factory=dict)
    #: 解析失败原因；None 表示 config 存在且读成功了（或压根没有 config）
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": dict(self.values),
            "sources": dict(self.sources),
            "clamped": {k: dict(v) for k, v in self.clamped.items()},
            "rejected": dict(self.rejected),
            "error": self.error,
        }


def _coerce_numeric(key: str, raw: Any, res: DefaultsResolution) -> float | None:
    """把一个外部数值收敛到合法区间。越界不丢弃，只 clamp 并记账。"""
    spec = PARAMS.get(key)
    if spec is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        res.rejected[key] = raw
        return None
    # NaN / inf 不能进 JSON，也不能进推理参数。
    if v != v or v in (float("inf"), float("-inf")):  # noqa: PLR0124
        res.rejected[key] = raw
        return None
    clamped = spec.clamp(v)
    if clamped != v:
        res.clamped[key] = {"asked": v, "used": clamped}
    return clamped


def _coerce_enum(key: str, raw: Any, res: DefaultsResolution) -> str | None:
    """枚举型参数：只认白名单，未知值丢弃并记账。"""
    allowed = _ENUM_PARAMS.get(key)
    if not allowed:
        res.rejected[key] = raw
        return None
    s = str(raw).strip().lower()
    if s in allowed:
        return s
    res.rejected[key] = raw
    return None


def read_model_config(model_dir: str | os.PathLike[str] | None) -> dict[str, Any] | None:
    """读取模型目录里可选的 ``config.json``。

    不存在 / 不可读 / 不是对象 → 返回 None（视为"没有提供"），绝不抛异常。
    """
    if not model_dir:
        return None
    path = Path(model_dir).expanduser() / CONFIG_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - 外部文件，任何错误都只降级
        log.warning("model_param_defaults: unreadable config %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        log.warning("model_param_defaults: %s is not a JSON object", path)
        return None
    return data


def normalize_config(raw: dict[str, Any] | None) -> tuple[dict[str, Any], DefaultsResolution]:
    """把一份原始 config 收敛成「可信的推荐值」。

    返回 ``(clean, res)``：clean 是可直接使用的字段字典，
    res 记录被 clamp / 被拒绝的项，供 UI 解释。
    """
    res = DefaultsResolution()
    clean: dict[str, Any] = {}
    if not raw:
        return clean, res

    for key, value in raw.items():
        if value is None:
            continue  # null = 这一级没指定，交给下一级
        if key in PARAMS:
            got = _coerce_numeric(key, value, res)
            if got is not None:
                clean[key] = got
        elif key in _ENUM_PARAMS:
            enum_val = _coerce_enum(key, value, res)
            if enum_val is not None:
                clean[key] = enum_val
        else:
            # 不认识的参数：不进 clean，但记账，方便排查"我写的怎么没生效"。
            res.rejected[key] = value
    return clean, res


def resolve_defaults(
    *,
    scenario: str | None = None,
    model_config: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> DefaultsResolution:
    """解析最终参数值。

    优先级（逐字段，高者胜）：**用户覆盖 > 模型自带 > 场景 > 全局默认**。

    * ``scenario`` 为 None 时跳过场景层。
    * ``model_config`` 可以是原始 config（内部会先 normalize）。
    """
    res = DefaultsResolution()

    # 第 1 层：全局默认
    values: dict[str, Any] = {k: s.default for k, s in PARAMS.items()}
    sources: dict[str, str] = {k: "global" for k in PARAMS}

    # 第 2 层：场景档位
    #
    # 注意：这里刻意读 ``SCENARIOS[key].values``（场景真正声明的那几个字段），
    # 而不是 ``scenario_values()``——后者返回的是"默认值打底 + 场景覆盖"的
    # 完整字典，会把没声明的字段也标成 scenario，source 标注就失真了。
    if scenario:
        sc = SCENARIOS.get(scenario)
        if sc is None:
            # 未知场景：不猜，也不影响其余层。调用方应先校验场景名。
            res.error = f"unknown scenario: {scenario!r}"
        else:
            for k, v in sc.values.items():
                if k in PARAMS:
                    values[k] = v
                    sources[k] = "scenario"

    # 第 3 层：模型自带推荐
    clean, cfg_res = normalize_config(model_config)
    for k, v in clean.items():
        if k in PARAMS or k in _ENUM_PARAMS:
            values[k] = v
            sources[k] = "model"
    res.clamped = cfg_res.clamped
    res.rejected = cfg_res.rejected

    # 第 4 层：用户显式覆盖（同样要校验，不能因为是用户写的就免检）
    ov_clean, ov_res = normalize_config(overrides)
    for k, ck in ov_clean.items():
        values[k] = ck
        sources[k] = "override"
    for rk, rv in ov_res.clamped.items():
        res.clamped[rk] = rv
    for rk, rv in ov_res.rejected.items():
        res.rejected[rk] = rv

    res.values = values
    res.sources = sources
    return res


def describe_resolution(res: DefaultsResolution) -> list[str]:
    """把 clamp / 拒绝情况翻成人话，供 UI 直接展示。"""
    out: list[str] = []
    for key, info in res.clamped.items():
        spec = PARAMS.get(key)
        label = spec.label if spec else key
        out.append(
            f"{label}：填的 {info['asked']:g} 超出范围，已压到 {info['used']:g}"
        )
    for key, value in res.rejected.items():
        spec = PARAMS.get(key)
        label = spec.label if spec else key
        out.append(f"{label}：不认识这个值（{value!r}），已忽略")
    return out
