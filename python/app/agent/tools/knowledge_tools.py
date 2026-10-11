"""Agent tools that retrieve from the local knowledge bases (RAG).

These tools let an autonomous agent brain search the user's knowledge bases
mid-task: it can list the available bases and run a semantic search, then read
the returned passages and reason over them. The agent itself is the generator,
so only a retrieval primitive is exposed (no separate answer model).

The knowledge base reuses a shared :class:`~app.embedding_runtime.EmbeddingManager`,
exactly like the REST layer, so no second model instance is loaded.
"""
from __future__ import annotations

from typing import Any

from ...settings import default_data_root
from ..tool_registry import Tool, ToolContext


def _knowledge(ctx: ToolContext) -> Any:
    """Return a cached KnowledgeBase, created once per ToolContext."""
    store = ctx.extra.setdefault("_knowledge", {})
    if "kb" not in store:
        from ...embedding_runtime import EmbeddingManager
        from ...knowledge_base import KnowledgeBase

        root = default_data_root()
        store["kb"] = KnowledgeBase(root, embedder=EmbeddingManager(root))
    return store["kb"]


def _resolve_kb_id(kb: Any, ref: str) -> str | None:
    """Match a knowledge base by id (exact) or display name (case-insensitive)."""
    ref = (ref or "").strip()
    if not ref:
        return None
    for meta in kb.list_kbs():
        if meta.get("id") == ref:
            return meta["id"]
    low = ref.lower()
    for meta in kb.list_kbs():
        if str(meta.get("name", "")).lower() == low:
            return meta["id"]
    return None


# ---------------------------------------------------------------------------
# 1. List knowledge bases
# ---------------------------------------------------------------------------
def _list_knowledge_bases(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    kb = _knowledge(ctx)
    bases = [
        {
            "id": m.get("id"),
            "name": m.get("name"),
            "doc_count": m.get("doc_count", 0),
            "chunk_count": m.get("chunk_count", 0),
        }
        for m in kb.list_kbs()
    ]
    return {"bases": bases, "count": len(bases)}


list_knowledge_bases = Tool(
    name="list_knowledge_bases",
    description="List the user's local knowledge bases (RAG) with their ids and document counts. Call this before search_knowledge to obtain the correct kb id.",
    parameters={
        "type": "object",
        "properties": {},
    },
    handler=_list_knowledge_bases,
    category="knowledge",
)


# ---------------------------------------------------------------------------
# 2. Search a knowledge base
# ---------------------------------------------------------------------------
def _search_knowledge(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    query = str(params.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    ref = str(params.get("kb") or params.get("kb_id") or "").strip()
    if not ref:
        return {"error": "kb (knowledge base id or name) is required"}
    top_k = int(params.get("top_k") or 5)

    kb = _knowledge(ctx)
    kb_id = _resolve_kb_id(kb, ref)
    if kb_id is None:
        names = ", ".join(str(m.get("name", "")) for m in kb.list_kbs()) or "无"
        return {"error": f"未找到知识库 {ref!r}；现有知识库：{names}"}
    try:
        hit = kb.search(kb_id, query, top_k=top_k)
    except Exception as e:  # noqa: BLE001
        hint = ""
        if "EngineMissing" in type(e).__name__ or "嵌入引擎" in str(e):
            hint = "请先在「向量嵌入」页安装 sentence-transformers 引擎。"
        return {"error": f"{e} {hint}".strip(), "error_type": type(e).__name__}

    passages = [
        {
            "score": r.get("score"),
            "doc_name": r.get("doc_name", ""),
            "text": r.get("text", ""),
        }
        for r in hit.get("results", [])
    ]
    pinned = [
        {"doc_name": r.get("doc_name", ""), "text": r.get("text", "")}
        for r in hit.get("pinned", [])
    ]
    return {"kb_id": kb_id, "query": query,
            "passages": passages, "pinned": pinned}


search_knowledge = Tool(
    name="search_knowledge",
    description="Semantic-search a local knowledge base and return the most relevant passages (plus pinned documents). Use the kb id from list_knowledge_bases.",
    parameters={
        "type": "object",
        "properties": {
            "kb": {"type": "string",
                   "description": "Knowledge base id or display name."},
            "query": {"type": "string", "description": "The question / search text."},
            "top_k": {"type": "integer",
                      "description": "Number of passages to return (default 5)."},
        },
        "required": ["kb", "query"],
    },
    handler=_search_knowledge,
    category="knowledge",
)


KNOWLEDGE_TOOLS = [
    list_knowledge_bases,
    search_knowledge,
]
