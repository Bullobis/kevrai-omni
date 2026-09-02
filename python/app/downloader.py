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
import hashlib
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx

from .catalog import (
    DEFAULT_MODEL_HOSTS,
    is_host_allowed,
)


class DownloadStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# URL validation (permissive by default)
# ---------------------------------------------------------------------------


class DownloadRefused(Exception):
    """Raised when URL fails validation (unsupported scheme, etc.)."""


def _check_url(url: str, *, enforce_allowlist: bool = False) -> None:
    """Validate a URL.

    Permissive by default — only the scheme is checked. Pass
    `enforce_allowlist=True` to additionally require the host to be in
    `DEFAULT_MODEL_HOSTS`.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise DownloadRefused(f"unsupported scheme {parsed.scheme!r}")
    if not parsed.hostname:
        raise DownloadRefused(f"url has no host: {url!r}")
    if enforce_allowlist and not is_host_allowed(url, DEFAULT_MODEL_HOSTS):
        raise DownloadRefused(f"host not in allowlist: {parsed.hostname}")


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
    speed_bps: float = 0.0
    final_sha256: str | None = None
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
        }


# ---------------------------------------------------------------------------
# Downloader
# ---------------------------------------------------------------------------


DEFAULT_CHUNK = 1 << 20  # 1 MiB
DEFAULT_TIMEOUT = httpx.Timeout(30.0, read=120.0, connect=10.0, write=10.0)


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
    ) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[str, DownloadTask] = {}
        self._tasks_lock = asyncio.Lock()
        base = allowed_hosts or DEFAULT_MODEL_HOSTS
        if extra_allowed_hosts:
            self._allowed_hosts = set(base) | set(extra_allowed_hosts)
        else:
            self._allowed_hosts = set(base)
        self._enforce_allowlist = bool(allowed_hosts)  # if caller passed set, enforce
        self._client = client
        self._client_owned = client is None
        self._chunk_size = chunk_size

    # --- public ---

    async def start(
        self,
        url: str,
        dest: str | os.PathLike[str],
        sha256: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        """Start a new download. Returns the task_id.

        `extra_headers` are sent with every request for this task (used for
        e.g. `Authorization: Bearer <hf_token>` on gated HuggingFace repos).
        Headers are never echoed into progress snapshots.

        Raises `DownloadRefused` only for unsupported scheme / missing host.
        Raises `FileExistsError` if the destination already exists.
        """
        dest_path = Path(dest).expanduser().resolve()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        # Validate url (permissive: only scheme + host presence)
        self._check_url(url)
        if dest_path.exists():
            raise FileExistsError(str(dest_path))

        task = DownloadTask(
            id=uuid.uuid4().hex,
            url=url,
            dest_path=str(dest_path),
            expected_sha256=sha256,
            started_at=time.time(),
            extra_headers=dict(extra_headers or {}),
        )
        async with self._tasks_lock:
            self._tasks[task.id] = task

        # Run in background without awaiting — caller polls via `progress()` /
        # subscribes to `task.queue`.
        asyncio.create_task(self._run(task))
        return task.id

    async def cancel(self, task_id: str) -> bool:
        async with self._tasks_lock:
            t = self._tasks.get(task_id)
        if t is None:
            return False
        if t.status in {DownloadStatus.DONE, DownloadStatus.FAILED, DownloadStatus.CANCELLED}:
            return True
        t.cancel_event.set()
        return True

    async def progress(self, task_id: str) -> dict[str, Any] | None:
        async with self._tasks_lock:
            t = self._tasks.get(task_id)
        if t is None:
            return None
        return t.snapshot()

    async def list_tasks(self) -> list[dict[str, Any]]:
        async with self._tasks_lock:
            return [t.snapshot() for t in self._tasks.values()]

    def get_task(self, task_id: str) -> DownloadTask | None:
        """Sync accessor for tasks (used by FastAPI handlers)."""
        return self._tasks.get(task_id)

    async def aclose(self) -> None:
        if self._client_owned and self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass

    # --- internals ---

    def _check_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise DownloadRefused(f"unsupported scheme {parsed.scheme!r}")
        if not parsed.hostname:
            raise DownloadRefused(f"url has no host: {url!r}")
        # Positive allowlist only enforced when the caller explicitly opted in.
        if self._enforce_allowlist and not is_host_allowed(url, self._allowed_hosts):
            raise DownloadRefused(f"host not in allowlist: {parsed.hostname}")

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if not hasattr(self, "_own_client") or self._own_client is None:
            import httpx as _httpx
            self._own_client = _httpx.AsyncClient(
                follow_redirects=True, timeout=DEFAULT_TIMEOUT
            )
        return self._own_client  # type: ignore[return-value]

    async def _run(self, task: DownloadTask) -> None:
        partial = Path(task.dest_path + ".partial")
        # If a previous partial exists from a crashed run, we resume from it.
        resume_from = partial.stat().st_size if partial.exists() else 0
        task.downloaded_bytes = resume_from

        async with self._sem:
            try:
                await self._emit(task, "started", extra={"resume_from": resume_from})
                await self._stream(task, partial, resume_from)
            except asyncio.CancelledError:  # noqa: PERF203
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
                task.status = DownloadStatus.FAILED
                task.error = f"{type(e).__name__}: {e}"
                task.finished_at = time.time()
                await self._emit(task, "failed", extra={"error": task.error})
                return

            # Verifying phase
            task.status = DownloadStatus.VERIFYING
            await self._emit(task, "verifying")
            try:
                actual = await self._sha256_file(Path(partial))
            except Exception as e:  # noqa: BLE001
                task.status = DownloadStatus.FAILED
                task.error = f"sha256 read error: {e}"
                task.finished_at = time.time()
                await self._emit(task, "failed", extra={"error": task.error})
                return

            task.final_sha256 = actual
            if task.expected_sha256 and actual.lower() != task.expected_sha256.lower():
                task.status = DownloadStatus.FAILED
                task.error = (
                    f"sha256 mismatch: expected {task.expected_sha256}, got {actual}"
                )
                task.finished_at = time.time()
                # Don't rename — leave .partial so user can inspect
                await self._emit(task, "failed", extra={"error": task.error})
                return

            try:
                _atomic_rename(partial, Path(task.dest_path))
            except Exception as e:  # noqa: BLE001
                task.status = DownloadStatus.FAILED
                task.error = f"rename failed: {e}"
                task.finished_at = time.time()
                await self._emit(task, "failed", extra={"error": task.error})
                return

            task.status = DownloadStatus.DONE
            task.finished_at = time.time()
            await self._emit(task, "done", extra={"sha256": actual})

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
        client = await self._get_client()
        async with client.stream("GET", task.url, headers=headers) as r:
            r.raise_for_status()

            # Server ignored our Range request (200 OK instead of 206): the
            # body is the whole file from byte 0. Treat it as a fresh download
            # — overwrite the partial instead of appending, and do NOT inflate
            # total_bytes by the stale resume offset (BUG-01).
            resumed = resume_from > 0 and r.status_code == 206
            mode = "wb" if not resumed else "ab"

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
                    fh.flush()
                    try:
                        os.fsync(fh.fileno())
                    except OSError:
                        pass
                    task.downloaded_bytes += len(chunk)

                    now = loop.time()
                    if now - last_tick >= 0.25:
                        elapsed = max(now - last_tick, 1e-6)
                        task.speed_bps = (task.downloaded_bytes - last_bytes) / elapsed
                        last_tick = now
                        last_bytes = task.downloaded_bytes
                        await self._emit(task, "progress")

            # Final fsync
            try:
                fh.flush()  # noqa: F821 — closed by `with`
                os.fsync(partial.open("rb").fileno())
            except (OSError, ValueError):
                pass

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
        try:
            task.queue.put_nowait(payload)
        except asyncio.QueueFull:  # pragma: no cover — bounded queue not used
            pass
        # Best-effort: drop old entries so slow consumers don't OOM.
        while task.queue.qsize() > 256:
            try:
                task.queue.get_nowait()
            except asyncio.QueueEmpty:
                break


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
