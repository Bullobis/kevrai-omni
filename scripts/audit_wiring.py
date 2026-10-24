#!/usr/bin/env python3
"""接线审计：找出「只有测试、没有对接」的死代码。

判据（不止一种，避免误报）：
  A. 死模块：模块只被 tests/ 导入，生产代码（main.py 可达图）完全没引用
  B. 死路由：定义了 APIRouter 但没有被 include_router
  C. 死工具：agent/tools 下定义了工具函数但没有注册进技能表
  D. 死前端模块：renderer 下的 JS 文件没有被 index.html 或任何 JS 引用

只报告，不改代码。退出码 0（审计本身不该让 CI 挂；要看数就自己读输出）。
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "python" / "app"
TESTS = ROOT / "python" / "tests"

# ---------------------------------------------------------------- 导入图


def module_name_of(path: Path) -> str:
    """把文件路径换回 app.xxx 形式的模块名。"""
    try:
        rel = path.relative_to(ROOT / "python")
    except ValueError:
        return ""
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def parse_imports(path: Path) -> set[str]:
    """返回该文件 import 到的、属于本项目的模块名集合。"""
    out: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith(("app", "python.app")):
                    out.add(a.name.replace("python.", ""))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:  # 相对导入，按所在包解析
                pkg = module_name_of(path)
                pkg_parts = pkg.split(".")[:-1] if "." in pkg else []
                base_parts = pkg_parts[: len(pkg_parts) - (node.level - 1)] if node.level > 1 else pkg_parts
                mod = ".".join([*base_parts, mod]) if mod else ".".join(base_parts)
            if mod.startswith("app"):
                out.add(mod)
                # from app.x import y 也可能是 from app.x.y import z
                for a in node.names:
                    out.add(f"{mod}.{a.name}")
    return out


def collect_py_files() -> tuple[list[Path], list[Path]]:
    prod, test = [], []
    for p in (ROOT / "python").rglob("*.py"):
        s = str(p).replace("\\", "/")
        if "__pycache__" in s or "/tests/" in s or s.endswith("/test_*.py") or "/conftest" in s:
            test.append(p)
        else:
            prod.append(p)
    return prod, test


def build_graph(files: list[Path]) -> dict[str, set[str]]:
    g: dict[str, set[str]] = {}
    for f in files:
        mn = module_name_of(f)
        if mn:
            g.setdefault(mn, set()).update(parse_imports(f))
    return g


def reachable_from(graph: dict[str, set[str]], roots: list[str]) -> set[str]:
    seen: set[str] = set()
    stack = [r for r in roots if r in graph or any(k.startswith(r) for k in graph)]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for nxt in graph.get(cur, ()):  # 精确
            if nxt not in seen:
                stack.append(nxt)
        # 前缀匹配：import app.x.y 也算覆盖了 app.x
        for k in graph:
            if k != cur and (k.startswith(cur + ".")) and k not in seen:
                stack.append(k)
    return seen


def audit_dead_modules() -> list[tuple[str, str]]:
    prod, test = collect_py_files()
    prod_graph = build_graph(prod)
    test_graph = build_graph(test)

    entry = "app.main"
    reachable = reachable_from(prod_graph, [entry])
    # 其它非 test 入口（脚本、sidecar 启动器）也算生产
    scripts = [p for p in prod if "/scripts/" in str(p).replace("\\", "/")]
    for s in scripts:
        reachable |= reachable_from(prod_graph, [module_name_of(s)])

    findings = []
    for p in prod:
        mn = module_name_of(p)
        if not mn or not mn.startswith("app."):
            continue
        if mn in reachable:
            continue
        used_in_test = any(mn in mods or any(x.startswith(mn) for x in mods) for mods in test_graph.values())
        prod_refs = [k for k, v in prod_graph.items() if mn in v or any(x.startswith(mn + ".") for x in v)]
        if used_in_test and not prod_refs:
            findings.append((mn, "仅被测试引用，生产代码无引用"))
        elif not used_in_test and not prod_refs:
            findings.append((mn, "完全无引用"))
    return sorted(findings)


# ---------------------------------------------------------------- 死路由


def audit_dead_routers() -> list[tuple[str, str]]:
    main_py = APP / "main.py"
    if not main_py.exists():
        return []
    main_src = main_py.read_text(encoding="utf-8", errors="replace")

    defined: dict[str, Path] = {}
    for p in (APP).rglob("*.py"):
        src = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"^\s*(\w+)\s*=\s*APIRouter\s*\(", src, re.M):
            defined.setdefault(m.group(1), p)

    findings = []
    for name, path in sorted(defined.items()):
        if f"include_router({name}" in main_src or re.search(rf"include_router\(\s*{re.escape(name)}\s*[,)]", main_src):
            continue
        findings.append((f"{name} ({path.relative_to(ROOT)})", "定义了 APIRouter 但未 include_router"))
    return findings


# ---------------------------------------------------------------- 死工具


def audit_dead_tools() -> list[tuple[str, str]]:
    tools_dir = APP / "agent" / "tools"
    if not tools_dir.exists():
        return []
    init = tools_dir / "__init__.py"
    init_src = init.read_text(encoding="utf-8", errors="replace") if init.exists() else ""
    tools_src = (APP / "agent" / "tools.py").read_text(encoding="utf-8", errors="replace") if (APP / "agent" / "tools.py").exists() else ""

    findings = []
    for p in sorted(tools_dir.rglob("*.py")):
        if p.name == "__init__.py":
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                if node.name not in init_src and node.name not in tools_src:
                    findings.append((f"{node.name} ({p.name})", "工具函数定义但未注册"))
    return findings


# ---------------------------------------------------------------- 前端


def audit_dead_js() -> list[tuple[str, str]]:
    rend = ROOT / "src" / "renderer"
    if not rend.exists():
        return []
    js_files = [p for p in rend.rglob("*.js") if "__pycache__" not in str(p)]
    index = ROOT / "src" / "renderer" / "index.html"
    html_src = index.read_text(encoding="utf-8", errors="replace") if index.exists() else ""

    all_src = html_src
    for p in js_files:
        all_src += "\n" + p.read_text(encoding="utf-8", errors="replace")

    findings = []
    for p in js_files:
        stem = p.stem
        # 被 script src 引入 / 被 import / 被 require / 被全局函数名引用
        pats = [
            rf'src=["\'][^"\']*{re.escape(p.name)}',
            rf'import\s+.*["\'][^"\']*{re.escape(stem)}',
            rf'require\(["\'][^"\']*{re.escape(stem)}',
        ]
        if any(re.search(x, all_src) for x in pats):
            continue
        findings.append((str(p.relative_to(ROOT)), "JS 文件未被任何 html/js 引用"))
    return findings


# ---------------------------------------------------------------- main


def main() -> int:
    sections = [
        ("A. 死模块（仅测试引用 / 完全无引用）", audit_dead_modules()),
        ("B. 死路由（定义未挂载）", audit_dead_routers()),
        ("C. 死工具（定义未注册）", audit_dead_tools()),
        ("D. 死前端模块（未引用）", audit_dead_js()),
    ]
    total = 0
    for title, items in sections:
        print(f"\n=== {title} ===")
        if not items:
            print("  (无)")
            continue
        for name, why in items:
            print(f"  [!] {name}  ->  {why}")
        total += len(items)
    print(f"\n合计可疑项：{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
