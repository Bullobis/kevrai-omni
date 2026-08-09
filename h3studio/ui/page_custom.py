# -*- coding: utf-8 -*-
"""
ui/page_custom.py — DIY 自定义打包页
=====================================
用户自选组件拼包 + 兼容性实时校验（错误拒绝下载，警告提示风险）+ 预设一键导入。
"""

import shutil

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import customizer
from ..facts import DIY_COMPONENTS
from .widgets import GlassPanel


class CustomPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._combos = {}
        self._build()
        QTimer.singleShot(400, self._refresh_validation)

    # ═══════════════════════════════════════════════════════
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ── 引擎选择 + 预设 ──
        head = GlassPanel(strong=True)
        hv = QVBoxLayout(head)
        hv.setContentsMargins(16, 12, 16, 12)
        hv.setSpacing(8)
        t = QLabel("🧩 DIY 自定义打包（选组件 → 自动校验 → 下载）")
        t.setObjectName("sectionTitle")
        hv.addWidget(t)
        hint = QLabel(
            "自由搭配组件时，软件会按官方/引擎的硬性规则实时校验：量化格式必须成套、分区必须匹配、"
            "显存/磁盘不够会直接拒绝下载，防止你的电脑卡死。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        hv.addWidget(hint)

        eng_row = QHBoxLayout()
        eng_row.addWidget(QLabel("目标引擎"))
        self.eng_combo = QComboBox()
        self.eng_combo.addItem("内置引擎 DiffSynth（本软件直接推理）", "diffsynth")
        self.eng_combo.addItem("ComfyUI（工作流路线）", "comfyui")
        self.eng_combo.currentIndexChanged.connect(lambda _: self._refresh_validation())
        eng_row.addWidget(self.eng_combo, 1)
        hv.addLayout(eng_row)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("快速预设"))
        presets = [
            ("⚡ 最优方案", self._preset_optimal),
            ("NF4 全套（FL2VA）", self._preset_nf4_fl2va),
            ("NF4 全套（Ref2VA）", self._preset_nf4_ref2va),
            ("ComfyUI INT8 16G 档", self._preset_comfy_int8),
            ("GGUF 低显存档", self._preset_gguf),
            ("清空重选", self._preset_clear),
        ]
        for label, fn in presets:
            b = QPushButton(label)
            b.setObjectName("chipBtn")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            preset_row.addWidget(b)
        preset_row.addStretch(1)
        hv.addLayout(preset_row)
        root.addWidget(head)

        # ── 组件选择区 ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        wrap = QWidget()
        wv = QVBoxLayout(wrap)
        wv.setContentsMargins(0, 0, 6, 0)
        wv.setSpacing(10)

        card = GlassPanel()
        cv = QVBoxLayout(card)
        cv.setContentsMargins(16, 14, 16, 14)
        cv.setSpacing(8)
        for cat in customizer.CATEGORIES:
            row = QHBoxLayout()
            lab = QLabel(customizer.CATEGORY_LABELS[cat])
            lab.setMinimumWidth(180)
            row.addWidget(lab)
            combo = QComboBox()
            combo.addItem("（不选）", "")
            for c in DIY_COMPONENTS.get(cat, []):
                combo.addItem(f"{c['name']}　[{c['size_gb']}GB]", c["id"])
            combo.currentIndexChanged.connect(lambda _: self._refresh_validation())
            row.addWidget(combo, 1)
            self._combos[cat] = combo
            cv.addLayout(row)
        wv.addWidget(card)

        # ── 校验结果区 ──
        vcard = GlassPanel(strong=True)
        vv = QVBoxLayout(vcard)
        vv.setContentsMargins(16, 12, 16, 12)
        vv.setSpacing(6)
        self.valid_label = QLabel("")
        self.valid_label.setWordWrap(True)
        self.valid_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        vv.addWidget(self.valid_label)
        root2 = QHBoxLayout()
        self.total_label = QLabel("")
        self.total_label.setObjectName("sectionTitle")
        root2.addWidget(self.total_label, 1)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("给这个包起个名字（可选）")
        self.name_edit.setMaximumWidth(220)
        root2.addWidget(self.name_edit)
        self.dl_btn = QPushButton("⬇ 校验通过，开始下载")
        self.dl_btn.setObjectName("primaryBtn")
        self.dl_btn.setEnabled(False)
        self.dl_btn.clicked.connect(self._start_custom_download)
        root2.addWidget(self.dl_btn)
        vv.addLayout(root2)
        wv.addWidget(vcard)
        wv.addStretch(1)
        scroll.setWidget(wrap)
        root.addWidget(scroll, 1)

    # ═══════════════════════════════════════════════════════
    def _selections(self):
        return {cat: combo.currentData() for cat, combo in self._combos.items()}

    def _refresh_validation(self):
        engine = self.eng_combo.currentData()
        selections = self._selections()
        hw = self.ctx.hw
        disk_free = None
        try:
            du = shutil.disk_usage(self.ctx.settings.get("models_dir"))
            disk_free = du.free / (1024 ** 3)
        except Exception:
            pass
        errors, warnings = customizer.validate_pack(engine, selections, hw, disk_free)
        total = customizer.pack_total_size(selections)

        lines = []
        if errors:
            lines += [f"❌ {e}" for e in errors]
        if warnings:
            lines += [f"⚠️ {w}" for w in warnings]
        if not errors and not warnings and any(selections.values()):
            lines.append("✅ 校验通过：组件成套、分区匹配、硬件可承载")
        if not any(selections.values()):
            lines.append("请选择组件（或用上方快速预设一键填充）")
        self.valid_label.setText("\n".join(lines))
        self.total_label.setText(f"包体合计：{total} GB")
        self.dl_btn.setEnabled(not errors and bool(selections.get("dit")))

    # ═══════════════════════════════════════════════════════
    def _set(self, mapping):
        for cat, cid in mapping.items():
            combo = self._combos.get(cat)
            if combo is None:
                continue
            i = combo.findData(cid or "")
            combo.setCurrentIndex(i if i >= 0 else 0)
        self._refresh_validation()

    def _preset_optimal(self):
        """按规划器的最优方案填充 DIY 选择。"""
        from ..planner import make_plan
        hw = self.ctx.hw
        if hw is None or hw.policy == "unsupported":
            self.ctx.toast("未检测到加速硬件，无法计算最优方案")
            return
        plan = make_plan(hw)
        if plan.bundle_id.startswith("nf4_full"):
            self.eng_combo.setCurrentIndex(self.eng_combo.findData("diffsynth"))
            self._set({"dit": "nf4_fl2va", "text_encoder": "nf4_te", "video_vae": "nf4_vvae",
                       "audio_vae": "nf4_avvae", "processor": "proc_fl2va"})
        elif plan.bundle_id == "nf4_fl2va":
            self.eng_combo.setCurrentIndex(self.eng_combo.findData("diffsynth"))
            self._set({"dit": "nf4_fl2va", "text_encoder": "nf4_te", "video_vae": "nf4_vvae",
                       "audio_vae": "nf4_avvae", "processor": "proc_fl2va"})
        elif plan.bundle_id.startswith("bf16"):
            self.eng_combo.setCurrentIndex(self.eng_combo.findData("diffsynth"))
            self._set({"dit": "bf16_fl2va", "text_encoder": "bf16_te", "video_vae": "official_vvae",
                       "audio_vae": "official_avvae", "processor": "proc_fl2va"})
        elif plan.bundle_id.startswith("gguf"):
            self.eng_combo.setCurrentIndex(self.eng_combo.findData("comfyui"))
            self._set({"dit": "gguf_q4_fl2va", "text_encoder": "gguf_te_q4",
                       "video_vae": "comfy_vvae_fp16", "audio_vae": "comfy_avvae_fp32"})
        else:
            self.eng_combo.setCurrentIndex(self.eng_combo.findData("comfyui"))
            self._set({"dit": "comfy_pruned_int8_fl2va", "text_encoder": "comfy_te_nvfp4",
                       "video_vae": "comfy_vvae_fp16", "audio_vae": "comfy_avvae_fp32"})
        self.ctx.toast("已按你的硬件填入最优组合，可再微调")

    def _preset_nf4_fl2va(self):
        self.eng_combo.setCurrentIndex(self.eng_combo.findData("diffsynth"))
        self._set({"dit": "nf4_fl2va", "text_encoder": "nf4_te", "video_vae": "nf4_vvae",
                   "audio_vae": "nf4_avvae", "processor": "proc_fl2va"})

    def _preset_nf4_ref2va(self):
        self.eng_combo.setCurrentIndex(self.eng_combo.findData("diffsynth"))
        self._set({"dit": "nf4_ref2va", "text_encoder": "nf4_te", "video_vae": "nf4_vvae",
                   "audio_vae": "nf4_avvae", "processor": "proc_ref2va"})

    def _preset_comfy_int8(self):
        self.eng_combo.setCurrentIndex(self.eng_combo.findData("comfyui"))
        self._set({"dit": "comfy_pruned_int8_fl2va", "text_encoder": "comfy_te_nvfp4",
                   "video_vae": "comfy_vvae_fp16", "audio_vae": "comfy_avvae_fp32",
                   "processor": ""})

    def _preset_gguf(self):
        self.eng_combo.setCurrentIndex(self.eng_combo.findData("comfyui"))
        self._set({"dit": "gguf_q4_fl2va", "text_encoder": "gguf_te_q4",
                   "video_vae": "comfy_vvae_fp16", "audio_vae": "comfy_avvae_fp32",
                   "processor": ""})

    def _preset_clear(self):
        self._set({cat: "" for cat in customizer.CATEGORIES})

    # ═══════════════════════════════════════════════════════
    def _start_custom_download(self):
        from pathlib import Path
        from ..downloader import BundleDownloadTask

        engine = self.eng_combo.currentData()
        selections = self._selections()
        hw = self.ctx.hw
        try:
            du = shutil.disk_usage(self.ctx.settings.get("models_dir"))
            disk_free = du.free / (1024 ** 3)
        except Exception:
            disk_free = None
        errors, _ = customizer.validate_pack(engine, selections, hw, disk_free)
        if errors:
            self.ctx.toast("存在校验错误，无法下载（详见红色提示）")
            return

        bundle = customizer.build_custom_bundle(engine, selections, self.name_edit.text())
        dest = Path(self.ctx.settings.get("models_dir")) / bundle["id"]

        # 选源：测速最优 or 默认
        source = self.ctx.settings.get("preferred_source_last_best", "modelscope")
        task = BundleDownloadTask(bundle_id=bundle["id"], source_key=source, dest_dir=dest,
                                  bundle=bundle,
                                  retries=int(self.ctx.settings.get("download_retries")))
        self.ctx.downloads[bundle["id"]] = task
        task.run()
        self.ctx.toast(f"自定义包开始下载（{bundle['size_gb']}GB，源：{source}），进度见模型市场页")
        self.ctx.window.goto_page("market")
