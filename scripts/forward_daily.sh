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
#
# SELF-LOGGING, mirroring portfolio-wave-rider's news_pull.sh. The crontab ALSO redirects to this
# same file, and that redundancy is deliberate rather than sloppy: the block below cannot capture a
# failure that happens BEFORE it -- an unresolvable PROJ, a bad cd, a missing interpreter -- and
# those went to cron's mail, which on this machine means nowhere. Belt (script) and braces (crontab)
# put every outcome in one file. It also makes a hand-run identical to a cron run, which is how the
# 2026-08-15 crash was eventually read: the traceback was in the log all along.
set -uo pipefail
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || { echo "forward_daily: cannot resolve repo root"; exit 1; }
cd "$PROJ" || { echo "forward_daily: cannot cd to $PROJ"; exit 1; }
mkdir -p data/forward
{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] daily pull start"
  .venv/bin/python src/forward.py --pull --scheduled \
    || echo "[$(date '+%Y-%m-%d %H:%M:%S')] daily pull FAILED (tolerated)"
  # MIRROR IT IMMEDIATELY. The pull is unrepeatable -- Tavily re-serves a window it has already
  # served with a smaller and partly different set -- so a daily file lost to a bad experiment or a
  # stray rm cannot be rebuilt. Append-only; a changed file's previous copy is kept under superseded/.
  # DID THE PULL ACTUALLY LAND? The step above is `|| tolerated`, which is right -- a missed day
  # must not abort the backup or the page refresh -- but for eighteen days that tolerance is exactly
  # what hid 2026-08-15: the run crashed on an Anthropic 400, the traceback went into this log, and
  # nothing ever compared the files on disk to the calendar. This does, every morning, and prints
  # the recovery command with it. Cheap (a directory listing) and it reads the whole sequence, so a
  # gap opened months ago is still reported today rather than scrolling away.
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] pull-gap check"
  .venv/bin/python scripts/check_pull_gaps.py \
    || echo "[$(date '+%Y-%m-%d %H:%M:%S')] ^^ A DAY OF NEWS IS MISSING -- the pull is unrepeatable, recover it above"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] daily backup start"
  .venv/bin/python scripts/backup_daily.py \
    || echo "[$(date '+%Y-%m-%d %H:%M:%S')] daily backup reported a problem (tolerated)"
  # TAG THE DAY'S NEW ARTICLES -- a SEPARATE STEP, deliberately NOT inside the pull.
  # Websearch articles arrive with no `orgs`, and company bundling is seeded from `orgs`, so
  # untagged days are days the scout cannot see a firm's news as one block. This fills the tagger
  # cache for whatever arrived today.
  # WHY IT IS ITS OWN STEP AND NOT PART OF forward.py --pull: the pull must not depend on a SECOND
  # LLM provider being reachable. If OpenRouter is down we still want the news -- tags can be
  # filled in tomorrow, a missed pull cannot be (Tavily re-serves a window with a smaller and
  # partly different set). Failure here is tolerated for exactly that reason.
  # It is the same command as the historical backfill because it is the same operation: send only
  # articles that both lack an org key and are absent from the cache. After a normal day that is
  # ~86 articles, a few seconds. It is a no-op when org_tagger_model is off.
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] org-tag start"
  .venv/bin/python scripts/backfill_org_tags.py --corpus bootstrap --post-handoff-only \
    || echo "[$(date '+%Y-%m-%d %H:%M:%S')] org-tag reported a problem (tolerated)"
  # REFRESH THE TWO PAGES THE PULL JUST CHANGED. Render-only: 0 LLM calls, ~15s combined, so this
  # is free and belongs on the DAILY job even though the bootstrap now re-curates MONTHLY. The two
  # cadences are independent and it is worth being explicit about why:
  #   FBS describes the CORPUS, which grows every single day this job runs.
  #   CBS's book is priced DAILY from live quotes; only its curation (the journal) is monthly.
  # So a monthly curation cadence does NOT make a daily refresh wasteful -- the numbers on both pages
  # move every day regardless of when the curator last ran. This mirrors portfolio-wave-rider, where
  # the daily news_pull refreshes RBS/RFT and the LLM curation is a separate biweekly job.
  # NOT the monthly re-curation: that spends money and rewrites the published book, so it stays a
  # deliberate hand-run until it has an --if-due guard of its own.
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] FBS refresh start"
  .venv/bin/python scripts/build_fbt_dashboard.py --bootstrap \
    || echo "[$(date '+%Y-%m-%d %H:%M:%S')] FBS refresh FAILED (tolerated)"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] CBS refresh start"
  .venv/bin/python scripts/build_cbt_dashboard.py --bootstrap \
    || echo "[$(date '+%Y-%m-%d %H:%M:%S')] CBS refresh FAILED (tolerated)"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] daily pull done"
} >> data/forward/cron.log 2>&1
