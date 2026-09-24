#!/usr/bin/env python3
"""Print a one-line-per-package summary of a pip_audit.py / npm_audit.py JSON
report.  Used by CI to surface findings in the job log without embedding
inline Python in the workflow YAML."""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    data = json.load(open(args.report, encoding="utf-8"))
    rows = data.get("direct_dependencies") or []
    total = 0
    print(f"== {data.get('tool', 'audit')} ==")
    for d in rows:
        v = d.get("vulnerabilities") or []
        n = len(v)
        total += n
        ver = d.get("resolved_version") or d.get("locked_version") or "?"
        line = f"  {d['name']:20s} {str(ver):14s} advisories={n}"
        if d.get("latest_version") and str(ver) != str(d.get("latest_version")):
            line += f"  (latest: {d['latest_version']})"
        print(line)
        for adv in v[:5]:
            cves = ",".join(adv.get("cves") or []) or "-"
            print(f"      - {adv.get('id')} [{adv.get('severity')}] {cves}: "
                  f"{(adv.get('summary') or '')[:90]}")
    print(f"total advisories on direct deps: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
