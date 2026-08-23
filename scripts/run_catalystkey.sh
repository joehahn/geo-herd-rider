#!/bin/zsh
# ONE re-curation testing the catalyst-keyed retired guard (2026-08-22).
#
# NEVER EDIT THIS FILE WHILE IT IS RUNNING. zsh reads a script by BYTE OFFSET, so an edit mid-run
# makes it execute fragments of the new text ("ython ...") and exit 1. That happened on 2026-08-21.
#
# Identical to the v18 invocation in every argument -- same corpus, same window, same profile. The
# ONLY difference is src/agent.py's retired-ticker guard, which is why provenance.py now stamps a
# curator code digest: the two runs fingerprint identically on config by construction.
# --workers 6, NOT the default 24. The first launch died at scan 21/37 on an OpenRouter 402
# `in_flight_budget_exhausted` -- OpenRouter sizes the CONCURRENT in-flight spend cap off remaining
# credit ($26.90 at the time), so 24 parallel event-agent calls exceeded it even though the total
# cost of the run is only ~$7. Not a rate limit and not a code fault; the retry ladder (5 attempts)
# exhausted itself against a condition that needed FEWER workers, not more waiting.
# Re-running is safe and cheap: backtest_gdelt.py RESUMES (reloads the journal, skips the 21 scans
# already in <out>/archive), so this picks up at scan 22.
set -u
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/backtest_gdelt.py \
  --start 2023-08-11 --end 2026-07-26 \
  --corpus data/backtest_3yr_v5 \
  --out data/cbt_3yr_v20_catalystkey \
  --workers 6 \
  --decisions
