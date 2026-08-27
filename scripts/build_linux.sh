#!/usr/bin/env bash
# Kevrai Studio — Linux installers (AppImage + .deb). Run on Linux.
#
# Output:
#   build/output/Kevrai Studio-2.2.0-x86_64.AppImage
#   build/output/Kevrai Studio-2.2.0-amd64.deb
#
# Requires: node, npm, dpkg-deb (for verifying .deb), squashfs (for AppImage).

set -euo pipefail
cd "$(dirname "$0")/.."

INDEX="${KEVRAI_PIP_INDEX:-https://mirrors.tencent.com/pypi/simple/}"
VERSION="$(node -p "require('./package.json').version" 2>/dev/null || echo 2.2.0)"
APPIMAGE="build/output/Kevrai Studio-${VERSION}-x86_64.AppImage"
DEB="build/output/Kevrai Studio-${VERSION}-amd64.deb"

step() { printf "\n\033[36m==>\033[0m %s\n" "$1"; }
fail() { printf "\n\033[31m==>\033[0m %s\n" "$1" >&2; exit 1; }

step "0. Tooling check"
command -v node >/dev/null 2>&1 || fail "node not on PATH"
command -v npm >/dev/null 2>&1  || fail "npm not on PATH"
command -v python >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1 \
  || fail "python / python3 not on PATH"
echo "  node: $(node -v)  npm: $(npm -v)"

step "1. npm install (verified)"
npm config set registry https://registry.npmmirror.com 2>/dev/null || true
export ELECTRON_MIRROR="${ELECTRON_MIRROR:-https://registry.npmmirror.com/-/binary/electron/}"
if [ -f package-lock.json ]; then
  npm ci --no-audit --no-fund 2>&1 | tail -5 || fail "npm ci failed"
else
  npm install --no-audit --no-fund 2>&1 | tail -5 || fail "npm install failed"
fi
[ -d node_modules/electron ] || fail "node_modules/electron missing"

step "2. python -m pip install -r requirements.txt"
PY="$(command -v python || command -v python3)"
${PY} -m pip install -i "${INDEX}" --disable-pip-version-check \
  -r python/requirements.txt || fail "pip install failed"

step "3. Clean prior artifacts"
rm -rf build/output dist

step "4. electron-builder --linux AppImage deb"
export ELECTRON_BUILDER_BINARIES_MIRROR="${ELECTRON_BUILDER_BINARIES_MIRROR:-https://registry.npmmirror.com/-/binary/electron-builder-binaries/}"
export CSC_IDENTITY_AUTO_DISCOVERY=false
npx --yes electron-builder --linux AppImage deb --x64 --publish never \
  --config.npmRebuild=false \
  --config.extraMetadata.main="electron/main.js" \
  || fail "electron-builder failed"

step "5. Verify outputs"
[ -f "${APPIMAGE}" ] || fail "AppImage missing at ${APPIMAGE}"
[ -f "${DEB}" ]      || fail ".deb missing at ${DEB}"

for f in "${APPIMAGE}" "${DEB}"; do
  SIZE_BYTES="$(stat -c %s "$f")"
  SIZE_MB="$(awk -v b="${SIZE_BYTES}" 'BEGIN { printf "%.1f", b/1024/1024 }')"
  SHA256="$(sha256sum "$f" | awk '{print $1}')"
  echo "  $(basename "$f")  ${SIZE_MB} MiB  ${SHA256}"
done

echo ""
echo "✅ Linux installers built"
echo "   ${APPIMAGE}"
echo "   ${DEB}"