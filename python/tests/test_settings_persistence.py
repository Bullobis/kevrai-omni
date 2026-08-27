"""Settings persistence tests: roundtrip, corruption, atomic-write hygiene."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from app.settings import Settings, load_settings, save_settings


def test_save_load_roundtrip(tmp_path: Path):
    fp = tmp_path / "settings.json"
    s = Settings(theme="dark", max_concurrent_downloads=8, telemetry_enabled=True)
    save_settings(s, fp)
    loaded = load_settings(fp)
    assert loaded.theme == "dark"
    assert loaded.max_concurrent_downloads == 8
    assert loaded.telemetry_enabled is True


def test_save_load_unicode_values(tmp_path: Path):
    fp = tmp_path / "settings.json"
    s = Settings(model_dir="/data/模型")
    save_settings(s, fp)
    loaded = load_settings(fp)
    assert loaded.model_dir == "/data/模型"


def test_corrupt_json_falls_back_to_defaults(tmp_path: Path):
    fp = tmp_path / "settings.json"
    fp.write_text("{not valid json", encoding="utf-8")
    s = load_settings(fp)
    assert s.theme == "system"
    assert s.max_concurrent_downloads == 3


def test_empty_file_falls_back_to_defaults(tmp_path: Path):
    fp = tmp_path / "settings.json"
    fp.write_text("", encoding="utf-8")
    s = load_settings(fp)
    assert s.theme == "system"


def test_file_with_only_garbage_falls_back(tmp_path: Path):
    fp = tmp_path / "settings.json"
    fp.write_text("just text, no json", encoding="utf-8")
    s = load_settings(fp)
    # json.loads raises, defaults returned
    assert s.theme == "system"


def test_atomic_write_does_not_leave_tmp_on_success(tmp_path: Path):
    """Successful save must NOT leave ``.tmp`` files lying around."""
    fp = tmp_path / "settings.json"
    s = Settings(theme="light")
    save_settings(s, fp)
    assert fp.exists()
    # No .tmp / .partial files anywhere in the dir
    leftovers = [p.name for p in tmp_path.iterdir()
                 if p.name.startswith(".settings") or p.name.endswith(".tmp")]
    assert not leftovers, f"atomic write leaked: {leftovers}"


def test_atomic_write_uses_tmp_file_pattern(tmp_path: Path):
    """Even though the temp file is cleaned up on success, the success-path
    code MUST use the temp-file + rename pattern (not in-place rewrite)."""
    import inspect
    from app import settings as settings_mod

    src = inspect.getsource(settings_mod.save_settings)
    # Must include mkstemp/rename pattern, not direct write
    assert "mkstemp" in src or "NamedTemporaryFile" in src, (
        "save_settings must use a temp-file + rename pattern for atomicity"
    )
    assert "os.replace" in src or "replace(" in src, (
        "save_settings must use os.replace for atomicity"
    )


def test_unknown_extra_keys_are_preserved(tmp_path: Path):
    """Keys not in the schema are kept under ``extra``."""
    fp = tmp_path / "settings.json"
    fp.write_text(
        json.dumps({"theme": "dark", "experimental": True, "rate": 0.5}, ensure_ascii=False),
        encoding="utf-8",
    )
    s = load_settings(fp)
    assert s.theme == "dark"
    # 'experimental' is unknown → falls through to extra and survives round-trip
    s2 = Settings(theme=s.theme, extra=s.extra)
    save_settings(s2, fp)
    again = json.loads(fp.read_text(encoding="utf-8"))
    assert "experimental" in again or "rate" in again


def test_invalid_literal_falls_back_to_defaults(tmp_path: Path):
    """A known field with an invalid value forces default-recovery path."""
    fp = tmp_path / "settings.json"
    fp.write_text(json.dumps({"theme": "ultraviolet"}), encoding="utf-8")
    s = load_settings(fp)
    assert s.theme == "system"


def test_settings_resolved_paths_use_defaults(tmp_path: Path):
    """Empty ``model_dir`` → resolved to default location."""
    s = Settings()
    md = s.resolved_model_dir()
    assert isinstance(md, Path)
    assert md.name == "models"


def test_save_then_corrupt_then_load_yields_defaults(tmp_path: Path):
    """Cycle: save → corrupt → load → must default."""
    fp = tmp_path / "settings.json"
    save_settings(Settings(theme="dark"), fp)
    fp.write_text("!!! corruption !!!", encoding="utf-8")
    s = load_settings(fp)
    assert s.theme == "system"


def test_save_writes_with_atomic_rename_under_parent(tmp_path: Path):
    """Temp file is created in the SAME directory as the target (so the
    final rename is atomic and on the same filesystem)."""
    import inspect
    from app import settings as settings_mod
    src = inspect.getsource(settings_mod.save_settings)
    assert "dir=" in src and "fp.parent" in src, (
        "save_settings should put its temp file in the same directory"
    )
