# Per-event agent loop — original design notes (now BUILT)

> This section is the original plan; it has since been built. The current implementation is in
> **Event-first refactor — BUILT** below, and the authoritative data model (event object,
> journal-entry schema, identity, lifecycle, storage) lives in the **Event / gem / journal model**
> section at the bottom. This section is kept for the motivation and the weekly-loop shape.

The architecture: replace the single weekly scan with **scout → per-event agents → joint
optimize**. Motivated by the seed decomposition (retrieval, not reasoning, is the wall): a
per-event agent attacks retrieval directly (targeted search for *its* event's early + ongoing
coverage) and carries a journal (continuity + exit calls). Discovery stays aggregate; sizing stays
mechanical. Built as an **optional harness variant**, A/B'd against the single-scan baseline —
kept only if the scoreboard says it pays.

## State — the journal IS the agent's memory
Each event carries an append-only weekly journal (continuity + exit calls). For the **authoritative
event object, journal-entry schema, identity rules, lifecycle, and storage decisions, see the
*Event / gem / journal model* section at the bottom of this file** (it owns the current schema).
Current store: a single `data/windows/agent_events.json` re-dumped per run (one-file-per-event is a
proposed change, documented there).

## The weekly loop (cadence = `rebalance_days`)
```
for each weekly anchor:
  # 1. SCOUT (aggregate, 1 call) — discovery MUST read the whole firehose
  candidates = scout(firehose_window, trump_posts)        # [{ticker, thesis, why_now}]
  candidates = dedup_against(open_events)                  # don't re-open tracked events

  # 2. FAN-OUT (parallel, 1 agent per event) — open events + new candidates
  events = open_events ∪ candidates
  parallel for ev in events:
      news   = targeted_retrieve(ev.query_terms, before=anchor)   # THIS event's coverage, date-bounded
      prior  = read_journal(ev)                                   # memory
      entry  = event_agent(ev, prior, news)                       # writes assessment+thesis_live+exit+sources
      append_journal(ev, entry)

  # 3. CONSOLIDATE — sticky hold, now journal-driven
  watchlist = [ev.vehicles for ev in events if ev.latest.thesis_live]   # + hysteresis from _stateful_watch

  # 4. OPTIMIZE (joint, after all agents) — unchanged
  weights = optimized_weights(watchlist, panel, anchor, fm)           # mean-variance; LLM never sizes
```

## Wiring into existing code
- **Reuse:** `scan_anchors` (cadence), the GDELT pool / seeds / forward `web_search` (retrieval),
  `curator._optimized_weights` (sizing), `_stateful_watch` (hysteresis over journal `thesis_live`),
  `score` (prices), `costs` (ledger), the dashboard (add per-event journal pages + hotlinks).
- **Built:** `src/agent.py` — `scout()` and `event_agent()`; the event/journal store
  (`data/windows/agent_events.json`; per-event files are a proposed change — see the model section);
  `targeted_retrieve(query_terms, before_date)` (forward: live `web_search`/Tavily; backtest: the
  GDELT pool filtered to the event + any seeds). The backtest runs scout+fan-out at each anchor in
  place of the single `scan_fixture`.
- **Harness A/B:** add `variant="agent"` to `run_harness.py`; compare recall / precision / tail /
  capture against the single-scan baseline and the seeded run.

## What it fixes (mapped to the decomposition)
- **Entry retrieval** (0%→92% gap): targeted search digs for the early under-the-radar naming the
  broad firehose misses. Clean **forward** ("search this event now"); in backtest, still seed/date-bounded.
- **Hold retrieval** (niche names dropped ~4wk): the agent keeps pulling *its* event's coverage, so
  it doesn't go stale and get cut before the run completes → captures more of the move.
- **Precision** (27%, lots of noise): each candidate gets a dedicated verify ("is this a real
  thesis-driven gem, or thematically-adjacent noise?") → fewer false positives.
- **Continuity:** the journal carries the thesis forward → steadier exit timing, auditable, hotlinked.

## Cost
~`N+1` LLM calls per week (1 scout + N≈3–8 event agents) vs 1 for the single scan. Backtest over
~198 weeks ≈ 5–8× the single-scan cost (~$25–40). Gate on the harness before it becomes default.

## Non-negotiable guardrails (carried forward)
- **Never forecast how HIGH; DO judge when to EXIT.** The LLM must not predict magnitude / a price
  target (that destroyed value in prior work, and no number ever feeds sizing — sizing is mechanical).
  But it *should* use common sense to judge **when to exit** — i.e. when the catalyst resolves (BWET:
  a ceasefire is signed and shipping resumes through Hormuz). "How long / when to exit" is an
  allowed, qualitative, catalyst-driven judgment (it IS the `thesis_live` / `exit_advice` call);
  "how high" is forbidden. Any magnitude in the journal is only *attribution of what the news claims*.
- **Look-ahead.** Targeted search is clean only forward; backtest uses date-bounded/seeded retrieval
  and remains an upper bound. The forward eval is the verdict.
- **Discovery first.** Can't target-search an undiscovered event → scout (aggregate) precedes fan-out.

---

# Event-first refactor — BUILT (`agent.run_event_agent_scans`, `--event-first`)

**Status (built):** scout → **LLM matcher** (groups this week's candidates into existing events or
"new") → per-event agent that owns an **evolving vehicle set** and picks the current best vehicle(s)
via a `vehicles` field (Pydantic-guarded, no-magnitude). Per-week resume checkpoint; ticker-keyed
`--agent` retained as the A/B baseline. Trigger that justified it: the 13-gem ticker-keyed run
fragmented single events across tickers (RNMBY/RHMTY = same company; nuclear across SMR/OKLO/CCJ/CEG).
The 13-gem event-first vs ticker-keyed A/B is the measurement (running).

**Why.** The agent was **ticker-keyed**: the scout proposes tickers, each ticker gets its own
journal, and an "event" exists only as the thesis string inside a ticker's note. That mismodels
reality — a single durable **event** (a war, an election, a supply shock) can last months/years and
throw off **different gems over time** (Iran war → BWET early, perhaps a different shipping vehicle
later). Ticker-keying splits one event into disconnected journals and can't express "the best
vehicle for this event changed." Making the **event** first-class fixes that and operationalizes the
vehicle-selection insight as a thing that evolves.

**Target model.**
- An `Event` owns: `id`, `catalyst`/thesis (the durable thing), `status` (live/exited), a rolling
  journal (the memory), and a **set of vehicles (gems)** with a *current* pick — which can change.
- **Scout** discovers *events* (catalyst + initial gem[s]), not bare tickers.
- **Event matching/dedup (the crux).** When the scout names a ticker/catalyst, an LLM-judged step
  decides: does this belong to an existing live event (same catalyst → add/update its vehicles) or
  is it new? Without this you get duplicate events. This is the hard, new piece (event/entity
  resolution).
- **Per-event agent** tracks the event over time and may **add or swap the current best vehicle**
  (vehicle-selection as a time-series), with reasons logged in the journal.
- **Watchlist** = the *current* vehicle(s) of each live event → optimizer sizes (unchanged).

**Preserved guardrails.** No-magnitude (Pydantic, unchanged); rolling one-week memory (anti-
anchoring); targeted retrieval per event (monitoring, not discovery); discovery stays aggregate.

**Effort / risk.** Moderate refactor of `agent.py` (journals keyed by event; vehicles as an evolving
attribute; the new matching step). The matching step is the main risk (false merges/splits). 

**Sequencing.** Do this AFTER validating the current ticker-keyed agent on the 13-gem A/B — don't
rebuild the engine before we know the simpler version's distribution behavior. If the 13-gem run
shows the same event surfacing under multiple vehicles (the symptom this fixes), that's the trigger
to build it.

---

# Event / gem / journal model — the contract for evaluation

This is the single source of truth for how events, gems, and journals are named, structured, and
stored — frozen *before* the BWET → BWET+2 → all-gems evaluation, since that phase compares runs
against each other and needs stable identity. It is authoritative over the older notes above.
Each item is tagged **[CURRENT]** (already in the code) or **[PROPOSED]** (agreed direction, not yet
built).

## Vocabulary (say it once, use it everywhere)
- **gem** = a *ticker* — the stable, ground-truth unit (e.g. `BWET`); the rows of `gems.json`.
- **event** = one *catalyst* (a war, an election, a supply shock) — the durable thing that names gems
  and can last weeks→years.
- **vehicle** = a ticker an event *currently holds*. A vehicle and a gem are the same kind of thing
  (a ticker); "gem" is the evaluation/ground-truth word, "vehicle" is the in-flight word. An event
  owns an evolving *set* of vehicles.

## Identity — three layers (the crux for cross-run comparison)
- **`evN`** **[CURRENT]** — a within-run counter (`ev1`, `ev2`, …). **Ephemeral**: it restarts each
  run, so it is NOT a stable key. Use it only as a handle inside one run.
- **`catalyst` + `slug`** — `catalyst` is the human-readable name **[CURRENT]**; **[PROPOSED]** add a
  stable `slug` derived from it (e.g. `iran-tanker-freight`, `pentagon-defense-spend`) so an event is
  recognizable across runs and in diffs.
- **Evaluation keys on the gem ticker, not the event id** **[CURRENT capability]** — "how well did it
  manage BWET" = "which event held `BWET`, from when to when, and did it exit near the peak," all
  keyed on the stable ticker (the harness already maps held tickers → gems). This sidesteps the
  ephemeral-id problem for the whole evaluation phase.

## Event object **[CURRENT]**
```json
{ "id": "ev1", "catalyst": "Iran war spikes tanker freight rates",
  "status": "live",                       // live | exited
  "vehicles": ["BWET"],                   // evolving set; the agent picks the current best
  "entries": [ /* one per week, below */ ] }
```
**[PROPOSED]** add `"slug": "iran-tanker-freight"`, `"discovered": "<week>"`, `"exited": "<week|null>"`.

## Journal entry — frozen schema (one row per event × week) **[CURRENT]**
```json
{ "date": "2026-02-20",
  "thesis_live": true,                    // THE hold/exit switch (catalyst active?) — drives the trade
  "vehicles": ["BWET"],                   // current best vehicle(s) for the event this week
  "exit_advice": "exit on ceasefire / Hormuz reopens / rates roll over",
  "assessment": "<=40 words: what changed + the read, continuous with the prior note",
  "news_claims": "press cites ~240% YTD", // ATTRIBUTION ONLY — never our forecast, never feeds sizing
  "sources": ["url", "url"] }
```
The journal is the agent's **one-week-deep memory** (it reads only the prior entry — anti-anchoring)
*and* the human audit trail. No magnitude/target/size field exists (Pydantic guardrail).

## Lifecycle **[CURRENT]**
- **Born** — scout proposes a candidate → deterministic same-ticker guard (a held ticker belongs to
  its event) → else LLM matcher assigns it to a live event or mints a new `evN`.
- **Evolves** — the per-event agent may add/swap vehicles week to week (vehicle-selection as a
  time series), with reasons in the journal.
- **Dies** — `status` → `exited` when `thesis_live=false` for `EXIT_PATIENCE` consecutive reads, or
  unmentioned for `MAX_STALE` weeks (`firehose._stateful_watch`).

## Storage & format
- **JSON, not a database** **[CURRENT]** — at ~5-year scale this is small data (see below); JSON is
  human-readable, git-diffable, and native to the LLM output.
- **[PROPOSED] Split source-of-record from analysis substrate.** Today `agent_events.json` /
  `agent_journals.json` are one nested array *re-dumped wholesale each run*, so a one-entry change
  rewrites the whole file (noisy diffs, unsafe hand-edits). Move the journal to **JSONL (one line per
  event-week entry)** or one-file-per-event so re-runs/fixes produce *localized* diffs and one event
  can be revised without touching the rest. Derive flat `events` / `entries` / `decisions` tables
  (CSV/parquet) for re-reads & visualization — regenerated, never hand-edited.
- **[PROPOSED] What's committed.** The full journal is a regenerated build artifact → don't commit it
  by default (churn). Commit the small scan-log + harness report, plus a **frozen "golden" journal
  snapshot per evaluated run** so the evaluated state is pinned and re-readable.
- **[PROPOSED] `decisions` provenance log.** Persist per-week scout candidates, matcher assignments,
  same-ticker-guard hits, and invalid-ticker drops — currently computed in-run and lost. Cheap to
  emit, impossible to reconstruct later; needed to audit *what the agents did*, not just what survived.

## Scale ballpark (~5-year weekly backtest)
~260 weekly scans · **~50–80 distinct events** (≤~150 worst case) · **~65–100 distinct gems/vehicles**
· **~1,000–1,500 journal entries** · ~3–8 concurrent live events · **~1–2 MB** on disk. Small data —
the format choice is about ergonomics (re-reads, diffs, revisions, cross-run comparison), not scale.
