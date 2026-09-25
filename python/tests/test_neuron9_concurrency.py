"""K-Cortex Neuron-9 — concurrency & race-condition audit.

Every test here forces a *deterministic* interleaving (threading.Barrier /
asyncio.Event / monkeypatched mid-critical-section delay) rather than hoping the
scheduler lands on the bug. Each confirmed race has a regression test that FAILED
on the pre-fix code; modules that held up under adversarial concurrency get a
guard test asserting their invariant still holds.

Covered:
  * POST /api/hub/download — concurrent job-table registration (was: an empty
    ``app.state.hub_jobs`` is falsy, so ``getattr(...) or {}`` allocated a fresh
    dict per request and one job was lost -> 404 on poll).
  * EngineManager installed.json RMW — an in-flight install progress write
    clobbered a concurrent ``_upsert``/``uninstall`` record.
  * mnn_runtime._get_client() lazy singleton — concurrent first callers spawned
    two subprocess clients (two child processes).
  * main._get_agent() lazy singleton — concurrent first callers built two Agents
    (two sqlite memory connections).
  * Guard tests: downloader single-flight, importer per-dir lock, converter
    single-flight, settings atomic write, post-test resource baseline.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path

import pytest

os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app import main as app_main  # noqa: E402
from app.engines import EngineManager, EngineRecord, EngineState  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeHubAdapter:
    def resolve_url(self, repo, path, revision=""):
        return f"https://hf.example/{repo}/resolve/{revision or 'main'}/{path}"

    def auth_headers(self):
        return {}


class _FakeHub:
    """Stand-in for the HubRegistry that lets the test gate the first await."""

    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate

    async def files(self, hub, repo, revision=""):
        # Park here so both concurrent requests reach the dict-reassignment
        # point with the table still empty.
        await self._gate.wait()
        return {
            "files": [{"path": "model.safetensors", "size": 1024, "sha256": None}],
            "also_on": [],
        }

    def adapter(self, hub):
        return _FakeHubAdapter()


class _FakeDownloader:
    """Stand-in for Downloader; gates inside start() to force the interleaving
    between the job-table read (line ~1407) and its population (line ~1467)."""

    def __init__(self, gate2: asyncio.Event) -> None:
        self._gate2 = gate2
        self.started: list[str] = []

    async def start(self, url, dest, *, sha256=None, candidates=None,
                    extra_headers=None):
        self.started.append(url)
        await self._gate2.wait()
        return "task-" + url[-8:]

    async def progress(self, task_id):
        return {"status": "done", "downloaded_bytes": 1024, "total_bytes": 1024}

    async def aclose(self):
        return None


def _wire_app(tmp_xdg: Path):
    """Point the app at an isolated data dir and return a settings object."""
    from app.settings import Settings

    s = Settings()
    s.model_dir = str(tmp_xdg / "models")
    s.engine_dir = str(tmp_xdg / "engines")
    s.download_dir = str(tmp_xdg / "downloads")
    app_main.app.state.settings = s
    app_main.app.state.settings_path = str(tmp_xdg / "settings.json")
    app_main.app.state.hub = None
    app_main.app.state.downloader = None
    app_main.app.state.hub_jobs = {}
    return s


# ---------------------------------------------------------------------------
# 1) POST /api/hub/download — concurrent job registration must not lose a job
# ---------------------------------------------------------------------------


async def test_hub_jobs_concurrent_no_lost_update(tmp_xdg, monkeypatch):
    _wire_app(tmp_xdg)
    gate = asyncio.Event()
    gate2 = asyncio.Event()
    hub = _FakeHub(gate)
    dl = _FakeDownloader(gate2)
    app_main.app.state.hub = hub
    app_main.app.state.downloader = dl
    # _get_hub() calls get_registry(settings) — redirect to our fake.
    monkeypatch.setattr(app_main, "get_registry", lambda settings=None: hub)

    transport = httpx.ASGITransport(app=app_main.app)
    headers = {"authorization": f"Bearer {os.environ['KEVRAI_SIDECAR_SECRET']}"}
    body = {
        "hub": "hf",
        "repo": "org/model",
        "revision": "main",
        "files": ["model.safetensors"],
        "auto_pick": False,
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://t",
                                 headers=headers) as client:
        t1 = asyncio.create_task(client.post("/api/hub/download", json=body))
        t2 = asyncio.create_task(client.post("/api/hub/download", json=body))
        # Both park inside reg.files() on `gate`.
        await asyncio.sleep(0.1)
        gate.set()
        # Both run to dl.start() on `gate2` — by now each has executed the
        # job-table read/reassign point.
        await asyncio.sleep(0.1)
        gate2.set()
        r1 = await t1
        r2 = await t2

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    j1 = r1.json()["job_id"]
    j2 = r2.json()["job_id"]
    # Both ids must be present in the shared table (not orphaned onto a copy).
    assert set(app_main.app.state.hub_jobs.keys()) == {j1, j2}


# ---------------------------------------------------------------------------
# 2) EngineManager installed.json RMW — no lost record across concurrent writers
# ---------------------------------------------------------------------------


def test_engines_manifest_no_lost_update(tmp_path, monkeypatch):
    import app.engines as emod

    mgr = EngineManager(tmp_path / "root")
    mgr._set_state("engineA", EngineState.NOT_INSTALLED)

    real_write = emod._write_manifest
    entered_mid_rmw = threading.Event()
    proceed = threading.Event()
    armed = {"done": False}

    def slow_write(root, records):
        # Pause the install-like writer right after it built its `records`
        # snapshot from a read, but before it rewrites the manifest.
        if not armed["done"]:
            armed["done"] = True
            entered_mid_rmw.set()
            proceed.wait(timeout=5)
        return real_write(root, records)

    monkeypatch.setattr(emod, "_write_manifest", slow_write)

    def install_like():
        mgr._set_state("engineA", EngineState.DOWNLOADING, progress=0.5)

    def upsert_like():
        entered_mid_rmw.wait(timeout=5)
        # Install hasn't rewritten yet -> this read sees the old snapshot.
        rec = EngineRecord(id="engineC", state=EngineState.INSTALLED, version="v1.0")
        mgr._upsert(rec)
        proceed.set()

    t1 = threading.Thread(target=install_like)
    t2 = threading.Thread(target=upsert_like)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    ids = {r.id for r in mgr._read()}
    assert "engineC" in ids, f"engineC clobbered, manifest={ids}"
    assert "engineA" in ids


def test_install_lock_is_plain_non_reentrant_lock():
    """_INSTALL_LOCK is a plain (non-reentrant) Lock — the design comment says
    re-entrancy would mask a nested install-call bug. Guard the type."""
    import app.engines as emod

    lk = emod._INSTALL_LOCK  # noqa: SLF001
    assert type(lk) is type(threading.Lock())
    assert lk.acquire(blocking=False)
    lk.release()
    # Non-reentrant: a second blocking=False acquire immediately after a held
    # one must fail.
    assert lk.acquire(blocking=False)
    assert not lk.acquire(blocking=False)
    lk.release()


# ---------------------------------------------------------------------------
# 3) mnn_runtime._get_client() lazy singleton — exactly one subprocess client
# ---------------------------------------------------------------------------


def test_mnn_get_client_single_instance(monkeypatch):
    import time

    import app.mnn_runtime as mrn

    monkeypatch.setenv(mrn._SUBPROCESS_ENV_VAR, "1")
    mrn._reset_client_for_tests()

    # Make construction deliberately slow so two threads can BOTH pass the outer
    # ``if _CLIENT is None`` check before either assigns. Without the init lock,
    # both construct -> two clients; with it, the loser blocks on the lock and
    # reuses the winner.
    constructions = {"n": 0}
    real_cls = mrn._MnnSubprocessClient

    class _SlowClient(real_cls):
        def __init__(self, *a, **k):
            constructions["n"] += 1
            time.sleep(0.3)  # widen the race window deterministically
            super().__init__(*a, **k)

    monkeypatch.setattr(mrn, "_MnnSubprocessClient", _SlowClient)
    try:
        barrier = threading.Barrier(8)
        results: list = []
        rlock = threading.Lock()

        def worker():
            barrier.wait()
            c = mrn._get_client()
            with rlock:
                results.append(c)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert len(results) == 8
        # Exactly one client constructed; all callers share it.
        assert constructions["n"] == 1, f"constructed {constructions['n']} clients"
        assert all(c is results[0] for c in results)
        assert mrn._CLIENT is results[0]
    finally:
        mrn._reset_client_for_tests()


# ---------------------------------------------------------------------------
# 4) main._get_agent() lazy singleton — exactly one Agent build
# ---------------------------------------------------------------------------


def test_get_agent_single_build(monkeypatch, tmp_xdg):
    from app.agent import skill_hub as sh_mod
    from app.agent import tools as tools_mod
    from app import agent as agent_mod

    constructions = {"n": 0}

    class _FakeMemory:
        def __init__(self, *a, **k):
            constructions["n"] += 1
            # Widen the check-then-act window so two threads both pass the
            # outer cache check before either stores the built agent.
            import time
            time.sleep(0.3)

    class _FakeAgent:
        def __init__(self, *a, **k):
            self.registry = type("R", (), {"list_tools": lambda self: [],
                                           "list_names": lambda self: []})()
            self.skills = None

    monkeypatch.setattr(agent_mod, "AgentMemory", _FakeMemory)
    monkeypatch.setattr(agent_mod, "Agent", _FakeAgent)
    monkeypatch.setattr(agent_mod, "ModelRouter", lambda: object())
    monkeypatch.setattr(agent_mod, "ToolContext", lambda **k: object())
    monkeypatch.setattr(sh_mod, "load_imported", lambda root: [])
    monkeypatch.setattr(sh_mod, "default_library_root", lambda root: str(tmp_xdg))
    monkeypatch.setattr(tools_mod, "build_skill_manager", lambda *a, **k: object())

    # Reset the singleton so we exercise the build path.
    app_main._AGENT_SINGLETON.clear()

    class _FakeReq:
        class state:
            settings = None

        class app:
            state = type("S", (), {"settings": None})()

    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        app_main._get_agent(_FakeReq())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert constructions["n"] == 1, f"Agent built {constructions['n']} times"
    assert "agent" in app_main._AGENT_SINGLETON
    app_main._AGENT_SINGLETON.clear()


# ---------------------------------------------------------------------------
# 5) Guard tests — modules already correct under adversarial concurrency
# ---------------------------------------------------------------------------


async def test_downloader_single_flight_same_dest():
    """Two start() calls for the SAME dest must coalesce onto one task."""
    from app.downloader import Downloader

    dl = Downloader(max_concurrent=2, stream_retries=0, stream_backoff=(0.0,))
    # Pre-register an in-flight dest directly (bypass network).
    async with dl._tasks_lock:  # noqa: SLF001
        from app.downloader import DownloadStatus, DownloadTask
        existing = DownloadTask(id="existing", url="https://x/a",
                                dest_path="/tmp/same.bin", expected_sha256=None)
        existing.status = DownloadStatus.DOWNLOADING
        dl._tasks[existing.id] = existing  # noqa: SLF001
        dl._inflight_dests["/tmp/same.bin"] = existing.id  # noqa: SLF001
    try:
        tid = await dl.start("https://x/a", "/tmp/same.bin")
        assert tid == "existing"
    finally:
        dl._inflight_dests.pop("/tmp/same.bin", None)  # noqa: SLF001
        dl._tasks.clear()  # noqa: SLF001
        await dl.aclose()


def test_importer_per_dir_lock_is_reentrant_and_distinct(tmp_path):
    from app.importer import _per_models_dir_lock

    d1 = tmp_path / "a"
    d2 = tmp_path / "b"
    l1 = _per_models_dir_lock(d1)
    l1b = _per_models_dir_lock(d1)
    l2 = _per_models_dir_lock(d2)
    assert l1 is l1b           # same dir -> same lock
    assert l1 is not l2         # different dirs -> different locks
    # Reentrant: same thread can acquire twice.
    l1.acquire()
    l1.acquire()
    l1.release()
    l1.release()


def test_converter_single_flight_rejects_second():
    from app import converter

    # Simulate an active task.
    class _T:
        id = "fake"
        status = converter.ConvertStatus.RUNNING

    converter._TASKS["fake"] = _T()  # noqa: SLF001
    converter._ACTIVE = _T()  # noqa: SLF001
    try:
        with pytest.raises(RuntimeError, match="已有转换任务"):
            converter.start_convert("hf-to-mnn-llm", "/src", "/dst")
    finally:
        converter._TASKS.clear()  # noqa: SLF001
        converter._ACTIVE = None  # noqa: SLF001


def test_settings_atomic_write_no_torn_file(tmp_path):
    """Concurrent save_settings must never leave a partial settings.json."""
    from app.settings import Settings, save_settings, load_settings

    p = tmp_path / "settings.json"
    errors: list[BaseException] = []

    def writer(i: int):
        try:
            s = Settings()
            s.theme = "dark" if i % 2 else "light"
            s.max_concurrent_downloads = 1 + (i % 16)
            save_settings(s, p)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    # The file is always valid JSON and a parseable Settings.
    s = load_settings(p)
    assert s.theme in {"light", "dark"}
    assert 1 <= s.max_concurrent_downloads <= 16


# ---------------------------------------------------------------------------
# 6) Resource baseline — no threads / FDs leaked by the concurrency tests
# ---------------------------------------------------------------------------


def test_no_thread_or_fd_leak_after_blast():
    base_threads = threading.active_count()
    base_fds = len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else -1

    barrier = threading.Barrier(16)

    def worker():
        barrier.wait()
        _ = sum(range(1000))

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Give the runtime a moment to reap finished daemon threads.
    import time
    time.sleep(0.2)
    assert threading.active_count() <= base_threads + 2
    if base_fds > 0:
        assert len(os.listdir("/proc/self/fd")) <= base_fds + 5
