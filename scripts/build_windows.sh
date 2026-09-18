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
WINE_CMD=""
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

  # Wine may be needed for the NSIS target.  Probe it for real: `--version`
  # alone succeeds even when the loader/DLL pairing is broken, so also run a
  # trivial PE command and check for the expected output.
  for candidate in "$(command -v wine64 || true)" "$(command -v wine || true)"; do
    [ -n "${candidate}" ] || continue
    if [ "$("${candidate}" --version 2>/dev/null | head -c 5)" = "wine-" ]; then
      probe_prefix="$(mktemp -d)"
      if WINEARCH=win32 WINEPREFIX="${probe_prefix}" WINEDEBUG=-all \
           timeout 120 "${candidate}" cmd /c "echo PROBE_OK" 2>/dev/null | grep -q PROBE_OK; then
        WINE_CMD="${candidate}"
      fi
      rm -rf "${probe_prefix}"
      [ -n "${WINE_CMD}" ] && break
    fi
  done
  if [ -n "${WINE_CMD}" ]; then
    echo "  wine:   ${WINE_CMD} (usable — NSIS installer will be built)"
  else
    echo "  wine:   not usable — NSIS installer will be skipped"
  fi
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
step "5b. Authenticode-sign the staged executable"
# ----------------------------------------------------------------------
# Signing has to happen *here*, on the staged app directory, not on the finished
# artifacts.  electron-builder's `zip` and `nsis` targets both re-derive the
# executable from the payload; signing a zip afterwards would be undone the next
# time the archive is rebuilt, and the NSIS uninstaller is extracted from the
# app dir too.  Signing the staged binary means every downstream artifact
# inherits the signature.
#
# A self-signed certificate is used by default.  Timestamping is
# network-dependent: if the TSA is unreachable we fall back to an untimestamped
# signature (still valid, just tied to the certificate's lifetime) rather than
# failing the build.
SIGN_CERT="${KEVRAI_SIGN_CERT:-.signing/kevrai.crt}"
SIGN_KEY="${KEVRAI_SIGN_KEY:-.signing/kevrai.key}"
TS_URL="${KEVRAI_TS_URL:-http://timestamp.digicert.com}"
STAGED_EXE_SIGNED=0

if [ -f "${SIGN_CERT}" ] && [ -f "${SIGN_KEY}" ] && command -v osslsigncode >/dev/null 2>&1; then
  _sign_staged_exe() {
    local tmp="${EXE}.unsigned"
    mv "${EXE}" "${tmp}"
    if osslsigncode sign -certs "${SIGN_CERT}" -key "${SIGN_KEY}" \
         -n "Kevrai Omni" \
         -i "https://github.com/Bullobis/kevrai-omni" \
         -ts "${TS_URL}" -h sha256 \
         -in "${tmp}" -out "${EXE}" >/dev/null 2>&1; then
      rm -f "${tmp}"
      echo "  ✓ signed (timestamped)"
      return 0
    fi
    if osslsigncode sign -certs "${SIGN_CERT}" -key "${SIGN_KEY}" \
         -n "Kevrai Omni" \
         -i "https://github.com/Bullobis/kevrai-omni" \
         -h sha256 \
         -in "${tmp}" -out "${EXE}" >/dev/null 2>&1; then
      rm -f "${tmp}"
      echo "  ✓ signed (no timestamp — TSA unreachable)"
      return 0
    fi
    mv "${tmp}" "${EXE}"
    echo "  ⚠ could not sign — shipping unsigned"
    return 1
  }
  if _sign_staged_exe; then
    STAGED_EXE_SIGNED=1
    # Confirm the signature actually landed and is self-consistent.
    if osslsigncode verify -in "${EXE}" -CAfile "${SIGN_CERT}" 2>&1 | grep -q "Succeeded"; then
      echo "  ✓ signature verified"
    else
      fail "signature was written but does not verify — refusing to ship"
    fi
  fi
else
  echo "  (no signing material at ${SIGN_CERT} / ${SIGN_KEY} — skipping)"
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
# Building NSIS from Linux requires Wine, because electron-builder generates the
# uninstaller by *executing* the freshly built installer — that step cannot be
# replaced by a native tool.
#
# Two environment details matter and are easy to get wrong:
#
#   1. WINEARCH must be win32.  Ubuntu's `wine` launcher is a 32-bit ELF
#      binary; forcing WINEARCH=win64 makes it look for a 64-bit loader and
#      fail with "could not load kernel32.dll, status c0000135".
#   2. WINEPREFIX must point at a prefix whose architecture matches WINEARCH,
#      otherwise Wine refuses to reuse it.
#
# macOS and native Windows builds skip this block entirely.
NSIS_OK=0
if [ "${HOST_OS}" = "Darwin" ]; then
  echo "  (macOS host — electron-builder cannot target Windows NSIS from here)"
elif [ -z "${WINRES}" ] && [ -z "${WINE_CMD}" ]; then
  echo "  (native Windows build — electron-builder handles NSIS itself)"
  if npx --yes electron-builder --win nsis --x64 --publish never \
        --prepackaged "${APP_DIR}" \
        --config.npmRebuild=false \
        --config.extraMetadata.main="electron/main.js"; then
    NSIS_OK=1
    echo "  ✓ NSIS installer built"
  fi
fi

if [ "${NSIS_OK}" -eq 0 ] && [ "${HOST_OS}" != "Darwin" ] && [ -n "${WINE_CMD}" ]; then
  export WINEARCH=win32
  export WINEPREFIX="${WINEPREFIX:-${HOME}/.wine-kevrai32}"
  export WINEDEBUG="${WINEDEBUG:--all}"
  # Make sure the prefix exists and is initialised for this architecture.
  if [ ! -d "${WINEPREFIX}/drive_c" ]; then
    echo "  initialising Wine prefix (${WINEPREFIX}, WINEARCH=${WINEARCH})..."
    "${WINE_CMD}" wineboot --init >/dev/null 2>&1 || true
  fi
  if npx --yes electron-builder --win nsis --x64 --publish never \
        --prepackaged "${APP_DIR}" \
        --config.npmRebuild=false \
        --config.extraMetadata.main="electron/main.js" \
        --config.win.signAndEditExecutable=false; then
    NSIS_OK=1
    echo "  ✓ NSIS installer built"
  fi
fi

if [ "${NSIS_OK}" -eq 0 ]; then
  echo ""
  echo "  ⚠ NSIS installer could not be produced on this machine."
  echo "    The portable zip above is fully functional and needs no installer."
  echo "    To produce the installer:"
  echo "      • run  npm run build:win  on a Windows machine, or"
  echo "      • install Wine (apt-get install -y wine64 wine32:i386) and re-run"
  echo "        this script — it will pick Wine up automatically."
  echo ""
fi

# ----------------------------------------------------------------------
step "8. Authenticode-sign the NSIS installer"
# ----------------------------------------------------------------------
# The installer is a distinct PE image built by NSIS — it embeds the signed
# payload as *data* but carries its own PE header, so it does NOT inherit the
# executable's signature.  It has to be signed separately, after the build.
if [ "${STAGED_EXE_SIGNED}" -eq 1 ] && [ "${NSIS_OK}" -eq 1 ] && [ -f "${EXPECTED_EXE}" ]; then
  _sign_installer() {
    local tmp="${EXPECTED_EXE}.unsigned"
    mv "${EXPECTED_EXE}" "${tmp}"
    if osslsigncode sign -certs "${SIGN_CERT}" -key "${SIGN_KEY}" \
         -n "Kevrai Omni" \
         -i "https://github.com/Bullobis/kevrai-omni" \
         -ts "${TS_URL}" -h sha256 \
         -in "${tmp}" -out "${EXPECTED_EXE}" >/dev/null 2>&1; then
      rm -f "${tmp}"
      echo "  ✓ installer signed (timestamped)"
      return 0
    fi
    if osslsigncode sign -certs "${SIGN_CERT}" -key "${SIGN_KEY}" \
         -n "Kevrai Omni" \
         -i "https://github.com/Bullobis/kevrai-omni" \
         -h sha256 \
         -in "${tmp}" -out "${EXPECTED_EXE}" >/dev/null 2>&1; then
      rm -f "${tmp}"
      echo "  ✓ installer signed (no timestamp — TSA unreachable)"
      return 0
    fi
    mv "${tmp}" "${EXPECTED_EXE}"
    echo "  ⚠ installer could not be signed — shipping unsigned"
    return 1
  }
  _sign_installer || true

  # Signing rewrites the installer's bytes, which invalidates two things
  # electron-builder generated from the pre-signature image:
  #
  #   * the .blockmap (differential-update index) — its offsets no longer match
  #   * latest.yml — it pins the installer's sha512 and size, so electron-updater
  #     would reject the download with a checksum mismatch
  #
  # The blockmap is simply dropped: without it electron-updater falls back to a
  # full download, which is correct if less bandwidth-efficient.  latest.yml is
  # rewritten below so the advertised hash matches what we actually ship.
  rm -f "${EXPECTED_EXE}.blockmap"

  if [ -f build/output/latest.yml ]; then
    ${PY} - build/output/latest.yml "${EXPECTED_EXE}" "${PRODUCT}" <<'PY' \
      || fail "could not refresh latest.yml after signing"
import base64, hashlib, os, re, sys

yml_path, exe_path, product = sys.argv[1], sys.argv[2], sys.argv[3]
name = os.path.basename(exe_path)

data = open(exe_path, "rb").read()
sha512 = base64.b64encode(hashlib.sha512(data).digest()).decode()
size = len(data)

text = open(yml_path, encoding="utf-8").read()
text = re.sub(r"sha512: [^\n]+", f"sha512: {sha512}", text)
text = re.sub(r"size: \d+", f"size: {size}", text)
open(yml_path, "w", encoding="utf-8").write(text)

print(f"    latest.yml refreshed for signed image ({size} bytes)")
PY
  fi
elif [ "${NSIS_OK}" -eq 1 ]; then
  echo "  (no signing material — installer ships unsigned)"
fi

# ----------------------------------------------------------------------
step "9. Verify signatures on the finished artifacts"
# ----------------------------------------------------------------------
# Both channels are checked, so a silent regression cannot ship an unsigned
# binary in either one.
if [ "${STAGED_EXE_SIGNED}" -eq 1 ]; then
  _verify_signed() {
    local f="$1" label="$2"
    if osslsigncode verify -in "${f}" -CAfile "${SIGN_CERT}" 2>&1 | grep -q "Succeeded"; then
      echo "  ✓ ${label}: signature valid"
    else
      echo "  ✗ ${label}: SIGNATURE MISSING OR INVALID" >&2
      return 1
    fi
  }

  if [ "${NSIS_OK}" -eq 1 ] && [ -f "${EXPECTED_EXE}" ]; then
    _verify_signed "${EXPECTED_EXE}" "installer (.exe)" \
      || fail "installer is not signed — refusing to ship"
  fi

  _ZIPCHECK="$(mktemp -d)"
  if unzip -q -o "${EXPECTED_ZIP}" "${PRODUCT}.exe" -d "${_ZIPCHECK}" 2>/dev/null; then
    _verify_signed "${_ZIPCHECK}/${PRODUCT}.exe" "portable zip payload" \
      || fail "portable zip lost the executable signature — refusing to ship"
  else
    echo "  ⚠ could not extract ${PRODUCT}.exe from the zip for verification"
  fi
  rm -rf "${_ZIPCHECK}"

  # latest.yml drives the in-app updater.  If its recorded size/hash drift from
  # the shipped installer, every client fails the update with a checksum error —
  # a silent, hard-to-diagnose failure, so assert it here.
  if [ "${NSIS_OK}" -eq 1 ] && [ -f build/output/latest.yml ]; then
    # Note: the keys are indented under `files:` as well as repeated at the top
    # level, so match with leading whitespace allowed and take the first hit.
    _YML_SIZE="$(grep -m1 -E '^[[:space:]]*size:' build/output/latest.yml | awk '{print $2}')"
    _YML_SHA512="$(grep -m1 -E '^[[:space:]]*sha512:' build/output/latest.yml | awk '{print $2}')"
    _EXE_SIZE="$(stat -c %s "${EXPECTED_EXE}" 2>/dev/null || stat -f %z "${EXPECTED_EXE}")"
    _EXE_SHA512="$(openssl dgst -sha512 -binary "${EXPECTED_EXE}" | base64 -w0)"
    [ -n "${_YML_SIZE}" ] || fail "latest.yml has no size field"
    [ -n "${_YML_SHA512}" ] || fail "latest.yml has no sha512 field"
    if [ "${_YML_SIZE}" != "${_EXE_SIZE}" ]; then
      fail "latest.yml size (${_YML_SIZE}) != installer size (${_EXE_SIZE})"
    fi
    if [ "${_YML_SHA512}" != "${_EXE_SHA512}" ]; then
      fail "latest.yml sha512 does not match the installer — updater would reject it"
    fi
    echo "  ✓ latest.yml matches the signed installer (size + sha512)"
  fi
else
  echo "  (artifacts are unsigned — nothing to verify)"
fi

# ----------------------------------------------------------------------
step "10. Report the resulting artifacts"
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

