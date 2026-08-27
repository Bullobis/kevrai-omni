"""Catalog schema validation: enforces structure + URL allowlists.

This file sits ON TOP of ``python/app/catalog.py`` (Pydantic) so we catch both
*structural* defects (missing field, wrong type, broken URL pattern) and
*policy* defects (a model repo pointed at a non-allowlisted host) at build time.

All models, engines, and GGUF repos are enumerated and checked individually.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

# We deliberately do NOT add the catalog/ directory as a Python package, so
# import the schema module by file location.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from catalog.schema import (  # noqa: E402 — sys.path magic above
    ALLOWED_ENGINE_HOSTS,
    ALLOWED_MODEL_HOSTS,
    BLOCKED_MIRRORS,
    validate_engines as validate_engines_schema,
    validate_models as validate_models_schema,
)
from app.catalog import (  # noqa: E402
    is_host_allowed,
    load_catalog,
)

CATALOG_DIR = REPO_ROOT / "catalog"
MODELS_PATH = CATALOG_DIR / "models.json"
ENGINES_PATH = CATALOG_DIR / "engines.json"


# ---------- helpers ----------

def _host_of(url: str) -> str:
    if not url:
        return ""
    h = urlparse(url).hostname or ""
    if h.startswith("www."):
        h = h[4:]
    return h.lower()


def _platform_keys() -> tuple[str, ...]:
    return ("windows-x64", "linux-x64", "darwin-arm64", "darwin-x64")


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([\-+][0-9A-Za-z.\-]+)?$")


# ---------- whole-file structural validation ----------

def test_models_json_is_valid_json():
    with MODELS_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, dict)
    assert "models" in data
    assert "version" in data


def test_engines_json_is_valid_json():
    with ENGINES_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, dict)
    assert "engines" in data
    assert isinstance(data["engines"], list)


def test_models_pass_jsonschema():
    """Run jsonschema validator against the whole models.json document."""
    data = json.loads(MODELS_PATH.read_text(encoding="utf-8"))
    errs = validate_models_schema(data)
    assert not errs, "models.json schema violations:\n" + "\n".join(errs[:20])


def test_engines_pass_jsonschema():
    """Run jsonschema validator against the whole engines.json document."""
    data = json.loads(ENGINES_PATH.read_text(encoding="utf-8"))
    errs = validate_engines_schema(data)
    assert not errs, "engines.json schema violations:\n" + "\n".join(errs[:20])


# ---------- per-model checks ----------

@pytest.fixture(scope="module")
def models_data():
    return json.loads(MODELS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def engines_data():
    return json.loads(ENGINES_PATH.read_text(encoding="utf-8"))


def test_every_model_id_is_unique(models_data):
    ids = [m["id"] for m in models_data["models"]]
    dups = {x for x in ids if ids.count(x) > 1}
    assert not dups, f"duplicate model ids: {sorted(dups)}"


def test_every_model_has_required_fields(models_data):
    required = {"id", "category", "name"}
    for m in models_data["models"]:
        missing = required - set(m.keys())
        assert not missing, f"model {m.get('id')!r} missing fields: {missing}"


_HF_OWNER_REPO = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")
# Repo can also be a single-segment identifier (e.g. insightface's "buffalo_l").
_NON_SLUG_OK = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _is_safe_hf_slug(slug: str) -> bool:
    """Models are referenced as ``owner/repo`` (HF implicit) OR a single-segment
    identifier (some models like ``insightface`` use model-only handles).

    The check excludes any segment that contains a blocked mirror string.
    Empty string is fine (means: no repo).
    """
    if not slug:
        return True
    if "/" in slug:
        if not _HF_OWNER_REPO.match(slug):
            return False
    else:
        if not _NON_SLUG_OK.match(slug):
            return False
    for part in slug.split("/"):
        for bad in BLOCKED_MIRRORS:
            if bad in part.lower():
                return False
    return True


def test_every_model_repo_is_safe_hf_slug(models_data):
    """For every model, ``repo`` (if set) must be a safe slug."""
    bad: list[tuple[str, str]] = []
    for m in models_data["models"]:
        slug = (m.get("repo") or "").strip()
        if not slug:
            continue
        if not _is_safe_hf_slug(slug):
            bad.append((m.get("id", "?"), slug))
    assert not bad, (
        "models with malformed or blocklisted repo slugs: "
        + ", ".join(f"{mid} -> {u}" for mid, u in bad[:5])
    )


def test_every_model_gguf_repo_is_safe_hf_slug(models_data):
    """For every model, ``gguf_repo`` (if set) must be a safe slug."""
    bad: list[tuple[str, str]] = []
    for m in models_data["models"]:
        slug = (m.get("gguf_repo") or "").strip()
        if not slug:
            continue
        if not _is_safe_hf_slug(slug):
            bad.append((m.get("id", "?"), slug))
    assert not bad, (
        "models with malformed or blocklisted gguf_repo: "
        + ", ".join(f"{mid} -> {u}" for mid, u in bad[:5])
    )


def test_every_gguf_repo_owner_repo_is_safe_hf_slug(models_data):
    """The catalog-level ``gguf_repos`` list must point at safe HF owner/repo."""
    bad = []
    for g in models_data.get("gguf_repos", []):
        slug = (g.get("owner_repo") or "").strip()
        if not slug:
            continue
        if not _is_safe_hf_slug(slug):
            bad.append((g.get("id", "?"), slug))
    assert not bad, f"gguf_repos outside HF (unsafe slug): {bad[:5]}"


def test_every_model_has_known_category(models_data):
    allowed = {"llm", "tts", "video", "image", "superres", "audio", "3d", "vision", "pending"}
    bad = [(m.get("id", "?"), m.get("category")) for m in models_data["models"]
           if m.get("category") not in allowed]
    assert not bad, f"models with unknown category: {bad[:5]}"


def test_size_gb_when_present_is_non_negative(models_data):
    for m in models_data["models"]:
        if "size_gb" in m:
            assert m["size_gb"] >= 0, f"{m['id']}: negative size_gb"


def test_gguf_repos_have_owner_repo_shape(models_data):
    for g in models_data.get("gguf_repos", []):
        orr = g.get("owner_repo") or ""
        assert "/" in orr, f"bad owner_repo: {orr!r}"
        owner, _, repo = orr.partition("/")
        assert owner and repo, f"owner or repo empty: {orr!r}"


def test_every_gguf_repo_field_is_safe_slug(models_data):
    """Sanity: every catalog-level gguf_repo has owner/repo slug (HF implicit)."""
    for g in models_data.get("gguf_repos", []):
        orr = (g.get("owner_repo") or "").strip()
        assert _HF_OWNER_REPO.match(orr), f"bad owner_repo slug: {orr!r}"


def test_no_model_field_contains_a_blocked_mirror_substring(models_data):
    """Belt-and-suspenders: every model entry's serialized form is scanned
    for any of the known phishing mirror substrings. They MUST NOT appear."""
    bad = []
    for m in models_data["models"]:
        blob = json.dumps(m, ensure_ascii=False).lower()
        for blocked in BLOCKED_MIRRORS:
            if blocked in blob:
                bad.append((m.get("id", "?"), blocked))
    assert not bad, f"model has blocked mirror in any field: {bad[:5]}"


# ---------- per-engine checks ----------

def test_every_engine_id_is_unique(engines_data):
    ids = [e["id"] for e in engines_data["engines"]]
    dups = {x for x in ids if ids.count(x) > 1}
    assert not dups, f"duplicate engine ids: {sorted(dups)}"


def test_every_engine_has_required_fields(engines_data):
    required = {"id", "name"}
    for e in engines_data["engines"]:
        missing = required - set(e.keys())
        assert not missing, f"engine {e.get('id')!r} missing fields: {missing}"


def test_every_engine_platform_url_is_well_formed(engines_data):
    """v2.2.0: any http(s) URL is acceptable; we just check parseability."""
    from urllib.parse import urlparse
    bad: list[tuple[str, str, str]] = []
    for e in engines_data["engines"]:
        plats = e.get("platforms") or {}
        for plat_name, url in plats.items():
            if not url:
                continue
            p = urlparse(url)
            if p.scheme not in ("http", "https") or not p.netloc:
                bad.append((e.get("id", "?"), plat_name, url))
    assert not bad, (
        "engine platforms with malformed URLs: "
        + ", ".join(f"{e}/{p} -> {u}" for e, p, u in bad[:5])
    )


def test_no_engine_url_is_blocked_mirror(engines_data):
    bad = []
    for e in engines_data["engines"]:
        for plat, url in (e.get("platforms") or {}).items():
            for blocked in BLOCKED_MIRRORS:
                if blocked in url.lower():
                    bad.append((e.get("id", "?"), plat, blocked))
    assert not bad, f"engine uses blocked mirror: {bad[:5]}"


def test_every_engine_version_field_if_present_is_semver(engines_data):
    """Engines may carry a ``version`` field (optional); when present it must
    look like semver. Engines without an explicit version just inherit whatever
    upstream release artifact they point at — that's allowed."""
    for e in engines_data["engines"]:
        v = e.get("version")
        if v is None:
            continue
        assert isinstance(v, str) and _SEMVER_RE.match(v), (
            f"engine {e.get('id')!r} has malformed version {v!r}"
        )


def test_every_engine_github_field_shape(engines_data):
    for e in engines_data["engines"]:
        gh = e.get("github", "")
        if not gh:
            continue
        assert "/" in gh and " " not in gh, f"bad github field: {gh!r} on engine {e.get('id')}"


# ---------- cross-cutting ----------

def test_catalog_loads_cleanly_through_pydantic():
    """Pydantic catalog loader must accept these JSON files without errors."""
    catalog, engines = load_catalog(CATALOG_DIR)
    assert len(catalog.models) >= 50
    assert engines
