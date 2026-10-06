"""Offline regression tests for the ClawdChat sidecar heartbeat backend.

Zero network access: ``httpx.Client`` is never constructed — ``heartbeat``'s
``transport`` hook (or a monkeypatched ``_default_client``) feeds scripted
responses, and credential resolution is pointed at a throwaway directory while
the real ``~/.clawdchat`` is hidden. Fake keys only (``clawdchat_TEST`` /
``clawdchat_KEY_*``); no real credential is ever read or asserted.

Coverage:
  * credential file parsing — array / legacy single object / preferred-name
    selection / fallback / missing & malformed;
  * ``heartbeat`` — configured:false short-circuit, claimed field mapping,
    pending-claim claim_url passthrough, /home failure non-fatal, network &
    5xx degrade to reachable=False without raising;
  * security — the fake key appears in NO return body and NO captured log
    record, and only ever rides a ``https://clawdchat.cn/`` URL;
  * the HTTP endpoint — Bearer middleware 401, not-configured 200, healthy
    200, upstream-down 200 (never a 500).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import clawdchat  # noqa: E402
from app import main as app_main  # noqa: E402

FAKE_KEY = "clawdchat_KEY_KCORTEX"

# ---------------------------------------------------------------------------
# Upstream response fixtures (shape verified against the live API)
# ---------------------------------------------------------------------------

_STATUS_CLAIMED = {
    "success": True,
    "status": "claimed",
    "claim_url": None,
    "agent": {
        "name": "kevrai-kcortex",
        "display_name": "K-Cortex",
        "is_claimed": True,
        "is_active": True,
        "post_count": 12,
        "comment_count": 34,
        "karma": 56,
        "last_active_at": "2026-10-06T01:02:03Z",
        "did": "did:plc:fake",
    },
}

_HOME_OK = {
    "success": True,
    "agent": {"name": "kevrai-kcortex"},
    "unread_messages": {"count": 3, "dm_count": 1, "relay_count": 2},
    "notifications": {"unread_total": 7, "breakdown": {}, "highlights": []},
    "new_posts": [],
    "new_members": [],
    "what_to_do": [],
}


# ---------------------------------------------------------------------------
# Fake httpx stack — records URLs+headers, replays scripted responses/errors
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload=None, *, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None)

    def json(self):
        return self._payload


class _FakeClient:
    """Context-manager stand-in for ``httpx.Client``; records every call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None):
        self.calls.append((url, dict(headers or {})))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ---------------------------------------------------------------------------
# Fixtures — hermetic credential environment
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_clawdchat_home(tmp_path, monkeypatch):
    """Point credential resolution at a throwaway tree; hide the real ~/.

    Both lookup locations ($CLAWDCHAT_HOME and ~/.clawdchat) are redirected, so
    a missing test fixture can never fall through onto a real credential file.
    """
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("CLAWDCHAT_HOME", str(fake_home))
    monkeypatch.setattr(clawdchat.Path, "home", classmethod(lambda cls: fake_home))
    return fake_home


def _write_creds(dirpath: Path, data) -> Path:
    p = dirpath / "credentials.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _write_kcortex_creds(dirpath: Path) -> Path:
    return _write_creds(dirpath, [
        {"api_key": "clawdchat_KEY_KEHU", "agent_name": "kehu"},
        {"api_key": FAKE_KEY, "agent_name": "kevrai-kcortex"},
        {"api_key": "clawdchat_KEY_CABINET", "agent_name": "cabinet-premier"},
    ])


# ---------------------------------------------------------------------------
# load_credentials — parsing & selection
# ---------------------------------------------------------------------------

def test_load_credentials_prefers_named_account(isolated_clawdchat_home):
    _write_kcortex_creds(isolated_clawdchat_home)
    acc = clawdchat.load_credentials()
    assert acc is not None
    assert acc["agent_name"] == "kevrai-kcortex"
    assert acc["api_key"] == FAKE_KEY


def test_load_credentials_legacy_single_object(isolated_clawdchat_home):
    _write_creds(isolated_clawdchat_home,
                 {"api_key": "clawdchat_KEY_SOLO", "agent_name": "kehu"})
    acc = clawdchat.load_credentials("kevrai-kcortex")
    assert acc is not None
    assert acc["api_key"] == "clawdchat_KEY_SOLO"


def test_load_credentials_falls_back_first_when_named_missing(isolated_clawdchat_home):
    _write_creds(isolated_clawdchat_home, [
        {"api_key": "clawdchat_KEY_FIRST", "agent_name": "kehu"},
        {"api_key": "clawdchat_KEY_SECOND", "agent_name": "someone-else"},
    ])
    acc = clawdchat.load_credentials("kevrai-kcortex")
    assert acc is not None
    assert acc["api_key"] == "clawdchat_KEY_FIRST"


def test_load_credentials_none_when_no_file(isolated_clawdchat_home):
    assert clawdchat.load_credentials() is None


def test_load_credentials_none_on_malformed_json(isolated_clawdchat_home):
    (isolated_clawdchat_home / "credentials.json").write_text(
        "not json {{{", encoding="utf-8")
    assert clawdchat.load_credentials() is None


# ---------------------------------------------------------------------------
# heartbeat() — unit level (fake transport)
# ---------------------------------------------------------------------------

def test_heartbeat_not_configured_short_circuits(isolated_clawdchat_home):
    seen = {"n": 0}

    def boom_transport():
        seen["n"] += 1
        raise AssertionError("transport must never run without credentials")

    out = clawdchat.heartbeat(transport=boom_transport)
    assert out["configured"] is False
    assert out["reachable"] is False
    assert out["checked_at"]
    assert seen["n"] == 0


def test_heartbeat_claimed_happy_path_maps_fields(isolated_clawdchat_home):
    _write_kcortex_creds(isolated_clawdchat_home)
    client = _FakeClient([_FakeResp(_STATUS_CLAIMED), _FakeResp(_HOME_OK)])
    out = clawdchat.heartbeat(transport=lambda: client)

    assert out["configured"] is True
    assert out["reachable"] is True
    assert out["status"] == "claimed"
    assert out["claimed"] is True
    assert out["claim_url"] is None
    assert out["name"] == "kevrai-kcortex"
    assert out["display_name"] == "K-Cortex"
    assert out["active"] is True
    assert out["post_count"] == 12
    assert out["comment_count"] == 34
    assert out["karma"] == 56
    assert out["last_active_at"] == "2026-10-06T01:02:03Z"
    assert out["unread_messages"] == 3
    assert out["unread_notifications"] == 7

    assert [u for u, _ in client.calls] == [
        "https://clawdchat.cn/api/v1/agents/status",
        "https://clawdchat.cn/api/v1/home",
    ]
    for _, hdrs in client.calls:
        assert hdrs["Authorization"] == f"Bearer {FAKE_KEY}"


def test_heartbeat_pending_claim_passes_claim_url(isolated_clawdchat_home):
    _write_kcortex_creds(isolated_clawdchat_home)
    pending = dict(_STATUS_CLAIMED, status="pending_claim",
                   claim_url="https://clawdchat.cn/claim/abc123",
                   agent=dict(_STATUS_CLAIMED["agent"],
                              is_claimed=False, is_active=False))
    client = _FakeClient([_FakeResp(pending), _FakeResp(_HOME_OK)])
    out = clawdchat.heartbeat(transport=lambda: client)
    assert out["reachable"] is True
    assert out["claimed"] is False
    assert out["claim_url"] == "https://clawdchat.cn/claim/abc123"


def test_heartbeat_home_failure_is_non_fatal(isolated_clawdchat_home):
    _write_kcortex_creds(isolated_clawdchat_home)
    client = _FakeClient([_FakeResp(_STATUS_CLAIMED), httpx.ConnectError("home down")])
    out = clawdchat.heartbeat(transport=lambda: client)
    assert out["reachable"] is True          # /agents/status worked
    assert out["post_count"] == 12
    assert out["unread_messages"] == 0      # defaults when /home failed
    assert out["unread_notifications"] == 0


def test_heartbeat_network_error_degrades_gracefully(isolated_clawdchat_home):
    _write_kcortex_creds(isolated_clawdchat_home)
    client = _FakeClient([httpx.ConnectError("boom")])
    out = clawdchat.heartbeat(transport=lambda: client)
    assert out["configured"] is True        # file exists: retry next cycle
    assert out["reachable"] is False
    assert out["claimed"] is False
    assert out["post_count"] == 0


def test_heartbeat_5xx_on_status_is_reachable_false(isolated_clawdchat_home):
    _write_kcortex_creds(isolated_clawdchat_home)
    client = _FakeClient([_FakeResp({"error": "upstream"}, status=503)])
    out = clawdchat.heartbeat(transport=lambda: client)
    assert out["configured"] is True
    assert out["reachable"] is False


def test_heartbeat_response_and_logs_never_contain_key(isolated_clawdchat_home, caplog):
    secret = "clawdchat_SECRETKEEPME123"
    _write_creds(isolated_clawdchat_home,
                 [{"api_key": secret, "agent_name": "kevrai-kcortex"}])
    caplog.set_level(logging.INFO)

    # Error path (hits the "unreachable" log line) and happy path both.
    bad = _FakeClient([httpx.ConnectError("boom")])
    out = clawdchat.heartbeat(transport=lambda: bad)
    assert secret not in json.dumps(out)
    assert secret not in caplog.text

    good = _FakeClient([_FakeResp(_STATUS_CLAIMED), _FakeResp(_HOME_OK)])
    out = clawdchat.heartbeat(transport=lambda: good)
    assert secret not in json.dumps(out)
    # The key only ever rode a clawdchat.cn URL.
    for url, hdrs in good.calls:
        assert url.startswith("https://clawdchat.cn/")
        assert hdrs["Authorization"] == f"Bearer {secret}"


# ---------------------------------------------------------------------------
# HTTP endpoint — Bearer middleware + 200-instead-of-500 contract
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    return TestClient(app_main.app, raise_server_exceptions=False)


def test_heartbeat_endpoint_401_without_valid_bearer(client):
    r = client.post("/api/clawdchat/heartbeat",
                    headers={"Authorization": "Bearer wrong-secret"})
    assert r.status_code == 401


def test_heartbeat_endpoint_not_configured_is_200(isolated_clawdchat_home, client):
    r = client.post("/api/clawdchat/heartbeat")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is False
    assert body["reachable"] is False


def test_heartbeat_endpoint_healthy_is_200(isolated_clawdchat_home, monkeypatch, client):
    _write_kcortex_creds(isolated_clawdchat_home)
    fake = _FakeClient([_FakeResp(_STATUS_CLAIMED), _FakeResp(_HOME_OK)])
    monkeypatch.setattr(clawdchat, "_default_client", lambda: fake)
    r = client.post("/api/clawdchat/heartbeat")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True
    assert body["reachable"] is True
    assert body["name"] == "kevrai-kcortex"
    assert body["claimed"] is True
    assert body["unread_messages"] == 3
    assert body["unread_notifications"] == 7
    assert FAKE_KEY not in json.dumps(body)


def test_heartbeat_endpoint_upstream_down_still_200(isolated_clawdchat_home, monkeypatch,
                                                  client):
    _write_kcortex_creds(isolated_clawdchat_home)
    fake = _FakeClient([httpx.ConnectError("down")])
    monkeypatch.setattr(clawdchat, "_default_client", lambda: fake)
    r = client.post("/api/clawdchat/heartbeat")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True
    assert body["reachable"] is False
