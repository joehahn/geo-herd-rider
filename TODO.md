# TODO — backlog (not yet scoped into a scoreboard-gated step)

Actionable ideas parked here until promoted into a scoreboard-gated step. See
[`CLAUDE.md`](CLAUDE.md) for the rules and [`README.md`](README.md) for the current design.

## Current plan — ordered (2026-07-07, soonest first)

1. **Review agent-conviction mechanics** — verify conviction assignment + the max_agents / spy-floor ranking do what we think; never leaks into sizing.
2. ~~**GDELT → BigQuery / GKG migration**~~ — **DONE 2026-08-08/09**, see below. `src/gkg.py`, own GCP project, 1-year corpus ingested.
3. ~~**Single data pull**~~ — **DONE**: one clean 1-year pull, `data/backtest_1yr/` (2025-07-04 → 2026-07-03, chunk-aligned so a 3-year extension reuses it).
4. **Curator half — breadth/diversity instrumentation + a real backtest run.** THE CURRENT BLOCKER. The retriever is measured end to end; the curator is not. Nothing about "is the backtest top-notch?" is answerable until this exists, and it also gates the `no_org`/`no_beat` relaxation A/B.
5. **Pivot to forward testing (LAST)** — the only clean scoreboard (`forward.py`); run it after the infra is solid.

**Done:** label seeds synthetic, review GDX, GDX seeding + analysis (locked as negative control), review RNMBY, news-derived seeds (all gems, P3), 1-ticker-vs-many-agent A/B (P4), README + diagrams refresh, delete unused exit knobs, Sonnet-5 eval (now default), 7-model bake-off, SPY-as-idle-holding.

**Dropped (not must-have; revisit only if forward proves out):** regime-contrast study, seedless backtest v1, structural-graph curator features, telegraphers/influencers roster, Fable-5 eval, resolved-catalyst-ledger windowing.

## DONE 2026-08-14 — Decouple the news window from the rebalance cadence: `news_lookback_days`

**Shipped in c0c8dda.** `optimizer` had documented this as live behaviour all along, but only
`firehose.py` (the backtest) implemented it; `forward.py` built its pool window from the cadence, so
setting the knob did nothing. Now honoured on both sides, with a `--lookback-days` override mirroring
the backtest's. Forward profile runs `news_lookback_days: 30` with `rebalance_period: weekly`, and
`news_cap` went 500 -> 0 in the same change because the cap truncated the new 30-day window back to
~5 days. Original note follows.

### (superseded) original note, parked 2026-08-11

Today one knob does both jobs: `backtest_gdelt.py:212` builds the scan's pool as
`anch - Timedelta(days=cadence) .. anch`, so the trailing news window IS `rebalance_period`. PWR carries a
separate `news_lookback_days: 14` for exactly this reason.

Not added yet because the coupling guarantees window == cadence, so there is never a blind stretch of news,
and a knob with no demonstrated job cuts against the ongoing effort to shrink the profile. The case that
would justify it is **`news_lookback_days` > `rebalance_period`** — a deliberate OVERLAP, so an article
GDELT indexes late (or one published right on a scan boundary) still gets read on the next scan instead of
falling in the gap. That is a real retrieval hole, just not one we have measured yet.

If adopted: add to `optimizer._FINANCIAL_MODEL_DEFAULTS` (else `load_financial_model` silently drops it),
default `0` = follow the cadence, and let the matcher's existing dedup absorb the re-read articles.

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

## Delete old data + dbs (2026-07-14)

The new curator+picker pipeline is being rebuilt from scratch (first a Q1-2025 single-db proof). Once the
new dbs are trusted, DELETE the stale artifacts from the old (gates/conviction/aging) pipeline so the repo
isn't cluttered/misleading:
- `data/windows/firehose_scans_*.json` per-gem slices + `firehose_scans_full.json*` (old 79-week run; backed
  up to `*.79wk.bak.json`), the `.journal.json`, `picker_cache.json`, `picker_decisions.jsonl`, `*.log`.
- old per-gem dashboards under `docs/` (mp, mu, nem, gdx, hl, bwet_curator, rnmby, cifr, tsm, intc, other) if
  the theme-keyed set is superseded by the new single/rebuilt dbs.
- stale `data/` outputs: old `retrieval_backtest*.json` checkpoints, `gem_ground_truth`/`gt_forward_reachable`
  if regenerated. Keep fixtures (`gems.json`, seeds) + the retrieval pool ckpt (expensive to rebuild).
Do a careful pass — don't delete the retrieval pool (`data/retrieval_backtest.ckpt.json`, ~$14 to rebuild).

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

## Reconcile the two notions of "which outlets matter" (parked 2026-08-07, do when convenient)

GHR has TWO disconnected source-authority stores, and the weaker one is the user-facing file:

- `news_sources.md` -- prose only. `util.news_domains()` regex-scrapes EVERY url out of it into one
  flat, undifferentiated preferred-domain list (`firehose.py:318`, `forward.py`). No tiers, no
  block list, no rationale carried into code.
- `investor_profile.{backtest,forward}.md` -- `specialty_allow` (the GEM pass allowlist) and
  `mill_block` (the COVERAGE pass blocklist). This is the list that actually steers retrieval, and
  the one the 2026-08-07 mill A/B edited.

PWR solved this by giving its `news_sources.md` YAML front matter (`source_block`, `source_major`)
plus a prose specialty section, parsed into THREE authority tiers shared by backtest and forward
(`corpus.source_tier`, `gkg_pool.authority`). GHR should land somewhere similar: ONE user-editable
source file with machine-readable tiers, and the profile keeping only the knobs.

Not blocking: the profile lists are correct and in use today; `news_domains()` is a soft preference.
Do it alongside phase 2 (forward usage), where the tiers actually get exercised.

## Retrieval filters — settled 2026-08-09, do not re-litigate without new evidence

Five funnel filters were audited with a blind strong-model judge (`scripts/audit_filter.py` replays
the funnel over cached BigQuery rows for free; `scripts/judge_dropped.py` judges the drops, and its
`--stage corpus` mode judges what SURVIVED). Two method notes that cost real time:

- A cheap-screen -> strong-confirm tier measures the SCREEN's precision and is blind to its RECALL.
  Every tiered number was a LOWER BOUND (1.4-3.2x too optimistic). Use `--strong-only` on a smaller
  sample instead.
- An FP rate is meaningless alone. It only means something against the SIGNAL DENSITY of what the
  filter keeps. Computing that table first would have saved a long detour.

  population                    articles/yr   signal %   vs corpus
  CORPUS (kept)                      38,896     48.3%
  - SQL keyword gate              2,778,347     27.0%    -21.3 pts
  - no_beat                         130,069     40.0%     -8.3 pts
  - no_org                           80,026     43.3%     -5.0 pts
  - spam                              3,915      3.3%    -45.0 pts
  - blocklist                        37,155      6.0%    -42.3 pts

EVERY filter discards material with lower signal density than the corpus it feeds -- none is
inverted. Verdicts:

- blocklist, spam        KEEP. Strong enrichers, trivial volume. Nothing to do.
- SQL keyword gate       KEEP. Weakest enricher, but the only thing keeping the corpus tractable:
                         dropping it puts 1.83M tokens/week at the scout, over llama4's 1M CONTEXT
                         (cost is irrelevant -- it is ~$14/yr). Both replacement discriminators were
                         TESTED AND FAILED: GKG themes do not separate (best lift 30% vs 11%), and
                         subject-org gives only 59% recall at 32% precision. Do not retry these.
- url_recycled, syndication  KEEP. Fixed (16% FP) and a collapse, not a deletion.
- no_org, no_beat        RELAX AND TEST. The only two where the tradeoff is genuinely open (-5.0 and
                         -8.3 pts). Admitting both costs ~$1.40/yr and 0.24M context -- cheap enough
                         to just try. But whether it HELPS is a CURATOR question (does it surface
                         more, more diverse ticker-events?), not a retrieval one, so gate it behind a
                         `retrieval_relaxed` toggle and A/B it once the breadth instrumentation
                         exists. Diluting the pool 4x for the same events would be strictly worse.
- English-origin         AUDIT LATER. `TranslationInfo IS NULL` drops 88.5M rows/yr and is the
                         largest unexamined thing in the system. Probably correct for a US-listed
                         strategy, but that is an assumption, not a measurement. It is a SCOPE
                         decision, so answer it by deciding, not by sampling.

Dashboard: `docs/fbt.html` (Firehose Backtest), built by `scripts/build_fbt_dashboard.py`.
**DONE for now (2026-08-09)** — 12 panels + 8 verdict tiles; funnel carries each stage's measured
false-positive rate, so it reads as a QUALITY chart, not just a volume one. Revisit only after the
Curator Backtest (CBT) exists, since two deferred items need curator output to be worth building:
  - articles-per-ticker, best drawn as a JOIN (coverage per ticker x picked/not-picked), not a bare
    corpus histogram -- ticker extraction is the curator's job, and a regex proxy here would
    disagree with it invisibly
  - the `no_org` / `no_beat` relaxation A/B, which only a curator outcome can settle
Two small known gaps, deliberately left: no look-ahead-verified tile (checked manually -- 0 articles
fall outside the requested window), and no weekly-pool-size callout (median 748/week, p10 600,
min 158). Panel 3 (day of week) is the weakest panel and a drop candidate.
Named FIREHOSE, not 'retriever' -- that is PWR's word and appears 0 times in this repo;
`firehose` appears 117 times. A later Firehose Bootstrap / Forwardtest would mirror it.

Corpus state: 38,896 articles, 106.6/day, 93.5% with text (10.2% of it clean/archived), 18,435
bylines, 46/46 beats, 2,284 sources. ~52% of it is noise; the scout is the intended backstop.

## TESTED AND REJECTED 2026-08-14 — "does concentrating pay?"

Built as a CBT panel (concentration on day i vs the portfolio's next-30d return), then REMOVED the
same day when the regime control killed it. Recorded so nobody rebuilds it.

    portfolio fwd 30d              monthly corr +0.28   n=35   t=+1.70   (p~0.10)
    SPY fwd 30d (regime control)   monthly corr +0.28   n=35   t=+1.70
    EXCESS (portfolio - SPY)       monthly corr +0.23   n=35   t=+1.36   (p~0.18)

Concentration predicts SPY's forward return EXACTLY as well as the portfolio's -- identical to two
decimals. The panel was measuring market regime, not concentration: the curator happens to
concentrate in periods that precede rallies. Nothing is significant, and the buckets are not
monotonic either (25-50% -> +12.1%, 50-80% -> +6.0%, 80-100% -> +18.5%).

TWO METHOD LESSONS worth more than the result:
  - The first confound test (concentration vs PRIOR 30d return: +0.04) looked reassuring and was
    NOT sufficient. Ruling out reverse causality does not rule out a third variable driving both.
    Always control for the market before believing any timing relationship in this book.
  - A per-EVENT version was tried first -- peak share vs realised P&L, 36 points, corr -0.09 -- and
    showed nothing. That was the honest framing; the daily one only looked better because
    overlapping windows inflate n from 35 to 704.

If revisited on the bootstrap corpus: test against EXCESS return from the start, and treat monthly
means as the unit, not days.

## Beat economics + an evidence-provenance hole (parked 2026-08-14, revisit with the bootstrap dbs)

Measured on v15 / CBT plot 5. Deferred deliberately: it is ONE curation, and we watched the optimizer
config flip sign between v14 and v15, so do not prune six beats on a single run.

### 0. Scout PASS-2 leaks past-tense catalysts (~9%, measured 2026-08-14)
The scout prompt requires every candidate to name a `pending_next` -- "the concrete thing that has NOT
happened yet whose happening would END this thesis" -- and to DROP anything whose catalyst is already
past, because "a catalyst in the past tense is news the market has already priced." It leaks. Of 187
proposals in v15, ~16 (9%) read as past-tense only:

    "Q4 earnings beat" (x2) · "CHIPS Act is signed" (x2) · "Rezdiffra wins FDA approval"
    "Novartis acquisition announced" · "Piedmont Lithium gets mining permit approved"
    "promising test results announced"

A keyword test leaves 80% of proposals ambiguous, so 9% is a FLOOR, not a rate. The filter mostly
works -- this is not a broken gate -- but it is the thing setting breadth, more than any knob: proposals
are bimodal at exactly 1 (13 scans) or 5-11 (21 scans), and input volume does not explain the split
(median 117 gate-passed articles on the 1-scans vs 150 on the others).

FIX WHEN RE-CURATING (not standalone -- it needs a curation to evaluate): tighten the drop rule with
the observed failure phrasings ("earnings beat", "wins approval", "is signed", "announced",
"gets ... approved"), then re-curate and re-measure the same 9%. Do it in the same pass as the
conviction-free prompt so one curation validates both.

### 1. THE THING TO LOOK AT FIRST -- 27% of the book has no evidence
`NRXP` and `XENE` are the only 2 of 104 tickers that NEVER carried an evidence URL, and they are
**$62,739 of $235,776 (27%) of total book gain**. They come from ev64/ev65, biotech trial events whose
own journal entry reads "No news directly mentions Azetukalner or its Phase 3 trial." So the single
largest contributor to the backtest was held on an event with no traceable article behind it. 9% of all
journal entries (19 of 221) come from the mechanical silence path, which produces no sources by design.
This is also why "no beat" shows as the #2 earner in plot 5 -- it is an ATTRIBUTION hole, not a missing
beat. Understand this before tuning any beat.

### 2. Beat economics as measured (44 beats: 10 earn, 8 lose, 26 return exactly $0)
  best earning   uranium nuclear fuel supply squeeze  $77,231 / 2,097 arts   $36,829 per 1k
                 memory chip DRAM shortage            $22,701 /   490 arts   $46,329 per 1k  (most efficient)
                 space stocks                         $38,498 / 5,233 arts
                 upcoming FDA decision                $19,006 / 1,878 arts   -- KEEP, it earns
  worst per art  best performing ETF little-known    -$9,086 /    39 arts  -$232,975 per 1k
                 ^ 33 of its 39 articles pass the discovery gate (85%), so it aims a loss-making
                   firehose straight at the scout. USER'S CALL 2026-08-14: keep for now, re-examine
                   when we return to the bootstrap dashboards.
  big + zero     financial 5,472 · consumer 4,576 · real estate 4,382 · gold silver mining 3,149
                 (326 gated!) · rare earth critical minerals 2,681 · crypto blockchain ETF 1,498
  losing but KEEP  export ban tariff sanctions -$24,418 -- thematically core (war/tariff channel);
                   the loss is NVTS, one bad name, not a bad beat.

### 3. You CANNOT find missing beats by scanning the corpus
All 100,180 corpus articles carry a beat tag; zero carry none. The BigQuery keyword gate REQUIRES a
beat match to enter, so the corpus is beat-filtered by construction and scanning it can only
rediscover the beats we already have. Finding what we are missing needs the RECALL PROBE (re-run one
window with the keyword gate removed, judge what is new) -- the same experiment parked for FBT's
uncounted upstream filters. That is the only thing that can answer "what beat are we not running?".

## Retired parameters still present in the code (parked 2026-08-14)

The `rebalance_days` -> `rebalance_period` rename was done 2026-08-09 but never finished, and the
leftover cost real money in wrong numbers before anyone noticed. `optimizer` kept `rebalance_days: 7`
as a default, so `load_financial_model` INJECTED it into every profile; any code reading the raw key
got 7 for a monthly run whose scans are 30 days apart. Two CBT figures were silently wrong (a funnel
bar 5x too small, a watchlist span 4x too short) and nothing complained, because 7 is a plausible
cadence. **The lesson to carry: a retired knob that still resolves to a plausible number is more
dangerous than one that is absent.** Deleting the default and making `resolve_cadence` fail loud is
DONE (2026-08-14); what follows is the remaining cosmetic tail.

### 1. `rebalance_days` function parameters -> `cadence_days`
`agent.run_agent_scans/run_event_agent_scans`, `firehose.run_scans`, `forward.scan_and_log/_current_anchor`,
`forward_engine.run_week`. These carry an ALREADY-RESOLVED integer, not the retired config key, so
they are safe -- but the name now points at a knob that no longer exists, which is how a future reader
gets sent back to the raw key. Pure rename, touches every call site, no behaviour change.
KEEP the `--rebalance-days` CLI flags: a numeric escape hatch is legitimate on the command line, where
it cannot masquerade as configuration.

### 2. Finish the `lookback_period_days` -> `optimizer_lookback_days` rename
Renamed 2026-08-12, but `optimizer.load_financial_model` still reconciles the two names in lockstep so
old readers keep working (`optimizer.py:285`). Verified correct today -- the profile's 45 reaches
`firehose.py:576` -- so this is hygiene, not a bug. Finish it: move the ~3 readers
(`firehose.py:576`, `forward.py:89`, `scripts/sweep_optimizer.py` GRID) onto the new name and delete
both the alias and the reconciliation block. Note the sweep GRID key is part of every saved
`data/sweep_*.json`, so either migrate those or keep the sweep key as-is deliberately.

### 3. THE `0 = UNCAPPED` SENTINEL — four bugs in one day, sweep for the rest
Every one of these is the same shape: a knob whose 0 means "no limit" used directly in a comparison,
a slice, or a `or DEFAULT` fallback, so 0 silently behaves as ZERO or as a live limit. None raised.

  cap=0 in forward_gather.gather   `kept[:cap]` -> `kept[:0]` -> []. Returned NOTHING from the
                                   Anthropic engine on every daily pull for 32 DAYS. Fixed b68e9ed.
  news_cap=500 (not 0, but same
  family: a cap nobody re-checked)  truncated the new 30-day news window back to ~5 days, cutting
                                   scout intake 149 -> 38. Fixed c0c8dda.
  rebalance_days                   retired knob still injected as 7 by the optimizer defaults, so any
                                   raw read got 7 for a 30-day run. Two wrong CBT figures. Fixed 3c014d2.
  max_new_events=0 in CBT's tile   `proposed > max_new_events` with no zero-guard, so every scan
                                   scored as a cap hit: "35/37 weeks hit the cap" in CRITICAL red for
                                   a knob that is OFF. Fixed 140a0fd.

TO DO: grep every knob documented as "0 = uncapped/unlimited" (news_cap, max_new_events, max_events,
event_news_cap, news_lookback_days, cull_* ...) and check each READ SITE for the guard. The correct
form is `x[:n] if n else x` and `if n and value > n`, never the bare comparison or slice. Consider a
single helper (e.g. `util.cap(seq, n)`) so the guard cannot be forgotten at a new call site.
Worth doing as one pass: the class is proven to reach production silently, and three of the four above
produced plausible-looking numbers rather than errors.

### 4. Audit result, for whoever picks this up
All 42 knobs in `_FINANCIAL_MODEL_DEFAULTS` have a real reader as of 2026-08-14 -- there is no third
dead knob hiding. `trailing_stop_pct` and `prune_zero_weight_weeks` (deferred 2026-07-03) are already
gone. So this item is genuinely just the two renames above.

## Cleanup backlog — dead code and retired data (parked 2026-08-09)

Deliberately NOT done during the retrieval work: none of it moves the blocker (the curator half is
unmeasured), and one item is a real decision rather than hygiene. Do it in one pass, and get the
deletions confirmed before removing anything -- these are Dropbox-synced with a 6-month restore
window, not a git-tracked working tree.

### 1. The DOC-API vocabulary — a DECISION, not a deletion
`firehose._VEHICLE / _MOVERS / _EARLY / _SECTORS(15) / _CATALYSTS(5)` compose `GDELT_QUERIES`, 22
boolean strings. This is an ENTIRE PARALLEL retrieval vocabulary -- superlatives, early-framing,
sectors, catalysts -- and the live GKG path reads none of it; `retrieval_config.json` holds the
current version of all four concepts. Two vocabularies for one idea, and the dead one has the names
that sound most canonical (`_MOVERS`, `_EARLY`), which is exactly how it caused confusion.

BUT deleting it removes the `doc` engine entirely, since that vocabulary IS its query set. So decide
first: do we ever want a keyless fallback (no GCP key, no billing)? Three options:
  a) keep both, accept the duplication (status quo -- confusing, zero work)
  b) delete `_MOVERS/_EARLY/_SECTORS/_CATALYSTS/GDELT_QUERIES` + `retrieval_engine: doc` + the
     dispatcher's doc branch + the now-dead `queries=` parameter threaded through 5 call sites
     (firehose.news_pool, agent x2, backtest_gdelt x2, backfill_gdelt)
  c) derive the DOC queries FROM retrieval_config.json, so one vocabulary has two renderings --
     principled, and the same shape as the gkg/websearch split, but real work for an unused path
Recommendation: (b) unless the keyless fallback is genuinely wanted. Nothing has used `doc` since
the GKG migration.

### 2. Retired gem machinery — dead per the 2026-08-07 redirect
Gem CAPTURE was retired as a scoreboard (the gem CONCEPT remains, CLAUDE.md #2). Dead:
  scripts/gem_detect.py, scripts/build_ground_truth.py, scripts/gem_capture_readout.py,
  scripts/gt_healthcheck.py, scripts/refresh_gem_dashboards.py,
  data/gem_ground_truth.json, data/gt_forward_reachable.json,
  and the gem-detection half of scripts/retrieval_backtest.py (the Tavily sweep)
Confirm the list before deleting; some may still be referenced by docs/ pages.

### 3. Config/code separation — remaining leaks
`engine.english_only` was promoted to retrieval_config.json on 2026-08-09 (it was the worst offender:
the largest filter in the system, 88.5M rows/yr, as a bare SQL condition with no knob). Still
hard-coded, in rough order of how much judgement they encode:
  - `lede.title_consistent(min_overlap=2)`  -- the threshold whose mis-specification caused a 73.5%
    false-positive incident; a tuned judgement call sitting in code
  - `wayback._PSEUDO_AUTHORS` / `_WIRE_PUBLISHERS` -- editorial calls about which bylines are people
  - `gkg._CRYPTO_SYMS` -- which symbols are coins rather than listed vehicles (named-ticker rescue)
  - `gkg._PLURAL`, `gkg._ORG_SUFFIX_RE`, `wayback._MIN_LEDE/_MAX_LEDE` -- mechanical, fine in code
The first two are worth promoting; the rest are genuinely implementation detail.

### 4. Stale run dirs
data/backtest_bwet_h, data/backtest_bwet_v2, data/forward_proto, data/forward_2wk, data/windows/*
and docs/{mp,q1_2025_book,h1_2026_book} predate the GKG migration and the filter fixes, so every
number in them is from a corpus that no longer exists. Confirm before removing.

## CBT — Curator Backtest (BUILT 2026-08-09; awaiting a clean-text re-run)

**DONE**: 52-week run over data/backtest_1yr (~$6, ~25 min), dashboard at `docs/cbt.html` built by
`scripts/build_cbt_dashboard.py`. Both dashboards share `scripts/dash_nav.py` (README · Backtest
[Firehose/Curator/Sweeps] · Bootstrap · Forwardtest; unbuilt pages render as greyed text, not dead
links). Instrumentation added to backtest_gdelt.py: `--decisions` (picker_log: scout PROPOSED vs
ADMITTED) and a per-week `curator_metrics.json` funnel, with the CAPS recorded alongside so a reading
is attributable to the curator or to a knob.

FIRST RESULTS (live-text corpus, NOT quotable):
  52 weeks · 26 events opened · 7 live at end · 31 distinct tickers picked · 729 picks
  events live/week median 6 (max 9) · distinct catalysts/week median 6 (max 9)
  scout: 65 proposed, 60 admitted, cap-bound in only 2 of 52 weeks
  curator picked 10 of the 40 most-covered tickers

  -> BREADTH IS CURATOR-BOUND, not cap-bound. `max_new_events` bound 2 weeks in 52, so loosening it
     changes nothing -- and that also further weakens the deferred no_org/no_beat relaxation A/B: a
     scout proposing ~1.25 candidates/week from 750 articles will not propose more from 3,400.
  -> Events and catalysts move TOGETHER (both median 6), so the curator is not running six variants
     of one theme. That is real diversity.

PENDING: re-run once the Wayback backfill finishes (~92 h from 2026-08-09; per-article cutoffs, ~7/min).
Every article the curator cited in this run was LIVE-page text (1,929 live, 111 headline-only, 0
archived), so no number on the current CBT page is quotable under CLAUDE.md #4. The re-run is $6.

## CBT — original plan (kept for the record)

The curator half is entirely unmeasured: the only run ever made was a 3-week smoke test on the OLD
pool, before every retrieval fix. Until this exists, "is the backtest top-notch?" has no answer on
the curator side, and three deferred decisions stay blocked:
  - the `no_org` / `no_beat` relaxation A/B (retrieval metrics cannot settle it -- only whether the
    curator finds MORE and MORE DIVERSE ticker-events with the bigger pool)
  - articles-per-ticker as a JOIN (coverage per ticker x picked/not-picked)
  - whether 52% corpus noise actually costs anything, given the scout is the intended backstop

WHAT IT MEASURES (per the 2026-08-07 redirect -- NOT gem capture):
  - BREADTH: adds per curation, how many distinct events are live, how many survive to funding
  - DIVERSITY: are funded events spread across catalysts/sectors, or all one theme
  - CONVERSION: articles -> candidates -> events -> funded positions, the curator's own funnel
  - COST: $ per curation, per event, per funded position

ORDER OF WORK:
  1. 4-week pilot on data/backtest_1yr to measure real per-week cost before committing to 52 weeks
  2. breadth/diversity instrumentation (the measures above) -- build BEFORE the full run so the run
     produces something readable, rather than an equity curve nobody wants to steer by
  3. full 52-week run
  4. docs/cbt.html, mirroring the FBT's structure (verdict tiles + a conversion funnel)

FORWARD-USE COMES AFTER, and the FBT's findings say it should look much better: nearly every ceiling
the FBT exposed is an artefact of GKG, not of the strategy -- 12 of 21 specialty desks unreachable,
title-only matching (40% FP on no_beat), English-origin only. Anthropic web_search reaches etf.com
despite its Cloudflare wall (validated 2026-07-10, forward_gather.py), indexes full text, and
supports the two-pass allow/block split BigQuery cannot. So the BACKTEST IS A CONSERVATIVE PROXY: it
under-finds relative to the forward. Right direction for a proxy to err, but it means backtest
numbers understate forward capability and the two corpora are not comparable article-for-article.
The catch: websearch cannot be used for a clean backtest at all (CLAUDE.md #4 -- `before:` leaks),
so forward retrieval can never be validated the way the FBT validated GKG. The forward paper trade
is the only test it gets.

## Standing risks (carried from the retired SPEC)

Deep ladders are seductive storytelling; public events get priced fast; survivorship bias is
everywhere; the herd is faster than it looks; and a retrospective backtest cannot prove a forward
edge (every historical number here is an upper bound — the forward eval is the only clean test).
The design is meant to fail loudly and cheaply when a rung doesn't pay.

## GDELT → BigQuery / GKG migration — DONE 2026-08-08/09 (was ON HOLD; the hold was wrong)

**OUTCOME.** Migrated. `src/gkg.py` + `retrieval_config.json`, own GCP project `geo-herd-rider`.
1-year corpus at `data/backtest_1yr/`: 38,896 articles, 106.6/day, 93.5% with body text, 18,435
bylines, 46/46 beats firing, 2,284 sources. Discovery for a full year takes ~5 minutes and ~250 GB
(inside the free tier) against 4-5 hours and 67% HTTP-429s on the DOC API.

**THE HOLD WAS BASED ON TWO WRONG PREMISES**, both recorded below and both refuted in practice:
  1. *"GKG carries no title, so Wayback must supply title AND lede."* False -- `<PAGE_TITLE>` is in
     the `Extras` column, with a URL-slug fallback. Title, date, source and URL all come free.
  2. *"The Wayback text bottleneck stays, so there is no speed win."* True as stated but no longer
     binding: `src/lede.py` splits the lede into a fast live fetch (~43/s, look-ahead-biased) and a
     clean archived one (16.7/min after switching CDX -> the availability API), so you prototype by
     day and de-bias overnight. Measured drift between the two is ~3% with no age trend.

The original analysis is kept below as the record of what was believed and why.

The GDELT **DOC API** was our firehose retrieval, and it was a **triple** bottleneck. Migrating the
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

## always_include / max_watchlist / starter_watchlist migration (2026-08-09)

Adopted from PWR at the user's direction (no A/B — PWR already proved them). Landed in
`src/optimizer.py` defaults, both investor profiles, `src/firehose.py`
(`anchor_tickers()` / `watchlist_cap()` / `_stateful_watch(seed=)` / the buy-and-hold series in
`_daily_series`), `scripts/build_cbt_dashboard.py`, `scripts/build_forward_sweeps.py`.

Still writing the DEPRECATED names, harmless but worth cleaning up in one pass:
- `max_agents` → `max_watchlist`: `scripts/build_dashboard.py` (sweep knob list + the JS sweep
  registry), `scripts/proto_select.py`, `scripts/backfill_gdelt.py`, `scripts/backfill_tavily.py`,
  `scripts/augment_scan.py`, `src/picker.py` docstring, `src/forward_engine.py` comment. The alias
  in `firehose.watchlist_cap()` keeps every one of them working, so this is cosmetic.
- `defensive_ticker` → `always_include`: `scripts/build_dashboard.py` (the pre-GKG gem dashboards
  build their own universes and still special-case GLD), `scripts/build_forward_dashboard.py`
  (display only). Kept in the optimizer defaults for exactly these.

Open question, NOT settled: is `max_watchlist: 7` binding often enough to do the rotation work it
is there for? Measure it on the next CBT run (weeks where live events > 7) before sweeping it.

## Unfunded prune turned ON (2026-08-09)

`drop_unfunded_weeks: 0 -> 3` with `unfunded_reentry_on_new_catalyst: true`, `unfunded_cooldown_weeks: 0`.
Implemented in `firehose.backtest` (`dropped_at` + `_is_dropped`, replacing the permanent
`dropped_unfunded` set). Both profiles + `optimizer._FINANCIAL_MODEL_DEFAULTS` updated; forward header
logs it as a dated discontinuity.

What the evidence does and does NOT support:
- TRUSTWORTHY: percentile against a MATCHED null (same drop count, same weeks, random victims).
  Prune scores 88th-100th %ile at every N in 2..8; new-catalyst re-entry 100th. So *which* names get
  dropped carries information — it is not merely "hold fewer names".
- NOT TRUSTWORTHY: the dollars. One window, contaminated corpus, still ~90% short of archived ledes.
  The N=3 dollar peak is one path's noise (N=2 $83K, N=3 $123K, N=4 $106K) — do not tune to it.
- Time-gated re-entry was tested and REJECTED: 4wk $58K, 8wk $104K, 12wk $94K, non-monotonic, all worse
  than the new-catalyst release ($130K). A clock readmits a name with no new information.
- Asking the optimizer directly for its top-N in ONE shot scored 13th %ile, worse than alphabetical
  and worse than random. Persistence is the noise filter; the instantaneous weights are not usable.

Follow-ups:
- Re-run all of the above once the wayback backfill lands (~42h from 2026-08-09) — every number here
  is on the incomplete-text corpus.
- `max_watchlist` is now VESTIGIAL (binds 0/50 weeks). Keep as a backstop; do not sweep it until the
  prune is off or the curator floods.
- REAL breadth constraint found, unrelated to any cull: the book funds only ~2.4 positions on average
  (HHI 0.45, effective N ~2.2), and the prune barely moved it (2.4 -> 2.3). That is the optimizer's
  doing — `risk_aversion 1.0` + `concentration_cap 0.667` + `min_trade_size 0.1` — NOT the watchlist.
  If diversity is the goal, sweep those three, not the cull.
- The proposed ranked cull (freshness reserve + trailing risk-adjusted return) was WITHDRAWN: with the
  prune on, the cull never fires. Scores are recorded here in case the prune is ever turned off:
  alphabetical 67th %ile, oldest-first 53rd, freshest-first 85th, trend 83rd, freshness+trend 83rd,
  press-recency 67th (inert — byte-identical to alphabetical, since live names are re-named weekly).

## 3-year biweekly rebuild (2026-08-09)

- 1-year wayback backfill STOPPED at 3,921/38,896 and the corpus superseded. Its wayback_cache.json
  (4,321 lookups, 3,274 hits) and lede_live_cache.json (39,405 fetches) were copied into
  data/backtest_3yr, so none of that work is repeated.
- Ingesting data/backtest_3yr, 2023-08-10 -> 2026-08-09, GKG + live ledes. ~470 GB new BigQuery scan
  (~$0-3 against the 1 TB/mo free tier and the $294 trial credit).
- `rebalance_days: 7` -> `rebalance_period: biweekly` (PWR's vocabulary). util.resolve_cadence() is the
  ONE resolver; rebalance_days survives as the numeric escape hatch. 7 and 14 both anchor on FRIDAY so
  weekly and biweekly series stay comparable.
- `optimizer.load_financial_model` now WARNS on profile keys missing from _FINANCIAL_MODEL_DEFAULTS.
  This class of bug bit us live: `rebalance_period` was written to both profiles and silently ignored.

TO DO once the ingest lands:
- Restart the wayback backfill on data/backtest_3yr (it will trickle for days; ~830/h).
- Re-run backtest_gdelt over 3y biweekly (79 anchors, ~$9), then rebuild FBT + CBT.
- RE-MEASURE the unfunded prune at biweekly. The 88th-100th %ile matched-null result was WEEKLY and
  does not transfer. Same for the ranked-cull scores recorded above.
- Sweep the diversity knobs the backtest profile just adopted from PWR (cap 0.25, risk_aversion 3.0,
  min_trade_size 0.05, max_watchlist 16). They are a borrowed STARTING POINT, not a fitted result.
- Promote to investor_profile.forward.md as a dated re-freeze once the sweep settles. The two profiles
  are KNOWINGLY out of sync on strategy knobs until then (cadence knobs ARE synced).

## Source-quality pass (2026-08-10)

mill_block +6, synced across both profiles: wkrb13.com, modernreaders.com, theenterpriseleader.com,
etfdailynews.com (MarketBeat-network clones; 13,447 arts = 10.5% of the 3-year pool at 13.8 evidence
hits/1k vs benzinga 179, so blocking costs 1.8% of evidence), fool.com.au, fool.co.uk (foreign-exchange
listicles; fool.co.uk produced ZERO evidence in 3 years). fool.ca deliberately KEPT (98 arts, 81.6/1k,
covers US-listed names).

Kept and vindicated: insidermonkey.com is the HIGHEST-yield large source in the corpus (187.5/1k,
above benzinga) -- the earlier decision not to block it was right. finanznachrichten.de kept (61.9/1k):
junior-resource press releases, exactly what the uranium/lithium/rare-earth beats want, though many are
CVE/European listings the US-ticker guard rejects.

Open, not acted on:
- Indian-market outlets (indiatimes, livemint, moneycontrol, businesstoday.in, thehindubusinessline):
  8,715 arts = 6.8% at 38.8/1k, mostly Indian-listed coverage. businesstoday.in is 6.9/1k. Needs a
  content look, not a title look, before any block.
- investors.com is 4th-highest yield here (149.5/1k) but Anthropic web_search is HARD-BLOCKED by Dow
  Jones domains, so it is BACKTEST-ONLY. The forward is structurally weaker here for retrieval reasons,
  not curator reasons. Do not read backtest source-mix as forward source-mix.
- The gem-beat review was measured through the GKG rendering ONLY and is therefore INVALID for the
  framing beats (`niche ETF surging`, `overlooked stock catalyst`, `under the radar small cap stock`):
  etf.com contributes 0 of 128,565 articles because GDELT does not crawl it, while Anthropic web_search
  reaches it. Do not drop those beats on GKG evidence.

## specialty_allow rebalance (2026-08-10)

The list was seeded from PWR's news_sources.md and carried PWR's sector profile (tech-growth/defense/
biotech). GHR's evidence is commodities/energy/shipping/trade-policy. Measured mismatch:
biotech had 3 desks for 94 evidence hits; critical minerals had 0 for 501; uranium had 1 for 1,774.

+9 desks, synced both profiles. Measured (GDELT-crawled, evidence-hits per 1k articles vs benzinga 179):
  mining.com 347 · northernminer.com 316 · argusmedia.com 238 · digitimes.com 135
  utilitydive.com 1333 (n=12) · powermag.com 600 (n=5)   <- tiny samples, sector gap is the real argument
Forward-only, NOT crawled by GDELT so unmeasurable here: trendforce.com (DRAM/NAND prices),
benchmarkminerals.com (lithium), splash247.com (tankers).

-1: kitco.com REMOVED. Crawled by GDELT, 99 articles, ZERO curator evidence in 3 years.

Kept but FLAGGED, cannot be measured (0 GKG presence): tipranks.com, barchart.com are data/screener
sites rather than desks that break early gem calls -- closer in kind to the mills than to etf.com.
Decide forward.

STANDING CAVEAT: specialty_allow is the FORWARD Anthropic gem-pass allowlist. Its purpose is reaching
desks GDELT cannot (etf.com = 0 of 128,565 here, reachable via Anthropic). So GKG yield is a PROXY --
good negative evidence when a desk IS crawled and yields nothing (kitco), good positive evidence when
crawled and high-yield (mining.com), and NO evidence either way for uncrawled desks.

## DONE 2026-08-14 — Forward gem pass returns ZERO from Anthropic (found 2026-08-11)

**Fixed in b68e9ed, 32 days after it started.** The cause was TWO independent bugs, and the first is
the one worth remembering:

1. `cap=0` MEANT "KEEP NOTHING". `gather()` ended with `kept[:cap]`, and `pull_day` passes `cap=0`
   for the reason its own docstring gives -- the daily pull must keep every day's news. `kept[:0]` is
   the empty list, so every article the gather had searched for, dated and frozen was discarded on the
   final line. `0 = uncapped` is the convention everywhere else here (`news_cap`, `max_new_events`),
   and `forward_gather_tavily` already implemented it correctly (`if cap:`) -- which is exactly why
   Tavily worked and Anthropic did not, for a month, with nobody able to see why.
2. A 1-DAY WINDOW MATCHES ALMOST NOTHING. Anthropic's web_search has no recency operator
   (`before:DATE` bounds only the upper end), so it returns articles spread over months. Measured on a
   live 165-result sweep: at the 1-day window the pull used, **0 of 165** survived the fail-closed date
   filter (3d -> 22, 7d -> 35). Anthropic now looks back 7 days; Tavily keeps 1, having a real date
   filter. `_drop_already_pulled` stops the wider window re-storing the same URL every day.

Verified live: 0 -> 43 articles, including etftrends.com -- the Cloudflare-walled specialty desk
Tavily cannot reach, i.e. the early-gem rung this engine exists for.

HARDENED so it cannot recur silently: `gather()` logs its funnel every run (raw -> triaged -> fetched
-> in-window), a pass returning queries-but-no-results is treated as rate limiting and retried with
backoff, and `pull_day` warns loudly to stderr when either engine returns zero. **The diagnosis only
became possible after adding the funnel log** -- zero had four indistinguishable causes, and guessing
between them produced two failed fix attempts first.

The 32 days of one-engine coverage are NOT recoverable; the bootstrap corpus carries that hole from
2026-07-07 to 2026-08-13. See `src/bootstrap_corpus.py`.

### (superseded) original note, parked 2026-08-11
DO NOT FIX NOW (user's call, 2026-08-11): the whole forward-use path is being revamped once the
backtest settles, so an incomplete daily ingest is acceptable in the meantime. Logged so the
finding is not lost — the $1-5/day of wasted spend is the thing to remember.

PWR found ONE domain (arstechnica.com) that blocks Anthropic's crawler 400-ing the whole specialty
pass, because all preferred desks go in as a single `allowed_domains` list. GHR has the IDENTICAL
architecture (`forward_gather.py:261`, one list of 29 domains in one web_search call), so it was
worth checking.

CHECKED: GHR does NOT have that bug. Probed the full 29-domain specialty_allow against Anthropic
web_search -- ACCEPTED, no 400. So no domain in our list blocks the crawler.

BUT a different problem is live and has been for at least 20 consecutive daily runs:
  `union: anthropic 0 + tavily 139` -- the ANTHROPIC half contributes ZERO, every single day.
  Tavily carries the entire forward pool. Meanwhile the ledger shows the gem+coverage calls running
  and costing $1.42-$4.68 PER DAY (2026-08-10 alone: gem 1.42M input tokens, $4.68).
  So we are paying daily for a stage that yields nothing.

Not yet root-caused. Two candidates:
  1. The FAIL-CLOSED date filter (forward_gather.py:299) drops every article whose published_date is
     missing or outside (lo, hi]. On a PAST-24H window almost nothing Anthropic returns may carry a
     parseable same-day date -- memory retrieval-ceiling-gdelt-niche-press records Anthropic reaching
     back only ~4-18 articles/WEEK, so ~0/day could be correct behaviour meeting an unrealistic window.
  2. Something upstream returns results that never survive `build`.
  `capture["results"]` carries an `in_window` flag per raw result and would separate these two
  immediately -- the daily pull does not appear to persist it. Persist it, then read one day.

If (1), the fix is either widening the Anthropic window (it is not a same-day engine) or dropping
Anthropic from the daily pull and running it weekly, where its reach-back actually produces articles.
Either way: stop paying $1-5/day for zero articles.
