# TODO — backlog (not yet scoped into a scoreboard-gated step)

Actionable ideas parked here until promoted into a scoreboard-gated step. See
[`CLAUDE.md`](CLAUDE.md) for the rules and [`README.md`](README.md) for the current design.

## Current plan — ordered (2026-07-07, soonest first)

1. **Review agent-conviction mechanics** — verify conviction assignment + the max_agents / spy-floor ranking do what we think; never leaks into sizing.
2. **GDELT → BigQuery migration** — kill the cold-scan hangs.
3. **Single data pull 2024 → end-of-BWET era** (after BigQuery) — replace the overlapping-scan hodgepodge.
4. **Pivot to forward testing (LAST)** — the only clean scoreboard (`forward.py`); run it after the infra is solid.

**Done:** label seeds synthetic, review GDX, GDX seeding + analysis (locked as negative control), review RNMBY, news-derived seeds (all gems, P3), 1-ticker-vs-many-agent A/B (P4), README + diagrams refresh, delete unused exit knobs, Sonnet-5 eval (now default), 7-model bake-off, SPY-as-idle-holding.

**Dropped (not must-have; revisit only if forward proves out):** regime-contrast study, seedless backtest v1, structural-graph curator features, telegraphers/influencers roster, Fable-5 eval, resolved-catalyst-ledger windowing.

## Window the resolved-catalyst ledger fed to the scout (not urgent)

The scout is told which catalysts have RESOLVED so it won't re-chase the hype (the `retired` ledger in
`run_event_agent_scans`, injected into the scout's weekly prompt). Today that ledger is **cumulative and
never expires** — fine for a short backtest, but over a long/forward run it (a) bloats the scout prompt and
(b) permanently bars a ticker whose thesis genuinely re-emerges much later on a *new* shock.

- **Window it:** keep only recent retirements — either **time-based** (last ~2–4 months) or **count-based**
  (last N resolved agents). Time-based is cleaner for forward operation; count-based bounds prompt size.
- **Tradeoff to tune (scoreboard):** too short → a ticker can re-hop right after the window closes (the ev6
  failure returns); too long → prompt clutter + legit re-entries blocked. Make the window a profile knob and
  sweep it once the resolved-catalyst guard itself is validated.
- Depends on the resolved-catalyst scout guard proving out first (currently under test).

## Standing risks (carried from the retired SPEC)

Deep ladders are seductive storytelling; public events get priced fast; survivorship bias is
everywhere; the herd is faster than it looks; and a retrospective backtest cannot prove a forward
edge (every historical number here is an upper bound — the forward eval is the only clean test).
The design is meant to fail loudly and cheaply when a rung doesn't pay.

## GDELT reliability — BigQuery as a production-grade source (not urgent)

The GDELT DOC API is our firehose retrieval, and it's proven flaky: degraded CDX / archive.org
stalls during cold scans, and a full doc-API outage (api.gdeltproject.org → http=000 for 10h+ on
2026-06-30/07-01) that blocked the GDX cold scan entirely. Per GDELT: they're mid-migration to
Spanner (frequent latency/interruptions); the DOC API *officially* supports only the most recent
~3 months (we get older data via enforced `startdatetime`/`enddatetime`, which has worked but may
degrade for old weeks during the migration); and it locks up above ~1 req/5s (we already throttle
at 15s in `src/gdelt.py`, so rate-limiting is not our problem).

- **The robust fix:** pull GDELT from **Google BigQuery** (the full dataset, production-grade,
  no 3-month limit, no per-request throttle) instead of the DOC API — for cold historical pool
  builds especially. Would need a `gdelt.py` alternate fetch path + GCP creds; keep the DOC API as
  the cheap/no-key default for recent windows.
- **Until then:** cold scans depend on the DOC API being up; the auto-resume poll handles transient
  outages. Warm caches are unaffected (retrieval is cache-hit, no API).

## Maturity tag as an entry/exit gate (does framing add lift?)

We removed the per-event **maturity tag** (`early | building | consensus | crested`) from the
pipeline — it was emitted by the curator but read by nothing (purely diagnostic), so it was dead
weight that invited "does it drive the trade?" confusion. Park the *idea* here: it may be worth
re-introducing **only if** it earns its keep as an actual gate.

The hypothesis: a gem still framed *under-the-radar / early* sits nearer the smart money than the
herd, so **gating entry on `early`** (and/or **exiting on `crested`**) could lift returns — or it
could cost us gems we only ever discover already-mainstream. Today entry fires on *press-named +
live thesis* and exit on *catalyst resolution* (`thesis_live`); the tag would be a new, separate
signal layered on top.

- **How to test:** re-add the tag as a curator output (one extra field, no extra LLM call), then
  A/B on the multi-gem harness — baseline (no gate) vs. `early`-gated entry vs. `crested`-triggered
  exit — on recall / precision / tail. Keep it **diagnostic until the scoreboard shows lift**.
- **Pre-register the bar** (which gate, what excess-vs-prior-config threshold) before running, so it
  can't be tuned to the data (CLAUDE.md #5). If no lift, leave it deleted.
- **Where it'd live in forward** (the reason to persist it, not just regenerate it per backtest): in
  forward operation you can't replay history, so the tag would need to be logged at decision time —
  another reason to defer until the early-gating question is actually on deck.
- Was previously documented as the README "open knob"; moved here when the tag was stripped.
