# -*- coding: utf-8 -*-
"""
ui/page_settings.py — 设置（主题 / 个性化 / 目录 / 协议 / 关于）
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox,
                               QDoubleSpinBox, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QVBoxLayout,
                               QWidget)

from .. import facts
from ..config import THEMES
from ..engine import check_engine_ready
from .widgets import GlassPanel


class SettingsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._build()

    def _card(self, title):
        card = GlassPanel()
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)
        t = QLabel(title)
        t.setObjectName("sectionTitle")
        v.addWidget(t)
        return card, v

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        wrap = QWidget()
        wv = QVBoxLayout(wrap)
        wv.setContentsMargins(0, 0, 6, 0)
        wv.setSpacing(10)
        scroll.setWidget(wrap)
        root.addWidget(scroll)

        # ── 外观个性化 ──
        card, v = self._card("外观与个性化")

        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("主题"))
        self.theme_group = QButtonGroup(self)
        self._theme_btns = {}
        for key, td in THEMES.items():
            b = QPushButton(td["label"])
            b.setObjectName("chipBtn")
            b.setCheckable(True)
            self.theme_group.addButton(b)
            self._theme_btns[key] = b
            theme_row.addWidget(b)
            b.toggled.connect(lambda _c, k=key: self._set_theme(k))
        cur = self.ctx.settings.get("theme")
        if cur in self._theme_btns:
            self._theme_btns[cur].setChecked(True)
        theme_row.addStretch(1)
        v.addLayout(theme_row)

        acc_row = QHBoxLayout()
        acc_row.addWidget(QLabel("强调色"))
        self.accent_preview = QLabel("  ●  ")
        acc_row.addWidget(self.accent_preview)
        acc_btn = QPushButton("自定义强调色…")
        acc_btn.clicked.connect(self._pick_accent)
        acc_row.addWidget(acc_btn)
        acc_row.addStretch(1)
        v.addLayout(acc_row)
        self._update_accent_preview()

        fs_row = QHBoxLayout()
        fs_row.addWidget(QLabel("界面字号"))
        self.fs_slider = QSlider(Qt.Horizontal)
        self.fs_slider.setRange(85, 130)
        self.fs_slider.setValue(int(self.ctx.settings.get("font_scale")))
        fs_row.addWidget(self.fs_slider, 1)
        self.fs_label = QLabel(f"{self.ctx.settings.get('font_scale')}%")
        fs_row.addWidget(self.fs_label)
        self.fs_slider.valueChanged.connect(self._set_font_scale)
        v.addLayout(fs_row)

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("界面语言"))
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("跟随系统（非中英文默认英文）", "auto")
        self.lang_combo.addItem("中文", "zh")
        self.lang_combo.addItem("English", "en")
        i = self.lang_combo.findData(self.ctx.settings.get("language", "auto"))
        if i >= 0:
            self.lang_combo.setCurrentIndex(i)
        self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        lang_row.addWidget(self.lang_combo, 1)
        lang_note = QLabel("切换语言立即保存；部分界面在下次打开时完整生效。")
        lang_note.setObjectName("hintLabel")
        v.addLayout(lang_row)
        v.addWidget(lang_note)

        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("玻璃透明度"))
        self.op_slider = QSlider(Qt.Horizontal)
        self.op_slider.setRange(50, 95)
        self.op_slider.setValue(int(self.ctx.settings.get("glass_opacity")))
        op_row.addWidget(self.op_slider, 1)
        self.op_label = QLabel(str(self.ctx.settings.get("glass_opacity")))
        op_row.addWidget(self.op_label)
        self.op_slider.valueChanged.connect(self._set_opacity)
        v.addLayout(op_row)

        wv.addWidget(card)

        # ── 目录 ──
        card, v = self._card("文件目录")
        for key, label in [("models_dir", "模型目录"), ("outputs_dir", "作品输出目录"),
                           ("loras_dir", "LoRA 目录")]:
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            edit = QLineEdit(self.ctx.settings.get(key))
            row.addWidget(edit, 1)
            browse = QPushButton("浏览…")

            def _pick(_=False, k=key, e=edit):
                d = QFileDialog.getExistingDirectory(self, "选择目录", e.text())
                if d:
                    e.setText(d)
                    self.ctx.settings.set(k, d)
                    self.ctx.toast("目录已更新")

            browse.clicked.connect(_pick)
            row.addWidget(browse)
            v.addLayout(row)
        note = QLabel("提示：模型目录建议放在 NVMe 固态硬盘上，低显存模式会从硬盘流式加载权重。")
        note.setObjectName("hintLabel")
        v.addWidget(note)
        wv.addWidget(card)

        # ── 推理引擎高级设置 ──
        card, v = self._card("推理引擎 · 显存与性能")

        row = QHBoxLayout()
        row.addWidget(QLabel("显存预算 (GB)"))
        self.vram_spin = QDoubleSpinBox()
        self.vram_spin.setRange(-1, 160)
        self.vram_spin.setSingleStep(1.0)
        self.vram_spin.setValue(float(self.ctx.settings.get("vram_budget_gb")))
        self.vram_spin.setToolTip("-1 = 自动（可用显存 - 2GB）；手动填写则固定预算")
        row.addWidget(self.vram_spin)
        row.addSpacing(16)
        row.addWidget(QLabel("卸载策略"))
        self.offload_combo = QComboBox()
        self.offload_combo.addItem("自动（按显卡分档）", "auto")
        self.offload_combo.addItem("强制内存卸载（速度快，吃内存）", "cpu")
        self.offload_combo.addItem("强制磁盘流式（省内存，较慢）", "disk")
        i = self.offload_combo.findData(self.ctx.settings.get("offload_mode"))
        if i >= 0:
            self.offload_combo.setCurrentIndex(i)
        row.addWidget(self.offload_combo, 1)
        v.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("CPU 线程数"))
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(-1, 128)
        self.threads_spin.setValue(int(self.ctx.settings.get("torch_threads")))
        self.threads_spin.setToolTip("-1 = 自动")
        row2.addWidget(self.threads_spin)
        row2.addStretch(1)
        v.addLayout(row2)

        eng_hint = QLabel("说明：显存管理为三级流转（硬盘→内存→显存）。8~12GB 显存建议保持自动；内存小于 16GB 时可选磁盘流式。")
        eng_hint.setObjectName("hintLabel")
        eng_hint.setWordWrap(True)
        v.addWidget(eng_hint)
        save_eng = QPushButton("保存引擎设置")
        save_eng.clicked.connect(self._save_engine_settings)
        v.addWidget(save_eng, 0, Qt.AlignLeft)
        wv.addWidget(card)

        # ── 高级生成参数 ──
        card, v = self._card("高级生成参数（默认值即 H3 官方推荐值）")

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("CFG 强度"))
        self.cfg_spin = QDoubleSpinBox()
        self.cfg_spin.setRange(1.0, 15.0)
        self.cfg_spin.setSingleStep(0.5)
        self.cfg_spin.setValue(float(self.ctx.settings.get("cfg_scale")))
        self.cfg_spin.setToolTip("H3 为 CFG 蒸馏模型，默认 1.0；调高会增强提示词约束但可能失真")
        r1.addWidget(self.cfg_spin)
        r1.addSpacing(16)
        r1.addWidget(QLabel("视频 flow_shift"))
        self.fs_spin = QDoubleSpinBox()
        self.fs_spin.setRange(1.0, 30.0)
        self.fs_spin.setSingleStep(0.5)
        self.fs_spin.setValue(float(self.ctx.settings.get("flow_shift")))
        r1.addWidget(self.fs_spin)
        r1.addSpacing(16)
        r1.addWidget(QLabel("音频 flow_shift"))
        self.afs_spin = QDoubleSpinBox()
        self.afs_spin.setRange(1.0, 15.0)
        self.afs_spin.setSingleStep(0.5)
        self.afs_spin.setValue(float(self.ctx.settings.get("audio_flow_shift")))
        r1.addWidget(self.afs_spin)
        r1.addStretch(1)
        v.addLayout(r1)

        r2 = QHBoxLayout()
        self.tiled_chk = QCheckBox("分块 VAE 解码（省显存，推荐开启）")
        self.tiled_chk.setChecked(bool(self.ctx.settings.get("tiled_vae")))
        r2.addWidget(self.tiled_chk)
        r2.addSpacing(12)
        r2.addWidget(QLabel("块大小"))
        self.tile_spin = QSpinBox()
        self.tile_spin.setRange(64, 512)
        self.tile_spin.setSingleStep(32)
        self.tile_spin.setValue(int(self.ctx.settings.get("tile_size")))
        r2.addWidget(self.tile_spin)
        r2.addSpacing(12)
        r2.addWidget(QLabel("块重叠"))
        self.overlap_spin = QSpinBox()
        self.overlap_spin.setRange(0, 128)
        self.overlap_spin.setSingleStep(8)
        self.overlap_spin.setValue(int(self.ctx.settings.get("tile_overlap")))
        r2.addWidget(self.overlap_spin)
        r2.addStretch(1)
        v.addLayout(r2)

        r3 = QHBoxLayout()
        r3.addWidget(QLabel("噪声设备"))
        self.rand_combo = QComboBox()
        self.rand_combo.addItem("CPU（跨显卡结果一致，推荐）", "cpu")
        self.rand_combo.addItem("GPU（不同显卡结果不同）", "cuda")
        i = self.rand_combo.findData(self.ctx.settings.get("rand_device"))
        if i >= 0:
            self.rand_combo.setCurrentIndex(i)
        r3.addWidget(self.rand_combo)
        r3.addSpacing(16)
        r3.addWidget(QLabel("输出文件名前缀"))
        self.prefix_edit = QLineEdit(str(self.ctx.settings.get("output_prefix")))
        self.prefix_edit.setFixedWidth(100)
        r3.addWidget(self.prefix_edit)
        r3.addSpacing(16)
        self.meta_chk = QCheckBox("保存参数元数据 JSON")
        self.meta_chk.setChecked(bool(self.ctx.settings.get("save_metadata")))
        r3.addWidget(self.meta_chk)
        r3.addStretch(1)
        v.addLayout(r3)

        save_adv = QPushButton("保存高级参数")
        save_adv.clicked.connect(self._save_adv_settings)
        v.addWidget(save_adv, 0, Qt.AlignLeft)
        wv.addWidget(card)

        # ── 下载设置 ──
        card, v = self._card("下载设置")
        d1 = QHBoxLayout()
        d1.addWidget(QLabel("默认下载源"))
        self.src_combo = QComboBox()
        self.src_combo.addItem("自动（按测速结果）", "auto")
        self.src_combo.addItem("魔搭 ModelScope", "modelscope")
        self.src_combo.addItem("HF-Mirror 镜像", "hf_mirror")
        self.src_combo.addItem("HuggingFace 原站", "hf")
        i = self.src_combo.findData(self.ctx.settings.get("preferred_source"))
        if i >= 0:
            self.src_combo.setCurrentIndex(i)
        d1.addWidget(self.src_combo, 1)
        v.addLayout(d1)
        d2 = QHBoxLayout()
        d2.addWidget(QLabel("测速采样大小 (MB)"))
        self.probe_spin = QSpinBox()
        self.probe_spin.setRange(1, 32)
        self.probe_spin.setValue(int(self.ctx.settings.get("probe_sample_mb")))
        d2.addWidget(self.probe_spin)
        d2.addSpacing(16)
        d2.addWidget(QLabel("下载重试次数"))
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(1, 20)
        self.retry_spin.setValue(int(self.ctx.settings.get("download_retries")))
        d2.addWidget(self.retry_spin)
        d2.addStretch(1)
        v.addLayout(d2)
        save_dl = QPushButton("保存下载设置")
        save_dl.clicked.connect(self._save_dl_settings)
        v.addWidget(save_dl, 0, Qt.AlignLeft)
        wv.addWidget(card)

        # ── 硬件检测与一键最优配置 ──
        card, v = self._card("硬件检测 · 一键最优配置")
        self.hw_info = QLabel("正在检测硬件…")
        self.hw_info.setObjectName("hintLabel")
        self.hw_info.setWordWrap(True)
        self.hw_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.hw_info)
        hw_row = QHBoxLayout()
        rescan = QPushButton("🔄 重新检测")
        rescan.clicked.connect(self._rescan_hw)
        hw_row.addWidget(rescan)
        apply_btn = QPushButton("⚡ 应用推荐配置")
        apply_btn.setObjectName("primaryBtn")
        apply_btn.clicked.connect(self._apply_recommended)
        hw_row.addWidget(apply_btn)
        hw_row.addStretch(1)
        v.addLayout(hw_row)
        wv.addWidget(card)
        QTimer.singleShot(300, self._refresh_hw_info)

        # ── 引擎环境 ──
        card, v = self._card("推理引擎环境")
        ok, msg = check_engine_ready()
        st = QLabel(("✅ " if ok else "⚠ ") + msg)
        if not ok:
            st.setStyleSheet("color:#facc15;")
        st.setWordWrap(True)
        v.addWidget(st)
        inst = QPlainTextEdit()
        inst.setReadOnly(True)
        inst.setPlainText(
            "# 一键安装引擎环境（在 Windows 终端执行）：\n"
            "# RTX 20~50 系用 cu128；GTX 10/900 系老卡改用 cu126\n"
            "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128\n"
            "pip install diffsynth>=2.1.0 av requests pillow psutil imageio imageio-ffmpeg\n"
            "pip install bitsandbytes   # NF4 量化支持（Windows 11 需 0.43+）")
        inst.setMaximumHeight(120)
        v.addWidget(inst)
        copy_btn = QPushButton("复制安装命令")

        def _copy(_=False):
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(inst.toPlainText())
            self.ctx.toast("已复制到剪贴板")

        copy_btn.clicked.connect(_copy)
        v.addWidget(copy_btn)
        wv.addWidget(card)

        # ── 协议与合规 ──
        card, v = self._card("模型协议与合规提醒")
        lic = facts.MODEL_INFO["license"]
        lic_text = QLabel(
            f"MiniMax H3 权重采用《{lic['name']}》。\n"
            f"协议明确排除地区：{'、'.join(lic['regions_excluded'])}。\n"
            f"{lic['note']}\n"
            f"协议全文：{lic['url']}")
        lic_text.setObjectName("hintLabel")
        lic_text.setWordWrap(True)
        lic_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(lic_text)
        open_lic = QPushButton("查看协议原文（网页）")
        open_lic.clicked.connect(lambda: self.ctx.open_url(lic["url"]))
        v.addWidget(open_lic, 0, Qt.AlignLeft)
        wv.addWidget(card)

        # ── 关于 ──
        card, v = self._card("关于")
        # 多芯片支持矩阵（来自 facts.BACKEND_MATRIX，均已核实）
        matrix_lines = ["多芯片支持矩阵："]
        for row in facts.BACKEND_MATRIX:
            matrix_lines.append(f"  · {row['backend']}：{row['status']} —— {row['note']}")
        matrix_label = QLabel("\n".join(matrix_lines))
        matrix_label.setObjectName("hintLabel")
        matrix_label.setWordWrap(True)
        matrix_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(matrix_label)

        from .. import __version__
        about = QLabel(
            f"MiniMax H3 Studio · v{__version__}\n"
            f"创作者：Bullobis\n"
            f"开源地址：https://github.com/Bullobis/minimax-h3-studio\n"
            f"许可协议：CC BY-NC-SA 4.0（免费开源，禁止商用）\n"
            f"内置推理引擎：{facts.ENGINE_INFO['name']}（{facts.ENGINE_INFO['license']}，GitHub {facts.ENGINE_INFO['stars_verified_at']}）\n"
            f"模型：{facts.MODEL_INFO['name']}，{facts.MODEL_INFO['developer']}，"
            f"{facts.MODEL_INFO['release_date']} 发布，{facts.MODEL_INFO['open_source_date']} 开源\n"
            f"开源范围：{facts.MODEL_INFO['open_scope']}\n"
            f"{facts.MODEL_INFO['not_open']}\n\n"
            "本软件为社区工具，与 MiniMax 官方无关。请遵守模型协议及当地法律法规使用。")
        about.setObjectName("hintLabel")
        about.setWordWrap(True)
        about.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(about)
        lora_lines = ["LoRA / 嵌入模型支持状态："]
        for k, label in [("lora_inference", "LoRA 推理"), ("lora_training", "LoRA 训练"),
                         ("embeddings", "嵌入(Embeddings)"), ("extra_components", "组件自定义"),
                         ("stacking_note", "叠加机制")]:
            lora_lines.append(f"  · {label}：{facts.LORA_SUPPORT[k]}")
        lora_label = QLabel("\n".join(lora_lines))
        lora_label.setObjectName("hintLabel")
        lora_label.setWordWrap(True)
        lora_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(lora_label)

        link_row = QHBoxLayout()
        for name, url in [("⭐ 项目开源地址", "https://github.com/Bullobis/minimax-h3-studio"),
                          ("HF 官方仓库", "https://huggingface.co/MiniMaxAI/MiniMax-H3"),
                          ("魔搭官方仓库", "https://modelscope.cn/models/MiniMax/MiniMax-H3"),
                          ("DiffSynth-Studio", facts.ENGINE_INFO["repo"]),
                          ("官方博客", "https://www.minimaxi.com/blog/minimax-h3")]:
            b = QPushButton(name)
            b.clicked.connect(lambda _=False, u=url: self.ctx.open_url(u))
            link_row.addWidget(b)
        link_row.addStretch(1)
        v.addLayout(link_row)
        wv.addWidget(card)
        wv.addStretch(1)

    # ═══════════════════════════════════════════════════════
    def _refresh_hw_info(self):
        hw = self.ctx.hw
        if hw is None:
            self.hw_info.setText("正在检测硬件…")
            return
        vram = f"显存 {hw.vram_total_gb} GB" if hw.vram_total_gb > 0 else "显存未知"
        lines = [
            f"加速设备：{hw.gpu_name}",
            f"计算后端：{hw.backend_label}　·　支持状态：{hw.support}",
            f"{vram}　·　系统内存 {hw.ram_total_gb} GB　·　磁盘剩余 {hw.disk_free_gb} GB",
            f"推荐策略：{hw.policy_label}",
        ]
        lines += [f"· {n}" for n in hw.notes]
        self.hw_info.setText("\n".join(lines))

    def _rescan_hw(self):
        from .main_window import HwProbeThread

        def _done(rep):
            self.ctx.hw = rep
            self.ctx.window._on_hw_done(rep)
            self._refresh_hw_info()
            mk = self.ctx.pages.get("market")
            if mk:
                mk.refresh_hardware()
            self.ctx.toast("硬件检测完成")

        self._rescan_thread = HwProbeThread()
        self._rescan_thread.done.connect(_done)
        self._rescan_thread.start()
        self.ctx.toast("正在重新检测硬件…")

    def _apply_recommended(self):
        hw = self.ctx.hw
        if hw is None or hw.policy == "unsupported":
            self.ctx.toast("未检测到加速设备，无法生成推荐配置")
            return
        s = self.ctx.settings
        s.set("offload_mode", "auto", autosave=False)
        s.set("vram_budget_gb", -1, autosave=False)      # 自动预算
        s.set("torch_threads", -1, autosave=False)        # 自动线程
        # 低显存档默认用 480P 预览，其它档用 768P 标准
        s.set("default_resolution", "480p" if hw.policy in ("low", "ultra") else "768p")
        # 同步界面控件
        self.vram_spin.setValue(-1)
        i = self.offload_combo.findData("auto")
        if i >= 0:
            self.offload_combo.setCurrentIndex(i)
        self.threads_spin.setValue(-1)
        self.ctx.toast(f"已按 {hw.backend_label} · {hw.policy_label} 应用推荐配置")

    def _save_engine_settings(self):
        s = self.ctx.settings
        s.set("vram_budget_gb", float(self.vram_spin.value()), autosave=False)
        s.set("offload_mode", self.offload_combo.currentData(), autosave=False)
        s.set("torch_threads", int(self.threads_spin.value()))
        self.ctx.toast("引擎设置已保存（下次加载模型时生效）")

    def _save_adv_settings(self):
        s = self.ctx.settings
        s.set("cfg_scale", float(self.cfg_spin.value()), autosave=False)
        s.set("flow_shift", float(self.fs_spin.value()), autosave=False)
        s.set("audio_flow_shift", float(self.afs_spin.value()), autosave=False)
        s.set("tiled_vae", self.tiled_chk.isChecked(), autosave=False)
        s.set("tile_size", int(self.tile_spin.value()), autosave=False)
        s.set("tile_overlap", int(self.overlap_spin.value()), autosave=False)
        s.set("rand_device", self.rand_combo.currentData(), autosave=False)
        s.set("output_prefix", self.prefix_edit.text().strip() or "h3", autosave=False)
        s.set("save_metadata", self.meta_chk.isChecked())
        self.ctx.toast("高级参数已保存")

    def _save_dl_settings(self):
        s = self.ctx.settings
        s.set("preferred_source", self.src_combo.currentData(), autosave=False)
        s.set("probe_sample_mb", int(self.probe_spin.value()), autosave=False)
        s.set("download_retries", int(self.retry_spin.value()))
        self.ctx.toast("下载设置已保存")

    def _on_lang_changed(self, _idx):
        from ..i18n import set_lang
        code = self.lang_combo.currentData()
        self.ctx.settings.set("language", code)
        set_lang(code)
        self.ctx.apply_theme()   # 主题里的文案随语言刷新
        self.ctx.toast("语言已切换 / Language switched")

    def _set_theme(self, key):
        if self._theme_btns[key].isChecked():
            self.ctx.settings.set("theme", key)
            self.ctx.apply_theme()

    def _pick_accent(self):
        cur = QColor(self.ctx.settings.get("accent_color"))
        c = QColorDialog.getColor(cur, self, "选择强调色")
        if c.isValid():
            self.ctx.settings.set("accent_color", c.name())
            self._update_accent_preview()
            self.ctx.apply_theme()

    def _update_accent_preview(self):
        self.accent_preview.setStyleSheet(
            f"color: {self.ctx.settings.get('accent_color')}; font-size: 18px;")

    def _set_font_scale(self, v):
        self.fs_label.setText(f"{v}%")
        self.ctx.settings.set("font_scale", v, autosave=False)
        self.ctx.apply_theme()

    def _set_opacity(self, v):
        self.op_label.setText(str(v))
        self.ctx.settings.set("glass_opacity", v, autosave=False)
        self.ctx.apply_theme()

    def showEvent(self, e):
        self.ctx.settings.save()
        super().showEvent(e)
