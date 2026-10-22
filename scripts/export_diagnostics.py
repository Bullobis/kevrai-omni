#!/usr/bin/env python3
"""导出诊断包：把排障需要的信息一次性打包，脱敏后可安全发给开发者。

开源项目的 issue 质量直接取决于用户能提供多少有效信息。以前排障靠 FAQ
里一条条对照，现在一键导出。

设计约束：
* 只依赖标准库 —— 依赖全没装的机器上也要能跑起来说清缺什么。
* 默认脱敏：令牌、密钥、用户名路径都会被打码。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
import time
import zipfile
from pathlib import Path

SCHEMA_VERSION = "1.0"

# 需要打码的键名（不区分大小写）。命中就替换值，不猜内容。
SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|authorization|bearer|"
    r"access[_-]?key|private[_-]?key|credential|cookie|session)",
    re.IGNORECASE,
)
# 看起来像令牌的值（即使键名没命中也打码）。
SENSITIVE_VALUE_RE = re.compile(
    r"\b(?:ghp|gho|ghu|ghs|github_pat|hf)_[A-Za-z0-9_]{16,}\b"
    r"|\bsk-[A-Za-z0-9]{16,}\b"
    r"|\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}\b"
)
REDACTED = "[REDACTED]"


def redact(obj, _depth: int = 0):
    """递归脱敏。深度上限防止循环引用把导出卡死。"""
    if _depth > 12:
        return "<max depth>"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and SENSITIVE_KEY_RE.search(k):
                out[k] = REDACTED
            else:
                out[k] = redact(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v, _depth + 1) for v in obj]
    if isinstance(obj, str):
        if SENSITIVE_VALUE_RE.search(obj):
            return SENSITIVE_VALUE_RE.sub(REDACTED, obj)
        return mask_home(obj)
    return obj


_HOME = str(Path.home())


def mask_home(text: str) -> str:
    """把用户名路径打码。路径里含用户名本身就是隐私。"""
    if _HOME and _HOME in text:
        return text.replace(_HOME, "~")
    return text


def collect_environment() -> dict:
    return {
        "python": sys.version.split()[0],
        "executable": mask_home(sys.executable),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "os": f"{platform.system()} {platform.release()}",
        "cpu_count": os.cpu_count(),
        "cwd": mask_home(os.getcwd()),
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "locale": os.environ.get("LANG") or os.environ.get("LC_ALL"),
        # 只记名字不记值 —— 值可能是密钥。
        "proxy_env_present": [
            k for k in os.environ if "proxy" in k.lower() or k.upper().endswith("_PROXY")
        ],
    }


def collect_dependencies() -> dict:
    """逐项探测可选依赖。缺失不是错误，是要报告的事实。"""
    probes = {
        "httpx": "httpx",
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "pydantic": "pydantic",
        "numpy": "numpy",
        "torch": "torch",
        "psutil": "psutil",
    }
    out = {}
    for name, mod in probes.items():
        try:
            m = __import__(mod)
            out[name] = getattr(m, "__version__", "present")
        except Exception as e:  # noqa: BLE001
            out[name] = f"missing ({type(e).__name__})"
    return out


def collect_gpu() -> dict:
    """尽力探测显卡。探测不到就如实说探测不到，绝不猜。"""
    info: dict = {"detected": [], "note": None}
    try:
        import subprocess

        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            for line in r.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                info["detected"].append(
                    {
                        "vendor": "nvidia",
                        "name": parts[0] if parts else None,
                        "memory_mb": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None,
                        "driver": parts[2] if len(parts) > 2 else None,
                    }
                )
            return info
    except Exception:  # noqa: BLE001
        pass
    info["note"] = (
        "未能通过 nvidia-smi 探测到 NVIDIA 显卡；"
        "AMD/Intel/Apple 与其他加速卡需要应用内的硬件检测模块结果（见 app/accelerators.py）"
    )
    return info


def collect_disk(paths: list[str]) -> dict:
    out = {}
    for p in paths:
        try:
            st = os.statvfs(p)
            out[mask_home(p)] = {
                "free_bytes": st.f_bavail * st.f_frsize,
                "total_bytes": st.f_blocks * st.f_frsize,
            }
        except OSError as e:
            out[mask_home(p)] = {"error": f"{type(e).__name__}: {e}"}
    return out


def collect_recent_logs(log_dir: str | None, max_bytes: int = 256 * 1024) -> str:
    """读取最近的日志尾部。限制大小，避免把 GB 级日志塞进诊断包。"""
    if not log_dir:
        return "(未提供日志目录)"
    d = Path(log_dir)
    if not d.is_dir():
        return f"(日志目录不存在: {mask_home(str(d))})"
    files = []
    try:
        files = sorted(
            [f for f in d.iterdir() if f.is_file() and f.suffix in {".log", ".txt"}],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )[:5]
    except OSError as e:
        return f"(无法列出日志: {type(e).__name__}: {e})"
    if not files:
        return "(无日志文件)"
    chunks = []
    for f in files:
        try:
            data = f.read_bytes()
        except OSError as e:
            chunks.append(f"=== {f.name} ===\n(读取失败: {e})\n")
            continue
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        chunks.append(
            f"=== {f.name} (tail {len(data)} bytes) ===\n"
            + redact(data.decode("utf-8", errors="replace"))
        )
    return "\n".join(chunks)


def collect_orphan_partials(model_dir: str | None) -> dict:
    """列出上次进程遗留的半截文件 —— 排障时"下不动"的高频原因。"""
    if not model_dir:
        return {"scanned": False, "reason": "未提供模型目录"}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
        from downloader import Downloader  # type: ignore

        found = Downloader.scan_orphan_partials(model_dir)
        return {
            "scanned": True,
            "count": len(found),
            "items": [
                {
                    "path": mask_home(str(o.partial)),
                    "size": o.size,
                    "age_s": round(o.age_s, 1),
                    "stale": o.stale,
                }
                for o in found[:50]
            ],
        }
    except Exception as e:  # noqa: BLE001
        return {"scanned": False, "reason": f"{type(e).__name__}: {e}"}


def build_report(args) -> dict:
    return redact(
        {
            "schema": SCHEMA_VERSION,
            "generated_by": "scripts/export_diagnostics.py",
            "environment": collect_environment(),
            "dependencies": collect_dependencies(),
            "gpu": collect_gpu(),
            "disk": collect_disk(args.disk_paths or [os.getcwd()]),
            "orphan_partials": collect_orphan_partials(args.model_dir),
            "user_notes": args.note or "",
        }
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="导出 Kevrai Omni 诊断包")
    ap.add_argument("--out", default="kevrai-diagnostics.zip", help="输出 zip 路径")
    ap.add_argument("--model-dir", default=None, help="模型目录（扫描半截文件）")
    ap.add_argument("--log-dir", default=None, help="日志目录")
    ap.add_argument(
        "--disk-paths", nargs="*", default=None, help="要检查剩余空间的路径"
    )
    ap.add_argument("--note", default=None, help="用户补充描述（会一并脱敏）")
    ap.add_argument(
        "--no-redact",
        action="store_true",
        help="关闭脱敏（仅在完全信任接收方时使用）",
    )
    args = ap.parse_args()

    report = build_report(args)
    logs = collect_recent_logs(args.log_dir)
    if not args.no_redact:
        logs = redact(logs)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "diagnostics.json",
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        )
        z.writestr("logs.txt", logs)
        z.writestr(
            "README.txt",
            "Kevrai Omni 诊断包\n"
            f"schema {SCHEMA_VERSION}\n\n"
            "包含：diagnostics.json（环境/依赖/显卡/磁盘/半截文件）、logs.txt（最近日志尾部）。\n"
            "内容已做脱敏处理；如需人工确认，请打开 diagnostics.json 检查后再发送。\n",
        )

    digest = hashlib.sha256(out.read_bytes()).hexdigest()[:16]
    print(f"✅ 诊断包已导出: {out} ({out.stat().st_size} bytes, sha256:{digest})")
    if not args.no_redact:
        print("   已脱敏（令牌/密钥/用户名路径已打码）")
    else:
        print("   ⚠️ 未脱敏 —— 发送前请自行检查是否含敏感信息")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
