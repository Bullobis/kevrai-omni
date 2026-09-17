"""v2.8.1 — version consistency + singleton/cache locking regressions.

Two hardening themes from the 2.8.1 audit:
  1. Version is declared in exactly one place per layer and never drifts
     (``app.__version__`` → ``USER_AGENT`` → engine UA; package.json → catalog).
  2. Process-wide singletons/memoisation that are read-then-written under
     concurrency hold a lock (registry, engine-id cache).
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1. Version single-source-of-truth
# ---------------------------------------------------------------------------

def test_version_is_2_8_1_everywhere():
    import app

    assert app.__version__ == "2.8.1"
    assert app.USER_AGENT == "kevrai-omni/2.8.1"


def test_engine_ua_delegates_to_package_version():
    """engines._UA() must not hardcode a version that can drift."""
    from app.engines import _UA
    import app

    assert _UA() == f"kevrai-omni/{app.__version__}"


def test_hub_adapters_use_shared_user_agent():
    import app
    from app.hub import hf, modelscope

    assert hf.USER_AGENT == app.USER_AGENT
    assert modelscope.USER_AGENT == app.USER_AGENT


def test_no_hardcoded_ua_version_in_source():
    """No module may embed a *stale* product/version UA literal.

    ``__init__.py`` is the canonical definition. ``engines._UA()`` keeps one
    literal as a last-resort fallback for the (unlikely) case where the package
    import fails — that one is allowed, but it must match ``__version__``.
    Historically the UA also drifted in *brand* (``kevrai-studio/2.3.0``,
    ``KevraiStudio/2.3``), so every brand spelling is checked, not just the
    current one.
    """
    import app

    allowed = {REPO_ROOT / "python" / "app" / "__init__.py"}
    # Any "ProductName/x.y.z" UA-shaped literal in any known brand spelling.
    pattern = re.compile(
        r'["\'](kevrai-omni|kevrai-studio|KevraiStudio|Kevrai Omni)/(\d+\.\d+(?:\.\d+)?)["\']'
    )
    offenders: list[str] = []
    for p in (REPO_ROOT / "python" / "app").rglob("*.py"):
        if p in allowed:
            continue
        src = p.read_text(encoding="utf-8")
        for m in pattern.finditer(src):
            brand, ver = m.group(1), m.group(2)
            if p.name == "engines.py" and brand == "kevrai-omni" and ver == app.__version__:
                continue  # the sanctioned fallback literal
            offenders.append(f"{p.relative_to(REPO_ROOT)}: {m.group(0)}")
    assert not offenders, "hardcoded/stale UA literal found:\n" + "\n".join(offenders)


def test_catalog_version_matches_package_version():
    pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    for name in ("engines", "models"):
        cat = json.loads((REPO_ROOT / "catalog" / f"{name}.json").read_text(encoding="utf-8"))
        assert cat["version"] == pkg["version"], f"{name}.json version drift"


def test_health_reports_package_version():
    """The sidecar /api/health version must equal package.json's version."""
    from fastapi.testclient import TestClient
    from app import main as app_main

    pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    r = TestClient(app_main.app).get("/api/health")
    assert r.status_code == 200
    assert r.json()["version"] == pkg["version"]


# ---------------------------------------------------------------------------
# 2. Concurrency: registry + engine-id cache are lock-protected
# ---------------------------------------------------------------------------

def test_engine_id_cache_is_stable_under_concurrency():
    from app.hub import taxonomy

    taxonomy.reset_engine_ids_cache()
    results: list[set[str]] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(taxonomy.known_engine_ids())
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, errors
    assert len(results) == 8
    # All threads must observe the exact same (cached) set object.
    first = results[0]
    assert all(r is first for r in results), "cache returned different objects"


def test_registry_singleton_under_concurrency():
    from app.hub import get_registry, reset_registry

    reset_registry()
    out: list[object] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait(timeout=5)
            out.append(get_registry(None))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, errors
    assert len(out) == 8
    assert all(o is out[0] for o in out), "registry singleton broken under race"
    reset_registry()


def test_reset_registry_rebuilds_new_instance():
    from app.hub import get_registry, reset_registry

    r1 = get_registry(None)
    reset_registry()
    r2 = get_registry(None)
    assert r1 is not r2
    reset_registry()
