"""P0：生命周期闭合 —— 状态机、看门狗、资源配额。

对照 Cherry Studio 的任务系统设计：任务有显式终态、终态可清理、昂贵操作的
重启恢复需显式声明。本文件覆盖：

1. EXPIRED 终态 + 看门狗：用"是否有推进"判断存活，而不是"谁持有引用"
2. 磁盘配额与背压：启动前预检，而不是下到一半才发现盘满
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.downloader import (  # noqa: E402
    DEFAULT_STALL_TIMEOUT_S,
    TERMINAL_STATUSES,
    Downloader,
    DownloadStatus,
    DownloadTask,
    InsufficientDiskSpace,
    dir_free_bytes,
)


def _mk(n: int, status: DownloadStatus | None = None) -> list[DownloadTask]:
    out = []
    for i in range(n):
        t = DownloadTask(
            id=f"t{i}",
            url="https://example.invalid/f",
            dest_path=f"/tmp/f{i}",
            expected_sha256=None,
        )
        t.status = status or DownloadStatus.DONE
        t.finished_at = float(i)
        t.started_at = float(i)
        out.append(t)
    return out


def _downloader(**kw) -> Downloader:
    return Downloader(max_concurrent=2, allowed_hosts={"example.invalid"}, **kw)


# ---------------------------------------------------------------------------
# 1. 状态机：EXPIRED 必须是终态
# ---------------------------------------------------------------------------

def test_expired_is_terminal():
    """EXPIRED 必须在终态集合里，否则永远不会被淘汰，等于换了个名字泄漏。"""
    assert DownloadStatus.EXPIRED in TERMINAL_STATUSES


def test_paused_is_not_terminal():
    """PAUSED 不是终态 —— 暂停的任务还要能恢复。"""
    assert DownloadStatus.PAUSED not in TERMINAL_STATUSES


def test_default_stall_timeout_sane():
    """默认超时要足够长（不误杀慢速下载）又不能无限长。"""
    assert 60 <= DEFAULT_STALL_TIMEOUT_S <= 3600


# ---------------------------------------------------------------------------
# 2. mark_progress：防假推进
# ---------------------------------------------------------------------------

def test_mark_progress_below_threshold_no_refresh():
    """少量 keep-alive 字节不算推进 —— 否则假推进能永远骗过看门狗。"""
    d = _downloader(stall_min_bytes=1024)
    t = _mk(1, DownloadStatus.DOWNLOADING)[0]
    t._stall_mark_bytes = 0
    t.last_progress_at = 0.0
    assert d.mark_progress(t, 100) is False
    assert t.last_progress_at == 0.0, "不足阈值却刷新了存活时间戳"


def test_mark_progress_above_threshold_refreshes():
    d = _downloader(stall_min_bytes=1024)
    t = _mk(1, DownloadStatus.DOWNLOADING)[0]
    t._stall_mark_bytes = 0
    t.last_progress_at = 0.0
    assert d.mark_progress(t, 2048) is True
    assert t.last_progress_at > 0.0


def test_mark_progress_resets_baseline():
    """刷新后基准要跟着走，否则下一次永远"达标"（阈值形同虚设）。"""
    d = _downloader(stall_min_bytes=1024)
    t = _mk(1, DownloadStatus.DOWNLOADING)[0]
    t._stall_mark_bytes = 0
    d.mark_progress(t, 2048)
    assert t._stall_mark_bytes == 2048
    assert d.mark_progress(t, 2100) is False, "基准没走，阈值失效"


# ---------------------------------------------------------------------------
# 3. reap_stalled：核心的 GC 语义
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reap_stalled_expires_silent_task():
    """引用还在、但从没推进过的任务必须被回收。

    这就是"可达性分析 vs 引用计数"的差别：引用计数看不出它已经死了。
    """
    d = _downloader(stall_timeout_s=10, watchdog_interval_s=9999)
    t = _mk(1, DownloadStatus.DOWNLOADING)[0]
    t.last_progress_at = time.monotonic() - 100  # 100 秒没动
    d._tasks[t.id] = t
    out = await d.reap_stalled()
    assert out == [t.id]
    assert t.status is DownloadStatus.EXPIRED


@pytest.mark.asyncio
async def test_reap_stalled_keeps_active_task():
    """正在推进的任务绝不能被误杀。"""
    d = _downloader(stall_timeout_s=10)
    t = _mk(1, DownloadStatus.DOWNLOADING)[0]
    t.last_progress_at = time.monotonic()  # 刚动过
    d._tasks[t.id] = t
    assert await d.reap_stalled() == []
    assert t.status is DownloadStatus.DOWNLOADING


@pytest.mark.asyncio
async def test_reap_stalled_skips_terminal():
    d = _downloader(stall_timeout_s=0.01)
    for st in TERMINAL_STATUSES:
        t = _mk(1, st)[0]
        t.id = f"x-{st.value}"
        t.last_progress_at = time.monotonic() - 1000
        d._tasks[t.id] = t
    assert await d.reap_stalled() == []


@pytest.mark.asyncio
async def test_reap_stalled_sets_cancel_event():
    """过期必须同时置 cancel_event —— 否则底层协程还在写 .partial。"""
    d = _downloader(stall_timeout_s=0.01)
    t = _mk(1, DownloadStatus.DOWNLOADING)[0]
    t.last_progress_at = time.monotonic() - 100
    d._tasks[t.id] = t
    await d.reap_stalled()
    assert t.cancel_event.is_set(), "没取消底层协程，会留下半截写入"


@pytest.mark.asyncio
async def test_reap_stalled_disabled_when_timeout_zero():
    d = _downloader(stall_timeout_s=0)
    t = _mk(1, DownloadStatus.DOWNLOADING)[0]
    t.last_progress_at = time.monotonic() - 10000
    d._tasks[t.id] = t
    assert await d.reap_stalled() == []
    assert t.status is DownloadStatus.DOWNLOADING


@pytest.mark.asyncio
async def test_reap_stalled_clears_credentials():
    d = _downloader(stall_timeout_s=0.01)
    t = _mk(1, DownloadStatus.DOWNLOADING)[0]
    t.last_progress_at = time.monotonic() - 100
    t.extra_headers = {"Authorization": "Bearer secret"}
    d._tasks[t.id] = t
    await d.reap_stalled()
    assert t.extra_headers == {}


@pytest.mark.asyncio
async def test_expired_enters_prune_pool():
    """EXPIRED 要能进入 200 条淘汰逻辑，不能另开一个永不清理的池子。"""
    d = _downloader(stall_timeout_s=0.01, max_retained_finished=2)
    for i in range(5):
        t = _mk(1, DownloadStatus.DOWNLOADING)[0]
        t.id = f"e{i}"
        t.last_progress_at = time.monotonic() - 100
        d._tasks[t.id] = t
    await d.reap_stalled()
    assert len(d._tasks) == 2


# ---------------------------------------------------------------------------
# 4. 看门狗生命周期
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_watchdog_not_started_when_disabled():
    d = _downloader(stall_timeout_s=0)
    d._ensure_watchdog()
    assert d._watchdog_task is None


@pytest.mark.asyncio
async def test_watchdog_starts_on_first_task():
    d = _downloader(stall_timeout_s=1, watchdog_interval_s=9999)
    d._ensure_watchdog()
    assert d._watchdog_task is not None
    d._watchdog_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await d._watchdog_task


@pytest.mark.asyncio
async def test_watchdog_exits_when_no_inflight():
    """没有进行中任务时看门狗要自己退出，不留常驻后台任务。"""
    d = _downloader(stall_timeout_s=1, watchdog_interval_s=0.01)
    d._ensure_watchdog()
    task = d._watchdog_task
    assert task is not None
    await asyncio.wait_for(task, timeout=5)
    assert d._watchdog_task is None, "看门狗没有自动退出"


# ---------------------------------------------------------------------------
# 5. 磁盘配额（背压）
# ---------------------------------------------------------------------------

def test_dir_free_bytes_returns_positive(tmp_path):
    assert dir_free_bytes(tmp_path) > 0


def test_dir_free_bytes_handles_nonexistent_parent(tmp_path):
    """父目录还不存在时不能抛异常（首次下载就是这个情况）。"""
    deep = tmp_path / "a" / "b" / "c" / "model.bin"
    assert dir_free_bytes(deep) > 0


def test_disk_check_rejects_absurd_size(tmp_path):
    """要的量远超磁盘总量时必须拒绝。"""
    d = _downloader(min_free_bytes=1024)
    with pytest.raises(InsufficientDiskSpace):
        d._check_disk_space(tmp_path / "m.bin", declared_bytes=10**15)


def test_disk_check_accepts_small(tmp_path):
    d = _downloader(min_free_bytes=0)
    d._check_disk_space(tmp_path / "m.bin", declared_bytes=1024)


def test_disk_check_disabled_when_zero(tmp_path):
    d = _downloader(min_free_bytes=0)
    d._check_disk_space(tmp_path / "m.bin", declared_bytes=10**15)


def test_disk_check_credits_existing_partial(tmp_path):
    """已有部分下载时只校验差额 —— 否则续传会被自己已下的部分挡住。

    用 100MB 量级：/tmp 上造 4GB 稀疏文件会触发 File too large。
    """
    dest = tmp_path / "m.bin"
    partial = tmp_path / "m.bin.partial"
    have = 100 * 1024**2
    with partial.open("wb") as fh:
        fh.truncate(have)
    assert partial.stat().st_size == have

    free = dir_free_bytes(tmp_path)
    # 必须打开预检（min_free_bytes>0）：min_free_bytes=0 表示禁用，一律放行。
    d = _downloader(min_free_bytes=1024)
    # 声明 100MB，已有 100MB → 还需 0，必然通过
    d._check_disk_space(dest, declared_bytes=have)
    # 声明"剩余空间 + 已有"再多一点 → 扣除已有后仍超出，应拒绝
    with pytest.raises(InsufficientDiskSpace):
        d._check_disk_space(dest, declared_bytes=have + free + 1024**2)


def test_disk_check_unknown_size_only_checks_reserve(tmp_path):
    """大小未知时不能拿估算值拒绝 —— 那会把小文件下载一起挡掉。"""
    d = _downloader(min_free_bytes=1024)
    d._check_disk_space(tmp_path / "m.bin", declared_bytes=None)


def test_insufficient_disk_space_message_has_numbers():
    e = InsufficientDiskSpace("/x/m.bin", 5 * 1024**3, 10 * 1024**3)
    assert "5.0" in str(e) and "10.0" in str(e)
    assert e.free == 5 * 1024**3 and e.needed == 10 * 1024**3


@pytest.mark.asyncio
async def test_start_rejects_when_disk_full(tmp_path):
    """start() 必须在启动前就拒绝，而不是让任务跑几小时后失败。"""
    d = _downloader(min_free_bytes=1024)
    with pytest.raises(InsufficientDiskSpace):
        await d.start(
            "https://example.invalid/big.bin",
            str(tmp_path / "big.bin"),
            declared_bytes=10**15,
        )
    # 拒绝时不应留下任何任务记录
    assert len(d._tasks) == 0


# ---------------------------------------------------------------------------
# 6. 跨进程重启恢复语义（P0-2）
# ---------------------------------------------------------------------------

def test_recovery_strategies_are_exhaustive():
    """三种策略必须都能被 plan_recovery 处理，不能有落到 else 黑洞的情况。"""
    from app.downloader import RecoveryStrategy

    expected = {"resume", "manual", "discard"}
    assert {s.value for s in RecoveryStrategy} == expected


def test_scan_orphans_finds_partial(tmp_path):
    p = tmp_path / "m.bin.partial"
    p.write_bytes(b"x" * 100)
    found = Downloader.scan_orphan_partials(tmp_path)
    assert len(found) == 1
    assert found[0].partial == p
    assert found[0].size == 100


def test_scan_orphans_ignores_final_files(tmp_path):
    """已完成的文件（无 .partial 后缀）不是孤儿。"""
    (tmp_path / "m.bin").write_bytes(b"x" * 100)
    assert Downloader.scan_orphan_partials(tmp_path) == []


def test_scan_orphans_recurses(tmp_path):
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "m.bin.partial").write_bytes(b"x" * 10)
    assert len(Downloader.scan_orphan_partials(tmp_path)) == 1


def test_scan_orphans_missing_dir():
    assert Downloader.scan_orphan_partials("/nonexistent-dir-xyz") == []


def test_scan_orphans_stale_flag(tmp_path):
    p = tmp_path / "m.bin.partial"
    p.write_bytes(b"x" * 10)
    now = time.time()
    fresh = Downloader.scan_orphan_partials(tmp_path, stale_after_s=3600, now=now)
    assert fresh[0].stale is False, "刚中断的不该被判为陈旧"
    old = Downloader.scan_orphan_partials(tmp_path, stale_after_s=1, now=now + 100)
    assert old[0].stale is True


def test_plan_recovery_resume_keeps_bytes():
    from app.downloader import OrphanPartial, RecoveryStrategy

    p = Path("/tmp/x.partial")
    o = OrphanPartial(p, 50 * 1024**2, 10.0, False)
    plan = Downloader.plan_recovery(o, RecoveryStrategy.RESUME)
    assert plan.action == "resume"
    assert plan.delete_partial is False, "续传策略绝不能删已下字节"


def test_plan_recovery_manual_keeps_bytes():
    """MANUAL 只是不自动跑，不能顺手删掉用户已下的部分。"""
    from app.downloader import OrphanPartial, RecoveryStrategy

    o = OrphanPartial(Path("/tmp/x.partial"), 1024, 10.0, True)
    plan = Downloader.plan_recovery(o, RecoveryStrategy.MANUAL)
    assert plan.action == "mark-failed"
    assert plan.delete_partial is False


def test_plan_recovery_discard_deletes():
    from app.downloader import OrphanPartial, RecoveryStrategy

    o = OrphanPartial(Path("/tmp/x.partial"), 1024, 10.0, True)
    plan = Downloader.plan_recovery(o, RecoveryStrategy.DISCARD)
    assert plan.delete_partial is True


def test_plan_recovery_is_pure(tmp_path):
    """plan_recovery 只是决策，不能碰磁盘。"""
    from app.downloader import OrphanPartial, RecoveryStrategy

    p = tmp_path / "m.bin.partial"
    p.write_bytes(b"x" * 100)
    o = OrphanPartial(p, 100, 10.0, True)
    Downloader.plan_recovery(o, RecoveryStrategy.DISCARD)
    assert p.exists(), "plan_recovery 不该产生副作用"


def test_default_recovery_is_resume():
    """默认是续传：重下只是带宽，删掉用户已下的几十 GB 才是真正的事故。"""
    from app.downloader import DEFAULT_RECOVERY, RecoveryStrategy

    assert DEFAULT_RECOVERY is RecoveryStrategy.RESUME


def test_task_carries_recovery_strategy():
    t = DownloadTask(
        id="x", url="https://example.invalid/f",
        dest_path="/tmp/f", expected_sha256=None,
    )
    from app.downloader import RecoveryStrategy

    assert t.recovery is RecoveryStrategy.RESUME
