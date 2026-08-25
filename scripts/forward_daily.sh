#!/usr/bin/env bash
# DAILY forward cron -- PULL ONLY (trimmed 2026-08-07 for the rewrite; see TODO.md phase plan).
#
# The only thing this still does is accumulate the day's news into data/forward/daily/<date>.json.
# That is kept running during the rewrite because the pull is UNREPEATABLE: WebSearch/Tavily results
# are not re-queryable, and articles get edited, paywalled or deleted, so every skipped day is a
# permanent hole in the corpus. Phase 3 (bootstrap) needs that corpus to splice the backtest tail
# onto, and no amount of later work can recreate it.
#
# REMOVED for the duration of the rewrite, restore in phase 5:
#   --report                     mark-to-market of the paper portfolio
#   build_forward_dashboard.py   dashboard render  (rebuilt in phase 4)
#   git add/commit/push          repo churn during a major rewrite
# The WEEKLY scout+rebalance (scripts/forward_cron.sh, Sunday) is commented out of crontab entirely:
# it is phase-5 work, and the 2026-08-07 mill_block edit to investor_profile.forward.md put a config
# discontinuity mid-series, so anything it accumulated from here would blend two configs.
#
# Billable Anthropic + Tavily (~$0.25-0.50/day). Dedups by date, so a re-run is a no-op.
# Data under data/forward/ stays LOCAL (gitignored); this script no longer touches git at all.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

.venv/bin/python src/forward.py --pull || echo "  daily pull failed"

# MIRROR IT IMMEDIATELY. The pull is unrepeatable -- Tavily re-serves a window it has already served
# with a smaller and partly different set -- so a daily file lost to a bad experiment or a stray rm
# cannot be rebuilt. Append-only; a changed file's previous copy is kept under superseded/.
.venv/bin/python scripts/backup_daily.py || echo "  daily backup reported a problem"
