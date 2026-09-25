"""K-Cortex Neuron-10 — settings PUT atomicity & lost-update repair.

The ``PUT /api/settings`` handler does a read-modify-write (RMW):

    s = app.state.settings.model_copy()   # read snapshot
    ... apply patch ...
    save_settings(s, path)                # persist
    app.state.settings = s                # publish

Two concurrent partial updates (A patches ``theme``, B patches
``max_concurrent_downloads``) previously each snapshotted the *same* old
``Settings``; whichever published last clobbered the other's field — a lost
update. The endpoint body had no ``await``, so on a single event loop the RMW
ran to completion without interleaving *by accident*; any future await inside
the critical section (e.g. offloading the disk write) would reopen the race.

Fix: a per-running-loop ``asyncio.Lock`` serialises read->patch->persist->
publish. The on-disk write was already atomic (temp-file + fsync + os.replace);
this slice only adds the in-memory RMW guard, a warning-logged corruption
fallback on ``load_settings``, and rejects unknown request-body fields
(``extra="forbid"``).

Deterministic interleaving: the handler persists through an ``async`` seam
(``_persist_settings``). Tests monkeypatch that seam with an ``asyncio.Event``
gate so request A parks *after* its read-copy but *before* it publishes — then
request B is released. Under the lock B blocks; without it B snapshots the
stale state and clobbers A.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any


os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app import main as app_main  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NoOpLock:
    """Stand-in lock that serialises nothing — used to expose the raw race."""

    async def __aenter__(self) -> "_NoOpLock":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _wire_app(tmp_xdg: Path) -> None:
    """Point the app at an isolated data dir and reset singleton handles."""
    from app.settings import Settings

    s = Settings()
    s.model_dir = str(tmp_xdg / "models")
    s.engine_dir = str(tmp_xdg / "engines")
    s.download_dir = str(tmp_xdg / "downloads")
    app_main.app.state.settings = s
    app_main.app.state.settings_path = str(tmp_xdg / "settings.json")
    app_main.app.state.hub = None
    app_main.app.state.downloader = None
    app_main.app.state.source_registry = None
    app_main.app.state.hub_jobs = {}


def _headers() -> dict[str, str]:
    return {"authorization": f"Bearer {os.environ['KEVRAI_SIDECAR_SECRET']}"}


# ---------------------------------------------------------------------------
# 1) Deterministic lost-update — the gate forces A to park between read & write
# ---------------------------------------------------------------------------


async def test_lost_update_regression_both_fields_preserved(tmp_xdg, monkeypatch):
    """With the real lock, B's update survives A's (regression guard).

    On the pre-fix code (no lock) this test FAILS: B snapshots the stale
    Settings before A publishes, so A's later publish reverts B's field.
    """
    _wire_app(tmp_xdg)
    entered = asyncio.Event()
    gate = asyncio.Event()
    real_persist = app_main._persist_settings

    async def gated_persist(s, path):
        # Park the FIRST writer right after its read-copy + patch, but before
        # it publishes on app.state.settings.
        if not entered.is_set():
            entered.set()
            await gate.wait()
        await real_persist(s, path)

    monkeypatch.setattr(app_main, "_persist_settings", gated_persist)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_main.app),
        base_url="http://t", headers=_headers(),
    ) as client:
        t_a = asyncio.create_task(
            client.put("/api/settings", json={"theme": "dark"}))
        # Let A reach the gate (it now holds the lock, parked on `gate`).
        await asyncio.sleep(0.15)
        assert entered.is_set(), "A never reached the persist gate"
        t_b = asyncio.create_task(
            client.put("/api/settings", json={"max_concurrent_downloads": 9}))
        # Give B a chance to run. With the lock it parks on lock.acquire();
        # without the lock it would snapshot the stale state and clobber.
        await asyncio.sleep(0.15)
        gate.set()
        r_a, r_b = await asyncio.gather(t_a, t_b)

    assert r_a.status_code == 200, r_a.text
    assert r_b.status_code == 200, r_b.text
    final = app_main.app.state.settings
    # The lock serialised the two RMWs: B built on top of A's snapshot, so
    # BOTH fields must survive.
    assert final.theme == "dark", f"theme lost: {final.theme!r}"
    assert final.max_concurrent_downloads == 9, (
        f"max_concurrent_downloads lost (clobbered): "
        f"{final.max_concurrent_downloads!r}"
    )


async def test_lost_update_is_real_without_lock(tmp_xdg, monkeypatch):
    """DEMONSTRATION that the gate actually exposes a lost update.

    Neutralising the lock (``_NoOpLock``) must drop B's update. This documents
    *why* the lock is required and proves the gating test above is not a
    vacuous pass.
    """
    _wire_app(tmp_xdg)
    entered = asyncio.Event()
    gate = asyncio.Event()
    real_persist = app_main._persist_settings

    async def gated_persist(s, path):
        if not entered.is_set():
            entered.set()
            await gate.wait()
        await real_persist(s, path)

    monkeypatch.setattr(app_main, "_persist_settings", gated_persist)
    monkeypatch.setattr(app_main, "_settings_lock", lambda: _NoOpLock())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_main.app),
        base_url="http://t", headers=_headers(),
    ) as client:
        t_a = asyncio.create_task(
            client.put("/api/settings", json={"theme": "dark"}))
        await asyncio.sleep(0.15)
        assert entered.is_set()
        t_b = asyncio.create_task(
            client.put("/api/settings", json={"max_concurrent_downloads": 9}))
        await asyncio.sleep(0.15)
        gate.set()
        await asyncio.gather(t_a, t_b)

    final = app_main.app.state.settings
    # A publishes last, from its own stale snapshot — B's mcd=9 is gone.
    assert final.theme == "dark"
    assert final.max_concurrent_downloads != 9, (
        "lock removal did NOT cause a lost update — the gate is not exercising "
        "the RMW window"
    )


# ---------------------------------------------------------------------------
# 2) Atomic write + corrupt-file load fallback
# ---------------------------------------------------------------------------


def test_save_is_atomic_and_load_roundtrips(tmp_path):
    from app.settings import Settings, load_settings, save_settings

    p = tmp_path / "settings.json"
    s = Settings()
    s.theme = "dark"
    s.max_concurrent_downloads = 12
    save_settings(s, p)
    # No leftover temp file.
    leftovers = [x for x in tmp_path.iterdir() if x.name.startswith(".settings-")]
    assert leftovers == [], f"temp file leaked: {leftovers}"
    loaded = load_settings(p)
    assert loaded.theme == "dark"
    assert loaded.max_concurrent_downloads == 12


def test_load_settings_corrupt_json_falls_back_to_defaults(tmp_path, caplog):
    from app.settings import Settings, load_settings

    p = tmp_path / "settings.json"
    p.write_text('{"theme": "dark", "max_concuren" ::: not json', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="app.settings"):
        s = load_settings(p)
    assert isinstance(s, Settings)
    # Default theme (not the half-written value).
    assert s.theme == "system"
    # The corruption was logged, not silently swallowed.
    assert any("settings" in rec.message.lower() and ("corrupt" in rec.message.lower()
               or "unreadable" in rec.message.lower()) for rec in caplog.records)


def test_load_settings_non_object_falls_back(tmp_path):
    from app.settings import Settings, load_settings

    p = tmp_path / "settings.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    s = load_settings(p)
    assert isinstance(s, Settings)


def test_load_settings_schema_mismatch_falls_back(tmp_path):
    from app.settings import load_settings

    p = tmp_path / "settings.json"
    # theme is a Literal; a bogus value fails model_validate -> defaults.
    p.write_text(json.dumps({"theme": "rainbow"}), encoding="utf-8")
    s = load_settings(p)
    assert s.theme == "system"


# ---------------------------------------------------------------------------
# 3) Concurrency stress — N concurrent PUTs on disjoint fields
# ---------------------------------------------------------------------------


# (field, pool of values) — every request writes exactly one of these.
_STRESS_FIELDS: list[tuple[str, list[Any]]] = [
    ("theme", ["light", "dark"]),
    ("telemetry_enabled", [True, False]),
    ("debug_http_logs", [True, False]),
    ("hub_page_size", [10, 20, 40, 60]),
    ("hub_cache_ttl_s", [30, 60, 120]),
    ("max_model_size_gb", [50, 100, 300]),
]


async def test_concurrent_put_stress_no_lost_update_no_deadlock(tmp_xdg):
    _wire_app(tmp_xdg)
    n = 40
    # Track which value each request writes, per field.
    written: dict[str, set] = {f: set() for f, _ in _STRESS_FIELDS}
    bodies: list[dict[str, Any]] = []
    for i in range(n):
        field, pool = _STRESS_FIELDS[i % len(_STRESS_FIELDS)]
        val = pool[i % len(pool)]
        bodies.append({field: val})
        written[field].add(val)

    base_threads = threading.active_count()
    base_fds = (
        len(os.listdir("/proc/self/fd"))
        if os.path.isdir("/proc/self/fd") else -1
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_main.app),
        base_url="http://t", headers=_headers(),
    ) as client:
        results = await asyncio.wait_for(
            asyncio.gather(*(client.put("/api/settings", json=b) for b in bodies),
                           return_exceptions=True),
            timeout=30,
        )

    # Every request returned 200 — no deadlock, no exception.
    for i, r in enumerate(results):
        assert not isinstance(r, Exception), f"PUT #{i} raised: {r!r}"
        assert r.status_code == 200, f"PUT #{i} -> {r.status_code}: {r.text}"

    # The on-disk file is always valid JSON and loadable.
    on_disk = json.loads(Path(tmp_xdg, "settings.json").read_text(encoding="utf-8"))
    assert isinstance(on_disk, dict)

    final = app_main.app.state.settings
    # Every field that was written ended at a value one of its writers chose —
    # never snapped back to a stale snapshot's value (the lost-update signature).
    for field, pool in _STRESS_FIELDS:
        assert getattr(final, field) in written[field], (
            f"{field}={getattr(final, field)!r} not among the values written "
            f"{sorted(written[field])} — a concurrent RMW clobbered it"
        )

    # Let the fire-and-forget pool-retirement tasks drain, then check baseline.
    await asyncio.sleep(0.3)
    assert threading.active_count() <= base_threads + 2
    if base_fds > 0:
        assert len(os.listdir("/proc/self/fd")) <= base_fds + 8


# ---------------------------------------------------------------------------
# 4) Incidental checks — multi-field, unknown field, validate_assignment
# ---------------------------------------------------------------------------


def test_put_multiple_fields_take_effect(tmp_xdg):
    _wire_app(tmp_xdg)
    from fastapi.testclient import TestClient

    with TestClient(app_main.app) as c:
        r = c.put("/api/settings", headers=_headers(),
                  json={"theme": "dark", "telemetry_enabled": True,
                        "max_concurrent_downloads": 11, "debug_http_logs": True})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["theme"] == "dark"
    assert j["telemetry_enabled"] is True
    assert j["max_concurrent_downloads"] == 11
    assert j["debug_http_logs"] is True


def test_put_unknown_field_rejected_422(tmp_xdg):
    _wire_app(tmp_xdg)
    from fastapi.testclient import TestClient

    with TestClient(app_main.app) as c:
        r = c.put("/api/settings", headers=_headers(),
                  json={"theme": "dark", "thme": "typo"})
    # extra="forbid" -> FastAPI 422 (was: 200, silently ignored).
    assert r.status_code == 422, r.text


def test_put_invalid_literal_still_400_under_lock(tmp_xdg):
    """validate_assignment (slice 3) must still 400 after locking."""
    _wire_app(tmp_xdg)
    from fastapi.testclient import TestClient

    with TestClient(app_main.app) as c:
        r1 = c.put("/api/settings", headers=_headers(), json={"theme": "rainbow"})
        r2 = c.put("/api/settings", headers=_headers(),
                   json={"hardware_acceleration": "quantum"})
    assert r1.status_code == 400, r1.text
    assert r2.status_code == 400, r2.text
    # The rejected value must not have persisted.
    assert app_main.app.state.settings.theme == "system"
    assert app_main.app.state.settings.hardware_acceleration == "auto"

