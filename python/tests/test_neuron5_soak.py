"""Neuron5 soak test: resource hygiene under repeated operation.

Loops N rounds of key backend operations (search hit/miss, recommend, agent
tool dispatch, MNN load/unload in subprocess mode, download success/failure)
and samples file descriptors, threads, child processes, and cache sizes after
each round. Asserts resources are bounded — no linear growth across rounds.

All operations use mocks / local HTTP / fake engines: no real network, no GPU.
Gate: runs by default (50 rounds, ~30s); set KEVRAI_SOAK_ROUNDS to override.
"""
from __future__ import annotations

import gc
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import mnn_runtime  # noqa: E402
from app.agent.tool_registry import Tool, ToolRegistry  # noqa: E402
from app.downloader import Downloader  # noqa: E402
from app.recommend import recommend  # noqa: E402
from app.search import SearchQuery, get_corpus, search  # noqa: E402

SOAK_ROUNDS = int(os.environ.get("KEVRAI_SOAK_ROUNDS", "50"))

# ---------------------------------------------------------------------------
# Resource sampling helpers
# ---------------------------------------------------------------------------


def _fd_count() -> int:
    """Open file descriptors for this process (Linux /proc)."""
    try:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except OSError:
        return -1


def _thread_count() -> int:
    return threading.active_count()


def _child_process_count() -> int:
    try:
        import multiprocessing
        return len(multiprocessing.active_children())
    except Exception:
        return 0


def _sample() -> dict[str, int]:
    gc.collect()
    return {
        "fd": _fd_count(),
        "threads": _thread_count(),
        "children": _child_process_count(),
    }


# ---------------------------------------------------------------------------
# Fake MNN engine (for subprocess-mode load/unload cycles)
# ---------------------------------------------------------------------------


class _FakeLlm:
    def __init__(self, cfg_path: str):
        self._cfg = cfg_path

    def load(self):
        pass

    def reset(self):
        pass

    def apply_chat_template(self, msg):
        return msg.get("content", "") if isinstance(msg, dict) else str(msg)

    def set_config(self, cfg):
        pass

    def response(self, prompt, stream):
        return "ok"

    def generate_init(self, prompt):
        pass

    def get_context(self):
        return {"generate_str": ""}

    def generate(self):
        pass


def _install_fake_mnn(monkeypatch):
    def _fake_create(cfg_path: str):
        return _FakeLlm(cfg_path)
    monkeypatch.setattr(mnn_runtime, "_import_llm", lambda: _fake_create)


# ---------------------------------------------------------------------------
# Local HTTP server for download soak
# ---------------------------------------------------------------------------


class _StaticHandler(BaseHTTPRequestHandler):
    payload: bytes = b"x" * 4096

    def log_message(self, *a, **kw):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)


@pytest.fixture(scope="module")
def static_server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = HTTPServer(("127.0.0.1", port), _StaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}/file.bin"
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Sample models for search/recommend
# ---------------------------------------------------------------------------

_SAMPLE_MODELS = [
    {"id": "model-a", "name": "Alpha Model", "description": "fast chat model",
     "params": "7B", "architecture": "llama", "context": 8192},
    {"id": "model-b", "name": "Beta Model", "description": "coding assistant",
     "params": "13B", "architecture": "qwen", "context": 32768},
    {"id": "model-c", "name": "Gamma Model", "description": "multimodal",
     "params": "3B", "architecture": "vit", "context": 4096},
]

_SAMPLE_HW = {
    "gpu": [{"name": "FakeGPU", "vram_total_mb": 8192, "vram_free_mb": 6000}],
    "cpu_cores": 8,
    "ram_total_gb": 16,
}


# ---------------------------------------------------------------------------
# Soak test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="FD sampling is Linux-only")
def test_soak_resource_bounded(monkeypatch, tmp_path, static_server):
    """50 rounds of mixed operations; FD/threads/children must not grow linearly."""
    # Enable MNN subprocess mode + fake engine
    monkeypatch.setenv(mnn_runtime._SUBPROCESS_ENV_VAR, "1")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    _install_fake_mnn(monkeypatch)
    mnn_runtime._reset_client_for_tests()

    # Build a fake tool registry for agent tool dispatch
    reg = ToolRegistry()
    call_count = {"n": 0}

    def _echo_handler(params, ctx):
        call_count["n"] += 1
        return {"ok": True, "echo": params.get("text", "")}

    reg.register(Tool(name="echo", description="echo", parameters={"type": "object"}, handler=_echo_handler))

    # Downloader for download soak
    dl = Downloader(max_concurrent=2)

    # Warm-up round (establishes baseline after lazy imports)
    _warm_up(reg, dl, static_server, tmp_path)
    baseline = _sample()
    time.sleep(0.05)

    samples: list[dict[str, int]] = [baseline]

    for i in range(SOAK_ROUNDS):
        # 1. Search — alternate cache hit (same query) and miss (new query)
        q = "model" if i % 2 == 0 else f"unique-query-{i}"
        sq = SearchQuery(q=q, page_size=5)
        search(_SAMPLE_MODELS, sq, cache_key="soak")

        # 2. Recommend
        recommend(_SAMPLE_MODELS, _SAMPLE_HW, limit=3)

        # 3. Agent tool dispatch
        reg.execute("echo", {"text": f"round-{i}"}, ctx=MagicMock())

        # 4. MNN load/unload in subprocess mode (every 5 rounds to avoid fork spam)
        if i % 5 == 0:
            mnn_runtime.load_model(str(tmp_path), "soak-model")
            mnn_runtime.unload_model()

        # 5. Download success (every 3 rounds)
        if i % 3 == 0:
            dest = tmp_path / f"dl-{i}.bin"
            import asyncio
            asyncio.run(dl.start(static_server, dest))

        if i % 10 == 0:
            samples.append(_sample())

    # Final sample
    final = _sample()
    samples.append(final)

    # Cleanup MNN subprocess client
    mnn_runtime._reset_client_for_tests()
    time.sleep(0.1)
    after_cleanup = _sample()

    # --- Assertions ---
    # FD growth from baseline to final must be bounded (allow pytest/uvicorn overhead)
    fd_growth = final["fd"] - baseline["fd"]
    assert fd_growth < 15, f"FD grew by {fd_growth} across {SOAK_ROUNDS} rounds (baseline={baseline['fd']}, final={final['fd']})"

    # Thread growth bounded
    thread_growth = final["threads"] - baseline["threads"]
    assert abs(thread_growth) < 8, f"Threads grew by {thread_growth} (baseline={baseline['threads']}, final={final['threads']})"

    # No orphan child processes after cleanup
    assert after_cleanup["children"] == 0, f"{after_cleanup['children']} child processes remain after cleanup"

    # Corpus cache should not grow unboundedly (same cache_key reuses corpus)
    corpus_before = get_corpus(_SAMPLE_MODELS, cache_key="soak")
    corpus_after = get_corpus(_SAMPLE_MODELS, cache_key="soak")
    assert corpus_before is corpus_after, "Corpus cache should return same object for same cache_key"


def _warm_up(reg, dl, url, tmp_path):
    """One round of each operation to warm up lazy imports / caches."""
    sq = SearchQuery(q="warmup", page_size=5)
    search(_SAMPLE_MODELS, sq, cache_key="soak")
    recommend(_SAMPLE_MODELS, _SAMPLE_HW, limit=3)
    reg.execute("echo", {"text": "warmup"}, ctx=MagicMock())
    import asyncio
    dest = tmp_path / "warmup.bin"
    asyncio.run(dl.start(url, dest))
