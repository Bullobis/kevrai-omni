# -*- coding: utf-8 -*-
"""
main.py — 程序入口
===================
python -m h3studio.main
"""

import os
import sys


def main():
    # Windows 高分屏适配
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    # 避免某些机器上多媒体后端缺失导致崩溃
    os.environ.setdefault("QT_MULTIMEDIA_PREFERRED_PLUGINS", "windowsmediafoundation")

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("MiniMax H3 Studio")
    app.setOrganizationName("H3Studio")

    from .config import Settings
    from .ui.main_window import MainWindow, show_license_dialog

    settings = Settings()
    win = MainWindow()

    if not settings.get("license_accepted"):
        if not show_license_dialog(win):
            return 0
        import time
        settings.set("license_accepted", True)
        settings.set("license_accepted_at", time.strftime("%Y-%m-%d %H:%M:%S"))

    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
