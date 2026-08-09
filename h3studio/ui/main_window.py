# -*- coding: utf-8 -*-
"""
ui/main_window.py — 主窗口与应用上下文
"""

import os
import subprocess
import sys

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel,
                               QPushButton, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import facts
from ..config import Settings
from ..engine import check_engine_ready, get_engine
from ..hardware import HardwareReport, probe_all
from .styles import build_qss
from .widgets import AuroraBackground, GlassPanel, TitleBar


# ─────────────────────────────────────────────────────────────
# 后台线程
# ─────────────────────────────────────────────────────────────
class HwProbeThread(QThread):
    done = Signal(object)

    def run(self):
        self.done.emit(probe_all(str(__import__("pathlib").Path.home())))


class LoadModelThread(QThread):
    log = Signal(str)
    ok = Signal()
    failed = Signal(str)

    def __init__(self, engine, bundle_dir, bundle_id, partition, policy,
                 vram_budget_gb=-1, offload_mode="auto", torch_threads=-1):
        super().__init__()
        self.args = (engine, bundle_dir, bundle_id, partition, policy,
                     vram_budget_gb, offload_mode, torch_threads)

    def run(self):
        (engine, bundle_dir, bundle_id, partition, policy,
         vram_budget_gb, offload_mode, torch_threads) = self.args
        try:
            engine.load(bundle_dir, bundle_id, partition, policy,
                        progress_cb=lambda s: self.log.emit(s),
                        vram_budget_gb=vram_budget_gb,
                        offload_mode=offload_mode,
                        torch_threads=torch_threads)
            self.ok.emit()
        except Exception as e:
            self.failed.emit(str(e))


# ─────────────────────────────────────────────────────────────
# AppContext：跨页面共享
# ─────────────────────────────────────────────────────────────
class AppContext:
    def __init__(self, window):
        self.window = window
        self.settings = Settings()
        self.settings.ensure_dirs()
        self.engine = get_engine()
        self.hw = None
        self.downloads = {}
        self.pages = {}
        self._load_thread = None

    # ── 通知 ──
    def toast(self, msg):
        self.window.show_toast(msg)

    def status(self, msg):
        self.window.status_label.setText(msg)

    # ── 主题 ──
    def apply_theme(self):
        self.window.apply_theme()

    def open_dir(self, path):
        os.makedirs(path, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def open_url(self, url):
        QDesktopServices.openUrl(__import__("PySide6.QtCore", fromlist=["QUrl"]).QUrl(url))

    # ── 模型安装状态 ──
    def bundle_state(self, b: dict):
        """返回 (missing|partial|complete, done_gb)"""
        bdir = os.path.join(self.settings.get("models_dir"), b["id"])
        if not os.path.isdir(bdir):
            return "missing", 0.0

        def dir_size(p):
            total = 0
            for root, _dirs, files in os.walk(p):
                for fn in files:
                    fp = os.path.join(root, fn)
                    if os.path.exists(fp) and not fn.endswith(".part"):
                        total += os.path.getsize(fp)
            return total

        expected = facts.bundle_total_bytes(b)
        done = 0
        for f in b["files"]:
            dest = os.path.join(bdir, f["dest"])
            if f.get("is_dir"):
                if os.path.isdir(dest):
                    done += dir_size(dest)
            else:
                if os.path.exists(dest):
                    done += os.path.getsize(dest)
        done_gb = done / (1024 ** 3)
        if expected > 0 and done >= expected * 0.99:
            return "complete", done_gb
        if done > 0:
            return "partial", done_gb
        return "missing", 0.0

    # ── 模型加载 ──
    def _pick_bundle_for(self, partition: str):
        """按优先级找一个已完整下载、可供内置引擎使用的 bundle。"""
        priority = {
            "FL2VA": ["nf4_full", "nf4_fl2va", "bf16_fl2va"],
            "Ref2VA": ["nf4_full", "bf16_ref2va"],
        }
        for bid in priority.get(partition, []):
            b = facts.get_bundle(bid)
            if b and self.bundle_state(b)[0] == "complete":
                return b
        return None

    def ensure_model_loaded(self, partition: str) -> bool:
        eng = self.engine
        if eng.ready and eng.loaded_partition == partition:
            return True
        b = self._pick_bundle_for(partition)
        if b is None:
            self.toast(f"需要 {partition} 分区的模型。请先在「模型市场」下载（推荐 NF4 版）")
            self.window.goto_page("market")
            return False
        return self.load_model(b, partition)

    def load_model(self, b: dict, partition: str) -> bool:
        """加载模型；返回是否真正加载成功。"""
        if self._load_thread and self._load_thread.isRunning():
            self.toast("正在加载模型，请稍候…")
            return False
        ok, msg = check_engine_ready()
        if not ok:
            self.toast("引擎未就绪：" + msg)
            self.window.goto_page("settings")
            return False

        policy = self.hw.policy if (self.hw and self.hw.policy != "unsupported") else "balanced"
        bundle_dir = os.path.join(self.settings.get("models_dir"), b["id"])

        dlg = LoadProgressDialog(self.window)
        self._load_thread = LoadModelThread(
            self.engine, bundle_dir, b["id"], partition, policy,
            vram_budget_gb=float(self.settings.get("vram_budget_gb")),
            offload_mode=self.settings.get("offload_mode"),
            torch_threads=int(self.settings.get("torch_threads")))
        self._load_thread.log.connect(dlg.set_msg)
        self._load_thread.ok.connect(dlg.accept)
        self._load_thread.failed.connect(lambda e: (dlg.set_msg("加载失败：" + e[:300]), dlg.fail()))
        self._load_thread.start()
        result = dlg.exec()
        if result == QDialog.Accepted:
            self.toast(f"模型已加载：{b['name']}（{partition}）")
            self.status(f"已加载 {partition} · 显存策略 {policy}")
            return True
        self.status("模型加载未完成")
        return False

    def gallery_dirty(self):
        pass

    def library_dirty(self):
        p = self.pages.get("library")
        if p:
            p.refresh()


class LoadProgressDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("加载模型")
        self.setModal(True)
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        self.msg = QLabel("准备加载模型…")
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        hint = QLabel("首次加载需要把权重读入内存/显存，可能需要几分钟，请耐心等待。")
        hint.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 12px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)
        v.addLayout(row)

    def set_msg(self, s):
        self.msg.setText(s)

    def fail(self):
        # 保持在屏幕上让用户看到错误，点「关闭」即拒绝
        pass


# ─────────────────────────────────────────────────────────────
# 首次启动协议
# ─────────────────────────────────────────────────────────────
def show_license_dialog(parent) -> bool:
    dlg = QDialog(parent)
    dlg.setWindowTitle("使用前必读 · 模型协议")
    dlg.setMinimumSize(560, 420)
    v = QVBoxLayout(dlg)
    lic = facts.MODEL_INFO["license"]
    body = QLabel(
        f"本软件将帮助你下载并本地运行 MiniMax H3 模型。\n\n"
        f"重要提醒：\n"
        f"1. MiniMax H3 权重采用《{lic['name']}》；\n"
        f"2. 该协议明确排除 {'、'.join(lic['regions_excluded'])} 地区的使用权限；\n"
        f"3. {lic['note']}\n"
        f"4. 开源版（H3-Base）最高输出短边 768 像素，2K 再生成模块未开源（仅官方 API）；\n"
        f"5. 本地推理需要加速硬件：NVIDIA（CUDA）完整支持，AMD ROCm / 华为昇腾支持，Intel / DirectML 实验性；\n"
        f"   并留有足够磁盘空间（NF4 版约 35~52GB）。\n\n"
        f"协议全文：{lic['url']}")
    body.setWordWrap(True)
    body.setTextInteractionFlags(Qt.TextSelectableByMouse)
    v.addWidget(body)
    cb = QCheckBox("我已阅读并同意遵守模型协议与所在地区法律法规")
    v.addWidget(cb)
    row = QHBoxLayout()
    row.addStretch(1)
    ok = QPushButton("同意并继续")
    ok.setEnabled(False)
    cancel = QPushButton("退出")
    cb.toggled.connect(ok.setEnabled)
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    row.addWidget(cancel)
    row.addWidget(ok)
    v.addLayout(row)
    return dlg.exec() == QDialog.Accepted


# ─────────────────────────────────────────────────────────────
# 主窗口
# ─────────────────────────────────────────────────────────────
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MiniMax H3 Studio")
        self.resize(1440, 900)
        self.setMinimumSize(1180, 760)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

        self.ctx = AppContext(self)

        # 背景（铺满窗口底层，内容控件叠在其上）
        self.bg = AuroraBackground(parent=self)
        self.bg.setGeometry(self.rect())
        self.bg.lower()

        content = QVBoxLayout(self)
        content.setContentsMargins(12, 12, 12, 12)
        content.setSpacing(10)

        self.title_bar = TitleBar(self)
        content.addWidget(self.title_bar)

        body = QHBoxLayout()
        body.setSpacing(10)

        # ── 导航栏 ──
        nav = GlassPanel(strong=True)
        nav.setFixedWidth(190)
        nv = QVBoxLayout(nav)
        nv.setContentsMargins(10, 12, 10, 12)
        nv.setSpacing(6)

        nav_label = QLabel("导航")
        nav_label.setObjectName("dimText")
        nv.addWidget(nav_label)

        self.nav_btns = {}
        NAV = [
            ("generate", "🎬  生成"),
            ("market", "🏪  模型市场"),
            ("custom", "🧩  DIY 打包"),
            ("library", "📦  我的模型"),
            ("gallery", "🖼  作品库"),
            ("settings", "⚙  设置"),
        ]
        self._nav_group_layout = nv
        for key, label in NAV:
            b = QPushButton(label)
            b.setObjectName("navBtn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.goto_page(k))
            nv.addWidget(b)
            self.nav_btns[key] = b
        nv.addStretch(1)

        # 硬件状态
        self.hw_chip = QLabel("硬件检测中…")
        self.hw_chip.setObjectName("hintLabel")
        self.hw_chip.setWordWrap(True)
        nv.addWidget(self.hw_chip)
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("dimText")
        self.status_label.setWordWrap(True)
        nv.addWidget(self.status_label)

        body.addWidget(nav)

        # ── 页面栈 ──
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        content.addLayout(body, 1)

        from .page_generate import GeneratePage
        from .page_market import MarketPage
        from .page_custom import CustomPage
        from .page_library import LibraryPage
        from .page_gallery import GalleryPage
        from .page_settings import SettingsPage

        self.ctx.pages = {
            "generate": GeneratePage(self.ctx),
            "market": MarketPage(self.ctx),
            "custom": CustomPage(self.ctx),
            "library": LibraryPage(self.ctx),
            "gallery": GalleryPage(self.ctx),
            "settings": SettingsPage(self.ctx),
        }
        for key in ("generate", "market", "custom", "library", "gallery", "settings"):
            self.stack.addWidget(self.ctx.pages[key])

        # Toast
        self.toast_label = QLabel(self)
        self.toast_label.setObjectName("glassPanelStrong")
        self.toast_label.setWordWrap(True)
        self.toast_label.setMaximumWidth(420)
        self.toast_label.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_label.hide)

        self._qset = QSettings("H3Studio", "MiniMaxH3Studio")
        geo = self._qset.value("geometry")
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass

        self.apply_theme()
        self.goto_page("generate")

        # 硬件检测
        self._hw_thread = HwProbeThread()
        self._hw_thread.done.connect(self._on_hw_done)
        self._hw_thread.start()

    # ═══════════════════════════════════════════════════════
    def resizeEvent(self, e):
        if hasattr(self, "bg"):
            self.bg.setGeometry(self.rect())
        self.bg.lower()
        super().resizeEvent(e)

    def apply_theme(self):
        s = self.ctx.settings
        theme = s.theme_def()
        accent = s.get("accent_color")
        # 字号缩放：100% = 13px 基准
        font_px = max(10, round(13 * int(s.get("font_scale")) / 100))
        qss = build_qss(theme, accent, int(s.get("glass_opacity")), font_px=font_px)
        self.setStyleSheet(qss)
        self.bg.set_colors(theme["bg_base"], theme["bg_aurora_a"], theme["bg_aurora_b"])

    def goto_page(self, key):
        self.stack.setCurrentWidget(self.ctx.pages[key])
        for k, b in self.nav_btns.items():
            b.setChecked(k == key)

    def show_toast(self, msg):
        self.toast_label.setText(msg)
        self.toast_label.adjustSize()
        self.toast_label.move(self.width() - self.toast_label.width() - 24,
                              self.height() - self.toast_label.height() - 24)
        self.toast_label.show()
        self.toast_label.raise_()
        self._toast_timer.start(4200)

    def _on_hw_done(self, rep: HardwareReport):
        self.ctx.hw = rep
        if rep.policy == "unsupported":
            self.hw_chip.setText(f"⚠ {rep.gpu_name}\n本地推理需要加速硬件\n(NVIDIA/AMD ROCm/昇腾)")
        else:
            vram = f"显存 {rep.vram_total_gb} GB · " if rep.vram_total_gb > 0 else ""
            self.hw_chip.setText(
                f"🖥 {rep.gpu_name}\n{rep.backend_label} · {vram}内存 {rep.ram_total_gb} GB\n"
                f"{rep.policy_label}")
        mk = self.ctx.pages.get("market")
        if mk:
            mk.refresh_hardware()
        self.ctx.status("硬件检测完成")

    def closeEvent(self, e):
        mk = self.ctx.pages.get("market")
        if mk:
            mk.cancel_all()
        try:
            self._qset.setValue("geometry", self.saveGeometry())
        except Exception:
            pass
        self.ctx.settings.save()
        super().closeEvent(e)
