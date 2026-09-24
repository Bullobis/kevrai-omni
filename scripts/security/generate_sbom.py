#!/usr/bin/env python3
"""Generate a CycloneDX 1.5 JSON SBOM for Kevrai Omni.

Combines the pip dependency graph (from pip-audit, resolved against
python/requirements.txt) and the npm dependency graph (from package-lock.json)
into a single CycloneDX 1.5 document.  Each component carries:

  - type        "library"
  - name, version
  - purl        package URL (pkg:pypi/... or pkg:npm/...)
  - license     SPDX-ish license (queried from PyPI / read from package-lock;
                UNKNOWN when the registry does not expose one)
  - scope       "required"
  - bom-ref     derived from purl

Output defaults to stdout; pass ``--out relay/sbom-<date>.json`` to write a
file.  The generator makes best-effort PyPI metadata requests for license
text; network failures degrade to UNKNOWN rather than aborting.

Usage:
    python scripts/security/generate_sbom.py \
        --requirements python/requirements.txt \
        --package-lock package-lock.json \
        --out relay/sbom-20260924.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

# Reuse the auditors' resolvers so the SBOM and the audit report never diverge.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pip_audit  # noqa: E402
import npm_audit  # noqa: E402

USER_AGENT = "kevrai-security-audit/1.0 (+https://github.com/Bullobis/kevrai-omni)"


def _pypi_license(name: str) -> str:
    try:
        md = pip_audit.pypi_metadata(name)
        return md.get("license") or "UNKNOWN"
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def _pypi_latest(name: str) -> str | None:
    try:
        return pip_audit.pypi_metadata(name).get("latest_version")
    except Exception:  # noqa: BLE001
        return None


def build_pip_components(req_path: Path) -> list[dict[str, Any]]:
    """Resolve pip deps via pip-audit and enrich licenses from PyPI."""
    audit = pip_audit.run_pip_audit(req_path) or {}
    deps = audit.get("dependencies") or []
    # Direct requirements (for constraint notes).
    direct = {d["name"].lower().replace("_", "-") for d in pip_audit.parse_requirements(req_path)}

    out = []
    for d in deps:
        name = d["name"]
        ver = d.get("version") or ""
        norm = name.lower().replace("_", "-")
        lic = _pypi_license(name)
        out.append({
            "type": "library",
            "bom-ref": f"pkg:pypi/{norm}@{ver}",
            "name": norm,
            "version": ver,
            "licenses": [{"license": {"name": lic}}],
            "purl": f"pkg:pypi/{norm}@{ver}",
            "scope": "required",
            "properties": [
                {"name": "kevrai:direct", "value": "true" if norm in direct else "false"},
                {"name": "kevrai:ecosystem", "value": "PyPI"},
            ],
        })
    return out


def build_npm_components(lock_path: Path) -> list[dict[str, Any]]:
    if not lock_path.exists():
        return []
    lk = json.loads(lock_path.read_text(encoding="utf-8"))
    direct_names = {"electron", "electron-builder", "electron-updater", "cross-env"}
    out = []
    for k, v in (lk.get("packages") or {}).items():
        if not k.startswith("node_modules/") or k.count("/") != 1:
            continue
        name = k.removeprefix("node_modules/")
        ver = v.get("version") or ""
        lic = v.get("license") or "UNKNOWN"
        out.append({
            "type": "library",
            "bom-ref": f"pkg:npm/{name}@{ver}",
            "name": name,
            "version": ver,
            "licenses": [{"license": {"name": str(lic)}}],
            "purl": f"pkg:npm/{name}@{ver}",
            "scope": "required",
            "properties": [
                {"name": "kevrai:direct", "value": "true" if name in direct_names else "false"},
                {"name": "kevrai:ecosystem", "value": "npm"},
            ],
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requirements", default="python/requirements.txt")
    ap.add_argument("--package-lock", default="package-lock.json")
    ap.add_argument("--component-name", default="kevrai-omni")
    ap.add_argument("--component-version", default="2.9.0")
    ap.add_argument("--out", default="-")
    args = ap.parse_args()

    pip_comps = build_pip_components(Path(args.requirements))
    npm_comps = build_npm_components(Path(args.package_lock))
    components = pip_comps + npm_comps

    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "serialNumber": f"urn:uuid:{hashlib.sha1(json.dumps(components, sort_keys=True).encode()).hexdigest()[:8]}-sbom",
        "metadata": {
            "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "tools": [{"vendor": "Kevrai Cabinet", "name": "generate_sbom.py",
                       "version": "1.0.0"}],
            "component": {
                "type": "application",
                "bom-ref": f"pkg:github/Bullobis/kevrai-omni@{args.component_version}",
                "name": args.component_name,
                "version": args.component_version,
                "licenses": [{"license": {"name": "SEE LICENSE IN LICENSE"}}],
            },
        },
        "components": components,
    }
    text = json.dumps(sbom, indent=2, ensure_ascii=False)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        n_pip = len([c for c in components if "pypi" in c["purl"]])
        n_npm = len([c for c in components if "npm" in c["purl"]])
        print(f"wrote {args.out}: {len(components)} components "
              f"({n_pip} pypi, {n_npm} npm)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
