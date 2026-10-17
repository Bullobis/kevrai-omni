#!/usr/bin/env python3
"""批量核验模型市场里的地址是否真实可下载。

为什么需要这个脚本
------------------
沙箱/CI 环境常常**整体无法访问** huggingface.co 与 modelscope.cn（连确定存在的
仓库也返回 403），在那里"核验"出来的结果全是假的。这个脚本设计为**在你能正常
上网的机器上跑**——一次跑完整个 catalog，把地址分成几类，而不是笼统地报"失败"。

最关键的一点：**403 不等于地址错**。它有好几种完全不同的含义，混为一谈会把
大量正常地址误判成坏地址：

* ``404``            → 地址确实不存在（这是真正要修的）
* ``401 / 403``      → 多半是 gated 仓库（要登录/同意协议）或站点反爬，
                        **不是地址错**。脚本会单独归到 `gated` 一类。
* ``200``            → 正常
* 超时 / 连接失败     → 网络问题，单独归到 `unreachable`，不计入坏地址

用法
----
::

    python scripts/verify_catalog_urls.py                    # 核验全部
    python scripts/verify_catalog_urls.py --source modelscope # 只验魔搭
    python scripts/verify_catalog_urls.py --source huggingface
    python scripts/verify_catalog_urls.py --only-missing      # 只验还没魔搭源的条目
    python scripts/verify_catalog_urls.py --json report.json  # 导出 JSON
    python scripts/verify_catalog_urls.py --md report.md      # 导出 Markdown
    python scripts/verify_catalog_urls.py --workers 16 --timeout 15

只依赖标准库（urllib），不需要 pip install 任何东西。
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# 站点反爬很常见：不给 UA 的话，连确定存在的仓库也会 403。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

OK = "ok"                    # 200 —— 地址正常
GATED = "gated"              # 401/403 —— 多半要授权或反爬，不是地址错
MISSING = "missing"          # 404 —— 地址真的不存在，必须修
UNREACHABLE = "unreachable"  # 超时/连接失败 —— 网络问题，不算坏地址
UNKNOWN = "unknown"          # 其他状态码

#: 哪些算"真正需要修的"
BAD = (MISSING,)


def catalog_path() -> Path:
    """Locate catalog/models.json relative to the repo root."""
    here = Path(__file__).resolve().parent
    root = here.parent
    p = root / "catalog" / "models.json"
    if not p.is_file():
        raise SystemExit(f"找不到 catalog：{p}")
    return p


def classify(http_code: int | None, err: str) -> str:
    if http_code == 200:
        return OK
    if http_code == 404:
        return MISSING
    if http_code in (401, 403):
        return GATED
    if http_code is None:
        return UNREACHABLE
    return UNKNOWN


def probe(url: str, timeout: float) -> tuple[str, int | None, str, float]:
    """HEAD-probe one URL, falling back to GET when HEAD is rejected.

    Returns ``(status, http_code, error, elapsed_ms)``. Never raises.
    """
    started = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for method in ("HEAD", "GET"):
        try:
            r = urllib.request.urlopen(  # noqa: S310 — 用户显式提供的 URL
                urllib.request.Request(
                    url, headers={"User-Agent": USER_AGENT}, method=method
                ),
                timeout=timeout,
            )
            return classify(r.status, ""), r.status, "", _ms(started)
        except urllib.error.HTTPError as e:
            code = e.code
            # HEAD 被禁（405/501）时退回 GET；其它状态码直接用
            if method == "HEAD" and code in (405, 501, 400):
                continue
            return classify(code, ""), code, "", _ms(started)
        except Exception as e:  # noqa: BLE001 — 超时/DNS/SSL 都归入 unreachable
            return UNREACHABLE, None, type(e).__name__, _ms(started)
    return UNKNOWN, None, "", _ms(started)


def _ms(started: float) -> float:
    return round((time.time() - started) * 1000, 1)


def source_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "modelscope" in host:
        return "modelscope"
    if "hf-mirror" in host:
        return "hf-mirror"
    if "huggingface" in host:
        return "huggingface"
    return "other"


def collect(models: list[dict[str, Any]], only_missing: bool) -> list[tuple[str, str, str]]:
    """Build (model_id, url, role) triples to probe."""
    out: list[tuple[str, str, str]] = []
    for m in models:
        mid = m.get("id", "?")
        sources = [str(s) for s in (m.get("sources") or [])]
        primary = str(m.get("primary_url") or "")
        if primary and primary not in sources:
            sources.append(primary)

        has_ms = any(source_of(s) == "modelscope" for s in sources)
        if only_missing and has_ms:
            continue
        for s in sources:
            out.append((mid, s, "primary" if s == primary else "source"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="核验模型市场地址是否真实可下载")
    ap.add_argument("--catalog", type=Path, default=None)
    ap.add_argument("--source", choices=["modelscope", "huggingface", "hf-mirror", "other"],
                    default=None, help="只核验某一类源")
    ap.add_argument("--only-missing", action="store_true",
                    help="只检查还没有魔搭源的条目")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--md", type=Path, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    path = args.catalog or catalog_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    models = data.get("models", [])

    triples = collect(models, args.only_missing)
    if args.source:
        triples = [t for t in triples if source_of(t[1]) == args.source]

    if not triples:
        print("没有需要核验的地址。")
        return 0

    print(f"核验 {len(triples)} 个地址（并发 {args.workers}，超时 {args.timeout}s）…",
          file=sys.stderr)

    results: list[dict[str, Any]] = []
    with futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe, url, args.timeout): (mid, url, role)
                for mid, url, role in triples}
        for fut in futures.as_completed(futs):
            mid, url, role = futs[fut]
            status, code, err, ms = fut.result()
            results.append({
                "model_id": mid, "url": url, "role": role,
                "source": source_of(url), "status": status,
                "http_code": code, "error": err, "elapsed_ms": ms,
            })

    results.sort(key=lambda r: (r["model_id"], r["url"]))

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    by_model: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_model.setdefault(r["model_id"], []).append(r)

    # 真正的问题：一个模型的所有地址都不存在（或只有 404）
    broken = []
    for mid, rs in by_model.items():
        if any(r["status"] == OK for r in rs):
            continue
        if any(r["status"] == MISSING for r in rs):
            broken.append(mid)
    broken.sort()

    if not args.quiet:
        print()
        print("=" * 64)
        print("地址核验报告")
        print("=" * 64)
        for k in (OK, GATED, MISSING, UNREACHABLE, UNKNOWN):
            if counts.get(k):
                print(f"  {k:12} {counts[k]}")
        print()
        if broken:
            print(f"⚠ 需要修的模型（至少有一个地址 404，且没有任何地址可用）：{len(broken)}")
            for mid in broken[:30]:
                print(f"   - {mid}")
                for r in by_model[mid]:
                    if r["status"] == MISSING:
                        print(f"       404  {r['url']}")
        else:
            print("✓ 没有发现 404 的地址。")
        if counts.get(GATED):
            print()
            print(f"注意：{counts[GATED]} 个地址返回 401/403 —— 这通常是 gated 仓库或站点反爬，")
            print("      不是地址错误。请在浏览器里手动确认，勿据此判定地址无效。")
        if counts.get(UNREACHABLE):
            print()
            print(f"注意：{counts[UNREACHABLE]} 个地址连不上（超时/DNS）—— 是网络问题，不算坏地址。")

    if args.json:
        args.json.write_text(json.dumps({
            "generated_at": time.time(),
            "counts": counts,
            "broken_models": broken,
            "results": results,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nJSON → {args.json}")

    if args.md:
        lines = [
            "# 模型地址核验报告",
            "",
            f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "| 状态 | 数量 | 含义 |",
            "|---|---|---|",
            f"| ok | {counts.get(OK, 0)} | 地址正常 |",
            f"| gated | {counts.get(GATED, 0)} | 401/403，多半需授权或反爬，非地址错误 |",
            f"| missing | {counts.get(MISSING, 0)} | 404，地址真的不存在 |",
            f"| unreachable | {counts.get(UNREACHABLE, 0)} | 网络连不上，不算坏地址 |",
            "",
        ]
        if broken:
            lines += ["## 需要修的模型", ""]
            for mid in broken:
                lines.append(f"- `{mid}`")
                for r in by_model[mid]:
                    if r["status"] == MISSING:
                        lines.append(f"  - 404 `{r['url']}`")
        else:
            lines += ["## 结论", "", "没有发现 404 的地址。", ""]
        args.md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Markdown → {args.md}")

    # 退出码：只把"真的有 404"算失败，网络问题不算
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
