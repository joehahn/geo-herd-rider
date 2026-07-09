# TODO — backlog (not yet scoped into a scoreboard-gated step)

Actionable ideas parked here until promoted into a scoreboard-gated step. See
[`CLAUDE.md`](CLAUDE.md) for the rules and [`README.md`](README.md) for the current design.

## Current plan — ordered (2026-07-07, soonest first)

1. **Review agent-conviction mechanics** — verify conviction assignment + the max_agents / spy-floor ranking do what we think; never leaks into sizing.
2. **GDELT → BigQuery / GKG migration** *(ON HOLD 2026-07-09)* — **recall** (themes/tone close the keyword-synonym gap) + reliability. NOT a clean-backtest speed win: GKG selects URLs but carries no article text, so Wayback is still the fetch bottleneck; real speed only on the forward (live fetch). Revisit when recall is the priority.
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

## GDELT → BigQuery / GKG migration — recall + reliability (ON HOLD as of 2026-07-09)

The GDELT **DOC API** is our firehose retrieval, and it's a **triple** bottleneck. Migrating the
retrieval layer to **Google BigQuery** (the GDELT dataset, incl. the **GKG** Global Knowledge Graph)
helps with recall + reliability. This is a candidate before the 114-week full run.

**REFINEMENT (2026-07-09) — and why it's ON HOLD:** GKG carries **metadata only** (themes/tone/entities
+ URL), **NOT article text/title** — GDELT doesn't redistribute article text (copyright); it points back
to the source. So GKG is a **selection/recall layer, not a text source**: you still fetch the text from
**Wayback** (as-of, clean, slow) for the backtest or the **live URL** (fast, edit-risk) for the forward —
the *same* `--enrich wayback/live` step we already have. **Consequence:** GKG's *speed* benefit is only on
*selection* (BigQuery is fast) and the *forward's* live fetch; the **clean-backtest text-fetch (Wayback)
bottleneck stays**, and GKG even *adds* load (Wayback must now supply title AND lede, where the DOC API
gave titles free). So GKG's real value is **recall** (topic-semantic, catches synonyms) + **reliability**,
NOT skipping the slow as-of fetch. Deferred to the forward phase / when recall is the priority. Also gated
on a GCP project + billing + auth (none set up). *The speed framing below is thus overstated — it's the
selection that's fast, not the end-to-end clean backtest.*

**1. Speed — the actual wall.** The DOC API rate-limits hard (bursts of 429s); a **2-month, 30-beat
pool build takes ~4–5h** of throttled requests (measured 2026-07-09, the BWET playtest, at 0.6–1.1
query-chunks/min). The **114-week full run is effectively unworkable** on it. BigQuery scans the same
window in **minutes, no per-request throttle**. *(Correction to the old note here: rate-limiting turned
out to very much BE our problem at scale.)*

**2. Recall — the keyword ceiling.** The DOC API is lexical. Two lessons from 2026-07-09:
  - **Exact quoted phrases return ~nothing** — `"robotics stocks"` got 0 articles in 7 of 8 weeks. A
    SPACE is GDELT's implicit **AND**, so unquoted `robotics stocks` returns ~10× more (fixed in
    `firehose.GDELT_QUERIES`, commit b0f2c08). Exposes how brittle keyword retrieval is.
  - Even unquoted AND **can't match synonyms/paraphrases** — an "overlooked **automation** ETF quietly
    doubled" slips past `robotics stocks`. You can't enumerate all vocabulary.
  **GKG closes this:** filter by extracted **themes** (topic — catches automation≈robotics), **entities**
  (companies), and **tone** (sentiment ≈ mover). That's **topic-semantic retrieval**, not keyword — the
  synonym gap closes and recall rises.

**3. Reliability.** DOC API has had full outages (http=000 for 10h+ on 2026-06-30/07-01, blocked the GDX
cold scan); GDELT is mid-migration to Spanner (latency/interruptions); the DOC API officially supports
only ~3 recent months (older data via enforced date bounds may degrade).

**Cost.** BigQuery on-demand is **$6.25/TB scanned, first 1 TB/month FREE**. A 2-month GKG extract lands
in **~$0–15 one-time** (column-pruned / a date-partitioned copy → free-tier; worst case ~$15 scanning the
big theme/tone columns once), cached locally. The **curator LLM cost is unchanged (~$6–8/run)** — the
retrieval source doesn't change what the scout reads-and-judges. So the migration buys **speed + recall
at ~flat cost**.

**Design work (the "recipe"):**
  - A `gdelt.py` alternate fetch path that queries `gdelt-bq.gdeltv2.gkg` per week (still date-indexed →
    look-ahead-clean), narrowing the huge `ECON_STOCKMARKET` theme by **catalyst/sector themes + tone +
    entity-salience** → a curated pool (the stock-market theme alone = the whole market firehose, too big).
  - GCP project + billing + auth (creds in `.env`). Keep the DOC API as the cheap/no-key default for
    recent windows / the forward.
  - **Unchanged:** still by-week; the scout still makes the "under-the-radar / still-early / thesis-driven"
    call — GKG tags topic + tone, **not** that nuance (retrieve-broad → LLM-judge, same shape as now).

**Semantic ceiling (scoping).** GKG is **topic-semantic** (theme/entity codes), NOT embedding-semantic.
True natural-language semantic search *as an API* (no local embeddings) means **Exa** — but that's live
web, so it look-ahead-leaks like Tavily/Brave → **forward-only, not the clean backtest**. So: **GKG = the
clean topic-semantic ceiling for the backtest; Exa = a true-semantic lever for the forward.**

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
