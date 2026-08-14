#!/bin/zsh
# Supervise the wayback lede backfill so it actually finishes.
#
# WHY. The backfill is an ~11-hour unattended archive.org crawl and it has now died three separate
# times mid-run -- twice by stalling at 0% CPU while still looking alive, once by vanishing outright
# (18,120/25,971, no traceback: launched with `nohup &` from a shell session that was later reaped
# with its process group). Every death was silent, and a log whose last line is a plausible progress
# update reads exactly like a job still running.
#
# The fix is not cleverness, it is a supervisor plus setsid. Resumption is FREE because the pass
# checkpoints wayback_cache.json every 20 URLs, so a restart is all cache hits up to the interruption.
#
#   setsid  -- detach from this session's process group, so ending the session cannot reap it
#   restart -- on ANY exit, until the pass reports it has nothing left to fetch
#   stall   -- if the log stops growing for STALL_MIN minutes, kill and restart it
#
# usage: scripts/wayback_supervisor.sh [corpus_dir] [log]
set -u
CORPUS=${1:-data/backtest_3yr}
LOG=${2:-/tmp/wayback_supervised.log}
STALL_MIN=20
cd "$(dirname "$0")/.."
for attempt in {1..40}; do
  print -r -- "=== attempt $attempt $(date -u +%FT%TZ) ===" >> "$LOG"
  # ingest.py is the corpus tool. NOT backfill_gdelt.py -- that one ignores its arguments, runs the
  # FORWARD pipeline, and bills BigQuery scans (~4.5 GB per weekly chunk) no matter what you pass it.
  # NO --misses-only: that pass is DONE (its 16,881 leftovers are confirmed-unarchived, not unfetched).
  # This pass targets the 81,523 articles whose text came from a LIVE page fetched today -- look-ahead
  # risk, since the page may have been edited since publication. An as-of snapshot replaces it.
  .venv/bin/python scripts/ingest.py --out "$CORPUS" --no-discover --wayback --gentle >> "$LOG" 2>&1 &
  pid=$!
  # watch for a stalled log while the child runs
  while kill -0 $pid 2>/dev/null; do
    sleep 60
    age=$(( $(date +%s) - $(stat -f %m "$LOG") ))
    if (( age > STALL_MIN * 60 )); then
      print -r -- "!! log stalled ${age}s -- killing $pid and restarting" >> "$LOG"
      kill -9 $pid 2>/dev/null
      break
    fi
  done
  wait $pid 2>/dev/null
  # done = the pass exited 0 with nothing left needing a fetch
  if tail -40 "$LOG" | grep -q "0 newly fetched\|no articles need\|WAYBACK: done"; then
    print -r -- "=== COMPLETE $(date -u +%FT%TZ) ===" >> "$LOG"; exit 0
  fi
  sleep 30
done
print -r -- "=== gave up after 40 attempts ===" >> "$LOG"
