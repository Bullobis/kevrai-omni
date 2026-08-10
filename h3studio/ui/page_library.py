# -*- coding: utf-8 -*-
"""
ui/page_library.py — 我的模型（已安装模型 + LoRA 管理）
"""

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from .. import facts
from .widgets import GlassPanel, clear_layout


def _resource_path(rel: str) -> str:
    """资源文件路径（兼容开发模式与 PyInstaller 打包模式）。

    开发模式：本文件位于 h3studio/ui/page_library.py，向上三级到项目根目录。
    打包模式：资源被 PyInstaller 释放到 sys._MEIPASS 下。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, rel)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, rel)


class LibraryPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ── 已安装模型 ──
        card = GlassPanel()
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        t = QLabel("已安装的模型")
        t.setObjectName("sectionTitle")
        v.addWidget(t)
        hint = QLabel("内置引擎可加载的模型会显示「加载」按钮；ComfyUI 专用模型请按卡片说明放入 ComfyUI 对应目录。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.model_list_container = QVBoxLayout()
        v.addLayout(self.model_list_container)
        root.addWidget(card)

        # ── LoRA ──
        lora_card = GlassPanel()
        lv = QVBoxLayout(lora_card)
        lv.setContentsMargins(16, 14, 16, 14)
        lv.setSpacing(8)
        lt_row = QHBoxLayout()
        lt = QLabel("LoRA 嵌入模型（社区微调 / 加速）")
        lt.setObjectName("sectionTitle")
        lt_row.addWidget(lt)
        lt_row.addStretch(1)
        import_btn = QPushButton("导入 LoRA 文件…")
        import_btn.setObjectName("primaryBtn")
        import_btn.clicked.connect(self._import_lora)
        lt_row.addWidget(import_btn)
        open_btn = QPushButton("打开目录")
        open_btn.clicked.connect(self._open_lora_dir)
        lt_row.addWidget(open_btn)
        lv.addLayout(lt_row)
        lora_hint = QLabel("支持 .safetensors / .bin / .pt。导入后可在生成页右侧「嵌入模型」中选择并调节强度。Turbo 类 LoRA 请配合 4 步采样使用。")
        lora_hint.setObjectName("hintLabel")
        lora_hint.setWordWrap(True)
        lv.addWidget(lora_hint)
        self.lora_list = QListWidget()
        self.lora_list.setMaximumHeight(160)
        lv.addWidget(self.lora_list)
        rm_row = QHBoxLayout()
        rm_btn = QPushButton("删除选中")
        rm_btn.setObjectName("dangerBtn")
        rm_btn.clicked.connect(self._remove_lora)
        rm_row.addWidget(rm_btn)
        rm_row.addStretch(1)
        lv.addLayout(rm_row)
        root.addWidget(lora_card)

        root.addStretch(1)
        root.addStretch(1)

        self.refresh()

    # ═══════════════════════════════════════════════════════
    def refresh(self):
        clear_layout(self.model_list_container)
        installed_any = False
        for b in facts.BUNDLES:
            state, done_gb = self.ctx.bundle_state(b)
            if state == "missing":
                continue
            installed_any = True
            row = QHBoxLayout()
            icon = "✅" if state == "complete" else "⏸"
            name = QLabel(f"{icon} {b['name']}")
            row.addWidget(name, 1)
            info = QLabel(f"{done_gb:.1f} / {b['size_gb']} GB · {b['precision']}")
            info.setObjectName("dimText")
            row.addWidget(info)
            if state == "complete":
                if b["engine"] == "builtin":
                    partitions = ["FL2VA", "Ref2VA"] if b["partition"] == "FL2VA+Ref2VA" else [b["partition"]]
                    for pt in partitions:
                        load_btn = QPushButton(f"加载 {pt}")
                        load_btn.clicked.connect(
                            lambda _=False, bb=b, pp=pt: self.ctx.load_model(bb, pp))
                        row.addWidget(load_btn)
                elif b["engine"] == "comfyui":
                    tip = QLabel("ComfyUI 专用：将文件复制到 ComfyUI 的 models/ 对应目录即可")
                    tip.setObjectName("hintLabel")
                    row.addWidget(tip)
                    open_b = QPushButton("打开目录")
                    open_b.clicked.connect(
                        lambda _=False, bb=b: self.ctx.open_dir(
                            os.path.join(self.ctx.settings.get("models_dir"), bb["id"])))
                    row.addWidget(open_b)
            self.model_list_container.addLayout(row)

        # DIY 自定义包（custom_ 开头的目录）
        models_dir = self.ctx.settings.get("models_dir")
        if os.path.isdir(models_dir):
            for dn in sorted(os.listdir(models_dir)):
                if not dn.startswith("custom_"):
                    continue
                installed_any = True
                row = QHBoxLayout()
                name = QLabel(f"🧩 {dn}（DIY 自定义包）")
                row.addWidget(name, 1)
                tip = QLabel("内置引擎包：生成页直接可用；ComfyUI 组件请复制到 ComfyUI 目录")
                tip.setObjectName("hintLabel")
                row.addWidget(tip)
                open_b = QPushButton("打开目录")
                open_b.clicked.connect(
                    lambda _=False, dd=os.path.join(models_dir, dn): self.ctx.open_dir(dd))
                row.addWidget(open_b)
                self.model_list_container.addLayout(row)

        if not installed_any:
            empty = QLabel("还没有安装任何模型。请前往「模型市场」下载（推荐 NF4 量化版）。")
            empty.setObjectName("hintLabel")
            self.model_list_container.addWidget(empty)

        # LoRA
        self.lora_list.clear()
        loras_dir = self.ctx.settings.get("loras_dir")
        if os.path.isdir(loras_dir):
            for fn in sorted(os.listdir(loras_dir)):
                if fn.lower().endswith((".safetensors", ".bin", ".pt")):
                    it = QListWidgetItem(fn)
                    it.setData(Qt.UserRole, os.path.join(loras_dir, fn))
                    self.lora_list.addItem(it)

    # ═══════════════════════════════════════════════════════
    def _import_lora(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择 LoRA 文件", "", "LoRA 权重 (*.safetensors *.bin *.pt)")
        if not files:
            return
        import shutil
        from ..engine import validate_lora_file
        loras_dir = self.ctx.settings.get("loras_dir")
        os.makedirs(loras_dir, exist_ok=True)
        ok_n, warns = 0, []
        for f in files:
            dest = os.path.join(loras_dir, os.path.basename(f))
            try:
                shutil.copy2(f, dest)
                ok_n += 1
                is_lora, note = validate_lora_file(dest)
                if is_lora is False:
                    warns.append(f"{os.path.basename(f)}：{note}")
            except Exception as e:
                self.ctx.toast(f"导入失败：{e}")
        self.refresh()
        gp = self.ctx.pages.get("generate")
        if gp:
            gp.refresh_loras()
        if warns:
            self.ctx.toast(f"已导入 {ok_n} 个，但 " + warns[0] + "（仍可尝试加载）")
        else:
            self.ctx.toast(f"已导入 {ok_n} 个 LoRA（格式预检通过）")

    def _open_lora_dir(self):
        self.ctx.open_dir(self.ctx.settings.get("loras_dir"))

    def _remove_lora(self):
        for it in self.lora_list.selectedItems():
            try:
                os.remove(it.data(Qt.UserRole))
            except Exception:
                pass
        self.refresh()
        gp = self.ctx.pages.get("generate")
        if gp:
            gp.refresh_loras()
