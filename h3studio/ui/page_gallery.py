# -*- coding: utf-8 -*-
"""
ui/page_gallery.py — 作品库
"""

import json
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from .widgets import GlassPanel, clear_layout
from .page_generate import VideoPlayerDialog


class GalleryPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        head = QHBoxLayout()
        t = QLabel("作品库")
        t.setObjectName("sectionTitle")
        head.addWidget(t)
        self.count_label = QLabel("")
        self.count_label.setObjectName("hintLabel")
        head.addWidget(self.count_label)
        head.addStretch(1)
        open_btn = QPushButton("打开输出目录")
        open_btn.clicked.connect(lambda: self.ctx.open_dir(self.ctx.settings.get("outputs_dir")))
        head.addWidget(open_btn)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh)
        head.addWidget(refresh_btn)
        root.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        wrap = QWidget()
        self.grid = QGridLayout(wrap)
        self.grid.setSpacing(10)
        self.grid.setContentsMargins(0, 0, 6, 0)
        scroll.setWidget(wrap)
        root.addWidget(scroll, 1)

    def refresh(self):
        clear_layout(self.grid)
        out_dir = self.ctx.settings.get("outputs_dir")
        items = []
        if os.path.isdir(out_dir):
            for fn in os.listdir(out_dir):
                if fn.lower().endswith(".mp4"):
                    fp = os.path.join(out_dir, fn)
                    meta = {}
                    mf = os.path.splitext(fp)[0] + ".json"
                    if os.path.exists(mf):
                        try:
                            meta = json.load(open(mf, "r", encoding="utf-8"))
                        except Exception:
                            pass
                    items.append((os.path.getmtime(fp), fp, meta))
        items.sort(reverse=True)

        self.count_label.setText(f"共 {len(items)} 个作品")
        if not items:
            empty = QLabel("还没有作品。去生成页创作第一个视频吧！")
            empty.setObjectName("hintLabel")
            self.grid.addWidget(empty, 0, 0)
            return

        col = 0
        row = 0
        for _, fp, meta in items[:60]:
            card = GlassPanel()
            card.setFixedWidth(236)
            v = QVBoxLayout(card)
            v.setContentsMargins(10, 10, 10, 10)
            v.setSpacing(6)

            thumb = self._make_thumb(fp)
            if thumb is not None:
                tl = QLabel()
                tl.setPixmap(thumb)
                tl.setAlignment(Qt.AlignCenter)
                tl.setFixedHeight(118)
                tl.setStyleSheet("background: rgba(0,0,0,0.35); border-radius: 8px;")
                v.addWidget(tl)
            else:
                tl = QLabel("🎬")
                tl.setAlignment(Qt.AlignCenter)
                tl.setFixedHeight(118)
                tl.setStyleSheet("font-size: 34px; background: rgba(0,0,0,0.35); border-radius: 8px;")
                v.addWidget(tl)

            name = QLabel(os.path.basename(fp)[:24])
            name.setObjectName("sectionTitle")
            v.addWidget(name)
            prompt = (meta.get("prompt") or "").strip()
            if prompt:
                pl = QLabel(prompt[:60] + ("…" if len(prompt) > 60 else ""))
                pl.setObjectName("hintLabel")
                pl.setWordWrap(True)
                v.addWidget(pl)
            info = QLabel(
                f"{meta.get('width', '?')}×{meta.get('height', '?')} · "
                f"{meta.get('steps', '?')} 步 · 种子 {meta.get('seed', '?')} · "
                f"用时 {meta.get('elapsed_s', '?')}s")
            info.setObjectName("dimText")
            v.addWidget(info)
            play = QPushButton("▶ 播放")
            play.setObjectName("primaryBtn")
            play.clicked.connect(lambda _=False, p=fp: VideoPlayerDialog(p, self).exec())
            v.addWidget(play)
            self.grid.addWidget(card, row, col)
            col += 1
            if col >= 3:
                col = 0
                row += 1
        self.grid.setRowStretch(row + 1, 1)

    @staticmethod
    def _make_thumb(path):
        """提取视频首帧做封面（失败返回 None，不影响列表展示）。"""
        try:
            import imageio.v2 as imageio
            reader = imageio.get_reader(path)
            frame = reader.get_data(0)
            reader.close()
            h, w = frame.shape[:2]
            qimg = QImage(frame.data, w, h, frame.strides[0], QImage.Format_RGB888)
            return QPixmap.fromImage(qimg.copy()).scaled(
                216, 118, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        except Exception:
            return None

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)
