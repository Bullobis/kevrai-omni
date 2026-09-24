#!/usr/bin/env python3
"""pip dependency audit for Kevrai Omni.

Audits every direct requirement in ``python/requirements.txt`` and emits a
JSON report on stdout.  For each direct package it reports:

  - name             distribution name (normalized)
  - constraint       the raw version specifier from requirements.txt
  - resolved_version the version pip-audit actually selected (latest
                     satisfying the constraint; includes transitive deps)
  - latest_version   the newest version on PyPI today
  - license          SPDX-ish license string (best effort; UNKNOWN if absent)
  - vulnerabilities  list of {id, summary, severity, aliases, fixed_in}

Primary path: ``pip-audit`` (pip install pip-audit), which resolves the full
dependency graph and queries the OSV / PyPI advisory database.  If pip-audit is
not installed the script falls back to querying the OSV REST API
(https://api.osv.dev/v1/query) directly against the *latest* PyPI version, and
labels the result as "unverified-constraint" because no resolver ran.

Network failures are recorded per-package rather than aborting the whole run;
packages that could not be checked get ``vulnerabilities: null`` and a
``note`` field explaining why.

Usage:
    python scripts/security/pip_audit.py \
        --requirements python/requirements.txt \
        --out /tmp/pip-audit-report.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PYPI_JSON = "https://pypi.org/pypi/{pkg}/json"
OSV_QUERY = "https://api.osv.dev/v1/query"
USER_AGENT = "kevrai-security-audit/1.0 (+https://github.com/Bullobis/kevrai-omni)"

# Direct requirements we care about (parsed from requirements.txt).
REQUIREMENT_RE = re.compile(
    r"^\s*([A-Za-z0-9._-]+)\s*(\[[^\]]+\])?\s*(>=|<=|==|!=|~=|>|<)?\s*([^;#]*)"
)


def _http_get_json(url: str, timeout: int = 25, data: bytes | None = None) -> Any:
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def parse_requirements(path: Path) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = REQUIREMENT_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        extras = (m.group(2) or "").strip("[]")
        op = m.group(3) or ""
        ver = (m.group(4) or "").strip()
        constraint = (op + ver).strip() if op else ""
        out.append({
            "name": name,
            "extras": extras,
            "constraint": constraint,
            "raw": line,
        })
    return out


def pypi_metadata(name: str) -> dict[str, Any]:
    """Return {latest_version, license, project_url} from PyPI JSON."""
    try:
        data = _http_get_json(PYPI_JSON.format(pkg=name))
    except Exception as e:  # noqa: BLE001
        return {"latest_version": None, "license": None, "project_url": None,
                "error": f"pypi: {e}"}
    info = data.get("info", {}) or {}
    # PEP 639: modern projects set an SPDX expression in `license_expression`.
    lic = (info.get("license_expression") or info.get("license") or "").strip()
    # PyPI sometimes stuffs a full license blob into `license`; classifiers are
    # more machine-friendly.  Prefer a short SPDX-ish string.
    if not lic or len(lic) > 64 or "\n" in lic:
        classifiers = info.get("classifiers") or []
        cls = [c.split("::")[-1].strip() for c in classifiers
               if c.lower().startswith("license ::")]
        lic = cls[0] if cls else None
    if lic:
        # Normalise a couple of common forms.
        lic = lic.replace("License :: ", "").strip()
    return {
        "latest_version": info.get("version"),
        "license": lic or "UNKNOWN",
        "project_url": info.get("project_url") or info.get("home_page"),
    }


def osv_query(name: str, version: str) -> list[dict[str, Any]]:
    """Query OSV for one resolved PyPI package version."""
    body = json.dumps({
        "package": {"name": name, "ecosystem": "PyPI"},
        "version": version,
    }).encode("utf-8")
    try:
        data = _http_get_json(OSV_QUERY, data=body)
    except Exception as e:  # noqa: BLE001
        return [{"error": f"osv: {e}"}]
    vulns = data.get("vulns") or []
    out: list[dict[str, Any]] = []
    for v in vulns:
        sev = ""
        for s in v.get("severity") or []:
            if s.get("type") in ("CVSS_V3", "CVSS_V4", "CVSS_V2"):
                sev = s.get("score", "")
                break
        aliases = v.get("aliases") or []
        cves = [a for a in aliases if a.startswith("CVE-")]
        fixed_in: list[str] = []
        for aff in v.get("affected") or []:
            for r in aff.get("ranges") or []:
                for ev in r.get("events") or []:
                    if "fixed" in ev:
                        fixed_in.append(ev["fixed"])
        out.append({
            "id": v.get("id"),
            "summary": (v.get("summary") or v.get("details") or "")[:400],
            "severity": sev or "UNKNOWN",
            "cves": cves,
            "fixed_in": sorted(set(fixed_in)),
        })
    return out


def run_pip_audit(req_path: Path) -> dict[str, Any] | None:
    """Run pip-audit and return its parsed JSON, or None if unavailable."""
    if not shutil.which("pip-audit"):
        return None
    cmd = [
        "pip-audit",
        "-r", str(req_path),
        "--format", "json",
        "--progress-spinner=off",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception as e:  # noqa: BLE001
        return {"error": f"pip-audit failed to run: {e}"}
    # pip-audit prints a human line to stderr ("No known vulnerabilities
    # found") and the JSON blob to stdout.  Extract the first JSON object.
    out = proc.stdout.strip()
    start = out.find("{")
    if start < 0:
        return {"error": "pip-audit produced no JSON", "stderr": proc.stderr[-500:]}
    try:
        return json.loads(out[start:])
    except json.JSONDecodeError as e:
        return {"error": f"pip-audit JSON parse: {e}", "stdout": out[-500:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requirements", default="python/requirements.txt")
    ap.add_argument("--out", default="-", help="write JSON report here (default stdout)")
    args = ap.parse_args()

    req_path = Path(args.requirements)
    if not req_path.exists():
        print(f"requirements file not found: {req_path}", file=sys.stderr)
        return 2

    direct = parse_requirements(req_path)
    audit = run_pip_audit(req_path)
    by_name: dict[str, dict[str, Any]] = {}
    if isinstance(audit, dict) and "dependencies" in audit:
        for dep in audit["dependencies"]:
            by_name[dep["name"].lower().replace("_", "-")] = dep

    rows: list[dict[str, Any]] = []
    for d in direct:
        norm = d["name"].lower().replace("_", "-")
        py = pypi_metadata(d["name"])
        resolved = by_name.get(norm, {})
        resolved_ver = resolved.get("version") or py.get("latest_version")
        vulns: Any
        note = ""
        if resolved.get("vulns") is not None:
            # pip-audit gave us authoritative results.
            vulns = []
            for v in resolved.get("vulns") or []:
                aliases = v.get("aliases") or []
                vulns.append({
                    "id": v.get("id"),
                    "summary": v.get("description") or v.get("id"),
                    "severity": v.get("severity") or "UNKNOWN",
                    "cves": [a for a in aliases if a.startswith("CVE-")],
                    "fixed_in": v.get("fixed_versions") or [],
                })
            source = "pip-audit"
        elif resolved_ver:
            # Fallback: OSV directly against the resolved/latest version.
            osv = osv_query(d["name"], resolved_ver)
            if osv and "error" in osv[0]:
                vulns = None
                note = osv[0]["error"]
            else:
                vulns = osv
            source = "osv-direct (pip-audit unavailable)"
        else:
            vulns = None
            note = "could not resolve version"

        rows.append({
            "name": d["name"],
            "constraint": d["constraint"],
            "resolved_version": resolved_ver,
            "latest_version": py.get("latest_version"),
            "license": py.get("license") or "UNKNOWN",
            "vulnerabilities": vulns,
            "source": source,
            "note": note,
        })

    report = {
        "tool": "pip_audit.py",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "requirements_file": str(req_path),
        "pip_audit_available": shutil.which("pip-audit") is not None,
        "pip_audit_error": (audit or {}).get("error") if isinstance(audit, dict) else None,
        "direct_dependencies": rows,
        "transitive_dependency_count": len(by_name),
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out} ({len(rows)} direct deps, {len(by_name)} transitive)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
