"""Tests for the embedding runtime (app.embedding_runtime).

No sentence-transformers/torch/GPU required: the engine is faked via a small
stub and injected through ``import_sentence_transformers``. Covers path
resolution, OpenAI response rendering, parameter validation, the embed happy
path, and the FastAPI route.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import embedding_runtime as emb  # noqa: E402

# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class _FakeTokenizer:
    def __call__(self, text, **_kw):
        return {"input_ids": [10] * (max(1, len(text) // 4) + 1)}


class _FakeModel:
    dim = 4

    def __init__(self, source, device="cpu", **kw):
        self.source = source
        self.device = device
        self.tokenizer = _FakeTokenizer()
        self.max_seq_length = 8192

    def encode(self, texts, normalize_embeddings=False):
        return [[0.1 * (j + 1)] * self.dim for j in range(len(texts))]

    def get_sentence_embedding_dimension(self):
        return self.dim


class _FakeST(types.ModuleType):
    __version__ = "6.1.0"
    SentenceTransformer = _FakeModel


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def test_engine_target_dir(tmp_path):
    p = emb.engine_target_dir(tmp_path)
    assert p == tmp_path / "engines" / "pip-sentence-transformers"


def test_capabilities_not_installed(tmp_path):
    cap = emb.capabilities(tmp_path)
    assert cap["installed"] is False
    assert cap["engine"] == "sentence-transformers"


def test_resolve_missing(tmp_path):
    out = emb.resolve_model_source("BAAI/bge-m3", tmp_path, tmp_path / "downloads")
    assert out == "BAAI/bge-m3"


def test_resolve_local(tmp_path):
    d = tmp_path / "downloads" / "hub" / "hf" / "BAAI__bge-m3"
    d.mkdir(parents=True)
    (d / "config.json").write_text("{}")
    (d / "model.safetensors").write_bytes(b"x")
    out = emb.resolve_model_source("BAAI/bge-m3", tmp_path, tmp_path / "downloads")
    assert out == d


def test_bootstrap_adds_path(tmp_path):
    target = tmp_path / "engines" / "pip-sentence-transformers"
    target.mkdir(parents=True)
    emb._bootstrap_sys_path(tmp_path)
    assert str(target) in sys.path


# --------------------------------------------------------------------------
# OpenAI response rendering
# --------------------------------------------------------------------------


def _result():
    return {
        "model": "BAAI/bge-m3",
        "dimensions": 4,
        "embeddings": [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]],
        "prompt_tokens": 7,
    }


def test_render_openai_shape():
    payload = emb.render_openai(_result())
    assert payload["object"] == "list"
    assert len(payload["data"]) == 2
    assert payload["data"][0]["index"] == 0
    assert payload["data"][1]["embedding"][0] == 0.5
    assert payload["usage"]["prompt_tokens"] == 7
    assert payload["usage"]["total_tokens"] == 7
    assert payload["model"] == "BAAI/bge-m3"


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _mgr(monkeypatch, tmp_path):
    mgr = emb.EmbeddingManager(tmp_path)
    monkeypatch.setattr(emb, "import_sentence_transformers",
                        lambda data_root=None: _FakeST("st"))
    return mgr


def test_bad_repo(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(emb.EmbeddingParamError):
        mgr.embed("no-slug", ["x"])


def test_empty_input(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(emb.EmbeddingParamError):
        mgr.embed("BAAI/bge-m3", [])


def test_input_not_list(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(emb.EmbeddingParamError):
        mgr.embed("BAAI/bge-m3", "x")


def test_batch_too_large(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(emb.EmbeddingParamError):
        mgr.embed("BAAI/bge-m3", ["x"] * (emb._MAX_BATCH + 1))


def test_blank_string_entry(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    with pytest.raises(emb.EmbeddingParamError):
        mgr.embed("BAAI/bge-m3", ["ok", "   "])


# --------------------------------------------------------------------------
# Happy path + failures
# --------------------------------------------------------------------------


def test_embed_single(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    out = mgr.embed("BAAI/bge-m3", ["hello world"])
    assert out["dimensions"] == 4
    assert len(out["embeddings"]) == 1
    assert len(out["embeddings"][0]) == 4
    assert out["prompt_tokens"] >= 1
    assert out["device"] == "cpu"


def test_embed_multiple_cached(monkeypatch, tmp_path):
    mgr = _mgr(monkeypatch, tmp_path)
    out1 = mgr.embed("BAAI/bge-m3", ["a"])
    out2 = mgr.embed("BAAI/bge-m3", ["b"])
    # same underlying model reused from cache
    assert out1["source"] == out2["source"]
    assert len(mgr._cache) == 1


def test_model_load_failure(monkeypatch, tmp_path):
    class Boom(_FakeST):
        def SentenceTransformer(self, *a, **kw):
            raise RuntimeError("native crash")

    mgr = emb.EmbeddingManager(tmp_path)
    monkeypatch.setattr(emb, "import_sentence_transformers",
                        lambda data_root=None: Boom("st"))
    with pytest.raises(emb.EmbeddingModelError):
        mgr.embed("BAAI/bge-m3", ["x"])


def test_encode_failure(monkeypatch, tmp_path):
    class BoomModel(_FakeModel):
        def encode(self, texts, **kw):
            raise RuntimeError("encode boom")

    boom = _FakeST("st")
    boom.SentenceTransformer = BoomModel
    mgr = emb.EmbeddingManager(tmp_path)
    monkeypatch.setattr(emb, "import_sentence_transformers",
                        lambda data_root=None: boom)
    with pytest.raises(emb.EmbeddingModelError):
        mgr.embed("BAAI/bge-m3", ["x"])


# --------------------------------------------------------------------------
# FastAPI route
# --------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from app import main

    monkeypatch.setattr(emb, "import_sentence_transformers",
                        lambda data_root=None: _FakeST("st"))
    main.app.state.emb = emb.EmbeddingManager(tmp_path)
    return TestClient(main.app)


def test_route_capabilities(client):
    r = client.get("/api/embeddings/capabilities")
    assert r.status_code == 200 and r.json()["engine"] == "sentence-transformers"


def test_route_string_input(client):
    r = client.post("/v1/embeddings", json={"model": "BAAI/bge-m3", "input": "hello"})
    assert r.status_code == 200
    data = r.json()
    assert len(data["data"]) == 1 and len(data["data"][0]["embedding"]) == 4


def test_route_list_input(client):
    r = client.post("/v1/embeddings",
                    json={"model": "BAAI/bge-m3", "input": ["a", "b"]})
    assert r.status_code == 200 and len(r.json()["data"]) == 2


def test_route_base64(client):
    import base64
    import struct

    r = client.post("/v1/embeddings", json={
        "model": "BAAI/bge-m3", "input": "x", "encoding_format": "base64"})
    assert r.status_code == 200
    blob = r.json()["data"][0]["embedding"]
    raw = base64.b64decode(blob)
    assert len(raw) == struct.calcsize("4f")


def test_route_bad_encoding(client):
    r = client.post("/v1/embeddings", json={
        "model": "BAAI/bge-m3", "input": "x", "encoding_format": "hex"})
    assert r.status_code == 400


def test_route_dimensions_mismatch(client):
    r = client.post("/v1/embeddings", json={
        "model": "BAAI/bge-m3", "input": "x", "dimensions": 2})
    assert r.status_code == 400


def test_route_engine_missing(monkeypatch):
    from starlette.testclient import TestClient

    from app import main

    def boom(data_root=None):
        raise emb.EmbeddingEngineMissing("not installed")

    monkeypatch.setattr(emb, "import_sentence_transformers", boom)
    main.app.state.emb = emb.EmbeddingManager()
    r = TestClient(main.app).post(
        "/v1/embeddings", json={"model": "BAAI/bge-m3", "input": "x"})
    assert r.status_code == 503
