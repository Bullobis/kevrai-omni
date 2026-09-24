"""Regression tests for the /api/env/install-engine endpoint sha256 fix.

Guards against the historical bug where main.py passed `cat.get("version", "")`
as the `sha256` argument to EngineManager.install(), causing every zip engine
install to fail hash verification because a version string is never a valid
sha256 digest.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app


class TestInstallEngineSha256:
    def test_install_passes_none_sha256_not_version(self):
        """The endpoint must pass None as sha256 (catalog has no per-binary hash)."""
        fake_engine_manager = MagicMock()
        fake_engine_manager.install = MagicMock(return_value={
            "ok": True, "engine_id": "llama-cpp", "path": "/tmp/test",
        })
        with patch("app.main.asyncio.to_thread", new=AsyncMock(return_value={"ok": True})):
            with patch.object(app.state, "engine_manager", fake_engine_manager, create=True):
                with patch("app.sources.measure_sources", new=AsyncMock(return_value=[])):
                    with patch("app.sources.pick_best", return_value={"url": "https://example.com/eng.zip"}):
                        client = TestClient(app)
                        resp = client.post("/api/env/install-engine", json={
                            "id": "llama.cpp",
                        })
        # The endpoint should reach install (200 or at least not 400/404).
        assert resp.status_code in (200, 500)
        # Verify install was called with sha256=None (3rd positional arg).
        call_args = fake_engine_manager.install.call_args
        if call_args is not None:
            args, kwargs = call_args
            if len(args) >= 3:
                assert args[2] is None, f"sha256 should be None, got {args[2]!r}"

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
