#!/usr/bin/env bash
# Version consistency hard gate.
#
# v4.17.7 shipped with python/pyproject.toml still declaring 4.17.6 while
# package.json, app.__version__ and the git tag were 4.17.7. Existing tests
# only check the User-Agent string, so that drift was invisible to CI.
# This script is called by .github/workflows/ci.yml on every push / PR.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

read_json_version() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["version"])' "$1"
}

read_toml_version() {
  python3 - "$1" <<'PY'
import re, sys
p = sys.argv[1]
try:
    import tomllib
    with open(p, "rb") as f:
        print(tomllib.load(f)["project"]["version"])
except ModuleNotFoundError:
    txt = open(p, encoding="utf-8").read()
    m = re.search(r'''^version\s*=\s*["']([^"']+)''', txt, re.M)
    if not m:
        sys.exit("cannot parse version from " + p)
    print(m.group(1))
PY
}

read_dunder_version() {
  python3 - "$1" <<'PY'
import re, sys
txt = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r'''^__version__\s*=\s*["']([^"']+)''', txt, re.M)
if not m:
    sys.exit("cannot parse __version__ from " + sys.argv[1])
print(m.group(1))
PY
}

read_site_version() {
  python3 - "$1" <<'PY'
import re, sys
txt = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r'v(\d+\.\d+\.\d+)', txt)
print(m.group(1) if m else "")
PY
}

pkg="$(read_json_version "$root/package.json")"
pyv="$(read_toml_version "$root/python/pyproject.toml")"
iniv="$(read_dunder_version "$root/python/app/__init__.py")"

echo "package.json     = $pkg"
echo "pyproject.toml   = $pyv"
echo "app/__init__.py  = $iniv"

if [ "$pkg" != "$pyv" ]; then
  echo "::error::pyproject.toml ($pyv) != package.json ($pkg)" >&2
  exit 1
fi
if [ "$pkg" != "$iniv" ]; then
  echo "::error::__version__ ($iniv) != package.json ($pkg)" >&2
  exit 1
fi

for f in website/index.html website/downloads/index.html; do
  v="$(read_site_version "$root/$f")"
  if [ -n "$v" ] && [ "$v" != "$pkg" ]; then
    echo "::error::$f advertises v$v but package.json is $pkg" >&2
    exit 1
  fi
done

notes="$root/RELEASE_NOTES_$pkg.md"
if [ ! -f "$notes" ]; then
  echo "::error::missing release notes for v$pkg: $notes" >&2
  exit 1
fi

echo "OK: all declared versions match $pkg (release notes present)"
