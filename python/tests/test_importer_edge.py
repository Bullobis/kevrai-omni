"""Importer edge cases: empty files, missing inputs, symlinks, huge payloads.

Both APIs under test:
    * ``import_local``          — dict-shaped return (legacy-compatible)
    * ``import_local_struct``   — dataclass return (typed)
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _make_models_dir(tmp_path: Path) -> Path:
    md = tmp_path / "models"
    md.mkdir(exist_ok=True)
    return md


def test_empty_file_accepted_with_zero_size(tmp_path: Path):
    """Zero-byte file is technically valid bytes; we just expose size=0."""
    from app.importer import import_local
    src = tmp_path / "empty.gguf"
    src.write_bytes(b"")
    res = import_local(src, _make_models_dir(tmp_path))
    assert res["size_bytes"] == 0
    assert res["sha256"]  # still produced
    assert res["duplicate"] is False


def test_0_byte_directory_accepted(tmp_path: Path):
    """An empty directory has no files -> total size 0; same treatment."""
    from app.importer import import_local
    src = tmp_path / "emptydir"
    src.mkdir()
    res = import_local(src, _make_models_dir(tmp_path))
    assert res["size_bytes"] == 0
    assert res["path"].endswith("emptydir")


def test_directory_with_non_utf8_name_handled(tmp_path: Path):
    """Some filesystems allow non-UTF-8 bytes in filenames; the importer
    should not crash when given such a source."""
    from app.importer import import_local, load_local_registry

    src = tmp_path / "srcdir"
    src.mkdir()
    bad_name = b"mod\xffl.gguf".decode("utf-8", errors="surrogateescape")
    f = src / bad_name
    try:
        f.write_bytes(b"x" * 100)
    except (OSError, UnicodeEncodeError):
        pytest.skip("filesystem rejects this name on this platform")

    md = _make_models_dir(tmp_path)
    res = import_local(src, md)
    assert Path(res["path"]).exists()
    reg = load_local_registry(md)
    assert any(r["path"] == res["path"] for r in reg)


def test_symlink_loop_detected_or_short_circuits(tmp_path: Path):
    """A symlink pointing back at its parent directory could explode on rglob.

    The call must return (success or controlled error) without an infinite loop.
    """
    from app.importer import import_local
    src = tmp_path / "loopy"
    src.mkdir()
    real = src / "weights.gguf"
    real.write_bytes(b"x" * 100)

    loop = src / "loop"
    try:
        os.symlink(str(src), str(loop))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")

    md = _make_models_dir(tmp_path)
    # If symlink-resolution blew up, we accept OSError/FileExistsError.
    try:
        res = import_local(src, md)
        # Sane return
        assert isinstance(res["size_bytes"], int)
    except (OSError, FileExistsError):
        pass


def test_oversized_file_rejected(tmp_path: Path):
    """Caller passes a small ``max_size_bytes`` — import is refused."""
    from app.importer import import_local
    src = tmp_path / "big.gguf"
    src.write_bytes(b"x" * (10 * 1024 * 1024))  # 10 MiB
    md = _make_models_dir(tmp_path)

    with pytest.raises(ValueError) as exc:
        import_local(src, md, max_size_bytes=1024)
    msg = str(exc.value).lower()
    assert "too large" in msg or "cap" in msg


def test_copy_vs_symlink_modes(tmp_path: Path):
    """``mode='symlink'`` creates a symlink (same FS) OR falls back to copy."""
    from app.importer import import_local
    src = tmp_path / "srcfile.gguf"
    src.write_bytes(b"GGUF" * 1024)
    md = _make_models_dir(tmp_path)
    res = import_local(src, md, mode="symlink")
    assert Path(res["path"]).exists()
    assert Path(res["path"]).stat().st_size == src.stat().st_size
    # Either mode=symlink (success) or fallback to copy
    assert res["mode"] in {"symlink", "copy"}


def test_default_mode_is_copy(tmp_path: Path):
    """No `mode=` argument → mode is 'copy' in the result."""
    from app.importer import import_local
    src = tmp_path / "srcfile2.gguf"
    src.write_bytes(b"hello" * 200)
    md = _make_models_dir(tmp_path)
    res = import_local(src, md)
    assert res["mode"] == "copy"
    assert not Path(res["path"]).is_symlink()


def test_idempotent_duplicate_flag(tmp_path: Path):
    """Importing twice returns duplicate=True on the second call."""
    from app.importer import import_local
    src = tmp_path / "dup.gguf"
    src.write_bytes(b"x" * 256)
    md = _make_models_dir(tmp_path)
    first = import_local(src, md)
    second = import_local(src, md)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["path"] == second["path"]
    assert first["id"] == second["id"]


def test_struct_return_form_matches_dict(tmp_path: Path):
    """`import_local_struct` returns a dataclass with the same fields."""
    from app.importer import import_local, import_local_struct
    src = tmp_path / "struct.gguf"
    src.write_bytes(b"x" * 1024)
    md = _make_models_dir(tmp_path)
    d = import_local(src, md)
    s = import_local_struct(src, md)
    # Both must expose the same fields
    assert d["size_bytes"] == s.size_bytes
    assert d["sha256"] == s.sha256
    assert s.duplicate is True  # second call


def test_import_local_missing_source_raises(tmp_path: Path):
    from app.importer import import_local
    with pytest.raises(FileNotFoundError):
        import_local(tmp_path / "does-not-exist", _make_models_dir(tmp_path))
