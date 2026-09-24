"""Regression tests for importer.download_file timeout and allowlist behaviour.

Guards against the historical bug where `httpx.Client(timeout=None)` was used,
which could hang indefinitely on a stalled connection (bandit S113).  The fix
uses `httpx.Timeout(600.0, connect=30.0)`.

Note: `httpx` is imported *locally* inside `download_file`, so we patch the
global `httpx.Client` rather than `app.importer.httpx`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app import importer


def _make_mock_client() -> MagicMock:
    """Build a mock httpx.Client that supports the `with` + stream protocol."""
    mock_response = MagicMock()
    mock_response.headers = {"Content-Length": "0"}
    mock_response.iter_bytes.return_value = []
    mock_response.raise_for_status = MagicMock()

    mock_stream_cm = MagicMock()
    mock_stream_cm.__enter__ = MagicMock(return_value=mock_response)
    mock_stream_cm.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.stream.return_value = mock_stream_cm
    return mock_client


class TestDownloadFileTimeout:
    """Verify the client is constructed with an explicit, non-None timeout."""

    @patch("httpx.Client")
    def test_client_uses_explicit_timeout(self, mock_client_cls, tmp_path):
        mock_client_cls.return_value = _make_mock_client()

        target = tmp_path / "out.bin"
        result = importer.download_file(
            "https://huggingface.co/repo/resolve/main/f.bin", target
        )

        assert result is True
        call_kwargs = mock_client_cls.call_args.kwargs
        timeout = call_kwargs.get("timeout")
        assert timeout is not None, "timeout must not be None (S113 regression)"
        # The real httpx.Timeout is constructed inside the function.
        assert hasattr(timeout, "connect"), "timeout should be httpx.Timeout"
        assert timeout.connect == 30.0
        assert timeout.read == 600.0

    @patch("httpx.Client")
    def test_follow_redirects_enabled(self, mock_client_cls, tmp_path):
        mock_client_cls.return_value = _make_mock_client()

        target = tmp_path / "out.bin"
        importer.download_file("https://huggingface.co/x/y/resolve/main/f", target)

        assert mock_client_cls.call_args.kwargs.get("follow_redirects") is True


class TestDownloadFileAllowlist:
    def test_enforce_allowlist_rejects_unknown_host(self):
        with pytest.raises(ValueError, match="non-allowlisted host"):
            importer.download_file(
                "https://evil.example.com/malware.bin",
                Path("/tmp/should_not_exist_k_cortex.bin"),
                enforce_allowlist=True,
            )

    @patch("httpx.Client")
    def test_enforce_allowlist_accepts_huggingface(self, mock_client_cls, tmp_path):
        mock_client_cls.return_value = _make_mock_client()
        target = tmp_path / "ok.bin"
        # No ValueError from allowlist gate.
        importer.download_file(
            "https://huggingface.co/repo/resolve/main/f.bin",
            target,
            enforce_allowlist=True,
        )


class TestDownloadFileResume:
    @patch("httpx.Client")
    def test_range_header_for_existing_partial(self, mock_client_cls, tmp_path):
        target = tmp_path / "partial.bin"
        target.write_bytes(b"x" * 100)

        mock_client_cls.return_value = _make_mock_client()

        importer.download_file("https://huggingface.co/r/resolve/main/f.bin", target)

        mock_client = mock_client_cls.return_value
        stream_call = mock_client.stream.call_args
        # headers is passed as keyword arg.
        headers = stream_call.kwargs.get("headers", {})
        assert headers.get("Range") == "bytes=100-"

    @patch("httpx.Client")
    def test_no_range_header_for_fresh_file(self, mock_client_cls, tmp_path):
        target = tmp_path / "fresh.bin"
        mock_client_cls.return_value = _make_mock_client()

        importer.download_file("https://huggingface.co/r/resolve/main/f.bin", target)

        mock_client = mock_client_cls.return_value
        headers = mock_client.stream.call_args.kwargs.get("headers", {})
        assert "Range" not in headers
