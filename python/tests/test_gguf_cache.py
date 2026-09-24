"""Tests for the short-lived GGUF file-listing cache."""

from __future__ import annotations

import httpx

from app import importer


class FakeResponse:
    def __init__(self, payload: list[dict[str, object]]) -> None:
        self._payload = payload
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict[str, object]]:
        return self._payload


class FakeClient:
    instantiations = 0
    get_calls = 0

    def __init__(self, *args: object, **kwargs: object) -> None:
        FakeClient.instantiations += 1

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str, params: object = None) -> FakeResponse:
        FakeClient.get_calls += 1
        return FakeResponse([
            {"type": "file", "path": "model-Q4.gguf", "size": 1234},
            {"type": "file", "path": "README.md", "size": 10},
        ])


def test_gguf_file_listing_is_cached(monkeypatch) -> None:
    FakeClient.instantiations = 0
    FakeClient.get_calls = 0
    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(
        importer,
        "_HF_API_MIRRORS",
        ("https://example.invalid/api",),
    )
    importer._GGUF_CACHE.clear()

    first = importer.list_gguf_files("example/cache-test", "*.gguf")
    first.append({"path": "mutated.gguf", "size": 0})
    second = importer.list_gguf_files("example/cache-test", "*.gguf")

    assert first[:1] == [{"path": "model-Q4.gguf", "size": 1234}]
    assert second == [{"path": "model-Q4.gguf", "size": 1234}]
    assert FakeClient.get_calls == 1
    assert FakeClient.instantiations == 1
