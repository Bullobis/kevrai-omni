"""Neuron-Cross (4th slice) — cross-module lifecycle / data-contract regressions.

Pins three bugs found by the cross-module adversarial review:

1. ``PUT /api/settings`` used to rebuild the hub registry singleton via
   ``reset_registry()`` but never repointed ``app.state.hub`` — and the
   lifespan had built *another* orphan registry via ``build_registry()``. The
   registry actually serving requests was therefore never the one shutdown
   closes, so its httpx pools leaked at exit.
2. Changing ``max_concurrent_downloads`` swapped in a fresh Downloader but
   never closed the retired one's httpx pool (one keep-alive pool leaked per
   settings change).
3. ``GET /api/hub/jobs/{job_id}`` read ``snap["downloaded"]`` /
   ``snap["bytes_done"]``, neither of which exists on
   ``DownloadTask.snapshot()`` (the real key is ``downloaded_bytes``), so
   multi-file job progress reported ``bytes_done=0`` and ``ratio=0`` forever.
"""
from __future__ import annotations

import time

from app import main as app_main
from app.downloader import Downloader
from app.hub import get_registry


def test_lifespan_wires_hub_handle_to_the_singleton(hub_client):
    """app.state.hub must BE the registry the requests actually use, so the
    lifespan shutdown closes the live registry (not an orphan)."""
    r = hub_client.get("/api/hub/sources")
    assert r.status_code == 200, r.text
    live = get_registry(app_main.app.state.settings)
    assert app_main.app.state.hub is live


def test_put_settings_repoints_hub_handle_and_retires_old(hub_client, monkeypatch):
    old_hub = app_main.app.state.hub
    closed_hubs: list = []

    def _spy_close_hub(hub):
        # create_task(...) evaluates this factory eagerly inside the PUT
        # handler, so we can observe retirement scheduling synchronously even
        # though the returned coroutine only runs later on the event loop.
        closed_hubs.append(hub)

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(app_main, "_aclose_hub_quietly", _spy_close_hub)

    r = hub_client.put("/api/settings", json={"max_concurrent_downloads": 5})
    assert r.status_code == 200, r.text
    assert r.json()["max_concurrent_downloads"] == 5

    # The lifespan handle must now point at the freshly built singleton...
    new_live = get_registry(app_main.app.state.settings)
    assert app_main.app.state.hub is new_live
    assert app_main.app.state.hub is not old_hub
    # ...and the warm old registry must have been scheduled for retirement.
    assert closed_hubs == [old_hub]


def test_put_settings_retires_old_downloader_pool(hub_client, monkeypatch):
    old_dl = hub_client.app.state.downloader
    retired: list = []

    def _spy_retire(dl):
        retired.append(dl)

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(app_main, "_retire_downloader", _spy_retire)

    r = hub_client.put("/api/settings", json={"max_concurrent_downloads": 7})
    assert r.status_code == 200, r.text

    new_dl = hub_client.app.state.downloader
    assert new_dl is not old_dl
    assert new_dl.max_concurrent == 7
    # The retired downloader (not the new one) must be queued for pool close.
    assert retired == [old_dl]


async def test_retire_downloader_closes_idle_pool():
    """Unit-level: an idle retired downloader's own pool is closed."""
    dl = Downloader(max_concurrent=2)
    own = await dl._get_client()  # creates the lazily-owned pool
    assert dl._own_client is own

    await app_main._retire_downloader(dl)

    assert own.is_closed


class _StubProgressDL:
    """Stand-in for Downloader: emits a realistic task snapshot."""

    def __init__(self, snaps: dict):
        self._snaps = snaps

    async def progress(self, task_id):
        return self._snaps.get(task_id)


def test_hub_job_reports_downloaded_bytes_from_snapshot(hub_client):
    """bytes_done must come from DownloadTask.snapshot()["downloaded_bytes"]."""
    hub_client.app.state.hub_jobs = {
        "job1": {
            "tasks": {
                "t1": {"path": "a.gguf", "dest": "/tmp/x/a.gguf", "size": 1000},
            },
            "dest_root": "/tmp/x",
            "created_at": time.time(),
        }
    }
    hub_client.app.state.downloader = _StubProgressDL({
        "t1": {"status": "downloading", "downloaded_bytes": 250},
    })
    r = hub_client.get("/api/hub/jobs/job1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bytes_done"] == 250, body
    assert body["bytes_total"] == 1000
    # ratio must be 0.25, not the always-0 that the wrong field names produced.
    assert abs(body["ratio"] - 0.25) < 1e-6
    assert body["tasks"][0]["bytes_done"] == 250
