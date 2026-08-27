"""Tests for mirror candidate expansion (auto-pick source generation)."""
from __future__ import annotations

import pytest

from app.sources import expand_mirror_candidates

MIRRORS = [
    "https://hf-mirror.com",
    "https://hf-mirror.us",
    "https://hf-cdn.sufy.com",
    "https://huggingface.dl.in.tel",
    "https://hf-cn-mirror.com",
]


def test_expand_keeps_primary_first():
    primary = "https://huggingface.co/Qwen/Qwen2-0.5B-Instruct/resolve/main/model.safetensors"
    out = expand_mirror_candidates(primary, MIRRORS)
    assert out[0] == primary


def test_expand_swaps_host_onto_every_mirror():
    primary = "https://huggingface.co/owner/repo/resolve/main/file.bin"
    out = expand_mirror_candidates(primary, MIRRORS)
    # primary + 5 mirrors
    assert len(out) == 6
    hosts = {u.split("/")[2] for u in out}
    assert "hf-mirror.com" in hosts
    assert "hf-cdn.sufy.com" in hosts
    assert "hf-mirror.us" in hosts
    # path preserved on every mirror
    for u in out[1:]:
        assert u.endswith("/owner/repo/resolve/main/file.bin")


def test_expand_dedups_primary_among_mirrors():
    # If the primary is already a mirror, that mirror must not be duplicated.
    primary = "https://hf-mirror.com/owner/repo/resolve/main/file.bin"
    out = expand_mirror_candidates(primary, MIRRORS)
    assert out.count(primary) == 1
    assert len(out) == len(set(u.lower() for u in out))


def test_expand_ignores_non_hf_primary():
    # A github.com URL is not HF-path-compatible; no mirror variants generated.
    primary = "https://github.com/owner/repo/archive/refs/tags/v1.zip"
    out = expand_mirror_candidates(primary, MIRRORS)
    assert out == [primary]


def test_expand_empty_mirrors_returns_primary():
    primary = "https://huggingface.co/a/b/c"
    assert expand_mirror_candidates(primary, []) == [primary]
    assert expand_mirror_candidates("", MIRRORS) == []


def test_expand_handles_cdn_lfs_host():
    primary = "https://cdn-lfs.huggingface.co/a/b/c"
    out = expand_mirror_candidates(primary, MIRRORS)
    assert len(out) == 6
    assert out[0] == primary
