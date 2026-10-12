"""Tests for DOCX / PPTX embedded-image OCR (app.knowledge_base).

Minimal OOXML zips are built with the stdlib (no python-docx / python-pptx).
A deterministic fake OCR callable is injected so no RapidOCR install is
required. Covers:

* inline image OCR text appears at the correct paragraph / slide position;
* rels resolution for both ``word/_rels`` and ``ppt/slides/_rels`` shapes;
* non-bitmap extensions (``.wmf``) are skipped;
* a missing media part, a per-image OCR failure, or a missing OCR engine
  degrades gracefully (text is still extracted, no exception propagates);
* legacy plain-text documents (no ``word/_rels/document.xml.rels``) still
  extract unchanged.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import knowledge_base as kb  # noqa: E402

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
PKG_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _docx(paragraphs: list[tuple[str, list[str]]],
          rels: dict[str, str] | None = None,
          media: dict[str, bytes] | None = None) -> bytes:
    """Build a .docx.

    ``paragraphs`` is a list of ``(text, [embed_rids])`` — embed_rids is the
    list of ``<a:blip r:embed>`` rIds rendered inside that paragraph.
    """
    body_parts: list[str] = []
    for text, embeds in paragraphs:
        inner = ""
        if text:
            inner += f"<w:r><w:t>{text}</w:t></w:r>"
        for rid in embeds:
            inner += (
                '<w:r><w:drawing>'
                f'<a:blip xmlns:a="{A_NS}" xmlns:r="{R_NS}" r:embed="{rid}"/>'
                '</w:drawing></w:r>'
            )
        body_parts.append(f"<w:p>{inner}</w:p>")
    doc_xml = (
        f'<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}" xmlns:a="{A_NS}" xmlns:r="{R_NS}">'
        f'<w:body>{"".join(body_parts)}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc_xml)
        if rels is not None:
            rels_xml = (
                f'<Relationships xmlns="{PKG_RELS_NS}">'
                + "".join(
                    f'<Relationship Id="{rid}" Type="x" Target="{target}"/>'
                    for rid, target in rels.items()
                )
                + "</Relationships>"
            )
            z.writestr("word/_rels/document.xml.rels", rels_xml)
        for name, data in (media or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def _pptx(slides: list[tuple[list[str], list[str]]],
          slide_rels: list[dict[str, str]] | None = None,
          media: dict[str, bytes] | None = None) -> bytes:
    """Build a .pptx.

    ``slides`` is a list of ``(text_runs, [embed_rids])``. ``slide_rels`` is a
    list (parallel to slides) of ``{rId: target}`` dicts.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for idx, (runs, embeds) in enumerate(slides, start=1):
            sps: list[str] = []
            for r in runs:
                sps.append(
                    f'<p:sp><p:txBody><a:r><a:t>{r}</a:t></a:r></p:txBody></p:sp>'
                )
            for rid in embeds:
                sps.append(
                    '<p:pic><p:blipFill>'
                    f'<a:blip xmlns:a="{A_NS}" xmlns:r="{R_NS}" r:embed="{rid}"/>'
                    '</p:blipFill></p:pic>'
                )
            slide_xml = (
                f'<p:sld xmlns:p="{P_NS}" xmlns:a="{A_NS}" xmlns:r="{R_NS}">'
                f'<p:cSld><spTree>{"".join(sps)}</spTree></p:cSld></p:sld>'
            )
            z.writestr(f"ppt/slides/slide{idx}.xml", slide_xml)
            rels = (slide_rels or [{}] * len(slides))[idx - 1]
            if rels:
                rels_xml = (
                    f'<Relationships xmlns="{PKG_RELS_NS}">'
                    + "".join(
                        f'<Relationship Id="{rid}" Type="x" Target="{target}"/>'
                        for rid, target in rels.items()
                    )
                    + "</Relationships>"
                )
                z.writestr(f"ppt/slides/_rels/slide{idx}.xml.rels", rels_xml)
        for name, data in (media or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


class FakeOCR:
    """Deterministic OCR: echoes the incoming image bytes length."""

    def __init__(self, text: str = "RECOGNISED") -> None:
        self.text = text
        self.calls: list[bytes] = []

    def __call__(self, img_bytes: bytes) -> str:
        self.calls.append(img_bytes)
        return f"{self.text}#{len(img_bytes)}"


class BoomOCR:
    def __call__(self, img_bytes: bytes) -> str:  # noqa: ARG002
        raise RuntimeError("ocr engine exploded")


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def test_docx_inline_image_ocr_inserted_at_paragraph_position():
    data = _docx(
        paragraphs=[
            ("Top text", []),
            ("", ["rId1"]),
            ("After image", []),
        ],
        rels={"rId1": "media/fig.png"},
        media={"word/media/fig.png": b"\x89PNGDATA"},
    )
    ocr = FakeOCR("FIG")
    img_bytes = b"\x89PNGDATA"
    text = kb.extract_docx_bytes(data, ocr_callable=ocr)
    # Image OCR text must sit between the surrounding paragraphs.
    assert text == (
        "Top text\n"
        f"{kb._IMAGE_MARK}FIG#{len(img_bytes)}\n"
        "After image"
    )
    assert ocr.calls == [img_bytes]


def test_docx_no_rels_no_ocr_callable_unchanged():
    # Legacy document without embedded images — must still extract.
    data = _docx(paragraphs=[("Hello", []), ("World", [])])
    assert kb.extract_docx_bytes(data) == "Hello\nWorld"
    assert kb.extract_docx_bytes(data, ocr_callable=FakeOCR()) == "Hello\nWorld"


def test_docx_non_bitmap_extensions_are_skipped():
    data = _docx(
        paragraphs=[("text", ["rId1"])],
        rels={"rId1": "media/chart.wmf"},
        # wmf part present on disk but not in _OCR_IMAGE_EXTS.
        media={"word/media/chart.wmf": b"WMFDATA"},
    )
    ocr = FakeOCR()
    text = kb.extract_docx_bytes(data, ocr_callable=ocr)
    assert text == "text"
    assert ocr.calls == []


def test_docx_missing_media_part_degrades_gracefully():
    data = _docx(
        paragraphs=[("text", ["rId1", "rId2"])],
        # rId1 exists, rId2 references a missing file.
        rels={"rId1": "media/ok.png", "rId2": "media/missing.png"},
        media={"word/media/ok.png": b"OK"},
    )
    ocr = FakeOCR()
    text = kb.extract_docx_bytes(data, ocr_callable=ocr)
    # Only the present image is OCR'd; the missing one is skipped.
    assert text == f"text\n{kb._IMAGE_MARK}RECOGNISED#2"
    assert ocr.calls == [b"OK"]


def test_docx_ocr_failure_does_not_abort_extraction():
    data = _docx(
        paragraphs=[("before", ["rId1"]), ("after", [])],
        rels={"rId1": "media/x.png"},
        media={"word/media/x.png": b"X"},
    )
    text = kb.extract_docx_bytes(data, ocr_callable=BoomOCR())
    assert text == "before\nafter"


def test_docx_none_ocr_callable_skips_images_silently():
    data = _docx(
        paragraphs=[("text", ["rId1"])],
        rels={"rId1": "media/x.png"},
        media={"word/media/x.png": b"X"},
    )
    assert kb.extract_docx_bytes(data, ocr_callable=None) == "text"


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------

def test_pptx_per_slide_image_ocr_appended_to_slide():
    data = _pptx(
        slides=[
            (["Slide A"], ["rId1"]),
            (["Slide B"], []),
        ],
        slide_rels=[{"rId1": "../media/a.png"}, {}],
        media={"ppt/media/a.png": b"PNG-A"},
    )
    ocr = FakeOCR("SLIDE")
    text = kb.extract_pptx_bytes(data, ocr_callable=ocr)
    assert text == (
        f"Slide A\n{kb._IMAGE_MARK}SLIDE#{len(b'PNG-A')}\n"
        "Slide B"
    )
    assert ocr.calls == [b"PNG-A"]


def test_pptx_slide_without_image_unchanged():
    data = _pptx(slides=[(["only text", "runs"], [])])
    assert kb.extract_pptx_bytes(data, ocr_callable=FakeOCR()) == "only textruns"


def test_pptx_ocr_failure_degrades():
    data = _pptx(
        slides=[(["title", "subtitle"], ["rId1"])],
        slide_rels=[{"rId1": "../media/x.png"}],
        media={"ppt/media/x.png": b"X"},
    )
    text = kb.extract_pptx_bytes(data, ocr_callable=BoomOCR())
    assert text == "titlesubtitle"


def test_pptx_non_bitmap_extension_skipped():
    data = _pptx(
        slides=[(["title"], ["rId1"])],
        slide_rels=[{"rId1": "../media/chart.emf"}],
        media={"ppt/media/chart.emf": b"EMF"},
    )
    ocr = FakeOCR()
    text = kb.extract_pptx_bytes(data, ocr_callable=ocr)
    assert text == "title"
    assert ocr.calls == []


def test_pptx_duplicate_blip_in_same_slide_ocr_once():
    data = _pptx(
        slides=[(["title"], ["rId1", "rId1"])],  # same rId twice
        slide_rels=[{"rId1": "../media/a.png"}],
        media={"ppt/media/a.png": b"A"},
    )
    ocr = FakeOCR()
    text = kb.extract_pptx_bytes(data, ocr_callable=ocr)
    # Only one OCR result (deduplicated by rId per slide).
    assert text == f"title\n{kb._IMAGE_MARK}RECOGNISED#1"
    assert ocr.calls == [b"A"]
