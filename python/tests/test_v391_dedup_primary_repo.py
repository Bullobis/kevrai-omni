"""Regression tests guarding against duplicate catalog entries by repo slug."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DATA = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))
_MODELS = _DATA["models"]


def _key(m: dict) -> tuple[str, ...]:
    """判重的键：仓库 + 权重文件。

    为什么不能只看仓库：一个 HF 仓库里**本来就可能有多个不同模型的权重**。
    例如 ``AEmotionStudio/roformer-models`` 同时托管
    ``bs_roformer/vocals_viperx/``（SDR 12.97）和
    ``mel_band_roformer/vocals_viperx/``（SDR 11.43）——它们是两个不同的模型，
    只是放在同一个仓库的不同子目录。只看仓库会把它们误判成重复登记。

    所以判据是 **仓库 + files**：
      * files 非空 → 用 (repo, 排序后的 files) 作键，同仓不同权重不算重复；
      * files 为空 → 退化成只看 repo，**并且**要求这种情况真的少见。
    这样既拦住了"同一模型登记两次"，又不误伤"一仓多权重"。
    """
    repo = (m.get("repo") or "").strip().lower()
    files = tuple(sorted(str(f).strip().lower() for f in (m.get("files") or [])))
    return (repo,) + files if files else (repo,)


def test_no_two_models_share_primary_repo():
    """同一仓库 + 同一组权重文件，不得登记两次。"""
    seen: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for m in _MODELS:
        if m.get("category") == "pending":
            continue
        repo = (m.get("repo") or "").strip().lower()
        if repo:
            seen[_key(m)].append(m["id"])
    dupes = {r: ids for r, ids in seen.items() if len(ids) > 1}
    assert not dupes, f"duplicate repo+files: {dupes}"


def test_shared_repo_entries_declare_distinct_files():
    """共享同一仓库的条目，必须各自声明 files 来证明它们是不同模型。

    守的是上面那条判据不被滥用：如果有人图省事把两个模型都指向同一个仓库
    却不填 files，判据就会退化成"只看 repo"从而漏判。这里要求——只要一个
    仓库被多个条目引用，每个条目都必须带 files。
    """
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for m in _MODELS:
        if m.get("category") == "pending":
            continue
        repo = (m.get("repo") or "").strip().lower()
        if repo:
            by_repo[repo].append(m)
    bad: list[str] = []
    for repo, group in by_repo.items():
        if len(group) < 2:
            continue
        for m in group:
            if not (m.get("files") or []):
                bad.append(f"{m['id']} (repo={repo}) 缺 files")
    assert not bad, "共享仓库的条目必须声明 files：\n" + "\n".join(bad)


def test_no_two_models_share_gguf_repo():
    seen: dict[str, list[str]] = defaultdict(list)
    for m in _MODELS:
        if m.get("category") == "pending":
            continue
        repo = (m.get("gguf_repo") or "").strip().lower()
        if repo:
            seen[repo].append(m["id"])
    dupes = {r: ids for r, ids in seen.items() if len(ids) > 1}
    assert not dupes, f"duplicate gguf repos: {dupes}"


def test_known_duplicates_removed():
    ids = {m["id"] for m in _MODELS}
    assert "flux2-klein-4b" not in ids
    assert "nemotron3-diarization" not in ids
    assert "bonsai-2-27b" not in ids


def test_distinct_bonsai_variants_kept():
    by = {m["id"]: m for m in _MODELS}
    # original Bonsai (binary, separate repo) and the Ternary release both remain
    assert by["bonsai-27b"]["repo"] == "prism-ml/Bonsai-27B-gguf"
    assert by["ternary-bonsai-2-27b"]["repo"] == "prism-ml/Ternary-Bonsai-2-27B-gguf"
