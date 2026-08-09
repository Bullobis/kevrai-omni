# -*- coding: utf-8 -*-
"""
ui/widgets.py — 自定义组件
===========================
AuroraBackground 动态极光背景 / GlassPanel 玻璃面板 / TitleBar 无边框标题栏 /
DropZone 拖拽区
"""

import math

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
                               QLabel, QPushButton, QWidget)


# ─────────────────────────────────────────────────────────────
# 动态极光背景（低开销：20fps，三个缓慢移动的光斑）
# ─────────────────────────────────────────────────────────────
class AuroraBackground(QWidget):
    def __init__(self, color_a="#341b63", color_b="#101c3f", base="#0b0716", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._base = QColor(base)
        self._ca = QColor(color_a)
        self._cb = QColor(color_b)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(66)

    def set_colors(self, base, a, b):
        self._base = QColor(base)
        self._ca = QColor(a)
        self._cb = QColor(b)
        self.update()

    def _tick(self):
        self._t += 0.012
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), self._base)

        blobs = [
            (0.25 + 0.10 * math.sin(self._t * 0.9), 0.30 + 0.10 * math.cos(self._t * 0.7), 0.55, self._ca, 110),
            (0.78 + 0.08 * math.cos(self._t * 0.6), 0.72 + 0.08 * math.sin(self._t * 0.8), 0.60, self._cb, 120),
            (0.55 + 0.12 * math.sin(self._t * 0.5), 0.15 + 0.06 * math.cos(self._t * 1.1), 0.40, self._ca, 70),
        ]
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        for fx, fy, fr, color, alpha in blobs:
            cx, cy, r = fx * w, fy * h, fr * max(w, h)
            g = QRadialGradient(QPointF(cx, cy), r)
            c = QColor(color)
            c.setAlpha(alpha)
            g.setColorAt(0.0, c)
            c2 = QColor(color)
            c2.setAlpha(0)
            g.setColorAt(1.0, c2)
            p.setBrush(QBrush(g))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, cy), r, r)
        p.end()


# ─────────────────────────────────────────────────────────────
# 玻璃面板
# ─────────────────────────────────────────────────────────────
class GlassPanel(QFrame):
    def __init__(self, strong=False, radius=14, shadow=True, parent=None):
        super().__init__(parent)
        self.setObjectName("glassPanelStrong" if strong else "glassPanel")
        if shadow:
            eff = QGraphicsDropShadowEffect(self)
            eff.setBlurRadius(26)
            eff.setOffset(0, 6)
            eff.setColor(QColor(0, 0, 0, 90))
            self.setGraphicsEffect(eff)


# ─────────────────────────────────────────────────────────────
# 标题栏（无边框窗口拖拽 + 最小化/最大化/关闭）
# ─────────────────────────────────────────────────────────────
class TitleBar(QWidget):
    def __init__(self, window, title="MiniMax H3 Studio", parent=None):
        super().__init__(parent)
        self._win = window
        self._drag_pos = None
        self.setObjectName("glassPanel")
        self.setFixedHeight(46)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 8, 0)
        lay.setSpacing(10)

        logo = QLabel("H3")
        logo.setFixedSize(26, 26)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "stop:0 #3b82f6, stop:1 #a855f7);"
            "border-radius: 8px; color: white; font-weight: 800; font-size: 12px;")
        lay.addWidget(logo)

        t = QLabel(title)
        f = t.font(); f.setPointSize(10); f.setBold(True)
        t.setFont(f)
        lay.addWidget(t)
        sub = QLabel("视频生成工作站")
        sub.setObjectName("dimText")
        lay.addWidget(sub)
        lay.addStretch(1)

        def mk(txt, slot, hover="#ffffff22", close=False):
            b = QPushButton(txt)
            b.setObjectName("iconBtn")
            b.setFixedSize(38, 30)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton#iconBtn {{ font-size: 13px; color: rgba(255,255,255,0.75); }}"
                f"QPushButton#iconBtn:hover {{ background: {'#e81123' if close else hover}; "
                f"{'color:white;' if close else ''} border-radius: 8px; }}")
            b.clicked.connect(slot)
            return b

        lay.addWidget(mk("─", lambda: window.showMinimized()))
        lay.addWidget(mk("▢", lambda: self._toggle_max()))
        lay.addWidget(mk("✕", lambda: window.close(), close=True))

    def _toggle_max(self):
        if self._win.isMaximized():
            self._win.showNormal()
        else:
            self._win.showMaximized()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.LeftButton:
            if self._win.isMaximized():
                self._win.showNormal()
            self._win.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, e):
        self._toggle_max()


# ─────────────────────────────────────────────────────────────
# 拖拽上传区
# ─────────────────────────────────────────────────────────────
class DropZone(QFrame):
    filesDropped = Signal(list)   # [path, ...]

    def __init__(self, text="点击选择文件，或直接拖拽到这里", parent=None):
        super().__init__(parent)
        self.setObjectName("glassPanel")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(96)
        self._hover = False
        self._text = text

    def setText(self, t):
        self._text = t
        self.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#3b82f6" if self._hover else "rgba(255,255,255,0.25)"))
        pen.setWidth(2)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(8, 8, -8, -8), 10, 10)
        p.setPen(QColor("rgba(255,255,255,0.55)" if not self._hover else "#3b82f6"))
        f = p.font(); f.setPointSize(10)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, self._text)
        p.end()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            self._hover = True
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._hover = False
        self.update()

    def dropEvent(self, e):
        self._hover = False
        self.update()
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
        e.acceptProposedAction()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            from PySide6.QtWidgets import QFileDialog
            files, _ = QFileDialog.getOpenFileNames(
                self, "选择素材文件", "",
                "媒体文件 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.mp4 *.mov *.mkv *.webm *.avi *.mp3 *.wav *.flac *.m4a *.ogg *.aac);;所有文件 (*.*)")
            if files:
                self.filesDropped.emit(files)


# ─────────────────────────────────────────────────────────────
# 布局工具
# ─────────────────────────────────────────────────────────────
def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
