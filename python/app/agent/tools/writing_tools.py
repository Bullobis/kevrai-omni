"""Agent skill tools — writing studio (writing skill).

Local text-craft helpers. Every tool returns a deterministic, immediately useful
result (outline scaffold, extractive summary, text statistics, a ready-to-run
prompt) and, when a dialogue model is loaded in the MNN runtime and
``use_llm`` is true, additionally fills ``llm_output`` with model-generated
content. This keeps the tools functional in rule-based mode (no LLM) while
taking advantage of a local LLM when present.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..tool_registry import Tool, ToolContext


def _clean(v: Any, max_len: int = 8000) -> str:
    return str(v or "").strip()[:max_len]


def _try_llm(prompt: str, max_new_tokens: int = 1024) -> dict[str, Any]:
    """Best-effort local LLM call; never raises (degrades to deterministic)."""
    try:
        from ... import mnn_runtime
        status = mnn_runtime.status()
        if not status.get("loaded"):
            return {"ready": False, "text": "", "model_name": ""}
        res = mnn_runtime.chat(prompt, max_new_tokens=max_new_tokens)
        return {
            "ready": True,
            "text": str(res.get("text") or "").strip(),
            "model_name": status.get("model_name", ""),
        }
    except Exception as e:  # pragma: no cover - defensive
        return {"ready": False, "text": "", "error": str(e)}


def _text_stats(text: str) -> dict[str, Any]:
    sentences = [s for s in re.split(r"[。！？!?\n]+", text) if s.strip()]
    cjk = len(re.findall(r"[一-鿿]", text))
    words = len(text)
    lengths = [len(s) for s in sentences] or [0]
    return {
        "chars": words,
        "cjk_chars": cjk,
        "sentence_count": len(sentences),
        "avg_sentence_len": round(sum(lengths) / len(lengths), 1),
        "max_sentence_len": max(lengths),
    }


# ---------------------------------------------------------------------------
# writing_outline
# ---------------------------------------------------------------------------
_OUTLINE_TEMPLATES: dict[str, list[str]] = {
    "文章": ["开篇引入（背景/钩子）", "现状与问题", "分论点一", "分论点二", "分论点三", "总结与行动建议"],
    "方案": ["背景与目标", "现状分析", "核心思路", "实施步骤", "资源与排期", "风险与应对", "预期收益"],
    "报告": ["摘要", "数据/事实", "分析", "问题定位", "结论", "建议"],
    "故事": ["开场处境与钩子", "冲突出现", "冲突升级", "关键转折", "高潮", "结局与余韵"],
    "演讲稿": ["开场抓人", "建立共鸣", "核心观点", "论据/故事", "行动号召", "金句收尾"],
    "通用": ["引言", "背景", "主体一", "主体二", "主体三", "结论"],
}


def _outline(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    topic = _clean(params.get("topic"), 300)
    if not topic:
        return {"error": "topic is required"}
    doc_type = _clean(params.get("doc_type"), 40) or "通用"
    template = _OUTLINE_TEMPLATES.get(doc_type, _OUTLINE_TEMPLATES["通用"])
    n = int(params.get("sections") or len(template))
    n = max(2, min(n, 12))
    sections = template[:n] if n <= len(template) else template + [f"补充部分{i}" for i in range(1, n - len(template) + 1)]
    outline = [{"index": i + 1, "heading": h, "points": []} for i, h in enumerate(sections)]
    prompt = (
        f"请为一篇「{doc_type}」拟定详细大纲，主题：{topic}。\n"
        f"要求包含 {n} 个部分：{ '、'.join(sections) }。每部分给出 2-3 个要点，用中文，条理清晰。"
    )
    out: dict[str, Any] = {
        "topic": topic, "doc_type": doc_type, "outline": outline,
        "llm_prompt": prompt,
    }
    if params.get("use_llm"):
        llm = _try_llm(prompt)
        out["llm_output"] = llm.get("text", "")
        out["llm_ready"] = llm.get("ready", False)
    return out


writing_outline = Tool(
    name="writing_outline",
    description="写作大纲：给定主题与文体（文章/方案/报告/故事/演讲稿/通用），返回结构化大纲骨架；use_llm=true 且已加载对话 AI 时附带模型扩写。",
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "主题（必填）"},
            "doc_type": {"type": "string", "description": "文体：文章/方案/报告/故事/演讲稿/通用"},
            "sections": {"type": "integer", "description": "部分数量 2-12"},
            "use_llm": {"type": "boolean", "description": "是否调用本地对话 AI 扩写"},
        },
        "required": ["topic"],
    },
    handler=_outline,
    category="creation",
)


# ---------------------------------------------------------------------------
# writing_polish
# ---------------------------------------------------------------------------
def _polish(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    text = _clean(params.get("text"))
    if not text:
        return {"error": "text is required"}
    goal = _clean(params.get("goal"), 40) or "润色"
    stats = _text_stats(text)
    checklist = [
        "删除冗余口语词与重复表达",
        "长句拆分，一句话只表达一个意思",
        "主谓宾完整，术语前后一致",
        "检查错别字、标点与中英文空格",
    ]
    prompt = (
        f"请对下面文本进行「{goal}」，保持原意与事实，不新增信息，直接输出修改后的文本：\n\n{text}"
    )
    out: dict[str, Any] = {
        "goal": goal, "stats": stats, "checklist": checklist, "llm_prompt": prompt,
    }
    if params.get("use_llm"):
        llm = _try_llm(prompt, max_new_tokens=max(512, min(len(text) * 2, 4096)))
        out["llm_output"] = llm.get("text", "")
        out["llm_ready"] = llm.get("ready", False)
    return out


writing_polish = Tool(
    name="writing_polish",
    description="文本润色/精简/正式化/口语化：返回文本统计、修改清单与提示词；use_llm=true 时附带本地模型改写结果（保持原意）。",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "待处理文本（必填）"},
            "goal": {"type": "string", "description": "目标：润色/精简/更正式/更口语"},
            "use_llm": {"type": "boolean", "description": "是否调用本地对话 AI"},
        },
        "required": ["text"],
    },
    handler=_polish,
    category="creation",
)


# ---------------------------------------------------------------------------
# writing_summary (extractive, deterministic + optional LLM)
# ---------------------------------------------------------------------------
_STOPWORDS = set(["的", "了", "和", "是", "在", "我", "你", "他", "她", "它", "们", "就", "都", "而", "及", "与", "着", "或", "一个", "没有", "我们", "你们", "他们", "这个", "那个", "因为", "所以", "但是", "如果", "这样", "那样", "可以", "什么", "怎么"])


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[。！？!?])", text) if s.strip()]


def _summary(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    text = _clean(params.get("text"))
    if not text:
        return {"error": "text is required"}
    top_n = int(params.get("max_sentences") or 3)
    top_n = max(1, min(top_n, 10))
    sentences = _split_sentences(text)
    # Word frequency (CJK bigrams + ascii words), stopwords removed.
    tokens = re.findall(r"[一-鿿]{2,}|[A-Za-z]{2,}", text)
    bigrams: list[str] = []
    for tok in tokens:
        if re.fullmatch(r"[A-Za-z]{2,}", tok):
            low = tok.lower()
            if low not in _STOPWORDS:
                bigrams.append(low)
        else:
            for i in range(len(tok) - 1):
                bg = tok[i:i + 2]
                if bg not in _STOPWORDS:
                    bigrams.append(bg)
    freq = Counter(bigrams)
    scored = []
    for idx, s in enumerate(sentences):
        toks = re.findall(r"[一-鿿]{2,}|[A-Za-z]{2,}", s)
        score = 0
        for tok in toks:
            if re.fullmatch(r"[A-Za-z]{2,}", tok):
                score += freq.get(tok.lower(), 0)
            else:
                for i in range(len(tok) - 1):
                    score += freq.get(tok[i:i + 2], 0)
        # Position bias: lead sentences usually carry the thesis.
        score += max(0, len(sentences) - idx) * 0.5
        scored.append((score, idx, s))
    picked = sorted(sorted(scored, key=lambda x: -x[0])[:top_n], key=lambda x: x[1])
    extractive = [{"index": idx, "sentence": s} for _, idx, s in picked]
    stats = _text_stats(text)
    prompt = f"请用 {top_n} 句话总结以下文本，保留关键事实，不要新增信息：\n\n{text}"
    out: dict[str, Any] = {
        "stats": stats,
        "extractive_summary": "".join(x["sentence"] for x in extractive),
        "key_sentences": extractive,
        "llm_prompt": prompt,
    }
    if params.get("use_llm"):
        llm = _try_llm(prompt)
        out["llm_output"] = llm.get("text", "")
        out["llm_ready"] = llm.get("ready", False)
    return out


writing_summary = Tool(
    name="writing_summary",
    description="文本摘要：离线抽取式摘要（词频+位置加权，返回关键句）与统计；use_llm=true 时附带本地模型摘要。",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "原文（必填）"},
            "max_sentences": {"type": "integer", "description": "抽取关键句数 1-10，默认 3"},
            "use_llm": {"type": "boolean", "description": "是否调用本地对话 AI 生成抽象摘要"},
        },
        "required": ["text"],
    },
    handler=_summary,
    category="creation",
)


# ---------------------------------------------------------------------------
# writing_translate
# ---------------------------------------------------------------------------
def _translate(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    text = _clean(params.get("text"))
    if not text:
        return {"error": "text is required"}
    target = _clean(params.get("target_lang"), 40) or "英文"
    prompt = (
        f"请把以下内容翻译成{target}，保留原意、术语准确、表达自然，只输出译文：\n\n{text}"
    )
    out: dict[str, Any] = {
        "target_lang": target,
        "source_chars": len(text),
        "guidance": ["专有名词首次出现可保留原文并括注", "数字、单位、型号不译错", "语气与原文语体一致"],
        "llm_prompt": prompt,
    }
    if params.get("use_llm"):
        llm = _try_llm(prompt, max_new_tokens=max(256, min(len(text) * 2, 4096)))
        out["llm_output"] = llm.get("text", "")
        out["llm_ready"] = llm.get("ready", False)
    else:
        out["note"] = "翻译需要本地对话 AI；请置 use_llm=true（未加载模型时仅返回提示词与规范）。"
    return out


writing_translate = Tool(
    name="writing_translate",
    description="文本翻译：返回译文规范与提示词；use_llm=true 且已加载对话 AI 时返回本地模型译文（默认目标语言英文）。",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "原文（必填）"},
            "target_lang": {"type": "string", "description": "目标语言，默认 英文"},
            "use_llm": {"type": "boolean", "description": "是否调用本地对话 AI"},
        },
        "required": ["text"],
    },
    handler=_translate,
    category="creation",
)


WRITING_TOOLS = [writing_outline, writing_polish, writing_summary, writing_translate]
