"""WebDAV backup cloud sync — push / pull the on-disk backup bundle.

Complements the local full backup (``backup.py`` / ``/api/backup/disk``) with
round-trip storage on a self-hosted WebDAV server (e.g. Nextcloud). The *same*
on-disk bundle produced by ``BackupManager.export_disk`` is uploaded as a single
JSON object, and ``import_disk`` consumes it on pull.

Deliberately stdlib-only (``http.client`` + HTTP Basic): no third-party WebDAV
dependency. Credentials live in ``<data-root>/webdav.json`` and are **never**
returned to the renderer (only a ``has_password`` boolean) and never written to
logs or surfaced in error messages.
"""
from __future__ import annotations

import base64
import contextlib
import http.client
import json
import logging
import os
import ssl
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from .backup import BackupManager

log = logging.getLogger("kevrai.webdav")

# Single fixed object we push/pull inside the remote collection. Pull always
# fetches the latest push, so there is nothing for the renderer to enumerate.
REMOTE_FILENAME = "kevrai-backup.json"
# Generous default: a knowledge-heavy bundle can be a few MB, both directions.
DEFAULT_TIMEOUT_S = 30.0


# ── Errors — mapped to HTTP status codes at the route layer �─────────────────


class WebDavError(Exception):
    """Base class; ``http_status`` is what the route should return."""

    http_status = 502

    def __init__(self, message: str, *, remote_status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.remote_status = remote_status


class WebDavConfigError(WebDavError):
    """Bad / incomplete local configuration (missing URL, scheme, …)."""

    http_status = 400


class WebDavAuthError(WebDavError):
    """The remote rejected our credentials (401 / 403)."""

    http_status = 401


class WebDavNotFoundError(WebDavError):
    """The remote collection / object does not exist (404)."""

    http_status = 404


class WebDavConnectionError(WebDavError):
    """Could not reach the server at all (DNS, refused, timeout, TLS)."""

    http_status = 502


class WebDavRemoteError(WebDavError):
    """Any other unexpected remote response."""

    http_status = 502


# ── Minimal stdlib WebDAV client ─────────────────────────────────────────────


class WebDavClient:
    """One-off, request-scoped WebDAV client over ``http.client``.

    Not a session pool: every call opens a fresh connection, which keeps the
    code trivially correct across the handful of requests a backup push/pull
    makes and avoids threading/keep-alive edge cases on localhost servers.
    """

    def __init__(
        self,
        url: str,
        username: str = "",
        password: str = "",
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.url = (url or "").strip()
        self.username = username or ""
        self.password = password or ""
        self.timeout = timeout
        parts = urlsplit(self.url)
        if parts.scheme not in ("http", "https"):
            raise WebDavConfigError(
                "WebDAV 地址必须以 http:// 或 https:// 开头"
            )
        if not parts.netloc:
            raise WebDavConfigError("WebDAV 地址缺少主机名")
        self.scheme = parts.scheme
        self.host = parts.hostname or ""
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        # Base collection path, e.g. "/remote.php/dav/files/alice". No trailing slash.
        self.base_path = parts.path.rstrip("/")

    # -- low-level request ---------------------------------------------------

    def _connect(self):
        if self.scheme == "https":
            ctx = ssl.create_default_context()
            return http.client.HTTPSConnection(
                self.host, self.port, timeout=self.timeout, context=ctx
            )
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def _auth_header(self) -> dict[str, str]:
        if not self.username and not self.password:
            return {}
        token = f"{self.username}:{self.password}".encode()
        return {"Authorization": "Basic " + base64.b64encode(token).decode("ascii")}

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        depth: str | None = None,
    ) -> tuple[int, bytes]:
        """Perform one WebDAV request. Returns ``(status, body_bytes)``.

        Transport failures become ``WebDavConnectionError``; 401/403 and 404
        map to their specific exceptions; other >=400 responses become
        ``WebDavRemoteError``. Response bodies are truncated before being
        embedded in any error — and never include the request headers, so a
        credential can never leak through an exception message.
        """
        conn = self._connect()
        headers: dict[str, str] = {
            "Accept": "application/json",
            **self._auth_header(),
        }
        if content_type:
            headers["Content-Type"] = content_type
        if depth is not None:
            headers["Depth"] = depth
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read() or b""
            status = int(resp.status)
        except TimeoutError:
            raise WebDavConnectionError("连接 WebDAV 服务器超时") from None
        except (ConnectionError, OSError) as e:
            # DNS failure, refused, reset, bad TLS, … — never echo ``e.args``
            # verbatim because some transports include the URL (which carries
            # no password, but stay conservative).
            log.warning("webdav transport failure: %s", type(e).__name__)
            raise WebDavConnectionError("无法连接 WebDAV 服务器，请检查地址与网络") from None
        finally:
            with contextlib.suppress(Exception):
                conn.close()

        if status in (401, 403):
            raise WebDavAuthError("WebDAV 认证失败：用户名或密码错误", remote_status=status)
        if status == 404:
            raise WebDavNotFoundError("WebDAV 路径不存在 (404)", remote_status=status)
        if status >= 400:
            snippet = data[:200].decode("utf-8", "replace") if data else ""
            log.warning("webdav unexpected status %s: %.120s", status, snippet)
            raise WebDavRemoteError(
                f"WebDAV 服务器返回错误状态 {status}", remote_status=status
            )
        return status, data

    # -- high-level operations -----------------------------------------------

    def collection_path(self, remote_path: str) -> str:
        """Full collection URL path for ``remote_path`` (no trailing slash).

        The base path is taken verbatim from the user's URL; only the segments
        we append (``remote_path``) are percent-encoded, so a directory with a
        space still works against a real server.
        """
        sub = (remote_path or "").strip().strip("/")
        parts = [self.base_path]
        if sub:
            parts.extend(quote(p, safe="") for p in sub.split("/") if p)
        return "/".join(parts).replace("//", "/") or "/"

    def object_path(self, remote_path: str, name: str = REMOTE_FILENAME) -> str:
        return self.collection_path(remote_path) + "/" + name

    def test_connection(self, remote_path: str = "") -> None:
        """PROPFIND the (root) collection. Raises on any failure mode."""
        path = self.collection_path(remote_path) or "/"
        # Empty-ish body is fine; Depth:0 asks for the collection itself.
        self.request("PROPFIND", path, body=b"", depth="0")

    def make_collection(self, remote_path: str) -> None:
        """MKCOL every path segment, tolerating 'already exists' (405)."""
        sub = (remote_path or "").strip().strip("/")
        segments = [quote(p, safe="") for p in sub.split("/") if p]
        current = self.base_path
        for seg in segments:
            current = current + "/" + seg
            try:
                self.request("MKCOL", current)
            except WebDavRemoteError as e:
                # 405 Method Not Allowed => the collection already exists.
                if e.remote_status == 405:
                    continue
                raise

    def upload(self, remote_path: str, data: bytes) -> str:
        """Ensure the collection exists, then PUT the bundle. Returns object path."""
        self.make_collection(remote_path)
        obj = self.object_path(remote_path)
        self.request(
            "PUT", obj, body=data, content_type="application/json; charset=utf-8"
        )
        return obj

    def download(self, remote_path: str) -> bytes:
        obj = self.object_path(remote_path)
        _status, data = self.request("GET", obj)
        return data


# ── Config persistence (atomic, like settings.py) ───────────────────────────


def _config_path(app_root: Path) -> Path:
    return Path(app_root) / "webdav.json"


def load_webdav_config(app_root: Path) -> dict[str, Any]:
    fp = _config_path(app_root)
    if not fp.exists():
        return {"url": "", "username": "", "password": "", "remote_path": "kevrai-backup"}
    try:
        data = json.loads(fp.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError) as e:
        log.warning("webdav.json unreadable/corrupt (%s); using defaults", e)
        return {"url": "", "username": "", "password": "", "remote_path": "kevrai-backup"}
    if not isinstance(data, dict):
        return {"url": "", "username": "", "password": "", "remote_path": "kevrai-backup"}
    return {
        "url": str(data.get("url") or ""),
        "username": str(data.get("username") or ""),
        "password": str(data.get("password") or ""),
        "remote_path": str(data.get("remote_path") or "kevrai-backup"),
    }


def save_webdav_config(app_root: Path, cfg: dict[str, Any]) -> Path:
    fp = _config_path(app_root)
    fp.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(cfg, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(prefix=".webdav-", suffix=".tmp", dir=str(fp.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(blob)
            fh.flush()
            with contextlib.suppress(OSError):
                os.fsync(fh.fileno())
        os.replace(tmp, fp)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return fp


def public_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Strip the password; expose only whether one is set."""
    return {
        "url": cfg.get("url", ""),
        "username": cfg.get("username", ""),
        "remote_path": cfg.get("remote_path", ""),
        "has_password": bool(cfg.get("password")),
    }


# ── Service layer (wires BackupManager + WebDavClient) ───────────────────────


class WebDavSync:
    def __init__(self, app_root: Path, knowledge: Any) -> None:
        self.app_root = Path(app_root)
        self._knowledge = knowledge
        self._backup: BackupManager | None = None

    def _mgr(self) -> BackupManager:
        if self._backup is None:
            self._backup = BackupManager(self.app_root, self._knowledge)
        return self._backup

    # -- config --
    def get_config(self) -> dict[str, Any]:
        return public_config(load_webdav_config(self.app_root))

    def set_config(self, body: dict[str, Any]) -> dict[str, Any]:
        """Persist config. An empty ``password`` keeps the previously saved one."""
        current = load_webdav_config(self.app_root)
        cfg = {
            "url": str(body.get("url") or current["url"]).strip(),
            "username": str(body.get("username") or "").strip(),
            "remote_path": str(
                body.get("remote_path") or current.get("remote_path") or "kevrai-backup"
            ).strip(),
        }
        new_pw = body.get("password")
        if new_pw is None or (isinstance(new_pw, str) and new_pw == ""):
            cfg["password"] = current["password"]
        else:
            cfg["password"] = str(new_pw)
        save_webdav_config(self.app_root, cfg)
        return public_config(cfg)

    def _client_for(self, overrides: dict[str, Any] | None) -> WebDavClient:
        """Build a client from saved config, applying optional unsaved overrides.

        A provided-but-empty ``password`` in ``overrides`` means "use the saved
        password" (so the UI can test without retyping it).
        """
        saved = load_webdav_config(self.app_root)
        url = (overrides or {}).get("url") or saved["url"]
        # Empty username/password both mean "keep the saved value" so the UI can
        # test the current form without re-typing the credential it doesn't show.
        username = (overrides or {}).get("username", None)
        if username is None or username == "":
            username = saved["username"]
        password = (overrides or {}).get("password", None)
        if password is None or password == "":
            password = saved["password"]
        if not url:
            raise WebDavConfigError("尚未配置 WebDAV 地址")
        return WebDavClient(url, username, password)

    def _remote_path_for(self, overrides: dict[str, Any] | None) -> str:
        saved = load_webdav_config(self.app_root)
        rp = (overrides or {}).get("remote_path", None)
        if rp is None or rp == "":
            rp = saved.get("remote_path") or "kevrai-backup"
        return rp

    # -- actions --
    def test(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        client = self._client_for(overrides)
        client.test_connection(self._remote_path_for(overrides))
        return {"ok": True}

    def push(self) -> dict[str, Any]:
        cfg = load_webdav_config(self.app_root)
        if not cfg["url"]:
            raise WebDavConfigError("尚未配置 WebDAV 地址")
        bundle = self._mgr().export_disk()
        data = json.dumps(bundle, ensure_ascii=False).encode("utf-8")
        client = WebDavClient(cfg["url"], cfg["username"], cfg["password"])
        obj = client.upload(cfg["remote_path"], data)
        return {"ok": True, "object": obj, "bytes": len(data)}

    def pull(self, mode: str = "merge") -> dict[str, Any]:
        cfg = load_webdav_config(self.app_root)
        if not cfg["url"]:
            raise WebDavConfigError("尚未配置 WebDAV 地址")
        client = WebDavClient(cfg["url"], cfg["username"], cfg["password"])
        data = client.download(cfg["remote_path"])
        try:
            bundle = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise WebDavRemoteError("远端备份文件不是有效的 JSON") from e
        if not isinstance(bundle, dict):
            raise WebDavRemoteError("远端备份格式错误")
        counts = self._mgr().import_disk(bundle, replace=(mode == "replace"))
        return {"ok": True, **counts}
