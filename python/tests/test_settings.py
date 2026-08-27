"""Tests for app.settings — atomic persistence, defaults, fallback."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.settings import (
    Settings,
    default_cache_root,
    default_data_root,
    default_settings_path,
    ensure_dirs,
    load_settings,
    save_settings,
)


def test_default_root_path_present():
    root = default_data_root()
    assert root.name == "KevraiOmni"


def test_cache_root_path_present():
    cache = default_cache_root()
    assert cache.name == "KevraiOmni"


def test_settings_basic():
    s = Settings(theme="dark")
    assert s.theme == "dark"
    assert s.telemetry_enabled is False
    assert 1 <= s.max_concurrent_downloads <= 16


def test_settings_invalid_theme_rejected():
    with pytest.raises(ValidationError):
        Settings(theme="rainbow")


def test_settings_cap_concurrency_below_one():
    s = Settings(max_concurrent_downloads=-5)
    assert s.max_concurrent_downloads == 1


def test_settings_cap_concurrency_above_sixteen():
    s = Settings(max_concurrent_downloads=9999)
    assert s.max_concurrent_downloads == 16


def test_settings_cap_size_below_one():
    s = Settings(max_model_size_gb=0)
    assert s.max_model_size_gb == 1


def test_settings_cap_size_above_four_tb():
    s = Settings(max_model_size_gb=10_000)
    assert s.max_model_size_gb == 4096


def test_save_and_load_roundtrip(tmp_path: Path):
    fp = tmp_path / "settings.json"
    s = Settings(theme="dark", telemetry_enabled=True, max_concurrent_downloads=7,
                 default_engine_id="vllm")
    save_settings(s, fp)
    s2 = load_settings(fp)
    assert s2.theme == "dark"
    assert s2.telemetry_enabled is True
    assert s2.max_concurrent_downloads == 7
    assert s2.default_engine_id == "vllm"


def test_load_settings_missing_returns_defaults(tmp_path: Path):
    fp = tmp_path / "missing.json"
    s = load_settings(fp)
    assert s.theme in {"light", "dark", "system"}


def test_load_settings_corrupt_falls_back(tmp_path: Path):
    fp = tmp_path / "settings.json"
    fp.write_text("definitely not JSON {{{")
    s = load_settings(fp)
    assert s.theme in {"light", "dark", "system"}


def test_load_settings_invalid_value_falls_back(tmp_path: Path):
    fp = tmp_path / "settings.json"
    fp.write_text(json.dumps({"theme": "rainbow"}))
    s = load_settings(fp)
    assert s.theme == "system"


def test_save_settings_atomic_no_leftover_tmp(tmp_path: Path):
    fp = tmp_path / "settings.json"
    save_settings(Settings(theme="dark"), fp)
    leftover = list(tmp_path.glob("*.tmp"))
    assert not leftover


def test_save_settings_overwrites_existing(tmp_path: Path):
    fp = tmp_path / "settings.json"
    save_settings(Settings(theme="light"), fp)
    save_settings(Settings(theme="dark"), fp)
    s = load_settings(fp)
    assert s.theme == "dark"


def test_ensure_dirs_creates_dirs(tmp_path: Path, monkeypatch):
    """ensure_dirs should not raise even when the dirs already exist."""
    s = Settings(model_dir=str(tmp_path / "a"),
                 engine_dir=str(tmp_path / "b"),
                 download_dir=str(tmp_path / "c"))
    paths = ensure_dirs(s)
    for p in paths:
        assert p.exists()


def test_resolved_dirs_use_settings_when_present(tmp_path: Path):
    s = Settings(model_dir=str(tmp_path / "m"),
                 engine_dir=str(tmp_path / "e"),
                 download_dir=str(tmp_path / "d"))
    assert s.resolved_model_dir() == tmp_path / "m"
    assert s.resolved_engine_dir() == tmp_path / "e"
    assert s.resolved_download_dir() == tmp_path / "d"


def test_resolved_dirs_have_defaults_when_empty():
    s = Settings()
    # When fields are blank, fall back to default_data_root() layout.
    md = s.resolved_model_dir()
    assert md.parent == default_data_root() or md.parent.parent == default_data_root()


def test_settings_partial_update(tmp_path: Path):
    fp = tmp_path / "settings.json"
    save_settings(Settings(theme="light", telemetry_enabled=False), fp)
    s = load_settings(fp)
    # Mutate just one field and save
    s2 = s.model_copy()
    s2.theme = "dark"
    save_settings(s2, fp)
    s3 = load_settings(fp)
    assert s3.theme == "dark"
    # boolean fields use Pydantic v2 defaults; ensure file is well-formed
    json.loads(fp.read_text())
