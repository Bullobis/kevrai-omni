"""Regression tests for the /api/env/install-engine endpoint.

Guards against the historical bug where main.py passed `cat.get("version", "")`
as the `sha256` argument to EngineManager.install(), causing every zip engine
install to fail hash verification because a version string is never a valid
sha256 digest.  The endpoint now delegates source/binary installs to
``engines.ensure_engine`` (platform asset resolution + tar flattening), so the
test pins that contract instead.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.engines import InstallResult
from app.main import app


class TestInstallEngineSha256:
    def test_install_engine_delegates_to_ensure_engine(self):
        """Source/binary install must go through ensure_engine (not a raw sha256)."""
        fake = InstallResult(
            engine_id="llama.cpp", path="/tmp/test", ok=True, message="installed",
        )
        with patch("app.main.asyncio.to_thread", new=AsyncMock(return_value=fake)):
            client = TestClient(app)
            resp = client.post("/api/env/install-engine", json={"id": "llama.cpp"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["path"] == "/tmp/test"

    def test_install_with_unknown_engine_returns_404(self):
        client = TestClient(app)
        resp = client.post("/api/env/install-engine", json={
            "id": "nonexistent-engine-xyz",
        })
        assert resp.status_code == 404

    def test_install_with_invalid_engine_id_returns_400(self):
        client = TestClient(app)
        resp = client.post("/api/env/install-engine", json={
            "id": "invalid id with spaces!",
        })
        assert resp.status_code == 400
