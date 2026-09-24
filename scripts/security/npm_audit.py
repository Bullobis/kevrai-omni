#!/usr/bin/env python3
"""npm dependency audit for Kevrai Omni.

Audits the four direct npm dependencies declared in ``package.json``
(electron, electron-builder, electron-updater, cross-env) and emits a JSON
report.  For each it reports:

  - name             package name
  - constraint       version range from package.json
  - locked_version   exact version resolved by package-lock.json (if present)
  - latest_version   newest version on the npm registry today
  - license          license from package-lock / registry metadata
  - vulnerabilities  OSV advisories (npm ecosystem) affecting locked_version

Primary path: ``npm audit --json`` when a node_modules tree is available.
Otherwise (and for license/latest metadata that ``npm audit`` does not
provide) we query the npm registry (``https://registry.npmjs.org/<pkg>``) and
the OSV REST API (``https://api.osv.dev/v1/query``, ecosystem=npm) directly.

Usage:
    python scripts/security/npm_audit.py \
        --package-json package.json \
        --out /tmp/npm-audit-report.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

NPM_REGISTRY = "https://registry.npmjs.org/{pkg}"
OSV_QUERY = "https://api.osv.dev/v1/query"
USER_AGENT = "kevrai-security-audit/1.0 (+https://github.com/Bullobis/kevrai-omni)"

DIRECT = ["electron", "electron-builder", "electron-updater", "cross-env"]


def _http(url: str, data: bytes | None = None, timeout: int = 25) -> Any:
    req = urllib.request.Request(
        url, data=data,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def npm_registry(name: str) -> dict[str, Any]:
    try:
        d = _http(NPM_REGISTRY.format(pkg=name))
    except Exception as e:  # noqa: BLE001
        return {"latest_version": None, "license": None, "error": f"npm: {e}"}
    dist_tags = d.get("dist-tags") or {}
    latest = dist_tags.get("latest")
    latest_meta = (d.get("versions") or {}).get(latest, {})
    lic = (latest_meta.get("license") or d.get("license") or "UNKNOWN")
    return {"latest_version": latest, "license": lic or "UNKNOWN"}


def osv_npm(name: str, version: str) -> list[dict[str, Any]] | None:
    body = json.dumps({
        "package": {"name": name, "ecosystem": "npm"},
        "version": version,
    }).encode("utf-8")
    try:
        d = _http(OSV_QUERY, data=body)
    except Exception as e:  # noqa: BLE001
        return [{"error": f"osv: {e}"}]
    vulns = d.get("vulns") or []
    out = []
    for v in vulns:
        sev = ""
        for s in v.get("severity") or []:
            if s.get("type") in ("CVSS_V3", "CVSS_V4", "CVSS_V2"):
                sev = s.get("score", ""); break
        aliases = v.get("aliases") or []
        out.append({
            "id": v.get("id"),
            "summary": (v.get("summary") or v.get("details") or "")[:400],
            "severity": sev or "UNKNOWN",
            "cves": [a for a in aliases if a.startswith("CVE-")],
            "fixed_in": [ev["fixed"]
                         for aff in v.get("affected") or []
                         for rng in aff.get("ranges") or []
                         for ev in rng.get("events") or [] if "fixed" in ev],
        })
    return out


def run_npm_audit() -> dict[str, Any] | None:
    if not shutil.which("npm"):
        return None
    try:
        proc = subprocess.run(
            ["npm", "audit", "--json", "--omit=dev"],
            capture_output=True, text=True, timeout=300,
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"npm audit failed: {e}"}
    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else {"error": "empty stdout"}
    except json.JSONDecodeError as e:
        return {"error": f"npm audit JSON: {e}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package-json", default="package.json")
    ap.add_argument("--lock", default="package-lock.json")
    ap.add_argument("--out", default="-")
    args = ap.parse_args()

    pkg = json.loads(Path(args.package_json).read_text(encoding="utf-8"))
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}

    lock: dict[str, Any] = {}
    lock_path = Path(args.lock)
    if lock_path.exists():
        lk = json.loads(lock_path.read_text(encoding="utf-8"))
        for k, v in (lk.get("packages") or {}).items():
            if k.startswith("node_modules/") and k.count("/") == 1:
                lock[k.removeprefix("node_modules/")] = v

    npm_audit = run_npm_audit()

    rows = []
    for name in DIRECT:
        constraint = deps.get(name, "")
        locked = lock.get(name, {})
        locked_ver = locked.get("version")
        locked_lic = locked.get("license")
        reg = npm_registry(name)
        latest = reg.get("latest_version")
        lic = locked_lic or reg.get("license") or "UNKNOWN"

        vulns: Any = None
        source = "osv-direct"
        if locked_ver:
            osv = osv_npm(name, locked_ver)
            if osv and "error" in osv[0]:
                vulns = None
                note = osv[0]["error"]
            else:
                vulns = osv
                note = ""
        else:
            note = "no locked version; run npm install"

        rows.append({
            "name": name,
            "constraint": constraint,
            "locked_version": locked_ver,
            "latest_version": latest,
            "license": lic,
            "vulnerabilities": vulns,
            "source": source,
            "note": note,
        })

    report = {
        "tool": "npm_audit.py",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "package_json": args.package_json,
        "npm_audit_available": shutil.which("npm") is not None,
        "npm_audit_summary": (npm_audit or {}).get("metadata", {}).get("vulnerabilities")
                             if isinstance(npm_audit, dict) else None,
        "direct_dependencies": rows,
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out} ({len(rows)} direct npm deps)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
