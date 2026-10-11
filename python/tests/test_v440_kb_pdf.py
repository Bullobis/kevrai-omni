"""Tests for knowledge base PDF support (app.knowledge_base).

A small valid PDF with selectable text is built with pypdf itself (no network,
no reportlab). Covers text extraction, the scanned/empty-PDF hint, corrupt PDF
handling, and the base64 document route end-to-end.
"""
from __future__ import annotations

import base64
import io
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import knowledge_base as kb  # noqa: E402


def _pdf_bytes(text: str) -> bytes:
    """Build a valid single-page PDF whose content stream draws ``text``."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    page = w.pages[0]
    content = DecodedStreamObject()
    content.set_data(
        f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    )
    page[NameObject("/Contents")] = content
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _blank_pdf_bytes() -> bytes:
    """A valid PDF page with no text-drawing operator (scanned-like)."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    page = w.pages[0]
    content = DecodedStreamObject()
    content.set_data(b"BT ET")  # no Tj → no extractable text
    page[NameObject("/Contents")] = content
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject(
            {NameObject("/F1"): DictionaryObject({
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })}
        )}
    )
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_extract_pdf_text():
    data = _pdf_bytes("Hello PDF world")
    text = kb.extract_pdf_bytes(data)
    assert "Hello PDF world" in text


def test_scanned_pdf_raises_clear_hint():
    with pytest.raises(kb.KnowledgeParamError) as exc:
        kb.extract_pdf_bytes(_blank_pdf_bytes())
    assert "扫描" in str(exc.value) or "OCR" in str(exc.value)


def test_corrupt_pdf_raises_400_not_500():
    with pytest.raises(kb.KnowledgeParamError):
        kb.extract_pdf_bytes(b"this is definitely not a pdf")
    with pytest.raises(kb.KnowledgeParamError):
        kb.extract_pdf_bytes(b"")


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


@pytest.fixture()
def client(tmp_path):
    from starlette.testclient import TestClient

    from app import main

    main.app.state.knowledge = kb.KnowledgeBase(tmp_path, embedder=FakeEmbedder())
    return TestClient(main.app)


def test_route_pdf_base64_ingest(client):
    r = client.post("/api/knowledge/kbs",
                    json={"name": "PDF 库", "model": "BAAI/bge-m3"})
    bid = r.json()["id"]
    b64 = base64.b64encode(_pdf_bytes("Dense PDF content about neutrinos")).decode()
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "paper.pdf",
                         "content_b64": b64, "encoding": "base64"})
    assert r.status_code == 200, r.text
    assert r.json()["chunk_count"] >= 1
    # The extracted text is searchable.
    r = client.post(f"/api/knowledge/kbs/{bid}/search",
                    json={"query": "neutrinos"})
    assert r.status_code == 200
    assert r.json()["results"]


def test_route_pdf_missing_b64_is_400(client):
    r = client.post("/api/knowledge/kbs",
                    json={"name": "PDF 库 2", "model": "BAAI/bge-m3"})
    bid = r.json()["id"]
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "paper.pdf"})
    assert r.status_code == 400


def test_route_pdf_scanned_needs_ocr_engine(client, monkeypatch):
    # A scanned / textless PDF now triggers render+OCR; when the optional
    # PyMuPDF engine is absent the route returns 503 with install guidance
    # (rather than the older 400 dead-end). Rendering is forced to report the
    # missing engine so the test is independent of the real data root.
    from app import main

    main.app.state.ocr = None
    monkeypatch.setattr(
        kb, "render_pdf_pages",
        lambda data, data_root=None, dpi=170: (_ for _ in ()).throw(
            kb.KnowledgeEngineMissing("install PyMuPDF")))
    r = client.post("/api/knowledge/kbs",
                    json={"name": "PDF 库 3", "model": "BAAI/bge-m3"})
    bid = r.json()["id"]
    b64 = base64.b64encode(_blank_pdf_bytes()).decode()
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "scan.pdf",
                         "content_b64": b64, "encoding": "base64"})
    assert r.status_code == 503


def test_route_text_route_still_works(client):
    r = client.post("/api/knowledge/kbs",
                    json={"name": "文本库", "model": "BAAI/bge-m3"})
    bid = r.json()["id"]
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "note.md", "content": "plain markdown text"})
    assert r.status_code == 200
