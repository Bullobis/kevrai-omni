#!/usr/bin/env python3
"""Kevrai Omni 一键环境体检。

用法（不需要懂命令行，双击或复制粘贴即可）::

    python scripts/health_check.py                 # 打印体检报告
    python scripts/health_check.py --md report.md  # 导出 Markdown 报告
    python scripts/health_check.py --json out.json # 导出 JSON
    python scripts/health_check.py --no-network    # 跳过联网检查（离线环境）

设计原则：
    * **只依赖标准库**。即使项目依赖一个都没装，本脚本也必须能跑起来并把
      缺什么说清楚 —— 否则"体检工具自己打不开"就太讽刺了。
    * 每一项都给出「哪里不行 + 怎么修」，修复建议写到能直接复制执行的命令。
    * 单项失败不影响其他项；任何异常都被记为 skip 而不是崩溃。
    * 退出码：有 FAIL 返回 1，全部通过返回 0（方便写进 CI / 批处理）。
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"

#: 体检顺序权重（数字越小越先显示）
STATUS_ORDER = {FAIL: 0, WARN: 1, SKIP: 2, OK: 3}

ICON = {OK: "[OK]", WARN: "[!!]", FAIL: "[XX]", SKIP: "[--]"}

#: 后端运行必需的核心依赖（包名 -> import 名）
BACKEND_DEPS: tuple[tuple[str, str], ...] = (
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("httpx", "httpx"),
    ("pydantic", "pydantic"),
    ("python-multipart", "multipart"),
    ("PyYAML", "yaml"),
    ("jsonschema", "jsonschema"),
    ("websockets", "websockets"),
    ("numpy", "numpy"),
)

#: 可选依赖 —— 缺了不影响启动，只影响某个功能
OPTIONAL_DEPS: tuple[tuple[str, str, str], ...] = (
    ("huggingface-hub", "huggingface_hub", "模型市场下载"),
    ("xxhash", "xxhash", "文件完整性校验加速"),
    ("pypdf", "pypdf", "知识库 PDF 解析"),
    ("defusedxml", "defusedxml", "知识库 docx 解析（安全）"),
    ("imageio-ffmpeg", "imageio_ffmpeg", "视频导出 MP4"),
)

DEFAULT_PORT = 8800
MIN_DISK_GB = 20.0          # 模型动辄几十 GB，低于此值给警告
WARN_DISK_GB = 60.0


# ---------------------------------------------------------------------------
# 结果模型
# ---------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _repo_root() -> Path:
    """脚本位于 <root>/scripts/ 下，向上两级不一定对，做稳健查找。"""
    here = Path(__file__).resolve()
    for parent in (here.parent.parent, here.parent):
        if (parent / "python" / "app").is_dir() or (parent / "package.json").is_file():
            return parent
    return here.parent.parent


def _python_dir(root: Path) -> Path:
    return root / "python"


# ---------------------------------------------------------------------------
# 各项检查
# ---------------------------------------------------------------------------


def check_python_version() -> Check:
    v = sys.version_info
    cur = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 10):
        return Check("运行环境 · Python 版本", OK, f"Python {cur}（满足 >= 3.10）")
    return Check(
        "运行环境 · Python 版本", FAIL,
        f"当前 Python {cur}，项目要求 >= 3.10（部分语法与依赖会直接报错）",
        "到 python.org 下载安装 Python 3.10 ~ 3.12；Windows 安装时务必勾选 "
        "「Add Python to PATH」。装完重开终端再运行本脚本。",
        {"current": cur},
    )


def check_platform() -> Check:
    sysname = platform.system()
    detail = f"{sysname} {platform.release()} · {platform.machine()}"
    if sysname not in ("Windows", "Linux", "Darwin"):
        return Check("运行环境 · 操作系统", WARN, detail,
                     "未在官方支持列表内（Windows / Linux / macOS），可能遇到未知问题。")
    return Check("运行环境 · 操作系统", OK, detail)


def _module_installed(import_name: str) -> bool:
    with contextlib.suppress(Exception):
        return importlib.util.find_spec(import_name) is not None
    return False


def _version_of(import_name: str) -> str:
    try:
        mod = importlib.import_module(import_name)
        return str(getattr(mod, "__version__", "") or "")
    except Exception:
        try:
            import importlib.metadata as md
            return md.version(import_name)
        except Exception:
            return ""


def check_backend_deps(root: Path) -> Check:
    missing: list[str] = []
    found: list[str] = []
    for pkg, imp in BACKEND_DEPS:
        if _module_installed(imp):
            v = _version_of(imp)
            found.append(f"{pkg}{(' ' + v) if v else ''}")
        else:
            missing.append(pkg)

    req = _python_dir(root) / "requirements.txt"
    pip_cmd = f'python -m pip install -r "{req}"' if req.exists() else (
        "python -m pip install -r python/requirements.txt"
    )
    if missing:
        return Check(
            "后端依赖", FAIL,
            f"缺少 {len(missing)} 个必需包：{', '.join(missing)}",
            f"在项目根目录执行：{pip_cmd}\n"
            f"国内较慢可加镜像：{pip_cmd} -i https://pypi.tuna.tsinghua.edu.cn/simple",
            {"missing": missing, "found": found},
        )
    return Check("后端依赖", OK, f"{len(found)} 个必需包齐全", extra={"found": found})


def check_optional_deps() -> Check:
    missing = [(p, use) for p, imp, use in OPTIONAL_DEPS if not _module_installed(imp)]
    if missing:
        desc = "；".join(f"{p}（影响：{u}）" for p, u in missing)
        names = " ".join(p for p, _ in missing)
        return Check(
            "可选依赖", WARN,
            f"{len(missing)} 个可选包未安装：{desc}",
            f"用到对应功能时再装即可：python -m pip install {names}",
            {"missing": [p for p, _ in missing]},
        )
    return Check("可选依赖", OK, "可选依赖齐全")


def check_frontend_toolchain() -> Check:
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node:
        return Check(
            "前端 · Node.js 工具链", FAIL,
            "未检测到 node（前端无法安装依赖与启动）",
            "到 nodejs.org 下载 LTS 版本安装（一路下一步即可），装完重开终端。"
            "只想用后端的话可以忽略这一项。",
        )
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True,
                             timeout=10, check=False).stdout.strip()
    except Exception:
        out = "未知版本"
    if not npm:
        return Check("前端 · Node.js 工具链", WARN,
                     f"node {out} 已安装，但没找到 npm（通常随 Node 一起安装）",
                     "重装 Node.js LTS，安装时保持默认勾选 npm。")
    return Check("前端 · Node.js 工具链", OK, f"node {out} · npm 可用")


def check_frontend_deps(root: Path) -> Check:
    pkg_json = root / "package.json"
    if not pkg_json.is_file():
        return Check("前端依赖", SKIP, "未找到 package.json，跳过")
    node_modules = root / "node_modules"
    if node_modules.is_dir():
        try:
            n = sum(1 for _ in node_modules.iterdir())
        except Exception:
            n = -1
        return Check("前端依赖", OK, f"node_modules 已安装（{n} 个条目）")
    return Check(
        "前端依赖", WARN,
        "node_modules 不存在，前端尚未安装依赖",
        "在项目根目录执行：npm install\n"
        "国内较慢可先执行：npm config set registry https://registry.npmmirror.com",
    )


def _detect_gpus() -> tuple[str, str]:
    """尽量复用项目自身的探测逻辑；拿不到就返回未知。"""
    root = _repo_root()
    pdir = _python_dir(root)
    if str(pdir) not in sys.path:
        sys.path.insert(0, str(pdir))
    try:
        from app.gpu import detect  # type: ignore
        gpus = asyncio.run(detect())
        if not gpus:
            return "未知", ""
        best = max(gpus, key=lambda g: g.vram_mb)
        names = "，".join(f"{g.name}" for g in gpus[:3])
        vram = f"{best.vram_mb / 1024:.1f}GB" if best.vram_mb else "未知"
        return f"{names}（最大显存 {vram}）", best.vendor
    except Exception as e:  # noqa: BLE001
        return f"探测失败（{type(e).__name__}）", ""


def check_gpu() -> Check:
    desc, vendor = _detect_gpus()
    if vendor == "cpu" or "失败" in desc or desc == "未知":
        return Check(
            "显卡 / 加速卡", WARN,
            f"{desc} —— 未识别到独立显卡，将只能用 CPU 推理（速度慢很多）",
            "确认显卡驱动已安装：NVIDIA 可在终端运行 nvidia-smi 验证；"
            "国产卡（摩尔线程 / 天数 / 寒武纪 / 海光）请安装对应厂商驱动与管控工具。",
            {"vendor": vendor or "unknown"},
        )
    return Check("显卡 / 加速卡", OK, desc, extra={"vendor": vendor})


def check_port(port: int) -> Check:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.6)
            busy = s.connect_ex(("127.0.0.1", port)) == 0
    except Exception as e:  # noqa: BLE001
        return Check("端口占用", SKIP, f"无法检测端口 {port}：{e}")
    if busy:
        return Check(
            f"端口占用 · {port}", WARN,
            f"端口 {port} 已被占用，sidecar 会启动失败或自动换端口",
            f"关掉占用该端口的程序，或设置环境变量换端口：\n"
            f"  Windows:  set KEVRAI_SIDECAR_PORT={port + 1}\n"
            f"  Linux/mac: export KEVRAI_SIDECAR_PORT={port + 1}",
        )
    return Check(f"端口占用 · {port}", OK, f"端口 {port} 空闲")


def _data_root() -> Path:
    try:
        root = _repo_root()
        pdir = str(_python_dir(root))
        if pdir not in sys.path:
            sys.path.insert(0, pdir)
        from app.settings import default_data_root  # type: ignore
        return Path(default_data_root())
    except Exception:  # noqa: BLE001
        base = Path.home()
        return base / ".kevrai-omni"


def check_disk(path: Path) -> Check:
    try:
        usage = shutil.disk_usage(str(path))
    except Exception as e:  # noqa: BLE001
        return Check("磁盘空间", SKIP, f"无法读取磁盘信息：{e}")
    free_gb = usage.free / (1024 ** 3)
    detail = f"{path} 剩余 {free_gb:.1f}GB / 共 {usage.total / (1024 ** 3):.1f}GB"
    if free_gb < MIN_DISK_GB:
        return Check(
            "磁盘空间", FAIL, detail,
            f"剩余空间不足 {MIN_DISK_GB}GB —— 单个大模型常在 10~60GB，"
            f"下载到一半会因空间不足失败。清理磁盘或换一个更大的盘作为数据目录。",
            {"free_gb": round(free_gb, 1)},
        )
    if free_gb < WARN_DISK_GB:
        return Check(
            "磁盘空间", WARN, detail,
            f"剩余不足 {WARN_DISK_GB}GB，下载大模型（10~60GB）容易在中途失败，"
            f"建议预留更多空间。",
            {"free_gb": round(free_gb, 1)},
        )
    return Check("磁盘空间", OK, detail, extra={"free_gb": round(free_gb, 1)})


def check_write_permission(path: Path) -> Check:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", delete=True, dir=str(path)) as fh:
            fh.write("kevrai-health-check")
            fh.flush()
        return Check("写入权限", OK, f"{path} 可读写")
    except Exception as e:  # noqa: BLE001
        return Check(
            "写入权限", FAIL,
            f"无法在 {path} 写入文件：{type(e).__name__}: {e}",
            "检查该目录权限（是否被杀毒软件 / 公司策略拦截），"
            "或设置 KEVRAI_DATA_ROOT 指向一个确定可写的目录。",
        )


def check_network() -> Check:
    """可选：模型源连通性。失败只警告 —— 离线用户本来就该能用。"""
    import urllib.request

    targets = (
        ("hf-mirror（国内镜像）", "https://hf-mirror.com"),
        ("ModelScope 魔搭", "https://www.modelscope.cn"),
    )
    results: list[str] = []
    reachable = 0
    for label, url in targets:
        try:
            req = urllib.request.Request(url, method="HEAD",
                                         headers={"User-Agent": "kevrai-health-check"})
            with urllib.request.urlopen(req, timeout=6) as r:  # noqa: S310
                ok = r.status < 500
        except Exception:
            ok = False
        results.append(f"{label}{'可达' if ok else '不可达'}")
        reachable += int(ok)

    if reachable == 0:
        return Check(
            "模型源连通性", WARN,
            "；".join(results) + " —— 将无法下载模型",
            "检查网络代理 / 防火墙；若处于离线环境可忽略，"
            "但需要提前把模型放到数据目录。",
        )
    return Check("模型源连通性", OK, "；".join(results))


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------


def render_text(checks: list[Check]) -> str:
    lines: list[str] = []
    lines.append("=" * 62)
    lines.append("  Kevrai Omni 环境体检报告")
    lines.append("=" * 62)
    lines.append("")

    ordered = sorted(checks, key=lambda c: STATUS_ORDER.get(c.status, 9))
    for c in ordered:
        lines.append(f"{ICON.get(c.status, '[??]')} {c.name}")
        if c.detail:
            lines.append(f"      {c.detail}")
        if c.fix:
            lines.append("      怎么修：")
            for ln in str(c.fix).splitlines():
                lines.append(f"        {ln}")
        lines.append("")

    n_fail = sum(1 for c in checks if c.status == FAIL)
    n_warn = sum(1 for c in checks if c.status == WARN)
    n_ok = sum(1 for c in checks if c.status == OK)
    n_skip = sum(1 for c in checks if c.status == SKIP)
    lines.append("-" * 62)
    lines.append(f"  通过 {n_ok} · 警告 {n_warn} · 失败 {n_fail} · 跳过 {n_skip}")
    if n_fail:
        lines.append("  结论：有问题需要先修，按上面「怎么修」逐条处理。")
    elif n_warn:
        lines.append("  结论：可以启动，但上面警告项会影响部分体验。")
    else:
        lines.append("  结论：环境正常，可以启动 Kevrai Omni。")
    lines.append("-" * 62)
    return "\n".join(lines)


def render_markdown(checks: list[Check]) -> str:
    lines = ["# Kevrai Omni 环境体检报告", ""]
    lines.append(f"- 生成时间：{_now()}")
    lines.append(f"- 系统：{platform.system()} {platform.release()} · {platform.machine()}")
    lines.append(f"- Python：{sys.version.split()[0]}")
    lines.append("")
    lines.append("| 状态 | 检查项 | 说明 | 修复建议 |")
    lines.append("|---|---|---|---|")
    for c in sorted(checks, key=lambda c: STATUS_ORDER.get(c.status, 9)):
        detail = c.detail.replace("\n", " ").replace("|", "/")
        fix = c.fix.replace("\n", " ").replace("|", "/")
        lines.append(f"| {c.status.upper()} | {c.name} | {detail} | {fix} |")
    lines.append("")
    n_fail = sum(1 for c in checks if c.status == FAIL)
    lines.append("## 结论")
    lines.append("")
    if n_fail:
        lines.append(f"发现 {n_fail} 项必须修复的问题，请按上表「修复建议」处理后重新体检。")
    else:
        lines.append("无阻断性问题，环境可用。")
    return "\n".join(lines)


def _now() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run_checks(port: int, with_network: bool) -> list[Check]:
    root = _repo_root()
    checks: list[Check] = [
        check_python_version(),
        check_platform(),
    ]
    data_root = _data_root()
    checks += [
        check_backend_deps(root),
        check_optional_deps(),
        check_frontend_toolchain(),
        check_frontend_deps(root),
        check_gpu(),
        check_port(port),
        check_disk(data_root),
        check_write_permission(data_root),
    ]
    if with_network:
        checks.append(check_network())
    checks.append(
        Check("数据目录", OK, str(data_root),
              extra={"path": str(data_root)})
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Kevrai Omni 一键环境体检",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"要检测的 sidecar 端口（默认 {DEFAULT_PORT}）")
    ap.add_argument("--md", metavar="PATH", help="导出 Markdown 报告到指定路径")
    ap.add_argument("--json", metavar="PATH", help="导出 JSON 报告到指定路径")
    ap.add_argument("--no-network", action="store_true", help="跳过联网检查")
    args = ap.parse_args(argv)

    checks = run_checks(args.port, with_network=not args.no_network)

    print(render_text(checks))

    if args.md:
        p = Path(args.md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_markdown(checks), encoding="utf-8")
        print(f"\nMarkdown 报告已导出：{p}")
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {"generated_at": _now(), "checks": [asdict(c) for c in checks]},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nJSON 报告已导出：{p}")

    return 1 if any(c.status == FAIL for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
