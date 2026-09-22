#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Catalog source hygiene + ModelScope coverage.

Reads catalog/models.json and, for every model entry:

1. *Verify* (never guess) whether the model's HF repos also exist on
   ModelScope, by querying the official ModelScope API.  Only repos that
   answer HTTP 200 get a ``https://modelscope.cn/models/<ns>/<name>/resolve/master/``
   source appended.  Repos that 404 are left untouched — a fabricated mirror
   URL would be worse than a missing one.
2. Remove the two download mirrors that were measured unreachable during the
   v2.8.1 source audit (``hf-mirror.us``, ``hf-cn-mirror.com``).  They stay in
   the user-extensible source registry (sources_registry.py) where they are
   disabled by default, but they no longer pollute every model's candidate
   list and slow down first-time speed probing.

The script is idempotent: running it again is a no-op.  It writes the file
back with the same key order and 4-space indent the catalog ships with, and
prints a summary of what changed.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import sys
import time
from pathlib import Path

import httpx

CATALOG = Path(__file__).resolve().parent.parent / "catalog" / "models.json"

MS_API = "https://modelscope.cn/api/v1/models/{repo}"
MS_SOURCE = "https://modelscope.cn/models/{repo}/resolve/master/"

DEAD_MIRRORS = (
    "https://hf-mirror.us",
    "https://hf-cn-mirror.com",
)

# Verified reachable mirror (v2.8.1 audit). Kept for reference; nothing to do
# here — hf-mirror.com entries already exist in the catalog sources.


def ms_exists(client: httpx.Client, repo: str) -> bool:
    """True when ModelScope answers 200 for the repo (i.e. it really exists)."""
    if not repo or repo.count("/") != 1:
        return False
    try:
        r = client.get(MS_API.format(repo=repo))
        return r.status_code == 200
    except Exception:  # noqa: BLE001 — network hiccup ⇒ treat as absent
        return False


def main() -> int:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    models = data["models"]
    print(f"models: {len(models)}")

    # Collect the unique repo set (model repos + gguf repos) to verify.
    repos: set[str] = set()
    for m in models:
        if m.get("repo"):
            repos.add(m["repo"])
        if m.get("gguf_repo"):
            repos.add(m["gguf_repo"])
    print(f"unique repos to verify on ModelScope: {len(repos)}")

    t0 = time.time()
    verified: set[str] = set()
    with httpx.Client(timeout=15.0, follow_redirects=True,
                      headers={"User-Agent": "kevrai-omni/2.9.0"}) as client:
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(ms_exists, client, r): r for r in sorted(repos)}
            for i, fut in enumerate(cf.as_completed(futs), 1):
                repo = futs[fut]
                if fut.result():
                    verified.add(repo)
                if i % 20 == 0 or i == len(repos):
                    print(f"  verified {i}/{len(repos)} "
                          f"({len(verified)} on ModelScope) "
                          f"[{time.time() - t0:.0f}s]")
    print(f"ModelScope-verified repos: {len(verified)}/{len(repos)}")

    # Rewrite the catalog.
    added_ms = 0
    removed_dead = 0
    for m in models:
        srcs = m.get("sources") or []
        # 1. drop measured-dead mirrors
        cleaned = [s for s in srcs if not any(s.startswith(d) for d in DEAD_MIRRORS)]
        removed_dead += len(srcs) - len(cleaned)
        # 2. append ModelScope source only for verified repos
        for repo in (m.get("repo"), m.get("gguf_repo")):
            if repo and repo in verified:
                url = MS_SOURCE.format(repo=repo)
                if url not in cleaned:
                    cleaned.append(url)
                    added_ms += 1
        if cleaned != srcs:
            m["sources"] = cleaned

    CATALOG.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"added ModelScope sources: {added_ms}")
    print(f"removed dead-mirror entries: {removed_dead}")
    print(f"catalog rewritten: {CATALOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
