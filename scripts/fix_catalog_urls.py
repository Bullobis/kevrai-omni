#!/usr/bin/env python3
"""模型市场地址修正（只改已核实的内容）。

用法::

    python scripts/fix_catalog_urls.py            # 只预览，不写入
    python scripts/fix_catalog_urls.py --apply    # 真正写入（自动备份）

原则：
    * **只修正有依据的条目**。没核实的一律不动 —— 宁可留着旧地址，
      也绝不编造新地址（编错地址比地址缺失危害大得多）。
    * 每次执行都打印完整改动清单，并把修正记录写进 catalog 的
      ``url_audit`` 字段，便于后续追溯。
    * 写入前自动备份 catalog/models.json。
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog" / "models.json"

# ---------------------------------------------------------------------------
# 已核实的修正表
# ---------------------------------------------------------------------------

#: 官方仓库名写错（已在官方页面/API 核实到正确名称）
REPO_FIXES: dict[str, dict[str, str]] = {
    "hunyuanimage-3.0": {
        "old": "tencent/HunyuanImage-3.0",
        "new": "tencent/HunyuanImage-3.0-Instruct",
        "why": "官方仓库名带 -Instruct 后缀，原名称无法解析",
    },
    "pixal3d": {
        "old": "TencentARC/Pixal3D",
        "new": "TencentARC/Pixal3D-T",
        "why": "官方仓库名为 Pixal3D-T，原仓库不存在",
    },
    "apple-sharp": {
        "old": "apple/Sharp",
        "new": "apple/ml-sharp",
        "why": "官方仓库名为 apple/ml-sharp，HF 上的 apple/Sharp 是第三方镜像",
    },
}

#: 补录魔搭（ModelScope）地址。注意魔搭名与 HF 名常常不同，
#: 以下均为官方页面确认过的真实仓库。
#:
#: 格式必须严格为 ``https://modelscope.cn/models/<owner>/<name>``（不带 www）。
#: catalog 的事实字段校验（test_v292_catalog_factfields）会检查这个前缀，
#: 带 www 会被判为 malformed。
MODELSCOPE_ADD: dict[str, dict[str, str]] = {
    "minimax-music3": {
        "url": "https://modelscope.cn/models/MiniMax/MiniMax-Music3",
        "why": "魔搭名为 MiniMax/MiniMax-Music3（≠ HF 的 MiniMaxAI/MiniMax-Music3），国内免令牌可下",
    },
    "minimax-h3": {
        "url": "https://modelscope.cn/models/MiniMax/MiniMax-H3",
        "why": "魔搭官方仓库，国内免令牌可下",
    },
}

#: 官方尚未发布权重 —— 不删条目，但补一句明确的 note，
#: 避免用户（和前端）把搜索页当成下载地址。
PENDING_NOTES: dict[str, str] = {
    "wan3.0": (
        "截至 2026-10，Wan-AI 官方未发布 3.0 开源权重，本条暂无可下载地址；"
        "页面上的链接只是搜索页。可用的仍是 Wan 2.2 系列。"
    ),
    "glm-5-code": "官方尚未发布可下载权重，链接仅为搜索页，暂不可下载。",
    "happyshrimp-1.0": "官方尚未发布可下载权重，链接仅为搜索页，暂不可下载。",
    "minimax-2k-pending": "官方尚未发布可下载权重，暂无下载地址。",
}


def _bump_sources(entry: dict[str, Any], new_repo: str) -> None:
    """把条目所有源地址里的旧仓库名替换成新仓库名。"""
    old_repo = str(entry.get("repo") or "")
    sources = entry.get("sources") or []
    out: list[str] = []
    for s in sources:
        if old_repo and old_repo in s:
            out.append(s.replace(old_repo, new_repo))
        else:
            out.append(s)
    entry["sources"] = out
    if entry.get("primary_url") and old_repo in str(entry["primary_url"]):
        entry["primary_url"] = str(entry["primary_url"]).replace(old_repo, new_repo)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="修正模型市场地址（只改已核实项）")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只预览）")
    args = ap.parse_args(argv)

    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    models = data.get("models", [])
    by_id = {m.get("id"): m for m in models}

    changes: list[str] = []

    # 1) 官方仓库名修正
    for mid, fix in REPO_FIXES.items():
        m = by_id.get(mid)
        if not m:
            changes.append(f"[跳过] {mid}: catalog 中不存在")
            continue
        cur = str(m.get("repo") or "")
        if cur == fix["new"]:
            changes.append(f"[已修] {mid}: 仓库名已是 {fix['new']}")
            continue
        if cur != fix["old"]:
            changes.append(
                f"[跳过] {mid}: 当前仓库名为 {cur!r}，与预期旧值 {fix['old']!r} 不符，需人工确认"
            )
            continue
        m["repo"] = fix["new"]
        _bump_sources(m, fix["new"])
        changes.append(f"[修正] {mid}: {fix['old']} -> {fix['new']}（{fix['why']}）")

    # 2a) 先规范化已有的魔搭地址（带 www 的一律去掉，与事实字段校验保持一致）
    for m in models:
        sources = m.get("sources") or []
        fixed: list[str] = []
        for s in sources:
            s2 = str(s)
            if s2.startswith("https://www.modelscope.cn/models/"):
                s2 = "https://modelscope.cn/models/" + s2[len("https://www.modelscope.cn/models/"):]
            fixed.append(s2)
        if fixed != list(sources):
            m["sources"] = fixed
            changes.append(f"[规范化] {m.get('id')}: 魔搭地址去掉 www 前缀")

    # 2b) 补录魔搭地址
    for mid, info in MODELSCOPE_ADD.items():
        m = by_id.get(mid)
        if not m:
            changes.append(f"[跳过] {mid}: catalog 中不存在")
            continue
        sources = list(m.get("sources") or [])
        url = info["url"]
        if any(url.rstrip("/") == str(s).rstrip("/") for s in sources):
            changes.append(f"[已有] {mid}: 魔搭地址已存在")
            continue
        sources.append(url)
        m["sources"] = sources
        changes.append(f"[新增] {mid}: 补录魔搭源 {url}（{info['why']}）")

    # 3) pending 条目补 note
    for mid, note in PENDING_NOTES.items():
        m = by_id.get(mid)
        if not m:
            changes.append(f"[跳过] {mid}: catalog 中不存在")
            continue
        m["note"] = note
        changes.append(f"[标注] {mid}: 补上「暂无可下载权重」说明")

    # 打印清单
    print("=" * 66)
    print("  模型地址修正预览" if not args.apply else "  模型地址修正（已写入）")
    print("=" * 66)
    for c in changes:
        print(" ", c)
    print("-" * 66)
    n = sum(1 for c in changes if c.startswith("[修正]") or c.startswith("[新增]") or c.startswith("[标注]"))
    print(f"  共 {len(changes)} 条记录，其中实际改动 {n} 条")
    print("-" * 66)

    if not args.apply:
        print("\n这是预览。确认无误后加 --apply 写入。")
        return 0

    # 备份 + 写入
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = CATALOG.with_name(f"models.bak-{stamp}.json")
    shutil.copy2(CATALOG, backup)
    data["url_audit"] = {
        "last_fixed": datetime.now().isoformat(timespec="seconds"),
        "tool": "scripts/fix_catalog_urls.py",
        "changes": changes,
    }
    CATALOG.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n已写入 {CATALOG}")
    print(f"原文件已备份为 {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
