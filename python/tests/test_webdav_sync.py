"""WebDAV backup sync: minimal stdlib fake server + client/service/route tests."""
from __future__ import annotations

import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.webdav_sync import (  # noqa: E402
    REMOTE_FILENAME,
    WebDavAuthError,
    WebDavClient,
    WebDavConfigError,
    WebDavConnectionError,
    WebDavNotFoundError,
    WebDavSync,
    load_webdav_config,
    public_config,
    save_webdav_config,
)

# ── In-process fake WebDAV server ─────────────────────────────────────────────


class _FakeWebDav:
    """ThreadingHTTPServer implementing OPTIONS/PROPFIND/MKCOL/PUT/GET.

    Stores objects in memory. ``reject_auth=True`` makes every authenticated
    request 401 (to exercise the auth mapping). Records the Authorization
    header it saw so tests can assert the Basic credential was sent.
    """

    def __init__(self, expect_user: str = "u", expect_pw: str = "p",
                 reject_auth: bool = False) -> None:
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()
        self.auth_seen: list[str] = []
        self.methods: list[str] = []
        self.expect_user = expect_user
        self.expect_pw = expect_pw
        self.reject_auth = reject_auth

    @property
    def base_url(self) -> str:
        assert self._httpd is not None
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> _FakeWebDav:
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence test noise
                pass

            def _auth_ok(self) -> bool:
                hdr = self.headers.get("Authorization", "")
                outer.auth_seen.append(hdr)
                if outer.reject_auth:
                    return False
                expected = "Basic " + base64.b64encode(
                    f"{outer.expect_user}:{outer.expect_pw}".encode()).decode()
                return hdr == expected

            def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_OPTIONS(self):
                outer.methods.append("OPTIONS")
                self.send_response(200)
                self.send_header("DAV", "1,2")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_PROPFIND(self):
                outer.methods.append("PROPFIND")
                if not self._auth_ok():
                    self._send(401, b"unauthorized")
                    return
                self._send(207, b'<?xml version="1.0"?><d:multistatus/>', "application/xml")

            def do_MKCOL(self):
                outer.methods.append("MKCOL")
                if not self._auth_ok():
                    self._send(401, b"unauthorized")
                    return
                if self.path in outer.dirs:
                    self._send(405, b"method not allowed")
                    return
                outer.dirs.add(self.path)
                self._send(201, b"created")

            def do_PUT(self):
                outer.methods.append("PUT")
                if not self._auth_ok():
                    self._send(401, b"unauthorized")
                    return
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length) if length else b""
                outer.files[self.path] = body
                self._send(201, b"created")

            def do_GET(self):
                outer.methods.append("GET")
                if not self._auth_ok():
                    self._send(401, b"unauthorized")
                    return
                if self.path in outer.files:
                    self._send(200, outer.files[self.path], "application/json")
                else:
                    self._send(404, b"not found")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None


@pytest.fixture()
def webdav_server():
    srv = _FakeWebDav().start()
    try:
        yield srv
    finally:
        srv.stop()


class _FakeKnowledge:
    def __init__(self) -> None:
        self.exported: dict | None = None
        self.imported: list[dict] = []

    def list_kbs(self):
        return [{"id": "k1"}]

    def export_kb(self, kb_id):
        return {"format": "kevrai-kb", "version": 1, "meta": {"name": "Lib"},
                "documents": []}

    def import_kb(self, bundle):
        self.imported.append(bundle)
        return {"kb_id": "k2", "name": "Lib", "doc_count": 0}


# ── Client unit tests ────────────────────────────────────────────────────────


def test_bad_scheme_rejected():
    with pytest.raises(WebDavConfigError):
        WebDavClient("ftp://example.com/dav")


def test_bad_host_rejected():
    with pytest.raises(WebDavConfigError):
        WebDavClient("https://")


def test_basic_auth_header(webdav_server):
    c = WebDavClient(webdav_server.base_url + "/dav/", "u", "p")
    c.test_connection("kevrai-backup")
    # The recorded Authorization must be the correct Basic credential.
    assert webdav_server.auth_seen
    expected = "Basic " + base64.b64encode(b"u:p").decode()
    assert webdav_server.auth_seen[-1] == expected


def test_roundtrip_push_pull_bytes(webdav_server, tmp_path):
    """push uploads exactly the bytes; GET returns them unchanged."""
    c = WebDavClient(webdav_server.base_url + "/dav/", "u", "p")
    payload = json.dumps({"hello": "world", "n": 1}).encode()
    obj = c.upload("kevrai-backup", payload)
    assert obj.endswith(REMOTE_FILENAME)
    assert webdav_server.files[obj] == payload
    back = c.download("kevrai-backup")
    assert back == payload


def test_mkcol_405_is_tolerated(webdav_server, tmp_path):
    """Pre-create the collection (so MKCOL -> 405); upload must still succeed."""
    c = WebDavClient(webdav_server.base_url + "/dav/", "u", "p")
    coll = c.collection_path("kevrai-backup")
    webdav_server.dirs.add(coll)  # simulate an existing collection
    obj = c.upload("kevrai-backup", b"{}")
    assert obj in webdav_server.files


def test_auth_failure_mapped(webdav_server):
    webdav_server.reject_auth = True
    c = WebDavClient(webdav_server.base_url + "/dav/", "u", "p")
    with pytest.raises(WebDavAuthError) as ei:
        c.test_connection("kevrai-backup")
    assert ei.value.http_status == 401


def test_not_found_mapped(webdav_server):
    c = WebDavClient(webdav_server.base_url + "/dav/", "u", "p")
    with pytest.raises(WebDavNotFoundError) as ei:
        c.download("kevrai-backup")  # never uploaded -> 404
    assert ei.value.http_status == 404


def test_connection_failure_mapped(tmp_path):
    # A port with nothing listening -> refused/timeout -> connection error.
    c = WebDavClient("http://127.0.0.1:1/dav/", "u", "p", timeout=2.0)
    with pytest.raises(WebDavConnectionError) as ei:
        c.test_connection("kevrai-backup")
    assert ei.value.http_status == 502


# ── Config persistence ───────────────────────────────────────────────────────


def test_config_roundtrip_no_password_leak(tmp_path):
    save_webdav_config(tmp_path, {
        "url": "http://x/dav/", "username": "u", "password": "s3cret",
        "remote_path": "kb",
    })
    cfg = load_webdav_config(tmp_path)
    assert cfg["password"] == "s3cret"
    pub = public_config(cfg)
    assert "password" not in pub
    assert pub["has_password"] is True
    assert pub["url"] == "http://x/dav/"


def test_public_config_no_password_when_empty(tmp_path):
    pub = public_config(load_webdav_config(tmp_path))
    assert pub["has_password"] is False
    assert "password" not in pub


# ── Service layer (wired to BackupManager) ───────────────────────────────────


def test_service_push_calls_export_and_pull_calls_import(webdav_server, tmp_path):
    k1 = _FakeKnowledge()
    svc = WebDavSync(tmp_path, k1)
    svc.set_config({
        "url": webdav_server.base_url + "/dav/",
        "username": "u", "password": "p", "remote_path": "kevrai-backup",
    })
    out = svc.push()
    assert out["ok"] is True
    # The uploaded object is a real disk bundle with the expected keys.
    obj = out["object"]
    bundle = json.loads(webdav_server.files[obj].decode())
    for key in ("version", "knowledge", "agent_memory", "brain"):
        assert key in bundle

    # Pull into a *fresh* knowledge layer -> import runs.
    k2 = _FakeKnowledge()
    svc2 = WebDavSync(tmp_path, k2)  # same config already on disk
    res = svc2.pull("merge")
    assert res["ok"] is True
    assert res["knowledge_bases"] == 1
    assert len(k2.imported) == 1


def test_service_test_with_unsaved_overrides(webdav_server, tmp_path):
    k = _FakeKnowledge()
    svc = WebDavSync(tmp_path, k)
    # No saved config; pass values straight in for "test before save".
    r = svc.test({
        "url": webdav_server.base_url + "/dav/",
        "username": "u", "password": "p", "remote_path": "kevrai-backup",
    })
    assert r == {"ok": True}


def test_service_test_empty_form_fields_reuse_saved(webdav_server, tmp_path):
    """The UI posts the whole (possibly blank) form on Test; empty username /
    password must fall back to the saved credential rather than authenticate
    as an empty user."""
    k = _FakeKnowledge()
    svc = WebDavSync(tmp_path, k)
    svc.set_config({
        "url": webdav_server.base_url + "/dav/",
        "username": "u", "password": "p", "remote_path": "kevrai-backup",
    })
    # Empty username + empty password (as the FastAPI model defaults to "").
    r = svc.test({"url": "", "username": "", "password": "", "remote_path": ""})
    assert r == {"ok": True}


def test_service_push_without_config_is_400(tmp_path):
    svc = WebDavSync(tmp_path, _FakeKnowledge())
    with pytest.raises(WebDavConfigError) as ei:
        svc.push()
    assert ei.value.http_status == 400


# ── Routes (through FastAPI) ──────────────────────────────────────────────────


def test_route_config_get_has_no_password(hub_client, tmp_path):
    r = hub_client.get("/api/backup/webdav/config")
    assert r.status_code == 200
    body = r.json()
    assert "password" not in body
    assert "has_password" in body


def test_route_push_without_config_is_400(hub_client):
    # Point the route's service at a fresh, unconfigured data root.
    hub_client.app.state.webdav = WebDavSync(_tmp_root(), _FakeKnowledge())
    r = hub_client.post("/api/backup/webdav/push")
    assert r.status_code == 400


def test_route_webdav_bad_mode_is_422(hub_client):
    r = hub_client.post("/api/backup/webdav/pull", json={"mode": "sideways"})
    assert r.status_code == 422


def _tmp_root() -> Path:
    import tempfile
    return Path(tempfile.mkdtemp(prefix="kevrai-wd-route-"))
