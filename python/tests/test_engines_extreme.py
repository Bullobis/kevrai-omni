"""Extreme-case tests for the engine install path (v2.8.0 hardening).

All cases exercise ``app.engines._stream_download`` / ``EngineManager.install``
against a *real* local HTTP server via real httpx — no transport mocks.

Covered:
  1. HTTP Range resume: pre-existing ``.partial`` continues at the right offset.
  2. 200-to-Range reset: a server that ignores Range must cause a clean
     restart (never a corrupted concatenation).
  3. Cooperative cancellation: ``cancel_cb`` → ``DownloadCancelled``.
  4. Progress telemetry: monotonic non-decreasing byte counts.
  5. Candidate fallback: install() falls through to the next mirror when the
     first candidate answers 5xx.
  6. Full install cycle over real network: zip download → verify → extract.
"""
from __future__ import annotations

import hashlib
import socket
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Local HTTP server fixtures
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _RangeAwareHandler(BaseHTTPRequestHandler):
    """Range-aware static handler (206 for Range, 200 otherwise)."""

    payload: bytes = b""
    # path → number of times to answer 503 before succeeding (flaky mode)
    path_failures: dict[str, int] = {}
    # when True, Range is ignored entirely (answers 200 with the full body)
    ignore_range: bool = False

    def log_message(self, *args, **kwargs):  # silence stderr noise
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        fails = _RangeAwareHandler.path_failures.get(path, 0)
        if fails > 0:
            _RangeAwareHandler.path_failures[path] = fails - 1
            self.send_error(503, "flaky")
            return
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes=") and not _RangeAwareHandler.ignore_range:
            try:
                start = int(rng[len("bytes="):].split("-", 1)[0])
            except ValueError:
                start = 0
            start = max(0, min(start, len(self.payload)))
            self.send_response(206)
            self.send_header(
                "Content-Range",
                f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}",
            )
            self.send_header("Content-Length", str(len(self.payload) - start))
            self.end_headers()
            self.wfile.write(self.payload[start:])
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(self.payload)


@pytest.fixture()
def range_server() -> Iterator[dict]:
    """Per-test server exposing payload/range/failure controls."""
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _RangeAwareHandler)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    _RangeAwareHandler.payload = b""
    _RangeAwareHandler.path_failures = {}
    _RangeAwareHandler.ignore_range = False
    try:
        yield {
            "base": f"http://127.0.0.1:{port}",
            "payload": lambda b: setattr(_RangeAwareHandler, "payload", b),
            "fail_next": lambda path, n: _RangeAwareHandler.path_failures.update({path: n}),
            "ignore_range": lambda: setattr(_RangeAwareHandler, "ignore_range", True),
        }
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# 1. Range resume
# ---------------------------------------------------------------------------

def test_stream_resume_from_partial(range_server, tmp_path: Path):
    from app.engines import _stream_download

    payload = bytes(range(256)) * 8  # 2048 bytes, deterministic
    range_server["payload"](payload)
    url = f"{range_server['base']}/engine.zip"

    tmp = tmp_path / "engine.partial"
    partial_len = 512
    tmp.write_bytes(payload[:partial_len])

    _stream_download(url, tmp)

    assert tmp.read_bytes() == payload
    assert hashlib.sha256(tmp.read_bytes()).hexdigest() == hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# 2. 200 answered to a Range request must reset, not concatenate
# ---------------------------------------------------------------------------

def test_stream_200_on_range_resets_file(range_server, tmp_path: Path):
    from app.engines import _stream_download

    payload = b"ABCDEFGH" * 128  # 1024 bytes
    range_server["payload"](payload)
    range_server["ignore_range"]()
    url = f"{range_server['base']}/engine.zip"

    tmp = tmp_path / "engine.partial"
    # Garbage prefix that a naive append would keep → corrupted file.
    tmp.write_bytes(b"\xff" * 300)

    _stream_download(url, tmp)

    assert tmp.read_bytes() == payload


# ---------------------------------------------------------------------------
# 3. Cooperative cancellation
# ---------------------------------------------------------------------------

def test_stream_cancel_raises(range_server, tmp_path: Path):
    from app.engines import DownloadCancelled, _stream_download

    payload = b"\x00" * 4096
    range_server["payload"](payload)
    url = f"{range_server['base']}/engine.zip"
    tmp = tmp_path / "engine.partial"

    with pytest.raises(DownloadCancelled):
        _stream_download(url, tmp, cancel_cb=lambda: True)

    # No complete file should be reported; the .partial may exist but must
    # not silently become the final artifact (caller owns that decision).


# ---------------------------------------------------------------------------
# 4. Progress telemetry is monotonic
# ---------------------------------------------------------------------------

def test_stream_progress_monotonic(range_server, tmp_path: Path):
    from app.engines import _stream_download

    payload = b"x" * (2 * 1024 * 1024)  # 2 MiB → 2+ chunks at 1 MiB chunking
    range_server["payload"](payload)
    url = f"{range_server['base']}/engine.zip"
    tmp = tmp_path / "engine.partial"

    seen: list[tuple[int, int]] = []

    def cb(done: int, _delta: int, total: int) -> None:
        seen.append((done, total))

    _stream_download(url, tmp, progress_cb=cb)

    assert seen, "progress callback never fired"
    assert all(t == len(payload) for _d, t in seen), seen
    dones = [d for d, _t in seen]
    assert dones == sorted(dones), f"progress went backwards: {dones}"
    assert dones[-1] == len(payload)


# ---------------------------------------------------------------------------
# 5. Candidate fallback inside EngineManager.install
# ---------------------------------------------------------------------------

def _make_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def test_install_falls_back_to_second_candidate(range_server, tmp_path: Path):
    """First candidate answers 503; the expanded second one must win."""
    from app.engines import EngineManager, EngineState

    payload_zip = tmp_path / "src.zip"
    _make_zip(payload_zip, {"run.sh": b"#!/bin/sh\n"})

    class _FileHandler(BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs):
            pass

        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(payload_zip.stat().st_size))
            self.end_headers()
            self.wfile.write(payload_zip.read_bytes())

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _FileHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        good_url = f"http://127.0.0.1:{port}/mirror/engine.zip"
        bad_url = f"{range_server['base']}/flaky/engine.zip"
        range_server["fail_next"]("/flaky/engine.zip", 1)  # first hit → 503

        fake_expand = [bad_url, good_url]
        with patch("app.engines.expand_engine_candidates", return_value=fake_expand):
            mgr = EngineManager(tmp_path)
            rec = mgr.install("fallback-eng", "https://github.com/x/y/releases/v1/engine.zip")

        assert rec.state == EngineState.INSTALLED
        assert mgr.verify_installed("fallback-eng") is True
    finally:
        server.shutdown()
        server.server_close()


def test_install_all_candidates_fail_marks_failed(range_server, tmp_path: Path):
    from app.engines import EngineManager, EngineState

    range_server["payload"](b"irrelevant")
    url = f"{range_server['base']}/never/engine.zip"
    range_server["fail_next"]("/never/engine.zip", 99)  # always 503

    with patch("app.engines.expand_engine_candidates", return_value=[url]):
        mgr = EngineManager(tmp_path)
        # install() records FAILED + last_error in the manifest, then re-raises
        # so the API layer can surface the failure to the caller.
        with pytest.raises(Exception):  # noqa: B017, PT011 — any transport error
            mgr.install("doomed-eng", "https://github.com/x/y/releases/v1/engine.zip")

    rec = mgr.get("doomed-eng")
    assert rec is not None
    assert rec.state == EngineState.FAILED
    assert rec.last_error  # error text is recorded for the UI


# ---------------------------------------------------------------------------
# 6. Full install cycle over a real network
# ---------------------------------------------------------------------------

def test_install_zip_full_cycle_real_http(range_server, tmp_path: Path):
    from app.engines import EngineManager, EngineState

    payload_zip = tmp_path / "src.zip"
    files = {"engine.sh": b"#!/bin/sh\necho hi\n", "README.md": b"real http install\n"}
    _make_zip(payload_zip, files)
    blob = payload_zip.read_bytes()
    range_server["payload"](blob)

    # GitHub-shaped primary URL; the real expand would point at github.com
    # (unreachable in CI) — patch it so candidate[0] is the local server while
    # still exercising the manager's real download→verify→extract path.
    with patch(
        "app.engines.expand_engine_candidates",
        return_value=[f"{range_server['base']}/x/y/releases/download/v1/e.zip"],
    ):
        mgr = EngineManager(tmp_path)
        rec = mgr.install(
            "real-http-eng",
            "https://github.com/x/y/releases/download/v1/e.zip",
            sha256=hashlib.sha256(blob).hexdigest(),
        )

    assert rec.state == EngineState.INSTALLED
    assert (mgr.engine_dir() / "real-http-eng" / "engine.sh").read_bytes() == files["engine.sh"]
    assert mgr.verify_installed("real-http-eng") is True
    assert mgr.uninstall("real-http-eng") is True
    assert not (mgr.engine_dir() / "real-http-eng").exists()
