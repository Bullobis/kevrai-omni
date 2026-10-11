"""On-device text translation built on the local text "brain" (transformers).

This is a pure, dependency-light module: the actual model call is injected as
a ``(system, prompt) -> str`` callable by the route layer (mirroring how
``knowledge_base`` accepts a ``brain`` callback), so the prompt assembly and the
noisy-completion cleaning stay fully unit-testable without torch/transformers.

The brain runs in raw-continuation mode (no chat template), so the prompt ends
on an explicit ``Translation:`` anchor that the model completes, and the
returned completion is aggressively trimmed to just the translation.
"""
from __future__ import annotations

import re
from collections.abc import Callable

# Supported language codes. ``LANG_ZN`` are the names handed to the model:
# Qwen3-0.6B / SmolLM2 follow a Chinese instruction with Chinese language names
# far more reliably than an English one-shot. The UI keeps its own localized
# labels on top of these codes.
LANG_NAMES: dict[str, str] = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "ru": "Russian",
}

LANG_ZN: dict[str, str] = {
    "zh": "中文",
    "en": "英文",
    "ja": "日文",
    "ko": "韩文",
    "fr": "法文",
    "de": "德文",
    "es": "西班牙文",
    "ru": "俄文",
}

# Strict system instruction: the brain must emit ONLY the translation.
SYSTEM_PROMPT = (
    "你是专业翻译引擎。只输出译文本身，不要解释、不要注释、"
    "不要引号、不要 Markdown、不要重复原文。"
)

# Anchors small brains love to re-emit when they loop: "译文：", "翻译结果:",
# "Translation:", etc. A second occurrence means a fresh (wrong) answer started.
_ANCHOR_RE = re.compile(
    r"^\s*(?:译文|译文结果|翻译|翻译结果|英文|中文|Translation|Translated|Translate|"
    r"Output|Result|Answer|输出)[：:]\s*",
    re.IGNORECASE,
)
# An anchor on a LATER line (after some real output) marks a loop restart.
_LATER_ANCHOR_RE = re.compile(
    r"\n\s*(?:译文|译文结果|翻译|翻译结果|英文|中文|Translation|Translated|Translate|"
    r"Output|Result|Answer|输出)[：:]",
    re.IGNORECASE,
)

# Surrounding quote pairs the model tends to wrap a one-shot answer in.
_OPEN_QUOTES = "\"'“「『《“"
_CLOSE_QUOTES = "\"'”」』》”"


class TranslateError(Exception):
    """Base error for translation."""


class TranslateParamError(TranslateError):
    """Invalid translation parameters (empty text, missing target, ...)."""


def language_name(code: str) -> str:
    """Human English name of a language code (unknown -> the code)."""
    return LANG_NAMES.get((code or "").strip().lower(), (code or "").strip())


def build_prompt(text: str, source_lang: str, target_lang: str) -> str:
    """Assemble the strict translation prompt that ends on an output anchor."""
    src = (source_lang or "auto").strip().lower()
    tgt = (target_lang or "").strip().lower()
    tgt_name = LANG_ZN.get(tgt, tgt)
    if src and src != "auto" and src in LANG_ZN:
        instruction = f"把下面的{LANG_ZN[src]}翻译成{tgt_name}，只输出译文："
    else:
        instruction = f"判断下面文本的语言并翻译成{tgt_name}，只输出译文："
    # The trailing anchor is what the raw-continuation model completes.
    return f"{instruction}\n\n{text}\n\n{tgt_name}："


def clean_translation(raw: str) -> str:
    """Trim a raw brain completion down to just the translation.

    A small greedy brain (Qwen3-0.6B) reliably emits the correct translation as
    its FIRST paragraph, then rambles (an explanation, a repeated "翻译结果："
    block, quotes or markdown). Keep that first paragraph and strip the
    scaffolding — deliberately a fresh implementation rather than reusing
    ``knowledge_base.clean_brain_answer`` (which anchors on 答案).
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    # 1. The whole completion may be wrapped in a markdown code fence; unwrap it.
    if text.startswith("```"):
        body = "\n".join(text.splitlines()[1:])
        text = body.split("```")[0].strip()
    # 2. Keep the FIRST paragraph — rambling follows a blank line.
    parts = re.split(r"\n\s*\n", text)
    text = parts[0].strip() if parts and parts[0].strip() else text
    # 3. Any anchor on a later line restarts the answer — keep only the block
    #    before it.
    text = _LATER_ANCHOR_RE.split(text, maxsplit=1)[0].strip()
    # 4. Strip a leading anchor on the first line (the prompt's scaffolding).
    text = _ANCHOR_RE.sub("", text, count=1).strip()
    # 5. Strip a whole-block markdown emphasis wrapper (**...**).
    if text.startswith("**") and text.endswith("**") and len(text) >= 4:
        text = text[2:-2].strip()
    # 6. Cut at the first repeated non-empty line (greedy repetition loop).
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        key = line.strip()
        if not key:
            out.append(line)
            continue
        if key in seen:
            break
        seen.add(key)
        out.append(line)
    text = "\n".join(out).strip()
    # 7. Strip one layer of surrounding matching quotes (ASCII / CJK curly).
    if len(text) >= 2 and text[0] in _OPEN_QUOTES and text[-1] in _CLOSE_QUOTES:
        text = text[1:-1].strip()
    return text


def resolve_brain_repo(explicit: str, backend: tuple) -> str | None:
    """Pick the brain repo to use.

    An explicit ``owner/name`` wins; otherwise fall back to the currently
    configured transformers brain from the agent router. Returns ``None`` when
    no usable transformers brain is selected (route layer then asks the user to
    pick one instead of 500-ing).
    """
    explicit = (explicit or "").strip()
    if "/" in explicit:
        return explicit
    kind, repo = backend
    if kind == "transformers" and isinstance(repo, str) and "/" in repo:
        return repo
    return None


def translate_text(
    generate: Callable[[str, str], str],
    *,
    text: str,
    source_lang: str,
    target_lang: str,
) -> str:
    """Run a translation through an injected ``generate(system, prompt)`` brain.

    ``generate`` is supplied by the route (wrapping ``TextBrainManager.generate``)
    and is what tests replace with a deterministic fake.
    """
    text = (text or "").strip()
    if not text:
        raise TranslateParamError("待翻译文本为空")
    target = (target_lang or "").strip()
    if not target or target.lower() == "auto":
        raise TranslateParamError("请选择目标语言")
    if target not in LANG_NAMES:
        raise TranslateParamError(f"不支持的目标语言: {target}")
    prompt = build_prompt(text, source_lang, target)
    raw = generate(SYSTEM_PROMPT, prompt)
    return clean_translation(raw)
