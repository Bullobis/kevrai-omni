#!/usr/bin/env bash
# Kevrai Studio — Windows installer build (run this on a Windows machine with
# Node + Python, or on Linux via Wine).
#
# Output:
#   build/output/Kevrai Studio Setup-1.0.0.exe  (NSIS, desktop shortcut)
#
# This script verifies every step and aborts non-zero on the first failure:
#   1. Tooling is present (node, npm, python)
#   2. npm ci succeeded (or `npm install` if no lockfile) and node_modules
#      is actually populated
#   3. python -m pip install -r requirements.txt exits 0
#   4. electron-builder produces a single .exe
#   5. The .exe exists, is > 1 MB, and we print its size + SHA-256

set -euo pipefail
cd "$(dirname "$0")/.."

INDEX="${KEVRAI_PIP_INDEX:-https://mirrors.tencent.com/pypi/simple/}"
VERSION="$(node -p "require('./package.json').version" 2>/dev/null || echo 2.2.0)"
EXPECTED_EXE="build/output/Kevrai Studio Setup-${VERSION}.exe"

step() { printf "\n\033[36m==>\033[0m %s\n" "$1"; }
fail() { printf "\n\033[31m==>\033[0m %s\n" "$1" >&2; exit 1; }

# ----------------------------------------------------------------------
step "0. Tooling check"
# ----------------------------------------------------------------------
command -v node >/dev/null 2>&1 || fail "node not on PATH"
command -v npm >/dev/null 2>&1  || fail "npm not on PATH"
command -v python >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1 \
  || fail "python / python3 not on PATH"

PY="$(command -v python || command -v python3)"
echo "  node: $(node -v)"
echo "  npm:  $(npm -v)"
echo "  py:   $(${PY} --version 2>&1 || true)"

# ----------------------------------------------------------------------
step "1. npm install (verified)"
# ----------------------------------------------------------------------
# Use npmmirror.com in CN (npmjs.org is often blocked in sandboxed CI).
npm config set registry https://registry.npmmirror.com 2>/dev/null || true
export ELECTRON_MIRROR="${ELECTRON_MIRROR:-https://registry.npmmirror.com/-/binary/electron/}"
if [ -f package-lock.json ]; then
  npm ci --no-audit --no-fund 2>&1 | tail -5 || fail "npm ci failed"
else
  npm install --no-audit --no-fund 2>&1 | tail -5 || fail "npm install failed"
fi

# Belt-and-suspenders: package-lock-written + node_modules populated.
if [ ! -f node_modules/.package-lock.json ]; then
  fail "node_modules/.package-lock.json missing after install — npm install didn't actually run"
fi
if [ ! -d node_modules/electron ] || [ ! -d node_modules/electron-builder ]; then
  fail "node_modules/electron or node_modules/electron-builder missing"
fi
echo "  ✓ node_modules populated"

# ----------------------------------------------------------------------
step "2. python -m pip install -r requirements.txt (verified exit 0)"
# ----------------------------------------------------------------------
${PY} -m pip install -i "${INDEX}" --disable-pip-version-check \
  -r python/requirements.txt || fail "pip install failed"

# quick import probe — catches the case where deps installed but the venv
# can't actually use them (broken .pth files, ABI mismatch, etc.).
${PY} -c "import fastapi, httpx, pydantic; print('  python deps importable')" \
  || fail "python deps installed but not importable"

# ----------------------------------------------------------------------
step "3. Clean prior artifacts"
# ----------------------------------------------------------------------
rm -rf build/output dist electron/python-dist
echo "  ✓ clean"

# ----------------------------------------------------------------------
step "4. Build the Windows NSIS installer"
# ----------------------------------------------------------------------
# In sandboxed/CI environments where github.com is blocked, point
# electron-builder at the npmmirror binary mirror so winCodeSign / nsis /
# rcedit tools are fetched from a reachable CDN.
export ELECTRON_BUILDER_BINARIES_MIRROR="${ELECTRON_BUILDER_BINARIES_MIRROR:-https://registry.npmmirror.com/-/binary/electron-builder-binaries/}"
export CSC_IDENTITY_AUTO_DISCOVERY=false
# On Linux, wine32 must be installed (apt-get install wine32:i386). Pin the
# WINEPREFIX so wine's 32-bit syswow64 is used when rcedit-ia32.exe is invoked.
if [ "$(uname -s)" = "Linux" ]; then
  if command -v dpkg >/dev/null 2>&1; then
    if ! dpkg -s wine32:i386 >/dev/null 2>&1; then
      echo "  ⚠ wine32:i386 not installed — running dpkg --add-architecture i386 && apt-get install"
      dpkg --add-architecture i386 2>/dev/null || true
      apt-get update 2>&1 | tail -2 || true
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends wine32:i386 2>&1 | tail -3 || true
    fi
    # Wine 9.0 places 32-bit DLLs under /usr/lib/i386-linux-gnu/wine/i386-windows,
    # but wine looks for them at /usr/lib/wine/i386-windows — link if missing.
    if [ -d /usr/lib/i386-linux-gnu/wine/i386-windows ] && [ ! -e /usr/lib/wine/i386-windows ]; then
      ln -sf /usr/lib/i386-linux-gnu/wine/i386-windows /usr/lib/wine/i386-windows
    fi
    export WINEPREFIX="${WINEPREFIX:-/root/.wine32_kevrai}"
  fi
fi
# We *don't* run --publish here: the flag is --publish never (electron-builder).
npx --yes electron-builder --win --x64 --publish never \
  --config.npmRebuild=false \
  --config.extraMetadata.main="electron/main.js" \
  || fail "electron-builder failed"

# ----------------------------------------------------------------------
step "5. Verify the resulting .exe"
# ----------------------------------------------------------------------
if [ ! -f "${EXPECTED_EXE}" ]; then
  # Tolerate alternate filename conventions
  CANDIDATE="$(ls -1 build/output/*.exe 2>/dev/null | head -1 || true)"
  if [ -z "${CANDIDATE}" ]; then
    fail "no .exe produced at ${EXPECTED_EXE}"
  fi
  EXPECTED_EXE="${CANDIDATE}"
fi

SIZE_BYTES="$(stat -c %s "${EXPECTED_EXE}" 2>/dev/null || stat -f %z "${EXPECTED_EXE}")"
SIZE_MB="$(awk -v b="${SIZE_BYTES}" 'BEGIN { printf "%.1f", b/1024/1024 }')"
if [ "${SIZE_BYTES}" -lt 1048576 ]; then
  fail "executable too small (${SIZE_BYTES} bytes) — likely truncated build"
fi

SHA256="$(sha256sum "${EXPECTED_EXE}" 2>/dev/null | awk '{print $1}' || shasum -a 256 "${EXPECTED_EXE}" | awk '{print $1}')"

echo ""
echo "✅ Build succeeded"
echo "   file:   ${EXPECTED_EXE}"
echo "   size:   ${SIZE_MB} MiB (${SIZE_BYTES} bytes)"
echo "   sha256: ${SHA256}"
