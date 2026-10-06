"""Regression tests guarding against duplicate catalog entries by repo slug."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DATA = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))
_MODELS = _DATA["models"]


def test_no_two_models_share_primary_repo():
    seen: dict[str, list[str]] = defaultdict(list)
    for m in _MODELS:
        if m.get("category") == "pending":
            continue
        repo = (m.get("repo") or "").strip().lower()
        if repo:
            seen[repo].append(m["id"])
    dupes = {r: ids for r, ids in seen.items() if len(ids) > 1}
    assert not dupes, f"duplicate repo slugs: {dupes}"


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
