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

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".csv"}
DEFAULT_CHUNK_SIZE = 600
DEFAULT_CHUNK_OVERLAP = 80
DEFAULT_TOP_K = 4
MAX_CHUNK_SIZE = 4000
MAX_OVERLAP_RATIO = 0.5  # overlap must stay below half the chunk size
MAX_CONTENT_BYTES = 8 * 1024 * 1024  # 8 MiB of raw text per document
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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
        return list(docs)

    def add_document(self, kb_id: str, filename: str, content: str) -> dict[str, Any]:
        meta = self._load_meta(kb_id)
        if self._embedder is None:
            raise KnowledgeEngineMissing(
                "未配置嵌入引擎，无法索引文档。请先在「向量嵌入」页安装 "
                "sentence-transformers 引擎。"
            )
        if not isinstance(content, str) or not content.strip():
            raise KnowledgeParamError("document content is empty")
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise KnowledgeParamError("document exceeds the 8 MiB text limit")

        filename = (filename or "document.txt").strip() or "document.txt"
        text = extract_text(filename, content)
        chunks = chunk_text(text, meta["chunk_size"], meta["overlap"])
        if not chunks:
            raise KnowledgeParamError("document produced no usable chunks")

        # Embed the chunks in one batch.
        result = self._embedder.embed(meta["model"], chunks)
        vectors = result.get("embeddings") or []
        if len(vectors) != len(chunks):
            raise KnowledgeEngineMissing(
                "嵌入数量与分块数量不一致，索引失败"
            )

        doc_id = uuid.uuid4().hex[:16]
        now = _now()
        doc = {
            "id": doc_id,
            "kb_id": kb_id,
            "filename": filename,
            "char_count": len(text),
            "chunk_count": len(chunks),
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
            docs_path = self._kb_dir(kb_id) / "docs.json"
            chunks_path = self._kb_dir(kb_id) / "chunks.json"
            docs = self._read_json(docs_path) if docs_path.is_file() else []
            store = self._read_json(chunks_path) if chunks_path.is_file() else []
            docs.append(doc)
            store.extend(new_records)
            self._write_json(docs_path, docs)
            self._write_json(chunks_path, store)
            meta["doc_count"] = len(docs)
            meta["chunk_count"] = len(store)
            meta["updated_at"] = now
            self._write_json(self._kb_dir(kb_id) / "meta.json", meta)

        log.info(
            "document ingested kb=%s doc=%s chunks=%d", kb_id, doc_id, len(chunks)
        )
        return doc

    def delete_document(self, kb_id: str, doc_id: str) -> None:
        self._load_meta(kb_id)
        if not _valid_id(doc_id):
            raise KnowledgeParamError(f"invalid document id: {doc_id!r}")
        with self._lock:
            d = self._kb_dir(kb_id)
            docs_path = d / "docs.json"
            chunks_path = d / "chunks.json"
            docs = self._read_json(docs_path) if docs_path.is_file() else []
            store = self._read_json(chunks_path) if chunks_path.is_file() else []
            kept_docs = [x for x in docs if x["id"] != doc_id]
            if len(kept_docs) == len(docs):
                raise KnowledgeNotFound(f"document not found: {doc_id}")
            store = [c for c in store if c["doc_id"] != doc_id]
            self._write_json(docs_path, kept_docs)
            self._write_json(chunks_path, store)
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

        store = self._load_chunks(kb_id)
        if not store:
            return {"kb_id": kb_id, "query": query, "model": meta["model"],
                    "results": []}

        qvec = self._embedder.embed(meta["model"], [query])["embeddings"][0]
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
                "results": results}

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
        context = "\n\n".join(
            f"[{i + 1}] ({r['doc_name']}) {r['text']}"
            for i, r in enumerate(results)
        )
        answer: str | None = None
        brain_used = False
        if brain is not None and results:
            system = (
                "你是一个基于本地知识库回答问题的助手。只依据下方提供的"
                "上下文作答；若上下文不足以回答，请明确说明。"
            )
            prompt = f"上下文：\n{context}\n\n问题：{question}"
            try:
                answer = str(brain(system, prompt))
                brain_used = True
            except Exception as e:  # noqa: BLE001 — a brain failure must not 500
                log.warning("knowledge brain generation failed: %s", e)
                answer = None
        return {
            "kb_id": kb_id,
            "question": question,
            "model": hit["model"],
            "context": context,
            "results": results,
            "answer": answer,
            "brain_used": brain_used,
            "answer_note": (None if brain_used
                            else "未配置生成模型，仅返回检索到的上下文"),
        }
