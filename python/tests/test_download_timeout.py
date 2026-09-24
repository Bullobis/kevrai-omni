"""Regression test for download_file() timeout safety.

Prior to the fix, ``httpx.Client(timeout=None)`` was used in
``app.importer.download_file``, meaning a wedged upstream (server accepts
the connection but stops sending bytes) would hang the UI forever.
Main now uses ``httpx.Timeout(600.0, connect=30.0)`` — finite on all axes.
This test pins that no ``timeout=None`` can creep back into the download path.
"""
from __future__ import annotations

import inspect
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import httpx

from app import importer


def test_download_source_has_no_none_timeout():
    """The download path must never pass timeout=None (would hang forever)."""
    src = inspect.getsource(importer)
    # Guard against the exact anti-pattern returning.
    assert "timeout=None" not in src, (
        "timeout=None found in app.importer — a wedged upstream would hang "
        "the UI forever. Use httpx.Timeout with finite connect/read values."
    )


def test_download_uses_httpx_timeout():
    """download_file must construct an httpx.Timeout (finite on all axes)."""
    src = inspect.getsource(importer)
    assert "httpx.Timeout(" in src, (
        "app.importer should use httpx.Timeout(...) for downloads, not a "
        "bare float or None."
    )


def test_importer_timeout_values_are_finite():
    """Parse the httpx.Timeout(...) call and assert all axes are finite."""
    src = inspect.getsource(importer)
    # Find the httpx.Timeout(...) construction in download_file.
    import re
    m = re.search(r"httpx\.Timeout\(([^)]+)\)", src)
    assert m is not None, "httpx.Timeout(...) construction not found"
    args = m.group(1)
    # The first positional arg is read timeout; connect= is keyword.
    read_match = re.match(r"\s*([\d.]+)", args)
    assert read_match is not None, f"cannot parse read timeout from: {args}"
    read_val = float(read_match.group(1))
    assert read_val > 0, f"read timeout must be positive, got {read_val}"
    assert read_val < 3600, f"read timeout must be < 1h, got {read_val}"
    connect_match = re.search(r"connect\s*=\s*([\d.]+)", args)
    assert connect_match is not None, "connect= timeout missing"
    connect_val = float(connect_match.group(1))
    assert connect_val > 0, f"connect timeout must be positive, got {connect_val}"
