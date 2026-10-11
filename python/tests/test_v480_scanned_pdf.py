"""Tests for scanned-PDF OCR fallback (app.knowledge_base render + route).

The PyMuPDF engine and OCR are faked at the boundaries for deterministic,
network-free tests.
"""
from __future__ import annotations

import base64
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import knowledge_base as kb  # noqa: E402


def test_ocr_scanned_pdf_joins_pages(monkeypatch):
    # Fake renderer returns two PNGs; fake OCR maps each to a line.
    monkeypatch.setattr(kb, "render_pdf_pages",
                        lambda data, data_root=None, dpi=170: [b"png1", b"png2", b"empty"])
    mapping = {b"png1": "Page One Text", b"png2": "Page Two Text", b"empty": "   "}
    text = kb.ocr_scanned_pdf(b"pdf", lambda png: mapping[png])
    assert text == "Page One Text\nPage Two Text"


def test_render_pdf_pages_missing_engine(tmp_path, monkeypatch):
    # No engine dir and ensure pymupdf/fitz unimportable.
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    monkeypatch.setitem(sys.modules, "fitz", None)
    with pytest.raises(kb.KnowledgeEngineMissing):
        kb.render_pdf_pages(b"%PDF-fake", data_root=tmp_path)


def test_render_pdf_pages_empty_raises(tmp_path):
    with pytest.raises(kb.KnowledgeParamError):
        kb.render_pdf_pages(b"", data_root=tmp_path)


# --------------------------------------------------------------------------
# Route fallback
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
    def extract(self, png: bytes) -> str:
        return {b"png1": "Recovered scanned caption", b"png2": "Second scanned line"}.get(png, "")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from app import main

    main.app.state.knowledge = kb.KnowledgeBase(tmp_path, embedder=FakeEmbedder())
    main.app.state.ocr = _FakeOcr()
    # extract_pdf_bytes reports scanned; renderer returns two fake pages.
    monkeypatch.setattr(kb, "extract_pdf_bytes",
                        lambda data: (_ for _ in ()).throw(
                            kb.ScannedPdfError("no text")))
    monkeypatch.setattr(kb, "render_pdf_pages",
                        lambda data, data_root=None, dpi=170: [b"png1", b"png2"])
    return TestClient(main.app)


def test_route_scanned_pdf_is_ocr_indexed(client):
    r = client.post("/api/knowledge/kbs",
                    json={"name": "扫描库", "model": "BAAI/bge-m3"})
    bid = r.json()["id"]
    b64 = base64.b64encode(b"%PDF-image-only").decode()
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "scan.pdf",
                         "content_b64": b64, "encoding": "base64"})
    assert r.status_code == 200, r.text
    assert r.json()["chunk_count"] >= 1
    r = client.post(f"/api/knowledge/kbs/{bid}/search",
                    json={"query": "scanned caption"})
    assert r.json()["results"]


def test_route_scanned_pdf_no_text_400(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from app import main

    class _BlankOcr:
        def extract(self, _png):
            return ""

    main.app.state.knowledge = kb.KnowledgeBase(tmp_path, embedder=FakeEmbedder())
    main.app.state.ocr = _BlankOcr()
    monkeypatch.setattr(kb, "extract_pdf_bytes",
                        lambda data: (_ for _ in ()).throw(kb.ScannedPdfError("x")))
    monkeypatch.setattr(kb, "render_pdf_pages",
                        lambda data, data_root=None, dpi=170: [b"png1"])
    client = TestClient(main.app)
    r = client.post("/api/knowledge/kbs",
                    json={"name": "扫描库 2", "model": "BAAI/bge-m3"})
    bid = r.json()["id"]
    b64 = base64.b64encode(b"%PDF-blank").decode()
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "blank.pdf",
                         "content_b64": b64, "encoding": "base64"})
    assert r.status_code == 400
