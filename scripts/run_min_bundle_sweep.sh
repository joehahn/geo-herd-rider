#!/bin/zsh
# Three curations differing ONLY in min_bundle_articles, on the canonical corpus and config.
# --decisions IS REQUIRED, not optional. Omitting it on the 2026-08-21 run cost CBT panels 14/16/17
# and the three scout bars of the funnel: proposed-and-culled candidates are persisted NOWHERE else
# (archive raw_results is empty, firehose_scans.csv holds only what was ADMITTED), so the counts are
# unrecoverable without re-curating.
# SERIAL on purpose: the bake-off ran arms concurrently and per-arm time went 20 min -> 2,295 min
# (115x). Contention here is far worse than queueing, so they run one at a time.
set -u
cd "$(dirname "$0")/.."
LOG=/tmp/min_bundle_sweep.log
for n in 1 2 3; do
  print -r -- "=== mb$n START $(date -u +%FT%TZ) ===" >> "$LOG"
  .venv/bin/python scripts/backtest_gdelt.py \
      --start 2023-08-11 --end 2026-07-26 \
      --corpus data/backtest_3yr_v5 \
      --out "data/cbt_3yr_mb$n" \
      --min-bundle-articles "$n" \
      --decisions >> "$LOG" 2>&1
  print -r -- "=== mb$n DONE rc=$? $(date -u +%FT%TZ) ===" >> "$LOG"
done
print -r -- "=== SWEEP COMPLETE $(date -u +%FT%TZ) ===" >> "$LOG"
