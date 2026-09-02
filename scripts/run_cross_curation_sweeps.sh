#!/bin/zsh
# Re-sweep the 13 distinct curations on ONE grid generation and ONE code generation, so
# scripts/cross_curation.py can rank cells across them.
#
# WHY ALL 13, INCLUDING ONES SWEPT TODAY: the aggregation ranks cells WITHIN each curation and
# averages the ranks, so every sweep must have been produced by the same book code. Mixing
# generations makes the average a comparison of code, not of config. Three changes on 2026-08-31/
# 09-01 (the min_trade_size/concentration_cap sizing fix, the liquidity + death-spiral gates, and
# resolved-catalysts-may-keep-but-not-open) moved every cell, so the whole set is re-run.
#
# PREREQUISITE, learned the hard way 2026-09-01: run scripts/freeze_gate_caches.py FIRST. When a run
# has no frozen corpactions.json/volume.csv, all 10 sweep workers fetch it from yfinance at once, get
# rate-limited, and each ends up with a different partial view -- so the death-spiral and liquidity
# gates fail open on different tickers in different workers. Six of these 13 runs were carrying caches
# with 47-100% of tickers unevaluable before that script existed.
#
# ZERO LLM COST -- the curations are fixed on disk and each sweep replays the frozen panel.csv.
#   ~5,040 cells x 13 curations, sequential; expect several hours.
#
#   scripts/run_cross_curation_sweeps.sh 2>&1 | tee /tmp/xc.log
set -e
cd "$(dirname "$0")/.."
RUNS=(bw21 mb1 mb2rep v9 v18 v19 v20_catalystkey v21_evscans12 v22_resolver v23_silence \
      v24_wirelede v25_vehgate wk14)
for r in $RUNS; do
  out="data/sweep_xc_${r}.json"
  if [ -f "$out" ]; then echo "== skip $r (have $out)"; continue; fi
  echo "== $(date '+%H:%M:%S') sweeping $r"
  .venv/bin/python scripts/sweep_optimizer.py --run "data/cbt_3yr_${r}" --out "$out"
done
echo "== $(date '+%H:%M:%S') done"
