"""Tests for the OCR runtime (app.ocr_runtime) and image ingest route.

A fake ``rapidocr`` module is injected into ``sys.modules`` for deterministic
tests (no onnxruntime / model download). A real PNG is produced with Pillow.
"""
from __future__ import annotations

import base64
import io
import math
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ocr_runtime  # noqa: E402


def _png_bytes() -> bytes:
    from PIL import Image

    img = Image.new("RGB",(200, 80), "white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture()
def fake_rapidocr(monkeypatch):
    """Install a fake rapidocr module whose engine returns configurable txts."""
    holder = {"txts": ("Hello line 1", "第二行", "")}

    result_holder = holder

    class _Result:
        @property
        def txts(self):
            return result_holder["txts"]

    class _Engine:
        def __call__(self, _arr):
            return _Result()

    mod = types.ModuleType("rapidocr")
    mod.RapidOCR = _Engine
    monkeypatch.setitem(sys.modules, "rapidocr", mod)
    return holder


def test_extract_joins_nonempty_lines(fake_rapidocr):
    m = ocr_runtime.OCRManager()
    assert m.extract(_png_bytes()) == "Hello line 1\n第二行"


def test_extract_empty_when_no_text(fake_rapidocr):
    fake_rapidocr["txts"] = ()
    m = ocr_runtime.OCRManager()
    assert m.extract(_png_bytes()) == ""


def test_corrupt_image_raises():
    m = ocr_runtime.OCRManager()
    with pytest.raises(ocr_runtime.OCRModelError):
        m.extract(b"not an image")
    with pytest.raises(ocr_runtime.OCRParamError):
        m.extract(b"")


def test_missing_engine_raises(monkeypatch):
    # Ensure no rapidocr is importable and no engine dir exists.
    monkeypatch.delitem(sys.modules, "rapidocr", raising=False)
    m = ocr_runtime.OCRManager(data_root="/definitely-no-such-root-12345")
    with pytest.raises(ocr_runtime.OCREngineMissing):
        m.extract(_png_bytes())


# --------------------------------------------------------------------------
# Route
# --------------------------------------------------------------------------
class FakeEmbedder:
    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    def embed(self, repo: str, texts: list[str], **_kw) -> dict:
        vecs = []
        for t in texts:
            v = [0.0] * self.dim
            for ch in t:
                v[ord(ch) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            vecs.append([x / n for x in v])
        return {"dimensions": self.dim, "embeddings": vecs}


class _FakeOcr:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract(self, data: bytes) -> str:
        return self._text


@pytest.fixture()
def client(tmp_path, fake_rapidocr):
    from starlette.testclient import TestClient

    from app import knowledge_base, main

    main.app.state.knowledge = knowledge_base.KnowledgeBase(
        tmp_path, embedder=FakeEmbedder()
    )
    main.app.state.ocr = _FakeOcr("recognised black hole caption")
    return TestClient(main.app)


def test_route_image_ingest_and_search(client):
    r = client.post("/api/knowledge/kbs",
                    json={"name": "图片库", "model": "BAAI/bge-m3"})
    bid = r.json()["id"]
    b64 = base64.b64encode(_png_bytes()).decode()
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "scan.png",
                         "content_b64": b64, "encoding": "base64"})
    assert r.status_code == 200, r.text
    assert r.json()["chunk_count"] >= 1
    r = client.post(f"/api/knowledge/kbs/{bid}/search",
                    json={"query": "black hole"})
    assert r.json()["results"]


def test_route_image_no_text_is_400(tmp_path, fake_rapidocr):
    from starlette.testclient import TestClient

    from app import knowledge_base, main

    main.app.state.knowledge = knowledge_base.KnowledgeBase(
        tmp_path, embedder=FakeEmbedder()
    )
    main.app.state.ocr = _FakeOcr("")
    client = TestClient(main.app)
    r = client.post("/api/knowledge/kbs",
                    json={"name": "图片库 2", "model": "BAAI/BGE"})
    bid = r.json()["id"]
    b64 = base64.b64encode(_png_bytes()).decode()
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "blank.png",
                         "content_b64": b64, "encoding": "base64"})
    assert r.status_code == 400


def test_route_image_missing_engine_is_503(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from app import knowledge_base, main

    class _MissingOcr:
        def extract(self, _data):
            raise ocr_runtime.OCREngineMissing("please install RapidOCR")

    main.app.state.knowledge = knowledge_base.KnowledgeBase(
        tmp_path, embedder=FakeEmbedder()
    )
    main.app.state.ocr = _MissingOcr()
    client = TestClient(main.app)
    r = client.post("/api/knowledge/kbs",
                    json={"name": "图片库 3", "model": "BAAI/BGE"})
    bid = r.json()["id"]
    b64 = base64.b64encode(_png_bytes()).decode()
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "scan.jpg",
                         "content_b64": b64, "encoding": "base64"})
    assert r.status_code == 503
