#!/usr/bin/env bash
# DAILY forward cron: a 1-day Anthropic news pull (accumulates the week's coverage day by day, for the
# weekly scan), plus a mark-to-market of the paper portfolio and a dashboard refresh + push. The WEEKLY
# scout + rebalance is forward_cron.sh (Sunday). The pull is billable Anthropic (~$0.25-0.50/day) and
# dedups by date, so a re-run is a no-op. Data under data/forward/ stays LOCAL; only docs/forward/ is pushed.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

.venv/bin/python src/forward.py --pull   || echo "  daily pull failed"
.venv/bin/python src/forward.py --report || true
.venv/bin/python scripts/build_forward_dashboard.py --sandbox data/forward --out docs/forward || true

git add docs/forward
if ! git diff --cached --quiet; then
    git commit -m "forward dashboard: daily update $(date +%F)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
    git push || echo "  push failed (commit is local; push it manually)"
fi
