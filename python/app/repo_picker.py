"""Pick which files of a model repo should actually be downloaded.

为什么需要这个模块
------------------
``POST /api/hub/download`` 要求调用方显式给出 ``files`` 列表（缺失即 400
``files required``）。对 GGUF 仓库没问题——前端先让用户选量化档，选完就有
具体的分片路径。但目录里 259 个模型中有 **165 个没有 gguf_repo**，它们只有
普通的 HF 仓库（safetensors / bin / onnx）。对这些模型，安装流程走到
「下载」这一步时手里没有任何 files，于是：

  * 旧行为：只装引擎，然后弹一个「正在安装引擎」的 toast —— **权重从未被下载**。
    用户以为装好了，打开本地模型目录却是空的。
  * ``HubDownloadReq.auto_pick`` 这个名字有误导性：它做的是**换源**（在多个
    镜像里挑最快的 URL），不是**挑文件**。所以把它设成 True 也救不了这个流程。

本模块补上「挑文件」这一环：给定仓库的完整文件清单，挑出真正需要的权重 +
配置，跳过 fp32 副本、原始检查点、flax/tf 变体这些又大又用不上的东西。
"""

from __future__ import annotations

import re
from typing import Any

# 权重文件扩展名，按常见度排序。
WEIGHT_EXTS: tuple[str, ...] = (
    ".safetensors", ".gguf", ".bin", ".onnx", ".mnn",
    ".pt", ".pth", ".ckpt", ".npz", ".msgpack",
)

# 必须一起下载的配套文件（缺了模型跑不起来）。
_ESSENTIAL_NAMES: frozenset[str] = frozenset({
    "config.json",
    "generation_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "tokenizer.model",
    "vocab.json",
    "vocab.txt",
    "merges.txt",
    "special_tokens_map.json",
    "added_tokens.json",
    "preprocessor_config.json",
    "processor_config.json",
    "chat_template.json",
    "model_index.json",      # diffusers 管线
    "scaling_factor.json",
})

# 这些目录/前缀永远不下载：原始检查点、示例、文档、测试。
_SKIP_DIR_PREFIXES: tuple[str, ...] = (
    ".git/", ".github/", "original/", "assets/", "docs/", "examples/",
    "tests/", "test/", "images/", "figures/", "runs/", "logs/",
)

# 这些变体绝大多数本地运行时用不到，且通常体积巨大。
# 注意：这里**不要**无条件排除 .gguf —— 有些仓库（尤其社区量化转存）的权重
# 本身就是 gguf。排除策略见 ``_prefer_primary_format``：只有在同一仓库里
# 存在其他格式的权重时，才把 gguf 视为"附带物"丢掉。
_SKIP_NAME_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^flax_model",      # JAX/Flax 变体
        r"^tf_model",        # TensorFlow 变体
        r"^model\.h5$",      # Keras
        r"^rust_model",      # Rust 变体
        r"^pytorch_model.*\.bin$",  # 与 safetensors 重复
        r"\.msgpack$",       # flax 权重
        r"^optimizer\.pt$",
        r"^rng_state\.pth$",
        r"^trainer_state\.json$",
        r"^training_args\.bin$",
        r"^scheduler\.pt$",
    )
)

# 分片权重：model-00001-of-00032.safetensors
_SHARD_RE = re.compile(
    r"^(?P<stem>.+?)-(?P<idx>\d{5})-of-(?P<total>\d{5})(?P<ext>\.[a-z0-9]+)$",
    re.IGNORECASE,
)

# 单文件分片索引合并进 index json 的情况（HF 常用 model.safetensors.index.json）
_INDEX_JSON_RE = re.compile(r"\.index\.json$", re.IGNORECASE)


def _norm(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("/")


def _basename(path: str) -> str:
    return _norm(path).rsplit("/", 1)[-1]


def _is_skipped_dir(path: str) -> bool:
    """True when any path segment is a known non-model directory.

    逐段比较而不是整串前缀匹配：``assets/`` 只在顶层出现时前缀匹配能命中，
    但仓库里更常见的是 ``some/sub/assets/...`` 这种嵌套形式。
    """
    segs = [s.lower() + "/" for s in _norm(path).split("/")[:-1]]
    skip = {pre.lower() for pre in _SKIP_DIR_PREFIXES}
    return any(s in skip for s in segs)


def _is_weight(path: str) -> bool:
    return _norm(path).lower().endswith(WEIGHT_EXTS)


def _is_essential(path: str) -> bool:
    return _basename(path).lower() in _ESSENTIAL_NAMES or bool(_INDEX_JSON_RE.search(_norm(path)))


def _is_discarded(path: str) -> bool:
    name = _basename(path)
    return any(pat.search(name) for pat in _SKIP_NAME_PATTERNS)


def _shard_group(path: str) -> tuple[str, str] | None:
    """Return (stem, ext) for a sharded weight file, else None."""
    m = _SHARD_RE.match(_basename(path))
    if not m:
        return None
    return (m.group("stem").lower(), m.group("ext").lower())


def _prefer_primary_format(weights: list[str]) -> list[str]:
    """Drop ``.gguf`` weights when the repo also ships another weight format.

    一个 safetensors 仓库里常附带社区转存的 gguf（体积不小、且量化档应由
    前端的量化选择器单独处理）。但如果 gguf 就是仓库里唯一的权重格式，
    就必须保留 —— 否则整个挑选结果为空，又回到"什么都没下载"的老问题。

    只对**权重文件**做这个比较。若把 config.json 这类非权重文件也算进
    "存在其他格式"，一个只含 gguf 的仓库会被误判成"有其他格式"而把
    唯一权重丢掉。
    """
    non_gguf = [p for p in weights if not p.lower().endswith(".gguf")]
    return non_gguf if non_gguf else list(weights)


def pick_repo_files(
    files: list[dict[str, Any]] | list[Any],
    *,
    max_files: int = 200,
) -> list[str]:
    """Choose the files worth downloading from a repo listing.

    Accepts either ``/api/hub/model/files`` dicts (``{"path": ..., "size": ...}``)
    or bare path strings. Returns the chosen paths, in a stable order:
    essential metadata first, then weights (shards in numeric order).

    The selection is deliberately conservative — it never invents a path that
    is not in the listing, and an unrecognisable repo yields an empty list
    rather than "everything" (downloading a whole repo can be tens of GB).
    """
    paths: list[str] = []
    sizes: dict[str, int] = {}
    for f in files or []:
        if isinstance(f, str):
            p, size = f, 0
        elif isinstance(f, dict):
            p = f.get("path") or f.get("name") or ""
            size = int(f.get("size") or 0)
        else:
            continue
        p = _norm(p)
        if not p:
            continue
        paths.append(p)
        sizes[p] = size

    if not paths:
        return []

    essential: list[str] = []
    weight_groups: dict[tuple[str, str], list[str]] = {}
    plain_weights: list[str] = []

    for p in paths:
        if _is_skipped_dir(p):
            continue
        if _is_essential(p):
            essential.append(p)
            continue
        if not _is_weight(p):
            continue
        if _is_discarded(p):
            continue
        g = _shard_group(p)
        if g:
            weight_groups.setdefault(g, []).append(p)
        else:
            plain_weights.append(p)

    # 同一组分片按文件名排序即等于按索引排序（零填充保证字典序 = 数值序）。
    chosen: list[str] = []
    for group in weight_groups.values():
        chosen.extend(sorted(group))

    # 单文件权重：按体积从大到小取，避免把一堆小废弃物也算进来。
    plain_weights.sort(key=lambda p: (-sizes.get(p, 0), p))
    chosen.extend(plain_weights)

    if not chosen:
        return []

    # gguf 与其他权重格式共存时只留其他格式（见 _prefer_primary_format）。
    chosen = _prefer_primary_format(chosen)

    ordered = essential + chosen
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for p in ordered:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out[:max_files]


def total_size_of(files: list[dict[str, Any]] | list[Any], paths: list[str]) -> int:
    """Sum the sizes of ``paths`` from a listing. Unknown paths count as 0."""
    sizes: dict[str, int] = {}
    for f in files or []:
        if isinstance(f, dict):
            p = _norm(f.get("path") or f.get("name") or "")
            if p:
                sizes[p] = int(f.get("size") or 0)
    want = {_norm(p) for p in paths}
    return sum(v for k, v in sizes.items() if k in want)
