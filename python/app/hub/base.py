"""Core abstractions for the Kevrai dual-source model hub (HF + ModelScope).

Everything in this module is **pure**: no network, no filesystem, and no
imports from the rest of ``app`` (so it can never create a circular import,
and every helper is unit-testable offline).

Why a separate ``RemoteModel`` instead of reusing ``catalog.ModelEntry``:

* ``ModelEntry.id`` has a validator (``catalog.py`` ``_id_safe``) restricted to
  ``^[A-Za-z0-9._-]{1,128}$``. Remote ids look like ``deepseek-ai/DeepSeek-V3``
  and contain ``/`` — constructing a ``ModelEntry`` with one raises
  ``pydantic.ValidationError``.
* ``ModelEntry.source`` is grouped with ``repo``/``primary_url`` by the
  ``_url_optional`` validator, so it must never carry an enum such as ``"hf"``.
  Remote provenance therefore lives in the dedicated ``hub`` field.

``RemoteModel.to_dict()`` is a deliberate **superset** of
``ModelEntry.model_dump()`` so the existing renderer (``models.js``
``renderCard()``) can display remote cards with zero changes.
"""
from __future__ import annotations

import contextlib
import hashlib
import html
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Hub identifiers
# ---------------------------------------------------------------------------

HUB_CURATED = "curated"
HUB_HF = "hf"
HUB_MODELSCOPE = "modelscope"

ALL_HUBS: tuple[str, ...] = (HUB_CURATED, HUB_HF, HUB_MODELSCOPE)
REMOTE_HUBS: tuple[str, ...] = (HUB_HF, HUB_MODELSCOPE)

_HUB_PREFIX: dict[str, str] = {
    HUB_CURATED: "cur",
    HUB_HF: "hf",
    HUB_MODELSCOPE: "ms",
}

_HUB_DISPLAY: dict[str, str] = {
    HUB_CURATED: "本地精选",
    HUB_HF: "HuggingFace",
    HUB_MODELSCOPE: "魔搭 ModelScope",
}

# Mirrors ``app.main._REPO_RE`` — "owner/name", no leading dot, no "..".
REPO_RE = re.compile(
    r"^(?![.])(?!.*\.\.)[A-Za-z0-9._-]{1,64}/(?![.])[A-Za-z0-9._-]{1,128}$"
)
# Mirrors ``app.main._MODEL_ID_RE`` — every synthesized id must satisfy it.
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# The 9 legacy categories plus the new "other" bucket (§1.6 of the design).
CATEGORIES: tuple[str, ...] = (
    "llm", "tts", "video", "image", "superres",
    "audio", "3d", "vision", "pending", "other",
)

SORTS: tuple[str, ...] = ("relevance", "downloads", "likes", "recent", "name_asc")


def hub_display(hub: str) -> str:
    """Human-readable label for a hub id (unknown ids are echoed back)."""
    return _HUB_DISPLAY.get(hub, hub)


def synth_id(hub: str, repo: str) -> str:
    """Build a DOM/route-safe id from a remote ``owner/name`` repo.

    Deterministic (sha1 of ``"{hub}/{repo}"``) so the same model always gets
    the same synthetic id across pages — required for cross-page de-duplication
    and for the renderer's selected-state keying.
    """
    prefix = _HUB_PREFIX.get(hub, re.sub(r"[^A-Za-z0-9]", "", hub)[:4] or "x")
    digest = hashlib.sha1(f"{hub}/{repo}".encode()).hexdigest()[:16]  # noqa: S324 — non-crypto id
    return f"{prefix}-{digest}"


def is_valid_repo(repo: Any) -> bool:
    """True when ``repo`` is a well-formed ``owner/name`` (no traversal)."""
    return isinstance(repo, str) and REPO_RE.fullmatch(repo) is not None


def normalize_repo_key(repo: Any) -> str:
    """Lower-cased ``owner/name`` used as the cross-source dedupe key."""
    s = str(repo or "").strip().strip("/").lower()
    return s


# ---------------------------------------------------------------------------
# Defensive coercion helpers (remote payloads are never trusted)
# ---------------------------------------------------------------------------


def pick(obj: Any, *keys: str, default: Any = None) -> Any:
    """Return the first present, non-``None`` key from a mapping.

    Tolerates ``None`` / non-mapping / non-sequence inputs so a malformed
    upstream payload can never raise inside the normalizer.
    """
    if not isinstance(obj, Mapping):
        return default
    for k in keys:
        if k in obj:
            v = obj[k]
            if v is not None:
                return v
    return default


def as_int(value: Any, default: int = 0) -> int:
    """Best-effort int coercion (handles ``"12,345"`` and floats).

    Never raises: ``json.loads`` accepts ``Infinity``/``NaN`` literals, and
    ``int(float("inf"))`` raises ``OverflowError``. A single dirty field must
    not be able to discard a whole page of otherwise-valid results.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else default
    if isinstance(value, str):
        s = value.replace(",", "").replace(" ", "").strip()
        try:
            return int(s)
        except ValueError:
            try:
                f = float(s)
            except ValueError:
                return default
            return int(f) if math.isfinite(f) else default
    return default


def as_float(value: Any, default: float = 0.0) -> float:
    """Best-effort float coercion that never raises."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.replace(",", "").strip()
        try:
            return float(s)
        except ValueError:
            return default
    return default


def as_bool(value: Any, default: bool = False) -> bool:
    """Best-effort bool coercion."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"true", "1", "yes", "y", "on"}:
            return True
        if s in {"false", "0", "no", "n", "off", ""}:
            return False
    return default


def as_str_list(value: Any, limit: int = 64, item_limit: int = 128) -> list[str]:
    """Flatten an arbitrary upstream value into a bounded list of strings.

    Strings are *not* exploded character-by-character (a bare ``str`` becomes a
    single element) — this is the guard for extreme case E05.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()[:item_limit]] if value.strip() else []
    if isinstance(value, Mapping):
        items: list[Any] = list(value.keys())
    elif isinstance(value, Sequence):
        items = list(value)
    elif isinstance(value, Iterable):
        items = []
        for i, v in enumerate(value):
            if i >= limit:
                break
            items.append(v)
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for v in items:
        if isinstance(v, Mapping):
            v = pick(v, "Name", "name", "value", "tag", default="")
        s = str(v or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s[:item_limit])
        if len(out) >= limit:
            break
    return out


def clamp_int(value: Any, default: int, low: int, high: int) -> int:
    """Coerce to int then clamp into ``[low, high]`` (E07/E08)."""
    return max(low, min(high, as_int(value, default)))


def clamp_str(value: Any, limit: int) -> str:
    """Coerce to str and truncate to ``limit`` characters."""
    return str(value or "")[:limit]


#: 降级原因 → 面向用户的中文解释。key 与 ``FetchOutcome.code`` 对齐。
_DEGRADED_REASONS: dict[str, str] = {
    "local_limited": "本机请求过于频繁，已暂时限流",
    "circuit_open": "连续失败过多，已暂时熔断",
    "timeout": "请求超时",
    "network": "网络不可达",
    "http_error": "上游返回错误",
    "bad_json": "上游返回内容无法解析",
    "not_found": "未找到",
}


def degraded_message(hub_label: str, code: str) -> str:
    """Build a user-facing degradation notice for a failed upstream call.

    The distinction matters: ``local_limited`` is **our own** token bucket
    refusing to send the request, not an upstream outage. Reporting it as
    「检索失败」 is misleading — the user would reasonably retry forever.
    """
    reason = _DEGRADED_REASONS.get(str(code or ""), "未知原因")
    return f"{hub_label} 暂时不可用：{reason}（{code or 'unknown'}）"


def as_iso(value: Any) -> str:
    """Normalize an upstream timestamp to ISO-8601 UTC (never raises).

    Upstreams disagree on the shape: HF sends ``"2026-08-05T08:22:59.000Z"``
    (already ISO) while 魔搭 sends Unix **seconds** as an int
    (``1789461232`` — verified live). Both are accepted; anything unusable
    becomes ``""`` so the UI shows nothing rather than ``"None"``.
    """
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)) or value <= 0:
            return ""
        try:
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return ""
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    s = str(value).strip()
    if not s:
        return ""
    # Already ISO-ish ("2026-08-05T08:22:59.000Z") — keep the date+time, drop
    # the fractional seconds, and normalize the trailing zone marker.
    m = _ISO_RE.match(s)
    if m:
        return f"{m.group(1)}Z"
    # A numeric string from an upstream that serialized ints as text.
    try:
        f = float(s)
    except (ValueError, TypeError):
        # Anything else (including a stringified list/dict) is not a timestamp.
        # Returning it verbatim would leak "[]" / "{}" into the UI.
        return ""
    return as_iso(f)


def flatten_rich_text(value: Any, limit: int = 4000) -> str:
    """Flatten a rich-text AST (or plain string) into readable text.

    魔搭's ``Organization.Description`` is **not** plain text — it is a Slate
    style node tree, verified live::

        ["root", {}, ["p", {}, ["span", {}, ["span", {}, "欢迎来到 Qwen 👋"]]]]

    Stringifying that directly would leak JSON punctuation into the UI, so the
    leaves are walked in order and joined. Plain strings pass through unchanged.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    out: list[str] = []

    def walk(node: Any, depth: int) -> None:
        if len(out) >= 400 or depth > 12:
            return
        if isinstance(node, str):
            # Skip the AST's own tag names / empty leaves.
            s = node.strip()
            if s:
                out.append(s)
            return
        if isinstance(node, Sequence):
            # Convention: [tag, attrs, ...children] — the first two entries are
            # structural for a non-empty array, but a bare list of children is
            # also tolerated (we only strip when the shape matches).
            start = 0
            if (len(node) >= 2 and isinstance(node[0], str)
                    and isinstance(node[1], Mapping)):
                start = 2
            for child in list(node)[start:]:
                walk(child, depth + 1)
            return
        if isinstance(node, Mapping):
            for key in ("text", "value", "children", "content"):
                if key in node:
                    walk(node[key], depth + 1)
                    return

    walk(value, 0)
    joined = " ".join(out)
    return re.sub(r"\s+", " ", joined).strip()[:limit]


#: ISO-8601 with optional fractional seconds and zone suffix.
_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.\d+)?Z?$")


def split_owner(repo: str) -> str:
    """``"Qwen/Qwen3-8B"`` → ``"Qwen"`` (HF's list ``author`` is often ``None``)."""
    s = str(repo or "").strip()
    return s.split("/", 1)[0][:128] if "/" in s else ""


#: Fenced code block (```lang ... ```) — removed wholesale.
_FENCE_RE = re.compile(r"```.*?```", re.S)
#: Inline HTML tag (the HF cards are full of `<a href=…><img …/></a>` badges).
_HTML_TAG_RE = re.compile(r"<[^>]{0,400}>")
#: Text-bearing anchor `<a href="URL">label</a>` → `label (URL)`. Anchors that
# only wrap an <img> (badge "shields" / logo) have no text and are dropped by
# the repl. href may use single or double quotes (back-referenced); only http(s)
# links are surfaced. Non-greedy body so adjacent anchors don't merge.
_HTML_ANCHOR_RE = re.compile(
    r"<a\b[^>]*?href=(['\"])(?P<href>[^'\"]*?)\1[^>]*?>(?P<body>.*?)</a>",
    re.I | re.S,
)
#: Leading ATX heading / blockquote / list markers.
_MD_LINE_PREFIX_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s*|>\s*|[-*+]\s+|\d+[.)]\s+)")
#: `![alt](url)` and `[text](url)` → alt / text.
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
#: `**bold**`, `__bold__`, `*em*`, `` `code` `` → inner text.
_MD_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3}|`+)(.+?)\1", re.S)
#: YAML front-matter delimited by `---` at the very start of the document.
_FRONT_MATTER_RE = re.compile(r"\A\s*---\s*\n.*?\n---\s*\n", re.S)


def strip_markdown(text: Any, limit: int = 4000) -> str:
    """Reduce Markdown (incl. HF YAML front-matter) to plain readable prose.

    Both upstreams hand back a raw ``README.md`` — HF's even starts with a YAML
    front-matter block (``---\\nlibrary_name: transformers\\n---``) that must not
    be shown as body text. This is a deliberately lossy, display-only cleaner;
    it never raises and always returns a bounded string.
    """
    s = str(text or "")
    if not s.strip():
        return ""
    s = _FRONT_MATTER_RE.sub("", s)
    s = _FENCE_RE.sub(" ", s)

    # Text anchors first: keep the label and append the URL in parentheses so
    # nav rows (ModelScope/HuggingFace/Blog/Demo/…) don't degrade into a list of
    # orphan labels. Image-only badge/logo anchors have no text and are removed.
    def _anchor_repl(m: re.Match[str]) -> str:
        href = (m.group("href") or "").strip()
        label = _HTML_TAG_RE.sub(" ", m.group("body") or "")
        label = re.sub(r"\s+", " ", label).strip()
        if not label:
            return " "
        if href.lower().startswith(("http://", "https://")):
            return f"{label} ({href})"
        return label

    s = _HTML_ANCHOR_RE.sub(_anchor_repl, s)
    s = _HTML_TAG_RE.sub(" ", s)
    s = _MD_IMAGE_RE.sub(r"\1", s)
    s = _MD_LINK_RE.sub(r"\1", s)
    s = _MD_EMPHASIS_RE.sub(r"\2", s)
    # Decode HTML entities (&nbsp; &amp; &#39; &hellip; …) only now, after every
    # tag has been removed, so a decoded entity can never reintroduce markup.
    s = html.unescape(s)
    lines = [_MD_LINE_PREFIX_RE.sub("", ln).strip() for ln in s.splitlines()]
    joined = " ".join(ln for ln in lines if ln)
    return re.sub(r"\s+", " ", joined).strip()[:limit]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RemoteFile:
    """One file in a remote repo, normalized across HF and ModelScope."""

    path: str = ""
    size: int = 0
    sha256: str = ""
    is_lfs: bool = False
    download_url: str = ""
    type: str = "file"

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "size": int(self.size),
            "sha256": self.sha256,
            "is_lfs": bool(self.is_lfs),
            "download_url": self.download_url,
            "type": self.type,
        }


@dataclass
class RemoteModel:
    """Source-agnostic model record (superset of ``ModelEntry.model_dump()``)."""

    # --- fields the existing renderer reads (same names/semantics) ---
    id: str = ""
    name: str = ""
    description: str = ""
    category: str = "other"
    license: str = ""
    size_gb: float = 0.0
    engine: list[str] = field(default_factory=list)
    trending: bool = False
    repo: str = ""
    hardware: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    modality: dict[str, Any] = field(default_factory=dict)
    # --- legacy ModelEntry slots kept present so `to_dict()` is a superset ---
    files: list[str] = field(default_factory=list)
    size_bytes: int = 0
    source: str = ""
    sources: list[str] = field(default_factory=list)
    primary_url: str = ""
    gguf_repo: str = ""
    mnn_repo: str = ""
    # --- new hub fields ---
    hub: str = HUB_CURATED
    size_known: bool = False
    downloads: int = 0
    likes: int = 0
    revision: str = ""
    task: str = ""
    also_on: list[str] = field(default_factory=list)
    import_only: bool = False
    engine_confidence: str = "low"
    # --- upstream provenance / popularity (v2.9.0) ---
    # Every field below is parsed from a key confirmed present by live probe of
    # the real upstream API (see `modelscope.py` / `hf.py` module docstrings).
    # Nothing here is invented; when an upstream omits the key the field stays
    # at its neutral default and the UI simply shows nothing.
    owner: str = ""            # 公司/组织：HF `author` / 魔搭 Organization
    owner_url: str = ""        # 组织头像（仅魔搭提供）
    owner_full_name: str = ""  # 组织中文名（魔搭 Organization.FullName）
    nickname: str = ""         # 上传者昵称（魔搭 NickName）
    name_cn: str = ""          # 模型中文名（魔搭 ChineseName）
    trending_score: int = 0    # 真实热度分（仅 HF `trendingScore`）
    library: str = ""          # 主框架（HF `library_name`）
    frameworks: list[str] = field(default_factory=list)
    architectures: list[str] = field(default_factory=list)
    created_at: str = ""       # ISO-8601 UTC
    updated_at: str = ""       # ISO-8601 UTC
    is_hot: bool = False       # 上游标记（魔搭 IsHot / HF trending 阈值）
    is_new: bool = False       # 上游标记（魔搭 IsNewModel）

    def __post_init__(self) -> None:
        # Synthesize a safe id when the caller only supplied a repo.
        if not self.id and self.repo:
            self.id = synth_id(self.hub, self.repo)
        if not self.name:
            self.name = self.repo.rsplit("/", 1)[-1] if self.repo else (self.id or "未命名")

    @property
    def dedupe_key(self) -> str:
        """Cross-source dedupe key (``"hub:owner/name"`` lower-cased)."""
        return f"{self.hub}:{normalize_repo_key(self.repo)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "license": self.license,
            "size_gb": round(float(self.size_gb), 3),
            "engine": list(self.engine),
            "trending": bool(self.trending),
            "repo": self.repo,
            "hardware": dict(self.hardware),
            "tags": list(self.tags),
            "modality": dict(self.modality),
            # legacy ModelEntry slots (kept so `renderCard`/`renderDetail`
            # never read `undefined`)
            "files": list(self.files),
            "size_bytes": int(self.size_bytes),
            "source": self.source,
            "sources": list(self.sources),
            "primary_url": self.primary_url,
            "gguf_repo": self.gguf_repo,
            "mnn_repo": self.mnn_repo,
            # hub-specific
            "hub": self.hub,
            "hub_display": hub_display(self.hub),
            "size_known": bool(self.size_known),
            "downloads": int(self.downloads),
            "likes": int(self.likes),
            "revision": self.revision,
            "task": self.task,
            "also_on": list(self.also_on),
            "import_only": bool(self.import_only),
            "engine_confidence": self.engine_confidence,
            # v2.9.0 — upstream provenance / popularity. `remote` is an explicit
            # flag the renderer uses to decide whether to render upstream-only
            # rows (the curated catalog has none of these and would otherwise
            # show a wall of empty labels).
            "owner": self.owner,
            "owner_url": self.owner_url,
            "owner_full_name": self.owner_full_name,
            "nickname": self.nickname,
            "name_cn": self.name_cn,
            "trending_score": int(self.trending_score),
            "library": self.library,
            "frameworks": list(self.frameworks),
            "architectures": list(self.architectures),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_hot": bool(self.is_hot),
            "is_new": bool(self.is_new),
            "remote": self.hub in REMOTE_HUBS,
        }


@dataclass
class SearchSpec:
    """A normalized, already-validated search request."""

    q: str = ""
    category: str = ""
    engine: str = ""
    license: str = ""
    sort: str = "relevance"
    page_size: int = 30
    sources: list[str] = field(default_factory=lambda: list(ALL_HUBS))

    def normalized(self) -> SearchSpec:
        """Return a copy with values clamped/whitelisted (E07/E08)."""
        sort = self.sort if self.sort in SORTS else "relevance"
        srcs = [s for s in (self.sources or []) if s in ALL_HUBS]
        if not srcs:
            srcs = list(ALL_HUBS)
        # de-dup, keep canonical order
        ordered = [h for h in ALL_HUBS if h in srcs]
        return SearchSpec(
            q=clamp_str(self.q, 200).strip(),
            category=clamp_str(self.category, 64).strip(),
            engine=clamp_str(self.engine, 64).strip(),
            license=clamp_str(self.license, 128).strip(),
            sort=sort,
            page_size=clamp_int(self.page_size, 30, 1, 100),
            sources=ordered,
        )

    def signature(self) -> str:
        """Fingerprint of the query+filters — guards cursor reuse (§2.3)."""
        raw = "|".join([
            self.q.strip().lower(),
            self.category.strip().lower(),
            self.engine.strip().lower(),
            self.license.strip().lower(),
            self.sort,
            str(self.page_size),
            ",".join(self.sources),
        ])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]  # noqa: S324 — non-crypto cache key


@dataclass
class SourceCursor:
    """Per-source pagination position."""

    page: int = 0        # 0 == "first page, nothing consumed yet"
    offset: int = 0      # used by the curated (local) adapter
    token: str = ""      # opaque upstream cursor (HF `x-next-cursor`)
    done: bool = False

    def next_page(self) -> int:
        return max(1, self.page) + 1 if self.page else 2

    def to_dict(self) -> dict[str, int | str | bool]:
        return {"page": self.page, "offset": self.offset,
                "token": self.token, "done": self.done}

    @classmethod
    def from_dict(cls, d: Any) -> SourceCursor:
        if not isinstance(d, Mapping):
            return cls()
        return cls(
            page=clamp_int(d.get("page"), 0, 0, 100_000),
            offset=clamp_int(d.get("offset"), 0, 0, 1_000_000),
            token=clamp_str(d.get("token"), 512),
            done=as_bool(d.get("done"), False),
        )


@dataclass
class PageResult:
    """What a single adapter returns for one page."""

    items: list[RemoteModel] = field(default_factory=list)
    next: SourceCursor | None = None
    total: int | None = None
    degraded: bool = False
    warning: str = ""
    # "ok" | "timeout" | "http_error" | "not_found" | "rate_limited" |
    # "circuit_open" | "local_limited" | "bad_json" | "network" | "empty"
    code: str = "ok"

    @property
    def ok(self) -> bool:
        return not self.degraded

    def degraded_with(self, code: str, message: str = "") -> PageResult:
        """Return an empty, degraded result (used when a source fails)."""
        return PageResult(items=[], next=None, total=None,
                          degraded=True, code=code, warning=message)


class SourceAdapter(ABC):
    """Uniform retrieval surface: search / detail / files / resolve."""

    hub: str = ""
    display_name: str = ""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        client: Any | None = None,
        settings: Any | None = None,
    ) -> None:
        self.base_url = base_url or ""
        self._client = client
        self._client_owned = client is None
        self._settings = settings
        self._closed = False

    # --- abstract surface -------------------------------------------------

    @abstractmethod
    async def search(self, spec: SearchSpec, cursor: SourceCursor) -> PageResult:
        """Return one page of results for ``spec`` starting at ``cursor``."""

    @abstractmethod
    async def detail(self, repo: str) -> RemoteModel:
        """Return the full record for a single ``owner/name`` repo."""

    @abstractmethod
    async def files(self, repo: str, revision: str = "") -> list[RemoteFile]:
        """Return the file listing for ``repo``."""

    @abstractmethod
    def resolve_url(self, repo: str, path: str, revision: str = "") -> str:
        """Build the direct download URL for one file."""

    # --- optional surface -------------------------------------------------

    def auth_headers(self) -> dict[str, str]:
        """Extra headers for authenticated (private/gated) access."""
        return {}

    def health(self) -> dict[str, Any]:
        """Structured status for ``GET /api/hub/sources``."""
        return {"hub": self.hub, "display_name": self.display_name,
                "enabled": True, "online": self.hub == HUB_CURATED,
                "circuit": "closed", "token_required": False, "token_set": False}

    async def aclose(self) -> None:
        """Release the adapter's HTTP client (no-op for local adapters)."""
        if self._client is not None and self._client_owned:
            with contextlib.suppress(Exception):  # best-effort client close
                await self._client.aclose()  # type: ignore[attr-defined]
        self._closed = True
