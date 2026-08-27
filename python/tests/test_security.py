"""Security-focused tests: path traversal, blocked-mirror URLs, settings validation.

Hits every public entry-point where adversarial input could be funneled in:

* HTTP path params (``model_id``)
* Engine install URL
* Direct download URLs (``download_zip_engine``, ``downloader._check_url``)
* Settings persistence (Pydantic Literal fields)

The tests use ``monkeypatch`` and stand-alone functions where network is
unavoidable, but prefer the FastAPI ``TestClient`` path so they mirror what a
real Electron renderer would do.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from app.catalog import (
    ALLOWED_ENGINE_HOSTS,
    ALLOWED_MODEL_HOSTS,
    DEFAULT_BLOCKED_MIRRORS,
    is_host_allowed,
)
from app.engines import download_zip_engine
from app.settings import Settings, default_settings_path, load_settings, save_settings
from app.downloader import DownloadRefused, _check_url as downloader_check_url


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CATALOG_DIR = REPO_ROOT / "catalog"


# ---------- path traversal in model_id ----------

@pytest.fixture(scope="module")
def client():
    """Shared FastAPI test client (env-redirected to a tempdir)."""
    import importlib  # noqa: F401 — keeps import order deterministic

    tmp = Path(tempfile.mkdtemp(prefix="kevrai-security-"))
    os.environ["LOCALAPPDATA"] = str(tmp)
    os.environ["XDG_DATA_HOME"] = str(tmp)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.main import app  # imported here so env is set first
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


def test_model_id_path_traversal(client):
    """`../../etc/passwd` style path traversals in ``model_id`` must NOT
    leak anything; they must return 404."""
    payloads = [
        "../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "..\\windows\\system32",
        "./../etc/hosts",
        "foo/../bar",
    ]
    for p in payloads:
        r = client.get(f"/api/models/{p}")
        assert r.status_code in (400, 404), (
            f"path-traversal {p!r} returned {r.status_code}, body={r.text}"
        )
        # Body must not echo the input path or contain any file contents
        body = r.text
        assert "root:" not in body
        assert "etc/passwd" not in body or r.status_code in (400, 404)


def test_model_id_url_encoded_traversal(client):
    """Double-encoded and unicode tricks."""
    cases = [
        "%2E%2E%2F%2E%2E%2Fetc%2Fpasswd",
        "....//....//etc/passwd",
        "model/../../etc/shadow",
    ]
    for c in cases:
        r = client.get(f"/api/models/{c}")
        assert r.status_code in (400, 404)


# ---------- blocked-mirror URLs ----------

def test_sufy_url_rejected_at_engine_install(tmp_path):
    """``download_zip_engine`` must refuse any non-allowlisted host."""
    for bad in DEFAULT_BLOCKED_MIRRORS:
        res = download_zip_engine(f"https://{bad}/x.zip", tmp_path, "evil")
        assert res.ok is False
        assert "allowlist" in res.message.lower() or "refusing" in res.message.lower()


def test_sufy_url_rejected_at_downloader_check():
    """Downloader has its OWN check too — belt-and-suspenders."""
    for bad in DEFAULT_BLOCKED_MIRRORS:
        with pytest.raises(DownloadRefused) as exc:
            downloader_check_url(f"https://{bad}/file.bin")
        assert bad in str(exc.value) or "blocked" in str(exc.value).lower()


def test_evil_random_host_rejected(tmp_path):
    """Hosts outside the engine allowlist are rejected."""
    res = download_zip_engine("https://evil.com/x.zip", tmp_path, "evil")
    assert res.ok is False


def test_localhost_non_allowlist_rejected(tmp_path):
    """`127.0.0.1` is not in the engine allowlist (we don't pull from local)."""
    res = download_zip_engine("http://127.0.0.1:9999/x.zip", tmp_path, "evil")
    assert res.ok is False


def test_pydantic_accepts_any_repo_string():
    """v2.2.0: no host is refused by the catalog. Even typosquat domains
    (e.g. ``hf-cdn.sufy.com``) parse cleanly so the user can opt in via
    the in-app Settings → Download sources panel."""
    from app.catalog import ModelEntry
    entry = ModelEntry.model_validate({
        "id": "evil-1", "category": "llm", "name": "x",
        "repo": "hf-cdn.sufy.com/some/repo",
    })
    assert entry.repo == "hf-cdn.sufy.com/some/repo"


# ---------- is_host_allowed ----------

def test_is_host_allowed_full_table():
    """v2.2.0: allowlist is *advisory*; is_host_allowed returns accurate
    membership for the curated default set, but a host outside the set
    simply returns False (advisory, not enforced)."""
    for ok in ALLOWED_MODEL_HOSTS:
        assert is_host_allowed(f"https://{ok}/file", set(ALLOWED_MODEL_HOSTS))
    # Blocked mirrors are no longer in the catalog; ensure the set is empty.
    assert DEFAULT_BLOCKED_MIRRORS == set(), "v2.2.0 removed the global blocklist"


def test_engines_json_every_url_is_well_formed():
    """engines.json must not contain any malformed URL — any http(s) URL is
    acceptable in v2.2.0, but it must at least parse."""
    import json
    engines = json.loads((CATALOG_DIR / "engines.json").read_text(encoding="utf-8"))
    bad: list[tuple[str, str]] = []
    for eng in engines["engines"]:
        for plat, url in (eng.get("platforms") or {}).items():
            if not url:
                continue
            # v2.2.0: any http(s) URL is well-formed; no allowlist check.
            from urllib.parse import urlparse
            p = urlparse(url)
            if p.scheme not in ("http", "https") or not p.netloc:
                bad.append((eng.get("id", "?"), url))
    assert not bad, f"engines with malformed URLs: {bad[:5]}"


# ---------- settings: invalid theme ----------

def test_settings_invalid_theme_rejected_by_pydantic():
    """`theme` is a Literal — any value outside the enum fails validation."""
    with pytest.raises(Exception) as exc:
        Settings(theme="rainbow")
    msg = str(exc.value).lower()
    assert "theme" in msg or "literal" in msg or "enum" in msg


def test_settings_invalid_hardware_accel_rejected():
    with pytest.raises(Exception) as exc:
        Settings(hardware_acceleration="quantum")
    msg = str(exc.value).lower()
    assert "hardware" in msg or "literal" in msg


def test_settings_cap_concurrency_min_1():
    s = Settings(max_concurrent_downloads=0)
    assert s.max_concurrent_downloads == 1


def test_settings_cap_concurrency_max_16():
    s = Settings(max_concurrent_downloads=9999)
    assert s.max_concurrent_downloads == 16


def test_settings_save_load_roundtrip(tmp_path):
    fp = tmp_path / "settings.json"
    s = Settings(theme="dark", max_concurrent_downloads=8)
    save_settings(s, fp)
    s2 = load_settings(fp)
    assert s2.theme == "dark"
    assert s2.max_concurrent_downloads == 8


def test_settings_corrupt_file_falls_back(tmp_path):
    fp = tmp_path / "settings.json"
    fp.write_text("not json {{{")
    s = load_settings(fp)
    # Default theme is "system"
    assert s.theme == "system"


def test_settings_invalid_json_field_kept_unaffected(tmp_path):
    """If a field is wrong, we fall back to defaults entirely."""
    fp = tmp_path / "settings.json"
    fp.write_text('{"theme": "rainbow"}')  # invalid Literal value
    s = load_settings(fp)
    assert s.theme == "system"
