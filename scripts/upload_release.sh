#!/usr/bin/env bash
# Kevrai Omni — 上传已构建的产物到 GitHub Release。
#
# 与 scripts/release.sh 的区别：
#   * 本脚本不重新构建，直接使用 build/output 里已有的产物
#   * 只做「创建 release + 上传资产」这一段，便于在构建已完成时单独重跑
#   * 会校验每个资产的 sha256 与 release notes 的存在
#
# 用法：
#   GITHUB_TOKEN=<新轮换的 PAT> bash scripts/upload_release.sh
#   GITHUB_TOKEN=... bash scripts/upload_release.sh --dry-run
#
# 可选环境变量：
#   API_BASE  默认 https://api.github.com
#             若直连不通，可设为 https://gh-proxy.com/https://api.github.com
#   REPO      默认 Bullobis/kevrai-omni

set -euo pipefail
cd "$(dirname "$0")/.."

API_BASE="${API_BASE:-https://api.github.com}"
REPO="${REPO:-Bullobis/kevrai-omni}"
VERSION="$(node -p "require('./package.json').version" 2>/dev/null || echo unknown)"
TAG="v${VERSION}"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

step() { printf "\n\033[36m==>\033[0m %s\n" "$1"; }
fail() { printf "\n\033[31m==>\033[0m %s\n" "$1" >&2; exit 1; }
info() { printf "  %s\n" "$1"; }

# ----------------------------------------------------------------------
step "0. Pre-flight"
# ----------------------------------------------------------------------
: "${GITHUB_TOKEN:?必须设置 GITHUB_TOKEN（请先轮换旧 token）}"

# 拒绝把「已知泄露过的凭证」用于发布。
#
# 这里刻意只比对**不可逆的指纹**，不存原文：把泄露 token 的字面量写进
# 源码，等于把它重新提交进一个公开仓库 —— 那正是要防止的事。
# （本脚本初版就犯了这个错，被 GitHub push protection 正确拦下。）
#
# 指纹取 sha256 的前 16 个十六进制字符。要新增一条，用：
#   printf '%s' '<token>' | sha256sum | cut -c1-16
LEAKED_FINGERPRINTS=(
  # 2026-09-23 在聊天中粘贴过的那枚 PAT（sha256 前 16 位）。
  # 记录指纹而非原文，既能让脚本拒绝它，又不会把凭证本身提交进仓库。
  "85dc79fb23eca006"
)
_HAVE="${GITHUB_TOKEN:-}"
if [ -n "${_HAVE}" ]; then
  _FP="$(printf '%s' "${_HAVE}" | sha256sum | cut -c1-16)"
  for known in "${LEAKED_FINGERPRINTS[@]}"; do
    if [ "${_FP}" = "${known}" ]; then
      fail "这枚 token 的指纹命中已知泄露列表（${known}）。请先到 GitHub 吊销，再换新的。"
    fi
  done
  unset _FP
fi
unset _HAVE

command -v curl >/dev/null 2>&1 || fail "curl 不可用"

info "repo    : ${REPO}"
info "tag     : ${TAG}"
info "api base: ${API_BASE}"
info "dry-run : ${DRY_RUN}"

# ----------------------------------------------------------------------
step "1. 确认 tag 已存在于远端"
# ----------------------------------------------------------------------
# release 必须挂在已经推送的 tag 上，否则 GitHub 会拒绝或用错 commit。
if [ "${DRY_RUN}" -eq 0 ]; then
  code="$(curl -s -o /dev/null -w '%{http_code}' -m 30 \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    "${API_BASE}/repos/${REPO}/git/ref/tags/${TAG}")"
  case "${code}" in
    200) info "tag ${TAG} 已存在 ✓" ;;
    404) fail "远端没有 tag ${TAG}，请先执行: git push origin ${TAG}" ;;
    401|403) fail "认证失败（HTTP ${code}）—— token 无效、已吊销或权限不足" ;;
    *) fail "查询 tag 失败（HTTP ${code}）" ;;
  esac
else
  info "(dry-run) 跳过 tag 检查"
fi

# ----------------------------------------------------------------------
step "2. 收集并校验产物"
# ----------------------------------------------------------------------
shopt -s nullglob
ASSETS=(
  build/output/*.exe
  build/output/*.zip
  build/output/*.AppImage
  build/output/*.deb
  build/output/*.dmg
  build/output/latest*.yml
  build/output/*.blockmap
)
[ "${#ASSETS[@]}" -gt 0 ] || fail "build/output/ 里没有产物，请先构建"

NOTES="RELEASE_NOTES_${VERSION}.md"
[ -f "${NOTES}" ] || fail "缺少 ${NOTES} —— 发布页会退回通用文案，请先补齐"

info "产物清单："
for a in "${ASSETS[@]}"; do
  size=$(stat -c %s "${a}" 2>/dev/null || stat -f %z "${a}")
  # 小于 1MB 的安装包几乎必然是截断的构建
  case "${a}" in
    *.exe|*.zip|*.AppImage|*.deb|*.dmg)
      [ "${size}" -gt 1048576 ] || fail "${a} 只有 ${size} 字节，疑似截断" ;;
  esac
  printf "    %-52s %10s bytes  sha256=%s\n" "${a}" "${size}" \
    "$(sha256sum "${a}" | cut -c1-16)…"
done

# ----------------------------------------------------------------------
step "3. 创建（或复用）release"
# ----------------------------------------------------------------------
if [ "${DRY_RUN}" -eq 1 ]; then
  info "(dry-run) 将创建 release ${TAG} 并上传 ${#ASSETS[@]} 个资产"
  info "(dry-run) 发布说明取自 ${NOTES}"
  echo
  echo "  dry-run 结束，未对远端做任何修改。"
  exit 0
fi

payload="$(python3 - "${TAG}" "${NOTES}" <<'PY'
import json, sys
tag, notes = sys.argv[1], sys.argv[2]
body = open(notes, encoding="utf-8").read()
print(json.dumps({
    "tag_name": tag,
    "name": f"Kevrai Omni {tag}",
    "body": body,
    "draft": False,
    "prerelease": False,
}, ensure_ascii=False))
PY
)"

resp="$(curl -s -m 60 -X POST \
  -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  "${API_BASE}/repos/${REPO}/releases" -d "${payload}")"

release_id="$(printf '%s' "${resp}" | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(''); raise SystemExit
print(d.get('id',''))
" 2>/dev/null || true)"

if [ -z "${release_id}" ]; then
  msg="$(printf '%s' "${resp}" | head -c 400)"
  # 已存在同名 release 时 GitHub 返回 422；这与「创建失败」要分开处理。
  case "${msg}" in
    *already_exists*|*"already exists"*)
      info "release ${TAG} 已存在，改为查询其 id"
      release_id="$(curl -s -m 30 \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H "Accept: application/vnd.github+json" \
        "${API_BASE}/repos/${REPO}/releases/tags/${TAG}" \
        | python3 -c "import json,sys; print(json.load(sys.stdin).get('id',''))")"
      [ -n "${release_id}" ] || fail "无法获取已存在 release 的 id"
      ;;
    *)
      fail "创建 release 失败：${msg}" ;;
  esac
fi
info "release id = ${release_id}"

# ----------------------------------------------------------------------
step "4. 上传资产"
# ----------------------------------------------------------------------
for a in "${ASSETS[@]}"; do
  name="$(basename "${a}")"
  info "上传 ${name} …"
  up="$(curl -s -m 1800 -X POST \
    --data-binary "@${a}" \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "Content-Type: application/octet-stream" \
    -H "Accept: application/vnd.github+json" \
    "https://uploads.github.com/repos/${REPO}/releases/${release_id}/assets?name=${name}" \
    2>&1 || true)"

  # 上传成功返回含 "browser_download_url" 的 JSON；失败则含 "message"。
  if printf '%s' "${up}" | grep -q "browser_download_url"; then
    echo "    ✓ ${name}"
  elif printf '%s' "${up}" | grep -qi "already_exists"; then
    echo "    • ${name} 已存在，跳过"
  else
    fail "上传 ${name} 失败：$(printf '%s' "${up}" | head -c 300)"
  fi
done

# ----------------------------------------------------------------------
step "5. 校验"
# ----------------------------------------------------------------------
final="$(curl -s -m 30 \
  -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  "${API_BASE}/repos/${REPO}/releases/tags/${TAG}")"
count="$(printf '%s' "${final}" | python3 -c "
import json,sys
print(len(json.load(sys.stdin).get('assets',[])))
")"
info "release ${TAG} 现有 ${count} 个资产"
echo
echo "✅ 发布完成：https://github.com/${REPO}/releases/tag/${TAG}"
