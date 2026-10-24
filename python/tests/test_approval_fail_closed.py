"""审批策略的失败方向（fail-closed）回归测试。

守的核心：这是一个**审批闸门**，读不回存档值时必须倒向"要确认"的一侧，
绝不能静默回落成"全部自动放行"。

对标 Operit ``ToolPermissionSystem.PermissionLevel.fromString``：
它对无法识别的值默认 ``ASK``（要问），而不是 ALLOW（放行）。
Kevrai 原先对无法识别的存档值**直接忽略**，于是 ``get_policy`` 返回默认的
``"auto"``——用户以为"每次询问"还开着，工具却已经不经确认直接跑了。
那是静默 fail-open，是这个模块里最坏的一种失败。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.approval import (  # noqa: E402
    ApprovalManager,
    normalize_policy,
)

# --- normalize_policy -------------------------------------------------------


def test_normalize_accepts_canonical_values() -> None:
    assert normalize_policy("auto") == "auto"
    assert normalize_policy("confirm") == "confirm"


def test_normalize_is_tolerant_of_case_and_whitespace() -> None:
    """大小写/空白差异不算"不认识"——否则一次格式变化就让用户的选择失效。"""
    for raw in ("Confirm", "CONFIRM", " confirm ", "\tconfirm\n"):
        assert normalize_policy(raw) == "confirm", raw
    for raw in ("Auto", "AUTO", " auto "):
        assert normalize_policy(raw) == "auto", raw


def test_normalize_maps_legacy_aliases() -> None:
    assert normalize_policy("ask") == "confirm"
    assert normalize_policy("manual") == "confirm"
    assert normalize_policy("automatic") == "auto"


def test_normalize_rejects_garbage() -> None:
    assert normalize_policy("不存在的策略") is None
    assert normalize_policy("") is None
    assert normalize_policy(None) is None
    assert normalize_policy(123) is None


# --- set_policy -------------------------------------------------------------


def test_set_policy_normalizes() -> None:
    m = ApprovalManager()
    assert m.set_policy("s", " Confirm ") == "confirm"
    assert m.get_policy("s") == "confirm"


def test_set_policy_rejects_unknown() -> None:
    m = ApprovalManager()
    try:
        m.set_policy("s", "nope")
    except ValueError:
        pass
    else:
        raise AssertionError("set_policy must reject unknown policy")
    # 失败的 set 不能留下半个状态
    assert m.get_policy("s") == "auto"


# --- load_policies: 失败方向 -------------------------------------------------


def test_load_policies_unknown_value_falls_back_to_confirm_not_auto() -> None:
    """核心断言：认不出来 → confirm（要问），绝不能是 auto（放行）。"""
    m = ApprovalManager()
    m.load_policies({"sess": "显然不是合法值"})
    assert m.get_policy("sess") == "confirm"


def test_load_policies_recognizes_case_variants() -> None:
    """大小写差异必须被还原，而不是被当成损坏值。"""
    m = ApprovalManager()
    result = m.load_policies({"a": "CONFIRM", "b": "Auto"})
    assert m.get_policy("a") == "confirm"
    assert m.get_policy("b") == "auto"
    assert result["unknown"] == []


def test_load_policies_reports_unknown_sessions() -> None:
    m = ApprovalManager()
    result = m.load_policies({"good": "auto", "bad1": "???", "bad2": ""})
    assert sorted(result["unknown"]) == ["bad1", "bad2"]


def test_load_policies_unknown_is_logged(caplog) -> None:
    """出问题必须可见：静默 fail-open 的根源就是没人记录。"""
    m = ApprovalManager()
    with caplog.at_level(logging.WARNING):
        m.load_policies({"sess": "garbage"})
    assert any("fail-closed" in r.getMessage() for r in caplog.records)


def test_load_policies_does_not_log_when_all_valid(caplog) -> None:
    m = ApprovalManager()
    with caplog.at_level(logging.WARNING):
        m.load_policies({"a": "auto", "b": "confirm"})
    assert not [r for r in caplog.records if "fail-closed" in r.getMessage()]


def test_load_policies_empty_mapping() -> None:
    m = ApprovalManager()
    assert m.load_policies({})["unknown"] == []
    # 没加载过的会话仍然是默认 auto（新用户不受影响）
    assert m.get_policy("never-seen") == "auto"


# --- 端到端：闸门行为 --------------------------------------------------------


def test_gate_auto_policy_runs_immediately() -> None:
    m = ApprovalManager()
    r = m.gate("s", "some_tool", {"a": 1})
    assert r.approved is True
    assert r.reason == "auto"


def test_gate_confirm_policy_parks_and_denies_on_timeout() -> None:
    """confirm 策略下没人回应 → 超时 → 不执行（不能放行）。"""
    m = ApprovalManager(default_timeout=0.01)
    m.set_policy("s", "confirm")
    r = m.gate("s", "some_tool", {"a": 1})
    assert r.approved is False
    assert r.reason == "timeout"


def test_corrupted_persisted_policy_still_gates() -> None:
    """串起整条链：存档损坏 → 恢复 → 闸门仍然拦得住。"""
    m = ApprovalManager(default_timeout=0.01)
    # 模拟启动时从持久化里读到一个损坏值
    m.load_policies({"sess": "binary-garbage"})
    r = m.gate("sess", "dangerous_tool", {})
    assert r.approved is False, "损坏的存档不得导致工具未经确认执行"
