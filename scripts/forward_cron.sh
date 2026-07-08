#!/usr/bin/env bash
# Weekly forward cron: scan the current week, build + PRESERVE the dated dashboard, and commit+push the
# DASHBOARDS ONLY (docs/forward/<week>.html + landing). The pulled/derived data under data/forward/ stays
# LOCAL (gitignored) — git holds the solution + dashboards, never the news pull. Auto-push follows the
# standing dashboards-are-safe-to-push rule; it needs non-interactive git auth (e.g. an SSH key), else the
# commit stays local and you push it manually. Idempotent: --scan dedups by week; the build overwrites only
# the current week's file, never prior weeks'.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

.venv/bin/python src/forward.py --scan || { echo "  scan failed"; exit 1; }
.venv/bin/python scripts/build_forward_dashboard.py --sandbox data/forward --out docs/forward || { echo "  build failed"; exit 1; }

git add docs/forward
if ! git diff --cached --quiet; then
    git commit -m "forward dashboard: weekly update $(date +%F)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
    git push || echo "  push failed (commit is local; push it manually)"
fi
