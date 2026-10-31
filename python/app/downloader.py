"""Resumable streaming downloads for Kevrai Omni.

Design goals:
    * Stream to a `.partial` sibling, fsync, then atomic-rename on success.
    * Resume from existing `.partial` via `Range: bytes={n}-`.
    * SHA-256 verify on completion (mandatory when caller supplied an expected hash).
    * Concurrency cap via `asyncio.Semaphore` (configurable per-Downloader).
    * Cooperative cancellation — `cancel(task_id)` flips a flag; the
      streaming loop checks the flag after every chunk.
    * **No host blocklist**: any http(s) URL is acceptable. An optional
      `enforce_host_allowlist` setting (default OFF) re-enables the
      positive allowlist from `app.catalog` for users who want stricter
      behaviour.
    * **Multi-source auto-pick**: callers may pass `candidates=[url1, ...]`
      and the Downloader speed-tests them, then streams from the fastest.
    * Per-task progress emitted through an `asyncio.Queue` the consumer can `await`.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .catalog import (
    DEFAULT_BLOCKED_MIRRORS,
    DEFAULT_MODEL_HOSTS,
    is_host_allowed,
)
from .download_shards import (
    DEFAULT_MAX_SHARDS,
    MAX_SHARDS,
    ShardingNotSupportedError,
    download_sharded,
    truncate_to_prefix,
)


def _host_is_blocked(url: str) -> bool:
    """True when the URL's host (case-insensitive, www-stripped) is on the
    hard-blocked mirror list (P0-3). Blocked hosts are refused UNCONDITIONALLY,
    independent of the positive allowlist setting."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if host.startswith("www."):
        host = host[4:]
    return host in DEFAULT_BLOCKED_MIRRORS


#: Link-local / private IP literals and well-known metadata endpoints that
#: must never be reachable by a downloader fetch. This is the classic SSRF
#: target set (cloud metadata 169.254.169.254, 10/8, 172.16/12, 192.168/16,
#: 0.0.0.0, fe80::/10, fc00::/7, and the IPv4-mapped equivalents).
#: Loopback (127.0.0.0/8, ::1) is deliberately NOT blocked — the downloader
#: may legitimately talk to a local model/mirror server, and the downloader
#: runs on the same host that a loopback target would reach anyway. The
#: credential-exfil vector is separately closed by the allowlist enforced for
#: auth-bearing requests (P0-2).
_SSRF_IP_NETS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),
)

_SSRF_LOCAL_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata",
    "instance-data",
})


def _host_is_ssrf_target(url: str) -> bool:
    """True when the URL targets an SSRF-prone host: a private / loopback /
    link-local IP literal, a localhost-like name, or a bare IPv4/IPv6 literal
    in a reserved range. Checked on every URL and every redirect hop so a
    trusted host cannot 302 us onto cloud metadata or an internal service."""
    try:
        host = (urlparse(url).hostname or "").strip("[]").lower()
    except Exception:
        return False
    if not host:
        return False
    if host in _SSRF_LOCAL_HOSTNAMES:
        return True
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) → strip the prefix before parsing.
    if host.startswith("::ffff:"):
        host = host[7:]
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False  # a hostname (not a literal) — resolved later if at all
    return any(addr in net for net in _SSRF_IP_NETS)


class DownloadStatus(str, Enum):
    """任务状态机。

    PENDING → DOWNLOADING ⇄ PAUSED → VERIFYING → DONE
                  │
                  ├→ FAILED
                  ├→ CANCELLED（用户主动取消）
                  └→ EXPIRED（看门狗判定"无推进"）
    """
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    VERIFYING = "verifying"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    #: 看门狗判定为"长时间没有任何字节推进"的悬挂任务。
    EXPIRED = "expired"


#: 终态：任务不会再自己改变状态。
TERMINAL_STATUSES: frozenset[DownloadStatus] = frozenset(
    {
        DownloadStatus.DONE,
        DownloadStatus.FAILED,
        DownloadStatus.CANCELLED,
        DownloadStatus.EXPIRED,
    }
)

#: 看门狗：进行中任务多久没有任何推进就算悬挂。
#:
#: 判据是"**是否有推进**"而不是"谁持有引用"。一个任务的 asyncio.Task
#: 可能因为 sidecar 内部异常而永远不再被 await，引用还在、字典里也有它，
#: 但它其实已经死了 —— 引用计数看不出这件事，只有推进能看出来。
DEFAULT_STALL_TIMEOUT_S = 600.0
#: 看门狗巡检间隔。
DEFAULT_WATCHDOG_INTERVAL_S = 30.0
#: 磁盘预留空间：剩余低于此值时拒绝新下载。
#:
#: 不预检的后果是"下到一半才发现盘满了"。那时任务已经跑了几小时，失败
#: 的代价远高于启动时拒绝。
DEFAULT_MIN_FREE_BYTES = 2 * 1024**3
class InsufficientDiskSpace(RuntimeError):
    """磁盘剩余空间不足以容纳这次下载。"""

    def __init__(self, path: str, free: int, needed: int):
        super().__init__(
            f"磁盘空间不足：目标位置 {path} 剩余 {free / 1024**3:.1f} GB，"
            f"本次下载至少需要 {needed / 1024**3:.1f} GB"
        )
        self.path = path
        self.free = free
        self.needed = needed


@dataclass(frozen=True)
class OrphanPartial:
    """上次进程遗留的半截文件。"""

    partial: Path
    #: 已下载的字节数。
    size: int
    #: 距最后一次写入过去了多少秒。
    age_s: float
    #: 是否已被判定为"陈旧"（超过 STALE_PARTIAL_AGE_S）。
    stale: bool


@dataclass(frozen=True)
class RecoveryPlan:
    """对一个孤儿 partial 的处置方案。plan_recovery 的产物，本身不做任何动作。"""

    partial: Path
    strategy: RecoveryStrategy
    action: str
    reason: str
    delete_partial: bool


#: HF resolve/main 会随上游推送漂移到新 commit。钉住 revision 后，同一个
#: URL 永远返回同一份字节 —— 这是"哈希校验"能成立的前提（否则哈希本身也
#: 会随上游更新而变，校验变成形同虚设）。
_HF_RESOLVE_RE = re.compile(
    r"^(?P<prefix>https://[^/]+)/(?P<repo>[^/]+/[^/]+)/resolve/(?P<rev>[^/]+)/(?P<rest>.+)$"
)


def pin_revision(url: str, revision: str) -> str:
    """把一个 HF resolve URL 的 revision 钉到指定 commit。

    非 HF resolve 形式的 URL 原样返回 —— 不能因为想钉就破坏别的源。
    """
    if not revision:
        return url
    m = _HF_RESOLVE_RE.match(url)
    if not m:
        return url
    return (
        f"{m.group('prefix')}/{m.group('repo')}/resolve/{revision}/{m.group('rest')}"
    )


def is_revision_pinned(url: str) -> bool:
    """URL 里的 revision 是否已经是 40 位 commit sha（而不是 main 这类分支名）。"""
    m = _HF_RESOLVE_RE.match(url)
    if not m:
        return False
    return bool(re.fullmatch(r"[0-9a-f]{40}", m.group("rev")))


def free_disk_bytes(path: str | os.PathLike[str]) -> int:
    """返回目标路径所在磁盘的剩余字节数。取不到就返回 0（拒绝而非放行）。"""
    try:
        st = os.statvfs(os.fspath(path))
    except OSError:
        return 0
    return st.f_bavail * st.f_frsize


def dir_free_bytes(path: str | os.PathLike[str]) -> int:
    """返回目标文件**父目录**所在磁盘的剩余字节数。

    父目录可能还不存在（首次下载），逐级向上找第一个存在的祖先。
    """
    p = Path(path).expanduser()
    cur = p.parent if p.suffix else p
    for _ in range(8):
        if cur.exists():
            return free_disk_bytes(cur)
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return free_disk_bytes(Path.cwd())


class RecoveryStrategy(str, Enum):
    """sidecar 重启后，磁盘上半截任务的处置方式。

    Cherry Studio 的 JobManager 要求每种任务类型**显式声明**恢复策略，因为
    不同任务的"自动续跑"代价差别巨大：重新嵌入一个知识库要花真金白银的
    API 费用，而重新下载一个文件只是带宽。本项目把它下沉到每个任务上。
    """

    #: 自动续传：保留 .partial，重启后接着下。要求进度文件可靠。
    RESUME = "resume"
    #: 标记失败，等用户手动重试。用于自动续跑可能有代价/有风险的场景。
    MANUAL = "manual"
    #: 丢弃半截文件，下次从头开始。用于源可能已变的场景。
    DISCARD = "discard"


#: 默认策略。下载是幂等的纯带宽消耗，自动续传代价最低，故默认 RESUME。
DEFAULT_RECOVERY = RecoveryStrategy.RESUME

#: 半截文件超过这个时长没被碰过，就认为它是"上一次进程遗留"而不是刚中断的。
#:
#: 判断依据只能是 mtime —— 进程重启后内存里的所有状态都没了，磁盘上的
#: mtime 是唯一能说明"最后一次有人动它是什么时候"的证据。
STALE_PARTIAL_AGE_S = 3600.0

#: 被视为"有推进"的最小新增字节数。
#:
#: 不能是 >0：某些源会每隔几秒发一个 keep-alive 字节，任务看起来一直在动，
#: 实际带宽接近 0。用阈值能识别这种"假推进"。
DEFAULT_STALL_MIN_BYTES = 64 * 1024

#: 终态任务在内存里的保留上限。
#:
#: 以前 ``_tasks`` 只增不减 —— 每个下载过的文件都永久留下一个 DownloadTask，
#: 连带它的 asyncio.Queue / Event 和 ``extra_headers``（里面有 gated 仓库的
#: Bearer token）。下几十个模型、成百上千个文件后，字典无限增长，前端轮询
#: ``list_tasks()`` 也越来越慢，而 token 一直驻留内存。
#:
#: 保留最近 N 个终态任务（按完成时间淘汰最老的），既能让前端读到刚完成的
#: 校验结果，又不会无界增长。进行中的任务**永不**被淘汰。
DEFAULT_MAX_RETAINED_FINISHED = 200


# ---------------------------------------------------------------------------
# URL validation (permissive by default)
# ---------------------------------------------------------------------------


class DownloadRefused(Exception):
    """Raised when URL fails validation (unsupported scheme, etc.)."""

class Sha256MismatchError(Exception):
    """Raised when the assembled ``.partial`` does not match the expected hash.

    Raised only on the *last* candidate: earlier mismatches fall back to the
    next mirror (which starts from a fresh, deleted partial) instead. The
    ``.partial`` is removed by the pipeline before this escapes, so a corrupt
    blob is never resumed onto a subsequent source.
    """


class DownloadAllCandidatesFailed(Exception):
    """Raised when every candidate URL (mirror) has been tried and failed.

    Carries the per-source error strings so the caller (and the task's
    ``error`` field) gets an aggregate, human-readable summary instead of only
    the last source's complaint.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors: list[str] = list(errors)
        super().__init__(" | ".join(self.errors) if self.errors else "all sources failed")


def _check_url(url: str, *, enforce_allowlist: bool = False,
               has_auth_header: bool = False) -> None:
    """Validate a URL.

    Permissive by default — only the scheme is checked. Pass
    ``enforce_allowlist=True`` to additionally require the host to be in
    ``DEFAULT_MODEL_HOSTS``.

    P0-2: when ``has_auth_header`` is True (the request will carry an
    ``Authorization`` credential such as a gated HF Bearer token), the host
    allowlist is FORCE-ENABLED regardless of the caller's opt-in. A credential
    must never be sent to a host outside the curated trusted set — otherwise a
    caller could trick the sidecar into exfiltrating the user's token to an
    arbitrary URL.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise DownloadRefused(f"unsupported scheme {parsed.scheme!r}")
    if not parsed.hostname:
        raise DownloadRefused(f"url has no host: {url!r}")
    # P0-3: hard-blocked mirrors are always refused, even when the positive
    # allowlist is otherwise permissive.
    if _host_is_blocked(url):
        raise DownloadRefused(f"host is on the blocked mirror list: {parsed.hostname}")
    # H2: never fetch a private / loopback / link-local / cloud-metadata
    # target, regardless of allowlist — closes the classic SSRF.
    if _host_is_ssrf_target(url):
        raise DownloadRefused(f"host is not fetchable (SSRF target): {parsed.hostname}")
    if has_auth_header:
        enforce_allowlist = True
    if enforce_allowlist and not is_host_allowed(url, DEFAULT_MODEL_HOSTS):
        raise DownloadRefused(
            "host not in allowlist (credential-bearing downloads are restricted "
            f"to trusted hosts): {parsed.hostname}"
        )


# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------


@dataclass
class DownloadTask:
    id: str
    url: str
    dest_path: str
    expected_sha256: str | None
    total_bytes: int = 0
    downloaded_bytes: int = 0
    status: DownloadStatus = DownloadStatus.PENDING
    error: str | None = None
    started_at: float = 0.0
    finished_at: float = 0.0
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    #: 最近一次"有推进"的时间戳（monotonic）。看门狗据此判定悬挂。
    last_progress_at: float = field(default_factory=time.monotonic)
    #: 看门狗记录的最近一次已确认字节数，用于判断是否达到"有推进"阈值。
    _stall_mark_bytes: int = 0
    #: 跨进程重启后的恢复策略。见 RecoveryStrategy。
    recovery: RecoveryStrategy = DEFAULT_RECOVERY
    #: 期望的模型 revision（commit sha）。锁定了才不会被上游静默换内容。
    expected_revision: str | None = None
    #: 完整性校验失败过（哈希对不上）。比"下载失败"严重一个量级。
    integrity_failed: bool = False
    speed_bps: float = 0.0
    final_sha256: str | None = None
    # Ordered fallback chain of mirror URLs for the *same* file. ``url`` always
    # equals ``candidates[0]``; when a source exhausts its in-URL retries the
    # pipeline advances to the next entry, preserving the ``.partial`` bytes
    # (Range resume). Empty list => single-source legacy behaviour.
    candidates: list[str] = field(default_factory=list)
    # Index into ``candidates`` currently being streamed (0-based).
    source_index: int = 0
    # How many times the pipeline has fallen back to a different mirror.
    fallbacks: int = 0
    # Extra request headers (e.g. Authorization for gated HF repos).
    # Never serialized into snapshots — tokens must not leak to the UI/logs.
    extra_headers: dict[str, str] = field(default_factory=dict, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "dest_path": self.dest_path,
            "status": self.status.value,
            "total_bytes": self.total_bytes,
            "downloaded_bytes": self.downloaded_bytes,
            "ratio": (self.downloaded_bytes / self.total_bytes) if self.total_bytes else 0.0,
            "speed_bps": self.speed_bps,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "expected_sha256": self.expected_sha256,
            "final_sha256": self.final_sha256,
            "source_index": self.source_index,
            "fallbacks": self.fallbacks,
            "candidates": list(self.candidates),
            "integrity_failed": self.integrity_failed,
            "expected_revision": self.expected_revision,
        }


# ---------------------------------------------------------------------------
# Downloader
# ---------------------------------------------------------------------------


DEFAULT_CHUNK = 1 << 20  # 1 MiB
DEFAULT_TIMEOUT = httpx.Timeout(30.0, read=120.0, connect=10.0, write=10.0)

#: HTTP statuses worth retrying on a fresh connection (mirrors hub.net).
#: 4xx other than 429 are provably useless to retry.
STREAM_RETRY_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})
#: Default number of *extra* attempts after the first (i.e. 3 total tries).
DEFAULT_STREAM_RETRIES = 3
#: Exponential backoff base delays (seconds) between retry attempts.
DEFAULT_STREAM_BACKOFF: tuple[float, ...] = (0.5, 1.0, 2.0)


class Downloader:
    """Manages a set of resumable streaming downloads."""

    def __init__(
        self,
        *,
        max_concurrent: int = 3,
        allowed_hosts: set[str] | None = None,
        extra_allowed_hosts: set[str] | None = None,
        client: httpx.AsyncClient | None = None,
        chunk_size: int = DEFAULT_CHUNK,
        stream_retries: int = DEFAULT_STREAM_RETRIES,
        stream_backoff: tuple[float, ...] = DEFAULT_STREAM_BACKOFF,
        shard_downloads: bool = True,
        max_shards: int = DEFAULT_MAX_SHARDS,
        max_retained_finished: int = DEFAULT_MAX_RETAINED_FINISHED,
        stall_timeout_s: float = DEFAULT_STALL_TIMEOUT_S,
        watchdog_interval_s: float = DEFAULT_WATCHDOG_INTERVAL_S,
        stall_min_bytes: int = DEFAULT_STALL_MIN_BYTES,
        min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    ) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        # 大文件分片并发下载。仅在服务器声明 Accept-Ranges 且文件足够大时生效，
        # 其余情况自动退回单线，行为与开启前完全一致。
        self.shard_downloads = bool(shard_downloads)
        self.max_shards = max(1, min(int(max_shards or 1), MAX_SHARDS))
        # v2.4.1 fix: exposed for main.put_settings concurrency compare —
        # previously absent, making every PUT /api/settings raise AttributeError.
        self.max_concurrent = max_concurrent
        self._tasks: dict[str, DownloadTask] = {}
        self._tasks_lock = asyncio.Lock()
        # 终态任务的保留上限（见 DEFAULT_MAX_RETAINED_FINISHED 的说明）。
        self._max_retained_finished = max(1, int(max_retained_finished or 1))
        # --- 看门狗：把"无推进"的进行中任务转 EXPIRED ---
        self._stall_timeout = float(stall_timeout_s or 0)
        self._watchdog_interval = max(1.0, float(watchdog_interval_s or 0))
        self._stall_min_bytes = max(0, int(stall_min_bytes or 0))
        # <=0 表示禁用看门狗（测试与不需要的部署场景）。
        self._watchdog_enabled = self._stall_timeout > 0
        self._watchdog_task: asyncio.Task | None = None
        # 磁盘预留：<=0 表示不做预检。
        self._min_free_bytes = max(0, int(min_free_bytes or 0))
        # Single-flight by resolved destination: a second `start()` for the same
        # file while one is already in flight must not open a second writer onto
        # the same `.partial` (the old check only looked at the *final* dest,
        # which does not exist until the atomic rename, so two concurrent starts
        # raced on the partial file and interleaved/corrupted it).
        self._inflight_dests: dict[str, str] = {}
        # Strong references for fire-and-forget tasks; without these the event
        # loop can garbage-collect an in-flight download task mid-run.
        self._bg_tasks: set[asyncio.Task] = set()
        base = allowed_hosts or DEFAULT_MODEL_HOSTS
        if extra_allowed_hosts:
            self._allowed_hosts = set(base) | set(extra_allowed_hosts)
        else:
            self._allowed_hosts = set(base)
        self._enforce_allowlist = bool(allowed_hosts)  # if caller passed set, enforce
        self._client = client
        self._client_owned = client is None
        # Connection pool built lazily in `_get_client` when no client was
        # injected. Declared here (rather than first assigned inside the method)
        # so `aclose` can simply test for None instead of `getattr`/`hasattr`.
        self._own_client: httpx.AsyncClient | None = None
        self._chunk_size = chunk_size
        self._stream_retries = max(0, int(stream_retries))
        self._stream_backoff = tuple(b for b in stream_backoff if b >= 0) or (0.5,)

    # --- public ---

    async def start(
        self,
        url: str,
        dest: str | os.PathLike[str],
        sha256: str | None = None,
        extra_headers: dict[str, str] | None = None,
        candidates: list[str] | None = None,
        declared_bytes: int | None = None,
    ) -> str:
        """Start a new download. Returns the task_id.

        `extra_headers` are sent with every request for this task (used for
        e.g. `Authorization: Bearer <hf_token>` on gated HuggingFace repos).
        Headers are never echoed into progress snapshots.

        `candidates` is an *ordered* list of equivalent mirror URLs for the
        **same file** (e.g. ``[huggingface.co/..., hf-mirror.com/..., ...]``).
        The primary `url` is always tried first; after its in-URL retries are
        exhausted the pipeline advances to the next candidate, keeping the
        ``.partial`` bytes and resuming via ``Range``. Omitted/empty preserves
        the legacy single-source behaviour.

        Raises `DownloadRefused` only for unsupported scheme / missing host.
        Raises `FileExistsError` if the destination already exists.
        Raises `InsufficientDiskSpace` if the target disk cannot hold the file.
        """
        dest_path = Path(dest).expanduser().resolve()
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # 磁盘预检：启动前拒绝，好过下到一半才发现盘满。
        if self._min_free_bytes > 0:
            self._check_disk_space(dest_path, declared_bytes)
        # Build the ordered fallback chain: primary first, then deduped extras.
        ordered = self._build_candidate_chain(url, candidates)
        # Validate url. When the task carries an Authorization credential
        # (gated HF Bearer), _check_url force-enables the host allowlist so the
        # token is never sent to an arbitrary host (P0-2).
        has_auth = any(str(k).lower() == "authorization" for k in (extra_headers or {}))
        # Validate every candidate up-front (fail fast on a bad mirror URL).
        for u in ordered:
            self._check_url(u, has_auth_header=has_auth)
        if dest_path.exists():
            raise FileExistsError(str(dest_path))

        task = DownloadTask(
            id=uuid.uuid4().hex,
            url=ordered[0],
            dest_path=str(dest_path),
            expected_sha256=sha256,
            started_at=time.time(),
            extra_headers=dict(extra_headers or {}),
            candidates=ordered,
        )
        async with self._tasks_lock:
            # Single-flight: if an earlier task is already downloading this exact
            # destination (only the `.partial` exists so far), hand back its id
            # instead of opening a second writer onto the same partial.
            existing_id = self._inflight_dests.get(str(dest_path))
            existing = self._tasks.get(existing_id) if existing_id else None
            if existing is not None and existing.status not in TERMINAL_STATUSES:
                return existing.id
            self._tasks[task.id] = task
            self._inflight_dests[str(dest_path)] = task.id

        # Run in background without awaiting — caller polls via `progress()` /
        # subscribes to `task.queue`.
        run = asyncio.create_task(self._run(task))
        self._bg_tasks.add(run)
        run.add_done_callback(self._bg_tasks.discard)
        self._ensure_watchdog()
        return task.id

    async def cancel(self, task_id: str) -> bool:
        async with self._tasks_lock:
            t = self._tasks.get(task_id)
        if t is None:
            return False
        if t.status in TERMINAL_STATUSES:
            return True
        t.cancel_event.set()
        return True

    async def progress(self, task_id: str) -> dict[str, Any] | None:
        async with self._tasks_lock:
            t = self._tasks.get(task_id)
        if t is None:
            return None
        return t.snapshot()

    def _check_disk_space(
        self, dest_path: Path, declared_bytes: int | None = None
    ) -> None:
        """校验目标磁盘剩余空间。不足则抛 InsufficientDiskSpace。

        `declared_bytes` 来自 catalog 里登记的已知大小。未知时**只用预留值
        校验，不猜大小** —— 拿一个"保守估计值"去拒绝，会把小文件下载一起
        挡掉（典型：磁盘只剩 5GB，一个 200MB 的模型也下不了）。

        大小未知的情况由 `_check_disk_space_midflight` 兜底：一旦响应头给出
        了真实长度，就在早期再校验一次。
        """
        # 开关放在这里而不是调用方：预检被禁用时，任何调用路径都不该拒绝。
        if self._min_free_bytes <= 0:
            return
        needed = int(declared_bytes or 0)
        if needed < 0:
            needed = 0
        # 已有部分下载时，只需要补上差额。
        partial = Path(str(dest_path) + ".partial")
        try:
            have = partial.stat().st_size if partial.exists() else 0
        except OSError:
            have = 0
        needed = max(0, needed - have)

        free = dir_free_bytes(dest_path)
        # 取不到剩余空间（0）时按"不足"处理：宁可拒绝，也不让任务跑几小时后
        # 才发现盘满。
        if free < needed + self._min_free_bytes:
            raise InsufficientDiskSpace(
                str(dest_path), free, needed + self._min_free_bytes
            )

    @staticmethod
    def scan_orphan_partials(
        directory: str | os.PathLike[str],
        stale_after_s: float = STALE_PARTIAL_AGE_S,
        now: float | None = None,
    ) -> list[OrphanPartial]:
        """扫描目录里上次进程遗留的半截文件。

        进程重启后内存状态全部丢失，磁盘上的 `.partial` 是唯一证据。这里不
        做恢复动作，只把"有哪些、多久没动、按当前策略该怎么处置"列出来，
        由调用方（sidecar 启动流程）决定。

        找不到进度文件、`mtime` 取不到等异常一律按"无法判定"处理并原样
        列出，绝不静默丢弃用户的已下载字节。
        """
        now = time.time() if now is None else now
        root = Path(directory)
        out: list[OrphanPartial] = []
        if not root.is_dir():
            return out
        try:
            entries = sorted(root.rglob("*.partial"))
        except OSError:
            return out
        for p in entries:
            try:
                st = p.stat()
                size = st.st_size
                age = max(0.0, now - st.st_mtime)
            except OSError:
                # 取不到就按"刚中断 + 大小未知"列出，交给调用方决定。
                out.append(OrphanPartial(p, 0, 0.0, False))
                continue
            stale = age >= stale_after_s
            out.append(OrphanPartial(p, size, age, stale))
        return out

    @classmethod
    def plan_recovery(
        cls,
        orphan: OrphanPartial,
        strategy: RecoveryStrategy = DEFAULT_RECOVERY,
    ) -> RecoveryPlan:
        """按策略给出对一个孤儿 partial 的处置方案（纯函数，不动磁盘）。"""
        if strategy is RecoveryStrategy.RESUME:
            return RecoveryPlan(
                orphan.partial,
                strategy,
                "resume",
                f"保留已下载的 {orphan.size / 1024**2:.1f} MB，从断点继续",
                delete_partial=False,
            )
        if strategy is RecoveryStrategy.MANUAL:
            return RecoveryPlan(
                orphan.partial,
                strategy,
                "mark-failed",
                "标记为失败，等用户在界面上手动重试",
                delete_partial=False,
            )
        return RecoveryPlan(
            orphan.partial,
            strategy,
            "discard",
            "丢弃半截文件，下次从头下载",
            delete_partial=True,
        )

    def _check_disk_space_midflight(
        self, dest_path: Path, total_bytes: int, have_bytes: int
    ) -> None:
        """响应头给出真实长度后的早期校验。

        启动前不知道大小，拿到 Content-Length 就知道能不能装下了。这时失败
        的代价远小于"下到 99% 才发现盘满"。
        """
        if self._min_free_bytes <= 0 or total_bytes <= 0:
            return
        remaining = max(0, total_bytes - have_bytes)
        free = dir_free_bytes(dest_path)
        if free < remaining + self._min_free_bytes:
            raise InsufficientDiskSpace(
                str(dest_path), free, remaining + self._min_free_bytes
            )

    def _ensure_watchdog(self) -> None:
        """懒启动看门狗。没有进行中任务时它自己退出，不留常驻后台任务。"""
        if not self._watchdog_enabled:
            return
        t = self._watchdog_task
        if t is not None and not t.done():
            return
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        """定期巡检：把长时间无推进的进行中任务转成 EXPIRED。"""
        try:
            while True:
                await asyncio.sleep(self._watchdog_interval)
                expired = await self.reap_stalled()
                # 没有进行中任务了就退出，避免常驻。
                if not expired and not await self._has_inflight():
                    return
        except asyncio.CancelledError:
            raise
        finally:
            self._watchdog_task = None

    async def _has_inflight(self) -> bool:
        async with self._tasks_lock:
            return any(
                t.status not in TERMINAL_STATUSES for t in self._tasks.values()
            )

    def mark_progress(self, task: DownloadTask, downloaded: int) -> bool:
        """记录一次推进。返回是否足以刷新"存活"时间戳。

        只有**新增字节达到阈值**才算推进。某些源会持续发极少量
        keep-alive 字节，看起来一直在动、实际带宽接近 0；只判断"字节数变了"
        会让这种假推进永远骗过看门狗。
        """
        gained = downloaded - task._stall_mark_bytes
        if gained < self._stall_min_bytes:
            return False
        task._stall_mark_bytes = downloaded
        task.last_progress_at = time.monotonic()
        return True

    async def reap_stalled(self, now: float | None = None) -> list[str]:
        """把所有超时无推进的进行中任务转 EXPIRED，返回被回收的任务 id。

        转 EXPIRED 前会先置 cancel_event：底层的下载协程可能还卡在读超时里，
        不主动取消它会继续写 .partial，和后续重试打架。
        """
        if not self._watchdog_enabled:
            return []
        now = time.monotonic() if now is None else now
        doomed: list[DownloadTask] = []
        async with self._tasks_lock:
            for t in self._tasks.values():
                if t.status in TERMINAL_STATUSES:
                    continue
                if now - t.last_progress_at >= self._stall_timeout:
                    doomed.append(t)
        out: list[str] = []
        for t in doomed:
            t.cancel_event.set()
            t.status = DownloadStatus.EXPIRED
            t.finished_at = time.time()
            t.error = (
                f"stalled: no progress for "
                f"{now - t.last_progress_at:.0f}s"
            )
            await self._emit(t, "expired", extra={"error": t.error})
            out.append(t.id)
        if out:
            async with self._tasks_lock:
                for t in doomed:
                    t.extra_headers.clear()
                self._prune_finished_locked()
        return out

    def _prune_finished_locked(self) -> int:
        """淘汰最老的终态任务。调用方必须持有 ``_tasks_lock``。

        只淘汰终态（DONE/FAILED/CANCELLED）；进行中的任务一律保留 —— 淘汰
        它们会让调用方拿到 404，而下载其实还在跑。

        返回被淘汰的数量。
        """
        finished = [t for t in self._tasks.values() if t.status in TERMINAL_STATUSES]
        if len(finished) <= self._max_retained_finished:
            return 0
        # 按完成时间升序，最老的先淘汰。finished_at 为 0（异常路径没设到）
        # 时用 started_at 兜底，保证排序稳定可预期。
        finished.sort(key=lambda t: (t.finished_at or 0.0, t.started_at or 0.0))
        doomed = finished[: len(finished) - self._max_retained_finished]
        for t in doomed:
            t.extra_headers.clear()
            self._tasks.pop(t.id, None)
        return len(doomed)

    async def prune_finished(self) -> int:
        """主动淘汰多余的终态任务，返回淘汰数量。"""
        async with self._tasks_lock:
            return self._prune_finished_locked()

    async def forget(self, task_id: str) -> bool:
        """显式丢弃一条任务记录。

        只对终态任务生效：进行中的任务被丢弃后调用方再也查不到它，而下载
        仍在后台跑，等于凭空丢失一个可取消的句柄。
        """
        async with self._tasks_lock:
            t = self._tasks.get(task_id)
            if t is None or t.status not in TERMINAL_STATUSES:
                return False
            t.extra_headers.clear()
            self._tasks.pop(task_id, None)
            return True

    async def list_tasks(self) -> list[dict[str, Any]]:
        async with self._tasks_lock:
            return [t.snapshot() for t in self._tasks.values()]

    def get_task(self, task_id: str) -> DownloadTask | None:
        """Sync accessor for tasks (used by FastAPI handlers)."""
        return self._tasks.get(task_id)

    async def aclose(self) -> None:
        if self._client_owned and self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
        # ``_own_client`` 是未注入 client 时自建的连接池。此前这里只关了
        # ``self._client``，导致每次 put_settings 重建 Downloader（并发数
        # 变更）都会泄漏一个 httpx 连接池（含 keep-alive socket）。
        if self._own_client is not None:
            with contextlib.suppress(Exception):
                await self._own_client.aclose()
            self._own_client = None

    # --- internals ---

    def _check_url(self, url: str, *, has_auth_header: bool = False) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise DownloadRefused(f"unsupported scheme {parsed.scheme!r}")
        if not parsed.hostname:
            raise DownloadRefused(f"url has no host: {url!r}")
        # P0-3: hard-blocked mirrors are always refused.
        if _host_is_blocked(url):
            raise DownloadRefused(f"host is on the blocked mirror list: {parsed.hostname}")
        # H2: never fetch a private / loopback / link-local / metadata target.
        if _host_is_ssrf_target(url):
            raise DownloadRefused(f"host is not fetchable (SSRF target): {parsed.hostname}")
        # Positive allowlist enforced when the caller opted in OR when the task
        # carries an Authorization credential (P0-2): never leak a Bearer token
        # to a host outside the trusted set.
        enforce = self._enforce_allowlist or has_auth_header
        if enforce and not is_host_allowed(url, self._allowed_hosts):
            raise DownloadRefused(f"host not in allowlist: {parsed.hostname}")

    def _build_candidate_chain(
        self, primary: str, candidates: list[str] | None,
    ) -> list[str]:
        """Ordered, de-duplicated fallback chain: ``primary`` first, then extras.

        Empty / blank entries are dropped. The primary is always retained even
        if it also appears in ``candidates`` (de-duped case-insensitively by
        exact URL string).
        """
        chain: list[str] = []
        seen: set[str] = set()
        for u in [primary, *(candidates or [])]:
            s = str(u or "").strip()
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            chain.append(s)
        if not chain:
            raise DownloadRefused("no candidate url provided")
        return chain

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._own_client is None:
            # follow_redirects=False: redirects are walked manually in `_stream`
            # so each hop is re-validated against the allowlist/blocklist (H2 —
            # a trusted host must not 302 us onto cloud metadata / a blocked or
            # phishing mirror).
            self._own_client = httpx.AsyncClient(
                follow_redirects=False, timeout=DEFAULT_TIMEOUT
            )
        return self._own_client

    async def _run(self, task: DownloadTask) -> None:
        partial = Path(task.dest_path + ".partial")

        try:
            async with self._sem:
                try:
                    await self._download_pipeline(task, partial)
                except asyncio.CancelledError:  # noqa: PERF203
                    # 看门狗可能已经把它判成 EXPIRED 了（并顺手置了
                    # cancel_event）。EXPIRED 比 CANCELLED 信息更具体 ——
                    # 一个是"用户点的"，一个是"自己卡死的"，不能互相覆盖。
                    if task.status is not DownloadStatus.EXPIRED:
                        task.status = DownloadStatus.CANCELLED
                        task.finished_at = time.time()
                        await self._emit(task, "cancelled")
                    return
                except DownloadRefused as e:
                    task.status = DownloadStatus.FAILED
                    task.error = str(e)
                    task.finished_at = time.time()
                    await self._emit(task, "failed", extra={"error": str(e)})
                    return
                except Exception as e:  # noqa: BLE001
                    if task.status is not DownloadStatus.EXPIRED:
                        # 取消优先于失败：用户点了取消之后，即便底层加载/推理
                        # 因为中断而抛异常，那也是取消的后果，不该报成 FAILED
                        # 吓用户一跳。
                        if task.cancel_event.is_set():
                            task.status = DownloadStatus.CANCELLED
                            task.error = None
                        else:
                            task.status = DownloadStatus.FAILED
                            task.error = f"{type(e).__name__}: {e}"
                        task.finished_at = time.time()
                        await self._emit(task, "failed", extra={"error": task.error})
                    return
        finally:
            # Release the single-flight slot for this destination so a later
            # (genuinely new) download to the same path can start.
            async with self._tasks_lock:
                if self._inflight_dests.get(str(task.dest_path)) == task.id:
                    self._inflight_dests.pop(str(task.dest_path), None)
                # 凭证用完即弃。任务对象要留着给前端查最终校验结果，但已经
                # 进入终态的任务再也不会发请求，Bearer token 不该继续驻留
                # 内存（gated 仓库的 token 属于长期凭据）。
                if task.status in TERMINAL_STATUSES:
                    task.extra_headers.clear()
                self._prune_finished_locked()

    async def _download_pipeline(self, task: DownloadTask, partial: Path) -> None:
        """Stream → verify → rename, with per-mirror fallback.

        For each URL in ``task.candidates`` (the primary first) we run the
        in-URL bounded-retry stream. When a source exhausts its retries we:

        * keep the ``.partial`` bytes and resume via ``Range`` on the next
          mirror (a transport drop / 5xx means the bytes we already have are
          almost certainly good);
        * but on a **sha256 mismatch** we *delete* the ``.partial`` (the
          assembled bytes are untrustworthy — we cannot know where corruption
          starts) and start the next mirror from byte 0.

        Cancellation and ``DownloadRefused`` propagate immediately; the former
        never triggers a fallback. After every candidate has failed we raise
        :class:`DownloadAllCandidatesFailed` with an aggregated error list.
        """
        ordered = task.candidates or [task.url]
        if not ordered:
            raise DownloadRefused("no candidate url provided")

        resume0 = partial.stat().st_size if partial.exists() else 0
        task.downloaded_bytes = resume0
        task._stall_mark_bytes = resume0
        task.last_progress_at = time.monotonic()
        await self._emit(task, "started", extra={
            "resume_from": resume0,
            "candidates": list(ordered),
        })

        errors: list[str] = []
        for idx, url in enumerate(ordered):
            if task.cancel_event.is_set():
                raise asyncio.CancelledError()
            task.url = url
            task.source_index = idx
            is_last = idx == len(ordered) - 1

            # --- stream this source (with its own in-URL backoff retries) ---
            try:
                # 大文件优先走分片并发；不适用时返回 False，退回原有单线流程。
                if not await self._try_sharded(task, partial):
                    await self._stream_with_retry(task, partial, 0)
            except asyncio.CancelledError:
                raise
            except DownloadRefused as e:
                errors.append(f"{url}: refused ({e})")
                if is_last or task.cancel_event.is_set():
                    raise DownloadAllCandidatesFailed(errors) from e
                await self._fallback(task, url, ordered, idx, "refused", errors)
                continue
            except Exception as e:  # noqa: BLE001 — transport exhaustion / terminal 4xx
                errors.append(f"{url}: {type(e).__name__}: {e}")
                if is_last or task.cancel_event.is_set():
                    # Every candidate exhausted: surface the full per-source
                    # summary rather than only the last source's complaint.
                    raise DownloadAllCandidatesFailed(errors) from e
                # Partial bytes are presumed good → keep them and Range-resume
                # onto the next mirror.
                await self._fallback(task, url, ordered, idx, type(e).__name__, errors)
                continue

            # --- stream body complete → verify integrity ---
            task.status = DownloadStatus.VERIFYING
            await self._emit(task, "verifying")
            try:
                actual = await self._sha256_file(partial)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{url}: sha256 read error: {e}")
                if is_last or task.cancel_event.is_set():
                    raise
                # Unreadable/corrupt partial — drop before retrying fresh.
                with contextlib.suppress(FileNotFoundError):
                    partial.unlink()
                task.final_sha256 = None
                task.downloaded_bytes = 0
                await self._fallback(task, url, ordered, idx, "sha256_read_error", errors)
                continue

            task.final_sha256 = actual
            if task.expected_sha256 and actual.lower() != task.expected_sha256.lower():
                errors.append(
                    f"{url}: sha256 mismatch expected {task.expected_sha256}, got {actual}"
                )
                # 完整性事件要单独发，不能只混在"下载失败"里。
                # "下到了错误的内容"比"没下下来"危险一个数量级：前者会被
                # 当成可用的模型加载，污染的是后续所有推理结果。
                task.integrity_failed = True
                await self._emit(
                    task,
                    "integrity_failed",
                    extra={
                        "url": url,
                        "expected": task.expected_sha256,
                        "actual": actual,
                        "severity": "critical",
                        "message": (
                            "文件哈希与期望值不符，已丢弃下载内容。"
                            "这可能是镜像源被污染或文件在传输中损坏。"
                        ),
                    },
                )
                if is_last or task.cancel_event.is_set():
                    # Last source: drop the corrupt partial and surface a
                    # clear integrity error.
                    with contextlib.suppress(FileNotFoundError):
                        partial.unlink()
                    task.final_sha256 = None
                    raise Sha256MismatchError(errors[-1])
                # Wrong/corrupt bytes from this source — start the next mirror
                # from scratch (a stale partial would guarantee a bad file).
                with contextlib.suppress(FileNotFoundError):
                    partial.unlink()
                task.final_sha256 = None
                task.downloaded_bytes = 0
                await self._fallback(task, url, ordered, idx, "sha256_mismatch", errors)
                continue

            # --- integrity OK → atomic rename, done ---
            _atomic_rename(partial, Path(task.dest_path))
            task.status = DownloadStatus.DONE
            task.finished_at = time.time()
            await self._emit(task, "done", extra={
                "sha256": actual,
                "url": url,
                "source_index": idx,
                "sources_tried": len(errors) + 1,
            })
            return

        # Unreachable in practice (the loop returns on success or raises on the
        # last failure), but keep a typed safety net.
        raise DownloadAllCandidatesFailed(errors)

    async def _fallback(
        self,
        task: DownloadTask,
        from_url: str,
        ordered: list[str],
        idx: int,
        reason: str,
        errors: list[str],
    ) -> None:
        """Record a mirror switch and emit a ``fallback`` progress event."""
        task.fallbacks += 1
        next_url = ordered[idx + 1] if idx + 1 < len(ordered) else ""
        await self._emit(task, "fallback", extra={
            "reason": reason,
            "from": from_url,
            "to": next_url,
            "source_index": idx,
            "errors": list(errors),
        })
        # Small cooperative pause so a hard-down host doesn't get hammered
        # instantly; tests pass zero-backoff to stay fast.
        await asyncio.sleep(self._backoff_seconds(task.fallbacks))

    async def _try_sharded(self, task: DownloadTask, partial: Path) -> bool:
        """尝试分片并发下载，返回 True 表示字节已全部就位。

        False 表示"不该分片或分片失败"，调用方应走原来的单线流程。
        任何失败都会把 ``.partial`` 截断到连续已完成的字节数，
        保证单线续传的前缀是完整正确的 —— 宁可少留几个字节，也不能留空洞。
        """
        if not self.shard_downloads:
            return False
        headers: dict[str, str] = {}
        for k, v in (task.extra_headers or {}).items():
            if k.lower() != "range" and v:
                headers[k] = v
        try:
            client = await self._get_client()
            return await download_sharded(
                client=client,
                url=task.url,
                base_headers=headers,
                partial=partial,
                task=task,
                chunk_size=self._chunk_size,
                max_shards=self.max_shards,
                emit=self._emit,
            )
        except asyncio.CancelledError:
            raise
        except ShardingNotSupportedError:
            _truncate_partial(partial, task)
            return False
        except Exception as e:  # noqa: BLE001 — 分片失败一律回退，不放弃下载
            with contextlib.suppress(Exception):

                logging.getLogger("kevrai.downloader").debug(
                    "sharded download failed, falling back to single stream: %s", e
                )
            _truncate_partial(partial, task)
            return False

    async def _stream_with_retry(
        self,
        task: DownloadTask,
        partial: Path,
        resume_from: int,
    ) -> None:
        """Stream the body with bounded exponential-backoff retry.

        A flaky mid-stream drop (read timeout / connection reset / transient
        5xx) used to fail the whole task. Now we retry a bounded number of
        times: every retry re-stats the ``.partial`` and issues a fresh
        ``Range`` request from wherever the last attempt got to.

        Only *retryable* failures are retried — transport errors (except a
        local protocol bug, which is our own misuse) and 429/502/503/504.
        Other 4xx, ``DownloadRefused`` and cancellation always propagate.
        """
        max_attempts = self._stream_retries + 1
        attempt = 0
        while True:
            attempt += 1
            # Re-stat on every attempt: a prior attempt may have grown the
            # partial, and a server that ignores Range hands us the whole file
            # from 0 (handled inside ``_stream``).
            current_resume = partial.stat().st_size if partial.exists() else 0
            try:
                await self._stream(task, partial, current_resume)
                return
            except asyncio.CancelledError:
                raise
            except DownloadRefused:
                raise
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if (
                    status in STREAM_RETRY_STATUSES
                    and attempt < max_attempts
                    and not task.cancel_event.is_set()
                ):
                    await self._emit(task, "retry", extra={"attempt": attempt, "status": status})
                    await asyncio.sleep(self._backoff_seconds(attempt))
                    continue

                # 416 Range Not Satisfiable —— 服务端在说"你要的起点超出了
                # 我这个文件的长度"。只可能因为磁盘上的 .partial 比服务端
                # 当前的文件还大（典型：HF 上这个文件被重新上传过，变小了）。
                #
                # 不做处理的话：每次重试都从同样的偏移续传，每次都 416，
                # 任务永久失败且无法自愈，磁盘上还一直躺着一堆对不上当前
                # 源的旧字节。
                #
                # 这里删 partial 是安全的（与"进度文件损坏"那类情况不同）：
                # 416 是服务端**明确**拒绝这些字节，它们已被证明对不上当前
                # 源，留着没有任何意义。重置后从头下一遍即可自愈，代价是
                # 重下，而不是永久卡死。
                if status == 416 and current_resume > 0 and not task.cancel_event.is_set():
                    reset_ok = self._reset_partial(partial)
                    await self._emit(
                        task, "range_reset",
                        extra={
                            "attempt": attempt,
                            "discarded_bytes": current_resume,
                            "reset_ok": reset_ok,
                        },
                    )
                    if not reset_ok:
                        raise
                    task.downloaded_bytes = 0
                    task.total_bytes = 0
                    if attempt < max_attempts:
                        continue
                raise
            except httpx.TransportError as e:
                # LocalProtocolError == our own misuse; retrying won't help.
                if isinstance(e, httpx.LocalProtocolError):
                    raise
                if attempt < max_attempts and not task.cancel_event.is_set():
                    await self._emit(
                        task, "retry",
                        extra={"attempt": attempt, "error": type(e).__name__},
                    )
                    await asyncio.sleep(self._backoff_seconds(attempt))
                    continue
                raise

    @staticmethod
    def _reset_partial(partial: Path) -> bool:
        """把 .partial 清零（连同它的分片进度文件）。

        仅在服务端用 416 明确拒绝已有字节时调用。返回是否成功。
        """
        try:
            with partial.open("wb"):
                pass
        except OSError:
            return False
        # 分片进度记的是旧的（更大的）长度，留着会让下一次续传按旧计划走。
        try:
            from .download_shards import _progress_path

            ppath = _progress_path(partial)
            if ppath.exists():
                ppath.unlink()
        except Exception as e:  # noqa: BLE001 — 进度文件清不掉不影响主流程
            logging.getLogger("kevrai.downloader").debug(
                "清理进度文件失败（不影响主流程）: %s", e)
        return True

    def _backoff_seconds(self, attempt: int) -> float:
        idx = min(attempt - 1, len(self._stream_backoff) - 1)
        return float(self._stream_backoff[idx])

    async def _stream(
        self,
        task: DownloadTask,
        partial: Path,
        resume_from: int,
    ) -> None:
        headers: dict[str, str] = {}
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"
        # Task-level headers (e.g. gated-repo Authorization). Range wins on
        # conflict — it is resume-critical.
        for k, v in (task.extra_headers or {}).items():
            if k.lower() != "range" and v:
                headers[k] = v
        has_auth = any(
            str(k).lower() == "authorization" for k in (task.extra_headers or {}))
        client = await self._get_client()
        url = task.url
        # H2 SSRF hardening: `follow_redirects` is off on the shared client, so
        # we walk each hop and re-validate the target against the allowlist /
        # blocklist before issuing the next request. A 302 from a trusted host
        # must not bounce us onto cloud metadata (169.254.169.254), a blocked /
        # phishing mirror, or any host outside the curated set.
        for _hop in range(10):
            async with client.stream("GET", url, headers=headers) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("Location") or ""
                    if not loc:
                        raise DownloadRefused(
                            f"redirect without Location from {url!r}")
                    next_url = str(httpx.URL(url).join(loc))
                    self._check_url(next_url, has_auth_header=has_auth)
                    url = next_url
                    continue
                await self._stream_body(task, partial, resume_from, r)
                return
        raise DownloadRefused(f"too many redirects: {task.url}")

    async def _stream_body(
        self,
        task: DownloadTask,
        partial: Path,
        resume_from: int,
        r: httpx.Response,
    ) -> None:
        r.raise_for_status()

        # Server ignored our Range request (200 OK instead of 206): the
        # body is the whole file from byte 0. Treat it as a fresh download
        # — overwrite the partial instead of appending, and do NOT inflate
        # total_bytes by the stale resume offset (BUG-01).
        resumed = resume_from > 0 and r.status_code == 206
        mode = "wb" if not resumed else "ab"
        # A fresh (overwrite) stream starts the byte counter at 0, otherwise
        # it keeps the stale resume offset and progress races past 100% while
        # the file on disk has already been truncated by mode="wb".
        if not resumed:
            task.downloaded_bytes = 0

        content_range = r.headers.get("Content-Range") or ""
        # When resuming, total = end + 1 from "bytes {start}-{end}/{total}"
        if content_range and "/" in content_range:
            total_str = content_range.split("/")[-1]
            try:
                task.total_bytes = int(total_str)
            except ValueError:
                task.total_bytes = int(r.headers.get("Content-Length", "0")) + (resume_from if resumed else 0)
        else:
            try:
                task.total_bytes = int(r.headers.get("Content-Length", "0")) + (resume_from if resumed else 0)
            except ValueError:
                task.total_bytes = 0

        # 拿到了真实长度，趁早校验一次能不能装下。
        self._check_disk_space_midflight(partial, task.total_bytes, resume_from)

        task.status = DownloadStatus.DOWNLOADING
        partial.parent.mkdir(parents=True, exist_ok=True)

        loop = asyncio.get_running_loop()
        last_tick = loop.time()
        last_bytes = task.downloaded_bytes

        with partial.open(mode) as fh:
            async for chunk in r.aiter_bytes(self._chunk_size):
                if task.cancel_event.is_set():
                    raise asyncio.CancelledError()
                if not chunk:
                    continue
                fh.write(chunk)
                task.downloaded_bytes += len(chunk)
                # 刷新看门狗的"存活"时间戳（达到阈值才刷新，防假推进）。
                self.mark_progress(task, task.downloaded_bytes)

                now = loop.time()
                if now - last_tick >= 0.25:
                    # fsync 只在这里做：此前是每 64KB 一次，10GB 模型会
                    # 产生数十万次 fsync，吞吐严重下降。
                    fh.flush()
                    with contextlib.suppress(OSError):
                        os.fsync(fh.fileno())
                    elapsed = max(now - last_tick, 1e-6)
                    task.speed_bps = (task.downloaded_bytes - last_bytes) / elapsed
                    last_tick = now
                    last_bytes = task.downloaded_bytes
                    await self._emit(task, "progress")

            # Final flush + fsync 必须在 with 内完成。此前这段在 with 外：
            # fh 已关闭，flush() 抛 ValueError 被吞掉 → 最终 fsync 从未
            # 生效；且 partial.open("rb") 新开的 fd 无人关闭，每次下载
            # 泄漏一个描述符。
            fh.flush()
            with contextlib.suppress(OSError):
                os.fsync(fh.fileno())

    async def _sha256_file(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            while True:
                chunk = await asyncio.to_thread(fh.read, self._chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    async def _emit(self, task: DownloadTask, event: str, extra: dict[str, Any] | None = None) -> None:
        payload = task.snapshot()
        payload["event"] = event
        if extra:
            payload.update(extra)
        with contextlib.suppress(asyncio.QueueFull):  # pragma: no cover — bounded queue not used
            task.queue.put_nowait(payload)
        # Best-effort: drop old entries so slow consumers don't OOM.
        while task.queue.qsize() > 256:
            try:
                task.queue.get_nowait()
            except asyncio.QueueEmpty:
                break


def _truncate_partial(partial: Path, task: DownloadTask) -> None:
    """分片失败回退单线前：截断到连续前缀并同步计数器。"""
    try:
        prefix = truncate_to_prefix(partial)
    except Exception:  # noqa: BLE001 — 截断失败也只影响续传起点，不能中断下载
        prefix = partial.stat().st_size if partial.exists() else 0
    task.downloaded_bytes = prefix


def _atomic_rename(src: Path, dst: Path) -> None:
    """Rename `src` -> `dst`, replacing atomically where supported."""
    src.replace(dst)


# ---------------------------------------------------------------------------
# Convenience synchronous wrapper (used by some legacy code paths)
# ---------------------------------------------------------------------------


def quick_download_sync(url: str, dest: Path, sha256: str | None = None) -> bool:
    """Synchronous helper for one-shot downloads (used by installer scripts)."""
    import asyncio as _asyncio

    async def _runner() -> bool:
        dl = Downloader(max_concurrent=1)
        try:
            tid = await dl.start(str(url), dest, sha256=sha256)
        except DownloadRefused:
            return False
        # Wait for completion
        while True:
            s = await dl.progress(tid)
            if s is None:
                return False
            if s["status"] in {"done", "failed", "cancelled"}:
                return s["status"] == "done"
            await _asyncio.sleep(0.1)

    try:
        return _asyncio.run(_runner())
    except Exception:
        return False
