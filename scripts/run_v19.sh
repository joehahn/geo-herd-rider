#!/bin/zsh
# ARM: AGENT_SYSTEM now says the FIRST read of an event is an ENTRY decision, not an exit one.
# WHY, measured on v18: a vehicle enters the book only when first read thesis_live=True, and 193 of
# 374 events were killed on read 1 (93% of those with catalyst_resolved=True). Of the killed
# vehicles, the 116 whose catalyst NAMED a specific event went on to +8.9% over six months --
# statistically like the +11.8% of events the agent KEPT -- while the 308 with a vague catalyst went
# -1.3%. So the kill rule is right on average and wrong on the specific-catalyst tail, and the cause
# is a prompt collision: the exit rule ("flip FALSE the week the catalyst resolves") fires on a first
# read, where the entry rule should govern.
# JUDGE ON MECHANISM: one-read share, entries already up >50%, forward return of newly-admitted
# vehicles. NOT on P&L -- same-config curations disagree as much as different-config ones.
set -u
cd "$(dirname "$0")/.."
LOG=/tmp/v19.log
print -r -- "=== curate START $(date -u +%FT%TZ) ===" >> "$LOG"
.venv/bin/python scripts/backtest_gdelt.py \
    --start 2023-08-11 --end 2026-07-26 --corpus data/backtest_3yr_v5 \
    --out data/cbt_3yr_v19 --decisions >> "$LOG" 2>&1
print -r -- "=== curate DONE rc=$? $(date -u +%FT%TZ) ===" >> "$LOG"
.venv/bin/python scripts/sweep_optimizer.py --run data/cbt_3yr_v19 --panel-only >> "$LOG" 2>&1
print -r -- "=== ALL COMPLETE $(date -u +%FT%TZ) ===" >> "$LOG"
