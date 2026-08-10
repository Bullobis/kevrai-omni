# -*- coding: utf-8 -*-
"""
ui/page_market.py — 模型市场
=============================
- 真实测速（HTTP Range 采样，测 TTFB 延迟 + 真实吞吐，速度权重 75% + 延迟 25%）
- 按硬件推荐版本
- 每个模型卡片可选下载源（自动/魔搭/HF镜像/HF原站），断点续传下载
"""

import os

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QScrollArea,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from .. import facts
from ..downloader import BundleDownloadTask
from ..sources import run_speed_test, pick_best_source
from .widgets import GlassPanel


# ─────────────────────────────────────────────────────────────
# 测速线程
# ─────────────────────────────────────────────────────────────
class SpeedTestWorker(QThread):
    one = Signal(object)
    done = Signal(list)

    def __init__(self, sample_mb: int = 4):
        super().__init__()
        self.sample_mb = max(1, min(32, sample_mb))

    def run(self):
        results = run_speed_test(
            on_result=lambda r: self.one.emit(r),
            sample_bytes=self.sample_mb * 1024 * 1024)
        self.done.emit(results)


class MarketPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._cards = {}
        self._speed_results = []
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick_downloads)
        self._timer.start(1000)

    # ═══════════════════════════════════════════════════════
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ── 硬件检测 + 最优方案 ──
        self.hw_banner = GlassPanel(strong=True)
        hv = QVBoxLayout(self.hw_banner)
        hv.setContentsMargins(16, 12, 16, 12)
        hv.setSpacing(8)
        self.hw_text = QLabel("正在检测硬件…")
        self.hw_text.setWordWrap(True)
        hv.addWidget(self.hw_text)

        plan_head = QHBoxLayout()
        pt = QLabel("🎯 你的电脑最优方案（速度 × 质量 × 成本自动权衡）")
        pt.setObjectName("sectionTitle")
        plan_head.addWidget(pt)
        plan_head.addStretch(1)
        hv.addLayout(plan_head)
        self.plan_text = QLabel("")
        self.plan_text.setObjectName("hintLabel")
        self.plan_text.setWordWrap(True)
        self.plan_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hv.addWidget(self.plan_text)
        plan_row = QHBoxLayout()
        self.plan_dl_btn = QPushButton("⬇ 一键下载推荐方案")
        self.plan_dl_btn.setObjectName("primaryBtn")
        self.plan_dl_btn.clicked.connect(self._adopt_plan_download)
        plan_row.addWidget(self.plan_dl_btn)
        self.plan_apply_btn = QPushButton("应用推荐生成参数")
        self.plan_apply_btn.clicked.connect(self._adopt_plan_params)
        plan_row.addWidget(self.plan_apply_btn)
        plan_row.addStretch(1)
        hv.addLayout(plan_row)
        self._current_plan = None
        root.addWidget(self.hw_banner)
        self._refresh_hw_banner()

        # ── 测速卡片 ──
        speed_card = GlassPanel()
        sv = QVBoxLayout(speed_card)
        sv.setContentsMargins(16, 14, 16, 14)
        sv.setSpacing(8)
        st_row = QHBoxLayout()
        t = QLabel("下载源智能测速")
        t.setObjectName("sectionTitle")
        st_row.addWidget(t)
        hint = QLabel("真实采样测速：同时测量延迟与下载速度，综合评分（速度 75% + 延迟 25%），不以延迟论英雄")
        hint.setObjectName("hintLabel")
        st_row.addWidget(hint)
        st_row.addStretch(1)
        self.speed_btn = QPushButton("开始测速")
        self.speed_btn.setObjectName("primaryBtn")
        self.speed_btn.clicked.connect(self._start_speed_test)
        st_row.addWidget(self.speed_btn)
        sv.addLayout(st_row)

        self.speed_table = QTableWidget(0, 5)
        self.speed_table.setHorizontalHeaderLabels(["下载源", "延迟 (ms)", "真实速度 (MB/s)", "综合评分", "状态"])
        self.speed_table.verticalHeader().setVisible(False)
        self.speed_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.speed_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.speed_table.setColumnWidth(0, 170)
        self.speed_table.setColumnWidth(1, 100)
        self.speed_table.setColumnWidth(2, 130)
        self.speed_table.setColumnWidth(3, 100)
        self.speed_table.setColumnWidth(4, 160)
        self.speed_table.setMaximumHeight(150)
        for src in facts.DOWNLOAD_SOURCES:
            r = self.speed_table.rowCount()
            self.speed_table.insertRow(r)
            self.speed_table.setItem(r, 0, QTableWidgetItem(f"{src['name']}（{src['tag']}）"))
            self.speed_table.setItem(r, 1, QTableWidgetItem("—"))
            self.speed_table.setItem(r, 2, QTableWidgetItem("—"))
            self.speed_table.setItem(r, 3, QTableWidgetItem("—"))
            self.speed_table.setItem(r, 4, QTableWidgetItem("未测试"))
        sv.addWidget(self.speed_table)

        self.speed_result_label = QLabel("尚未测速。首次使用建议先测速，软件会自动选择综合最优的源。")
        self.speed_result_label.setObjectName("hintLabel")
        sv.addWidget(self.speed_result_label)
        root.addWidget(speed_card)

        # ── 模型卡片 ──
        head_row = QHBoxLayout()
        mt = QLabel("模型版本")
        mt.setObjectName("sectionTitle")
        head_row.addWidget(mt)
        note = QLabel(f"数据核实于 {facts.VERIFIED_AT} · GitHub 不托管官方权重，故未列入下载源")
        note.setObjectName("hintLabel")
        head_row.addWidget(note)
        head_row.addStretch(1)
        root.addLayout(head_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        grid_wrap = QWidget()
        self.grid = QGridLayout(grid_wrap)
        self.grid.setSpacing(10)
        self.grid.setContentsMargins(0, 0, 6, 0)
        scroll.setWidget(grid_wrap)
        root.addWidget(scroll, 1)

        row, col = 0, 0
        for b in facts.BUNDLES:
            card = self._make_card(b)
            self.grid.addWidget(card, row, col)
            col += 1
            if col >= 2:
                col = 0
                row += 1
        self._grid_next = [row, col]
        self.grid.setRowStretch(row + 1, 1)

    # ═══════════════════════════════════════════════════════
    # 硬件横幅
    # ═══════════════════════════════════════════════════════
    def _refresh_hw_banner(self):
        from .. import planner
        hw = self.ctx.hw
        if hw is None:
            return
        vram = f"显存 {hw.vram_total_gb}GB · " if hw.vram_total_gb > 0 else ""
        if hw.policy == "unsupported":
            self.hw_text.setText(
                f"⚠ 未检测到可用的加速设备（{hw.gpu_name}）。"
                "本地推理需要 NVIDIA / AMD ROCm / 华为昇腾 / Intel 加速硬件；"
                "你仍可以下载 ComfyUI 版本模型在其他硬件上使用。")
            self.plan_text.setText("无法生成最优方案：请先安装加速硬件驱动，然后在设置页点「重新检测」。")
            self.plan_dl_btn.setEnabled(False)
            self.plan_apply_btn.setEnabled(False)
            self._current_plan = None
            return

        self.hw_text.setText(
            f"检测到 {hw.gpu_name} · {hw.backend_label} · {vram}内存 {hw.ram_total_gb}GB"
            f" → {hw.policy_label}")

        plan = planner.make_plan(hw)
        self._current_plan = plan
        lines = [f"推荐模型：{plan.bundle_name}"]
        lines.append(f"推荐理由：{plan.reason}")
        if plan.speed_ref:
            lines.append(f"预期速度：{plan.speed_ref}")
        if plan.quality_note:
            lines.append(f"画质建议：{plan.quality_note}")
        if plan.cost_note:
            lines.append(f"成本：{plan.cost_note}")
        for w in plan.warnings:
            lines.append(f"⚠ {w}")
        for n in hw.notes:
            lines.append(f"· {n}")
        self.plan_text.setText("\n".join(lines))
        self.plan_dl_btn.setEnabled(bool(plan.bundle_id))
        self.plan_apply_btn.setEnabled(bool(plan.bundle_id))

    def refresh_hardware(self):
        self._refresh_hw_banner()

    # ═══════════════════════════════════════════════════════
    # 最优方案采纳
    # ═══════════════════════════════════════════════════════
    def _adopt_plan_download(self):
        from .. import facts
        plan = self._current_plan
        if not plan or not plan.bundle_id:
            return
        b = facts.get_bundle(plan.bundle_id)
        if b is None:
            return
        state, _ = self.ctx.bundle_state(b)
        if state == "complete":
            self.ctx.toast("推荐方案已下载完整，可直接去生成页使用")
            return
        card = self._cards.get(plan.bundle_id)
        if card is None:
            return
        self._start_download(b, card["combo"])

    def _adopt_plan_params(self):
        plan = self._current_plan
        if not plan:
            return
        s = self.ctx.settings
        s.set("default_resolution", plan.resolution, autosave=False)
        s.set("default_steps", plan.steps)
        msg = f"已应用推荐参数：{plan.resolution} / {plan.steps} 步"
        if plan.suggest_turbo_lora:
            msg += "。提示：下载 InstantX Turbo LoRA 后在生成页启用，可切换「速度优先」档"
        self.ctx.toast(msg)

    # ═══════════════════════════════════════════════════════
    # 模型卡片
    # ═══════════════════════════════════════════════════════
    def _make_card(self, b: dict) -> QWidget:
        card = GlassPanel()
        card.setMinimumWidth(360)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)

        top = QHBoxLayout()
        name = QLabel(b["name"])
        name.setObjectName("sectionTitle")
        name.setWordWrap(True)
        top.addWidget(name, 1)
        if b.get("recommended"):
            star = QLabel("★ 推荐")
            star.setObjectName("badge")
            top.addWidget(star)
        v.addLayout(top)

        badges = QHBoxLayout()
        if b.get("heat"):
            heat = QLabel(b["heat"])
            heat.setObjectName("badge")
            heat.setToolTip("社区热度（下载量/好评数，数据日期见徽章）")
            badges.addWidget(heat)
        prec = QLabel(b["precision"])
        prec.setObjectName("badge")
        badges.addWidget(prec)
        size = QLabel(f"{b['size_gb']} GB")
        size.setObjectName("badge")
        badges.addWidget(size)
        if b["min_vram_gb"] > 0:
            vram = QLabel(f"显存 ≥{b['min_vram_gb']}GB")
            vram.setObjectName("badge")
            badges.addWidget(vram)
        eng_txt = {"builtin": "内置引擎可推理", "comfyui": "ComfyUI 专用", "lora": "LoRA 嵌入模型"}
        eng = QLabel(eng_txt.get(b["engine"], b["engine"]))
        eng.setObjectName("badge" if b["engine"] == "builtin" else "badgeWarn")
        badges.addWidget(eng)
        badges.addStretch(1)
        v.addLayout(badges)

        desc = QLabel(b["desc"])
        desc.setObjectName("hintLabel")
        desc.setWordWrap(True)
        v.addWidget(desc)

        # 下载行
        dl_row = QHBoxLayout()
        combo = QComboBox()
        combo.addItem("自动（按测速结果）", "auto")
        for src in facts.DOWNLOAD_SOURCES:
            has = bool(b.get("source_repos", {}).get(src["key"]))
            combo.addItem(f"{src['name']}{'（未上架）' if not has else ''}", src["key"])
            if not has:
                it = combo.model().item(combo.count() - 1)
                it.setEnabled(False)
        dl_row.addWidget(combo, 1)
        btn = QPushButton("下载")
        btn.setObjectName("primaryBtn")
        btn.clicked.connect(lambda _=False, bb=b, cc=combo: self._start_download(bb, cc))
        dl_row.addWidget(btn)
        v.addLayout(dl_row)

        prog = QProgressBar()
        prog.setRange(0, 100)
        prog.setValue(0)
        v.addWidget(prog)
        status = QLabel("")
        status.setObjectName("hintLabel")
        v.addWidget(status)

        # 已安装状态
        state, done_gb = self.ctx.bundle_state(b)
        if state == "complete":
            status.setText(f"✅ 已下载完整（{done_gb:.1f} GB）")
            prog.setValue(100)
            btn.setText("重新校验")
        elif state == "partial":
            status.setText(f"⏸ 已部分下载（{done_gb:.1f} / {b['size_gb']} GB），继续下载将断点续传")
            btn.setText("继续下载")

        self._cards[b["id"]] = {"combo": combo, "btn": btn, "prog": prog, "status": status}
        return card

    # ═══════════════════════════════════════════════════════
    # 测速
    # ═══════════════════════════════════════════════════════
    def _start_speed_test(self):
        self.speed_btn.setEnabled(False)
        self.speed_btn.setText("测速中…")
        sample_mb = int(self.ctx.settings.get("probe_sample_mb"))
        self.speed_result_label.setText(
            f"正在对 3 个源做真实采样（每源约 {sample_mb}MB）…")
        self._worker = SpeedTestWorker(sample_mb=sample_mb)
        self._worker.one.connect(self._on_probe)
        self._worker.done.connect(self._on_speed_done)
        self._worker.start()

    def _row_of(self, key):
        for i, src in enumerate(facts.DOWNLOAD_SOURCES):
            if src["key"] == key:
                return i
        return -1

    def _on_probe(self, r):
        row = self._row_of(r.key)
        if row < 0:
            return
        if r.ok:
            self.speed_table.setItem(row, 1, QTableWidgetItem(f"{r.latency_ms:.0f}"))
            self.speed_table.setItem(row, 2, QTableWidgetItem(f"{r.speed_mbs:.1f}"))
            self.speed_table.setItem(row, 4, QTableWidgetItem(f"采样 {r.sampled_mb}MB 完成"))
        else:
            self.speed_table.setItem(row, 4, QTableWidgetItem(f"❌ {r.error}"))

    def _on_speed_done(self, results):
        self._speed_results = results
        for r in results:
            row = self._row_of(r.key)
            if row < 0:
                continue
            self.speed_table.setItem(row, 3, QTableWidgetItem(f"{r.score:.0f}" if r.ok else "0"))
        best = pick_best_source(results, self.ctx.settings.get("preferred_source"))
        best_name = next((r.name for r in results if r.key == best), best)
        self.speed_result_label.setText(
            f"✅ 测速完成。综合最优：{best_name}（评分按 速度75% + 延迟25% 计算，已自动用于下载）")
        self.ctx.settings.set("preferred_source_last_best", best)
        self.ctx.status(f"测速完成，推荐源：{best_name}")
        self.speed_btn.setEnabled(True)
        self.speed_btn.setText("重新测速")

    # ═══════════════════════════════════════════════════════
    # 下载
    # ═══════════════════════════════════════════════════════
    def _start_download(self, b: dict, combo: QComboBox):
        source = combo.currentData()
        if source == "auto":
            if self._speed_results:
                source = pick_best_source(self._speed_results,
                                          self.ctx.settings.get("preferred_source"))
            else:
                source = self.ctx.settings.get("preferred_source_last_best", "modelscope")
        # 所选源未上架该模型时，自动回退到有货的最优源（按测速排序优先）
        repos = b.get("source_repos", {})
        if not repos.get(source):
            order = [r.key for r in self._speed_results if r.ok] if self._speed_results else []
            order += ["modelscope", "hf_mirror", "hf"]
            for cand in order:
                if repos.get(cand):
                    self.ctx.toast("该模型在首选源未上架，已自动改用可用源")
                    source = cand
                    break
            else:
                self.ctx.toast("该模型没有可用的下载源")
                return

        task = self.ctx.downloads.get(b["id"])
        if task and task.is_alive():
            self.ctx.toast("该模型正在下载中")
            return

        dest = os.path.join(self.ctx.settings.get("models_dir"), b["id"])
        task = BundleDownloadTask(
            bundle_id=b["id"], source_key=source,
            dest_dir=__import__("pathlib").Path(dest),
            retries=int(self.ctx.settings.get("download_retries")))
        self.ctx.downloads[b["id"]] = task
        card = self._cards[b["id"]]
        card["btn"].setEnabled(False)
        card["btn"].setText("下载中…")
        card["status"].setText("正在列举远端文件…")
        task.run()
        self.ctx.status(f"开始下载：{b['name']}（源：{source}）")

    def _tick_downloads(self):
        for bid, task in list(self.ctx.downloads.items()):
            card = self._cards.get(bid)
            # DIY 自定义包：动态补一张进度卡片
            if card is None and task.bundle is not None:
                widget = self._make_card(task.bundle)
                r, c = self._grid_next
                self.grid.addWidget(widget, r, c)
                c += 1
                if c >= 2:
                    c = 0
                    r += 1
                self._grid_next = [r, c]
                self.grid.setRowStretch(r + 1, 1)
                card = self._cards.get(bid)
            if not card:
                continue
            p = task.progress
            # 终态只处理一次（防止每秒重复 toast / 重复刷新）
            if p.status in ("done", "error", "cancelled"):
                if getattr(task, "_ui_done", False):
                    continue
                task._ui_done = True
            if p.status == "downloading":
                card["prog"].setValue(int(p.percent))
                eta = f"{p.eta_s // 60:.0f} 分钟" if p.eta_s > 0 else "…"
                card["status"].setText(
                    f"{p.percent:.1f}% · {p.done_bytes / 1e9:.2f} / "
                    f"{p.total_bytes / 1e9:.2f} GB · {p.speed_mbs} MB/s · 剩余 {eta}"
                    + (f" · {p.current_file}" if p.current_file else ""))
            elif p.status == "done":
                card["prog"].setValue(100)
                card["status"].setText("✅ 下载完成，可在「我的模型」中查看 / 加载")
                card["btn"].setText("已完成")
                card["btn"].setEnabled(False)
                self.ctx.toast(f"模型下载完成：{task.bundle['name']}")
                self.ctx.library_dirty()
            elif p.status == "error":
                card["status"].setText(f"❌ 下载出错：{p.error}（再次点击下载可断点续传）")
                card["btn"].setText("继续下载")
                card["btn"].setEnabled(True)
            elif p.status == "cancelled":
                card["status"].setText("已取消（文件保留，可续传）")
                card["btn"].setText("继续下载")
                card["btn"].setEnabled(True)

    def cancel_all(self):
        for task in self.ctx.downloads.values():
            task.cancel()
