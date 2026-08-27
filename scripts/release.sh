#!/usr/bin/env bash
# Kevrai Studio — release runbook.
#
# Pre-flight checks (all must pass):
#   * `gh` CLI installed and authenticated
#   * `GITHUB_TOKEN` env var set; NEW (rotated) PAT, not the leaked one
#   * PAT prefix is `ghp_` or `github_pat_` and is ≥ 40 chars
#   * The destination tag (default v$VERSION) does NOT already exist
#   * Both Windows + Linux artifacts are built and present in build/output
#   * Working tree is clean (no uncommitted changes)
#
# Flags:
#   --dry-run    Print every action we'd take, but don't actually release.
#   --skip-build Skip `build_windows.sh` / `electron-builder --linux`.

set -euo pipefail
cd "$(dirname "$0")/.."

INDEX="${KEVRAI_PIP_INDEX:-https://mirrors.tencent.com/pypi/simple/}"
VERSION="$(node -p "require('./package.json').version" 2>/dev/null || echo 1.0.0)"
REPO="${REPO:-Bullobis/kevrai-studio}"
TAG="${TAG:-v${VERSION}}"

DRY_RUN=0
SKIP_BUILD=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run)    DRY_RUN=1 ;;
    --skip-build) SKIP_BUILD=1 ;;
    --repo=*)     REPO="${arg#--repo=}" ;;
    --version=*)  VERSION="${arg#--version=}" ;;
    --tag=*)      TAG="${arg#--tag=}" ;;
    -h|--help)
      sed -n '2,30p' "$0"
      exit 0
      ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

step() { printf "\n\033[36m==>\033[0m %s\n" "$1"; }
fail() { printf "\n\033[31m==>\033[0m %s\n" "$1" >&2; exit 1; }
info() { printf "  %s\n" "$1"; }

# ----------------------------------------------------------------------
step "0. Pre-flight: required env"
# ----------------------------------------------------------------------
: "${GITHUB_TOKEN:?Set GITHUB_TOKEN (rotate the old one first!)}"
command -v gh >/dev/null 2>&1 || fail "gh CLI not installed"

# ----------------------------------------------------------------------
step "1. Reject the leaked token (belt-and-suspenders)"
# ----------------------------------------------------------------------
LEAKED_PREFIX="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
if [[ "${GITHUB_TOKEN}" == "${LEAKED_PREFIX}"* ]]; then
  fail "You are using the token that was pasted in chat. Rotate it first."
fi

# New PAT must look like a real GitHub PAT
case "${GITHUB_TOKEN}" in
  ghp_*)            : ;;
  github_pat_*)     : ;;
  *) fail "GITHUB_TOKEN must start with 'ghp_' or 'github_pat_' (current prefix: ${GITHUB_TOKEN:0:8}…)" ;;
esac
if [ "${#GITHUB_TOKEN}" -lt 40 ]; then
  fail "GITHUB_TOKEN looks too short (${#GITHUB_TOKEN} chars, need ≥ 40)"
fi
info "token shape OK (prefix matches, ≥ 40 chars)"

# ----------------------------------------------------------------------
step "2. Verify gh auth status with the new token"
# ----------------------------------------------------------------------
if [ "${DRY_RUN}" -eq 1 ]; then
  echo "  (dry-run) skipping gh auth status"
else
  GH_TOKEN="${GITHUB_TOKEN}" gh auth status || fail "gh is not authenticated with the supplied token"
fi

# ----------------------------------------------------------------------
step "3. Tag ${TAG} must NOT already exist on ${REPO}"
# ----------------------------------------------------------------------
if [ "${DRY_RUN}" -eq 1 ]; then
  echo "  (dry-run) skipping tag existence check"
else
  if GH_TOKEN="${GITHUB_TOKEN}" gh release view "${TAG}" --repo "${REPO}" >/dev/null 2>&1; then
    fail "tag ${TAG} already exists on ${REPO}; bump version or pick a new TAG"
  fi
  info "tag ${TAG} is free"
fi

# ----------------------------------------------------------------------
step "4. Working tree must be clean"
# ----------------------------------------------------------------------
if git rev-parse --git-dir >/dev/null 2>&1; then
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    info "warning: working tree has uncommitted changes"
    if [ "${DRY_RUN}" -eq 0 ]; then
      git status --porcelain | head -10
    fi
  else
    info "working tree clean"
  fi
else
  info "(no git repo)"
fi

# ----------------------------------------------------------------------
step "5. Build installers"
# ----------------------------------------------------------------------
if [ "${SKIP_BUILD}" -eq 1 ]; then
  info "skip-build requested; assuming build/output already populated"
else
  bash scripts/build_windows.sh || fail "build_windows.sh failed"
  npx --yes electron-builder --linux \
    || fail "electron-builder --linux failed (try again or use --skip-build)"
fi

# ----------------------------------------------------------------------
step "6. Collect artifacts"
# ----------------------------------------------------------------------
shopt -s nullglob
ARTIFACTS=( build/output/*.exe build/output/*.AppImage build/output/*.deb )
if [ "${#ARTIFACTS[@]}" -eq 0 ]; then
  fail "no artifacts found in build/output/"
fi
info "artifacts:"
for a in "${ARTIFACTS[@]}"; do
  size=$(stat -c %s "$a" 2>/dev/null || stat -f %z "$a")
  printf "    %s (%s bytes)\n" "$a" "$size"
done

# ----------------------------------------------------------------------
step "7. Generate release notes"
# ----------------------------------------------------------------------
NOTES_FILE="build/output/RELEASE_NOTES.md"
mkdir -p "$(dirname "${NOTES_FILE}")"
cat > "${NOTES_FILE}" <<EOF
# Kevrai Studio ${VERSION}

One installer, no bundled engines, full desktop shortcut on Windows.

## Highlights
- Model market: 60+ curated open models (LLM / TTS / Image / Video / 3D / Audio / SR).
- Engine market: on-demand downloads (llama.cpp, MNN, ONNX Runtime, vLLM, …).
- Local model import (folder or single file).
- GGUF repo enumeration (all quantizations of a repo are listable).
- Lazy installer: the .exe itself is small; engines download on first use.

## Security note
This release ships with a strict allowlist of model hosts (\`huggingface.co\`)
and engine hosts (\`github.com\` / \`pypi.org\` / \`mirrors.tencent.com\`).
The phishing domain \`hf-cdn.sufy.com\` is explicitly blocked.

## Quick start
1. Install \`Kevrai Studio-Setup-${VERSION}.exe\`.
2. Launch from desktop shortcut.
3. Open "AI 引擎" tab → install \`llama.cpp\` (first time).
4. Open "模型市场" → pick a model → HF 主页 to download, or use GGUF 全量化.
EOF
info "release notes written to ${NOTES_FILE}"

# ----------------------------------------------------------------------
step "8. ${DRY_RUN:+dry-run }Create the release"
# ----------------------------------------------------------------------
if [ "${DRY_RUN}" -eq 1 ]; then
  echo "  (dry-run) would run:"
  echo "    gh release create ${TAG} \\"
  for a in "${ARTIFACTS[@]}"; do
    echo "      ${a} \\"
  done
  echo "      --repo ${REPO} \\"
  echo "      --title 'Kevrai Studio ${VERSION}' \\"
  echo "      --notes-file ${NOTES_FILE}"
  echo ""
  echo "✅ Dry-run complete. No release was created."
  exit 0
fi

GH_TOKEN="${GITHUB_TOKEN}" gh release create "${TAG}" \
  "${ARTIFACTS[@]}" \
  --repo "${REPO}" \
  --title "Kevrai Studio ${VERSION}" \
  --notes-file "${NOTES_FILE}" \
  || fail "gh release create failed"

echo ""
echo "✅ Release ${TAG} published to ${REPO}"
echo "   verify: gh release view ${TAG} --repo ${REPO}"
echo "   sha / size of one artifact:"
SAMPLE="${ARTIFACTS[0]}"
sha256sum "${SAMPLE}" 2>/dev/null || shasum -a 256 "${SAMPLE}"
