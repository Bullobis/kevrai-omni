"""大文件分片并发下载（多线并发 + 按段续传）。

目标：几十 GB 的模型权重下载更快、断线代价更小。

设计要点
--------
1. **只对值得的文件分片**：先 HEAD 探测，只有服务器声明
   ``Accept-Ranges: bytes`` 且给出了 ``Content-Length``、并且文件足够大时才分片。
   否则返回 ``None``，调用方走原来的单线流程 —— 不会变慢，也不会改变行为。

2. **按段续传**：每个分片的进度记在 ``<file>.partial.shards.json``。
   中断后重启，已完成的分片直接跳过，未完成的分片从断点继续，
   不用像单线那样"从头再来"（单线续传虽然也能续，但只有一个连接，
   丢一段就要重排在它后面的所有字节）。

3. **失败安全回退**：分片过程中任何异常都交给调用方。回退时会把
   ``.partial`` 截断到"从 0 开始连续已完成的字节数"，保证单线续传拿到的
   前缀是完整正确的，然后删掉分片进度文件。

4. **完整性校验不在本模块**：分片下载只负责把字节按偏移写对，
   sha256 校验仍由 Downloader 主流程统一做，行为与单线完全一致。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
from typing import Any

import httpx

__all__ = [
    "ShardPlan",
    "plan_shards",
    "probe_range_support",
    "download_sharded",
    "MIN_TOTAL_BYTES",
    "DEFAULT_MAX_SHARDS",
]

#: 小于这个体积不值得分片（分片本身有连接开销）
MIN_TOTAL_BYTES = 8 * 1024 * 1024          # 8 MiB
#: 单个分片的最小体积，避免把大文件切成几百个碎片
MIN_SHARD_BYTES = 2 * 1024 * 1024          # 2 MiB
#: 默认并发分片数
DEFAULT_MAX_SHARDS = 4
#: 硬上限，防止超大文件切出过多连接把服务端打爆
MAX_SHARDS = 8

_PROGRESS_SUFFIX = ".shards.json"


class ShardPlan:
    """一次分片下载的任务划分与进度。"""

    def __init__(self, total: int, shards: list[list[int]]) -> None:
        # shards: [[start, end, done_bytes], ...]（end 为闭区间）
        self.total = total
        self.shards = shards

    @property
    def done_bytes(self) -> int:
        return sum(s[2] for s in self.shards)

    def completed_prefix(self) -> int:
        """返回从 0 开始**连续**已完成的字节数。

        分片是乱序完成的，回退到单线续传时不能假设"总字节数"就是安全偏移，
        只能取连续前缀，否则中间空洞会让续传后的文件错位。
        """
        prefix = 0
        for start, end, done in self.shards:
            if start != prefix:
                break
            if done < (end - start + 1):
                prefix += done
                break
            prefix = end + 1
        return prefix

    def to_json(self) -> dict[str, Any]:
        return {"version": 1, "total": self.total, "shards": self.shards}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ShardPlan | None:
        try:
            if int(data.get("version", 0)) != 1:
                return None
            total = int(data["total"])
            shards = [[int(a), int(b), int(c)] for a, b, c in data["shards"]]
        except (KeyError, TypeError, ValueError):
            return None
        if total <= 0 or not shards:
            return None
        return cls(total, shards)


def plan_shards(total: int, max_shards: int = DEFAULT_MAX_SHARDS) -> ShardPlan:
    """把 [0, total) 切成若干段。

    分片数同时受三个条件约束：配置上限、每片最小体积、以及总分片数上限。
    """
    n = max(1, min(int(max_shards) or 1, MAX_SHARDS))
    # 每片至少 MIN_SHARD_BYTES，避免碎片化
    n = max(1, min(n, max(1, total // MIN_SHARD_BYTES)))
    n = min(n, MAX_SHARDS)
    if total < MIN_TOTAL_BYTES:
        n = 1

    shards: list[list[int]] = []
    step = total // n
    start = 0
    for i in range(n):
        end = total - 1 if i == n - 1 else start + step - 1
        shards.append([start, end, 0])
        start = end + 1
    return ShardPlan(total, shards)


def _progress_path(partial: Path) -> Path:
    return partial.with_name(partial.name + _PROGRESS_SUFFIX)


async def probe_range_support(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str] | None,
) -> tuple[bool, int]:
    """探测服务器是否支持断点续传。

    返回 ``(supports_range, total_bytes)``。探测失败一律返回 ``(False, 0)``，
    让调用方安全退回单线。
    """
    try:
        r = await client.request("HEAD", url, headers=headers or None)
    except Exception:  # noqa: BLE001 — HEAD 不被支持/超时都当作不支持
        return False, 0
    if r.status_code >= 400:
        return False, 0
    if (r.headers.get("Accept-Ranges") or "").strip().lower() != "bytes":
        return False, 0
    try:
        total = int(r.headers.get("Content-Length") or "0")
    except ValueError:
        return False, 0
    if total <= 0:
        return False, 0
    return True, total


def _load_or_create_plan(partial: Path, total: int, max_shards: int) -> ShardPlan:
    """恢复已有进度，或新建分片计划。

    若磁盘上的进度与当前文件总大小不一致（服务端文件变了），丢弃重建。
    """
    ppath = _progress_path(partial)
    if ppath.exists():
        # 必须先初始化：读取/解析被 suppress 吞掉异常时 plan 会是未绑定状态。
        plan: ShardPlan | None = None
        with contextlib.suppress(OSError, json.JSONDecodeError), ppath.open(
            encoding="utf-8"
        ) as fh:
            plan = ShardPlan.from_json(json.load(fh))

        if plan is not None:
            if plan.total == total:
                # 进度与当前文件一致 —— 直接续跑。
                return plan
            # 服务端文件变了（长度不同）：旧内容和旧进度都不可信，必须重建。
            # 这里删 partial 是安全的，因为长度已变，任何旧字节都无意义。
            with contextlib.suppress(OSError):
                ppath.unlink()
            with contextlib.suppress(OSError):
                partial.unlink()
            return plan_shards(total, max_shards)

        # 进度文件损坏（版本不对 / JSON 坏了 / 字段缺失）。
        #
        # 这里**绝不能删 partial**。旧代码把"损坏"和"文件变了"同等处理，
        # 一并 unlink 了 partial —— 一个已经下了几十 GB 的文件，只因为旁边
        # 几 KB 的进度文件写坏了就整份作废。长度没变，重新分片覆盖写即可
        # 得到正确结果（每个分片只写自己那段，最后统一 truncate 到 total），
        # 代价是重下一遍，而不是丢掉一遍。
        with contextlib.suppress(OSError):
            ppath.unlink()
        return plan_shards(total, max_shards)

    # 没有进度文件：全新开始（或由调用方走单线续传）。
    return plan_shards(total, max_shards)


def _sync_write_at(path: Path, offset: int, data: bytes) -> None:
    """在指定偏移写入数据（阻塞版，供 to_thread 调用）。"""
    with path.open("r+b") as fh:
        fh.seek(offset)
        fh.write(data)


async def _download_one_shard(
    client: httpx.AsyncClient,
    url: str,
    base_headers: dict[str, str],
    partial: Path,
    shard: list[int],
    chunk_size: int,
    cancel_event: Any,
    on_bytes: Any,
) -> None:
    """下载单个分片。断点从 ``start + done`` 继续。"""
    start, end, done = shard
    pos = start + done

    while pos <= end:
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError()
        headers = dict(base_headers)
        headers["Range"] = f"bytes={pos}-{end}"

        async with client.stream("GET", url, headers=headers) as r:
            if r.status_code == 416:  # Range 不满足 —— 说明本地进度比远端还长
                shard[2] = end - start + 1
                return
            r.raise_for_status()
            if r.status_code != 206:
                # 服务端拒绝分片（返回整个文件），交给上层回退单线
                raise ShardingNotSupportedError("server returned 200 instead of 206")

            async for chunk in r.aiter_bytes(chunk_size):
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError()
                if not chunk:
                    continue
                await asyncio.to_thread(_sync_write_at, partial, pos, chunk)
                pos += len(chunk)
                shard[2] = pos - start
                if on_bytes is not None:
                    on_bytes(len(chunk))
    shard[2] = end - start + 1


class ShardingNotSupportedError(RuntimeError):
    """服务端不支持分片下载，调用方应退回单线流程。"""


async def download_sharded(
    *,
    client: httpx.AsyncClient,
    url: str,
    base_headers: dict[str, str] | None,
    partial: Path,
    task: Any,
    chunk_size: int,
    max_shards: int = DEFAULT_MAX_SHARDS,
    emit=None,
) -> bool:
    """尝试分片并发下载。

    返回 ``True`` 表示分片下载成功完成（字节已就位，交给上层校验）；
    返回 ``False`` 表示不适用，调用方应走原来的单线流程；
    抛出其他异常表示分片失败，调用方需截断到连续前缀后回退单线。
    """
    ok, total = await probe_range_support(client, url, base_headers)
    if not ok:
        return False
    if total < MIN_TOTAL_BYTES:
        return False

    partial.parent.mkdir(parents=True, exist_ok=True)
    plan = _load_or_create_plan(partial, total, max_shards)

    # 预分配文件（稀疏占位），保证各分片 seek 写入时文件足够长
    if not partial.exists() or partial.stat().st_size != total:
        with contextlib.suppress(OSError), partial.open("wb") as fh:
            fh.truncate(total)

    task.total_bytes = total
    base = int(getattr(task, "downloaded_bytes", 0) or 0)
    # 已有进度要计入起点，否则进度条会从 0 跳回
    carried = plan.done_bytes
    task.downloaded_bytes = max(base, carried)

    loop = asyncio.get_running_loop()
    last_tick = loop.time()
    last_bytes = task.downloaded_bytes

    def _on_bytes(n: int) -> None:
        task.downloaded_bytes += n

    async def _tick() -> None:
        nonlocal last_tick, last_bytes
        now = loop.time()
        if now - last_tick >= 0.25:
            elapsed = max(now - last_tick, 1e-6)
            task.speed_bps = (task.downloaded_bytes - last_bytes) / elapsed
            last_tick = now
            last_bytes = task.downloaded_bytes
            if emit is not None:
                await emit(task, "progress")

    pending = [s for s in plan.shards if s[2] < (s[1] - s[0] + 1)]
    if not pending:
        return True

    sem = asyncio.Semaphore(min(max_shards, len(pending)))
    ppath = _progress_path(partial)
    stop_tick = asyncio.Event()

    async def _tick_loop() -> None:
        while not stop_tick.is_set():
            await _tick()
            await asyncio.sleep(0.25)

    ticker = asyncio.create_task(_tick_loop())

    async def _save_progress() -> None:
        tmp = ppath.with_name(ppath.name + ".tmp")
        try:
            data = json.dumps(plan.to_json())
            await asyncio.to_thread(tmp.write_text, data, "utf-8")
            tmp.replace(ppath)
        except Exception:  # noqa: BLE001 — 进度写失败不影响下载本身
            with contextlib.suppress(OSError):
                tmp.unlink()

    async def _worker(shard: list[int]) -> None:
        async with sem:
            await _download_one_shard(
                client, url, base_headers or {}, partial, shard,
                chunk_size, getattr(task, "cancel_event", None), _on_bytes,
            )

    try:
        results = await asyncio.gather(
            *(_worker(s) for s in pending), return_exceptions=True
        )
    finally:
        stop_tick.set()
        with contextlib.suppress(Exception):
            await ticker
        await _save_progress()

    for r in results:
        if isinstance(r, ShardingNotSupportedError):
            return False
        if isinstance(r, asyncio.CancelledError):
            raise r
        if isinstance(r, BaseException):
            raise r

    # 全部完成 → 清理进度文件
    with contextlib.suppress(OSError):
        ppath.unlink()
    task.downloaded_bytes = total
    return True


def truncate_to_prefix(partial: Path) -> int:
    """回退单线前调用：把 partial 截断到连续已完成的字节数。

    返回截断后的长度。没有进度文件时原样返回当前长度。
    """
    ppath = _progress_path(partial)
    if not ppath.exists():
        return partial.stat().st_size if partial.exists() else 0
    with contextlib.suppress(OSError, json.JSONDecodeError), ppath.open(
        encoding="utf-8"
    ) as fh:
        plan = ShardPlan.from_json(json.load(fh))
    prefix = plan.completed_prefix() if plan else 0
    with contextlib.suppress(OSError):
        if not partial.exists():
            partial.open("wb").close()
        with partial.open("r+b") as f:
            f.truncate(prefix)
        os.fsync  # noqa: B018 — 保持引用，避免被 linter 误删 import
    with contextlib.suppress(OSError):
        ppath.unlink()
    return prefix
