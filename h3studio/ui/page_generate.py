# -*- coding: utf-8 -*-
"""
ui/page_generate.py — 生成主页
===============================
左：正/负提示词
中：参数（帧率固定 24fps、比例、秒数 4~15s、分辨率档）+ 参考素材导入（拖拽/点击）
右：模式选择（文生视频 / 首帧 / 尾帧 / 首尾帧 / 全模态参考 / 音频驱动 / 视频编辑）
    + LoRA 嵌入模型 + 生成按钮
底部：实时进度（步数/阶段/速度）与结果预览
"""

import os
import time

from PySide6.QtCore import Qt, QThread, QSize, Signal
from PySide6.QtGui import QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox,
                               QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QSlider, QSpinBox, QSplitter, QVBoxLayout,
                               QWidget)

from ..facts import (ACCEPT_AUDIO, ACCEPT_IMAGE, ACCEPT_VIDEO, ASPECT_PRESETS,
                     GENERATION_SPECS, UPLOAD_LIMITS)
from ..engine import GenerationParams, align_num_frames, check_engine_ready


# ─────────────────────────────────────────────────────────────
# 生成线程
# ─────────────────────────────────────────────────────────────
class GenerateWorker(QThread):
    progress = Signal(int, int, str)      # step, total, phase
    round_info = Signal(int, int)         # 第几轮 / 共几轮
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, engine, params: GenerationParams, count: int = 1):
        super().__init__()
        self.engine = engine
        self.params = params
        self.count = max(1, min(4, count))

    def run(self):
        results = []
        try:
            for i in range(self.count):
                self.round_info.emit(i + 1, self.count)
                p = GenerationParams(**{**self.params.__dict__})
                # 批量时每轮换种子（指定种子则递增，保证可复现）
                if p.seed is not None and p.seed >= 0:
                    p.seed = p.seed + i
                out = self.engine.generate(
                    p, progress_cb=lambda s, t, ph: self.progress.emit(s, t, ph))
                results.append(out)
            self.finished_ok.emit({"results": results})
        except Exception as e:
            if results:
                self.finished_ok.emit({"results": results, "partial_error": str(e)})
            else:
                self.failed.emit(str(e))


PROMPT_TEMPLATES = [
    ("产品广告", "图1 中的产品悬浮在画面中央缓慢旋转，柔和的影棚灯光，背景为渐变色，镜头缓慢推近，质感高级，广告大片风格，配乐轻快有节奏感。"),
    ("人物说话", "图1 中的人物面向镜头自然说话，表情生动，口型与台词同步，台词是：“（在这里写台词）”，背景虚化，电影感浅景深。"),
    ("风景运镜", "无人机航拍视角掠过广阔的自然风光，镜头平稳向前推进，晨光洒落，云层流动，画面宏大治愈，配以舒缓的环境音乐。"),
    ("动漫场景", "动漫风格，色彩鲜艳，镜头跟随主角奔跑穿过街道，花瓣飘落，动态模糊恰到好处，热血昂扬的背景音乐。"),
    ("电商展示", "图1 中的商品置于生活化场景中，一只手自然地拿起并展示商品细节，镜头特写材质纹理，光线温暖，轻快的背景音。"),
    ("电影片头", "电影感片头：黑场渐亮，标题文字以优雅的动画浮现，背景为缓慢流动的光影粒子，配乐恢弘渐强。"),
    # 以下模板参考 MiniMax 官方仓库 9 个提示词技能的方法论（2026-08-09 核实）
    ("3D动画短剧", "3D 渲染动画风格，皮克斯级材质与光照。角色形象圆润可爱，表情夸张生动，镜头跟随角色动作流畅切换，色彩明快饱和，配乐轻快活泼，结尾定格在角色的俏皮特写。"),
    ("纸艺定格动画", "纸艺定格动画风格，手工剪纸质感清晰可见，纸张边缘有真实的纤维细节。逐帧动画略带手工的顿挫感，场景由立体纸雕层叠构成，暖色灯光，旁白娓娓道来，氛围温馨治愈。"),
    ("品牌宣传片", "高端品牌宣传片：开场品牌 Logo 以光影粒子汇聚成形，随后产品与品牌视觉元素在高级灰背景中依次呈现，镜头语言克制大气，转场干净利落，配乐恢弘有质感，结尾品牌标语浮现。"),
    ("MV歌词字幕", "音乐视频风格：歌手演唱画面与歌词字幕交替呈现，字幕以动态排版动画浮现（放大、位移、渐隐），与节拍严格对齐，镜头随节奏切换，灯光氛围感强，音乐情绪饱满。"),
    ("手绘实拍风", "手绘线条与真实场景融合的创意风格：实景画面上有手绘涂鸦线条生长、勾勒、跳动，线条为鲜明对比色，带有铅笔沙沙声的音效，整体俏皮有生命力，镜头轻快。"),
    ("拼贴科普", "杂志拼贴风格科普短片：复古杂志剪报、照片、手绘插图在画面上拼贴组合并动态翻转，配合简洁的转场与指针式动效，旁白清晰讲解知识点，节奏明快，配乐复古轻快。"),
]

PHASE_LABELS = {
    "encode": "阶段 1/3 · 文本与素材编码",
    "denoise": "阶段 2/3 · 视频+音频联合去噪",
    "decode": "阶段 3/3 · VAE 解码合成",
}


class GeneratePage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.worker = None
        self._refs = []             # [{kind, path}]
        self._retake_video = ""
        self._build()
        self._apply_mode()

    # ═══════════════════════════════════════════════════════
    # UI 构建
    # ═══════════════════════════════════════════════════════
    def _build(self):
        from .widgets import GlassPanel, DropZone

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)

        # ── 左栏：提示词 ──
        left = GlassPanel()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(16, 16, 16, 16)
        lv.setSpacing(10)

        t = QLabel("提示词")
        t.setObjectName("sectionTitle")
        lv.addWidget(t)
        hint = QLabel("用自然语言描述画面与声音，可用「图1」「视频1」「音频1」指代下方参考素材；台词请写进提示词。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        lv.addWidget(hint)

        guide_row = QHBoxLayout()
        from ..facts import OFFICIAL_PROMPT_RESOURCES as _GUIDES
        for _g in _GUIDES["guides"]:
            gb = QPushButton(_g["label"])
            gb.setObjectName("chipBtn")
            gb.setToolTip(_g["source"])
            gb.setCursor(Qt.PointingHandCursor)
            gb.clicked.connect(lambda _c=False, gg=_g: self._open_guide(gg["file"]))
            guide_row.addWidget(gb)
        guide_row.addStretch(1)
        lv.addLayout(guide_row)

        tpl_title = QLabel("💡 提示词模板（点一下自动填入，适合新手）")
        tpl_title.setObjectName("sectionTitle")
        lv.addWidget(tpl_title)
        from .widgets import FlowLayout
        tpl_wrap = QWidget()
        tpl_flow = FlowLayout(tpl_wrap, margin=0, hspacing=6, vspacing=6)
        self._tpl_btns = []
        for name, text in PROMPT_TEMPLATES:
            tb = QPushButton(name)
            tb.setObjectName("chipBtn")
            tb.setToolTip(text[:60] + "…")
            tb.setCursor(Qt.PointingHandCursor)
            tb.clicked.connect(lambda _c=False, t=text: self._apply_template_text(t))
            tpl_flow.addWidget(tb)
            self._tpl_btns.append(tb)
        tpl_wrap.setMaximumHeight(76)
        lv.addWidget(tpl_wrap)
        # 保留隐藏下拉以兼容旧调用
        self.tpl_combo = QComboBox()
        self.tpl_combo.addItem("选择提示词模板（可选）", "")
        for name, text in PROMPT_TEMPLATES:
            self.tpl_combo.addItem(name, text)
        self.tpl_combo.activated.connect(self._apply_template)
        self.tpl_combo.hide()

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("例：图1 中的女孩站在海边日落里，微风吹动头发，她轻声说：“我们出发吧。” 海浪声作背景，电影感色调。")
        self.prompt_edit.setMinimumHeight(180)
        lv.addWidget(self.prompt_edit, 3)

        self.prompt_count = QLabel("0 / 7000")
        self.prompt_count.setObjectName("dimText")
        self.prompt_count.setAlignment(Qt.AlignRight)
        lv.addWidget(self.prompt_count)
        self.prompt_edit.textChanged.connect(self._update_count)

        neg_t = QLabel("负向提示词")
        neg_t.setObjectName("sectionTitle")
        lv.addWidget(neg_t)
        neg_hint = QLabel("提示：H3 为 CFG 蒸馏模型，负向提示词默认不起作用，仅供特殊需求使用。")
        neg_hint.setObjectName("hintLabel")
        neg_hint.setWordWrap(True)
        lv.addWidget(neg_hint)
        neg_t.hide()
        neg_hint.hide()
        self.neg_edit = QPlainTextEdit()
        self.neg_edit.setPlaceholderText("（可选）不希望出现的内容")
        self.neg_edit.setMaximumHeight(70)
        lv.addWidget(self.neg_edit, 1)
        self.neg_edit.hide()   # H3 为 CFG 蒸馏模型，负向提示词无效果，界面隐藏（引擎保留接口）
        lv.addStretch(1)

        # ── 中栏：参数 + 参考素材 ──
        center_wrap = QWidget()
        cv = QVBoxLayout(center_wrap)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(10)

        params_card = GlassPanel()
        pv = QVBoxLayout(params_card)
        pv.setContentsMargins(16, 14, 16, 14)
        pv.setSpacing(10)
        pt = QLabel("生成参数")
        pt.setObjectName("sectionTitle")
        pv.addWidget(pt)

        # 速度/均衡/质量 三档预设
        preset_row = QHBoxLayout()
        from ..planner import PRESETS as _PRESETS
        for _key, _pd in _PRESETS.items():
            pb = QPushButton(_pd["label"])
            pb.setObjectName("chipBtn")
            pb.setToolTip(_pd["tip"])
            pb.setCursor(Qt.PointingHandCursor)
            pb.clicked.connect(lambda _c=False, k=_key: self._apply_preset(k))
            preset_row.addWidget(pb)
        preset_row.addStretch(1)
        pv.addLayout(preset_row)

        # 比例
        ratio_row = QHBoxLayout()
        ratio_row.addWidget(QLabel("画面比例"))
        self.ratio_group = QButtonGroup(self)
        self._ratio_btns = {}
        for r in ASPECT_PRESETS.keys():
            b = QPushButton(r)
            b.setObjectName("chipBtn")
            b.setCheckable(True)
            self.ratio_group.addButton(b)
            self._ratio_btns[r] = b
            ratio_row.addWidget(b)
            b.toggled.connect(lambda _c, rr=r: self._param_changed())
        self._ratio_btns[self.ctx.settings.get("default_ratio")].setChecked(True)
        ratio_row.addStretch(1)
        pv.addLayout(ratio_row)

        # 时长
        dur_row = QHBoxLayout()
        dur_row.addWidget(QLabel("视频时长"))
        self.dur_slider = QSlider(Qt.Horizontal)
        self.dur_slider.setRange(GENERATION_SPECS["duration_min_s"], GENERATION_SPECS["duration_max_s"])
        self.dur_slider.setValue(self.ctx.settings.get("default_duration"))
        self.dur_slider.setTickPosition(QSlider.TicksBelow)
        self.dur_slider.setTickInterval(1)
        dur_row.addWidget(self.dur_slider, 1)
        self.dur_label = QLabel("5 秒")
        self.dur_label.setObjectName("accentText")
        self.dur_label.setMinimumWidth(52)
        dur_row.addWidget(self.dur_label)
        self.dur_slider.valueChanged.connect(self._param_changed)
        pv.addLayout(dur_row)

        # 分辨率档 + 帧率说明
        res_row = QHBoxLayout()
        res_row.addWidget(QLabel("分辨率档"))
        self.res_combo = QComboBox()
        self.res_combo.addItem("480p · 快速预览（更快出片）", "480p")
        self.res_combo.addItem("640p · 均衡", "640p")
        self.res_combo.addItem("768p · 标准（H3-Base 默认）", "768p")
        idx = self.res_combo.findData(self.ctx.settings.get("default_resolution"))
        if idx >= 0:
            self.res_combo.setCurrentIndex(idx)
        res_row.addWidget(self.res_combo, 1)
        fps_badge = QLabel("24 FPS 固定 · 32kHz 立体声")
        fps_badge.setObjectName("badge")
        res_row.addWidget(fps_badge)
        pv.addLayout(res_row)

        # 高级：步数 + 种子
        adv_row = QHBoxLayout()
        adv_row.addWidget(QLabel("采样步数"))
        self.steps_spin = QSpinBox()
        self.steps_spin.setRange(1, 100)
        self.steps_spin.setValue(self.ctx.settings.get("default_steps"))
        adv_row.addWidget(self.steps_spin)
        adv_row.addSpacing(18)
        adv_row.addWidget(QLabel("随机种子"))
        self.seed_edit = QLineEdit(str(self.ctx.settings.get("default_seed")))
        self.seed_edit.setFixedWidth(120)
        self.seed_edit.setToolTip("-1 表示每次随机")
        adv_row.addWidget(self.seed_edit)
        adv_row.addSpacing(18)
        adv_row.addWidget(QLabel("生成数量"))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 4)
        self.count_spin.setValue(1)
        self.count_spin.setToolTip("一次生成多个不同种子的版本，方便挑片（依次生成，耗时成倍）")
        adv_row.addWidget(self.count_spin)
        dice = QPushButton("🎲")
        dice.setObjectName("iconBtn")
        dice.setFixedWidth(34)
        dice.setToolTip("随机一个种子")
        dice.clicked.connect(lambda: self.seed_edit.setText(str(int(time.time()) % 1000000)))
        adv_row.addWidget(dice)
        adv_row.addStretch(1)
        pv.addLayout(adv_row)

        frames_hint = QLabel("")
        frames_hint.setObjectName("hintLabel")
        self.frames_hint = frames_hint
        pv.addWidget(frames_hint)

        cv.addWidget(params_card)

        # 参考素材卡片
        ref_card = GlassPanel()
        rv = QVBoxLayout(ref_card)
        rv.setContentsMargins(16, 14, 16, 14)
        rv.setSpacing(8)
        rt_row = QHBoxLayout()
        rt = QLabel("参考素材导入")
        rt.setObjectName("sectionTitle")
        rt_row.addWidget(rt)
        rt_row.addStretch(1)
        self.limit_badge = QLabel("")
        self.limit_badge.setObjectName("badge")
        rt_row.addWidget(self.limit_badge)
        rv.addLayout(rt_row)

        self.dropzone = DropZone("点击选择文件，或将 GIF / MP4 / MP3 / 图片 拖拽到这里")
        self.dropzone.filesDropped.connect(self._add_files)
        rv.addWidget(self.dropzone)

        self.ref_list = QListWidget()
        self.ref_list.setMaximumHeight(120)
        self.ref_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        rv.addWidget(self.ref_list)

        ref_btns = QHBoxLayout()
        rm = QPushButton("移除选中")
        rm.clicked.connect(self._remove_selected_refs)
        clear = QPushButton("清空")
        clear.clicked.connect(self._clear_refs)
        ref_btns.addWidget(rm)
        ref_btns.addWidget(clear)
        ref_btns.addStretch(1)
        rv.addLayout(ref_btns)

        # 视频编辑（Retake）源视频
        self.retake_row = QHBoxLayout()
        self.retake_label = QLabel("源视频：未选择")
        self.retake_label.setObjectName("hintLabel")
        pick_retake = QPushButton("选择源视频…")
        pick_retake.clicked.connect(self._pick_retake_video)
        self.retake_keep_audio = QCheckBox("保留原音轨")
        self.retake_keep_audio.setChecked(True)
        self.retake_row.addWidget(self.retake_label, 1)
        self.retake_row.addWidget(self.retake_keep_audio)
        self.retake_row.addWidget(pick_retake)
        rv.addLayout(self.retake_row)

        cv.addWidget(ref_card, 1)
        splitter.addWidget(left)
        splitter.addWidget(center_wrap)

        # ── 右栏：模式 + LoRA + 生成 ──
        right = GlassPanel()
        rvv = QVBoxLayout(right)
        rvv.setContentsMargins(16, 16, 16, 16)
        rvv.setSpacing(8)
        mt = QLabel("生成模式")
        mt.setObjectName("sectionTitle")
        rvv.addWidget(mt)

        self.mode_group = QButtonGroup(self)
        self._mode_btns = {}
        MODES = [
            ("t2va", "文生视频", "纯文字生成带声音的视频"),
            ("first", "首帧 → 视频", "提供 1 张首帧图片"),
            ("last", "尾帧 → 视频", "提供 1 张尾帧图片"),
            ("fl", "首尾帧 → 视频", "提供首、尾 2 张图片"),
            ("ref2va", "全模态参考", "≤9 图 + ≤3 视频 + ≤3 音频混合参考"),
            ("audio_driven", "音频驱动", "用音频（台词/音乐）驱动画面"),
            ("retake", "视频编辑", "基于源视频重生成指定区间"),
        ]
        for key, name, desc in MODES:
            b = QPushButton(f"{name}\n{desc}")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton { text-align: left; padding: 8px 10px; border-radius: 10px; }"
                "QPushButton:checked { border: 1px solid #3b82f6; }")
            self.mode_group.addButton(b)
            self._mode_btns[key] = b
            rvv.addWidget(b)
            b.toggled.connect(lambda _c, k=key: self._on_mode_toggled(k))
        self._mode_btns["t2va"].setChecked(True)

        rvv.addSpacing(6)
        lt_row = QHBoxLayout()
        lt = QLabel("嵌入模型（LoRA）")
        lt.setObjectName("sectionTitle")
        lt_row.addWidget(lt)
        lt_row.addStretch(1)
        lora_import = QPushButton("＋导入")
        lora_import.setObjectName("chipBtn")
        lora_import.setToolTip("导入 .safetensors/.bin/.pt 社区微调或加速模型")
        lora_import.clicked.connect(self._quick_import_lora)
        lt_row.addWidget(lora_import)
        rvv.addLayout(lt_row)
        self.lora_combo = QComboBox()
        self.lora_combo.addItem("不使用", "")
        rvv.addWidget(self.lora_combo)
        alpha_row = QHBoxLayout()
        alpha_row.addWidget(QLabel("强度"))
        self.lora_alpha = QSlider(Qt.Horizontal)
        self.lora_alpha.setRange(0, 200)
        self.lora_alpha.setValue(100)
        alpha_row.addWidget(self.lora_alpha, 1)
        self.lora_alpha_label = QLabel("1.00")
        alpha_row.addWidget(self.lora_alpha_label)
        self.lora_alpha.valueChanged.connect(
            lambda v: self.lora_alpha_label.setText(f"{v / 100:.2f}"))
        rvv.addLayout(alpha_row)

        rvv.addStretch(1)

        self.gen_btn = QPushButton("▶  开始生成")
        self.gen_btn.setObjectName("primaryBtn")
        self.gen_btn.setMinimumHeight(46)
        self.gen_btn.setCursor(Qt.PointingHandCursor)
        self.gen_btn.clicked.connect(self._start_generate)
        rvv.addWidget(self.gen_btn)

        self.cancel_btn = QPushButton("取消生成")
        self.cancel_btn.setObjectName("dangerBtn")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_generate)
        rvv.addWidget(self.cancel_btn)

        splitter.addWidget(right)
        splitter.setSizes([300, 620, 300])
        root.addWidget(splitter, 1)

        # ── 底部：进度 + 预览 ──
        bottom = GlassPanel(strong=True)
        bv = QHBoxLayout(bottom)
        bv.setContentsMargins(16, 10, 16, 10)
        bv.setSpacing(14)

        self.phase_label = QLabel("就绪")
        self.phase_label.setMinimumWidth(220)
        bv.addWidget(self.phase_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        bv.addWidget(self.progress_bar, 1)

        self.speed_label = QLabel("")
        self.speed_label.setObjectName("dimText")
        self.speed_label.setMinimumWidth(150)
        bv.addWidget(self.speed_label)

        self.preview_btn = QPushButton("预览最新结果")
        self.preview_btn.setEnabled(False)
        self.preview_btn.clicked.connect(self._show_last_result)
        bv.addWidget(self.preview_btn)

        root.addWidget(bottom)

        sc = QShortcut(QKeySequence("Ctrl+Return"), self)
        sc.activated.connect(self._start_generate)

        self.ref_list.setIconSize(QSize(44, 44))
        self._param_changed()

    # ═══════════════════════════════════════════════════════
    # 参数
    # ═══════════════════════════════════════════════════════
    def _current_ratio(self):
        for r, b in self._ratio_btns.items():
            if b.isChecked():
                return r
        return "16:9"

    def _param_changed(self):
        if not hasattr(self, "dur_slider"):
            return  # 构建期间 toggled 信号可能提前触发
        d = self.dur_slider.value()
        self.dur_label.setText(f"{d} 秒")
        ratio = self._current_ratio()
        res = self.res_combo.currentData()
        w, h = ASPECT_PRESETS[ratio][res]
        nf = align_num_frames(d)
        self.frames_hint.setText(
            f"输出 {w}×{h} · {nf} 帧（{nf / 24:.1f}s @24fps，帧数按 17n+5 对齐）")

    def _update_count(self):
        n = len(self.prompt_edit.toPlainText())
        self.prompt_count.setText(f"{n} / 7000")

    # ═══════════════════════════════════════════════════════
    # 模式切换
    # ═══════════════════════════════════════════════════════
    def _on_mode_toggled(self, key):
        if self._mode_btns[key].isChecked():
            self._apply_mode()

    def _apply_mode(self):
        mode = self.current_mode()
        is_retake = mode == "retake"
        self.retake_label.setVisible(is_retake)
        self.retake_keep_audio.setVisible(is_retake)
        # retake 行里的按钮通过遍历控制
        for i in range(self.retake_row.count()):
            w = self.retake_row.itemAt(i).widget()
            if w is not None and isinstance(w, QPushButton):
                w.setVisible(is_retake)

        needs_refs = mode in ("ref2va", "audio_driven")
        self.dropzone.setVisible(not is_retake)
        self.ref_list.setVisible(needs_refs or mode in ("first", "last", "fl"))
        dz_text = {
            "t2va": "文生视频模式无需素材（如需参考请切换模式）",
            "first": "拖入 1 张图片作为首帧",
            "last": "拖入 1 张图片作为尾帧",
            "fl": "拖入 2 张图片（第 1 张=首帧，第 2 张=尾帧）",
            "ref2va": "拖入图片/视频/音频并在提示词中说明用途。官方限制：视频/音频每段2~15秒且各自总时长≤15秒",
            "audio_driven": "拖入音频（MP3/WAV等）+ 建议配图或视频。官方规格：音频不能作为唯一输入，须伴随图像或视频",
        }
        if not is_retake:
            self.dropzone.setText(dz_text.get(mode, ""))
        self._refresh_limit_badge()

    def current_mode(self):
        for k, b in self._mode_btns.items():
            if b.isChecked():
                return k
        return "t2va"

    # ═══════════════════════════════════════════════════════
    # 素材管理
    # ═══════════════════════════════════════════════════════
    @staticmethod
    def _file_kind(path: str):
        ext = os.path.splitext(path)[1].lower()
        if ext in ACCEPT_IMAGE:
            return "image"
        if ext in ACCEPT_VIDEO:
            return "video"
        if ext in ACCEPT_AUDIO:
            return "audio"
        return None

    def _add_files(self, paths):
        mode = self.current_mode()
        if mode == "t2va":
            self.ctx.toast("文生视频模式不需要素材，已忽略")
            return
        if mode == "retake":
            self.ctx.toast("视频编辑模式请在下方选择源视频")
            return
        for p in paths:
            kind = self._file_kind(p)
            if kind is None:
                self.ctx.toast(f"不支持的文件类型：{os.path.basename(p)}")
                continue
            if mode in ("first", "last") and len(self._refs) >= 1:
                self.ctx.toast("该模式只需 1 张图片")
                break
            if mode == "fl" and len(self._refs) >= 2:
                self.ctx.toast("首尾帧模式最多 2 张图片")
                break
            if mode == "audio_driven" and kind not in ("audio", "image"):
                self.ctx.toast("音频驱动模式仅接受音频和图片")
                continue
            if mode == "ref2va":
                lim = UPLOAD_LIMITS["ref2va"]
                n_img = sum(1 for r in self._refs if r["kind"] == "image")
                n_vid = sum(1 for r in self._refs if r["kind"] in ("video", "video_audio"))
                n_aud = sum(1 for r in self._refs if r["kind"] == "audio")
                if kind == "image" and n_img >= lim["image"]:
                    self.ctx.toast(f"图片最多 {lim['image']} 张（H3 官方限制）")
                    continue
                if kind == "video" and n_vid >= lim["video"]:
                    self.ctx.toast(f"视频最多 {lim['video']} 段（H3 官方限制）")
                    continue
                if kind == "audio" and n_aud >= lim["audio"]:
                    self.ctx.toast(f"音频最多 {lim['audio']} 段（H3 官方限制）")
                    continue
                if len(self._refs) >= lim["total"]:
                    self.ctx.toast(f"素材总数最多 {lim['total']} 个（H3 官方限制）")
                    continue
            self._refs.append({"kind": kind, "path": p})
        self._refresh_ref_list()

    def _refresh_ref_list(self):
        self.ref_list.clear()
        icons = {"image": "🖼", "video": "🎬", "audio": "🎵", "video_audio": "🎞"}
        for r in self._refs:
            item = QListWidgetItem(f"{icons.get(r['kind'], '·')} {os.path.basename(r['path'])}")
            item.setToolTip(r["path"])
            icon = self._make_icon(r["path"], r["kind"])
            if icon is not None:
                item.setIcon(icon)
            self.ref_list.addItem(item)
        self._refresh_limit_badge()

    def _refresh_limit_badge(self):
        mode = self.current_mode()
        if mode == "ref2va":
            lim = UPLOAD_LIMITS["ref2va"]
            n_img = sum(1 for r in self._refs if r["kind"] == "image")
            n_vid = sum(1 for r in self._refs if r["kind"] in ("video", "video_audio"))
            n_aud = sum(1 for r in self._refs if r["kind"] == "audio")
            self.limit_badge.setText(
                f"图 {n_img}/{lim['image']} · 视频 {n_vid}/{lim['video']} · "
                f"音频 {n_aud}/{lim['audio']} · 总 {len(self._refs)}/{lim['total']}")
        elif mode in ("first", "last"):
            self.limit_badge.setText(f"关键帧 {min(len(self._refs), 1)}/1")
        elif mode == "fl":
            self.limit_badge.setText(f"关键帧 {min(len(self._refs), 2)}/2")
        else:
            self.limit_badge.setText("")

    def _open_guide(self, filename):
        """打开随软件内置的官方提示词指南。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from .page_library import _resource_path
        path = _resource_path(os.path.join("resources", "prompt_guides", filename))
        if os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            self.ctx.toast("指南文件缺失，请重新下载完整软件包")

    def _apply_preset(self, key):
        from ..planner import PRESETS
        pd = PRESETS.get(key)
        if not pd:
            return
        idx = self.res_combo.findData(pd["resolution"])
        if idx >= 0:
            self.res_combo.setCurrentIndex(idx)
        self.steps_spin.setValue(pd["steps"])
        if pd.get("need_turbo"):
            turbo_i = -1
            for i in range(self.lora_combo.count()):
                if "turbo" in self.lora_combo.itemText(i).lower():
                    turbo_i = i
                    break
            if turbo_i >= 0:
                self.lora_combo.setCurrentIndex(turbo_i)
                self.ctx.toast("速度优先：已启用 Turbo LoRA + 4 步采样，快速抽卡模式")
            else:
                self.ctx.toast(pd["tip"])
        else:
            self.ctx.toast(pd["tip"])
        self._param_changed()

    def _apply_template_text(self, text):
        cur = self.prompt_edit.toPlainText().strip()
        self.prompt_edit.setPlainText(text if not cur else cur + "\n" + text)
        self._update_count()

    def _apply_template(self, idx):
        text = self.tpl_combo.currentData()
        if text:
            cur = self.prompt_edit.toPlainText().strip()
            self.prompt_edit.setPlainText(text if not cur else cur + "\n" + text)
            self.tpl_combo.setCurrentIndex(0)
            self._update_count()

    @staticmethod
    def _make_icon(path: str, kind: str):
        """图片直接取缩略图；视频/GIF 尝试取首帧；失败返回 None。"""
        try:
            if kind == "image":
                pm = QPixmap(path)
                if pm.isNull():
                    return None
                return QIcon(pm.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            if kind == "video":
                import imageio.v2 as imageio
                reader = imageio.get_reader(path)
                frame = reader.get_data(0)
                reader.close()
                h, w = frame.shape[:2]
                qimg = QImage(frame.data, w, h, frame.strides[0], QImage.Format_RGB888)
                return QIcon(QPixmap.fromImage(qimg.copy()).scaled(
                    44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            return None
        return None

    def _remove_selected_refs(self):
        rows = sorted({i.row() for i in self.ref_list.selectedIndexes()}, reverse=True)
        for r in rows:
            del self._refs[r]
        self._refresh_ref_list()

    def _clear_refs(self):
        self._refs = []
        self._refresh_ref_list()

    def _pick_retake_video(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "选择要编辑的源视频", "", "视频 (*.mp4 *.mov *.mkv *.webm *.avi)")
        if f:
            self._retake_video = f
            self.retake_label.setText(f"源视频：{os.path.basename(f)}")

    # ═══════════════════════════════════════════════════════
    # LoRA 列表同步
    # ═══════════════════════════════════════════════════════
    def refresh_loras(self):
        cur = self.lora_combo.currentData()
        self.lora_combo.clear()
        self.lora_combo.addItem("不使用", "")
        loras_dir = self.ctx.settings.get("loras_dir")
        if os.path.isdir(loras_dir):
            for fn in sorted(os.listdir(loras_dir)):
                if fn.lower().endswith((".safetensors", ".bin", ".pt")):
                    self.lora_combo.addItem(fn, os.path.join(loras_dir, fn))
        if cur:
            i = self.lora_combo.findData(cur)
            if i >= 0:
                self.lora_combo.setCurrentIndex(i)

    def _quick_import_lora(self):
        """生成页快捷导入 LoRA（复制到 LoRA 目录并刷新列表）。"""
        import shutil
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择 LoRA 文件", "", "LoRA 权重 (*.safetensors *.bin *.pt)")
        if not files:
            return
        loras_dir = self.ctx.settings.get("loras_dir")
        os.makedirs(loras_dir, exist_ok=True)
        ok_n = 0
        for f in files:
            try:
                shutil.copy2(f, os.path.join(loras_dir, os.path.basename(f)))
                ok_n += 1
            except Exception as e:
                self.ctx.toast(f"导入失败：{e}")
        self.refresh_loras()
        lib = self.ctx.pages.get("library")
        if lib:
            lib.refresh()
        if ok_n:
            self.ctx.toast(f"已导入 {ok_n} 个嵌入模型，可在下拉框选用")

    # ═══════════════════════════════════════════════════════
    # 生成
    # ═══════════════════════════════════════════════════════
    def _validate(self):
        prompt = self.prompt_edit.toPlainText().strip()
        if not prompt:
            return "请先输入提示词"
        if len(prompt) > 7000:
            return "提示词超过 7000 字上限"
        mode = self.current_mode()
        if mode in ("first", "last") and not any(r["kind"] == "image" for r in self._refs):
            return "该模式需要 1 张图片（拖入或点击上传）"
        if mode == "fl" and sum(1 for r in self._refs if r["kind"] == "image") < 2:
            return "首尾帧模式需要 2 张图片"
        if mode == "audio_driven" and not any(r["kind"] == "audio" for r in self._refs):
            return "音频驱动模式需要至少 1 个音频文件"
        if mode == "audio_driven" and all(r["kind"] == "audio" for r in self._refs):
            return "官方规格：音频不能作为唯一输入，请再添加至少 1 张图或 1 段视频（详见官方指南）"
        if mode == "ref2va" and not self._refs:
            return "全模态参考模式需要至少 1 个参考素材"
        if mode == "retake" and not self._retake_video:
            return "请先选择要编辑的源视频"
        ok, msg = check_engine_ready()
        if not ok:
            return "引擎未就绪：" + msg
        return None

    def _start_generate(self):
        err = self._validate()
        if err:
            self.ctx.toast(err)
            return

        mode = self.current_mode()
        ratio = self._current_ratio()
        res = self.res_combo.currentData()
        w, h = ASPECT_PRESETS[ratio][res]

        try:
            seed = int(self.seed_edit.text().strip())
        except ValueError:
            seed = -1

        params = GenerationParams(
            prompt=self.prompt_edit.toPlainText().strip(),
            negative_prompt=self.neg_edit.toPlainText().strip(),
            duration_s=self.dur_slider.value(),
            width=w, height=h,
            steps=self.steps_spin.value(),
            seed=seed,
            mode=mode,
            keyframe_paths=[r["path"] for r in self._refs if r["kind"] == "image"][:2],
            references=[dict(r) for r in self._refs] if mode in ("ref2va", "audio_driven") else [],
            retake_video_path=self._retake_video if mode == "retake" else "",
            retake_keep_audio=self.retake_keep_audio.isChecked(),
            lora_path=self.lora_combo.currentData() or "",
            lora_alpha=self.lora_alpha.value() / 100.0,
            output_dir=self.ctx.settings.get("outputs_dir"),
            output_prefix=str(self.ctx.settings.get("output_prefix")),
            save_metadata=bool(self.ctx.settings.get("save_metadata")),
        )

        # 引擎加载检查
        engine = self.ctx.engine
        need_partition = "Ref2VA" if mode in ("ref2va", "audio_driven", "retake") else "FL2VA"
        if not engine.ready or engine.loaded_partition != need_partition:
            loaded = self.ctx.ensure_model_loaded(need_partition)
            if not loaded:
                return

        self.gen_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.phase_label.setText("启动中…")
        self.ctx.status("正在生成…")

        count = self.count_spin.value()
        if count > 1:
            self.ctx.toast(f"将依次生成 {count} 个版本（不同种子），请耐心等待")
        self.worker = GenerateWorker(engine, params, count=count)
        self.worker.progress.connect(self._on_progress)
        self.worker.round_info.connect(self._on_round)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_round(self, i, n):
        if n > 1:
            self.phase_label.setText(f"第 {i}/{n} 轮生成")

    def _cancel_generate(self):
        # DiffSynth 的去噪循环不支持中途安全打断，取消仅能在完成后生效；
        # 这里给出诚实提示而不是假取消。
        self.ctx.toast("生成过程无法安全中断（会损坏显存状态），请等待本次完成")

    def _on_progress(self, step, total, phase):
        self.phase_label.setText(PHASE_LABELS.get(phase, phase))
        if phase == "denoise" and total > 0:
            pct = int(step / total * 95)
            self.progress_bar.setValue(pct)
            self.speed_label.setText(f"步 {step}/{total}")
        elif phase == "encode":
            self.progress_bar.setValue(2)
            self.speed_label.setText("编码中")
        elif phase == "decode":
            self.progress_bar.setValue(97)
            self.speed_label.setText("解码合成中")

    def _on_done(self, out):
        results = out.get("results", [])
        self.progress_bar.setValue(100)
        total_s = round(sum(r.get("elapsed_s", 0) for r in results), 1)
        self.phase_label.setText(f"完成 · {len(results)} 个作品 · 总用时 {total_s}s")
        self.speed_label.setText("")
        self.gen_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.preview_btn.setEnabled(bool(results))
        if results:
            self._last_video = results[0]["video_path"]
        self.ctx.status(f"生成完成：{len(results)} 个作品")
        if out.get("partial_error"):
            self.ctx.toast(f"部分完成：{len(results)} 个成功后中断（{out['partial_error'][:80]}）")
        elif len(results) > 1:
            self.ctx.toast(f"已完成 {len(results)} 个版本！点「预览最新结果」查看第一个")
        else:
            self.ctx.toast("生成完成！点击底部「预览最新结果」查看")
        self.ctx.gallery_dirty()

    def _on_failed(self, err):
        self.gen_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.phase_label.setText("生成失败")
        self.ctx.status("生成失败")
        self.ctx.toast("生成失败：" + err[:200])

    # ═══════════════════════════════════════════════════════
    # 预览
    # ═══════════════════════════════════════════════════════
    def _show_last_result(self):
        path = getattr(self, "_last_video", "")
        if path and os.path.exists(path):
            dlg = VideoPlayerDialog(path, self)
            dlg.exec()

    def showEvent(self, e):
        self.refresh_loras()
        # 同步设置页/最优方案改过的默认参数（不覆盖用户已输入的内容）
        st = self.ctx.settings
        i = self.res_combo.findData(st.get("default_resolution"))
        if i >= 0:
            self.res_combo.setCurrentIndex(i)
        self.steps_spin.setValue(int(st.get("default_steps")))
        super().showEvent(e)


# ─────────────────────────────────────────────────────────────
# 视频播放对话框（作品库共用）
# ─────────────────────────────────────────────────────────────
class VideoPlayerDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(os.path.basename(path))
        self.resize(880, 560)
        v = QVBoxLayout(self)

        self.video_w = QVideoWidget()
        v.addWidget(self.video_w, 1)

        row = QHBoxLayout()
        self.play_btn = QPushButton("播放")
        self.play_btn.clicked.connect(self._toggle)
        row.addWidget(self.play_btn)
        self.slider = QSlider(Qt.Horizontal)
        row.addWidget(self.slider, 1)
        self.time_label = QLabel("00:00 / 00:00")
        row.addWidget(self.time_label)
        v.addLayout(row)

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.setVideoOutput(self.video_w)
        from PySide6.QtCore import QUrl
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.positionChanged.connect(self._pos)
        self.player.durationChanged.connect(self._dur)
        self.slider.sliderMoved.connect(self.player.setPosition)

    def _toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_btn.setText("播放")
        else:
            self.player.play()
            self.play_btn.setText("暂停")

    def _pos(self, p):
        if not self.slider.isSliderDown():
            self.slider.setValue(p)
        self.time_label.setText(f"{self._fmt(p)} / {self._fmt(self.player.duration())}")

    def _dur(self, d):
        self.slider.setRange(0, d)

    @staticmethod
    def _fmt(ms):
        s = int(ms / 1000)
        return f"{s // 60:02d}:{s % 60:02d}"
