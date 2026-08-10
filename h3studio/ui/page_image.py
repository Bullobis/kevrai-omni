# -*- coding: utf-8 -*-
"""
ui/page_image.py — 图片生成页
==============================
提示词（含负向，图片模型有效）+ 模型选择 + 参数 + 生成 + 预览。
"""

import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from ..facts import BUNDLES
from ..i18n import tr
from ..image_gen import ImageParams
from .widgets import GlassPanel


IMG_RATIOS = {
    "1:1": (1024, 1024),
    "16:9": (1344, 768),
    "9:16": (768, 1344),
    "4:3": (1152, 864),
    "3:4": (864, 1152),
}
IMG_RES_SCALE = {"512": 0.5, "768": 0.75, "1024": 1.0}


class ImageWorker(QThread):
    progress = Signal(int, int)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, engine, params: ImageParams):
        super().__init__()
        self.engine = engine
        self.params = params

    def run(self):
        try:
            out = self.engine.generate(
                self.params,
                progress_cb=lambda s, t: self.progress.emit(s, t))
            self.finished_ok.emit(out)
        except Exception as e:
            self.failed.emit(str(e))


class ImagePage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.worker = None
        self._last_image = ""
        self._build()
        self.refresh_models()

    # ═══════════════════════════════════════════════════════
    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ── 左：提示词 ──
        left = GlassPanel()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(16, 16, 16, 16)
        lv.setSpacing(10)

        t = QLabel(tr("img_title"))
        t.setObjectName("sectionTitle")
        lv.addWidget(t)
        hint = QLabel(tr("img_prompt_hint"))
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        lv.addWidget(hint)

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("一只戴墨镜的柴犬坐在冲浪板上，海浪飞溅，电影感光影，超写实。")
        self.prompt_edit.setMinimumHeight(160)
        lv.addWidget(self.prompt_edit, 3)

        neg_t = QLabel(tr("img_neg"))
        neg_t.setObjectName("sectionTitle")
        lv.addWidget(neg_t)
        self.neg_edit = QPlainTextEdit()
        self.neg_edit.setPlaceholderText("low quality, blurry, extra fingers…")
        self.neg_edit.setMaximumHeight(64)
        lv.addWidget(self.neg_edit, 1)
        lv.addStretch(1)

        # ── 中：参数 ──
        center = GlassPanel()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(16, 14, 16, 14)
        cv.setSpacing(10)

        pt = QLabel(tr("gen_params_title"))
        pt.setObjectName("sectionTitle")
        cv.addWidget(pt)

        m_row = QHBoxLayout()
        m_row.addWidget(QLabel(tr("img_model")))
        self.model_combo = QComboBox()
        m_row.addWidget(self.model_combo, 1)
        cv.addLayout(m_row)
        m_hint = QLabel(tr("img_model_tip"))
        m_hint.setObjectName("hintLabel")
        m_hint.setWordWrap(True)
        cv.addWidget(m_hint)

        r_row = QHBoxLayout()
        r_row.addWidget(QLabel(tr("img_ratio")))
        self.ratio_combo = QComboBox()
        for r in IMG_RATIOS:
            self.ratio_combo.addItem(r, r)
        r_row.addWidget(self.ratio_combo, 1)
        r_row.addSpacing(12)
        r_row.addWidget(QLabel(tr("img_res")))
        self.res_combo = QComboBox()
        self.res_combo.addItem("512 · 快速", "512")
        self.res_combo.addItem("768 · 均衡", "768")
        self.res_combo.addItem("1024 · 高清", "1024")
        self.res_combo.setCurrentIndex(2)
        r_row.addWidget(self.res_combo, 1)
        cv.addLayout(r_row)

        s_row = QHBoxLayout()
        s_row.addWidget(QLabel(tr("img_steps")))
        self.steps_spin = QSpinBox()
        self.steps_spin.setRange(1, 60)
        self.steps_spin.setValue(8)
        s_row.addWidget(self.steps_spin)
        s_row.addSpacing(12)
        s_row.addWidget(QLabel(tr("img_cfg")))
        self.cfg_spin = QSpinBox()
        self.cfg_spin.setRange(1, 15)
        self.cfg_spin.setValue(1)
        s_row.addWidget(self.cfg_spin)
        s_row.addSpacing(12)
        s_row.addWidget(QLabel(tr("img_seed")))
        self.seed_edit = QLineEdit("-1")
        self.seed_edit.setFixedWidth(100)
        self.seed_edit.setToolTip(tr("gen_seed_tip"))
        s_row.addWidget(self.seed_edit)
        cv.addLayout(s_row)

        eng_hint = QLabel(tr("img_engine_tip"))
        eng_hint.setObjectName("hintLabel")
        cv.addWidget(eng_hint)
        cv.addStretch(1)

        self.gen_btn = QPushButton(tr("img_start"))
        self.gen_btn.setObjectName("primaryBtn")
        self.gen_btn.setMinimumHeight(46)
        self.gen_btn.setCursor(Qt.PointingHandCursor)
        self.gen_btn.clicked.connect(self._start)
        cv.addWidget(self.gen_btn)

        # ── 右：进度 + 预览 ──
        right = GlassPanel()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(16, 14, 16, 14)
        rv.setSpacing(10)

        self.phase_label = QLabel(tr("gen_phase_ready"))
        rv.addWidget(self.phase_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        rv.addWidget(self.progress_bar)

        self.preview_label = QLabel("🖼️")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(320, 320)
        self.preview_label.setStyleSheet(
            "background: rgba(0,0,0,0.35); border-radius: 12px; font-size: 42px;")
        rv.addWidget(self.preview_label, 1)

        self.info_label = QLabel("")
        self.info_label.setObjectName("dimText")
        self.info_label.setWordWrap(True)
        rv.addWidget(self.info_label)

        root.addWidget(left, 3)
        root.addWidget(center, 3)
        root.addWidget(right, 3)

    # ═══════════════════════════════════════════════════════
    def refresh_models(self):
        """扫描已下载的图片模型。"""
        cur = self.model_combo.currentData()
        self.model_combo.clear()
        models_dir = self.ctx.settings.get("models_dir")
        found = 0
        for b in BUNDLES:
            if b.get("category") != "image" or b.get("engine") != "builtin":
                continue
            bdir = os.path.join(models_dir, b["id"])
            if os.path.isdir(bdir) and os.path.isdir(os.path.join(bdir, "transformer")):
                self.model_combo.addItem(b["name"], b["id"])
                found += 1
        if found == 0:
            self.model_combo.addItem(tr("img_model_none"), "")
        elif cur:
            i = self.model_combo.findData(cur)
            if i >= 0:
                self.model_combo.setCurrentIndex(i)
        # 按模型类型设置默认步数/CFG
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._on_model_changed()

    def _on_model_changed(self, *_):
        bid = self.model_combo.currentData()
        b = next((x for x in BUNDLES if x["id"] == bid), None)
        if b and b.get("image_engine") == "qwen_image":
            self.steps_spin.setValue(30)
            self.cfg_spin.setValue(4)
        else:
            self.steps_spin.setValue(8)
            self.cfg_spin.setValue(1)

    def _current_dims(self):
        ratio = self.ratio_combo.currentData()
        scale = IMG_RES_SCALE[self.res_combo.currentData()]
        w, h = IMG_RATIOS[ratio]
        w = int(w * scale) // 32 * 32
        h = int(h * scale) // 32 * 32
        return max(w, 64), max(h, 64)

    # ═══════════════════════════════════════════════════════
    def _start(self):
        prompt = self.prompt_edit.toPlainText().strip()
        if not prompt:
            self.ctx.toast("请先输入提示词" if self.ctx.settings.get("language") != "en"
                           else "Please enter a prompt")
            return
        bid = self.model_combo.currentData()
        if not bid:
            self.ctx.toast(tr("img_model_none"))
            self.ctx.window.goto_page("market")
            return

        from ..image_gen import get_image_engine
        eng = get_image_engine()
        b = next(x for x in BUNDLES if x["id"] == bid)
        bdir = os.path.join(self.ctx.settings.get("models_dir"), bid)

        if not eng.ready or eng.loaded_dir != bdir:
            # 与视频引擎互斥：先卸载视频引擎
            try:
                if self.ctx.engine.ready:
                    self.ctx.engine.unload()
                hw = self.ctx.hw
                policy = hw.policy if (hw and hw.policy != "unsupported") else "balanced"
                eng.load(b.get("image_engine", "z_image"), bdir, policy,
                         vram_budget_gb=float(self.ctx.settings.get("vram_budget_gb")))
            except Exception as e:
                self.ctx.toast(f"模型加载失败：{str(e)[:120]}")
                return

        try:
            seed = int(self.seed_edit.text().strip())
        except ValueError:
            seed = -1
        w, h = self._current_dims()
        params = ImageParams(
            prompt=prompt,
            negative_prompt=self.neg_edit.toPlainText().strip(),
            width=w, height=h,
            steps=self.steps_spin.value(),
            cfg_scale=float(self.cfg_spin.value()),
            seed=seed,
            output_dir=self.ctx.settings.get("outputs_dir"),
            output_prefix=str(self.ctx.settings.get("output_prefix")),
        )

        self.gen_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.phase_label.setText(tr("gen_phase_starting"))
        self.worker = ImageWorker(eng, params)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, step, total):
        if total > 0:
            self.progress_bar.setValue(int(step / total * 100))
            self.phase_label.setText(f"{step}/{total}")

    def _on_done(self, out):
        self.progress_bar.setValue(100)
        self.phase_label.setText("✅ " + tr("gen_phase_ready"))
        self.gen_btn.setEnabled(True)
        self._last_image = out["image_path"]
        pm = QPixmap(out["image_path"])
        if not pm.isNull():
            self.preview_label.setPixmap(pm.scaled(
                self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.info_label.setText(
            f"{os.path.basename(out['image_path'])} · {out['elapsed_s']}s · seed {out['seed']}")
        self.ctx.status(f"图片生成完成：{os.path.basename(out['image_path'])}")
        self.ctx.gallery_dirty()

    def _on_failed(self, err):
        self.gen_btn.setEnabled(True)
        self.phase_label.setText("❌")
        self.ctx.toast("生成失败：" + err[:150])

    def showEvent(self, e):
        self.refresh_models()
        super().showEvent(e)
