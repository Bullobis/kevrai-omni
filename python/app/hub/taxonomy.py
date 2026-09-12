"""Pure mapping helpers: upstream signal → local taxonomy / engine / size.

and §1.7 (``infer_engines()`` — the "全部兼容" landing point).

**No network, no IO except reading ``catalog/engines.json`` once** (so inferred
engine ids can be intersected with the 30 real engine ids the UI can install).

Verified facts baked into this module
-------------------------------------
* ModelScope search returns ``Tasks`` as a list of *objects*
  (``{"Name": "image-text-to-text", "DomainName": "multi-modal",
  "ChineseName": "视觉多模态理解", "Id": 295}``) — verified by live
  ``PUT https://modelscope.cn/api/v1/models``.
* HuggingFace ``/api/models`` returns snake_case ``pipeline_tag``, ``tags``,
  ``library_name`` — verified via the ``hf-mirror.com`` mirror.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .base import (
    CATEGORIES,
    HUB_CURATED,
    as_float,
    as_int,
    as_str_list,
    pick,
)

# ---------------------------------------------------------------------------
# Engine ids (must intersect with catalog/engines.json)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
ENGINES_JSON = _REPO_ROOT / "catalog" / "engines.json"

_ENGINE_IDS_CACHE: set[str] | None = None


def known_engine_ids() -> set[str]:
    """Ids declared in ``catalog/engines.json`` (cached; degrades to a
    hard-coded fallback if the file is missing so tests never break)."""
    global _ENGINE_IDS_CACHE
    if _ENGINE_IDS_CACHE is not None:
        return _ENGINE_IDS_CACHE
    ids: set[str] = set()
    try:
        data = json.loads(ENGINES_JSON.read_text(encoding="utf-8"))
        for e in (data or {}).get("engines", []):
            eid = str((e or {}).get("id", "")).strip()
            if eid:
                ids.add(eid)
    except (OSError, json.JSONDecodeError, AttributeError):
        ids = set()
    if not ids:
        ids = {
            "llama.cpp", "vllm", "mnn", "onnxruntime", "diffusers", "comfyui",
            "ltx-video", "kokoro-engine", "fish-speech", "f5-tts", "cosyvoice",
            "spark-tts", "chatterbox", "indextts", "hunyuan3d", "trellis",
            "triposr", "direct3d-s2", "triposg", "partcrafter", "insightface",
            "sglang", "ollama", "sglang-omni", "comfyui-fl-minimaxmusic3",
            "mlx", "hunyuanworld", "sam3d", "pixal3d", "4danyone",
        }
    _ENGINE_IDS_CACHE = ids
    return ids


def reset_engine_ids_cache() -> None:
    """Drop the memoized engine-id set (used by tests)."""
    global _ENGINE_IDS_CACHE
    _ENGINE_IDS_CACHE = None


# ---------------------------------------------------------------------------
# License canonicalization
# ---------------------------------------------------------------------------

LICENSE_CANON: dict[str, str] = {
    "mit": "MIT",
    "apache-2.0": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache2.0": "Apache-2.0",
    "apache-2": "Apache-2.0",
    "gpl-3.0": "GPL-3.0",
    "gpl-2.0": "GPL-2.0",
    "lgpl-3.0": "LGPL-3.0",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "cc-by-nc-4.0": "CC-BY-NC-4.0",
    "cc-by-4.0": "CC-BY-4.0",
    "cc-by-sa-4.0": "CC-BY-SA-4.0",
    "openrail": "OpenRAIL",
    "openrail++": "OpenRAIL++",
    "other": "other",
    "unknown": "",
    "": "",
}

# HF encodes the license inside `tags` as "license:apache-2.0".
_HF_LICENSE_TAG_RE = re.compile(r"^license:(.+)$", re.I)


def canonical_license(raw: Any, tags: Sequence[str] | None = None) -> str:
    """Normalize a license string; fall back to an HF ``license:*`` tag."""
    s = str(raw or "").strip()
    if not s and tags:
        for t in tags:
            m = _HF_LICENSE_TAG_RE.match(str(t or "").strip())
            if m:
                s = m.group(1).strip()
                break
    if not s:
        return ""
    key = s.lower().replace(" ", "")
    if key in LICENSE_CANON:
        return LICENSE_CANON[key]
    # Preserve the upstream casing when we have no canonical form.
    return s[:64]


# ---------------------------------------------------------------------------
# Category mapping
# ---------------------------------------------------------------------------

_HF_PIPELINE_MAP: dict[str, str] = {
    "text-generation": "llm",
    "text2text-generation": "llm",
    "question-answering": "llm",
    "fill-mask": "llm",
    "text-to-speech": "tts",
    "text-to-audio": "tts",
    "automatic-speech-recognition": "audio",
    "audio-classification": "audio",
    "text-to-video": "video",
    "image-to-video": "video",
    "text-to-image": "image",
    "image-to-image": "image",
    "unconditional-image-generation": "image",
    "image-super-resolution": "superres",
    "image-to-3d": "3d",
    "text-to-3d": "3d",
    "image-classification": "vision",
    "object-detection": "vision",
    "image-segmentation": "vision",
    "image-text-to-text": "vision",
    "any-to-any": "other",
}

# ModelScope Tasks[].DomainName → local category
_MS_DOMAIN_MAP: dict[str, str] = {
    "multi-modal": "vision",
    "cv": "vision",
    "nlp": "llm",
    "audio": "audio",
    "3d": "3d",
    "image": "image",
    "video": "video",
    "llm": "llm",
}

# ModelScope Tasks[].Name (same vocabulary as HF pipeline tags) → category
_MS_TASK_MAP: dict[str, str] = {
    "image-text-to-text": "vision",
    "text-to-image": "image",
    "image-to-image": "image",
    "text-to-video": "video",
    "image-to-video": "video",
    "text-to-speech": "tts",
    "text-to-audio": "tts",
    "automatic-speech-recognition": "audio",
    "audio-classification": "audio",
}

_CJK_HINTS: tuple[tuple[str, str], ...] = (
    ("视频", "video"),
    ("语音合成", "tts"),
    ("文本转语音", "tts"),
    ("超分", "superres"),
    ("三维", "3d"),
    ("文生图", "image"),
    ("图生图", "image"),
    ("图像生成", "image"),
    ("语音识别", "audio"),
    ("对话", "llm"),
    ("大模型", "llm"),
)


def _match_cjk(text: str) -> str:
    for needle, cat in _CJK_HINTS:
        if needle in text:
            return cat
    return ""


def map_category(
    *,
    pipeline_tag: Any = "",
    tasks: Any = None,
    domain: Any = None,
    tags: Sequence[str] | None = None,
    fallback: str = "other",
) -> str:
    """Map heterogeneous upstream signals onto one of the 10 local categories.

    Defensive by construction: ``tasks`` may be ``None``, ``[]``, a list of
    strings, or a list of objects (ModelScope verified shape). Anything
    unrecognized yields ``fallback`` (``"other"``) — never raises (E07).
    """
    pt = str(pipeline_tag or "").strip().lower()
    if pt:
        # Handle ModelScope's `Tasks[].Name` too — same vocabulary.
        hashed = pt.lower().replace("_", "-")
        if hashed in _MS_TASK_MAP:
            return _MS_TASK_MAP[hashed]
        # `diffusers` / `transformers` library names are not pipeline tags.
        cat = _HF_PIPELINE_MAP.get(hashed)
        if cat:
            return cat

    # ModelScope: Tasks[] is a list of objects with Name / DomainName / ChineseName.
    if isinstance(tasks, Sequence) and not isinstance(tasks, (str, bytes)):
        for t in tasks:
            if isinstance(t, Mapping):
                name = str(pick(t, "Name", "name", default="") or "").strip()
                dom = str(pick(t, "DomainName", "domainName", default="") or "").strip()
                zh = str(pick(t, "ChineseName", "chineseName", default="") or "").strip()
            else:
                name, dom, zh = str(t or "").strip(), "", ""
            if name:
                n = name.lower().replace("_", "-")
                if n in _MS_TASK_MAP:
                    return _MS_TASK_MAP[n]
                if n in _HF_PIPELINE_MAP:
                    return _HF_PIPELINE_MAP[n]
            if dom:
                d = dom.lower()
                if d in _MS_DOMAIN_MAP:
                    # `audio` domain splits into tts/audio by task wording.
                    if d == "audio" and ("tts" in name.lower() or "语音合成" in zh):
                        return "tts"
                    return _MS_DOMAIN_MAP[d]
            if zh:
                c = _match_cjk(zh)
                if c:
                    return c

    if isinstance(domain, str) and domain.strip():
        d = domain.strip().lower()
        if d in _MS_DOMAIN_MAP:
            return _MS_DOMAIN_MAP[d]
    elif isinstance(domain, Sequence):
        for d0 in domain:
            d = str(d0 or "").strip().lower()
            if d in _MS_DOMAIN_MAP:
                return _MS_DOMAIN_MAP[d]

    for t in (tags or []):
        ts = str(t or "").strip().lower().replace("_", "-")
        if ts in _HF_PIPELINE_MAP:
            return _HF_PIPELINE_MAP[ts]
        if ts in _MS_TASK_MAP:
            return _MS_TASK_MAP[ts]
        c = _match_cjk(str(t or ""))
        if c:
            return c

    return fallback if fallback in CATEGORIES else "other"


# ---------------------------------------------------------------------------
# Engine inference (§1.7)
# ---------------------------------------------------------------------------

_MNN_NAMESPACES = {"mnn", "taobao-mnn"}

_GGUF_RE = re.compile(r"\.gguf$", re.I)
_MNN_RE = re.compile(r"\.mnn$", re.I)
_ONNX_RE = re.compile(r"\.onnx$", re.I)
_SAFETENSORS_RE = re.compile(r"\.safetensors$", re.I)
_TORCH_BIN_RE = re.compile(r"(^|/)pytorch_model.*\.bin$", re.I)
_PT_RE = re.compile(r"\.pt$", re.I)
_CKPT_RE = re.compile(r"\.ckpt$", re.I)
_PTH_RE = re.compile(r"\.pth$", re.I)


def _file_paths(files: Iterable[Any]) -> list[str]:
    """Accept ``str``, ``RemoteFile``, or dicts; return lower-cased paths."""
    out: list[str] = []
    for f in files or []:
        if isinstance(f, str):
            p = f
        elif isinstance(f, Mapping):
            p = str(pick(f, "path", "Path", "name", "Name", default="") or "")
        else:
            p = str(getattr(f, "path", "") or "")
        p = p.strip().lower()
        if p:
            out.append(p)
    return out


def infer_engines(
    files: Iterable[Any],
    task: str = "",
    repo: str = "",
    platform: str = "",
    category: str = "",
) -> tuple[list[str], bool]:
    """Infer installable engines from the *actual file formats*.

    Args:
        files: iterable of paths / ``RemoteFile`` / dicts with a path key.
        task: upstream task tag (unused for now, kept for signature stability).
        repo: ``owner/name`` — used for the MNN namespace shortcut.
        platform: ``"darwin"`` adds ``mlx`` for LLM safetensors.
        category: local category, decides which safetensors engines apply.

    Returns:
        ``(engines, import_only)``. ``engines`` is always a subset of the ids in
        ``catalog/engines.json`` so the "安装" button always maps to a real
        engine. ``import_only`` is True when nothing could be inferred — the
        model is still downloadable + importable (§1.7 fallback semantics).
    """
    paths = _file_paths(files)
    repo_l = str(repo or "").lower()
    ns = repo_l.split("/", 1)[0] if "/" in repo_l else ""

    candidates: list[str] = []

    if any(_GGUF_RE.search(p) for p in paths):
        candidates += ["llama.cpp", "ollama"]

    if (any(_MNN_RE.search(p) for p in paths)
            or ns in _MNN_NAMESPACES
            or repo_l.endswith("-mnn")):
        candidates.append("mnn")

    if any(_ONNX_RE.search(p) for p in paths):
        candidates.append("onnxruntime")

    if any(_SAFETENSORS_RE.search(p) or _TORCH_BIN_RE.search(p) for p in paths):
        if category == "llm":
            candidates += ["vllm", "sglang", "transformers"]
            if platform == "darwin":
                candidates.append("mlx")
        elif category in ("image", "video"):
            candidates += ["diffusers", "comfyui"]
        else:
            candidates.append("transformers")

    if any(_PT_RE.search(p) for p in paths):
        candidates.append("transformers")

    if any(_CKPT_RE.search(p) or _PTH_RE.search(p) for p in paths):
        candidates.append("comfyui")

    known = known_engine_ids()
    ordered: list[str] = []
    for e in candidates:
        if e in known and e not in ordered:
            ordered.append(e)
    return ordered, (not ordered)


def infer_engines_from_names(
    files: Iterable[Any],
    *,
    repo: str = "",
    category: str = "",
    platform: str = "",
) -> tuple[list[str], bool]:
    """Alias kept for readability at call sites (same as :func:`infer_engines`)."""
    return infer_engines(files, task="", repo=repo, platform=platform,
                         category=category)


# ---------------------------------------------------------------------------
# Size helpers
# ---------------------------------------------------------------------------


def size_gb_from_bytes(total_bytes: Any) -> float:
    """Bytes → GiB rounded to 3 decimals (never negative)."""
    try:
        b = as_float(total_bytes, 0.0)
    except (TypeError, ValueError):
        b = 0.0
    if b <= 0:
        return 0.0
    return round(b / (1024 ** 3), 3)


def total_size_bytes(files: Iterable[Any]) -> int:
    """Sum the sizes of file-like objects / mappings (always an int)."""
    total = 0
    for f in files or []:
        if isinstance(f, Mapping):
            total += as_int(pick(f, "size", "Size", default=0), 0)
        else:
            total += as_int(getattr(f, "size", 0), 0)
    return total


def trending_from(downloads: Any, likes: Any = 0, threshold: int = 100_000) -> bool:
    """Local trending rule — deliberately does not depend on upstream flags."""
    return as_int(downloads, 0) >= threshold or as_int(likes, 0) >= threshold // 10


def build_tags(*sources: Any) -> list[str]:
    """Merge heterogeneous tag sources into a de-duplicated bounded list."""
    out: list[str] = []
    for s in sources:
        out.extend(as_str_list(s, limit=64))
    deduped: list[str] = []
    seen: set[str] = set()
    for t in out:
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        deduped.append(t)
    return deduped[:64]


def curated_category(raw: Any) -> str:
    """Pass-through for curated entries (validated against the 10 categories)."""
    s = str(raw or "").strip()
    return s if s in CATEGORIES else "other"


__all__ = [
    "CATEGORIES",
    "HUB_CURATED",
    "LICENSE_CANON",
    "build_tags",
    "canonical_license",
    "curated_category",
    "infer_engines",
    "infer_engines_from_names",
    "known_engine_ids",
    "map_category",
    "reset_engine_ids_cache",
    "size_gb_from_bytes",
    "total_size_bytes",
    "trending_from",
]
