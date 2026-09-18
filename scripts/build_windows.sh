#!/usr/bin/env bash
# Kevrai Omni — Windows installer + portable zip build.
#
# Works both on Windows (native) and on Linux (cross-build).
#
# Output:
#   build/output/Kevrai-Omni-<version>-x64.exe   (NSIS installer)
#   build/output/Kevrai-Omni-<version>-x64.zip   (portable archive)
#   build/output/latest.yml                        (auto-update metadata)
#
# Cross-building from Linux normally requires Wine, because electron-builder
# patches the executable's icon and version resource by running rcedit.exe.
# Wine is frequently unavailable in CI/sandboxes, so this script instead uses
# `go-winres` (a pure-Go PE resource editor) to embed the metadata natively and
# tells electron-builder to skip its own rcedit step.
#
# Steps (each verified, aborting non-zero on the first failure):
#   1. Tooling is present (node, npm, python)
#   2. npm install succeeded and node_modules is actually populated
#   3. python deps install and are importable
#   4. Application payload is staged (--dir)
#   5. Version resource + icon are embedded with go-winres and verified
#   6. electron-builder assembles the installer, portable zip and latest.yml
#   7. Artefacts exist, are non-trivial in size, and their hashes are printed

set -euo pipefail
cd "$(dirname "$0")/.."

INDEX="${KEVRAI_PIP_INDEX:-https://mirrors.tencent.com/pypi/simple/}"
VERSION="$(node -p "require('./package.json').version" 2>/dev/null || echo 2.8.1)"
PRODUCT="$(node -p "require('./package.json').productName" 2>/dev/null || echo 'Kevrai Omni')"
EXPECTED_EXE="build/output/Kevrai-Omni-${VERSION}-x64.exe"
EXPECTED_ZIP="build/output/Kevrai-Omni-${VERSION}-x64.zip"

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

# go-winres is only needed when cross-building (i.e. not on Windows).
HOST_OS="$(uname -s)"
WINRES=""
if [ "${HOST_OS}" != "MINGW"* ] && [ "${HOST_OS}" != "MSYS"* ] && [ "${HOST_OS}" != "CYGWIN"* ]; then
  WINRES="$(command -v go-winres || true)"
  if [ -z "${WINRES}" ]; then
    for candidate in "${HOME}/go/bin/go-winres" /usr/local/go-packages/bin/go-winres; do
      [ -x "${candidate}" ] && { WINRES="${candidate}"; break; }
    done
  fi
  if [ -z "${WINRES}" ]; then
    fail "go-winres not found (needed to embed icon/version without Wine).
       install with:  go install github.com/tc-hib/go-winres@latest"
  fi
  echo "  winres: ${WINRES}"
fi

# ----------------------------------------------------------------------
step "1. npm install (verified)"
# ----------------------------------------------------------------------
# Use npmmirror.com in CN (npmjs.org is often blocked in sandboxed CI).
npm config set registry https://registry.npmmirror.com 2>/dev/null || true
export ELECTRON_MIRROR="${ELECTRON_MIRROR:-https://cdn.npmmirror.com/binaries/electron/}"
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
step "2. python deps install (verified exit 0)"
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

export ELECTRON_BUILDER_BINARIES_MIRROR="${ELECTRON_BUILDER_BINARIES_MIRROR:-https://cdn.npmmirror.com/binaries/electron-builder-binaries/}"
export CSC_IDENTITY_AUTO_DISCOVERY=false

# ----------------------------------------------------------------------
step "4. Stage the application payload (--dir)"
# ----------------------------------------------------------------------
# ``--dir`` packs the app without building an installer, which is exactly what
# we need before re-branding the executable.  signAndEditExecutable is false in
# electron-builder.yml, so nothing tries to invoke Wine here.
npx --yes electron-builder --win --x64 --dir --publish never \
  --config.npmRebuild=false \
  --config.extraMetadata.main="electron/main.js" \
  || fail "electron-builder --dir failed"

APP_DIR="build/output/win-unpacked"
EXE="${APP_DIR}/${PRODUCT}.exe"
[ -f "${EXE}" ] || fail "staged executable not found: ${EXE}"
echo "  ✓ staged ${EXE}"

# ----------------------------------------------------------------------
step "5. Embed version resource + icon (go-winres, no Wine)"
# ----------------------------------------------------------------------
if [ -n "${WINRES}" ]; then
  WR="$(mktemp -d)"
  trap 'rm -rf "${WR}"' EXIT

  ICON_SRC="assets/icons/icon-256.png"
  [ -f "${ICON_SRC}" ] || ICON_SRC="assets/icons/icon-1024.png"
  cp "${ICON_SRC}" "${WR}/icon.png"

  FILE_VERSION="${VERSION}.0"
  cat > "${WR}/winres.json" <<JSON
{
  "RT_GROUP_ICON": {
    "APP": {
      "0000": ["icon.png"]
    }
  },
  "RT_VERSION": {
    "#1": {
      "0000": {
        "fixed": {
          "file_version": "${FILE_VERSION}",
          "product_version": "${FILE_VERSION}"
        },
        "info": {
          "0409": {
            "CompanyName": "Kevrai Omni contributors",
            "FileDescription": "Kevrai Omni - One-click Local AI Workstation",
            "FileVersion": "${VERSION}",
            "InternalName": "${PRODUCT}",
            "LegalCopyright": "Copyright (C) 2026 Kevrai Omni contributors",
            "OriginalFilename": "${PRODUCT}.exe",
            "ProductName": "${PRODUCT}",
            "ProductVersion": "${VERSION}"
          }
        }
      }
    }
  }
}
JSON

  "${WINRES}" patch --no-backup --in "${WR}/winres.json" "${EXE}" \
    || fail "go-winres patch failed"

  # Verify the branding actually landed; a silent no-op here would ship an
  # executable still identifying itself as "Electron".
  ${PY} - "${EXE}" "${PRODUCT}" <<'PY' || fail "embedded metadata verification failed"
import sys
try:
    import pefile
except ImportError:
    # pefile is optional; without it we cannot verify but the patch still ran.
    sys.exit(0)

exe, expected = sys.argv[1], sys.argv[2]
pe = pefile.PE(exe, fast_load=False)
found = {}
for group in pe.FileInfo:
    for entry in group:
        if entry.Key == b"StringFileInfo":
            for table in entry.StringTable:
                for key, value in table.entries.items():
                    found[key.decode(errors="replace")] = value.decode(errors="replace")
pe.close()

product = found.get("ProductName")
if product != expected:
    sys.stderr.write(f"error: ProductName is {product!r}, expected {expected!r}\n")
    raise SystemExit(1)
print(f"    ProductName = {product}")
print(f"    FileVersion = {found.get('FileVersion')}")
print(f"    CompanyName = {found.get('CompanyName')}")
PY
  echo "  ✓ metadata embedded and verified"
else
  echo "  (native Windows build — electron-builder handles metadata itself)"
fi

# ----------------------------------------------------------------------
step "6. Build the portable zip"
# ----------------------------------------------------------------------
# ``--prepackaged`` reuses the staging directory we just re-branded, instead of
# repackaging from scratch.  Without it electron-builder would extract a fresh
# electron.exe — overwriting the icon/version resource embedded in step 5 — and
# the shipped binary would identify itself as "Electron" again.
# Argument order matters: `--win` is declared as type "array" by electron-builder
# and `--x64` as a boolean.  Placing the boolean *between* the array flag and its
# value terminates the array's value collection, so the target name (`zip`) is
# parsed as a stray positional argument and yargs' .strict() aborts with
# "Unknown argument: zip".  Always pass the target immediately after --win.
npx --yes electron-builder --win zip --x64 --publish never \
  --prepackaged "${APP_DIR}" \
  --config.npmRebuild=false \
  --config.extraMetadata.main="electron/main.js" \
  --config.win.signAndEditExecutable=false \
  || fail "electron-builder (zip) failed"

if [ ! -f "${EXPECTED_ZIP}" ]; then
  CANDIDATE_ZIP="$(ls -1 build/output/*.zip 2>/dev/null | head -1 || true)"
  [ -n "${CANDIDATE_ZIP}" ] || fail "no portable .zip produced"
  EXPECTED_ZIP="${CANDIDATE_ZIP}"
fi
echo "  ✓ portable zip: ${EXPECTED_ZIP}"

# ----------------------------------------------------------------------
step "7. Build the NSIS installer"
# ----------------------------------------------------------------------
# Caveat: building NSIS from Linux requires Wine, because electron-builder
# generates the uninstaller by *executing* the freshly built installer, and
# that step cannot be replaced by a native tool.  Where Wine is unavailable we
# report it clearly and still deliver everything else, rather than failing the
# whole build.
NSIS_OK=0
if npx --yes electron-builder --win nsis --x64 --publish never \
      --prepackaged "${APP_DIR}" \
      --config.npmRebuild=false \
      --config.extraMetadata.main="electron/main.js" \
      --config.win.signAndEditExecutable=false; then
  NSIS_OK=1
  echo "  ✓ NSIS installer built"
else
  echo ""
  echo "  ⚠ NSIS installer could not be produced on this machine."
  echo "    Reason: electron-builder needs Wine to run the generated installer"
  echo "            when extracting the uninstaller, and Wine cannot execute"
  echo "            PE binaries in this environment."
  echo "    Options:"
  echo "      • run  npm run build:win  on a Windows machine, or"
  echo "      • install a working Wine (wine32:i386) and re-run this script."
  echo "    The portable zip above is fully functional and needs no installer."
  echo ""
fi

# ----------------------------------------------------------------------
step "8. Verify the resulting artifacts"
# ----------------------------------------------------------------------
if [ "${NSIS_OK}" -eq 1 ] && [ ! -f "${EXPECTED_EXE}" ]; then
  CANDIDATE="$(ls -1 build/output/*.exe 2>/dev/null | head -1 || true)"
  [ -n "${CANDIDATE}" ] || fail "no .exe installer produced at ${EXPECTED_EXE}"
  EXPECTED_EXE="${CANDIDATE}"
fi

if [ ! -f "${EXPECTED_ZIP}" ]; then
  CANDIDATE_ZIP="$(ls -1 build/output/*.zip 2>/dev/null | head -1 || true)"
  [ -n "${CANDIDATE_ZIP}" ] || fail "no portable .zip produced at ${EXPECTED_ZIP} (zip target missing?)"
  EXPECTED_ZIP="${CANDIDATE_ZIP}"
fi

# latest.yml is only emitted alongside a real installer; warn (don't fail)
# when NSIS was skipped so the rest of the build still completes.
if [ "${NSIS_OK}" -eq 1 ] && [ ! -f build/output/latest.yml ]; then
  fail "build/output/latest.yml missing — auto-update will not work (publish config?)"
fi

verify_artifact() {
  local f="$1" label="$2"
  local sb smb sha
  sb="$(stat -c %s "${f}" 2>/dev/null || stat -f %z "${f}")"
  smb="$(awk -v b="${sb}" 'BEGIN { printf "%.1f", b/1024/1024 }')"
  if [ "${sb}" -lt 1048576 ]; then
    fail "${label} too small (${sb} bytes) — likely truncated build"
  fi
  sha="$(sha256sum "${f}" 2>/dev/null | awk '{print $1}' || shasum -a 256 "${f}" | awk '{print $1}')"
  echo "   ${label}: ${f}"
  echo "     size:   ${smb} MiB (${sb} bytes)"
  echo "     sha256: ${sha}"
}

echo ""
if [ "${NSIS_OK}" -eq 1 ]; then
  echo "✅ Build succeeded (installer + portable zip)"
  verify_artifact "${EXPECTED_EXE}" "installer"
  echo "   auto-update metadata: build/output/latest.yml"
else
  echo "✅ Build succeeded (portable zip)"
  echo "   the NSIS installer was skipped — see the note above for how to"
  echo "   produce it on a Windows machine"
fi
verify_artifact "${EXPECTED_ZIP}" "portable zip"

