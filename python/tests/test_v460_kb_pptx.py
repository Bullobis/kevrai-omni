"""Tests for knowledge base .pptx support (app.knowledge_base).

A minimal .pptx (Office Open XML presentation zip) is built with the stdlib
(no network, no python-pptx). Covers text extraction across runs/slides,
numeric slide ordering, the missing-slides / non-zip / empty error paths, and
the base64 document route end-to-end.
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

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _slide_xml(runs: list[str]) -> str:
    parts = [f'<p:sld xmlns:p="{P_NS}"><p:cSld><spTree>']
    for run in runs:
        parts.append(
            f'<p:sp><p:txBody><a:r xmlns:a="{A_NS}"><a:t>{run}</a:t></a:r>'
            "</p:txBody></p:sp>"
        )
    parts.append("</spTree></p:cSld></p:sld>")
    return "".join(parts)


def _pptx_bytes(slides: dict[int, list[str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, runs in slides.items():
            z.writestr(f"ppt/slides/slide{n}.xml", _slide_xml(runs))
        z.writestr("[Content_Types].xml", "<Types/>")
    return buf.getvalue()


def test_extract_pptx_orders_slides_numerically_and_joins_runs():
    # Write slide10 first; output must still read 1, 2, 10.
    data = _pptx_bytes({
        10: ["Tenth slide"],
        1: ["First slide ", "title"],
        2: ["第二页 content"],
    })
    text = kb.extract_pptx_bytes(data)
    assert text == "First slide title\n第二页 content\nTenth slide"


def test_pptx_missing_slides_raises():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "x")
    with pytest.raises(kb.KnowledgeParamError) as exc:
        kb.extract_pptx_bytes(buf.getvalue())
    assert "slides" in str(exc.value) or ".ppt" in str(exc.value)


def test_corrupt_pptx_raises_400_not_500():
    with pytest.raises(kb.KnowledgeParamError):
        kb.extract_pptx_bytes(b"not a zip")
    with pytest.raises(kb.KnowledgeParamError):
        kb.extract_pptx_bytes(b"")


def test_empty_text_pptx_raises():
    # Slide with no text shapes.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "ppt/slides/slide1.xml",
            f'<p:sld xmlns:p="{P_NS}"><p:cSld/></p:sld>',
        )
    with pytest.raises(kb.KnowledgeParamError):
        kb.extract_pptx_bytes(buf.getvalue())


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


def test_route_pptx_base64_ingest(client):
    r = client.post("/api/knowledge/kbs",
                    json={"name": "PPTX 库", "model": "BAAI/bge-m3"})
    bid = r.json()["id"]
    b64 = base64.b64encode(
        _pptx_bytes({1: ["Dense slide about black holes and pulsars"]})
    ).decode()
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "deck.pptx",
                         "content_b64": b64, "encoding": "base64"})
    assert r.status_code == 200, r.text
    assert r.json()["chunk_count"] >= 1
    r = client.post(f"/api/knowledge/kbs/{bid}/search",
                    json={"query": "black holes"})
    assert r.status_code == 200
    assert r.json()["results"]


def test_route_pptx_missing_b64_is_400(client):
    r = client.post("/api/knowledge/kbs",
                    json={"name": "PPTX 库 2", "model": "BAAI/bge-m3"})
    bid = r.json()["id"]
    r = client.post(f"/api/knowledge/kbs/{bid}/documents",
                   json={"filename": "deck.pptx"})
    assert r.status_code == 400
