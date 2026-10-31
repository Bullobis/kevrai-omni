#!/usr/bin/env bash
# Kevrai Omni — freeze the Python sidecar into a PyInstaller onedir bundle.
#
# PyInstaller is NOT a cross-compiler: build this on the SAME OS you are
# packaging for (run it on Windows before build_windows.sh, on Linux before
# build_linux.sh). electron-builder.yml packs `python/dist/sidecar` as
# extraResources, and electron/main.js prefers the frozen bundle over the
# source sidecar when it is present (no system Python needed at runtime).
#
# Output:
#   python/dist/sidecar/            (onedir bundle; the launcher is `sidecar`)
#
# Usage:
#   bash scripts/build_sidecar.sh
#   KEVRAI_SIDECAR_OPTIONAL=1 bash scripts/build_sidecar.sh   # warn, don't fail
#
# The script creates a dedicated venv at python/.venv-sidecar and installs the
# sidecar's runtime deps + PyInstaller there, so it never touches the system
# interpreter. Pass KEVRAI_SIDECAR_PY to override the base interpreter.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${KEVRAI_SIDECAR_PY:-$(command -v python3 || command -v python)}"
INDEX="${KEVRAI_PIP_INDEX:-https://mirrors.tencent.com/pypi/simple/}"
VENV="python/.venv-sidecar"

step() { printf "\n\033[36m==>\033[0m %s\n" "$1"; }
fail() { printf "\n\033[31m==>\033[0m %s\n" "$1" >&2; exit 1; }

[ -n "${PY}" ] || fail "no python interpreter found"
[ -f "python/sidecar.spec" ] || fail "python/sidecar.spec not found"

# ----------------------------------------------------------------------
step "0. Interpreter"
# ----------------------------------------------------------------------
echo "  python: ${PY}"
"${PY}" --version

# ----------------------------------------------------------------------
step "1. Create/build the sidecar venv"
# ----------------------------------------------------------------------
if [ ! -x "${VENV}/bin/python" ]; then
  echo "  creating venv at ${VENV}"
  "${PY}" -m venv "${VENV}"
fi
VPY="${VENV}/bin/python"
echo "  venv python: ${VPY}"
"${VPY}" -m pip install --quiet --upgrade pip \
  -i "${INDEX}" || fail "pip upgrade failed (offline? set KEVRAI_SIDECAR_OPTIONAL=1)"

# ----------------------------------------------------------------------
step "2. Install runtime deps + PyInstaller"
# ----------------------------------------------------------------------
# Install the declared runtime dependencies (fastapi/uvicorn/httpx/...) plus
# PyInstaller. The build needs to import `app` to collect its submodules, so
# the runtime deps must be present; the heavy inference stacks (torch, etc.)
# are excluded in sidecar.spec and deliberately not installed here.
"${VPY}" -m pip install --quiet \
  -e "python[media]" \
  "pyinstaller>=6.0" \
  -i "${INDEX}" --extra-index-url "https://pypi.org/simple" \
  || {
    if [ "${KEVRAI_SIDECAR_OPTIONAL:-0}" = "1" ]; then
      echo "  WARNING: could not install sidecar build deps (offline?) —" \
           "skipping frozen sidecar; the source sidecar will be used instead"
      exit 0
    fi
    fail "pip install failed"
  }

# ----------------------------------------------------------------------
step "3. Build the frozen bundle"
# ----------------------------------------------------------------------
# --clean removes stale dist/build so a stale bundle cannot be packaged.
( cd python && "${VPY}" -m PyInstaller sidecar.spec --noconfirm --clean ) \
  || fail "PyInstaller failed"

# ----------------------------------------------------------------------
step "4. Verify the bundle"
# ----------------------------------------------------------------------
if [ ! -x "python/dist/sidecar/sidecar" ]; then
  if [ "${KEVRAI_SIDECAR_OPTIONAL:-0}" = "1" ]; then
    echo "  WARNING: dist/sidecar/sidecar not found — using source sidecar"
    exit 0
  fi
  fail "frozen sidecar missing: python/dist/sidecar/sidecar"
fi
SIZE="$(du -sh python/dist/sidecar 2>/dev/null | cut -f1)"
echo "  ✓ frozen sidecar ready (${SIZE}): python/dist/sidecar"
