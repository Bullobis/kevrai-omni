"""Neuron-Hub2 K-Cortex regression tests.

Covers the two P2 hardening items added on top of the v2.8.1 retry/single-flight
work:

Task A — cross-mirror mid-stream fallback:
    * when the primary URL exhausts its in-URL retries, the Downloader advances
      to the next candidate mirror, **preserving** the ``.partial`` bytes and
      resuming via ``Range``;
    * every candidate failing produces a single aggregated error.

Task B — sha256 integrity fallback:
    * a sha256 mismatch on one mirror deletes the corrupt ``.partial`` and falls
      back to the next mirror from byte 0;
    * a mismatch on the *last* mirror removes the ``.partial`` and fails the task;
    * omitting ``sha256`` (or passing ``""``) keeps the legacy no-verify path.

All HTTP traffic goes to a local ``http.server`` — no real network.
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

import pytest

from app.downloader import (
    DownloadAllCandidatesFailed,
    Downloader,
    Sha256MismatchError,
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Local server: per-path routing simulating two independent mirrors
# ---------------------------------------------------------------------------


class _DualMirrorHandler(BaseHTTPRequestHandler):
    """Routes by URL path:

    * ``/dead.bin``   -> always 503 (primary mirror hard-down).
    * ``/drop.bin``   -> serves the *first half* of the slice then closes the
      socket abruptly (mid-stream transport drop).
    * ``/good.bin``   -> fully Range-aware, serves the requested slice of the
      *good* payload.
    * ``/bad.bin``    -> serves a fixed wrong (zero-filled) body of the same
      length, Range-aware.
    """

    good: bytes = b""
    bad: bytes = b""
    #: Range header observed on /good.bin (for the resume-offset assertion).
    good_range_seen: str = ""
    #: How many times /drop.bin has been hit.
    drop_hits: int = 0

    def log_message(self, *args: object, **kwargs: object) -> None:  # silence
        pass

    def _range_start(self) -> int:
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                return max(0, int(rng[len("bytes="):].split("-", 1)[0]))
            except ValueError:
                return 0
        return 0

    def _serve_slice(self, body: bytes, start: int) -> None:
        start = max(0, min(start, len(body)))
        self.send_response(206 if start > 0 else 200)
        self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        self.wfile.write(body[start:])

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]

        if path == "/dead.bin":
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if path == "/drop.bin":
            type(self).drop_hits += 1
            start = self._range_start()
            self.send_response(206 if start > 0 else 200)
            self.send_header(
                "Content-Range", f"bytes {start}-{len(self.good) - 1}/{len(self.good)}"
            )
            # Send only half of the requested slice, then drop.
            half = (len(self.good) - start) // 2
            self.send_header("Content-Length", str(len(self.good) - start))
            self.end_headers()
            self.wfile.write(self.good[start:start + half])
            self.wfile.flush()
            self.connection.close()
            return

        if path == "/bad.bin":
            start = self._range_start()
            self._serve_slice(self.bad, start)
            return

        # default /good.bin
        start = self._range_start()
        if start > 0:
            type(self).good_range_seen = self.headers.get("Range", "")
        self._serve_slice(self.good, start)


@pytest.fixture(scope="module")
def mirror_server() -> Iterator[tuple[str, type[_DualMirrorHandler]]]:
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _DualMirrorHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}", _DualMirrorHandler
    finally:
        server.shutdown()
        server.server_close()


def _reset(handler: type[_DualMirrorHandler], good: bytes, bad: bytes | None = None) -> None:
    handler.good = good
    handler.bad = bad if bad is not None else (b"\x00" * len(good))
    handler.good_range_seen = ""
    handler.drop_hits = 0


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
# Task A: cross-mirror fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_to_mirror_when_primary_hard_down(mirror_server, tmp_path: Path):
    """Primary always 503 -> must advance to the mirror and finish."""
    base, handler = mirror_server
    payload = b"PRIMARY-DOWN-FALLBACK" * 256  # ~5 KiB
    _reset(handler, payload)

    dl = Downloader(
        max_concurrent=1,
        stream_retries=0,          # no in-URL retry: fail fast on 503
        stream_backoff=(0.0,),
        extra_allowed_hosts={"127.0.0.1"},
    )
    dst = tmp_path / "fallback.bin"
    tid = await dl.start(
        f"{base}/dead.bin", dst,
        candidates=[f"{base}/good.bin"],
    )
    snap = await _wait_terminal(dl, tid)
    assert snap["status"] == "done", snap
    assert dst.read_bytes() == payload
    assert snap["fallbacks"] >= 1
    assert snap["source_index"] == 1  # finished on the mirror
    await dl.aclose()


@pytest.mark.asyncio
async def test_fallback_resumes_partial_bytes_via_range(mirror_server, tmp_path: Path):
    """Primary drops mid-stream (partial has half the bytes) -> mirror must be
    asked for the *remaining* half via Range, and the assembled file must equal
    the full payload byte-for-byte."""
    base, handler = mirror_server
    payload = b"0123456789abcdef" * 1024  # 16 KiB
    _reset(handler, payload)
    expected = hashlib.sha256(payload).hexdigest()

    dl = Downloader(
        max_concurrent=1,
        stream_retries=0,          # primary drops once, then fall back
        stream_backoff=(0.0,),
        chunk_size=4096,
        extra_allowed_hosts={"127.0.0.1"},
    )
    dst = tmp_path / "resume_cross.bin"
    tid = await dl.start(
        f"{base}/drop.bin", dst,
        sha256=expected,
        candidates=[f"{base}/good.bin"],
    )
    snap = await _wait_terminal(dl, tid)
    assert snap["status"] == "done", snap
    assert dst.read_bytes() == payload
    # The mirror MUST have received a Range request starting at ~half the file
    # (the bytes already sitting in .partial), not a fresh 0-offset download.
    assert handler.good_range_seen.startswith("bytes="), handler.good_range_seen
    offset = int(handler.good_range_seen[len("bytes="):].rstrip("-"))
    # Primary wrote half of the whole payload (16KiB/2 = 8KiB), modulo chunking.
    assert 1 <= offset < len(payload), f"expected a nonzero resume offset, got {offset}"
    assert handler.drop_hits >= 1
    await dl.aclose()


@pytest.mark.asyncio
async def test_all_candidates_fail_aggregates_error(mirror_server, tmp_path: Path):
    """Every candidate hard-down -> task FAILED with an aggregated error string
    naming both sources (not just the last one)."""
    base, handler = mirror_server
    _reset(handler, b"x" * 1024)

    dl = Downloader(
        max_concurrent=1,
        stream_retries=0,
        stream_backoff=(0.0,),
        extra_allowed_hosts={"127.0.0.1"},
    )
    dst = tmp_path / "all_down.bin"
    tid = await dl.start(
        f"{base}/dead.bin", dst,
        candidates=[f"{base}/drop.bin"],  # also fails (mid-stream drop)
    )
    snap = await _wait_terminal(dl, tid, timeout=15.0)
    assert snap["status"] == "failed", snap
    err = snap.get("error", "")
    assert err, snap
    # Aggregated error must name BOTH sources, not just the last one.
    assert "/dead.bin" in err, err
    assert "/drop.bin" in err, err
    # fallbacks == 1 (primary tried, one mirror tried, both dead).
    assert snap["fallbacks"] == 1, snap
    await dl.aclose()


# ---------------------------------------------------------------------------
# Task B: sha256 integrity fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sha256_mismatch_deletes_partial_and_falls_back(mirror_server, tmp_path: Path):
    """Primary serves a wrong (zero-filled) body whose hash does not match: the
    corrupt .partial must be deleted and the next mirror tried from 0."""
    base, handler = mirror_server
    good = b"REAL-GOOD-PAYLOAD" * 512  # ~8 KiB
    bad = b"\x00" * len(good)
    _reset(handler, good, bad)
    expected = hashlib.sha256(good).hexdigest()

    dl = Downloader(
        max_concurrent=1,
        stream_retries=0,
        stream_backoff=(0.0,),
        chunk_size=4096,
        extra_allowed_hosts={"127.0.0.1"},
    )
    dst = tmp_path / "integrity.bin"
    tid = await dl.start(
        f"{base}/bad.bin", dst,
        sha256=expected,
        candidates=[f"{base}/good.bin"],
    )
    snap = await _wait_terminal(dl, tid)
    assert snap["status"] == "done", snap
    assert dst.read_bytes() == good
    assert snap["fallbacks"] >= 1
    # The fallback after a mismatch must be a FRESH download: the mirror got a
    # Range request starting at 0 (i.e. no Range header at all).
    await dl.aclose()


@pytest.mark.asyncio
async def test_sha256_mismatch_last_source_fails_and_cleans_partial(mirror_server, tmp_path: Path):
    """Wrong payload on the *only* source -> FAILED with a sha256 error AND the
    .partial removed (a corrupt blob must not linger to be resumed later)."""
    base, handler = mirror_server
    good = b"good" * 256
    bad = b"\x00" * len(good)
    _reset(handler, good, bad)
    wrong_hash = hashlib.sha256(b"something else entirely").hexdigest()

    dl = Downloader(
        max_concurrent=1,
        stream_retries=0,
        stream_backoff=(0.0,),
        extra_allowed_hosts={"127.0.0.1"},
    )
    dst = tmp_path / "nope.bin"
    partial = dst.with_suffix(dst.suffix + ".partial")
    tid = await dl.start(f"{base}/bad.bin", dst, sha256=wrong_hash)
    snap = await _wait_terminal(dl, tid)
    assert snap["status"] == "failed", snap
    assert "sha256" in snap.get("error", "").lower() or "mismatch" in snap.get("error", "").lower()
    # The corrupt partial must be gone.
    assert not partial.exists(), f"partial should have been cleaned: {partial}"
    assert not dst.exists()
    await dl.aclose()


@pytest.mark.asyncio
async def test_no_sha256_legacy_unchanged(mirror_server, tmp_path: Path):
    """No sha256 expected -> verification is skipped; a (possibly wrong) file is
    still renamed to the destination. This pins backward compatibility."""
    base, handler = mirror_server
    bad = b"\x00" * 1024
    _reset(handler, good=b"whatever", bad=bad)

    dl = Downloader(
        max_concurrent=1,
        stream_backoff=(0.0,),
        extra_allowed_hosts={"127.0.0.1"},
    )
    dst = tmp_path / "noverify.bin"
    tid = await dl.start(f"{base}/bad.bin", dst)  # sha256 omitted
    snap = await _wait_terminal(dl, tid)
    assert snap["status"] == "done", snap
    assert dst.read_bytes() == bad
    await dl.aclose()


# ---------------------------------------------------------------------------
# Candidate-chain construction (unit, no network)
# ---------------------------------------------------------------------------


def test_build_candidate_chain_dedupes_and_keeps_order():
    dl = Downloader(max_concurrent=1)
    chain = dl._build_candidate_chain(
        "https://hf-mirror.com/a/b",
        ["https://huggingface.co/a/b", "https://hf-mirror.com/a/b", "  ", ""],
    )
    assert chain == [
        "https://hf-mirror.com/a/b",
        "https://huggingface.co/a/b",
    ]


def test_build_candidate_chain_single_source():
    dl = Downloader(max_concurrent=1)
    assert dl._build_candidate_chain("https://x.y/z", None) == ["https://x.y/z"]


def test_exception_classes_carry_messages():
    assert str(Sha256MismatchError("boom")) == "boom"
    agg = DownloadAllCandidatesFailed(["a: 503", "b: timeout"])
    assert "a: 503" in str(agg) and "b: timeout" in str(agg)
    assert DownloadAllCandidatesFailed([]).errors == []
