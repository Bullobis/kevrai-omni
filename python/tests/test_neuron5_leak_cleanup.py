"""N5 临时文件/目录泄漏回归测试。

覆盖两条经证据确认的真实泄漏：

1. ``app.main._media_to_local`` 为 data:/http(s) 媒体 ``mkstemp`` 出的临时文件
   此前用完不删（每次多模态对话都在系统 temp 目录留一个文件，无限增长）。
   修复后：临时路径被登记进 ``created`` 清单，由请求方在结束时统一 unlink；
   用户自己的本地路径 / file:// 原样返回、**绝不**删除。

2. ``app.engines.download_zip_engine`` 解压失败时，下载下来的 ``download.tmp``
   此前不清理（每次失败留一份完整 zip）。修复后：解压异常路径也 unlink。
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.main import _media_to_local


# ---------------------------------------------------------------------------
# 1) _media_to_local 临时文件生命周期
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_data_url_temp_file_is_registered_for_cleanup(tmp_path: Path):
    """data: URL 落盘的临时文件必须进 created 清单，且文件真实存在。"""
    payload = b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes"
    b64 = base64.b64encode(payload).decode()
    data_url = f"data:image/png;base64,{b64}"

    created: list[str] = []
    path = await _media_to_local(data_url, "image", created)

    try:
        assert path.startswith(_temp_prefix_hint()), "应落在系统临时目录"
        assert os.path.isfile(path)
        assert path in created, "临时路径必须登记进 created 清单供调用方清理"
        with open(path, "rb") as fh:
            assert fh.read() == payload
    finally:
        # 模拟调用方在请求结束时的清理（v1_chat_completions 的 finally 块）。
        for p in created:
            with contextlib_suppress_oserror():
                os.unlink(p)


@pytest.mark.asyncio
async def test_caller_cleanup_removes_data_url_temp(tmp_path: Path):
    """请求方 finally 中 unlink created 后，临时文件必须消失（成功路径）。"""
    payload = b"RIFF....WEBP"
    b64 = base64.b64encode(payload).decode()
    created: list[str] = []
    path = await _media_to_local(f"data:audio/ogg;base64,{b64}", "audio", created)
    assert os.path.isfile(path)
    assert path in created

    # 模拟 finally 清理
    for p in created:
        with contextlib_suppress_oserror():
            os.unlink(p)

    assert not os.path.exists(path), "成功路径结束后临时文件必须被删除"


@pytest.mark.asyncio
async def test_local_user_path_is_not_tracked_for_deletion(tmp_path: Path):
    """用户自己的本地文件（file:// / 绝对路径）必须原样返回，且**不**进 created。"""
    user_file = tmp_path / "mine.png"
    user_file.write_bytes(b"user-owned")

    created: list[str] = []
    # 绝对路径形式
    out = await _media_to_local(str(user_file), "image", created)
    assert out == str(user_file)
    assert created == [], "用户本地路径不得登记进临时清理清单"

    # file:// 形式
    created2: list[str] = []
    out2 = await _media_to_local(f"file://{user_file}", "image", created2)
    assert out2 == str(user_file)
    assert created2 == [], "file:// 路径不得登记进临时清理清单"

    # 用户文件必须原封不动保留
    assert user_file.read_bytes() == b"user-owned"


@pytest.mark.asyncio
async def test_data_url_write_failure_self_cleans_temp(tmp_path: Path, monkeypatch):
    """mkstemp 之后、return 之前若写盘失败，刚建的临时文件必须就地删除。"""
    import app.main as m

    b64 = base64.b64encode(b"whatever").decode()
    created: list[str] = []

    real_fdopen = os.fdopen

    def boom_fdopen(*a, **k):
        # 第一次（也是唯一一次）打开刚建的 fd 写盘时炸掉。
        real_fdopen(*a, **k)
        raise OSError("simulated disk full on write")

    monkeypatch.setattr(m.os, "fdopen", boom_fdopen)
    with pytest.raises(HTTPException) as exc:
        await m._media_to_local(f"data:image/png;base64,{b64}", "image", created)
    assert exc.value.status_code == 400
    # created 不应包含任何路径（异常已就地清理），且系统 temp 里不应留下 kevrai-image-*。
    assert created == []
    leftovers = _leftover_kevrai_temp()
    assert leftovers == [], f"写盘失败后仍有临时文件泄漏: {leftovers}"


# ---------------------------------------------------------------------------
# 2) download_zip_engine 解压失败清理 download.tmp
# ---------------------------------------------------------------------------


def test_download_zip_engine_extract_failure_cleans_tmp(tmp_path: Path, monkeypatch):
    """解压失败（损坏 zip）时，download.tmp 必须被删除，不得留在 engine 目录。"""
    from app import engines

    # 绕过 host allowlist + 真实网络：直接伪造 _stream_download 写一份坏 zip。
    monkeypatch.setattr(engines, "is_host_allowed", lambda *a, **k: True)

    def fake_stream_download(url, dest, **kw):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"this is NOT a real zip archive")

    monkeypatch.setattr(engines, "_stream_download", fake_stream_download)

    res = engines.download_zip_engine(
        "https://allowed.example.com/engine.zip", tmp_path, "badzip"
    )
    assert res.ok is False, "损坏 zip 必须返回失败"
    # 关键断言：download.tmp 不得残留
    tmp_file = tmp_path / "badzip" / "download.tmp"
    assert not tmp_file.exists(), (
        f"解压失败后 download.tmp 泄漏: {tmp_file} 仍存在"
    )


def test_download_zip_engine_success_removes_tmp(tmp_path: Path, monkeypatch):
    """对照：成功路径本来就删 tmp（保护既有行为不被回归改坏）。"""
    import io
    import zipfile

    from app import engines

    monkeypatch.setattr(engines, "is_host_allowed", lambda *a, **k: True)

    def fake_stream_download(url, dest, **kw):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("engine.bin", b"#!/bin/sh\n")
        Path(dest).write_bytes(buf.getvalue())

    monkeypatch.setattr(engines, "_stream_download", fake_stream_download)

    res = engines.download_zip_engine(
        "https://allowed.example.com/engine.zip", tmp_path, "goodzip"
    )
    assert res.ok is True
    tmp_file = tmp_path / "goodzip" / "download.tmp"
    assert not tmp_file.exists(), "成功路径后 download.tmp 也必须删除"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def contextlib_suppress_oserror():
    import contextlib
    return contextlib.suppress(OSError)


def _temp_prefix_hint() -> str:
    import tempfile
    return tempfile.gettempdir()


def _leftover_kevrai_temp() -> list[str]:
    """扫描系统 temp 目录里残留的 kevrai-image-/kevrai-audio- 文件。"""
    import tempfile
    root = Path(tempfile.gettempdir())
    return [
        str(p) for p in root.iterdir()
        if p.name.startswith(("kevrai-image-", "kevrai-audio-"))
    ]
