"""A programmable, in-process fake hub server for offline tests.


One single HTTP server (bound to port 0 — the kernel picks a free port, so CI
never collides) serves **both** the HuggingFace and ModelScope response shapes,
selected by the request path. Every failure mode is toggled with a **query
parameter** so a test just appends ``?boom=1`` instead of spinning up another
server.

Failure-mode switches (query params):

==============  ==========================================================
``delay``       sleep N seconds before replying (slow / read-timeout tests)
``cut``         send ``Content-Length`` then only half the body, then close
``bad``         ``1`` → non-JSON body; ``2`` → ``Data`` is a string;
                ``3`` → drop known fields; ``4`` → ``Data`` is a dict not list
``n``           override the item count (``0`` → empty result set)
``rl``          ``1`` → 429 + ``Retry-After: 1``; ``2`` → 429 with no header
``auth``        ``1`` → 401; ``2`` → 403
``boom``        ``1`` → 500; ``2`` → 503
``norange``     download route ignores ``Range`` and returns the whole body
``enospc``      download route sends a little data then aborts the connection
==============  ==========================================================

The server also records every path it was asked for, so tests can assert that
caching collapsed N client calls into 1 upstream request.
"""
from __future__ import annotations

import contextlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


def hf_item(ns: str, name: str, *, downloads: int = 1000, likes: int = 10,
            pipeline: str = "text-generation", tags: list[str] | None = None) -> dict[str, Any]:
    """Build one HuggingFace ``/api/models`` item (verified key names)."""
    return {
        "_id": f"{ns}/{name}",
        "id": f"{ns}/{name}",
        "modelId": name,
        "author": ns,
        "downloads": downloads,
        "likes": likes,
        "trendingScore": downloads // 100,
        "pipeline_tag": pipeline,
        "library_name": "transformers",
        "tags": tags if tags is not None else ["license:mit", "safetensors"],
        "lastModified": "2026-01-01T00:00:00.000Z",
        "createdAt": "2025-01-01T00:00:00.000Z",
        "private": False,
    }


def ms_item(namespace: str, name: str, *, downloads: int = 5000, stars: int = 20,
            task: str = "text-generation", domain: str = "nlp",
            license_: str = "apache-2.0") -> dict[str, Any]:
    """Build one ModelScope PascalCase item (verified shape)."""
    return {
        "Path": namespace,
        "Name": name,
        "ChineseName": name,
        "Description": f"{name} 描述",
        "License": license_,
        "Downloads": downloads,
        "Stars": stars,
        "Revision": "master",
        "StorageSize": 4_000_000_000,
        "Tasks": [{"Name": task, "DomainName": domain,
                   "ChineseName": "文本生成", "Id": 1}],
        "Frameworks": ["pytorch"],
        "Architectures": ["LlamaForCausalLM"],
    }


class FakeHubServer:
    """A controllable HTTP server emulating HF + ModelScope endpoints."""

    def __init__(self) -> None:
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.requests: list[str] = []
        self.hf_items: list[dict[str, Any]] = [
            hf_item("Qwen", "Qwen2.5-7B-Instruct"),
            hf_item("meta-llama", "Llama-3.1-8B-Instruct"),
        ]
        self.ms_items: list[dict[str, Any]] = [
            ms_item("Qwen", "Qwen2.5-7B-Instruct"),
            ms_item("deepseek-ai", "DeepSeek-V3"),
        ]
        self.hf_files: list[dict[str, Any]] = [
            {"type": "file", "path": "config.json", "size": 800},
            {"type": "file", "path": "model.safetensors", "size": 4_000_000_000},
            {"type": "file", "path": "model.gguf", "size": 2_000_000_000},
        ]
        self.ms_files: list[dict[str, Any]] = [
            {"Name": "config.json", "Path": "config.json", "Size": 800,
             "Sha256": "", "IsLFS": False, "Type": "blob"},
            {"Name": "model.safetensors", "Path": "model.safetensors",
             "Size": 4_000_000_000, "Sha256": "", "IsLFS": True, "Type": "blob"},
        ]
        #: Global failure-mode override applied to *every* request. Mirrors the
        #: query-param switches but survives adapters that append paths onto the
        #: base URL (a ``?`` in the base would corrupt the path). Values match
        #: the query switches, e.g. ``{"boom": "1"}`` or ``{"bad": "1"}``.
        self.force: dict[str, str] = {}

    # -- lifecycle ---------------------------------------------------------

    @property
    def base_url(self) -> str:
        assert self._httpd is not None, "server not started"
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> FakeHubServer:
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        daemon=True, name="fake-hub")
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._httpd = None
        self._thread = None

    def reset_requests(self) -> None:
        self.requests.clear()

    def count(self, needle: str) -> int:
        return sum(1 for r in self.requests if needle in r)


def _make_handler(server: FakeHubServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a, **k):  # silence the default stderr spam
            pass

        # -- helpers -------------------------------------------------------

        def _qs(self) -> dict[str, list[str]]:
            return parse_qs(urlparse(self.path).query)

        def _send_json(self, obj: Any, status: int = 200) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_raw(self, body: bytes, status: int = 200, ctype: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _apply_failure_modes(self, qs: dict[str, list[str]]) -> bool:
            """Return True when the request was fully handled (as a failure)."""
            # Global override wins over (and merges with) query switches.
            qs = {**{k: [v] for k, v in server.force.items()}, **qs}
            delay = qs.get("delay", [""])[0]
            if delay:
                with contextlib.suppress(ValueError):
                    time.sleep(float(delay))

            if qs.get("cut", [""])[0]:
                body = b'{"partial": '  # deliberately truncated JSON
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body) + 100))
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True
                return True

            auth = qs.get("auth", [""])[0]
            if auth == "1":
                self._send_json({"error": "unauthorized"}, status=401)
                return True
            if auth == "2":
                self._send_json({"error": "forbidden"}, status=403)
                return True

            rl = qs.get("rl", [""])[0]
            if rl == "1":
                body = json.dumps({"error": "slow down"}).encode()
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", "1")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return True
            if rl == "2":
                self._send_json({"error": "slow down"}, status=429)
                return True

            boom = qs.get("boom", [""])[0]
            if boom == "1":
                self._send_json({"error": "server error"}, status=500)
                return True
            if boom == "2":
                self._send_json({"error": "unavailable"}, status=503)
                return True

            bad = qs.get("bad", [""])[0]
            if bad == "1":
                self._send_raw(b"<html>not json</html>", status=200, ctype="application/json")
                return True
            return False

        # -- routes --------------------------------------------------------

        def do_GET(self) -> None:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self._route("GET")

        def do_PUT(self) -> None:
            try:
                # Drain the request body so keep-alive framing stays valid.
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length:
                    self.rfile.read(length)
                self._route("PUT")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _route(self, method: str) -> None:
            server.requests.append(f"{method} {self.path}")
            parsed = urlparse(self.path)
            path = parsed.path
            # Merge the global override into the effective query so that BOTH
            # the failure-mode gate and the route handlers observe it (the
            # adapters append paths onto the base URL, so a ``?`` in the base
            # would corrupt the path — the override is the only way to inject
            # switches like ``bad=2`` / ``bad=3`` that live inside a handler).
            qs = {**{k: [v] for k, v in server.force.items()}, **self._qs()}

            if self._apply_failure_modes(qs):
                return

            # --- ModelScope style -----------------------------------------
            if path.startswith("/api/v1/models"):
                rest = path[len("/api/v1/models"):].strip("/")
                if not rest:
                    self._modelscope_search(qs)
                    return
                if rest.endswith("/repo/files"):
                    self._modelscope_files(qs)
                    return
                self._modelscope_detail(qs)
                return

            # --- HuggingFace / generic API style --------------------------
            if path.endswith("/tree/main"):  # /api/models/{repo}/tree/main
                self._hf_tree(qs)
                return
            rest_after = path[len("/api/models"):].strip("/") if path.startswith("/api/models") else ""
            if rest_after:
                # /api/models/{repo}  (detail; repo contains a slash)
                self._hf_detail(qs)
                return
            if path.startswith("/api/models"):
                self._hf_search(qs)
                return

            # --- generic file download (Range aware) -----------------------
            self._serve_file(qs)

        # -- HF ------------------------------------------------------------

        def _hf_search(self, qs: dict[str, list[str]]) -> None:
            n = _count(qs, len(server.hf_items))
            items = server.hf_items[:n]
            if qs.get("bad", [""])[0] == "3":
                items = [{k: v for k, v in it.items()
                          if k not in {"downloads", "likes", "tags"}} for it in items]
            body = json.dumps(items).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if items:
                self.send_header("Link", '<http://x/api/models?cursor=abc>; rel="next"')
            self.end_headers()
            self.wfile.write(body)

        def _hf_detail(self, qs: dict[str, list[str]]) -> None:
            self._send_json(server.hf_items[0] if server.hf_items else {})

        def _hf_tree(self, qs: dict[str, list[str]]) -> None:
            self._send_json(server.hf_files)

        # -- ModelScope ----------------------------------------------------

        def _modelscope_search(self, qs: dict[str, list[str]]) -> None:
            n = _count(qs, len(server.ms_items))
            items = server.ms_items[:n]
            bad = qs.get("bad", [""])[0]
            if bad == "2":
                data: Any = {"Models": "oops", "TotalCount": n}   # string, not list
            elif bad == "4":
                data = {"Models": {"not": "a list"}, "TotalCount": n}
            else:
                data = {"Models": items, "TotalCount": n}
            self._send_json({"Code": 200, "Data": data})

        def _modelscope_detail(self, qs: dict[str, list[str]]) -> None:
            data = server.ms_items[0] if server.ms_items else {}
            self._send_json({"Code": 200, "Data": data})

        def _modelscope_files(self, qs: dict[str, list[str]]) -> None:
            files = server.ms_files
            if qs.get("bad", [""])[0] == "3":
                files = [{k: v for k, v in f.items() if k != "Size"} for f in files]
            self._send_json({"Code": 200, "Data": {"Files": files}})

        # -- file download -------------------------------------------------

        def _serve_file(self, qs: dict[str, list[str]]) -> None:
            payload = (b"kevrai-fake-payload-" * 4096)  # ~86 KB
            if qs.get("enospc", [""])[0]:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload[:1024])
                self.close_connection = True
                return
            range_hdr = self.headers.get("Range", "")
            if range_hdr.startswith("bytes=") and not qs.get("norange", [""])[0]:
                spec = range_hdr[len("bytes="):]
                try:
                    start = int(spec.split("-", 1)[0] or 0)
                except ValueError:
                    start = 0
                chunk = payload[start:]
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
                return
            self._send_raw(payload, status=200, ctype="application/octet-stream")

    return Handler


def _count(qs: dict[str, list[str]], default: int) -> int:
    if "n" in qs:
        try:
            return max(0, int(qs["n"][0]))
        except ValueError:
            return default
    return default


__all__ = ["FakeHubServer", "hf_item", "ms_item"]
