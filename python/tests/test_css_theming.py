"""CSS 完整性回归测试。

为什么需要这些测试：`renderer/styles.css` 里同时存在两套变量命名——新的
`--bg-*` / `--fg` / `--bord` 一族，以及早期区块（约 660 行之后）使用的
`--accent` / `--border` / `--card` / `--bg` / `--sel` / `--mono`。旧变量在
每个调用点都写了深色字面量兜底（`var(--card, #1b1c1f)`），而
`html[data-theme="light"]` 只覆盖了新变量。

后果：浅色主题下这些区块拿不到覆盖，仍渲染深色底 + 深色字。
实测 Agent 消息区在修复前是 bg=rgb(18,19,22) 配 fg=rgb(27,31,58)，
对比度 1.15:1（不可读）；修复后为 14.94:1。

这类 bug 单元测试天然看不见（它只在计算样式里体现），所以这里用**静态
分析**兜住：断言旧变量名有定义、两套主题都覆盖到。真实浏览器对比度复核
见 `.promo/theme_audit.py`。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parents[2] / "renderer" / "styles.css"

#: 旧调色板变量名 —— 必须有定义，否则调用点的深色兜底会泄漏到浅色主题。
LEGACY_VARS = ["accent", "border", "card", "bg", "sel", "mono"]

#: 新调色板的核心变量 —— 浅色主题必须覆盖它们。
THEMED_VARS = ["bg-0", "bg-1", "bg-2", "fg", "mut", "card", "bord", "line", "shadow"]


@pytest.fixture(scope="module")
def css() -> str:
    assert CSS.exists(), f"找不到样式文件：{CSS}"
    return CSS.read_text(encoding="utf-8")


def _block(css: str, selector: str) -> str:
    """取出某个选择器的声明块原文（不做完整 CSS 解析，够用即可）。"""
    m = re.search(
        re.escape(selector) + r"\s*\{(.*?)\}",
        css,
        re.S,
    )
    assert m, f"样式里找不到选择器块：{selector}"
    return m.group(1)


def _defined(block: str) -> set[str]:
    return set(re.findall(r"--([a-zA-Z0-9-]+)\s*:", block))


class TestLegacyPaletteIsBridged:
    """旧调色板必须被桥接到真实令牌上，浅色主题才不会漏。"""

    @pytest.mark.parametrize("name", LEGACY_VARS)
    def test_legacy_var_is_defined(self, css: str, name: str) -> None:
        root = _defined(_block(css, ":root"))
        assert name in root, (
            f"--{name} 未定义。旧区块用的是 var(--{name}, <深色兜底>)，"
            f"一旦浅色主题不覆盖它，兜底深色就会顶上来，文字直接不可读。"
        )

    @pytest.mark.parametrize("name", ["accent", "border", "bg"])
    def test_legacy_var_points_at_new_token(self, css: str, name: str) -> None:
        root = _block(css, ":root")
        # 必须是指向新令牌的别名（var(--...)），而不是硬编码色值 ——
        # 硬编码就无法跟随主题切换。
        pat = rf"--{name}\s*:\s*var\(--[a-z0-9-]+\)"
        assert re.search(pat, root), (
            f"--{name} 应当别名到一个新令牌（var(--...)），"
            f"否则换肤时它不会跟着变。"
        )


class TestLightThemeCoverage:
    """浅色主题必须覆盖全部影响可读性的核心变量。"""

    @pytest.mark.parametrize("name", THEMED_VARS)
    def test_light_theme_overrides_var(self, css: str, name: str) -> None:
        light = _defined(_block(css, 'html[data-theme="light"]'))
        assert name in light, (
            f'html[data-theme="light"] 没有覆盖 --{name}，'
            f"浅色主题下会退回深色值。"
        )

    def test_light_theme_redefines_sel(self, css: str) -> None:
        """--sel 是叠加高亮，深色下用白、浅色下必须用黑。"""
        light = _block(css, 'html[data-theme="light"]')
        assert re.search(r"--sel\s*:\s*rgba\(\s*0\s*,\s*0\s*,\s*0", light), (
            "--sel 在浅色主题下仍应是黑色叠加；若沿用白色的 rgba(255,255,255,..)，"
            "搜索下拉的键盘高亮在浅色底上完全看不见。"
        )


class TestNoUndefinedVarWithoutFallback:
    """不带兜底的 var() 引用必须能找到定义。"""

    def test_every_var_is_defined_or_has_fallback(self, css: str) -> None:
        defined = _defined(css)
        missing: list[str] = []
        for m in re.finditer(r"var\(\s*--([a-zA-Z0-9-]+)\s*([,)])", css):
            name, nxt = m.group(1), m.group(2)
            if name in defined:
                continue
            if nxt == ",":
                continue          # 有兜底，降级路径明确
            missing.append(name)
        assert not missing, (
            "以下变量被引用但没有定义、也没有兜底值，会直接导致该条声明失效："
            f"{sorted(set(missing))}"
        )


class TestThemeSelectorIsWired:
    """renderer 必须真的把 data-theme 写到 <html> 上，否则整套浅色主题是死代码。"""

    def test_theme_module_sets_dataset(self) -> None:
        js = (Path(__file__).resolve().parents[2] / "renderer" / "modules" / "theme.js")
        src = js.read_text(encoding="utf-8")
        assert "documentElement.dataset.theme" in src, (
            "theme.js 没有写 documentElement.dataset.theme —— "
            "CSS 里的 html[data-theme=...] 规则永远不会生效。"
        )
        assert '"light"' in src and '"dark"' in src, "主题取值应同时覆盖 light / dark"
