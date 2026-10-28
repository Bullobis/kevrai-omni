"""Operit 式「全局默认 + 单工具例外」权限模型，以及 deny 第三档。

覆盖三条最容易写错的边界：
1. 工具例外必须**优先于**会话全局策略（否则这套模型没意义）。
2. deny 必须是**立即拒绝**，不能退化成弹确认框（否则 FORBID 等于 ASK）。
3. 工具例外的存档值损坏时，要朝保守方向倒（confirm），不能静默放行。
"""
import threading

from app.agent.approval import ApprovalManager


def _mgr() -> ApprovalManager:
    m = ApprovalManager()
    m.set_policy("s1", "auto")
    return m


def test_tool_override_beats_session_policy():
    """会话是 auto（全放行），但单个工具设成 confirm —— 该工具必须拦。"""
    m = _mgr()
    m.set_tool_policy("s1", "write_file", "confirm")
    assert m.get_policy("s1") == "auto"
    assert m.effective_policy("s1", "write_file") == "confirm"
    # 没配例外的工具仍跟随全局
    assert m.effective_policy("s1", "read_file") == "auto"


def test_deny_rejects_without_prompting():
    """deny 档：直接拒绝，不挂起等待，也不占用审批超时窗口。"""
    m = _mgr()
    m.set_tool_policy("s1", "http_request", "deny")
    res = m.gate("s1", "http_request", {"url": "http://x"}, None)
    assert res.approved is False
    assert res.reason == "deny"
    # 关键：没有产生任何待审批请求（否则 UI 会弹出一个永远没人点的框）
    assert m.list_pending() == []


def test_deny_at_session_level_blocks_every_tool():
    m = _mgr()
    m.set_policy("s1", "deny")
    for tool in ("read_file", "write_file", "search"):
        assert m.gate("s1", tool, {}, None).approved is False


def test_corrupt_tool_override_falls_back_to_confirm():
    """损坏的工具例外值：朝保守方向倒，绝不能静默回落到 auto。"""
    m = _mgr()
    with m._lock:
        m._tool_policies["s1"] = {"write_file": "garbage"}
    assert m.effective_policy("s1", "write_file") == "confirm"


def test_clear_tool_override_restores_session_policy():
    m = _mgr()
    m.set_tool_policy("s1", "write_file", "deny")
    assert m.effective_policy("s1", "write_file") == "deny"
    m.clear_tool_policy("s1", "write_file")
    assert m.effective_policy("s1", "write_file") == "auto"
    assert m.get_tool_policies("s1") == {}


def test_aliases_normalise_to_three_levels():
    """Operit 的 fromString 是 tolerant 解析，别名必须收敛到同一档。"""
    m = _mgr()
    for raw, want in (("ALLOW", "auto"), ("ask", "confirm"),
                      ("FORBID", "deny"), ("never", "deny")):
        assert m.set_tool_policy("s1", "t", raw) == want


def test_invalid_tool_name_rejected():
    m = _mgr()
    try:
        m.set_tool_policy("s1", "   ", "deny")
    except ValueError:
        pass
    else:
        raise AssertionError("空工具名必须被拒绝")


def test_deny_does_not_block_on_timeout():
    """deny 场景不能卡住 ReAct 循环：gate 必须同步立即返回。"""
    m = _mgr()
    m.set_policy("s1", "deny")
    done = threading.Event()

    def run():
        m.gate("s1", "anything", {}, None)
        done.set()

    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=2.0)
    assert done.is_set(), "deny 的 gate 必须立即返回，不能阻塞"
