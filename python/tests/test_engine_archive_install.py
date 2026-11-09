"""引擎安装器：归档识别 / tar 解压 / GitHub 资产管理 的回归测试。

对应手工复现的问题：
* ``.tar.gz`` 明明不是 zip，却走 zip 解压分支 → "File is not a zip file"
* ``.tar.zst`` 用错临时文件名 → 解压必失败
* GitHub 源码归档带一层 ``repo-main/``，不展平则引擎目录里找不到入口
* 仓库页只拼 ``main`` 分支，ComfyUI（master）直接 404
"""

from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import engines as eng  # noqa: E402


@pytest.mark.parametrize("url,expected", [
    ("https://x/y.zip", True),
    ("https://x/y.tar.gz", True),
    ("https://x/y.tgz", True),
    ("https://x/y.tar.zst", True),
    ("https://github.com/user/repo", False),
    ("https://github.com/user/repo/releases/latest", False),
    ("https://github.com/user/repo/releases/download/v1/a.zip", True),
])
def test_looks_like_archive_url(url, expected):
    assert eng.looks_like_archive_url(url) is expected


def _make_tar(path: Path, members: dict[str, str]) -> None:
    with tarfile.open(path, "w:gz") as tf:
        for name, content in members.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def test_tar_extracts_and_flattens_single_root(tmp_path, monkeypatch):
    archive = tmp_path / "repo.tar.gz"
    _make_tar(archive, {
        "repo-main/README.md": "hello",
        "repo-main/bin/tool": "#!/bin/sh\n",
    })

    def fake_stream(url, dest, **_kw):
        dest.write_bytes(archive.read_bytes())

    monkeypatch.setattr(eng, "_stream_download", fake_stream)
    root = tmp_path / "data"
    res = eng._download_and_extract_tarball(
        "https://github.com/u/repo/archive/refs/heads/main.tar.gz", root, "repo"
    )
    assert res.ok, res.message
    target = root / "engines" / "repo"
    assert (target / "README.md").read_text() == "hello"
    assert (target / "bin" / "tool").exists()
    assert not (target / "repo-main").exists()
    assert not (target / "download.tmp").exists()


def test_tar_rejects_path_traversal(tmp_path, monkeypatch):
    archive = tmp_path / "evil.tar.gz"
    _make_tar(archive, {"../evil.txt": "pwned"})
    monkeypatch.setattr(
        eng, "_stream_download",
        lambda url, dest, **kw: dest.write_bytes(archive.read_bytes()),
    )
    root = tmp_path / "data"
    res = eng._download_and_extract_tarball("https://x/evil.tar.gz", root, "evil")
    assert res.ok is False
    assert "escapes" in res.message
    assert not (root / "engines" / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_asset_platform_score_prefers_cpu_build():
    plat = "linux-x64"
    assert eng._asset_platform_score("llama-b1-bin-ubuntu-x64.zip", plat) > 0
    assert eng._asset_platform_score("llama-b1-bin-ubuntu-arm64.zip", plat) == -1
    assert eng._asset_platform_score("llama-b1-bin-win-cpu-x64.zip", plat) == -1
    assert eng._asset_platform_score("cudart-llama-bin-win-cuda.zip", "windows-x64") < \
        eng._asset_platform_score("llama-b1-bin-win-cpu-x64.zip", "windows-x64")


def test_resolve_github_sources_orders_release_asset_first(monkeypatch):
    def fake_get(path):
        if path.endswith("/releases/latest"):
            return {"assets": [
                {"name": "llama-b1-bin-ubuntu-arm64.zip", "browser_download_url": "https://g/arm.zip"},
                {"name": "llama-b1-bin-ubuntu-x64.zip", "browser_download_url": "https://g/x64.zip"},
            ]}
        return {"default_branch": "master"}

    monkeypatch.setattr(eng, "_github_get", fake_get)
    urls = eng.resolve_github_engine_sources("ggml-org/llama.cpp", "linux-x64")
    assert urls[0] == "https://g/x64.zip"
    # 没有可用资产时，源码归档必须用真实默认分支（master），而不是硬编码 main
    assert "archive/refs/heads/master.tar.gz" in urls[1]
