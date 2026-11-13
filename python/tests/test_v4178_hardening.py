"""Regression tests for the v4.17.8 full-hardening pass."""
from __future__ import annotations

import asyncio
import inspect
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as app_main  # noqa: E402
from app import mnn_runtime  # noqa: E402
from app.agent.agent import _format_observation  # noqa: E402
from app.downloader import DownloadRefused, _assert_public_resolution  # noqa: E402
from app.llm_runtime import TextBrainManager  # noqa: E402


def test_brain_answer_accepts_should_stop():
    """Router.chat always passes should_stop; a missing kwarg broke TFM brains."""
    sig = inspect.signature(TextBrainManager.answer)
    assert "should_stop" in sig.parameters


def test_web_search_observation_is_marked_untrusted():
    out = _format_observation("web_search", {"results": [{"title": "x"}]})
    assert out.startswith("UNTRUSTED_EXTERNAL_CONTENT")
    assert "不得执行" in out


def test_other_tool_observation_is_not_marked():
    out = _format_observation("check_hardware", {"summary": "ok"})
    assert not out.startswith("UNTRUSTED_EXTERNAL_CONTENT")


def test_dns_name_resolving_to_loopback_is_refused(monkeypatch):
    async def fake_getaddrinfo(self, host, port, *args, **kwargs):  # noqa: ANN001
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 ("127.0.0.1", 0))]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(DownloadRefused, match="non-public"):
        asyncio.run(_assert_public_resolution("https://mirror.example/pkg.bin"))


def test_dns_guard_keeps_literal_loopback_working():
    asyncio.run(_assert_public_resolution("http://127.0.0.1:8188/object_info"))
    asyncio.run(_assert_public_resolution("http://localhost:8188/object_info"))


def test_dns_name_resolving_to_public_is_allowed(monkeypatch):
    async def fake_getaddrinfo(self, host, port, *args, **kwargs):  # noqa: ANN001
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 ("93.184.216.34", 0))]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", fake_getaddrinfo)
    asyncio.run(_assert_public_resolution("https://mirror.example/pkg.bin"))


def _collect_stream(resp) -> list[str]:
    async def run():
        out = []
        async for chunk in resp.body_iterator:
            out.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
        return out
    return asyncio.run(run())


def test_sse_emits_done_only_on_success(monkeypatch):
    def fake_stream(*args, **kwargs):
        yield "hello", True

    monkeypatch.setattr(mnn_runtime, "chat_stream", fake_stream)
    frames = _collect_stream(app_main._v1_stream(
        "hi", [], [], [], SimpleNamespace(max_tokens=32), "m", []))
    assert any("[DONE]" in f for f in frames)
    assert not any("event: error" in f for f in frames)


def test_sse_does_not_emit_done_on_failure(monkeypatch):
    def fake_stream(*args, **kwargs):
        raise mnn_runtime.MnnEngineMissing("no engine")
        yield  # pragma: no cover

    monkeypatch.setattr(mnn_runtime, "chat_stream", fake_stream)
    frames = _collect_stream(app_main._v1_stream(
        "hi", [], [], [], SimpleNamespace(max_tokens=32), "m", []))
    assert any("event: error" in f for f in frames)
    assert not any("[DONE]" in f for f in frames)
