#!/usr/bin/env bash
# Re-render the Remotion promo from the current catalog and publish it.
# Self-contained: installs deps if missing, renders, then commits + pushes only
# when the output actually changed. Safe to run on a schedule.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d promo/node_modules ]; then
  (cd promo && npm install)
fi

(cd promo && npm run render)

git add assets/media/promo.mp4
if git diff --cached --quiet; then
  echo "promo unchanged; nothing to commit."
  exit 0
fi

git -c user.name="Kevrai Bot" -c user.email="bot@kevrai.local" \
  commit -m "chore(promo): scheduled promo refresh $(date -u +%F)"
git push origin main
echo "promo refreshed and pushed."
