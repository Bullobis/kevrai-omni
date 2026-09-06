from __future__ import annotations

import pathlib

# Regression test for the download_file() timeout change.
#
# Prior to this fix, ``httpx.Client(timeout=None)`` was used in
# ``app.importer.download_file``, meaning a wedged upstream (server accepts
# the connection but stops sending bytes) would hang the UI forever.
# We now use a layered ``httpx.Timeout`` (see ``DOWNLOAD_TIMEOUT`` in
# ``app.importer``).
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.importer import DOWNLOAD_TIMEOUT


def test_download_timeout_is_finite():
    """Read/connect/pool/write timeouts must all be finite (not None)."""
    for name in ("connect", "read", "write", "pool"):
        value = getattr(DOWNLOAD_TIMEOUT, name)
        assert value is not None, f"{name} timeout must not be None"
        assert value > 0, f"{name} timeout must be positive"


def test_download_timeout_read_under_one_hour():
    # Read timeout guards against a wedged upstream. Anything up to an hour
    # is acceptable; a hard ceiling ensures download_file cannot hang forever.
    assert DOWNLOAD_TIMEOUT.read < 3600


def test_download_timeout_is_httpx_timeout():
    """Sanity: DOWNLOAD_TIMEOUT is an httpx.Timeout, not a bare float."""
    import httpx
    assert isinstance(DOWNLOAD_TIMEOUT, httpx.Timeout)
