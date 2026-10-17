"""Generation parameters in plain language (the Cherry Studio lesson).

Cherry Studio 的关键做法：参数不配术语词典，而是给**人话标签 + 场景档位 + 改了会怎样**。
它的 temperature 说明直接绑定低/中/高区间和"代码 / 日常对话 / 创意"场景，并给出默认 0.7；
Top-P 也给出 0.1–0.3 / 0.4–0.6 / 0.7–1.0 的任务建议。

这个模块把同样的思路落到 Kevrai Omni：

* 每个参数都有一句**人话说明**、**两端描述**（调小会怎样 / 调大会怎样）、
  以及**改坏怎么办**。
* 提供**场景档位**（代码 / 日常对话 / 创意写作 / 翻译 / 精确抽取），
  一键套用一整套参数，而不是让用户逐个滑块试。
* 任何取值都会返回一段**后果提示**——用户能判断"这个值对我意味着什么"，
  而不只是看懂词义。

纯逻辑、零依赖：不碰 torch，不做 I/O，方便前端、体检脚本与测试共用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# 参数定义
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamSpec:
    """一个生成参数的完整人话描述。"""

    key: str
    label: str            # 人话名字
    default: float        # 默认值
    min: float
    max: float
    low_text: str         # 调到最小意味着什么
    high_text: str        # 调到最大意味着什么
    meaning: str          # 一句话解释
    fix_text: str         # 调坏了怎么办
    step: float = 0.01

    def clamp(self, value: float) -> float:
        return max(self.min, min(self.max, float(value)))


PARAMS: dict[str, ParamSpec] = {
    "temperature": ParamSpec(
        key="temperature",
        label="有多大创造力",
        default=0.7,
        min=0.0,
        max=2.0,
        low_text="更稳定：每次答案差不多，适合代码、 factual 问答",
        high_text="更有创意：答案更多变，适合写故事、起名字、头脑风暴",
        meaning="控制「敢不敢选不太常见的词」。越低越保守，越高越跳脱。",
        fix_text="答案变得乱七八糟或离题 → 调回 0.7 以下；"
                 "答案千篇一律没新意 → 往上加 0.1～0.2",
    ),
    "top_p": ParamSpec(
        key="top_p",
        label="选词范围有多宽",
        default=0.9,
        min=0.0,
        max=1.0,
        low_text="更集中：只在最有把握的几个词里挑，输出更可预测",
        high_text="更多样：候选词更多，表达更丰富但也可能跑偏",
        meaning="每一步从多少个候选词里挑。和创造力类似，但更偏「候选池宽度」。",
        fix_text="一般不用动。想让输出更严谨可降到 0.7；"
                 "注意别把 temperature 和它同时调极端",
    ),
    "context_length": ParamSpec(
        key="context_length",
        label="能记住多少内容",
        default=4096,
        min=512,
        max=131072,
        step=1.0,
        low_text="记得少：省显存、响应快，长文档会被截断",
        high_text="记得多：能处理长文、长对话，但吃显存且变慢",
        meaning="模型一次能「看到」的字数上限，包含你的问题和它的回答。",
        fix_text="报错「显存不足」或特别慢 → 调小；"
                 "长文档被截掉 → 调大，或分段提问",
    ),
    "max_tokens": ParamSpec(
        key="max_tokens",
        label="最多一次写多长",
        default=1024,
        min=16,
        max=32768,
        step=1.0,
        low_text="写得短：省时间，但可能被截断",
        high_text="写得长：能一次写完长文，但耗时更久",
        meaning="单次回答的字数上限。到点就停，哪怕没写完。",
        fix_text="回答总是半截 → 调大；"
                 "嫌等太久 → 调小，或让它分几次写",
    ),
    "tool_rounds": ParamSpec(
        key="tool_rounds",
        label="Agent 最多连续操作多少步",
        default=8,
        min=1,
        max=64,
        step=1.0,
        low_text="步数少：快，但复杂任务可能做不完",
        high_text="步数多：能处理复杂任务，但可能绕远路、耗时久",
        meaning="Agent 模式下，允许它连续调用工具的次数上限。",
        fix_text="任务总做一半 → 调大；"
                 "它一直在瞎转圈 → 调小，并在提示词里说清楚目标",
    ),
}

# ---------------------------------------------------------------------------
# 场景档位
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    hint: str
    values: dict[str, float]


SCENARIOS: dict[str, Scenario] = {
    "coding": Scenario(
        key="coding",
        label="写代码",
        hint="要准确、可复现，不要发挥",
        values={"temperature": 0.2, "top_p": 0.7, "max_tokens": 2048},
    ),
    "chat": Scenario(
        key="chat",
        label="日常对话",
        hint="自然、稳妥，兼顾一点变化",
        values={"temperature": 0.7, "top_p": 0.9, "max_tokens": 1024},
    ),
    "creative": Scenario(
        key="creative",
        label="创意写作",
        hint="要有新意，允许跑偏一点",
        values={"temperature": 1.0, "top_p": 0.95, "max_tokens": 2048},
    ),
    "translation": Scenario(
        key="translation",
        label="翻译",
        hint="忠实原文，不添油加醋",
        values={"temperature": 0.3, "top_p": 0.8, "max_tokens": 2048},
    ),
    "extraction": Scenario(
        key="extraction",
        label="精确抽取",
        hint="从材料里找信息，要零发挥",
        values={"temperature": 0.0, "top_p": 0.5, "max_tokens": 1024},
    ),
}


def default_values() -> dict[str, float]:
    return {k: s.default for k, s in PARAMS.items()}


def scenario_values(key: str) -> dict[str, float]:
    """场景给出的参数，未指定的沿用默认值。"""
    sc = SCENARIOS.get(key)
    if sc is None:
        raise KeyError(f"unknown scenario: {key!r}")
    out = default_values()
    out.update(sc.values)
    return out


# ---------------------------------------------------------------------------
# 人话解释
# ---------------------------------------------------------------------------


def band(spec: ParamSpec, value: float) -> str:
    """把取值映射到 低 / 中 / 高 三档。"""
    v = spec.clamp(value)
    span = spec.max - spec.min
    if span <= 0:
        return "中"
    ratio = (v - spec.min) / span
    if ratio < 1 / 3:
        return "低"
    if ratio < 2 / 3:
        return "中"
    return "高"


def explain(key: str, value: float) -> dict[str, Any]:
    """解释某个参数取某个值时意味着什么。

    返回人话标签、所处档位、两端后果与"改坏怎么办"。
    """
    spec = PARAMS.get(key)
    if spec is None:
        raise KeyError(f"unknown param: {key!r}")
    v = spec.clamp(value)
    b = band(spec, v)
    if b == "低":
        consequence = spec.low_text
    elif b == "高":
        consequence = spec.high_text
    else:
        consequence = f"中间档：{spec.low_text.split('：')[0]}与{spec.high_text.split('：')[0]}的平衡点"
    return {
        "key": key,
        "label": spec.label,
        "value": v,
        "default": spec.default,
        "min": spec.min,
        "max": spec.max,
        "band": b,
        "meaning": spec.meaning,
        "consequence": consequence,
        "fix_text": spec.fix_text,
        "is_default": abs(v - spec.default) < 1e-9,
    }


def explain_all(values: dict[str, float] | None = None) -> list[dict[str, Any]]:
    v = dict(default_values())
    v.update(values or {})
    return [explain(k, v.get(k, s.default)) for k, s in PARAMS.items()]


def describe_scenario(key: str) -> dict[str, Any]:
    """场景档位 + 它把哪些参数改成了什么 + 为什么会这样。"""
    sc = SCENARIOS.get(key)
    if sc is None:
        raise KeyError(f"unknown scenario: {key!r}")
    values = scenario_values(key)
    changed = [
        {
            "key": k,
            "label": PARAMS[k].label,
            "from": PARAMS[k].default,
            "to": val,
            "why": explain(k, val)["consequence"],
        }
        for k, val in sc.values.items()
        if k in PARAMS
    ]
    return {
        "key": sc.key,
        "label": sc.label,
        "hint": sc.hint,
        "values": values,
        "changed": changed,
    }


def catalogue() -> dict[str, Any]:
    """给前端用的全量描述。"""
    return {
        "params": [
            {
                "key": s.key,
                "label": s.label,
                "default": s.default,
                "min": s.min,
                "max": s.max,
                "step": s.step,
                "meaning": s.meaning,
                "low_text": s.low_text,
                "high_text": s.high_text,
                "fix_text": s.fix_text,
            }
            for s in PARAMS.values()
        ],
        "scenarios": [
            {"key": s.key, "label": s.label, "hint": s.hint}
            for s in SCENARIOS.values()
        ],
        "note": (
            "参数按「人话」呈现：先看场景档位，不行再微调单个滑块。"
            "不确定就用默认值——这是 Cherry Studio 验证过的做法："
            "先跑一段时间，发现不足再回来调。"
        ),
    }
