"""分片并发下载测试。

用一个"支持 Range 的假 HTTP 客户端"做端到端验证：它能按请求的
``Range`` 头返回正确的字节片段，因此我们能在离线环境下验证：

    * 分片拼接后与原始文件**逐字节一致**
    * 不支持 Range / 小文件 → 不分片（行为不变）
    * 中断后按段续传，已完成的分片不会重复下载
    * 分片失败回退时，partial 被截断到连续前缀（不留空洞）
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app import download_shards as ds
from app.download_shards import (
    MAX_SHARDS,
    MIN_SHARD_BYTES,
    MIN_TOTAL_BYTES,
    ShardingNotSupportedError,
    ShardPlan,
    download_sharded,
    plan_shards,
    probe_range_support,
    truncate_to_prefix,
)


# ---------------------------------------------------------------------------
# 假 HTTP 客户端
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, body: bytes, status: int = 206, headers: dict[str, str] | None = None):
        self._body = body
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    async def aiter_bytes(self, size: int):
        for i in range(0, len(self._body), size):
            yield self._body[i : i + size]


class _StreamCtx:
    def __init__(self, resp: _Resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class FakeRangeClient:
    """模拟支持断点续传的服务器。

    ``fail_on`` 可指定第几次 GET 抛错，用于测试失败回退。
    """

    def __init__(self, content: bytes, *, support_range: bool = True,
                 force_200: bool = False, fail_on: int | None = None):
        self.content = content
        self.support_range = support_range
        self.force_200 = force_200       # 忽略 Range，返回整个文件（200）
        self.fail_on = fail_on           # 第 N 次 GET 抛异常
        self.get_count = 0
        self.ranges: list[tuple[int, int]] = []   # 记录实际请求的区间

    async def request(self, method: str, url: str, headers=None):
        if method != "HEAD":
            raise AssertionError("unexpected method")
        if not self.support_range:
            return _Resp(b"", 200, {"Content-Length": str(len(self.content))})
        return _Resp(
            b"", 200,
            {"Accept-Ranges": "bytes", "Content-Length": str(len(self.content))},
        )

    def stream(self, method: str, url: str, headers=None):
        headers = headers or {}
        rng = headers.get("Range", "")
        self.get_count += 1
        if self.fail_on is not None and self.get_count == self.fail_on:
            raise ConnectionResetError("boom")

        if self.force_200 or not rng.startswith("bytes="):
            return _StreamCtx(_Resp(self.content, 200,
                                    {"Content-Length": str(len(self.content))}))

        spec = rng[len("bytes="):]
        s, _, e = spec.partition("-")
        start = int(s)
        end = int(e) if e else len(self.content) - 1
        self.ranges.append((start, end))
        if start >= len(self.content):
            return _StreamCtx(_Resp(b"", 416))
        body = self.content[start : end + 1]
        return _StreamCtx(_Resp(body, 206, {
            "Content-Range": f"bytes {start}-{end}/{len(self.content)}",
        }))


class _Task:
    def __init__(self) -> None:
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.speed_bps = 0.0
        self.cancel_event = asyncio.Event()


async def _noop_emit(task, event, **kw):  # noqa: ANN001
    return None


# ---------------------------------------------------------------------------
# 分片划分
# ---------------------------------------------------------------------------


def test_plan_shards_covers_whole_file() -> None:
    total = 1000
    for n in (1, 2, 3, 4, 8):
        plan = plan_shards(total, n)
        assert plan.shards[0][0] == 0
        assert plan.shards[-1][1] == total - 1
        # 段与段之间必须首尾相接，不能有空隙或重叠
        for i in range(1, len(plan.shards)):
            assert plan.shards[i][0] == plan.shards[i - 1][1] + 1
        assert sum(e - s + 1 for s, e, _ in plan.shards) == total


def test_plan_shards_respects_min_shard_size() -> None:
    # 10MB / 2MB 每片 = 最多 5 片，即使请求 8 片
    total = 10 * 1024 * 1024
    plan = plan_shards(total, 8)
    assert len(plan.shards) <= total // MIN_SHARD_BYTES
    assert all(e - s + 1 >= MIN_SHARD_BYTES for s, e, _ in plan.shards)


def test_plan_shards_caps_at_max_shards() -> None:
    huge = 100 * 1024 * 1024 * 1024
    assert len(plan_shards(huge, 32).shards) == MAX_SHARDS


def test_small_file_not_split() -> None:
    plan = plan_shards(1024, 4)
    assert len(plan.shards) == 1


def test_plan_shards_handles_odd_division() -> None:
    plan = plan_shards(10, 4)
    assert sum(e - s + 1 for s, e, _ in plan.shards) == 10


# ---------------------------------------------------------------------------
# 连续前缀计算（回退单线时最关键的正确性点）
# ---------------------------------------------------------------------------


def test_completed_prefix_all_done() -> None:
    plan = ShardPlan(100, [[0, 49, 50], [50, 99, 50]])
    assert plan.completed_prefix() == 100
    assert plan.done_bytes == 100


def test_completed_prefix_stops_at_hole() -> None:
    # 第一段完成、第二段没动、第三段完成 → 前缀只到第一段末尾
    plan = ShardPlan(150, [[0, 49, 50], [50, 99, 0], [100, 149, 50]])
    assert plan.done_bytes == 100      # 总共下了 100 字节
    assert plan.completed_prefix() == 50  # 但连续的只有前 50


def test_completed_prefix_partial_first_shard() -> None:
    plan = ShardPlan(100, [[0, 49, 20], [50, 99, 0]])
    assert plan.completed_prefix() == 20


def test_completed_prefix_nothing_done() -> None:
    plan = ShardPlan(100, [[0, 49, 0], [50, 99, 0]])
    assert plan.completed_prefix() == 0


def test_plan_json_roundtrip() -> None:
    plan = ShardPlan(100, [[0, 49, 10], [50, 99, 0]])
    back = ShardPlan.from_json(plan.to_json())
    assert back is not None
    assert back.total == 100
    assert back.shards == [[0, 49, 10], [50, 99, 0]]


def test_plan_json_rejects_garbage() -> None:
    assert ShardPlan.from_json({"version": 99}) is None
    assert ShardPlan.from_json({"version": 1}) is None
    assert ShardPlan.from_json({"version": 1, "total": "x", "shards": []}) is None


# ---------------------------------------------------------------------------
# Range 探测
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_supports_range() -> None:
    c = FakeRangeClient(b"x" * 100)
    ok, total = await probe_range_support(c, "http://x/f", None)  # type: ignore[arg-type]
    assert ok and total == 100


@pytest.mark.asyncio
async def test_probe_no_accept_ranges() -> None:
    c = FakeRangeClient(b"x" * 100, support_range=False)
    ok, _ = await probe_range_support(c, "http://x/f", None)  # type: ignore[arg-type]
    assert not ok


@pytest.mark.asyncio
async def test_probe_failure_is_safe() -> None:
    class Boom:
        async def request(self, *a, **k):
            raise RuntimeError("network down")

    ok, total = await probe_range_support(Boom(), "http://x/f", None)  # type: ignore[arg-type]
    assert ok is False and total == 0


# ---------------------------------------------------------------------------
# 分片下载端到端
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sharded_download_reassembles_exactly(tmp_path: Path) -> None:
    content = bytes(range(256)) * 400_000          # ~100MB -> 会被分片
    partial = tmp_path / "model.bin.partial"
    task = _Task()
    client = FakeRangeClient(content)

    ok = await download_sharded(
        client=client,  # type: ignore[arg-type]
        url="http://x/f",
        base_headers={},
        partial=partial,
        task=task,
        chunk_size=1 << 20,
        max_shards=4,
        emit=_noop_emit,
    )

    assert ok is True
    assert partial.read_bytes() == content          # 逐字节一致
    assert task.downloaded_bytes == len(content)


@pytest.mark.asyncio
async def test_sharded_download_uses_multiple_ranges(tmp_path: Path) -> None:
    content = bytes(range(256)) * 400_000
    partial = tmp_path / "m.partial"
    client = FakeRangeClient(content)

    await download_sharded(
        client=client, url="http://x/f", base_headers={}, partial=partial,
        task=_Task(), chunk_size=1 << 20, max_shards=4, emit=_noop_emit,  # type: ignore[arg-type]
    )
    # 确实并发请求了多个不同区间
    assert len(client.ranges) > 1


@pytest.mark.asyncio
async def test_no_range_support_falls_back(tmp_path: Path) -> None:
    content = b"x" * (20 * 1024 * 1024)
    partial = tmp_path / "m.partial"
    client = FakeRangeClient(content, support_range=False)

    ok = await download_sharded(
        client=client, url="http://x/f", base_headers={}, partial=partial,  # type: ignore[arg-type]
        task=_Task(), chunk_size=1 << 20, max_shards=4, emit=_noop_emit,
    )
    assert ok is False
    assert not partial.exists()          # 不应创建 partial，交给单线处理


@pytest.mark.asyncio
async def test_small_file_falls_back(tmp_path: Path) -> None:
    content = b"x" * 1024
    partial = tmp_path / "m.partial"
    client = FakeRangeClient(content)
    ok = await download_sharded(
        client=client, url="http://x/f", base_headers={}, partial=partial,  # type: ignore[arg-type]
        task=_Task(), chunk_size=4096, max_shards=4, emit=_noop_emit,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_server_ignores_range_falls_back(tmp_path: Path) -> None:
    content = b"x" * (20 * 1024 * 1024)
    partial = tmp_path / "m.partial"
    client = FakeRangeClient(content, force_200=True)
    ok = await download_sharded(
        client=client, url="http://x/f", base_headers={}, partial=partial,  # type: ignore[arg-type]
        task=_Task(), chunk_size=1 << 20, max_shards=4, emit=_noop_emit,
    )
    assert ok is False


# ---------------------------------------------------------------------------
# 按段续传
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_skips_completed_shards(tmp_path: Path) -> None:
    content = bytes(range(256)) * 400_000
    partial = tmp_path / "m.partial"
    client = FakeRangeClient(content)

    # 手工造一份"第一段已完成"的进度
    plan = plan_shards(len(content), 4)
    plan.shards[0][2] = plan.shards[0][1] - plan.shards[0][0] + 1
    ppath = partial.with_name(partial.name + ".shards.json")
    ppath.write_text(__import__("json").dumps(plan.to_json()), encoding="utf-8")
    # 预分配，并把"已完成"的第一段填入真实内容（真实场景也是如此）
    buf = bytearray(len(content))
    fs, fe, _ = plan.shards[0]
    buf[fs:fe + 1] = content[fs:fe + 1]
    partial.write_bytes(bytes(buf))

    await download_sharded(
        client=client, url="http://x/f", base_headers={}, partial=partial,  # type: ignore[arg-type]
        task=_Task(), chunk_size=1 << 20, max_shards=4, emit=_noop_emit,
    )

    first_start, first_end, _ = plan.shards[0]
    # 已完成的第一段不应再被请求
    assert (first_start, first_end) not in client.ranges
    assert partial.read_bytes() == content


@pytest.mark.asyncio
async def test_stale_progress_is_discarded(tmp_path: Path) -> None:
    content = bytes(range(256)) * 400_000
    partial = tmp_path / "m.partial"
    client = FakeRangeClient(content)

    # 进度里的 total 与真实文件大小不符 → 必须丢弃重建
    ppath = partial.with_name(partial.name + ".shards.json")
    ppath.write_text(
        __import__("json").dumps(
            {"version": 1, "total": 12345, "shards": [[0, 99, 100]]}
        ),
        encoding="utf-8",
    )
    ok = await download_sharded(
        client=client, url="http://x/f", base_headers={}, partial=partial,  # type: ignore[arg-type]
        task=_Task(), chunk_size=1 << 20, max_shards=4, emit=_noop_emit,
    )
    assert ok is True
    assert partial.read_bytes() == content


# ---------------------------------------------------------------------------
# 失败回退：截断到连续前缀
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_is_propagated_for_fallback(tmp_path: Path) -> None:
    content = bytes(range(256)) * 400_000
    partial = tmp_path / "m.partial"
    client = FakeRangeClient(content, fail_on=2)

    with pytest.raises(ConnectionResetError):
        await download_sharded(
            client=client, url="http://x/f", base_headers={}, partial=partial,  # type: ignore[arg-type]
            task=_Task(), chunk_size=1 << 20, max_shards=4, emit=_noop_emit,
        )


@pytest.mark.asyncio
async def test_truncate_to_prefix_removes_holes(tmp_path: Path) -> None:
    partial = tmp_path / "m.partial"
    # 假设 150 字节文件，第一段 50 完成、第二段 0、第三段 50 完成
    plan = ShardPlan(150, [[0, 49, 50], [50, 99, 0], [100, 149, 50]])
    ppath = partial.with_name(partial.name + ".shards.json")
    ppath.write_text(__import__("json").dumps(plan.to_json()), encoding="utf-8")
    partial.write_bytes(b"Z" * 150)

    prefix = truncate_to_prefix(partial)
    assert prefix == 50
    assert partial.stat().st_size == 50      # 空洞被截掉
    assert not ppath.exists()                # 进度文件已清理


def test_truncate_without_progress_is_noop(tmp_path: Path) -> None:
    partial = tmp_path / "m.partial"
    partial.write_bytes(b"abc")
    assert truncate_to_prefix(partial) == 3
    assert partial.read_bytes() == b"abc"


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_stops_sharded_download(tmp_path: Path) -> None:
    content = bytes(range(256)) * 400_000
    partial = tmp_path / "m.partial"
    client = FakeRangeClient(content)
    task = _Task()

    async def _cancel_soon() -> None:
        await asyncio.sleep(0)
        task.cancel_event.set()

    asyncio.ensure_future(_cancel_soon())
    with pytest.raises(asyncio.CancelledError):
        await download_sharded(
            client=client, url="http://x/f", base_headers={}, partial=partial,  # type: ignore[arg-type]
            task=task, chunk_size=1 << 20, max_shards=4, emit=_noop_emit,
        )


# ---------------------------------------------------------------------------
# 进度文件损坏时不得删除已下载内容（回归）
# ---------------------------------------------------------------------------

def test_corrupt_progress_file_keeps_downloaded_partial(tmp_path):
    """进度文件写坏了，不能把已经下好的 partial 一起删掉。

    旧代码把"进度损坏"和"服务端文件变了"当作同一种情况处理，两者都执行
    `partial.unlink()`。长度没变时，重新分片覆盖写即可得到正确结果；
    删文件意味着几十 GB 的下载成果只因几 KB 的进度文件坏掉就作废。
    """
    from app.download_shards import _load_or_create_plan, _progress_path

    partial = tmp_path / "model.gguf.partial"
    partial.write_bytes(b"x" * 4096)          # 已下载的内容
    ppath = _progress_path(partial)
    ppath.write_text("{ this is not valid json", encoding="utf-8")

    plan = _load_or_create_plan(partial, total=4096, max_shards=4)
    assert partial.exists(), "partial 必须保留"
    assert plan.total == 4096
    assert all(s[2] == 0 for s in plan.shards), "损坏的进度不能当作已有进展"
    assert not ppath.exists(), "坏进度文件应被清理"


def test_changed_remote_size_still_resets(tmp_path):
    """服务端文件长度变了才需要重建——这种情况旧内容确实无意义。"""
    from app.download_shards import _load_or_create_plan, _progress_path, plan_shards

    partial = tmp_path / "m.partial"
    partial.write_bytes(b"y" * 1000)
    ppath = _progress_path(partial)
    old = plan_shards(1000, 2)
    ppath.write_text(json.dumps(old.to_json()), encoding="utf-8")

    plan = _load_or_create_plan(partial, total=2048, max_shards=2)
    assert plan.total == 2048
    assert not partial.exists(), "长度变了，旧内容必须丢弃"


def test_valid_progress_is_resumed(tmp_path):
    """正常进度应当被续跑，不能动 partial。"""
    from app.download_shards import _load_or_create_plan, _progress_path, plan_shards

    partial = tmp_path / "m.partial"
    partial.write_bytes(b"z" * 1000)
    ppath = _progress_path(partial)
    old = plan_shards(1000, 2)
    old.shards[0][2] = old.shards[0][1] - old.shards[0][0] + 1   # 第一片已完成
    ppath.write_text(json.dumps(old.to_json()), encoding="utf-8")

    plan = _load_or_create_plan(partial, total=1000, max_shards=2)
    assert plan.shards[0][2] > 0, "第一片进度应被保留"
    assert partial.exists()
