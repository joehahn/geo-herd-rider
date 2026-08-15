#!/bin/zsh
# max_events sweep: one FULL re-curation per value (this knob is a CURATION knob, so unlike
# sweep_optimizer.py it cannot be replayed off a fixed book -- it costs LLM calls).
# Runs SEQUENTIALLY on purpose: concurrent runs would interleave writes into data/llm_costs.csv.
cd /Users/joehahn/Library/CloudStorage/Dropbox/prog/claude/geo-herd-rider
set_me () {
  .venv/bin/python -c "
import re; p='investor_profile.backtest.md'; s=open(p).read()
open(p,'w').write(re.sub(r'^max_events: \d+', 'max_events: $1', s, count=1, flags=re.M))"
}
for N in 4 8 12 16 20; do
  OUT=data/cbt_3yr_me$N
  set_me $N
  echo "=== max_events=$N -> $OUT  ($(date +%H:%M)) ==="
  rm -rf $OUT
  .venv/bin/python scripts/backtest_gdelt.py --start 2023-08-11 --end 2026-08-09 \
    --out $OUT --corpus data/backtest_3yr_v4 --no-pull --decisions --workers 24 2>&1 | tail -2
done
set_me 0            # restore the nominal
echo "=== MAX_EVENTS SWEEP DONE ($(date +%H:%M)); profile restored to max_events: 0 ==="
