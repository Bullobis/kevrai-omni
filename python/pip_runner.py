"""随安装包分发的 pip 运行器。

打包后的 sidecar 是 PyInstaller 冻结二进制，``sys.executable`` 指向 sidecar
本身，``sys.executable -m pip`` 会在启动参数上再拉起一个服务进程并立即失败
（实测 rc=1）。这个入口把 pip 编译成独立可执行文件，冻结版安装引擎时改用它，
开发环境仍然用 ``python -m pip``。
"""

from __future__ import annotations

import sys


def main() -> int:
    from pip._internal.cli.main import main as pip_main

    return int(pip_main(sys.argv[1:]) or 0)


if __name__ == "__main__":
    sys.exit(main())
