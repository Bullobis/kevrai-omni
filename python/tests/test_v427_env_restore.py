"""Regression: module-scoped client fixtures restore env vars on teardown.

Loads each test module with a pass-through ``pytest.fixture`` shim so the raw
fixture generator is reachable, then drives it to ``yield`` and closes it
(running the ``finally`` block), asserting the redirected env values are
restored and cannot leak into later test modules.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
from pathlib import Path

TESTS = Path(__file__).resolve().parent
MODULES = [
    "test_smoke.py",
    "test_security.py",
    "test_v24_api.py",
    "test_v241_api.py",
]
REAL_PYTEST = importlib.import_module("pytest")


def _passthrough_fixture(*args, **kwargs):
    if args and callable(args[0]) and not kwargs:
        return args[0]
    return lambda fn: fn


class _PytestShim(types.ModuleType):
    fixture = staticmethod(_passthrough_fixture)

    def __getattr__(self, name):
        return getattr(REAL_PYTEST, name)


def _load_raw(fname):
    shim = _PytestShim("pytest")
    saved = sys.modules.get("pytest")
    sys.modules["pytest"] = shim
    try:
        spec = importlib.util.spec_from_file_location(f"raw_{fname[:-3]}", TESTS / fname)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved is not None:
            sys.modules["pytest"] = saved
    return mod


def test_fixtures_restore_environment():
    sentinel_xdg = "/kevrai-env-restore-sentinel-xdg"
    sentinel_local = "/kevrai-env-restore-sentinel-local"
    try:
        for fname in MODULES:
            os.environ["XDG_DATA_HOME"] = sentinel_xdg
            os.environ["LOCALAPPDATA"] = sentinel_local
            gen = _load_raw(fname).client()
            next(gen)      # body up to `yield`
            gen.close()    # teardown (`finally`)
            assert os.environ["XDG_DATA_HOME"] == sentinel_xdg, fname
            assert os.environ["LOCALAPPDATA"] == sentinel_local, fname
    finally:
        os.environ.pop("XDG_DATA_HOME", None)
        os.environ.pop("LOCALAPPDATA", None)
