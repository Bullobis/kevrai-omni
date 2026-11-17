"""v4.18.2 regression: archive extraction must preserve executable bits
and internal symlinks (soname chains), while still rejecting escapes.

Live verification found llama-server extracted as 0644 with its soname
symlinks dropped — every GGUF chat failed ("Permission denied", then
"cannot open shared object file").
"""
from __future__ import annotations

import io
import stat
import tarfile
import zipfile
from pathlib import Path


def test_tar_extraction_preserves_exec_bit(tmp_path: Path):
    from app.engines import _safe_extract_tar

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"#!/bin/sh\necho hi\n"
        m = tarfile.TarInfo("bin/tool")
        m.size = len(data)
        m.mode = 0o755
        tf.addfile(m, io.BytesIO(data))
        m2 = tarfile.TarInfo("readme.txt")
        m2.size = 3
        m2.mode = 0o644
        tf.addfile(m2, io.BytesIO(b"abc"))
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        _safe_extract_tar(tf, tmp_path)
    tool = tmp_path / "bin" / "tool"
    assert tool.stat().st_mode & stat.S_IXUSR, "exec bit lost on tar member"
    assert not (tmp_path / "readme.txt").stat().st_mode & stat.S_IXUSR


def test_tar_extraction_masks_setuid(tmp_path: Path):
    from app.engines import _safe_extract_tar

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        m = tarfile.TarInfo("evil")
        m.size = 1
        m.mode = 0o4755  # setuid must NOT survive
        tf.addfile(m, io.BytesIO(b"x"))
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        _safe_extract_tar(tf, tmp_path)
    assert not (tmp_path / "evil").stat().st_mode & stat.S_ISUID


def test_zip_extraction_preserves_exec_bit(tmp_path: Path):
    from app.hub.paths import safe_extract

    zpath = tmp_path / "a.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        info = zipfile.ZipInfo("bin/tool")
        info.create_system = 3  # Unix
        info.external_attr = (0o755 & 0xFFFF) << 16
        zf.writestr(info, "#!/bin/sh\necho hi\n")
        info2 = zipfile.ZipInfo("readme.txt")
        info2.create_system = 3
        info2.external_attr = (0o644 & 0xFFFF) << 16
        zf.writestr(info2, "abc")
    with zipfile.ZipFile(zpath) as zf:
        safe_extract(zf, tmp_path / "out")
    out = tmp_path / "out"
    assert (out / "bin" / "tool").stat().st_mode & stat.S_IXUSR
    assert not (out / "readme.txt").stat().st_mode & stat.S_IXUSR


def test_tar_internal_symlink_allowed_external_rejected(tmp_path: Path):
    from app.engines import _safe_extract_tar

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"ELF..."
        m = tarfile.TarInfo("lib/libfoo.so.1.0.0")
        m.size = len(data)
        m.mode = 0o755
        tf.addfile(m, io.BytesIO(data))
        link = tarfile.TarInfo("lib/libfoo.so.1")
        link.type = tarfile.SYMTYPE
        link.linkname = "libfoo.so.1.0.0"
        tf.addfile(link)
        evil = tarfile.TarInfo("lib/evil")
        evil.type = tarfile.SYMTYPE
        evil.linkname = "/etc/passwd"
        tf.addfile(evil)
        escape = tarfile.TarInfo("lib/escape")
        escape.type = tarfile.SYMTYPE
        escape.linkname = "../../outside"
        tf.addfile(escape)
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        _safe_extract_tar(tf, tmp_path)
    assert (tmp_path / "lib" / "libfoo.so.1").is_symlink()
    assert (tmp_path / "lib" / "libfoo.so.1").resolve() == (tmp_path / "lib" / "libfoo.so.1.0.0").resolve()
    assert not (tmp_path / "lib" / "evil").exists()
    assert not (tmp_path / "lib" / "escape").exists()


def test_zip_internal_symlink_allowed_external_rejected(tmp_path: Path):
    from app.hub.paths import safe_extract

    zpath = tmp_path / "s.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("lib/libfoo.so.1.0.0", "ELF...")
        info = zipfile.ZipInfo("lib/libfoo.so.1")
        info.create_system = 3
        info.external_attr = (0o120777 & 0xFFFF) << 16
        zf.writestr(info, "libfoo.so.1.0.0")
        bad = zipfile.ZipInfo("lib/evil")
        bad.create_system = 3
        bad.external_attr = (0o120777 & 0xFFFF) << 16
        zf.writestr(bad, "/etc/passwd")
    with zipfile.ZipFile(zpath) as zf:
        safe_extract(zf, tmp_path / "out")
    out = tmp_path / "out"
    assert (out / "lib" / "libfoo.so.1").is_symlink()
    assert not (out / "lib" / "evil").exists()
