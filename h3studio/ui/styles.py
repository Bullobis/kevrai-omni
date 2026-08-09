# -*- coding: utf-8 -*-
"""
ui/styles.py — QSS 主题样式生成
================================
磨砂玻璃拟态：半透明面板 + 细边框 + 圆角 + 阴影，配合动态极光背景。
"""


def hex_to_rgba(hex_color: str, alpha_pct: int) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{max(0, min(100, alpha_pct)) / 100.0:.2f})"


def build_qss(theme: dict, accent: str, glass_opacity: int, font_px: int = 13) -> str:
    text = theme["text"]
    dim = theme["text_dim"]
    glass = hex_to_rgba("#ffffff", max(4, glass_opacity // 12))      # 面板白色玻璃底
    glass_solid = hex_to_rgba(theme["bg_aurora_a"], glass_opacity)
    border = hex_to_rgba("#ffffff", 10)
    border_hi = hex_to_rgba("#ffffff", 22)
    accent_soft = hex_to_rgba(accent, 18)
    # 渐变副色：向紫/蓝偏移
    _h = accent.lstrip("#")
    _r, _g, _b = int(_h[0:2], 16), int(_h[2:4], 16), int(_h[4:6], 16)
    _r2 = min(255, _r + 40); _b2 = min(255, _b + 60); _g2 = max(0, _g - 10)
    accent2 = f"#{_r2:02x}{_g2:02x}{_b2:02x}"
    accent_softer = hex_to_rgba(accent, 10)

    return f"""
/* ── 基础 ─────────────────────────────── */
* {{ outline: none; }}
QWidget {{
    color: {text};
    font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    font-size: {font_px}px;
}}
QToolTip {{
    background: {glass_solid};
    color: {text};
    border: 1px solid {border_hi};
    border-radius: 6px;
    padding: 6px 9px;
}}

/* ── 面板 ─────────────────────────────── */
QFrame#glassPanel {{
    background: {glass};
    border: 1px solid {border};
    border-radius: 14px;
}}
QFrame#glassPanelStrong {{
    background: {glass_solid};
    border: 1px solid {border_hi};
    border-radius: 14px;
}}
QLabel#sectionTitle {{
    font-size: 14px;
    font-weight: 600;
    color: {text};
}}
QLabel#hintLabel {{ color: {dim}; font-size: 12px; }}
QLabel#bigStat {{ font-size: 22px; font-weight: 700; color: {accent}; }}
QLabel#accentText {{ color: {accent}; }}
QLabel#dimText {{ color: {dim}; }}
QLabel#badge {{
    background: {accent_softer};
    color: {accent};
    border: 1px solid {accent_soft};
    border-radius: 8px;
    padding: 2px 8px;
    font-size: 11px;
}}
QLabel#badgeWarn {{
    background: rgba(250, 204, 21, 0.10);
    color: #facc15;
    border: 1px solid rgba(250, 204, 21, 0.25);
    border-radius: 8px;
    padding: 2px 8px;
    font-size: 11px;
}}

/* ── 导航 ─────────────────────────────── */
QPushButton#navBtn {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 9px 12px;
    text-align: left;
    color: {dim};
    font-size: 13px;
}}
QPushButton#navBtn:hover {{ background: {hex_to_rgba("#ffffff", 6)}; color: {text}; }}
QPushButton#navBtn:checked {{
    background: {accent_soft};
    border: 1px solid {hex_to_rgba(accent, 30)};
    color: {accent};
    font-weight: 600;
}}

/* ── 按钮 ─────────────────────────────── */
QPushButton {{
    background: {hex_to_rgba("#ffffff", 8)};
    border: 1px solid {border};
    border-radius: 9px;
    padding: 7px 14px;
    color: {text};
}}
QPushButton:hover {{ background: {hex_to_rgba("#ffffff", 14)}; border-color: {border_hi}; }}
QPushButton:pressed {{ background: {hex_to_rgba("#ffffff", 5)}; }}
QPushButton:disabled {{ color: {hex_to_rgba(text, 35)}; background: {hex_to_rgba("#ffffff", 3)}; }}

QPushButton#primaryBtn {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {accent}, stop:1 {accent2});
    color: #ffffff;
    border: none;
    border-radius: 10px;
    padding: 10px 18px;
    font-weight: 700;
    font-size: 14px;
}}
QPushButton#primaryBtn:hover {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {accent2}, stop:1 {accent});
}}
QPushButton#primaryBtn:disabled {{ background: {hex_to_rgba(accent, 30)}; color: rgba(255,255,255,0.5); }}

QPushButton#dangerBtn {{
    background: rgba(239, 68, 68, 0.12);
    color: #f87171;
    border: 1px solid rgba(239, 68, 68, 0.3);
}}
QPushButton#chipBtn {{
    background: {hex_to_rgba("#ffffff", 5)};
    border: 1px solid {border};
    border-radius: 14px;
    padding: 5px 12px;
    font-size: 12px;
}}
QPushButton#chipBtn:checked {{
    background: {accent_soft};
    border-color: {accent};
    color: {accent};
    font-weight: 600;
}}
QPushButton#iconBtn {{
    background: transparent; border: none; border-radius: 8px; padding: 6px;
}}
QPushButton#iconBtn:hover {{ background: {hex_to_rgba("#ffffff", 10)}; }}

/* ── 输入 ─────────────────────────────── */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {hex_to_rgba("#000000", 25)};
    border: 1px solid {border};
    border-radius: 9px;
    padding: 7px 10px;
    selection-background-color: {accent_soft};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {accent};
    background: {hex_to_rgba("#000000", 35)};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid {dim}; margin-right: 8px; }}
QComboBox QAbstractItemView {{
    background: {theme["bg_aurora_b"]};
    border: 1px solid {border_hi};
    border-radius: 8px;
    selection-background-color: {accent_soft};
    selection-color: {text};
}}

/* ── 滑块 ─────────────────────────────── */
QSlider::groove:horizontal {{ height: 4px; background: {hex_to_rgba("#ffffff", 15)}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 15px; height: 15px; margin: -6px 0;
    border-radius: 8px; background: {accent};
    border: 2px solid {hex_to_rgba("#ffffff", 70)};
}}

/* ── 进度条 ────────────────────────────── */
QProgressBar {{
    background: {hex_to_rgba("#ffffff", 8)};
    border: none; border-radius: 6px;
    height: 12px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 6px; }}

/* ── 滚动条 ────────────────────────────── */
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {hex_to_rgba("#ffffff", 18)}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {accent}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {hex_to_rgba("#ffffff", 18)}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ── 单选 / 复选 ───────────────────────── */
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {border_hi}; background: {hex_to_rgba("#000000", 25)}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}

/* ── 表格 ─────────────────────────────── */
QTableWidget {{
    background: transparent; border: none; gridline-color: {border};
    alternate-background-color: {hex_to_rgba("#ffffff", 3)};
}}
QHeaderView::section {{
    background: transparent; color: {dim}; border: none;
    border-bottom: 1px solid {border}; padding: 6px;
}}
QTableWidget::item {{ padding: 4px; border: none; }}
QTableWidget::item:selected {{ background: {accent_soft}; color: {text}; }}

/* ── 分组框 ────────────────────────────── */
QGroupBox {{
    border: 1px solid {border};
    border-radius: 12px;
    margin-top: 10px;
    padding-top: 6px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {dim}; }}

/* ── 分割条 ────────────────────────────── */
QSplitter::handle {{ background: transparent; }}
"""
