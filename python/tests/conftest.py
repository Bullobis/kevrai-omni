"""Shared pytest fixtures for the offline test-suite.

The repository previously had **no** ``conftest.py``; every test file rolled its
own ``TestClient`` + tmp-XDG boilerplate. This module centralizes the pieces the
new hub tests need while staying collision-free with the existing module-scoped
``client`` fixtures (we use distinctly named fixtures here).

Everything defined here is **offline**: the fake hub server binds port 0 (the
kernel assigns a free port) and is torn down + joined in teardown, and
``no_sleep`` never waits on real time.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure ``app`` is importable regardless of the invocation directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Ensure sibling test helpers (``hub_fake_server``) are importable too.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# P0-1: the sidecar refuses every protected route unless the caller presents the
# per-session bearer secret. The Electron main process generates one at spawn
# time; in the test-suite we pin a fixed value so the app under test has a
# secret to check against. This MUST be set before ``app.main`` is imported and
# before any TestClient makes a request.
os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")

# Inject the bearer header into EVERY TestClient in the suite, exactly like the
# Electron main process does. Individual tests stay focused on what they
# actually assert; the auth-enforcement tests override this per-request.
from starlette.testclient import TestClient as _TestClient  # noqa: E402

_orig_testclient_init = _TestClient.__init__


def _testclient_init_with_bearer(self, app, *args, **kwargs):
    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("authorization", f"Bearer {os.environ['KEVRAI_SIDECAR_SECRET']}")
    kwargs["headers"] = headers
    _orig_testclient_init(self, app, *args, **kwargs)


_TestClient.__init__ = _testclient_init_with_bearer


@pytest.fixture(autouse=True)
def _reset_source_registry():
    """Keep the (process-global) source registry out of cross-test state.

    ``/api/download/start`` and ``/api/sources/*`` share ``app.state`` on the
    module-level FastAPI app. Tests that build their own ``TestClient(app)``
    without entering the lifespan would otherwise inherit the probe cache and
    health of a previous test. Clearing the registry before every test makes
    the suite hermetic without touching any assertion.
    """
    try:
        from app import main as app_main

        if hasattr(app_main.app, "state"):
            app_main.app.state.source_registry = None
    except Exception:
        pass
    yield


@pytest.fixture()
def tmp_xdg(monkeypatch):
    """Point XDG / APPDATA at a throwaway dir so tests never touch ~/.config."""
    tmp = Path(tempfile.mkdtemp(prefix="kevrai-test-"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp))
    monkeypatch.setenv("APPDATA", str(tmp))
    return tmp


@pytest.fixture()
def hub_client(tmp_xdg):
    """A ``TestClient`` bound to the sidecar app with isolated settings."""
    from fastapi.testclient import TestClient

    from app import main as app_main

    with TestClient(app_main.app) as c:
        yield c


@pytest.fixture()
def fake_hub():
    """Start an in-process, programmable fake hub server; yield its base URL.

    Teardown **must** shut the server down and join the thread, otherwise the
    process-internal HTTP server keeps the interpreter alive and CI hangs.
    """
    from hub_fake_server import FakeHubServer

    server = FakeHubServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture()
def no_sleep():
    """Record requested back-off delays without ever actually sleeping.

    Returns the list of slept durations so tests can assert retry counts and
    monotonic back-off without paying real wall-clock time.
    """
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(float(seconds))

    _sleep.slept = slept  # type: ignore[attr-defined]
    return _sleep
