#!/usr/bin/env bash
#
# update_promo.sh — 定时重新生成宣传视频并替换 assets/media/promo.mp4，
# 然后提交、推送（默认不打 GitHub Release；需要发布时另行执行 release 流程）。
#
# 用法：
#   bash scripts/update_promo.sh
#
# 环境：
#   需要 Node/npm、系统 Chromium（chromium-browser / chromium）、git 推送权限。
#   首次运行会在 promo/ 自动 npm install。
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BROWSER="$(command -v chromium-browser || command -v chromium || true)"
if [ -z "$BROWSER" ]; then
  echo "error: 未找到 chromium-browser / chromium" >&2
  exit 1
fi

BUILD_DATE="$(date +%F)"
VERSION="$(node -p "require('./package.json').version")"
# variant 按 epoch 天数在 0..7 间轮换：不同日期渲染，粒子/波形动态不同
VARIANT="$(( $(date +%s) / 86400 % 8 ))"
PROPS="{\"variant\":${VARIANT},\"buildDate\":\"${BUILD_DATE}\",\"version\":\"${VERSION}\"}"

echo "[promo] variant=${VARIANT} buildDate=${BUILD_DATE} version=${VERSION}"

cd promo
[ -d node_modules ] || npm install
rm -f out/promo.mp4
npx remotion render src/index.ts KevraiPromo out/promo.mp4 \
  --codec=h264 \
  --browser-executable="$BROWSER" \
  --gl=swiftshader \
  --props="$PROPS"

cd "$REPO_ROOT"
cp promo/out/promo.mp4 assets/media/promo.mp4

echo "[promo] 已替换 assets/media/promo.mp4"

# 提交并推送（无变更 / 推送失败不视为脚本失败，便于在受限环境中重跑）
git add assets/media/promo.mp4
if git diff --cached --quiet; then
  echo "[promo] 视频无变化，跳过提交"
else
  git -c user.name="${GIT_AUTHOR_NAME:-K-Nexus Bot}" \
      -c user.email="${GIT_AUTHOR_EMAIL:-k-nexus@local}" \
      commit -m "chore(promo): 定时更新宣传视频 (${BUILD_DATE} variant=${VARIANT})"
  git push || echo "[promo] git push 失败，请检查网络/权限"
fi
