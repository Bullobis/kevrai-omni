"""Tests for catalog loading and v2.2.0 permissive multi-source policy."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.catalog import (
    ALLOWED_ENGINE_HOSTS,
    ALLOWED_MODEL_HOSTS,
    DEFAULT_BLOCKED_MIRRORS,
    DEFAULT_MODEL_HOSTS,
    Catalog,
    is_host_allowed,
    load_catalog,
)

CATALOG_DIR = Path(__file__).resolve().parent.parent.parent / "catalog"


def test_catalog_loads():
    cat, engines = load_catalog(CATALOG_DIR)
    assert cat.version, "catalog version missing"
    assert len(cat.models) >= 50, f"need >=50 models, got {len(cat.models)}"
    assert engines, "engines manifest empty"


def test_all_categories_have_models():
    cat, _ = load_catalog(CATALOG_DIR)
    cats = {m.category for m in cat.models}
    required = {"llm", "tts", "video", "image", "superres", "audio", "3d", "pending"}
    missing = required - cats
    assert not missing, f"missing categories: {missing}"


def test_blocked_mirror_in_models_now_accepted():
    """v2.2.0: no host is refused by the catalog. Even typosquat domains
    (e.g. ``hf-cdn.sufy.com``) parse cleanly so the user can opt in via
    the in-app Settings → Download sources panel."""
    bad = {
        "version": "1.0",
        "models": [{
            "id": "bad", "category": "llm", "name": "bad",
            "repo": "hf-cdn.sufy.com/some/repo",
        }],
        "gguf_repos": [],
    }
    cat = Catalog.model_validate(bad)
    assert cat.models[0].repo == "hf-cdn.sufy.com/some/repo"


def test_is_host_allowed_truth_table():
    """v2.2.0: allowlist is *advisory*; is_host_allowed still returns
    accurate membership for the curated default set, but a host outside the
    set simply returns False (it is not "blocked", it is just not
    recognised by the curated default)."""
    for ok in ["huggingface.co", "cdn-lfs.huggingface.co", "hf-mirror.com",
               "github.com", "mirrors.aliyun.com", "pypi.org"]:
        assert is_host_allowed(f"https://{ok}/x", DEFAULT_MODEL_HOSTS)
    for bad in ["someshadysite.example.com", "evil.example.com"]:
        # NOT in the default allowlist (advisory, not enforced).
        assert not is_host_allowed(f"https://{bad}/x", DEFAULT_MODEL_HOSTS)
    # www. prefix is stripped
    assert is_host_allowed("https://www.huggingface.co/x", DEFAULT_MODEL_HOSTS)


def test_engine_host_whitelist():
    for ok in ["github.com", "pypi.org", "mirrors.tencent.com", "raw.githubusercontent.com"]:
        assert is_host_allowed(f"https://{ok}/x", ALLOWED_ENGINE_HOSTS)


def test_default_blocked_mirrors_empty():
    """v2.2.0: the global blocklist is empty by design. The user opts in
    to any mirror via the in-app UI."""
    assert DEFAULT_BLOCKED_MIRRORS == set()


def test_every_model_has_multi_source_mirrors():
    """v2.2.0: every model in the shipped catalog exposes a sources[] list
    so the downloader can speed-test and pick the fastest mirror.

    `pending` models (waiting for an official open-source release) are
    exempt — by definition they have no real repo yet.
    """
    cat, _ = load_catalog(CATALOG_DIR)
    missing = [m.id for m in cat.models
               if m.category != "pending" and not (m.sources or [])]
    assert not missing, f"non-pending models without sources[]: {missing[:10]}"
    # For non-pending models, at least 2 mirrors each (official + mirror).
    short = [m.id for m in cat.models
             if m.category != "pending" and len(m.sources or []) < 2]
    assert not short, f"non-pending models with <2 sources: {short[:10]}"


def test_no_duplicate_model_ids():
    cat, _ = load_catalog(CATALOG_DIR)
    ids = [m.id for m in cat.models]
    dups = {x for x in ids if ids.count(x) > 1}
    assert not dups, f"duplicate ids: {dups}"


def test_gguf_repo_entries_have_owner_repo():
    cat, _ = load_catalog(CATALOG_DIR)
    assert cat.gguf_repos, "gguf_repos must be non-empty"
    for g in cat.gguf_repos:
        assert "/" in g.owner_repo, f"bad gguf repo: {g.owner_repo!r}"
        # v2.2.0: gguf_repos also expose sources[] for multi-mirror download.
        assert g.sources, f"gguf_repo without sources: {g.id}"


def test_pending_minimax_2k_present():
    cat, _ = load_catalog(CATALOG_DIR)
    pend = [m for m in cat.models if m.category == "pending"]
    assert pend, "must have at least one pending entry"
    assert any("MiniMax" in m.name or "minimax" in (m.description or "").lower() for m in pend), \
        "MiniMax 2K pending entry missing"