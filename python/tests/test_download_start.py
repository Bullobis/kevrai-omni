"""Tests for /api/download/start — especially the v2.2.x fast-fail behaviour.

When auto-pick is on and the speed probe runs but finds *no* reachable source,
the endpoint must fail fast with HTTP 422 (structured error) instead of
spawning a background task that is guaranteed to fail. If the probe itself
crashes (ranking empty), we must fall back to the primary URL, not refuse.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


class FakeDownloader:
    """Deterministic stand-in so success paths never touch the network."""

    async def start(self, *args, **kwargs):
        return "fake-task-id"

    async def aclose(self):
        return None


@pytest.fixture
def client():
    c = TestClient(app)
    # Override the real downloader injected by lifespan.
    c.app.state.downloader = FakeDownloader()
    yield c


_HOSTS = [
    "huggingface.co",
    "hf-mirror.com",
    "hf-cdn.sufy.com",
    "hf-mirror.us",
    "huggingface.dl.in.tel",
    "hf-cn-mirror.com",
]


def _make_ranking(oks: list[bool]) -> list[dict]:
    out = []
    for host, ok in zip(_HOSTS, oks):
        out.append({
            "url": f"https://{host}/owner/repo/resolve/main/m.bin",
            "host": host,
            "ok": ok,
            "latency_ms": 1.0,
            "speed_mbps": 1.0,
            "status": 200 if ok else 0,
            "size_bytes": 10,
            "error": "" if ok else "unreachable",
        })
    return out


async def _fake_measure(urls, **kwargs):
    return _make_ranking([False] * len(urls))


def test_fail_fast_when_all_sources_unreachable(client, monkeypatch):
    """All candidates unreachable + probe succeeded -> 422, no background task."""
    monkeypatch.setattr("app.sources.measure_sources", _fake_measure)
    r = client.post("/api/download/start", json={
        "url": "https://huggingface.co/owner/repo/resolve/main/m.bin",
        "dest_filename": "m.bin",
        "auto_pick": True,
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "all_sources_unreachable"
    assert isinstance(detail["ranking"], list) and len(detail["ranking"]) >= 1
    assert "均不可达" in detail["message"]


def test_probe_crash_falls_back_no_fastfail(client, monkeypatch):
    """Probe itself crashed (ranking empty) -> must NOT refuse; fall to primary."""
    async def _boom(*a, **k):
        raise RuntimeError("probe crashed")
    monkeypatch.setattr("app.sources.measure_sources", _boom)
    r = client.post("/api/download/start", json={
        "url": "https://huggingface.co/owner/repo/resolve/main/m.bin",
        "dest_filename": "m.bin",
        "auto_pick": True,
    })
    assert r.status_code == 200
    assert r.json()["task_id"] == "fake-task-id"


def test_single_candidate_not_fastfailed(client):
    """Non-HF primary -> no mirror expansion -> single candidate -> no probe."""
    r = client.post("/api/download/start", json={
        "url": "https://github.com/owner/repo/archive/refs/tags/v1.zip",
        "dest_filename": "v1.zip",
        "auto_pick": True,
    })
    assert r.status_code == 200
    assert r.json()["task_id"] == "fake-task-id"


def test_auto_pick_disabled_not_fastfailed(client):
    """Caller explicitly disables auto-pick -> respect it, never refuse."""
    r = client.post("/api/download/start", json={
        "url": "https://huggingface.co/owner/repo/resolve/main/m.bin",
        "dest_filename": "m.bin",
        "auto_pick": False,
    })
    assert r.status_code == 200
    assert r.json()["task_id"] == "fake-task-id"


def test_mixed_reachable_picks_reachable(client, monkeypatch):
    """Primary reachable, mirrors down -> pick the reachable one, 200."""
    async def _measure(urls, **kwargs):
        return _make_ranking([True] + [False] * 5)
    monkeypatch.setattr("app.sources.measure_sources", _measure)
    r = client.post("/api/download/start", json={
        "url": "https://huggingface.co/owner/repo/resolve/main/m.bin",
        "dest_filename": "m.bin",
        "auto_pick": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "fake-task-id"
    assert body["url"].startswith("https://huggingface.co/")
