"""JSON integrity tests — every JSON file in the repo must be valid JSON.

We don't fix invalid JSON here; we ASSERT, so a CI run catches a typo
the moment it lands. Failure mode is a clear ``pytest.fail`` listing the
offending path + the parser error.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _all_json_files() -> list[Path]:
    out: list[Path] = []
    for sub in ("catalog", "python", "renderer", "electron", "."):
        base = REPO_ROOT / sub
        if not base.exists():
            continue
        for p in base.rglob("*.json"):
            if "__pycache__" in p.parts:
                continue
            if "node_modules" in p.parts:
                continue
            out.append(p)
    return sorted(out)


@pytest.fixture(scope="module")
def json_files() -> list[Path]:
    return _all_json_files()


def test_at_least_one_json_file(json_files):
    """Sanity: the repo isn't empty of JSON files."""
    assert json_files, "no .json files found under catalog/, python/, renderer/, electron/"


def test_package_json_is_valid(json_files):
    """Top-level package.json must parse (electron-builder / npm rely on it)."""
    pkg = REPO_ROOT / "package.json"
    assert pkg.exists(), "package.json missing"
    data = json.loads(pkg.read_text(encoding="utf-8"))
    assert data.get("name"), "package.json: missing 'name'"
    assert data.get("version"), "package.json: missing 'version'"


def test_catalog_models_json_is_valid(json_files):
    p = REPO_ROOT / "catalog" / "models.json"
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "models" in data
    assert isinstance(data["models"], list)
    assert len(data["models"]) > 0


def test_catalog_engines_json_is_valid(json_files):
    p = REPO_ROOT / "catalog" / "engines.json"
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "engines" in data
    assert isinstance(data["engines"], list)
    assert len(data["engines"]) > 0


@pytest.mark.parametrize("path", [str(p.relative_to(REPO_ROOT)) for p in _all_json_files()])
def test_every_json_parses(path: str):
    """Run once per JSON file; the parametrize list stays in sync with disk."""
    p = REPO_ROOT / path
    assert p.exists(), f"{path} disappeared"
    try:
        json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        pytest.fail(f"{path}: JSON parse error: {e}")


def test_pyproject_toml_is_valid_toml():
    """pyproject.toml is not JSON, but we still verify it's at least valid TOML
    if tomllib is available."""
    import sys
    if sys.version_info < (3, 11):
        pytest.skip("tomllib requires Python 3.11+")
    import tomllib
    p = REPO_ROOT / "python" / "pyproject.toml"
    assert p.exists()
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    assert "project" in data
    assert "name" in data["project"]
