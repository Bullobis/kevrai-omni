#!/usr/bin/env bash
#
# update_promo.sh — 定时重新生成宣传视频并替换 assets/media/promo.mp4，
# 然后提交、推送（默认不打 GitHub Release）。
#
# 使用仓库内 main 的 Remotion 工程（promo/，React 19 + JSX）。
# 每次运行 prepare-data.mjs 会：
#   - 把结尾 dateLabel 更新为当天（Asia/Shanghai）；
#   - 按 epoch 天数（dayIndex）轮换模型市场的 featured 列表，
#     因此每次定时渲染的画面内容真实不同。
#
# 用法： bash scripts/update_promo.sh
# 环境： Node/npm、系统 Chromium（chromium-browser / chromium）、git 推送权限。
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
echo "[promo] scheduled refresh ${BUILD_DATE}"

cd promo
[ -d node_modules ] || npm install

# 重新生成 src/data.json：更新 dateLabel 与按天轮换的 featured
npm run data

# 无头渲染（SwiftShader 软件 GL，适配无 GPU 的云环境）
npx remotion render src/index.js Promo ../assets/media/promo.mp4 \
  --codec=h264 \
  --browser-executable="$BROWSER" \
  --gl=swiftshader

cd "$REPO_ROOT"
echo "[promo] 已替换 assets/media/promo.mp4"

git add assets/media/promo.mp4
if git diff --cached --quiet; then
  echo "[promo] 视频无变化，跳过提交"
else
  git -c user.name="${GIT_AUTHOR_NAME:-Kevrai Bot}" \
      -c user.email="${GIT_AUTHOR_EMAIL:-bot@kevrai.local}" \
      commit -m "chore(promo): scheduled promo refresh ${BUILD_DATE}"
  git push || echo "[promo] git push 失败，请检查网络/权限"
fi
