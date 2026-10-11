"""Tests for knowledge base .docx support (app.knowledge_base).

A minimal .docx (Office Open XML zip) is built with the stdlib (no network,
no python-docx). Covers text extraction across runs/paragraphs, the
missing-document.xml / non-zip / empty error paths, and the base64 document
route end-to-end.
"""
from __future__ import annotations

import base64
import io
import math
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import knowledge_base as kb  # noqa: E402

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_bytes(paragraphs: list[list[str]]) -> bytes:
    """Build a .docx zip; each paragraph is a list of text runs."""
    parts = [f'<?xml version="1.0"?><w:document xmlns:w="{W_NS}"><w:body>']
    for runs in paragraphs:
        parts.append("<w:p>")
        for run in runs:
            parts.append(f"<w:r><w:t>{run}</w:t></w:r>")
        parts.append("</w:p>")
    parts.append("</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "".join(parts))
        z.writestr("[Content_Types].xml", "<Types/>")
    return buf.getvalue()


def _zip_without_document() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hi")
    return buf.getvalue()


def test_extract_docx_joins_runs_and_paragraphs():
    data = _docx_bytes([["Hello ", "World"], ["第二段落 content"]])
    text = kb.extract_docx_bytes(data)
    assert text == "Hello World\n第二段落 content"


def test_docx_missing_document_xml_raises():
    with pytest.raises(kb.KnowledgeParamError) as exc:
        kb.extract_docx_bytes(_zip_without_document())
    assert "document.xml" in str(exc.value) or ".doc" in str(exc.value)


def test_corrupt_docx_raises_400_not_500():
    with pytest.raises(kb.KnowledgeParamError):
        kb.extract_docx_bytes(b"this is not a zip")
    with pytest.raises(kb.KnowledgeParamError):
        kb.extract_docx_bytes(b"")


def test_empty_text_docx_raises():
    # Paragraphs with no text runs.
    data = _docx_bytes([[""], [""]])
    with pytest.raises(kb.KnowledgeParamError):
        kb.extract_docx_bytes(data)


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


def test_route_docx_base64_ingest(client):
    r = client.post("/api/knowledge/kbs",
                    json={"name": "DOCX 库", "model": "BAAI/bge-m3"})
    bid = r.json()["id"]
    b64 = base64.b64encode(
        _docx_bytes([["Dense docx content about quasars and nebulae"]])
    ).decode()
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "notes.docx",
                         "content_b64": b64, "encoding": "base64"})
    assert r.status_code == 200, r.text
    assert r.json()["chunk_count"] >= 1
    r = client.post(f"/api/knowledge/kbs/{bid}/search",
                    json={"query": "quasars"})
    assert r.status_code == 200
    assert r.json()["results"]


def test_route_docx_missing_b64_is_400(client):
    r = client.post("/api/knowledge/kbs",
                    json={"name": "DOCX 库 2", "model": "BAAI/bge-m3"})
    bid = r.json()["id"]
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "notes.docx"})
    assert r.status_code == 400
