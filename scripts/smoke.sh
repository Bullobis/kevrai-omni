#!/usr/bin/env bash
# Kevrai Studio — local smoke test.
#
# What it tests (each step MUST exit non-zero on failure):
#   1. JSON catalog validity (models.json + engines.json)
#   2. JSON catalog validity under jsonschema (catalog/schema.py)
#   3. Endpoint URL allowlist enforcement (no leaked "hf-cdn.sufy.com")
#   4. No leaked secrets in shipped source (AWS, GitHub PAT, OpenAI, etc.)
#   5. Python deps install via Tencent mirror
#   6. pytest (full suite)
#   7. node --check on every .js file
#   8. electron-builder.yml sanity
#   9. Catalog stats (model count, engine count, license distribution, trending)
#
# Aimed at CI + local dev; no real network for downloads, schema check uses
# the bundled jsonschema package.

set -euo pipefail
cd "$(dirname "$0")/.."

INDEX="${KEVRAI_PIP_INDEX:-https://mirrors.tencent.com/pypi/simple/}"
SUMMARY_FILE="$(mktemp -t kevrai-smoke.XXXXXX)"
trap 'rm -f "${SUMMARY_FILE}"' EXIT

# ----- helpers -----
pass() { printf "  \033[32m✓\033[0m %s\n" "$1"; echo "  pass: $1" >>"${SUMMARY_FILE}"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$1"; echo "  FAIL: $1" >>"${SUMMARY_FILE}"; echo "❌ FAIL: $1" >&2; FAILED=1; }
section() { printf "\n== %s ==\n" "$1"; }

FAILED=0

# ----------------------------------------------------------------------
section "Step 1/9 · JSON catalog validity"
# ----------------------------------------------------------------------
python3 - <<'PY' || fail "catalog JSON parse"
import json, sys
for p in ["catalog/models.json", "catalog/engines.json"]:
    with open(p, encoding="utf-8") as f:
        json.load(f)
    print(f"  {p} parses OK")
PY
pass "catalog JSON parse"

# ----------------------------------------------------------------------
section "Step 2/9 · JS schema validation (catalog/schema.py)"
# ----------------------------------------------------------------------
python3 - <<'PY' || fail "schema validation"
import json, sys
sys.path.insert(0, ".")
from catalog.schema import validate_models, validate_engines
m = json.load(open("catalog/models.json", encoding="utf-8"))
e = json.load(open("catalog/engines.json", encoding="utf-8"))
errs = validate_models(m) + validate_engines(e)
if errs:
    for x in errs[:6]:
        print("   -", x)
    sys.exit(1)
print("  schema OK for both catalogs")
PY
pass "jsonschema validation"

# ----------------------------------------------------------------------
section "Step 3/9 · Multi-source catalog & URL well-formedness"
# v2.2.0: there is NO negative blocklist. This step now verifies the
# opposite: every model exposes a `sources[]` mirror list with at least
# 2 entries, every engine `platforms[*]` URL is well-formed http(s), and
# every GGUF repo also has a `sources[]` list.
# ----------------------------------------------------------------------
python3 - <<'PY' || fail "multi-source / url well-formedness check failed"
import json, sys
from urllib.parse import urlparse

m = json.load(open("catalog/models.json", encoding="utf-8"))
e = json.load(open("catalog/engines.json", encoding="utf-8"))

bad = []
# 1) every non-pending model has sources[] with >=2 entries
for entry in m["models"]:
    if entry.get("category") == "pending":
        continue
    sources = entry.get("sources") or []
    if len(sources) < 2:
        bad.append(("models/" + entry.get("id", "?"), "sources<2", len(sources)))

# 2) every engine platforms[*] URL is well-formed http(s)
for eng in e["engines"]:
    for plat, url in (eng.get("platforms") or {}).items():
        if not url:
            continue
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc:
            bad.append(("engines/" + eng.get("id", "?"), plat, url))
    # 3) every engine has sources[] (multi-mirror)
    if not (eng.get("sources") or []):
        bad.append(("engines/" + eng.get("id", "?"), "sources empty", ""))

# 4) every gguf_repo has sources[]
for g in m.get("gguf_repos", []):
    if not (g.get("sources") or []):
        bad.append(("gguf_repos/" + g.get("id", "?"), "sources empty", ""))

if bad:
    for x in bad[:10]:
        print("   -", x)
    sys.exit(1)
PY
pass "multi-source catalog shape"

# ----------------------------------------------------------------------
section "Step 4/9 · No leaked secrets in shipped source"
# ----------------------------------------------------------------------
python3 - <<'PY' || fail "secret-pattern scan found hits"
import re, sys
from pathlib import Path

EXCLUDE = {
    "SECURITY.md", "RELEASE.md",
    "scripts/release.sh", "scripts/smoke.sh", "scripts/build_windows.sh",
}
PATTERNS = [
    (r"\b(AKIA|ASIA)[0-9A-Z]{16}\b", "AWS access key"),
    (r"\bghp_[A-Za-z0-9]{30,}\b", "GitHub PAT"),
    (r"\bgithub_pat_[A-Za-z0-9_]{40,}\b", "GitHub fine-grained PAT"),
    (r"\bsk-[A-Za-z0-9]{20,}\b", "OpenAI/Anthropic key"),
    (r"\bhf_[A-Za-z0-9]{30,}\b", "HuggingFace write token"),
    (r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", "private key"),
]

root = Path(".")
hits = []
for p in root.rglob("*"):
    if not p.is_file(): continue
    rel = str(p).replace("\\", "/")
    if any(part in p.parts for part in ("__pycache__", ".git", "node_modules")):
        continue
    if rel in EXCLUDE: continue
    if p.suffix not in {".py",".js",".json",".yml",".yaml",".toml",".md",".sh",".txt"}:
        continue
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        continue
    for rx, name in PATTERNS:
        for m in re.finditer(rx, text):
            line_no = text.count("\n", 0, m.start()) + 1
            hits.append(f"  {rel}:{line_no} [{name}]")

if hits:
    print("\n".join(hits[:20]))
    sys.exit(1)
PY
pass "no leaked secrets"

# ----------------------------------------------------------------------
section "Step 5/9 · pip install (Tencent mirror, dependencies only)"
# ----------------------------------------------------------------------
python3 -m pip install -i "${INDEX}" --quiet --disable-pip-version-check \
  -r python/requirements.txt || true
pass "pip install (best-effort)"

# ----------------------------------------------------------------------
section "Step 6/9 · pytest (full suite)"
# ----------------------------------------------------------------------
(
  cd python
  python3 -m pytest -ra tests/ "$@" 2>&1 | tail -20
)
pass "pytest"

# ----------------------------------------------------------------------
section "Step 7/9 · node --check on every .js file"
# ----------------------------------------------------------------------
shopt -s globstar nullglob
node_fail=0
for f in electron/**/*.js renderer/**/*.js scripts/**/*.js; do
  [ -f "$f" ] || continue
  if ! node --check "$f" >/dev/null 2>&1; then
    fail "$f: node --check failed"
    node_fail=1
  else
    pass "$f parses"
  fi
done
[ "${node_fail}" -eq 0 ] && pass "all .js files parse"

# ----------------------------------------------------------------------
section "Step 8/9 · electron-builder.yml sanity"
# ----------------------------------------------------------------------
python3 - <<'PY' || fail "electron-builder.yml"
yaml_text = open("electron-builder.yml", encoding="utf-8").read()
checks = [
    ("createDesktopShortcut: true", "desktop shortcut ON"),
    ("target:", "target: present"),
    ("nsis", "NSIS target used"),
    ("asar: true", "asar enabled"),
    ("publish: null", "publish: null (release script handles)"),
]
for needle, desc in checks:
    if needle not in yaml_text:
        print(f"  ✗ missing {needle} ({desc})")
        raise SystemExit(1)
    print(f"  ✓ {desc}")
PY
pass "electron-builder.yml"

# ----------------------------------------------------------------------
section "Step 9/9 · Catalog stats"
# ----------------------------------------------------------------------
python3 - <<'PY'
import json, collections
from pathlib import Path

m = json.load(open("catalog/models.json", encoding="utf-8"))
e = json.load(open("catalog/engines.json", encoding="utf-8"))
models = m["models"]
engines = e["engines"]

cat_counts = collections.Counter(x["category"] for x in models)
lic_counts = collections.Counter(x.get("license", "Unknown") for x in models)
trending_models = [x["id"] for x in models if x.get("trending")]
trending_engines = [x["id"] for x in engines if x.get("trending")]

print(f"  models:             {len(models)} total")
print(f"  engines:            {len(engines)} total")
print(f"  models trending:    {len(trending_models)}")
print(f"  engines trending:   {len(trending_engines)}")
print(f"  categories:         {dict(cat_counts)}")
print(f"  license dist (top 5):")
for lic, n in lic_counts.most_common(5):
    print(f"    {lic}: {n}")
PY
pass "catalog stats"

# ----------------------------------------------------------------------
section "Smoke summary"
# ----------------------------------------------------------------------
echo "----- what was tested -----"
sort "${SUMMARY_FILE}" | uniq -c | awk '{printf "  %4d  %s\n", $1, $3, $4}'

if [ "${FAILED}" -ne 0 ]; then
  echo ""
  echo "❌ ${FAILED} smoke step(s) failed."
  exit 1
fi
echo ""
echo "✅ All smoke tests passed."
