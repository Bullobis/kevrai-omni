"""统一的 pip 调用入口。

开发环境用 ``python -m pip``；安装包里 sidecar 是 PyInstaller 冻结二进制，
``sys.executable`` 指向 sidecar 本身，直接 ``-m pip`` 会把 pip 参数当成启动参数
（实测 rc=1，引擎永远装不上）。因此冻结版沿用随包分发的 ``pip-runner``。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PIP_RUNNER_NAME = "pip-runner.exe" if os.name == "nt" else "pip-runner"


def pip_runner_path() -> Path | None:
    """返回可用的 pip 运行器；没有则返回 None。"""
    if not getattr(sys, "frozen", False):
        return None
    candidate = Path(sys.executable).resolve().parent / PIP_RUNNER_NAME
    return candidate if candidate.is_file() else None


def pip_command() -> list[str]:
    """构造 pip 命令前缀（不含子命令与参数）。"""
    runner = pip_runner_path()
    if runner is not None:
        return [str(runner)]
    return [sys.executable, "-m", "pip"]
