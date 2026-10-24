#!/usr/bin/env python3
"""对接链路审计：后端路由 -> IPC 桥接 -> preload -> api.js -> 前端模块。

逐层回答「这一层有没有」，找出断在哪一环：
  L1 后端路由      python/app/main.py  的 @app.get("/api/...") 等
  L2 IPC 桥接      electron/main.js    的 sidecarFetch("/api/...")
  L3 preload 暴露  electron/preload.js 的 contextBridge 方法名
  L4 api.js 封装   renderer/modules/api.js 里 api.xxx
  L5 前端模块调用  renderer/modules/*.js 里 api.xxx()

只报告不改代码。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BE = ROOT / "python" / "app" / "main.py"
IPC = ROOT / "electron" / "main.js"
PRE = ROOT / "electron" / "preload.js"
API = ROOT / "renderer" / "modules" / "api.js"
MODS = ROOT / "renderer" / "modules"


def norm(p: str) -> str:
    """统一路径形态：去 query、去模板表达式、参数名归一。"""
    p = p.split("?")[0].split("#")[0]
    # JS 模板里的 ${...} 有两种身份：
    #   ${kbId}  → 真实路径参数，应归一成 {ID}
    #   ${qs} / ${suffix} → 拼接的 query 尾巴，应删掉
    # 两种形态都算命中，由调用方取并集，避免误报。
    p = re.sub(r"\$\{[^}]*\}", "{ID}", p)
    p = re.sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "{ID}", p)
    p = re.sub(r"/+", "/", p)
    return p.rstrip("/") or "/"


def norm_loose(p: str) -> str:
    """把 ${...} 当拼接尾巴删掉的形态。"""
    p = p.split("?")[0].split("#")[0]
    p = re.sub(r"\$\{[^}]*\}", "", p)
    p = re.sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "{ID}", p)
    p = re.sub(r"/+", "/", p)
    return p.rstrip("/") or "/"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


# ---- L1 后端路由
def l1_routes() -> set[str]:
    src = read(BE)
    out = set()
    for m in re.finditer(r'@app\.(get|post|put|delete|patch)\(\s*"([^"]+)"', src):
        if m.group(2).startswith("/api/"):
            out.add(norm(m.group(2)))
    return out


# ---- L2 IPC 桥接里出现的 sidecar 路径
def l2_paths() -> set[str]:
    src = read(IPC)
    out = set()
    # sidecarFetch("...") / sidecarFetchBytes(`...`) 以及任何 "/api/..." 字面量
    for m in re.finditer(r'["`](/api/[^"`]*)["`]', src):
        out.add(norm(m.group(1)))
        out.add(norm_loose(m.group(1)))
    return out


# ---- L3 preload 暴露的方法名
def l3_methods() -> set[str]:
    src = read(PRE)
    return set(re.findall(r"^\s{2,}([A-Za-z][A-Za-z0-9]*)\s*:", src, re.M))


# ---- L4 api.js 里定义/转发的方法名
def l4_methods() -> set[str]:
    src = read(API)
    return set(re.findall(r"^\s{2}([A-Za-z][A-Za-z0-9]*)\s*[:(]", src, re.M))


# ---- L5 前端模块实际调用的 api.xxx
def l5_calls() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for p in sorted(MODS.glob("*.js")):
        if p.name == "api.js":
            continue
        src = read(p)
        names = set(re.findall(r"\bapi\.([A-Za-z][A-Za-z0-9]*)\s*\(", src))
        if names:
            out[p.name] = names
    return out


def main() -> int:
    r1, r2 = l1_routes(), l2_paths()
    m3, m4 = l3_methods(), l4_methods()
    c5 = l5_calls()
    used5 = set().union(*c5.values()) if c5 else set()

    print(f"L1 后端路由        : {len(r1)}")
    print(f"L2 IPC 桥接路径    : {len(r2)}")
    print(f"L3 preload 方法    : {len(m3)}")
    print(f"L4 api.js 方法     : {len(m4)}")
    print(f"L5 前端模块调用    : {len(used5)} 个不同方法，分布于 {len(c5)} 个模块")

    print("\n=== A. 后端路由无 IPC 桥接（UI 调不到） ===")
    a = sorted(r1 - r2)
    print(f"  {len(a)} 个")
    for x in a:
        print(f"    [!] {x}")

    print("\n=== B. preload 暴露但 api.js 没封装（桥接白做） ===")
    lower4 = {m.lower() for m in m4}
    b = sorted(m for m in m3 if m.lower() not in lower4)
    print(f"  {len(b)} 个")
    for x in b[:40]:
        print(f"    [!] {x}")

    print("\n=== C. api.js 封装了但前端从没调用（死封装） ===")
    lower5 = {x.lower() for x in used5}
    lower3 = {m.lower() for m in m3}
    c = sorted(m for m in m4 if m.lower() not in lower5 and m.lower() in lower3)
    print(f"  {len(c)} 个")
    for x in c[:40]:
        print(f"    [!] {x}")

    print("\n=== D. 前端调用了但 preload 没暴露（会运行时报错） ===")
    d = sorted(x for x in used5 if x.lower() not in lower3 and x.lower() not in lower4)
    print(f"  {len(d)} 个")
    for x in d[:40]:
        print(f"    [!] {x}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
