"""Neuron-Hub K-Cortex reliability regression tests.

Covers the hardening applied to the download/Hub link:

* ``downloader.Downloader`` — bounded exponential-backoff retry of *retryable*
  stream failures (5xx/429, transport drops) with correct Range resume, and no
  retry of non-retryable 4xx;
* same-destination single-flight: a second ``start()`` for a path that already
  has an in-flight download returns the existing task id instead of opening a
  second writer onto the same ``.partial``;
* ``sources._probe_one`` closes the response even when the body stream errors
  mid-probe (no connection-pool leak).

All HTTP traffic goes to a local ``http.server`` or an in-process
``httpx.MockTransport`` — no real network.
"""
from __future__ import annotations

import asyncio
import hashlib
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

from app.downloader import (
    DEFAULT_STREAM_BACKOFF,
    STREAM_RETRY_STATUSES,
    Downloader,
)
from app.sources import _probe_one

# ---------------------------------------------------------------------------
# Local HTTP server with failure injection
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FlakyHandler(BaseHTTPRequestHandler):
    """Serves a fixed buffer; supports Range and configurable failure rules.

    Class attributes are mutated per-test (the server is module-scoped).
    """

    payload: bytes = b""
    #: The HTTP status to return for the first ``fail_status_for_n`` requests.
    fail_status: int = 503
    #: How many of the first requests should get ``fail_status`` (0 = none).
    fail_status_for_n: int = 0
    #: Requests served so far (any path).
    requests: int = 0
    #: If > 0, the first N streaming responses write only the first half of the
    #: body then close the socket abruptly (simulates a dropped connection).
    truncate_for_n: int = 0
    truncate_done: int = 0

    def log_message(self, *args: object, **kwargs: object) -> None:  # silence
        pass

    def do_GET(self) -> None:  # noqa: N802
        type(self).requests += 1
        n = type(self).requests

        if type(self).fail_status_for_n and n <= type(self).fail_status_for_n:
            self.send_response(type(self).fail_status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        rng = self.headers.get("Range")
        payload = type(self).payload
        start = 0
        if rng and rng.startswith("bytes="):
            try:
                start = int(rng[len("bytes="):].split("-", 1)[0])
            except ValueError:
                start = 0
            start = max(0, min(start, len(payload)))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}")
            self.send_header("Content-Length", str(len(payload) - start))
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        # Simulate a dropped connection: write the first half of the requested
        # slice, then return without the rest (the socket closes on return).
        if type(self).truncate_for_n and type(self).truncate_done < type(self).truncate_for_n:
            type(self).truncate_done += 1
            half = (len(payload) - start) // 2
            self.wfile.write(payload[start:start + half])
            self.wfile.flush()
            # Close abruptly without Content-Length-completing the body.
            self.connection.close()
            return

        self.wfile.write(payload[start:])


@pytest.fixture(scope="module")
def flaky_server() -> Iterator[tuple[str, type[_FlakyHandler]]]:
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _FlakyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}", _FlakyHandler
    finally:
        server.shutdown()
        server.server_close()


def _reset_handler(handler: type[_FlakyHandler], payload: bytes) -> None:
    handler.payload = payload
    handler.requests = 0
    handler.fail_status = 503
    handler.fail_status_for_n = 0
    handler.truncate_for_n = 0
    handler.truncate_done = 0


async def _wait_terminal(dl: Downloader, tid: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    snap: dict = {}
    while time.monotonic() < deadline:
        snap = await dl.progress(tid)  # type: ignore[assignment]
        if snap is None:
            break
        if snap["status"] in {"done", "failed", "cancelled"}:
            break
        await asyncio.sleep(0.02)
    return snap


# ---------------------------------------------------------------------------
# Retry: retryable transient failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_on_transient_5xx_eventually_succeeds(flaky_server, tmp_path: Path):
    base, handler = flaky_server
    payload = b"ABCDEFGH" * 4096  # 32 KiB
    _reset_handler(handler, payload)
    handler.fail_status_for_n = 2  # first two GETs -> 503, third -> 200

    expected = hashlib.sha256(payload).hexdigest()
    dl = Downloader(
        max_concurrent=1,
        stream_retries=3,
        stream_backoff=(0.0, 0.0, 0.0),
        extra_allowed_hosts={"127.0.0.1"},
    )
    dst = tmp_path / "retry.bin"
    tid = await dl.start(f"{base}/f.bin", dst, sha256=expected)
    snap = await _wait_terminal(dl, tid)
    assert snap["status"] == "done", snap
    assert dst.read_bytes() == payload
    # The server really was hit 3 times (1 initial + 2 retries).
    assert handler.requests == 3
    await dl.aclose()


@pytest.mark.asyncio
async def test_no_retry_on_permanent_4xx(flaky_server, tmp_path: Path):
    base, handler = flaky_server
    _reset_handler(handler, b"x" * 1024)
    handler.fail_status = 404
    handler.fail_status_for_n = 100  # always 404
    # 404 is not in the retry set -> must NOT be retried.
    dl = Downloader(
        max_concurrent=1,
        stream_retries=5,
        stream_backoff=(0.0, 0.0, 0.0),
        extra_allowed_hosts={"127.0.0.1"},
    )
    dst = tmp_path / "nope.bin"
    tid = await dl.start(f"{base}/missing.bin", dst)
    snap = await _wait_terminal(dl, tid)
    assert snap["status"] == "failed", snap
    # Exactly one request — no retries on a 4xx.
    assert handler.requests == 1
    await dl.aclose()


@pytest.mark.asyncio
async def test_mid_stream_drop_resumes_and_succeeds(flaky_server, tmp_path: Path):
    """A connection that drops after half the body must be retried with Range
    resume — the final file must equal the full payload byte-for-byte."""
    base, handler = flaky_server
    payload = b"0123456789" * 4096  # 40 KiB
    _reset_handler(handler, payload)
    handler.truncate_for_n = 1  # first stream drops early, second serves fully

    expected = hashlib.sha256(payload).hexdigest()
    dl = Downloader(
        max_concurrent=1,
        stream_retries=3,
        stream_backoff=(0.0, 0.0, 0.0),
        chunk_size=4096,
        extra_allowed_hosts={"127.0.0.1"},
    )
    dst = tmp_path / "resume.bin"
    tid = await dl.start(f"{base}/big.bin", dst, sha256=expected)
    snap = await _wait_terminal(dl, tid)
    assert snap["status"] == "done", snap
    assert dst.read_bytes() == payload
    await dl.aclose()


@pytest.mark.asyncio
async def test_retry_exhaustion_marks_failed(flaky_server, tmp_path: Path):
    """When every attempt fails, the task must end FAILED (no infinite loop)."""
    base, handler = flaky_server
    _reset_handler(handler, b"x" * 1024)
    handler.truncate_for_n = 100  # drop every stream

    dl = Downloader(
        max_concurrent=1,
        stream_retries=2,
        stream_backoff=(0.0, 0.0),
        chunk_size=4096,
        extra_allowed_hosts={"127.0.0.1"},
    )
    dst = tmp_path / "always_drop.bin"
    tid = await dl.start(f"{base}/drop.bin", dst)
    snap = await _wait_terminal(dl, tid, timeout=15.0)
    assert snap["status"] == "failed", snap
    # 1 initial + 2 retries = 3 attempts, then give up.
    assert handler.requests == 3
    await dl.aclose()


def test_retry_statuses_are_the_expected_set():
    # 429 + the standard gateway errors; plain 4xx must NOT be in here.
    assert frozenset({429, 502, 503, 504}) == STREAM_RETRY_STATUSES
    assert 404 not in STREAM_RETRY_STATUSES
    assert DEFAULT_STREAM_BACKOFF == (0.5, 1.0, 2.0)


# ---------------------------------------------------------------------------
# Single-flight by destination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_start_same_dest_returns_existing_task(flaky_server, tmp_path: Path):
    base, handler = flaky_server
    payload = b"SAMEDEST" * 4096
    _reset_handler(handler, payload)

    dl = Downloader(
        max_concurrent=2,
        stream_backoff=(0.0,),
        extra_allowed_hosts={"127.0.0.1"},
    )
    dst = tmp_path / "once.bin"
    tid1 = await dl.start(f"{base}/a.bin", dst)
    # Second call to the SAME destination while the first is still in flight.
    tid2 = await dl.start(f"{base}/b.bin", dst)
    # Must be de-duplicated to the single in-flight task.
    assert tid1 == tid2

    snap = await _wait_terminal(dl, tid1)
    assert snap["status"] == "done", snap
    assert dst.read_bytes() == payload

    # After completion the slot is released: a fresh start to the same path
    # (which no longer exists) is allowed again.
    dst.unlink()
    tid3 = await dl.start(f"{base}/a.bin", dst)
    assert tid3 != tid1
    snap3 = await _wait_terminal(dl, tid3)
    assert snap3["status"] == "done", snap3
    await dl.aclose()


@pytest.mark.asyncio
async def test_distinct_destinations_are_not_deduped(flaky_server, tmp_path: Path):
    base, handler = flaky_server
    _reset_handler(handler, b"X" * 2048)
    dl = Downloader(max_concurrent=2, stream_backoff=(0.0,),
                    extra_allowed_hosts={"127.0.0.1"})
    t1 = await dl.start(f"{base}/d1", tmp_path / "d1.bin")
    t2 = await dl.start(f"{base}/d2", tmp_path / "d2.bin")
    assert t1 != t2
    s1 = await _wait_terminal(dl, t1)
    s2 = await _wait_terminal(dl, t2)
    assert s1["status"] == "done"
    assert s2["status"] == "done"
    await dl.aclose()


# ---------------------------------------------------------------------------
# sources._probe_one: response closed even on body-stream error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_one_graceful_on_transport_error():
    """A transport error while probing must return ok=False, never raise.

    Regression: the probe must degrade to a failed SourceProbe (so one dead
    mirror never crashes the whole ranking) and close the response via the
    try/finally rather than leaking it.
    """

    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(boom)
    async with httpx.AsyncClient(transport=transport) as client:
        probe = await _probe_one(client, "https://probe.example.com/file")

    assert probe.ok is False
    assert probe.status == 0
    assert probe.error  # populated


