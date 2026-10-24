#!/usr/bin/env python3
"""批量核验 catalog 模型的 HF 仓库地址是否仍存在（经 hf-mirror API）。

输出：stderr 进度，stdout 末尾打印死链/异常清单 JSON。
退出码恒为 0（这是诊断脚本，不是质量门）。
"""
import concurrent.futures as cf
import json
import re
import sys
import time
import urllib.request
import urllib.error

MIRROR = "https://hf-mirror.com/api/models/{}"
CATALOG = "catalog/models.json"


def hf_repo_of(url_or_name: str) -> str:
    """从 URL 或 org/name 提取 org/name。空串返回 ''。"""
    if not url_or_name:
        return ""
    s = str(url_or_name).strip()
    m = re.search(r"huggingface\.co/([^/]+/[^/?#]+)", s) or re.search(
        r"hf-mirror\.com/([^/]+/[^/?#]+)", s)
    if m:
        return m.group(1).rstrip("/")
    if "/" in s and "http" not in s:
        return s.rstrip("/")
    return ""


def check(repo: str) -> tuple[str, int]:
    url = MIRROR.format(repo)
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kevrai-verify/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r:
                return repo, r.status
        except urllib.error.HTTPError as e:
            return repo, e.code  # 404 即死链
        except Exception:
            if attempt == 1:
                return repo, 0  # 网络失败
            time.sleep(1.0)


def main() -> int:
    data = json.load(open(CATALOG, encoding="utf-8"))
    models = data.get("models", [])
    targets = {}  # repo -> list of model ids
    for m in models:
        for key in ("repo", "gguf_repo", "mnn_repo"):
            r = hf_repo_of(m.get(key, ""))
            if r:
                targets.setdefault(r, set()).add(m["id"])

    repos = sorted(targets)
    print(f"待核验仓库数: {len(repos)}（来自 {len(models)} 个模型）", file=sys.stderr)

    dead, netfail, ok = [], [], 0
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for i, (repo, code) in enumerate(ex.map(check, repos), 1):
            if code == 200:
                ok += 1
            elif code == 404:
                dead.append({"repo": repo, "used_by": sorted(targets[repo])})
            else:
                netfail.append({"repo": repo, "code": code, "used_by": sorted(targets[repo])})
            if i % 20 == 0:
                print(f"  进度 {i}/{len(repos)}", file=sys.stderr)

    report = {
        "total_repos": len(repos),
        "ok": ok,
        "dead_404": dead,
        "network_fail": netfail,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
