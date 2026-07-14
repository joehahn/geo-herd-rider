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

## Add a crypto / bitcoin-miner beat (parked 2026-07-14; needs a full re-ingest)

CIFR (Cipher Mining — the bitcoin-miner→AI-datacenter pivot, 13.9× in 2026) recalls only **16% (5/31)**;
crypto miners are thinly covered by the current 46 beats. Add a crypto/bitcoin-miner beat (e.g.
`"bitcoin miner hashrate AI datacenter stock"` + a coverage variant) to `forward_gather.GEM_BEATS` /
`COVERAGE_BEATS`. **Batch it with any OTHER beat tweaks** — a new beat only takes effect after a full
~$14 Tavily re-ingest (the sweep resume is per-anchor, so there's no cheap delta), so don't re-sweep for
this alone. It ALSO helps the live forward immediately (fresh gather, no re-ingest needed).

## Curator simplification: LLM agent-picker replaces conviction/gates/aging (2026-07-14, forward-test candidate)

Backtest (`scripts/proto_select.py`, post-hoc replay over `firehose_scans_full.json`) settled the redesign:
**drop `conviction` + all gates (`momentum_gate`, `rvol_gate`) + `aging`; KEEP event-agents + milestones +
catalyst + exits; replace the conviction-ranked `max_agents` cull with a weekly LLM agent-picker** that reads
each live event's catalyst/milestones/exit/weeks-alive/cumulative-P&L and emits an ORDERED KEEP-LIST only
(no numbers to the optimizer — #1-safe; sizing stays mechanical). Ranks on catalyst ARC (favor early/building,
demote crested/near-resolution, reserve slots for fresh events), never predicted $.

Evidence: conviction ≈ random (worthless); deepseek picker 23rd %ile (worse than random); **sonnet5 picker
83rd %ile, +162%, funded the real winners (MU/MP)** — model quality is the whole story. BUT one backtest
window + LLM training-contamination (sonnet5 trained on 2025–26 may recognize memorized winners) ⇒ **the
forward paper trade is the only clean test** (see [[agent-picker-findings]]). Picker prompt + scoring harness
live in `scripts/proto_select.py`; responses cached in `data/windows/picker_cache.json`.

**IMPLEMENTED 2026-07-14 (verified free):** `src/picker.py` (portfolio picker, prompt + model-specific cache);
`firehose.backtest(..., picker=)` opt-in pluggable cull — legacy path byte-identical (MP still +120.6%), picker
path stub-tested (SPY+GLD appended post-cull, 6 metadata fields fed); `optimizer` defaults `max_events` +
`picker_model`; `agent.scout`/`process_week` `max_events` knob (rename of CANDIDATE_CAP, default 3 = unchanged);
`forward_engine` logs milestones+exit; `forward.py --report` builds+passes the picker (NaN-safe on old logs);
both profiles carry `max_events`+`picker_model`. SPY/GLD dropped as competing agents (appended post-cull).

**REMAINING (behavior change → needs a paid curator re-run / forward run to validate):**
1. relax the scout prompt's self-limit ("rarely more than 2") so it discovers freely, then replace the
   take-first-N inflow cull with a mechanical **diversity/novelty tiebreak** (needs a theme classifier moved into
   `src/`). Flagged in the `agent.py` CANDIDATE_CAP comment.
2. **Run it forward** (`forward.py --scan` weekly on post-cutoff weeks, `--report` for the picker cull) — the
   clean test where memorization can't help. Acceptance bar = the random-percentile-vs-sub-windows scoreboard.
3. Optional: dedup `scripts/proto_select.py`'s picker copy to import from `src/picker.py` (one prompt source).

## Delete the RVOL gate if we don't need it later (turned OFF 2026-07-14)

`rvol_gate` (breakout volume co-confirmation: fund a name only if recent volume ≥ Nx its 20-day avg) is
now **OFF** (`rvol_gate: 0.0` in `investor_profile.backtest.md`). It tested as a win on the whole-era book
(+$79K MP) but was **overfit**: on the per-gem thematic books it evicts the climbing gem from its own
dashboard (volume fades faster than price → the gem fails the 1.5× test at most rebalances → capital parks
in peers/SPY; MP capture 28%→59% with it off). Kept inert (guarded by `if rvol_gate > 0`, no runtime cost)
in case it earns its keep at a **lower/adaptive threshold** or **as an exit** rather than an entry gate.
**If it stays unused, DELETE the code** — `_rvol` + the gate application (`firehose.py`),
`fetch_volume_panel` (`score.py`), the volume-panel plumbing, and the profile knob. **Batch this with the
other deferred dead-knob deletions** (`trailing_stop_pct`, `prune_zero_weight_weeks`) — one deliberate
cleanup pass, not piecemeal.

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

## Curator memory upgrade (exit_advice + conviction + milestones) — IMPLEMENTED 2026-07-09, VALIDATION PENDING

Fixed a class of bug: the weekly agent (`EVENT_AGENT_SYSTEM` + `event_agent_v2`) stored fields it never
fed back to itself, so its own prompt rules couldn't use them. `_journal_digest` (the agent's memory)
only surfaced `date/live/vehicles/assessment`. Three fixes, all landing on the **next** curator run
(the current pull already imported the old `agent.py`; prelim data shows empty milestones → "—"):

- **exit_advice** — was pure DB/display text (not a gate — `thesis_live` drives the mechanical exit — AND
  not in memory). Now the prompt makes it a STANDING exit condition ("exit if/when …", never "Hold",
  revisable as the catalyst arc moves) and `_journal_digest` carries it forward → the agent re-reads and
  tests its own trigger each week.
- **conviction** — SILENCE-DECAY says "step DOWN 1 from your PRIOR score", but the digest didn't show prior
  conviction. Now `conv=N` is in every memory line.
- **milestones** — NEW field (`EVENT_AGENT_SCHEMA` + `JournalEntry` + prompt + digest trail + picks +
  Plot-12 "milestones" column): an ordered list of the catalyst's concrete progress events, carried
  forward and appended weekly — the evidence trail behind conviction/exit.

Guardrail verified intact (non-negotiable #1: a `price_target` key is still silently dropped by the model).
**Still to do:** milestones ADDS a required output field (bigger perturbation surface than the memory-only
tweaks), so on the next run **sanity-check the live/exit + conviction calls stay sound**. Ablation note:
all stages of the 3-stage retrieval playtest must share ONE curator version — when Stages 2/3 run, re-curate
Stage 1 on the new `agent.py` (`--no-pull`, ~$4) so they match.

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
