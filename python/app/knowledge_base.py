"""Local knowledge base + retrieval-augmented generation (RAG) core.

A deliberately dependency-light module: it reuses the already-available
:class:`app.embedding_runtime.EmbeddingManager` to turn documents into dense
vectors, stores everything as plain JSON under ``<data_root>/knowledge/<kb_id>/``
and performs similarity search with a pure-Python cosine similarity. No
``chromadb`` / ``faiss`` / ``langchain`` is pulled in.

Design
------
* A *knowledge base* (KB) is a folder containing:

  - ``meta.json``  — id, display name, embedding model, chunking parameters;
  - ``docs.json``  — ingested documents (metadata only);
  - ``chunks.json``— every stored text chunk plus its embedding vector.

* Documents are ingested from text content (``.txt`` / ``.md`` verbatim;
  ``.json`` and ``.csv`` are flattened to their textual values). They are split
  into paragraph-aware, overlap-joining chunks, embedded in one batch and
  persisted. Retrieval embeds the query once and ranks chunks by cosine.

* The embedding engine is *injected*. Production passes the shared
  ``EmbeddingManager``; tests pass a deterministic fake so no model download is
  needed. The contract is: ``embedder.embed(repo, texts) -> {"dimensions": int,
  "embeddings": list[list[float]]}``.

* Asking a question returns the assembled context; an optional text brain can be
  supplied by the route layer to produce a grounded answer. When no brain is
  configured the endpoint still returns the retrieved context rather than
  erroring.
"""
from __future__ import annotations

import io
import json
import logging
import math
import re
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .settings import default_data_root

log = logging.getLogger("kevrai.knowledge")

# ---------------------------------------------------------------------------
# Constants & errors
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".csv", ".pdf",
                        ".docx", ".pptx"}
DEFAULT_CHUNK_SIZE = 600
DEFAULT_CHUNK_OVERLAP = 80
DEFAULT_TOP_K = 4
MAX_CHUNK_SIZE = 4000
MAX_OVERLAP_RATIO = 0.5  # overlap must stay below half the chunk size
MAX_CONTENT_BYTES = 8 * 1024 * 1024  # 8 MiB of raw text per document
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def clean_brain_answer(raw: str) -> str:
    """Trim a raw grounded-answer completion.

    Small brains decoded in raw-continuation mode often emit the correct
    answer first and then loop (repeating an ``答案：`` anchor or identical
    lines). Keep the text up to the first repeated line / second anchor.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    # A re-occurring answer anchor means the model started a new "answer".
    parts = re.split(r"\n?答案[:：]", text)
    text = parts[0].strip() if parts and parts[0].strip() else text
    seen: set[str] = set()
    kept: list[str] = []
    for line in text.splitlines():
        key = line.strip()
        if not key:
            kept.append(line)
            continue
        if key in seen:  # first repeated non-empty line → stop here
            break
        seen.add(key)
        kept.append(line)
    return "\n".join(kept).strip()


class KnowledgeError(Exception):
    """Base error for the knowledge base runtime."""


class KnowledgeNotFound(KnowledgeError):
    """Raised when a KB / document id does not exist."""


class KnowledgeParamError(KnowledgeError):
    """Raised for invalid parameters / input."""


class KnowledgeEngineMissing(KnowledgeError):
    """Raised when the embedding engine is not installed/loadable."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _now() -> float:
    return time.time()


def _valid_id(value: str) -> bool:
    return bool(value) and bool(_ID_RE.match(str(value)))


def _parse_json_text(content: str) -> str:
    """Flatten a JSON document into newline-joined textual leaf values."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        # Fall back to the raw text — it is still useful, just not structured.
        return content

    leaves: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if node.strip():
                leaves.append(node.strip())
        elif isinstance(node, (int, float, bool)):
            leaves.append(str(node))
        elif isinstance(node, dict):
            for key, val in node.items():
                walk(f"{key}: {val}" if not isinstance(val, (dict, list)) else val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return "\n".join(leaves)


def _parse_csv_text(content: str) -> str:
    """Join CSV cells row-by-row into readable text."""
    lines: list[str] = []
    for raw in content.splitlines():
        cells = [c.strip().strip('"') for c in raw.split(",")]
        cells = [c for c in cells if c]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def extract_text(filename: str, content: str) -> str:
    """Turn raw uploaded content into searchable plain text by extension."""
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".json"}:
        return _parse_json_text(content)
    if suffix == ".csv":
        return _parse_csv_text(content)
    # .txt / .md / .markdown / unknown → keep verbatim.
    return content


def extract_pdf_bytes(data: bytes) -> str:
    """Extract selectable text from PDF bytes, pages joined with newlines.

    pypdf is imported lazily so the control plane starts even when the package
    is not installed. A corrupt PDF raises :class:`KnowledgeParamError` (the
    route maps it to HTTP 400). A page-scanned / image PDF that yields no
    selectable text raises :class:`KnowledgeParamError` with a clear OCR hint,
    instead of silently indexing empty content.
    """
    if not data:
        raise KnowledgeParamError("PDF content is empty")
    try:
        from pypdf import PdfReader
    except Exception as e:  # noqa: BLE001 — dependency missing
        raise KnowledgeParamError(
            "PDF 解析组件未安装（pypdf），请先安装依赖后再导入 PDF。"
        ) from e

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except KnowledgeParamError:
        raise
    except Exception as e:  # noqa: BLE001 — corrupt / unreadable PDF
        raise KnowledgeParamError(f"无法解析该 PDF（文件可能已损坏）：{e}") from e

    text = "\n".join(p.strip() for p in pages).strip()
    if not text:
        raise KnowledgeParamError(
            "该 PDF 不含可供检索的文本（疑似扫描件 / 图片版），"
            "暂不支持扫描件 OCR，请改用可选中文字的 PDF 或文本文件。"
        )
    return text


def extract_docx_bytes(data: bytes) -> str:
    """Extract text from a .docx (Office Open XML) file, stdlib only.

    A .docx is a ZIP whose main document is ``word/document.xml``. Paragraphs
    are ``<w:p>`` and visible text lives in ``<w:t>`` runs. We read it with
    :mod:`zipfile` and :mod:`xml.etree` so no third-party dependency is needed.
    A corrupt / non-docx file raises :class:`KnowledgeParamError` (route → 400).
    The legacy binary ``.doc`` format is not supported (different container).
    """
    if not data:
        raise KnowledgeParamError("DOCX content is empty")
    try:
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            try:
                xml_bytes = zf.read("word/document.xml")
            except KeyError as e:
                raise KnowledgeParamError(
                    "该文件不是有效的 .docx（缺少 word/document.xml），"
                    "旧版 .doc 二进制格式暂不支持，请另存为 .docx。"
                ) from e
    except KnowledgeParamError:
        raise
    except Exception as e:  # noqa: BLE001 — not a zip / corrupt
        raise KnowledgeParamError(
            f"无法解析该 .docx（文件可能已损坏）：{e}"
        ) from e

    try:
        # Prefer defusedxml (entity-expansion hardening); fall back to the
        # stdlib parser if the optional package is absent (files are supplied
        # by the local user).
        try:
            from defusedxml.ElementTree import fromstring as _xml_fromstring
        except Exception:  # noqa: BLE001 — defusedxml not installed
            from xml.etree.ElementTree import (  # noqa: S314
                fromstring as _xml_fromstring,
            )
        root = _xml_fromstring(xml_bytes)  # noqa: S314
    except Exception as e:  # noqa: BLE001 — malformed XML
        raise KnowledgeParamError(
            f"无法解析该 .docx 的文档内容（XML 已损坏）：{e}"
        ) from e

    w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for p in root.iter(f"{w_ns}p"):
        # Join all text runs in the paragraph; <w:tab/> and breaks are rare, so
        # a simple concatenation preserves the readable text.
        runs = [t.text or "" for t in p.iter(f"{w_ns}t")]
        paragraphs.append("".join(runs).strip())
    text = "\n".join(p for p in paragraphs if p).strip()
    if not text:
        raise KnowledgeParamError(
            "该 .docx 不含可供检索的文本（可能为空文档或仅含图片）。"
        )
    return text


def extract_pptx_bytes(data: bytes) -> str:
    """Extract text from a .pptx (Office Open XML presentation), stdlib only.

    Slides live at ``ppt/slides/slideN.xml``; visible text is in DrawingML
    ``<a:t>`` runs. Slides are read in numeric order and joined with newlines.
    Uses defusedxml when available for entity-expansion hardening. A corrupt /
    non-pptx file raises :class:`KnowledgeParamError` (route → 400).
    """
    if not data:
        raise KnowledgeParamError("PPTX content is empty")
    import re
    try:
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            numbered: list[tuple[int, str]] = []
            for n in zf.namelist():
                m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", n)
                if m:
                    numbered.append((int(m.group(1)), n))
            if not numbered:
                raise KnowledgeParamError(
                    "该文件不是有效的 .pptx（缺少 ppt/slides/），"
                    "旧版 .ppt 二进制格式暂不支持，请另存为 .pptx。"
                )
            numbered.sort(key=lambda item: item[0])
            slide_xml = [zf.read(n) for _, n in numbered]
    except KnowledgeParamError:
        raise
    except Exception as e:  # noqa: BLE001 — not a zip / corrupt
        raise KnowledgeParamError(
            f"无法解析该 .pptx（文件可能已损坏）：{e}"
        ) from e

    try:
        try:
            from defusedxml.ElementTree import fromstring as _xml_fromstring
        except Exception:  # noqa: BLE001 — defusedxml not installed
            from xml.etree.ElementTree import (  # noqa: S314
                fromstring as _xml_fromstring,
            )
        a_ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
        slides_text: list[str] = []
        for xml_bytes in slide_xml:
            root = _xml_fromstring(xml_bytes)  # noqa: S314
            runs = [t.text or "" for t in root.iter(f"{a_ns}t")]
            line = "".join(runs).strip()
            if line:
                slides_text.append(line)
    except KnowledgeParamError:
        raise
    except Exception as e:  # noqa: BLE001 — malformed XML
        raise KnowledgeParamError(
            f"无法解析该 .pptx 的幻灯片内容（XML 已损坏）：{e}"
        ) from e

    text = "\n".join(slides_text).strip()
    if not text:
        raise KnowledgeParamError(
            "该 .pptx 不含可供检索的文本（可能为空演示或仅含图片）。"
        )
    return text


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE,
               overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
    """Split ``text`` into paragraph-aware, overlap-joining chunks.

    * Paragraphs (blank-line separated) are greedily packed up to
      ``chunk_size`` characters.
    * A single paragraph longer than ``chunk_size`` is split on sentence / line
      boundaries first, then hard-cut to ``chunk_size``.
    * Adjacent chunks share an ``overlap``-char tail from the previous chunk so
      context is not lost at the seam.
    """
    if chunk_size <= 0:
        raise KnowledgeParamError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size * MAX_OVERLAP_RATIO:
        raise KnowledgeParamError(
            f"overlap must be within [0, {int(chunk_size * MAX_OVERLAP_RATIO)})"
        )

    clean = (text or "").replace("\r\n", "\n").strip()
    if not clean:
        return []

    # Split into paragraphs on blank lines.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean) if p.strip()]

    def _split_long(par: str) -> list[str]:
        if len(par) <= chunk_size:
            return [par]
        # Try sentence/line boundaries first.
        parts = re.split(r"(?<=[。！？.!?])\s+|\n+", par)
        parts = [p for p in (x.strip() for x in parts) if p]
        out: list[str] = []
        buf = ""
        for part in parts:
            if len(part) > chunk_size:
                if buf:
                    out.append(buf)
                    buf = ""
                for i in range(0, len(part), chunk_size):
                    out.append(part[i:i + chunk_size])
            elif len(buf) + len(part) + 1 <= chunk_size:
                buf = f"{buf}\n{part}" if buf else part
            else:
                if buf:
                    out.append(buf)
                buf = part
        if buf:
            out.append(buf)
        return out

    # Greedily pack paragraphs (each already ≤ chunk_size) into chunks.
    raw_chunks: list[str] = []
    buf = ""
    for par in paragraphs:
        for piece in _split_long(par):
            if len(buf) + len(piece) + 2 <= chunk_size:
                buf = f"{buf}\n\n{piece}" if buf else piece
            else:
                if buf:
                    raw_chunks.append(buf)
                buf = piece
    if buf:
        raw_chunks.append(buf)

    if overlap <= 0 or len(raw_chunks) <= 1:
        return [c.strip() for c in raw_chunks if c.strip()]

    # Carry the overlap tail of the previous chunk into the next.
    joined: list[str] = []
    prev_tail = ""
    for chunk in raw_chunks:
        piece = chunk.strip()
        if prev_tail:
            piece = f"{prev_tail}\n\n{piece}"
        joined.append(piece)
        prev_tail = chunk[-overlap:]
    return [c for c in joined if c]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------------------------------------------------------------------------
# Knowledge base manager
# ---------------------------------------------------------------------------

class KnowledgeBase:
    """CRUD + ingestion + retrieval over on-disk JSON knowledge bases."""

    def __init__(self, data_root: Path | str | None = None,
                 embedder: Any | None = None) -> None:
        root = Path(data_root) if data_root is not None else default_data_root()
        self.root = Path(root) / "knowledge"
        self.root.mkdir(parents=True, exist_ok=True)
        # An embedder exposing ``.embed(repo, texts) -> {"dimensions",
        # "embeddings"}``. None means "no embedding engine" — retrieval is
        # impossible until one is injected, but CRUD still works.
        self._embedder = embedder
        self._lock = threading.RLock()

    # -- path helpers ------------------------------------------------------
    def _kb_dir(self, kb_id: str) -> Path:
        if not _valid_id(kb_id):
            raise KnowledgeParamError(f"invalid knowledge base id: {kb_id!r}")
        d = self.root / kb_id
        # Hard-bound under self.root to prevent traversal.
        if not str(d.resolve()).startswith(str(self.root.resolve())):
            raise KnowledgeParamError("knowledge base path escapes root")
        return d

    @staticmethod
    def _read_json(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)

    # -- CRUD --------------------------------------------------------------
    def create_kb(self, name: str, model: str, *,
                  chunk_size: int = DEFAULT_CHUNK_SIZE,
                  overlap: int = DEFAULT_CHUNK_OVERLAP) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise KnowledgeParamError("knowledge base name is required")
        if not isinstance(model, str) or "/" not in model:
            raise KnowledgeParamError("model must be an 'owner/name' repo id")
        # Validate chunking params up-front.
        chunk_text("probe", chunk_size, overlap)

        kb_id = uuid.uuid4().hex[:16]
        now = _now()
        meta = {
            "id": kb_id,
            "name": name,
            "model": model,
            "chunk_size": int(chunk_size),
            "overlap": int(overlap),
            "created_at": now,
            "updated_at": now,
            "doc_count": 0,
            "chunk_count": 0,
        }
        d = self._kb_dir(kb_id)
        d.mkdir(parents=True, exist_ok=False)
        with self._lock:
            self._write_json(d / "meta.json", meta)
            self._write_json(d / "docs.json", [])
            self._write_json(d / "chunks.json", [])
            self._write_json(d / "originals.json", {})
        log.info("knowledge base created id=%s name=%r", kb_id, name)
        return self.get_kb(kb_id)

    def _load_meta(self, kb_id: str) -> dict[str, Any]:
        d = self._kb_dir(kb_id)
        meta_path = d / "meta.json"
        if not meta_path.is_file():
            raise KnowledgeNotFound(f"knowledge base not found: {kb_id}")
        return self._read_json(meta_path)

    def get_kb(self, kb_id: str) -> dict[str, Any]:
        return self._load_meta(kb_id)

    def list_kbs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return out
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            meta_path = child / "meta.json"
            if not meta_path.is_file():
                continue
            try:
                out.append(self._read_json(meta_path))
            except (json.JSONDecodeError, OSError):
                continue
        out.sort(key=lambda m: m.get("updated_at", 0), reverse=True)
        return out

    def delete_kb(self, kb_id: str) -> None:
        d = self._kb_dir(kb_id)
        if not d.is_dir():
            raise KnowledgeNotFound(f"knowledge base not found: {kb_id}")
        import shutil

        shutil.rmtree(d, ignore_errors=False)
        log.info("knowledge base deleted id=%s", kb_id)

    # -- documents ---------------------------------------------------------
    def list_documents(self, kb_id: str) -> list[dict[str, Any]]:
        self._load_meta(kb_id)
        d = self._kb_dir(kb_id)
        docs = self._read_json(d / "docs.json") if (d / "docs.json").is_file() else []
        # Pinned documents keep their full text on disk for injection, but the
        # listing only exposes the metadata (and the pinned flag) to avoid
        # shipping the whole document on every refresh.
        out: list[dict[str, Any]] = []
        for doc in docs:
            view = dict(doc)
            view.pop("text", None)
            out.append(view)
        return out

    def _load_pinned(self, kb_id: str) -> list[dict[str, Any]]:
        """Return pinned documents as ``{doc_id, doc_name, text}``.

        Pinned documents are *not* chunked or embedded: their full text lives on
        the ``docs.json`` record and is injected verbatim on search/ask. This
        never needs the embedding engine.
        """
        d = self._kb_dir(kb_id)
        docs_path = d / "docs.json"
        if not docs_path.is_file():
            return []
        out: list[dict[str, Any]] = []
        for doc in self._read_json(docs_path):
            if doc.get("pinned") and doc.get("text"):
                out.append({
                    "doc_id": doc["id"],
                    "doc_name": doc.get("filename", ""),
                    "text": doc["text"],
                })
        return out

    def _load_originals(self, kb_id: str) -> dict[str, dict[str, str]]:
        """Return ``{doc_id: {"filename", "text"}}`` raw extracted texts.

        Originals make an exact export possible: chunk text only ever stores
        overlapping slices, so without this the full source cannot be rebuilt.
        Older KBs (created before this file existed) simply have no entry here
        and fall back to a chunks-based reconstruction on export.
        """
        d = self._kb_dir(kb_id)
        path = d / "originals.json"
        if not path.is_file():
            return {}
        data = self._read_json(path)
        if not isinstance(data, dict):
            return {}
        # Keep only well-formed ``{filename, text}`` records.
        return {
            str(k): v for k, v in data.items()
            if isinstance(v, dict)
        }

    def _embed(self, model: str, texts: list[str]) -> dict[str, Any]:
        """Embed texts, translating engine-missing errors.

        The embedder is a duck-typed object (typically
        ``embedding_runtime.EmbeddingManager``). When its backend is not
        installed it raises ``EmbeddingEngineMissing``; we re-raise that as
        :class:`KnowledgeEngineMissing` so the HTTP routes surface a clean
        install-guidance response (503) instead of an unhandled 500.
        """
        if self._embedder is None:
            raise KnowledgeEngineMissing(
                "未配置嵌入引擎。请先在「向量嵌入」页安装 "
                "sentence-transformers 引擎。"
            )
        try:
            return self._embedder.embed(model, texts)
        except Exception as exc:
            if type(exc).__name__ == "EmbeddingEngineMissing":
                raise KnowledgeEngineMissing(str(exc)) from exc
            raise

    def add_document(self, kb_id: str, filename: str, content: str,
                     pinned: bool = False) -> dict[str, Any]:
        """Ingest a document into a knowledge base.

        When ``pinned`` is true the document is *not* chunked or embedded: its
        full text is stored on the doc record (``docs.json``) so it can be
        injected verbatim on every ask. This is meant for short reference
        notes that should always be in context. A pinned document therefore has
        ``chunk_count == 0`` and no rows in ``chunks.json``.
        """
        meta = self._load_meta(kb_id)
        if not isinstance(content, str) or not content.strip():
            raise KnowledgeParamError("document content is empty")
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise KnowledgeParamError("document exceeds the 8 MiB text limit")

        filename = (filename or "document.txt").strip() or "document.txt"
        text = extract_text(filename, content)
        if not text.strip():
            raise KnowledgeParamError("document produced no usable content")

        doc_id = uuid.uuid4().hex[:16]
        now = _now()
        pinned = bool(pinned)

        new_records: list[dict[str, Any]] = []
        if pinned:
            # Store the full text; skip chunking/embedding entirely.
            doc: dict[str, Any] = {
                "id": doc_id,
                "kb_id": kb_id,
                "filename": filename,
                "char_count": len(text),
                "chunk_count": 0,
                "pinned": True,
                "text": text,
                "created_at": now,
            }
        else:
            if self._embedder is None:
                raise KnowledgeEngineMissing(
                    "未配置嵌入引擎，无法索引文档。请先在「向量嵌入」页安装 "
                    "sentence-transformers 引擎。"
                )
            chunks = chunk_text(text, meta["chunk_size"], meta["overlap"])
            if not chunks:
                raise KnowledgeParamError("document produced no usable chunks")

            # Embed the chunks in one batch.
            result = self._embed(meta["model"], chunks)
            vectors = result.get("embeddings") or []
            if len(vectors) != len(chunks):
                raise KnowledgeEngineMissing(
                    "嵌入数量与分块数量不一致，索引失败"
                )

            doc = {
                "id": doc_id,
                "kb_id": kb_id,
                "filename": filename,
                "char_count": len(text),
                "chunk_count": len(chunks),
                "pinned": False,
                "created_at": now,
            }
            new_records = [
                {
                    "id": f"{doc_id}_{i}",
                    "doc_id": doc_id,
                    "doc_name": filename,
                    "index": i,
                    "text": chunk,
                    "embedding": vec,
                }
                for i, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True))
            ]

        with self._lock:
            base = self._kb_dir(kb_id)
            docs_path = base / "docs.json"
            chunks_path = base / "chunks.json"
            originals_path = base / "originals.json"
            docs = self._read_json(docs_path) if docs_path.is_file() else []
            store = self._read_json(chunks_path) if chunks_path.is_file() else []
            originals = (self._read_json(originals_path)
                         if originals_path.is_file() else {})
            docs.append(doc)
            store.extend(new_records)
            # Keep the raw extracted text so export is byte-exact. Pinned docs
            # already keep ``text`` on the doc record, but we register it here
            # too so export has one uniform source.
            originals[doc_id] = {"filename": filename, "text": text}
            self._write_json(docs_path, docs)
            self._write_json(chunks_path, store)
            self._write_json(originals_path, originals)
            meta["doc_count"] = len(docs)
            meta["chunk_count"] = len(store)
            meta["updated_at"] = now
            self._write_json(base / "meta.json", meta)

        log.info(
            "document ingested kb=%s doc=%s pinned=%s chunks=%d",
            kb_id, doc_id, pinned, len(new_records),
        )
        return doc

    def set_pinned(self, kb_id: str, doc_id: str, pinned: bool) -> dict[str, Any]:
        """Toggle a document's pinned flag.

        * Pinning (``pinned=True``) an *already* pinned doc is a no-op. Pinning
          a normally-indexed document is rejected (``KnowledgeParamError``):
          only chunks were stored, so the original full text cannot be rebuilt.
          Pinning is therefore offered at upload time, not afterwards.
        * Unpinning (``pinned=False``) a pinned document re-indexes its stored
          full text (chunk + embed) and drops the stored ``text`` field, so it
          becomes a normal retrievable document again.
        """
        meta = self._load_meta(kb_id)
        if not _valid_id(doc_id):
            raise KnowledgeParamError(f"invalid document id: {doc_id!r}")
        pinned = bool(pinned)
        with self._lock:
            d = self._kb_dir(kb_id)
            docs_path = d / "docs.json"
            chunks_path = d / "chunks.json"
            docs = self._read_json(docs_path) if docs_path.is_file() else []
            store = self._read_json(chunks_path) if chunks_path.is_file() else []
            idx = next((i for i, x in enumerate(docs) if x["id"] == doc_id), None)
            if idx is None:
                raise KnowledgeNotFound(f"document not found: {doc_id}")
            doc = docs[idx]
            already_pinned = bool(doc.get("pinned"))

            if pinned:
                if already_pinned:
                    return doc
                raise KnowledgeParamError(
                    "无法钉选已索引文档：缺少全文，请在上传时勾选钉选"
                )

            # Unpin.
            if not already_pinned:
                return doc
            text = doc.get("text")
            if not text:
                raise KnowledgeParamError("钉选文档缺少全文，无法取消钉选")
            if self._embedder is None:
                raise KnowledgeEngineMissing(
                    "未配置嵌入引擎，无法取消钉选并重新索引"
                )
            chunks = chunk_text(text, meta["chunk_size"], meta["overlap"])
            result = self._embed(meta["model"], chunks)
            vectors = result.get("embeddings") or []
            if len(vectors) != len(chunks):
                raise KnowledgeEngineMissing(
                    "嵌入数量与分块数量不一致，索引失败"
                )
            new_records = [
                {
                    "id": f"{doc_id}_{i}",
                    "doc_id": doc_id,
                    "doc_name": doc.get("filename", ""),
                    "index": i,
                    "text": chunk,
                    "embedding": vec,
                }
                for i, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True))
            ]
            store.extend(new_records)
            new_doc = {
                "id": doc_id,
                "kb_id": kb_id,
                "filename": doc.get("filename", ""),
                "char_count": doc.get("char_count", len(text)),
                "chunk_count": len(chunks),
                "pinned": False,
                "created_at": doc.get("created_at", _now()),
            }
            docs[idx] = new_doc
            self._write_json(docs_path, docs)
            self._write_json(chunks_path, store)
            meta["doc_count"] = len(docs)
            meta["chunk_count"] = len(store)
            meta["updated_at"] = _now()
            self._write_json(d / "meta.json", meta)
            log.info(
                "document unpinned kb=%s doc=%s chunks=%d", kb_id, doc_id, len(chunks)
            )
            return new_doc

    def delete_document(self, kb_id: str, doc_id: str) -> None:
        self._load_meta(kb_id)
        if not _valid_id(doc_id):
            raise KnowledgeParamError(f"invalid document id: {doc_id!r}")
        with self._lock:
            d = self._kb_dir(kb_id)
            docs_path = d / "docs.json"
            chunks_path = d / "chunks.json"
            originals_path = d / "originals.json"
            docs = self._read_json(docs_path) if docs_path.is_file() else []
            store = self._read_json(chunks_path) if chunks_path.is_file() else []
            originals = (self._read_json(originals_path)
                         if originals_path.is_file() else {})
            kept_docs = [x for x in docs if x["id"] != doc_id]
            if len(kept_docs) == len(docs):
                raise KnowledgeNotFound(f"document not found: {doc_id}")
            store = [c for c in store if c["doc_id"] != doc_id]
            originals.pop(doc_id, None)
            self._write_json(docs_path, kept_docs)
            self._write_json(chunks_path, store)
            if originals_path.is_file():
                self._write_json(originals_path, originals)
            meta = self._read_json(d / "meta.json")
            meta["doc_count"] = len(kept_docs)
            meta["chunk_count"] = len(store)
            meta["updated_at"] = _now()
            self._write_json(d / "meta.json", meta)

    # -- retrieval ---------------------------------------------------------
    def _load_chunks(self, kb_id: str) -> list[dict[str, Any]]:
        d = self._kb_dir(kb_id)
        path = d / "chunks.json"
        if not path.is_file():
            return []
        return self._read_json(path)

    def search(self, kb_id: str, query: str,
               top_k: int = DEFAULT_TOP_K) -> dict[str, Any]:
        meta = self._load_meta(kb_id)
        query = (query or "").strip()
        if not query:
            raise KnowledgeParamError("query is empty")
        if self._embedder is None:
            raise KnowledgeEngineMissing("未配置嵌入引擎，无法检索")
        top_k = max(1, min(int(top_k or DEFAULT_TOP_K), 50))

        pinned = self._load_pinned(kb_id)
        store = self._load_chunks(kb_id)
        if not store:
            return {"kb_id": kb_id, "query": query, "model": meta["model"],
                    "results": [], "pinned": pinned}

        qvec = self._embed(meta["model"], [query])["embeddings"][0]
        scored = []
        for rec in store:
            vec = rec.get("embedding") or []
            if len(vec) != len(qvec):
                continue
            scored.append((_cosine(qvec, vec), rec))
        scored.sort(key=lambda t: t[0], reverse=True)
        results = [
            {
                "score": round(score, 6),
                "chunk_id": rec["id"],
                "doc_id": rec["doc_id"],
                "doc_name": rec.get("doc_name", ""),
                "index": rec.get("index", 0),
                "text": rec["text"],
            }
            for score, rec in scored[:top_k]
        ]
        return {"kb_id": kb_id, "query": query, "model": meta["model"],
                "results": results, "pinned": pinned}

    def ask(self, kb_id: str, question: str,
            top_k: int = DEFAULT_TOP_K,
            brain: Callable[[str, str], str] | None = None) -> dict[str, Any]:
        """Retrieve context for ``question`` and optionally ground a brain answer.

        ``brain`` is an optional ``(system, prompt) -> answer`` callable supplied
        by the route layer. When it is missing the response still carries the
        retrieved chunks and a flag noting that no generator is configured.
        """
        hit = self.search(kb_id, question, top_k)
        results = hit["results"]
        pinned = hit.get("pinned") or []
        # Pinned documents are injected verbatim *before* the retrieved chunks,
        # so short always-relevant notes lead the context.
        pinned_block = "\n\n".join(
            f"【钉选】({p['doc_name']}) {p['text']}" for p in pinned
        )
        retrieved_block = "\n\n".join(
            f"[{i + 1}] ({r['doc_name']}) {r['text']}"
            for i, r in enumerate(results)
        )
        context = "\n\n".join(b for b in (pinned_block, retrieved_block) if b)
        has_context = bool(results) or bool(pinned)
        answer: str | None = None
        brain_used = False
        if brain is not None and has_context:
            system = (
                "你是一个基于本地知识库回答问题的助手。只依据下方提供的"
                "上下文作答；若上下文不足以回答，请明确说明。不要复述上下文。"
            )
            prompt = (
                f"上下文：\n{context}\n\n问题：{question}\n\n"
                "请只根据上述上下文，用与问题相同的语言，简要给出答案。\n答案："
            )
            try:
                answer = clean_brain_answer(str(brain(system, prompt)))
                brain_used = bool(answer)
            except Exception as e:  # noqa: BLE001 — a brain failure must not 500
                log.warning("knowledge brain generation failed: %s", e)
                answer = None
        return {
            "kb_id": kb_id,
            "question": question,
            "model": hit["model"],
            "context": context,
            "results": results,
            "pinned": pinned,
            "answer": answer,
            "brain_used": brain_used,
            "answer_note": (None if brain_used
                            else "未配置生成模型，仅返回检索到的上下文"),
        }

    # ------------------------------------------------------------------
    # Export / import (portable bundle)
    # ------------------------------------------------------------------
    def _reconstruct_text(self, chunks: list[dict[str, Any]],
                          overlap: int) -> str:
        """Best-effort original text from stored overlapping chunks.

        This only ever runs for documents ingested before ``originals.json``
        existed. Stored chunk ``i`` is ``(tail of chunk i-1) + "\\n\\n" + raw_i``,
        so we peel the carried prefix off every chunk after the first.
        """
        recs = sorted(chunks, key=lambda c: c.get("index", 0))
        overlap = max(0, int(overlap or 0))
        if not recs:
            return ""
        head = recs[0].get("text", "")
        tail = head[-overlap:] if overlap else ""
        parts = [head]
        for rec in recs[1:]:
            t = rec.get("text", "")
            if overlap and tail and t.startswith(tail):
                t = t[len(tail):].lstrip("\n")
            elif overlap:
                t = t[overlap:].lstrip("\n")
            parts.append(t)
            tail = t[-overlap:] if overlap else ""
        return "\n\n".join(p for p in parts if p).strip()

    def export_kb(self, kb_id: str) -> dict[str, Any]:
        """Build a portable bundle of a knowledge base.

        Layout::

            {"format": "kevrai-kb", "version": 1,
             "meta": {"name", "model", "chunk_size", "overlap"},
             "documents": [{"filename", "pinned", "text",
                            "reconstructed"?: bool}]}
        """
        meta = self._load_meta(kb_id)
        d = self._kb_dir(kb_id)
        docs = self._read_json(d / "docs.json") if (d / "docs.json").is_file() else []
        store = self._read_json(d / "chunks.json") if (d / "chunks.json").is_file() else []
        originals = self._load_originals(kb_id)
        overlap = int(meta.get("overlap", 0) or 0)

        docs_by_id: dict[str, list[dict[str, Any]]] = {}
        for rec in store:
            docs_by_id.setdefault(rec.get("doc_id"), []).append(rec)

        documents: list[dict[str, Any]] = []
        for doc in docs:
            doc_id = doc["id"]
            pinned = bool(doc.get("pinned"))
            entry: dict[str, Any] = {
                "filename": doc.get("filename", "document.txt"),
                "pinned": pinned,
            }
            original = originals.get(doc_id) or {}
            text = original.get("text")
            if not text and pinned:
                text = doc.get("text")
            reconstructed = False
            if not text:
                # Legacy document: rebuild from overlapping chunks.
                text = self._reconstruct_text(docs_by_id.get(doc_id, []), overlap)
                reconstructed = True
            entry["text"] = text or ""
            if reconstructed:
                entry["reconstructed"] = True
            documents.append(entry)

        return {
            "format": "kevrai-kb",
            "version": 1,
            "meta": {
                "name": meta.get("name", "imported"),
                "model": meta.get("model", ""),
                "chunk_size": meta.get("chunk_size", DEFAULT_CHUNK_SIZE),
                "overlap": meta.get("overlap", DEFAULT_CHUNK_OVERLAP),
            },
            "documents": documents,
        }

    def _unique_kb_name(self, base: str) -> str:
        """Append a numeric suffix until the desired KB name is free."""
        base = (base or "imported").strip() or "imported"
        existing = {m.get("name") for m in self.list_kbs()}
        if base not in existing:
            return base
        for n in range(2, 1000):
            candidate = f"{base} ({n})"
            if candidate not in existing:
                return candidate
        return f"{base} ({uuid.uuid4().hex[:6]})"

    def import_kb(self, bundle: dict[str, Any]) -> dict[str, Any]:
        """Import an exported bundle into a brand-new knowledge base."""
        if not isinstance(bundle, dict):
            raise KnowledgeParamError("bundle must be a JSON object")
        if bundle.get("format") != "kevrai-kb":
            raise KnowledgeParamError("not a kevrai-kb bundle (format mismatch)")
        if int(bundle.get("version", 0)) != 1:
            raise KnowledgeParamError("unsupported bundle version")
        meta = bundle.get("meta")
        if not isinstance(meta, dict) or not meta.get("model"):
            raise KnowledgeParamError("bundle missing meta.model")
        documents = bundle.get("documents")
        if not isinstance(documents, list):
            raise KnowledgeParamError("bundle missing documents list")

        name = self._unique_kb_name(str(meta.get("name") or "imported"))
        chunk_size = int(meta.get("chunk_size", DEFAULT_CHUNK_SIZE))
        overlap = int(meta.get("overlap", DEFAULT_CHUNK_OVERLAP))
        new_kb = self.create_kb(name, str(meta["model"]),
                                chunk_size=chunk_size, overlap=overlap)
        new_id = new_kb["id"]

        added = 0
        try:
            for item in documents:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "")
                if not text.strip():
                    continue
                filename = str(item.get("filename") or "document.txt")
                pinned = bool(item.get("pinned"))
                # add_document re-extracts by extension; for a .txt/.md payload
                # the text passes through unchanged. Errors here (e.g. missing
                # embedder) surface as KnowledgeEngineMissing/KnowledgeParamError.
                self.add_document(new_id, filename, text, pinned=pinned)
                added += 1
        except BaseException:
            # Make import transactional: never leave a half-built KB behind
            # when a document fails to ingest (e.g. embedding engine missing).
            self.delete_kb(new_id)
            raise

        log.info("knowledge base imported id=%s docs=%d", new_id, added)
        return {"kb_id": new_id, "name": name, "doc_count": added}
