"""End-to-end checks for the specialized model routes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _client():
    import httpx

    from app import main as app_main

    transport = httpx.ASGITransport(app=app_main.app)
    headers = {"authorization": f"Bearer {os.environ['KEVRAI_SIDECAR_SECRET']}"}
    return httpx.AsyncClient(transport=transport, base_url="http://t", headers=headers)


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    from app import main as app_main
    from app.settings import Settings

    s = Settings()
    s.model_dir = str(tmp_path / "models")
    s.engine_dir = str(tmp_path / "engines")
    app_main.app.state.settings = s
    app_main.app.state.settings_path = str(tmp_path / "settings.json")
    yield s


async def test_special_routes_are_registered(_isolated):
    async with _client() as c:
        r = await c.get("/openapi.json")
    paths = r.json()["paths"]
    for path in (
        "/api/local/tts",
        "/api/local/superres",
        "/api/local/vision/faces",
        "/api/local/vision/detect",
        "/api/local/audio/chat",
        "/api/local/audio/diarize",
        "/api/local/decide",
    ):
        assert path in paths, path


async def test_availability_summary_has_no_unsupported(_isolated):
    async with _client() as c:
        r = await c.get("/api/availability/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["unsupported"] == 0
    assert body["counts"]["native"] + body["counts"]["external"] == body["total"]


async def test_tts_blank_text_is_a_400(_isolated):
    async with _client() as c:
        r = await c.post("/api/local/tts", json={"model": "m", "text": " "})
    assert r.status_code == 400, r.text


async def test_decide_without_labels_is_a_400(_isolated):
    async with _client() as c:
        r = await c.post("/api/local/decide", json={"model": "gliner2.5", "text": "x"})
    assert r.status_code == 400, r.text


async def test_diarize_without_audio_is_a_400(_isolated):
    async with _client() as c:
        r = await c.post("/api/local/audio/diarize", json={"model": "pyannote"})
    assert r.status_code == 400, r.text


async def test_superres_empty_upload_is_a_400(_isolated):
    async with _client() as c:
        r = await c.post(
            "/api/local/superres",
            data={"model": "/tmp/no-model"},
            files={"file": ("a.png", b"", "image/png")},
        )
    assert r.status_code == 400, r.text
