"""Tests for v2.2.0 multi-source speed-test (app.sources)."""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.sources import (
    SourceProbe,
    _score,
    measure_sources,
    pick_best,
)

# ---------------------------------------------------------------------------
# In-process HTTP servers that simulate two mirrors with different speeds
# ---------------------------------------------------------------------------


def _make_handler(delay: float, body: bytes, status: int = 200):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a, **k):  # silence
            pass

        def do_GET(self):
            # Honour Range: bytes=0-N
            range_hdr = self.headers.get("Range", "")
            if range_hdr.startswith("bytes="):
                # We don't actually range — just return a chunk
                spec = range_hdr.split("=", 1)[1]
                end = spec.split("-", 1)[1] if "-" in spec else ""
                try:
                    end_n = int(end) if end else len(body) - 1
                except ValueError:
                    end_n = len(body) - 1
                body_to_send = body[: end_n + 1]
            else:
                body_to_send = body
            if delay:
                time.sleep(delay)
            self.send_response(status)
            self.send_header("Content-Length", str(len(body_to_send)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(body_to_send)

    return H


def _start_server(handler_cls) -> tuple[HTTPServer, str, threading.Thread]:
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    host, port = srv.server_address
    return srv, f"http://{host}:{port}/file", t


def test_probe_score_ranks_speed_and_latency():
    good = SourceProbe(url="a", host="a", ok=True, latency_ms=50,
                       speed_mbps=10, status=200, size_bytes=100)
    slow = SourceProbe(url="b", host="b", ok=True, latency_ms=200,
                       speed_mbps=10, status=200, size_bytes=100)
    broken = SourceProbe(url="c", host="c", ok=False, latency_ms=10,
                         speed_mbps=99, status=500, size_bytes=0)
    assert _score(good) > _score(slow)
    assert _score(good) > _score(broken)
    assert _score(broken) < 0


def test_pick_best_returns_first_ok():
    ranking = [
        {"url": "broken", "ok": False},
        {"url": "ok1", "ok": True},
        {"url": "ok2", "ok": True},
    ]
    assert pick_best(ranking)["url"] == "ok1"


@pytest.mark.asyncio
async def test_measure_sources_real_loopback(tmp_path: Path):
    """Spin up two localhost servers and verify the faster one is picked."""
    body = b"x" * 4096  # small probe body

    slow = _make_handler(delay=0.15, body=body)
    fast = _make_handler(delay=0.0, body=body)
    srv_slow, url_slow, t_slow = _start_server(slow)
    srv_fast, url_fast, t_fast = _start_server(fast)
    try:
        ranking = await measure_sources([url_slow, url_fast])
        assert len(ranking) == 2
        assert all(r["ok"] for r in ranking), [r for r in ranking if not r["ok"]]
        # The fast one should come first (or tie → whichever the score picked).
        assert ranking[0]["url"] == url_fast
    finally:
        srv_slow.shutdown()
        srv_fast.shutdown()
        t_slow.join(timeout=2)
        t_fast.join(timeout=2)


@pytest.mark.asyncio
async def test_measure_sources_handles_unreachable():
    """Unreachable hosts are marked ok=False and ranked last (or excluded)."""
    # Use a port we know is closed.
    ranking = await measure_sources([
        "http://127.0.0.1:1/will-fail",  # privileged/closed port
        "https://huggingface.co/api/models",
    ])
    assert len(ranking) == 2
    # HF should be reachable; the localhost:1 should fail.
    bad = [r for r in ranking if not r["ok"]]
    assert bad, "expected at least one failed probe"
    # The ok one (if any) should be ranked first.
    ok = [r for r in ranking if r["ok"]]
    if ok:
        assert ranking[0] in ok


@pytest.mark.asyncio
async def test_measure_sources_dedups():
    url = "http://127.0.0.1:1/never"
    ranking = await measure_sources([url, url, url])
    assert len(ranking) == 1


# ---------------------------------------------------------------------------
# v2.8.1 compatibility additions (existing 5 cases above are unchanged).
# ---------------------------------------------------------------------------


def test_probe_to_dict_new_fields_present_legacy_unchanged():
    """`SourceProbe.to_dict()` gains fields but keeps every legacy key."""
    p = SourceProbe(url="u", host="h", ok=True, latency_ms=1.0,
                    speed_mbps=2.0, status=200, size_bytes=10)
    d = p.to_dict()
    for legacy in ("url", "host", "ok", "latency_ms", "speed_mbps",
                   "status", "size_bytes", "error"):
        assert legacy in d
    # new v2.8.1 keys
    assert d["source_id"] == ""
    assert d["source_type"] == ""
    assert d["from_cache"] is False


def test_score_fallback_still_linear():
    """The stateless fallback `_score` keeps the legacy linear ordering."""
    fast = SourceProbe(url="f", host="f", ok=True, latency_ms=10,
                       speed_mbps=20, status=200, size_bytes=1)
    slow = SourceProbe(url="s", host="s", ok=True, latency_ms=10,
                       speed_mbps=5, status=200, size_bytes=1)
    assert _score(fast) > _score(slow)
    broke = SourceProbe(url="b", host="b", ok=False, latency_ms=0,
                        speed_mbps=0, status=500, size_bytes=0)
    assert _score(broke) == -1e9


@pytest.mark.asyncio
async def test_measure_sources_registry_param_preserves_signature():
    """Passing no registry keeps the old list-of-dicts return shape."""
    url = "http://127.0.0.1:1/never"
    ranking = await measure_sources([url])
    assert isinstance(ranking, list) and ranking
    r = ranking[0]
    for key in ("url", "host", "ok", "latency_ms", "speed_mbps", "status",
                "size_bytes", "error"):
        assert key in r
