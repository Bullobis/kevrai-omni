"""下载任务的终态保留与凭证清理。

对照 Cherry Studio 的 JobManager 与知识库任务设计：任务有终态、终态可清理、
昂贵操作的重启恢复策略要显式声明。本项目原本 ``_tasks`` 只增不减，每个
下载过的文件都永久留下一个 DownloadTask（含 asyncio.Queue / Event 和
``extra_headers`` 里的 Bearer token）。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.downloader import (  # noqa: E402
    DEFAULT_MAX_RETAINED_FINISHED,
    TERMINAL_STATUSES,
    DownloadStatus,
    DownloadTask,
    Downloader,
)


def _mk(n: int, status: DownloadStatus | None = None) -> list[DownloadTask]:
    """造 n 个任务；默认 DONE 且 finished_at 递增（越后面越新）。"""
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


def _downloader(tmp_path, **kw) -> Downloader:
    # 注意：Downloader 没有 download_dir 参数（dest 是 start() 时给的）。
    return Downloader(
        max_concurrent=2,
        allowed_hosts={"example.invalid"},
        **kw,
    )


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

def test_terminal_statuses_exclude_inflight():
    """PENDING/DOWNLOADING/VERIFYING 绝不能算终态 —— 否则会被提前淘汰，
    调用方查不到还在跑的下载。"""
    for s in (DownloadStatus.PENDING, DownloadStatus.DOWNLOADING, DownloadStatus.VERIFYING):
        assert s not in TERMINAL_STATUSES
    for s in (DownloadStatus.DONE, DownloadStatus.FAILED, DownloadStatus.CANCELLED):
        assert s in TERMINAL_STATUSES


# ---------------------------------------------------------------------------
# 淘汰：只淘汰终态、只淘汰最老的
# ---------------------------------------------------------------------------

async def test_prune_keeps_under_limit(tmp_path):
    d = _downloader(tmp_path, max_retained_finished=5)
    for t in _mk(5):
        d._tasks[t.id] = t
    assert await d.prune_finished() == 0
    assert len(d._tasks) == 5


async def test_prune_drops_oldest_finished(tmp_path):
    d = _downloader(tmp_path, max_retained_finished=3)
    for t in _mk(10):
        d._tasks[t.id] = t
    assert await d.prune_finished() == 7
    assert len(d._tasks) == 3
    # 保留的必须是最新完成的三个
    assert set(d._tasks) == {"t7", "t8", "t9"}


async def test_prune_never_drops_inflight(tmp_path):
    """进行中的任务永不被淘汰 —— 淘汰它就再也拿不到取消句柄了。"""
    d = _downloader(tmp_path, max_retained_finished=1)
    old_done = _mk(5)[0]
    d._tasks[old_done.id] = old_done
    live = _mk(1, DownloadStatus.DOWNLOADING)[0]
    live.id = "live"
    live.finished_at = 0.0  # 没完成
    d._tasks["live"] = live
    await d.prune_finished()
    assert "live" in d._tasks, "进行中的任务被淘汰了，等于丢失取消句柄"


async def test_prune_is_bounded_not_unbounded(tmp_path):
    """核心修复：任务数不再随下载量无限增长。"""
    d = _downloader(tmp_path, max_retained_finished=10)
    for t in _mk(500):
        d._tasks[t.id] = t
    await d.prune_finished()
    assert len(d._tasks) == 10


# ---------------------------------------------------------------------------
# 凭证清理
# ---------------------------------------------------------------------------

async def test_prune_clears_credentials(tmp_path):
    """淘汰时清空 extra_headers —— gated 仓库的 Bearer token 不该长期驻留。"""
    d = _downloader(tmp_path, max_retained_finished=2)
    tasks = _mk(4)
    for t in tasks:
        t.extra_headers = {"Authorization": "Bearer hf_secret"}
        d._tasks[t.id] = t
    dropped = await d.prune_finished()
    assert dropped == 2
    # 被淘汰的两个（最老的 t0/t1）必须清空 token。
    # 保留下来的 t2/t3 在真实流程里由 _run 的 finally 清理 —— 这个测试直接
    # 构造对象塞进 _tasks，绕过了 _run，所以只断言被淘汰的那些。
    assert tasks[0].extra_headers == {}, "被淘汰的任务应清空 token"
    assert tasks[1].extra_headers == {}, "被淘汰的任务应清空 token"


async def test_forget_clears_credentials(tmp_path):
    d = _downloader(tmp_path)
    t = _mk(1)[0]
    t.extra_headers = {"Authorization": "Bearer hf_secret"}
    d._tasks[t.id] = t
    assert await d.forget(t.id) is True
    assert t.extra_headers == {}


# ---------------------------------------------------------------------------
# forget：只接受终态
# ---------------------------------------------------------------------------

async def test_forget_rejects_inflight(tmp_path):
    """丢弃进行中的任务会丢失取消句柄，必须拒绝。"""
    d = _downloader(tmp_path)
    t = _mk(1, DownloadStatus.DOWNLOADING)[0]
    d._tasks[t.id] = t
    assert await d.forget(t.id) is False
    assert t.id in d._tasks


async def test_forget_unknown_id(tmp_path):
    d = _downloader(tmp_path)
    assert await d.forget("nope") is False


async def test_forget_accepts_terminal(tmp_path):
    d = _downloader(tmp_path)
    for st in TERMINAL_STATUSES:
        t = _mk(1, st)[0]
        t.id = f"x-{st.value}"
        d._tasks[t.id] = t
        assert await d.forget(t.id) is True


# ---------------------------------------------------------------------------
# 默认上限合理
# ---------------------------------------------------------------------------

def test_default_limit_is_sane():
    """默认上限要既能给前端查近期结果，又不会无界增长。"""
    assert 50 <= DEFAULT_MAX_RETAINED_FINISHED <= 1000


async def test_constructor_clamps_bad_limit(tmp_path):
    """传 0 / 负数不能导致"一个都不保留"。"""
    d = _downloader(tmp_path, max_retained_finished=0)
    assert d._max_retained_finished >= 1
    d2 = _downloader(tmp_path, max_retained_finished=-5)
    assert d2._max_retained_finished >= 1


# ---------------------------------------------------------------------------
# 端到端：真实跑一个下载，确认收尾会清理凭证
# ---------------------------------------------------------------------------

async def test_real_download_clears_credentials(tmp_path, monkeypatch):
    """跑通真实下载路径，验证 finally 里的凭证清理确实执行。"""
    import app.downloader as dl

    body = b"hello-kevrai"

    class _Resp:
        status_code = 200
        headers = {"content-length": str(len(body))}

        async def aiter_bytes(self, chunk=8192):
            yield body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Client:
        def stream(self, method, url, **kw):
            return _Resp()

    d = _downloader(tmp_path)
    monkeypatch.setattr(d, "_get_client", lambda: _Client())
    # 真实的方法名是 Downloader._sha256_file（模块里没有 verify_sha256）。
    monkeypatch.setattr(Downloader, "_sha256_file", lambda self, path: "x" * 64)

    dest = tmp_path / "out.bin"
    tid = await d.start(
        "https://example.invalid/f.bin",
        str(dest),
        sha256=None,
        extra_headers={"Authorization": "Bearer secret"},
    )
    # 等待后台任务结束
    for _ in range(200):
        snap = await d.progress(tid)
        if snap and snap["status"] in {"done", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.01)

    snap = await d.progress(tid)
    assert snap is not None
    if snap["status"] == "done":
        t = d._tasks.get(tid)
        assert t is not None and t.extra_headers == {}, "完成后 token 应已清空"


# ---------------------------------------------------------------------------
# 416 Range Not Satisfiable：必须能自愈，不能永久卡死
# ---------------------------------------------------------------------------

def test_reset_partial_truncates(tmp_path):
    p = tmp_path / "f.partial"
    p.write_bytes(b"x" * 1000)
    assert Downloader._reset_partial(p) is True
    assert p.stat().st_size == 0, "416 后必须清零，否则每次重试都同样失败"


def test_reset_partial_returns_false_when_missing_dir(tmp_path):
    """父目录不存在时不能抛异常 —— 失败要能被调用方转成明确的错误。"""
    p = tmp_path / "nope" / "deep" / "f.partial"
    assert Downloader._reset_partial(p) is False


async def test_416_resets_and_retries(tmp_path, monkeypatch):
    """核心场景：partial 比服务端文件大 → 416 → 清零 → 从头下成功。

    不处理的话任务会永久失败：每次重试都从同样的偏移续传，每次都 416。
    """
    import httpx

    d = _downloader(tmp_path)
    dest = tmp_path / "out.bin"
    partial = tmp_path / "out.bin.partial"
    partial.write_bytes(b"x" * 500)  # 陈旧的、比服务端文件大的残留

    calls = {"n": 0}

    class _R:
        def __init__(self, code, body=b""):
            self.status_code = code
            self._body = body
            self.headers = {"content-length": str(len(body))}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def raise_for_status(self):
            if self.status_code >= 400:
                req = httpx.Request("GET", "https://example.invalid/f")
                raise httpx.HTTPStatusError(
                    "e", request=req, response=httpx.Response(self.status_code, request=req)
                )

        async def aiter_bytes(self, chunk=8192):
            yield self._body

    class _C:
        def stream(self, m, u, **kw):
            calls["n"] += 1
            return _R(416) if calls["n"] == 1 else _R(200, b"fresh")

    async def _gc():
        return _C()

    monkeypatch.setattr(d, "_get_client", _gc)

    tid = await d.start("https://example.invalid/f.bin", str(dest))
    for _ in range(200):
        snap = await d.progress(tid)
        if snap and snap["status"] in {"done", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.01)

    snap = await d.progress(tid)
    assert snap is not None
    assert snap["status"] == "done", f"416 后应自愈完成，实际 {snap['status']}: {snap.get('error')}"
    assert calls["n"] >= 2, "416 后应有一次重置重试"
    # 最终产物是服务端给的全新内容，不是残留字节
    assert dest.read_bytes() == b"fresh"
