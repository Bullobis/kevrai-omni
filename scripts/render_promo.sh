#!/usr/bin/env bash
# Re-render the Remotion promo from the current catalog and publish it.
# Self-contained: installs deps if missing, renders, then commits + pushes only
# when the output actually changed. Safe to run on a schedule.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d promo/node_modules ]; then
  (cd promo && npm install)
fi

# Remotion 4.0.x defaults to the legacy `--headless` Chrome flag, which Chrome
# 144 removed ("Old Headless mode has been removed from the Chrome binary").
# `--chrome-mode=chrome-for-testing` switches it to `--headless=new`. Both the
# env var and the npm script flag are set so this works whether the render is
# driven by `npm run render` or by a direct `remotion render` call.
CHROME_MODE="${KEVRAI_CHROME_MODE:-chrome-for-testing}"
if [ -n "${KEVRAI_BROWSER_EXECUTABLE:-}" ]; then
  (cd promo && npm run render -- \
    --chrome-mode="$CHROME_MODE" \
    --browser-executable="$KEVRAI_BROWSER_EXECUTABLE")
else
  (cd promo && npm run render -- --chrome-mode="$CHROME_MODE")
fi

# Guard against a truncated encode: a 1080p/32s render is ~3 MB, so anything
# under 1 MB means the encoder died part-way rather than produced a short clip.
MIN_BYTES=$((1024 * 1024))
actual_bytes=$(wc -c < assets/media/promo.mp4)
if [ "$actual_bytes" -lt "$MIN_BYTES" ]; then
  echo "ERROR: promo.mp4 is only ${actual_bytes} bytes (< ${MIN_BYTES}); refusing to publish." >&2
  exit 1
fi

git add assets/media/promo.mp4
if git diff --cached --quiet; then
  echo "promo unchanged; nothing to commit."
  exit 0
fi

git -c user.name="Bullobis" -c user.email="2671369836@qq.com" \
  commit -m "chore(promo): scheduled promo refresh $(date -u +%F)"
git push origin main
echo "promo refreshed and pushed."
