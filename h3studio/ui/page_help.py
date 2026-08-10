# -*- coding: utf-8 -*-
"""
ui/page_help.py — 帮助教程页（内置于软件，打包版可见）
========================================================
小白教程（三步上手）+ 常见问题 + 高级模式入口（ComfyUI 工作流/官方指南）。
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from .. import facts
from .widgets import GlassPanel


STEP_GUIDE = [
    ("① 下载模型",
     "打开「模型市场」→ 顶部有软件按你的电脑自动算好的「最优方案」→ 点「一键下载推荐方案」。"
     "下载支持断点续传，中断了再点一次会继续。首次使用建议先点「开始测速」，软件会自动选最快的下载源。"),
    ("② 写提示词",
     "打开「生成」页 → 在左侧输入你想生成的画面（中文即可）。"
     "不知道写什么？点提示词区的模板按钮（产品广告/风景运镜/动漫…），一键填入再改。"
     "想让人物说话：把台词直接写进提示词，例如「她轻声说：我们出发吧」。"),
    ("③ 点击生成",
     "选择时长（4~15 秒）和画面比例 → 点「开始生成」（或按 Ctrl+Enter）。"
     "建议先用「⚡速度优先」档快速抽卡，满意后再切「✨质量优先」出正式片。"
     "生成的视频在「作品库」里查看和播放。"),
]

FAQ = [
    ("显存不够 / 生成很慢怎么办？",
     "生成页切「⚡速度优先」档（4 步 Turbo LoRA + 480P）；或在市场下载更小的量化版本。"
     "8GB 显存选 NF4 版，16GB 以上体验明显更好。"),
    ("怎么让视频更快出片？",
     "市场里下载「Turbo 加速 LoRA」（推荐 lightx2v 或 InstantX 版）→ 我的模型页导入 → "
     "生成页「嵌入模型」选中它 → 点「⚡速度优先」，50 步压缩到 4 步，提速十几倍。"),
    ("支持哪些素材输入？",
     "全模态参考模式：最多 9 张图片 + 3 段视频 + 3 段音频（合计 12 个文件，每段 2~15 秒）。"
     "首尾帧模式：1~2 张图片。支持 GIF/MP4/MP3 等常见格式，直接拖进生成页即可。"),
    ("负向提示词去哪了？",
     "H3 是 CFG 蒸馏模型，负向提示词不生效，已从界面移除（引擎接口仍保留）。"),
    ("支持 LoRA 吗？",
     "支持。市场里有多个社区高分 Turbo LoRA 可下载，导入后在生成页「嵌入模型」选用。"
     "LoRA 切换会自动清理旧权重，不会叠加污染。"),
    ("我的电脑能跑吗？",
     "设置页「硬件检测」可重新检测。NVIDIA 显卡完整支持；AMD ROCm/华为昇腾支持；"
     "没有加速硬件可走 ComfyUI 路线（见下方高级模式）。"),
    ("软件会一直更新吗？",
     "社区微调模型更新很快，建议每 1~2 周回到「模型市场」查看新增 LoRA/量化版本。"),
]


class HelpPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._build()

    def _card(self, title):
        card = GlassPanel()
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
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

        # ── 三步上手 ──
        card, v = self._card("🚀 三步上手（新手教程）")
        for i, (title, body) in enumerate(STEP_GUIDE):
            st = QLabel(title)
            st.setObjectName("sectionTitle")
            v.addWidget(st)
            bd = QLabel(body)
            bd.setObjectName("hintLabel")
            bd.setWordWrap(True)
            v.addWidget(bd)
            if i < len(STEP_GUIDE) - 1:
                sep = QLabel("")
                sep.setFixedHeight(4)
                v.addWidget(sep)
        wv.addWidget(card)

        # ── 常见问题 ──
        card, v = self._card("❓ 常见问题")
        for q, a in FAQ:
            ql = QLabel("Q：" + q)
            ql.setObjectName("sectionTitle")
            ql.setWordWrap(True)
            v.addWidget(ql)
            al = QLabel(a)
            al.setObjectName("hintLabel")
            al.setWordWrap(True)
            v.addWidget(al)
        wv.addWidget(card)

        # ── 高级模式 ──
        card, v = self._card("🔧 高级模式（给懂行的人）")
        hint = QLabel(
            "普通用户不需要看这里。以下内容面向熟悉 ComfyUI 工作流的进阶用户：")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        v.addWidget(hint)

        g_row = QHBoxLayout()
        from ..facts import OFFICIAL_PROMPT_RESOURCES as _GUIDES
        for _g in _GUIDES["guides"]:
            gb = QPushButton(_g["label"])
            gb.setObjectName("chipBtn")
            gb.setToolTip(_g["source"])
            gb.setCursor(Qt.PointingHandCursor)
            gb.clicked.connect(lambda _c=False, gg=_g: self._open_guide(gg["file"]))
            g_row.addWidget(gb)
        g_row.addStretch(1)
        v.addLayout(g_row)

        w_row = QHBoxLayout()
        b1 = QPushButton("📋 复制 ComfyUI FL2VA 工作流（文生/首尾帧）")
        b1.clicked.connect(lambda: self._copy_workflow("minimax_fl2v_gguf_workflow.json"))
        w_row.addWidget(b1)
        b2 = QPushButton("📋 复制 ComfyUI Ref2VA 工作流（全模态参考）")
        b2.clicked.connect(lambda: self._copy_workflow("minimax_ref2va_gguf_workflow.json"))
        w_row.addWidget(b2)
        w_row.addStretch(1)
        v.addLayout(w_row)

        note = QLabel(
            "工作流来自社区仓库 Abiray/MiniMax-H3-GGUF（已随软件内置）。"
            "用法：ComfyUI 安装 ComfyUI-GGUF 插件 → 市场下载 GGUF 模型 → 粘贴工作流。"
            f"\n\n模型协议：{facts.MODEL_INFO['license']['name']}"
            f"（排除 {'/'.join(facts.MODEL_INFO['license']['regions_excluded'])} 地区）")
        note.setObjectName("hintLabel")
        note.setWordWrap(True)
        v.addWidget(note)
        wv.addWidget(card)
        wv.addStretch(1)
        scroll.setWidget(wrap)
        root.addWidget(scroll)

    # ═══════════════════════════════════════════════════════
    def _open_guide(self, filename):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from .page_library import _resource_path
        path = _resource_path(os.path.join("resources", "prompt_guides", filename))
        if os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            self.ctx.toast("指南文件缺失，请重新下载完整软件包")

    def _copy_workflow(self, filename):
        from .page_library import _resource_path
        path = _resource_path(os.path.join("resources", "comfyui_workflows", filename))
        if not os.path.exists(path):
            self.ctx.toast("工作流文件缺失，请重新下载完整软件包")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            QApplication.clipboard().setText(content)
            self.ctx.toast("工作流已复制：打开 ComfyUI → 画布上 Ctrl+V 粘贴即可")
        except Exception as e:
            self.ctx.toast(f"复制失败：{e}")
