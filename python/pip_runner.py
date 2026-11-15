"""随安装包分发的 pip 运行器。

打包后的 sidecar 是 PyInstaller 冻结二进制，``sys.executable`` 指向 sidecar
本身，``sys.executable -m pip`` 会在启动参数上再拉起一个服务进程并立即失败
（实测 rc=1）。这个入口把 pip 编译成独立可执行文件，冻结版安装引擎时改用它，
开发环境仍然用 ``python -m pip``。
"""

from __future__ import annotations

import sys


def _patch_distlib_for_frozen() -> None:
    """Make pip's vendored distlib understand PyInstaller's loader.

    ``pip._vendor.distlib.scripts`` calls ``distlib.resources.finder`` at import
    time.  The finder registry only knows the standard importlib loaders; under
    a frozen Windows build the package is loaded by PyInstaller's frozen loader,
    so distlib raises ``Unable to locate finder for 'pip._vendor.distlib'`` and
    every ``installEngine`` fails with a 400.  Registering ResourceFinder for
    that loader fixes it; the package is extracted to a real directory under
    ``sys._MEIPASS``, which ResourceFinder can scan.
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        import pip._vendor.distlib as distlib
        import pip._vendor.distlib.resources as resources

        loader_type = type(distlib.__loader__)
        resources._finder_registry.setdefault(loader_type, resources.ResourceFinder)
    except Exception:
        # Never let the compatibility shim prevent pip from starting.
        return


def main() -> int:
    _patch_distlib_for_frozen()
    from pip._internal.cli.main import main as pip_main

    return int(pip_main(sys.argv[1:]) or 0)


if __name__ == "__main__":
    sys.exit(main())
