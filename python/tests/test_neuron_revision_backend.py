"""Offline regression tests for the GGUF revision / refs backend.

Covers, with zero network access (``httpx.Client`` is faked out):

* ``list_gguf_files`` forwards ``revision`` into the tree URL (URL-quoted), and
  defaults to ``main`` when omitted / blank.
* ``list_repo_revisions`` parses the live-verified ``/refs`` shape
  (``{"branches": [...], "tags": [...], "converts": []}``), tolerates bad JSON
  / non-list fields, falls through mirrors on a single failure, and only raises
  when every mirror fails.
* The ``/api/models/{id}/gguf-files`` endpoint forwards ``?revision=``, and the
  new ``/api/models/{id}/revisions`` endpoint honours the empty-repo / unknown
  -model / upstream-failure contract (empty structure vs 404 vs 502).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import importer  # noqa: E402
from app import main as app_main  # noqa: E402

# ---------------------------------------------------------------------------
# Fake httpx stack — records URLs, replays scripted responses, can raise.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload=None, *, status: int = 200,
                 next_cursor: str | None = None, raw: bytes | None = None):
        self._payload = payload
        self._status = status
        self._raw = raw
        self.headers: dict[str, str] = {}
        if next_cursor:
            self.headers["x-next-cursor"] = next_cursor

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self._status}", request=None, response=None)

    def json(self):
        if self._raw is not None:
            import json as _json
            return _json.loads(self._raw)
        return self._payload


class _FakeClient:
    """Stand-in for ``httpx.Client`` used as a context manager."""

    #: class-level so tests can script behaviour across the patched hole.
    script: list[_FakeResponse] = []
    requests: list[str] = []
    #: URLs containing this substring force a connection error (mirror down).
    dead_substrings: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None):
        _FakeClient.requests.append(url)
        for dead in _FakeClient.dead_substrings:
            if dead in url:
                raise OSError(f"mirror down: {dead}")
        if not _FakeClient.script:
            return _FakeResponse(payload=[])
        return _FakeClient.script.pop(0)


@pytest.fixture(autouse=True)
def _patch_httpx(monkeypatch):
    """Redirect every ``httpx.Client(...)`` (imported lazily inside importer)
    to ``_FakeClient``, and reset the shared script between tests."""
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    _FakeClient.script = []
    _FakeClient.requests = []
    _FakeClient.dead_substrings = []
    yield


# ---------------------------------------------------------------------------
# importer.list_gguf_files — revision passthrough
# ---------------------------------------------------------------------------


def test_list_gguf_files_defaults_to_main(monkeypatch):
    """No revision arg → tree URL hits ``/tree/main`` (baseline unchanged)."""
    _FakeClient.script = [_FakeResponse(payload=[])]
    out = importer.list_gguf_files("Acme/Some-GGUF")
    assert out == []
    assert any("/tree/main?" in u for u in _FakeClient.requests), _FakeClient.requests


def test_list_gguf_files_revision_passthrough():
    """A custom revision is spliced into the tree URL."""
    _FakeClient.script = [_FakeResponse(payload=[])]
    importer.list_gguf_files("Acme/Some-GGUF", revision="dev")
    assert any("/tree/dev?" in u for u in _FakeClient.requests), _FakeClient.requests


def test_list_gguf_files_revision_url_quoted():
    """Refs with ``/`` or spaces are percent-encoded, not path-injected."""
    _FakeClient.script = [_FakeResponse(payload=[])]
    importer.list_gguf_files("Acme/Some-GGUF", revision="refs/heads/my feat")
    # "/refs/heads/my feat" must be encoded as a single path segment.
    assert any("/tree/refs%2Fheads%2Fmy%20feat?" in u
               for u in _FakeClient.requests), _FakeClient.requests


def test_list_gguf_files_blank_revision_falls_back_to_main():
    _FakeClient.script = [_FakeResponse(payload=[])]
    importer.list_gguf_files("Acme/Some-GGUF", revision="   ")
    assert any("/tree/main?" in u for u in _FakeClient.requests), _FakeClient.requests


def test_list_gguf_files_filters_by_pattern():
    page = [
        {"type": "file", "path": "model.Q4_K_M.gguf", "size": 100},
        {"type": "file", "path": "config.json", "size": 8},
        {"type": "directory", "path": "nested", "size": 0},
    ]
    _FakeClient.script = [_FakeResponse(payload=page)]
    out = importer.list_gguf_files("Acme/Some-GGUF", "*.gguf", revision="v1.0")
    assert out == [{"path": "model.Q4_K_M.gguf", "size": 100}]
    assert any("/tree/v1.0?" in u for u in _FakeClient.requests)


# ---------------------------------------------------------------------------
# importer.list_repo_revisions — refs parsing
# ---------------------------------------------------------------------------


def test_list_repo_revisions_parses_branches_and_tags():
    payload = {
        "branches": [
            {"name": "main", "ref": "refs/heads/main",
             "targetCommit": "aaa"},
            {"name": "dev", "ref": "refs/heads/dev",
             "targetCommit": "bbb"},
        ],
        "tags": [
            {"name": "v1.0", "ref": "refs/tags/v1.0",
             "targetCommit": "ccc"},
        ],
        "converts": [],
    }
    _FakeClient.script = [_FakeResponse(payload=payload)]
    out = importer.list_repo_revisions("Acme/Some-GGUF")
    assert out == {
        "branches": [
            {"name": "main", "ref": "refs/heads/main"},
            {"name": "dev", "ref": "refs/heads/dev"},
        ],
        "tags": [{"name": "v1.0", "ref": "refs/tags/v1.0"}],
    }
    # Must hit the aggregate /refs route, not the 404 sub-paths.
    assert any(u.endswith("/refs") for u in _FakeClient.requests), _FakeClient.requests


def test_list_repo_revisions_mirror_fallback():
    """First mirror (hf-mirror) raises → second (hf.co) serves the refs."""
    _FakeClient.dead_substrings = ["hf-mirror.com"]
    payload = {"branches": [{"name": "main", "ref": "refs/heads/main"}],
               "tags": []}
    _FakeClient.script = [_FakeResponse(payload=payload)]
    out = importer.list_repo_revisions("Acme/Some-GGUF")
    assert [b["name"] for b in out["branches"]] == ["main"]
    # Both mirrors were attempted.
    assert len(_FakeClient.requests) == 2


def test_list_repo_revisions_all_mirrors_fail_raises():
    _FakeClient.dead_substrings = ["hf-mirror.com", "huggingface.co"]
    with pytest.raises(OSError):
        importer.list_repo_revisions("Acme/Some-GGUF")


def test_list_repo_revisions_bad_json_is_safe():
    """Non-JSON / non-dict / non-list payload must not crash the parser."""
    # 1) payload is a bare list (wrong shape) → empty structure, no raise.
    _FakeClient.script = [_FakeResponse(payload=["not", "a", "dict"])]
    out = importer.list_repo_revisions("Acme/Some-GGUF")
    assert out == {"branches": [], "tags": []}

    # 2) branches field is a dict, not a list → ignored, tags still parsed.
    _FakeClient.script = [_FakeResponse(
        payload={"branches": {"oops": 1}, "tags": "nope"})]
    out = importer.list_repo_revisions("Acme/Some-GGUF")
    assert out == {"branches": [], "tags": []}

    # 3) malformed entries inside the list (non-dict, missing name) skipped.
    _FakeClient.script = [_FakeResponse(payload={
        "branches": [{"name": "main", "ref": "refs/heads/main"},
                     "garbage", 42, {"no-name": True}],
        "tags": [{"name": "v2", "ref": "refs/tags/v2"}],
    })]
    out = importer.list_repo_revisions("Acme/Some-GGUF")
    assert out == {"branches": [{"name": "main", "ref": "refs/heads/main"}],
                    "tags": [{"name": "v2", "ref": "refs/tags/v2"}]}


def test_list_repo_revisions_empty_repo():
    assert importer.list_repo_revisions("") == {"branches": [], "tags": []}


# ---------------------------------------------------------------------------
# HTTP endpoint layer — uses a TestClient with a fake CATALOG.
# ---------------------------------------------------------------------------


def _fake_catalog(monkeypatch, *, repo: str = "Acme/Some-GGUF",
                  model_id: str = "test-model"):
    """Install a one-model CATALOG so the endpoint lookup hits our fixture."""
    model = SimpleNamespace(id=model_id, gguf_repo=repo)
    catalog = SimpleNamespace(models=[model])
    monkeypatch.setattr(app_main, "CATALOG", catalog)
    return model


@pytest.fixture()
def client():
    return TestClient(app_main.app, raise_server_exceptions=False)


def test_gguf_files_endpoint_passes_revision_query(monkeypatch, client):
    _fake_catalog(monkeypatch)
    seen: dict = {}

    def fake_list(repo, pattern="*.gguf", revision="main"):
        seen["repo"] = repo
        seen["revision"] = revision
        return [{"path": "m.gguf", "size": 1}]

    monkeypatch.setattr(app_main, "list_gguf_files", fake_list)
    r = client.get("/api/models/test-model/gguf-files?revision=v2.0")
    assert r.status_code == 200, r.text
    body = r.json()
    assert seen["revision"] == "v2.0"
    assert seen["repo"] == "Acme/Some-GGUF"
    assert body["revision"] == "v2.0"
    assert body["count"] == 1


def test_gguf_files_endpoint_default_revision(monkeypatch, client):
    _fake_catalog(monkeypatch)
    seen: dict = {}

    def fake_list(repo, pattern="*.gguf", revision="main"):
        seen["revision"] = revision
        return []

    monkeypatch.setattr(app_main, "list_gguf_files", fake_list)
    r = client.get("/api/models/test-model/gguf-files")
    assert r.status_code == 200, r.text
    assert seen["revision"] == "main"
    assert r.json()["revision"] == "main"


def test_gguf_files_endpoint_empty_gguf_repo(monkeypatch, client):
    _fake_catalog(monkeypatch, repo="")
    called = {"n": 0}

    def fake_list(*a, **k):
        called["n"] += 1
        return []

    monkeypatch.setattr(app_main, "list_gguf_files", fake_list)
    r = client.get("/api/models/test-model/gguf-files")
    assert r.status_code == 200, r.text
    assert r.json() == {"files": [], "count": 0}
    assert called["n"] == 0, "must not hit the network for a repo-less model"


def test_gguf_files_endpoint_404_unknown_model(monkeypatch, client):
    _fake_catalog(monkeypatch)
    r = client.get("/api/models/no-such-model/gguf-files")
    assert r.status_code == 404


def test_gguf_files_endpoint_502_on_upstream_failure(monkeypatch, client):
    _fake_catalog(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("network dead")

    monkeypatch.setattr(app_main, "list_gguf_files", boom)
    r = client.get("/api/models/test-model/gguf-files")
    assert r.status_code == 502


def test_revisions_endpoint_returns_branches_tags(monkeypatch, client):
    _fake_catalog(monkeypatch)

    def fake_refs(repo):
        return {"branches": [{"name": "main", "ref": "refs/heads/main"}],
                "tags": [{"name": "v1", "ref": "refs/tags/v1"}]}

    monkeypatch.setattr(app_main, "list_repo_revisions", fake_refs)
    r = client.get("/api/models/test-model/revisions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["repo"] == "Acme/Some-GGUF"
    assert body["branches"] == [{"name": "main", "ref": "refs/heads/main"}]
    assert body["tags"] == [{"name": "v1", "ref": "refs/tags/v1"}]


def test_revisions_endpoint_empty_gguf_repo_returns_empty_structure(monkeypatch, client):
    """A model without gguf_repo → empty structure (NOT 404)."""
    _fake_catalog(monkeypatch, repo="")
    called = {"n": 0}

    def fake_refs(*a, **k):
        called["n"] += 1
        return {"branches": [], "tags": []}

    monkeypatch.setattr(app_main, "list_repo_revisions", fake_refs)
    r = client.get("/api/models/test-model/revisions")
    assert r.status_code == 200, r.text
    assert r.json() == {"repo": "", "branches": [], "tags": []}
    assert called["n"] == 0


def test_revisions_endpoint_404_unknown_model(monkeypatch, client):
    _fake_catalog(monkeypatch)
    r = client.get("/api/models/no-such-model/revisions")
    assert r.status_code == 404


def test_revisions_endpoint_502_on_upstream_failure(monkeypatch, client):
    _fake_catalog(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("network dead")

    monkeypatch.setattr(app_main, "list_repo_revisions", boom)
    r = client.get("/api/models/test-model/revisions")
    assert r.status_code == 502
