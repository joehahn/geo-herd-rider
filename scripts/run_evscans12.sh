#!/bin/zsh
# ONE curation at max_event_scans=12, everything else canonical (2026-08-22).
#
# NEVER EDIT WHILE RUNNING -- zsh reads by byte offset; a mid-run edit executes fragments.
#
# WHY. Measured on 5 curations: at the profile's max_event_scans=6, 55-57% of events die pinned at
# EXACTLY the cap -- the span histogram is a wall (1:25 2:14 3:15 4:7 5:14 6:99). The timer, not the
# thesis, ends the median event. At 12 only 14-17% hit it and the distribution decays naturally
# (reproduced across cbt_3yr_mb1/mb2/mb3). BE, BKSY, INTC, IREN and CORZ were all killed by this
# timer, not by an exit judgement.
#
# WHY NOT JUST USE mb1, WHICH ALREADY RAN AT 12. It also sets max_events=16, a cap on CONCURRENT live
# events, and that confound dominates: mb1 opened 96 events against v18's 182 and its target
# live-weeks FELL 79 -> 52. This arm varies max_event_scans ALONE.
#
# BASELINE IS v20_catalystkey, NOT v18 -- src/agent.py currently carries the catalyst-keyed retired
# guard, so v18 would differ in two things at once. v20 has the same code at cap 6.
#
# --workers 24 (the default) is safe again now that OpenRouter credit auto-re-ups at $25; the
# 2026-08-22 402 `in_flight_budget_exhausted` was a low-balance in-flight cap, not a rate limit.
set -u
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/backtest_gdelt.py \
  --start 2023-08-11 --end 2026-07-26 \
  --corpus data/backtest_3yr_v5 \
  --out data/cbt_3yr_v21_evscans12 \
  --max-event-scans 12 \
  --workers 24 \
  --decisions
