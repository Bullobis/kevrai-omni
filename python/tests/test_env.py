"""Tests for v2.2.0 environment / dependency / engine management (app.env)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.env import (
    REQUIRED_PIP_PACKAGES,
    EnvStatus,
    InstallError,
    NodeStatus,
    PythonStatus,
    detect_node,
    detect_python,
    disk_info,
    install_pip_package,
    list_pip_packages,
    _parse_pip_freeze,
    _parse_version_tuple,
    _is_newer,
)


def test_parse_pip_freeze_handles_typical_output():
    text = "\n".join([
        "# comment",
        "fastapi==0.111.0",
        "uvicorn>=0.27",
        "    # indented comment",
        "pydantic==2.6.0",
    ])
    out = _parse_pip_freeze(text)
    assert out == {"fastapi": "0.111.0", "pydantic": "2.6.0"}


def test_parse_version_tuple_handles_prerelease():
    assert _parse_version_tuple("1.2.3") == (1, 2, 3)
    # "1.2.3-rc.1" — non-numeric pieces become 0, the trailing 1 is captured
    assert _parse_version_tuple("1.2.3-rc.1")[:3] == (1, 2, 3)
    # Note: leading non-numeric char ("v") becomes 0 — we strip it via the
    # version's natural numeric prefix in real callers.
    assert _parse_version_tuple("2.10.4+local") == (2, 10, 4)


def test_is_newer_compares_correctly():
    assert _is_newer("1.2.4", "1.2.3")
    assert not _is_newer("1.2.3", "1.2.3")
    assert not _is_newer("0.9.0", "1.0.0")


def test_detect_python_finds_interpreter():
    s = detect_python()
    assert s.available
    assert "Python" in s.version
    assert "pip" in s.pip_version.lower()


def test_detect_node_optional():
    s = detect_node()
    # No assertion about availability — just that the function runs.
    assert isinstance(s, NodeStatus)
    assert isinstance(s.available, bool)


def test_disk_info_returns_free_total():
    info = disk_info(Path("/tmp"), Path("/tmp"))
    assert info.free_bytes > 0
    assert info.total_bytes > 0
    assert info.total_bytes > info.free_bytes


def test_list_pip_packages_returns_known_required():
    pkgs = list_pip_packages()
    by_name = {p.name: p for p in pkgs}
    for required in REQUIRED_PIP_PACKAGES:
        # Either installed (version known) or not installed (version "")
        assert required in by_name


def test_install_pip_package_validates_name():
    with pytest.raises(InstallError):
        install_pip_package("definitely-not-a-real-pkg-xyzzy_999", version="99.99.99",
                            extra_index_urls=["https://mirrors.aliyun.com/pypi/simple/"])


def test_env_status_to_dict_shape():
    s = EnvStatus(
        python=PythonStatus(available=True, version="Python 3.11.0",
                            pip_version="pip 23.0"),
        node=NodeStatus(available=False),
        pip_packages=[],
        engines=[],
        gpus=[],
        disk=disk_info(Path("/tmp"), Path("/tmp")),
        has_updates=False,
    )
    d = s.to_dict()
    assert d["python"]["available"] is True
    assert d["node"]["available"] is False
    assert "pip_packages" in d
    assert "disk" in d
    assert "issues" in d
