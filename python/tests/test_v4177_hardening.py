"""Regression tests for the v4.17.7 hardening pass.

Each test pins one concrete defect found in the v4.17.6 audit, so the fix
cannot silently regress. Grouped by the audit item it covers.
"""

from __future__ import annotations

import io
import os
import stat
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.asr_runtime import AsrParamError, _validate_model_arg  # noqa: E402
from app.downloader import (  # noqa: E402
    DEFAULT_MIN_FREE_BYTES,
    MIN_FREE_FLOOR_BYTES,
    Downloader,
    DownloadRefused,
    _check_url,
    _host_is_loopback,
)
from app.engines import _safe_extract_tar  # noqa: E402
from app.sources_registry import SourceRegistry  # noqa: E402
from app.webdav_sync import save_webdav_config  # noqa: E402

# ---------------------------------------------------------------------------
# P0-1 — downloader is default-deny (SECURITY.md said so; the code did not)
# ---------------------------------------------------------------------------


def test_unknown_host_is_refused_by_default():
    with pytest.raises(DownloadRefused):
        _check_url("https://attacker.example/collect")


def test_curated_host_is_allowed_by_default():
    _check_url("https://huggingface.co/owner/repo/resolve/main/m.bin")
    _check_url("https://hf-mirror.com/owner/repo/resolve/main/m.bin")


def test_explicit_opt_out_is_honoured():
    _check_url("https://attacker.example/collect", enforce_allowlist=False)


def test_credential_bearing_download_cannot_opt_out():
    """A Bearer token must never reach a host outside the curated set."""
    with pytest.raises(DownloadRefused):
        _check_url(
            "https://attacker.example/collect",
            enforce_allowlist=False,
            has_auth_header=True,
        )


def test_downloader_defaults_to_enforcing():
    assert Downloader(max_concurrent=1)._enforce_allowlist is True


def test_passing_allowed_hosts_always_enforces():
    dl = Downloader(max_concurrent=1, allowed_hosts={"only.example"},
                    enforce_allowlist=False)
    assert dl._enforce_allowlist is True


def test_loopback_is_exempt_from_allowlist_but_not_ssrf():
    assert _host_is_loopback("http://127.0.0.1:8080/x")
    assert _host_is_loopback("http://localhost/x")
    assert _host_is_loopback("http://[::1]/x")
    assert not _host_is_loopback("https://huggingface.co/x")
    # Loopback passes the allowlist...
    _check_url("http://127.0.0.1:8080/x")
    # ...but a private LAN address is still an SSRF target.
    with pytest.raises(DownloadRefused):
        _check_url("http://192.168.1.1/x")


# ---------------------------------------------------------------------------
# P0-2 — disk reserve must scale down on small volumes
# ---------------------------------------------------------------------------


def test_disk_reserve_is_capped_by_volume_size(tmp_path: Path):
    dl = Downloader(max_concurrent=1)
    reserve = dl._effective_reserve(tmp_path / "x.bin")
    total = __import__("shutil").disk_usage(str(tmp_path)).total
    assert reserve <= DEFAULT_MIN_FREE_BYTES
    assert reserve >= MIN_FREE_FLOOR_BYTES
    # On a small volume the reserve must be a small fraction, not the 2 GB cap.
    if total < 40 * 1024**3:
        assert reserve < DEFAULT_MIN_FREE_BYTES


def test_disk_reserve_zero_when_disabled(tmp_path: Path):
    dl = Downloader(max_concurrent=1, min_free_bytes=0)
    assert dl._effective_reserve(tmp_path / "x.bin") == 0


# ---------------------------------------------------------------------------
# P0-3 — ASR model argument validation (was relaxed to "non-empty")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alias", ["tiny", "base.en", "large-v3", "turbo",
                                   "large-v3-turbo"])
def test_asr_accepts_size_aliases(alias: str):
    assert _validate_model_arg(alias) == alias


@pytest.mark.parametrize("repo", ["Systran/faster-whisper-large-v3",
                                  "openai/whisper-tiny"])
def test_asr_accepts_repo_ids(repo: str):
    assert _validate_model_arg(repo) == repo


@pytest.mark.parametrize("bad", [
    "", "   ", "../../etc", "https://evil.example/x", "a/b/c",
    "/abs/path", "owner/../name", ".hidden/name", "owner/na me",
    "owner\\name", "owner/name?x=1",
])
def test_asr_rejects_bad_model_args(bad: str):
    with pytest.raises(AsrParamError):
        _validate_model_arg(bad)


# ---------------------------------------------------------------------------
# P1 — tar extraction must never create links or escape the target
# ---------------------------------------------------------------------------


def _tar_with(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> tarfile.TarFile:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for info, data in members:
            tf.addfile(info, io.BytesIO(data) if data is not None else None)
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode="r")


def _file(name: str, data: bytes = b"x") -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def test_safe_extract_writes_regular_files(tmp_path: Path):
    tf = _tar_with([_file("a/b.txt", b"hello")])
    _safe_extract_tar(tf, tmp_path)
    assert (tmp_path / "a" / "b.txt").read_bytes() == b"hello"


def test_safe_extract_skips_symlinks(tmp_path: Path):
    link = tarfile.TarInfo("evil")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    tf = _tar_with([(link, None), _file("ok.txt")])
    _safe_extract_tar(tf, tmp_path)
    assert not (tmp_path / "evil").exists()
    assert (tmp_path / "ok.txt").exists()


def test_safe_extract_skips_hardlinks_and_devices(tmp_path: Path):
    hard = tarfile.TarInfo("hard")
    hard.type = tarfile.LNKTYPE
    hard.linkname = "ok.txt"
    fifo = tarfile.TarInfo("fifo")
    fifo.type = tarfile.FIFOTYPE
    tf = _tar_with([(hard, None), (fifo, None), _file("ok.txt")])
    _safe_extract_tar(tf, tmp_path)
    assert not (tmp_path / "hard").exists()
    assert not (tmp_path / "fifo").exists()


def test_safe_extract_rejects_traversal(tmp_path: Path):
    tf = _tar_with([_file("../escape.txt")])
    with pytest.raises(ValueError):
        _safe_extract_tar(tf, tmp_path)
    assert not (tmp_path.parent / "escape.txt").exists()


# ---------------------------------------------------------------------------
# P1 — WebDAV config holds a cleartext password: must be owner-only
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_webdav_config_is_owner_only(tmp_path: Path):
    fp = save_webdav_config(tmp_path, {"url": "https://dav.example",
                                       "user": "u", "password": "s3cret"})
    mode = stat.S_IMODE(fp.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_webdav_config_tightens_a_pre_existing_loose_file(tmp_path: Path):
    fp = tmp_path / "webdav.json"
    fp.write_text("{}")
    os.chmod(fp, 0o644)
    save_webdav_config(tmp_path, {"url": "https://dav.example", "password": "x"})
    assert stat.S_IMODE(fp.stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# P1 — the phishing typosquat must not ship as a selectable preset
# ---------------------------------------------------------------------------


def test_phishing_mirror_is_not_a_preset():
    reg = SourceRegistry(persist_path=None)
    assert reg.get("hf-cdn-sufy-com") is None
    # ...and it is still hard-blocked if a user pastes it in.
    with pytest.raises(DownloadRefused):
        _check_url("https://hf-cdn.sufy.com/owner/repo.bin")


def test_dead_mirrors_remain_but_disabled():
    reg = SourceRegistry(persist_path=None)
    for dead in ("hf-mirror-us", "hf-cn-mirror-com", "huggingface-dl-in-tel"):
        src = reg.get(dead)
        assert src is not None and src.enabled is False
