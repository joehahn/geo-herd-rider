# Per-event agent loop: original design notes (now BUILT)

> This section is the original plan; it has since been built. The current implementation is in
> **Event-first refactor. BUILT** below, and the authoritative data model (event object,
> journal-entry schema, identity, lifecycle, storage) lives in the **Event / gem / journal model**
> section at the bottom. This section is kept for the motivation and the weekly-loop shape.

The architecture: replace the single weekly scan with **scout → per-event agents → joint
optimize**. Motivated by the seed decomposition (retrieval, not reasoning, is the wall): a
per-event agent attacks retrieval directly (targeted search for *its* event's early + ongoing
coverage) and carries a journal (continuity + exit calls). Discovery stays aggregate; sizing stays
mechanical. Built as an **optional harness variant**, A/B'd against the single-scan baseline ,
kept only if the scoreboard says it pays.

## State: the journal IS the agent's memory
Each event carries an append-only weekly journal (continuity + exit calls). For the **authoritative
event object, journal-entry schema, identity rules, lifecycle, and storage decisions, see the
*Event / gem / journal model* section at the bottom of this file** (it owns the current schema).
Current store: a single `data/windows/agent_events.json` re-dumped per run (one-file-per-event is a
proposed change, documented there).

## The curation loop (cadence = `rebalance_period`)
```
for each weekly anchor:
  # 1. SCOUT (aggregate, 1 call), discovery MUST read the whole firehose
  candidates = scout(firehose_window, trump_posts)        # [{ticker, thesis, why_now}]
  candidates = dedup_against(open_events)                  # don't re-open tracked events

  # 2. FAN-OUT (parallel, 1 agent per event), open events + new candidates
  events = open_events ∪ candidates
  parallel for ev in events:
      news   = targeted_retrieve(ev.query_terms, before=anchor)   # THIS event's coverage, date-bounded
      prior  = read_journal(ev)                                   # memory
      entry  = event_agent(ev, prior, news)                       # writes assessment+thesis_live+exit+sources
      append_journal(ev, entry)

  # 3. CONSOLIDATE, sticky hold, now journal-driven
  watchlist = [ev.vehicles for ev in events if ev.latest.thesis_live]   # + hysteresis from _stateful_watch

  # 4. OPTIMIZE (joint, after all agents), unchanged
  weights = optimized_weights(watchlist, panel, anchor, fm)           # mean-variance; LLM never sizes
```

## Wiring into existing code
- **Reuse:** `scan_anchors` (cadence), the GDELT pool / seeds / forward `web_search` (retrieval),
  `curator._optimized_weights` (sizing), `_stateful_watch` (hysteresis over journal `thesis_live`),
  `score` (prices), `costs` (ledger), the dashboard (add per-event journal pages + hotlinks).
- **Built:** `src/agent.py`, `scout()` and `event_agent()`; the event/journal store
  (`data/windows/agent_events.json`; per-event files are a proposed change, see the model section);
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
  target (that destroyed value in prior work, and no number ever feeds sizing; sizing is mechanical).
  But it *should* use common sense to judge **when to exit**, i.e. when the catalyst resolves (BWET:
  a ceasefire is signed and shipping resumes through Hormuz). "How long / when to exit" is an
  allowed, qualitative, catalyst-driven judgment (it IS the `thesis_live` / `exit_advice` call);
  "how high" is forbidden. Any magnitude in the journal is only *attribution of what the news claims*.
- **Look-ahead.** Targeted search is clean only forward; backtest uses date-bounded/seeded retrieval
  and remains an upper bound. The forward eval is the verdict.
- **Discovery first.** Can't target-search an undiscovered event → scout (aggregate) precedes fan-out.

---

# Event-first refactor: BUILT (`agent.run_event_agent_scans`, `--event-first`)

**Status (built):** scout → **LLM matcher** (groups this week's candidates into existing events or
"new") → per-event agent that owns an **evolving vehicle set** and picks the current best vehicle(s)
via a `vehicles` field (Pydantic-guarded, no-magnitude). Per-week resume checkpoint; ticker-keyed
`--agent` retained as the A/B baseline. Trigger that justified it: the 13-gem ticker-keyed run
fragmented single events across tickers (RNMBY/RHMTY = same company; nuclear across SMR/OKLO/CCJ/CEG).
The 13-gem event-first vs ticker-keyed A/B is the measurement (running).

**Why.** The agent was **ticker-keyed**: the scout proposes tickers, each ticker gets its own
journal, and an "event" exists only as the thesis string inside a ticker's note. That mismodels
reality, a single durable **event** (a war, an election, a supply shock) can last months/years and
throw off **different gems over time** (Iran war → BWET early, perhaps a different shipping vehicle
later). Ticker-keying splits one event into disconnected journals and can't express "the best
vehicle for this event changed." Making the **event** first-class fixes that and operationalizes the
vehicle-selection insight as a thing that evolves.

**Target model.**
- An `Event` owns: `id`, `catalyst`/thesis (the durable thing), `status` (live/exited), a rolling
  journal (the memory), and a **set of vehicles (gems)** with a *current* pick, which can change.
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

**Sequencing.** Do this AFTER validating the current ticker-keyed agent on the 13-gem A/B, don't
rebuild the engine before we know the simpler version's distribution behavior. If the 13-gem run
shows the same event surfacing under multiple vehicles (the symptom this fixes), that's the trigger
to build it.

---

## The catalyst gate: scout selectivity **[CURRENT]**

The scout names a ticker only when the press ties it to a **specific, datable, resolvable catalyst**, a war/chokepoint, an export ban or tariff, a named bill, a regulatory approval, a supply shock. It **rejects pure theme/momentum** ("AI-power demand", "rising gold demand", "safe-haven flows"), which has no resolution and rides through every crash. That named resolution is exactly what later flips the position to EXIT.

**Anticipation clause (validated prototype, not yet swept across all gems).** A surgically-tested refinement admits one more class: **anticipation of a specific dated future event**, a national election, an FDA/PDUFA date, a scheduled vote, a court-ruling date, where the name is demonstrably rising *ahead* of the event and the **known date is the exit**. This lets the curator ride a run *into* a fixed event and sell the news: MicroStrategy rode Bitcoin in anticipation of the pro-crypto 2024 election (entering September, exiting at the November vote), a trade the un-refined gate declined as momentum. Validated in isolation (dated-election anticipation 6/6; a dateless Bitcoin-demand control 0/6); it is a shared prompt change and stays forward-test-gated. The clean negative control is **GDX** (gold miners): a ~3x run that was a diffuse macro theme with no discrete catalyst until a late gold-specific tariff, so the gate correctly declines it early and catches it only at the blow-off top.

## Peer-basket: multiple vehicles per catalyst **[CURRENT]**

One catalyst usually has several credible vehicles (a European-rearmament shock lifts Rheinmetall + BAE / Saab / Thales; a rare-earth curb lifts several miners). The scout names the purest vehicle as the primary `ticker` and lists its direct same-catalyst peers in a `peers` field; the event-agent proposes the whole basket and the mechanical optimizer sizes them, dropping the weak ones (the LLM never forecasts *which* peer wins). A peer must share the **same catalyst**, so a basket cannot drift into an unrelated gem (a naive "just propose more names" version drifted across catalysts and lost ~45% of return). **That was an aspiration, not an enforced invariant, until 2026-08-30**: the word "structurally" stood here and was wrong. Examples of the drift, from `cbt_3yr_v24_wirelede`: `XOM` was added to "the Iran war disrupts oil supplies" with Exxon in 0 of 22 matched articles, and `WSM` to "RH pivots supply chain amid tariffs" with Williams-Sonoma in 0 of 319. Those are "oil disruption so Exxon benefits" and "RH has trouble so its competitor matters": the retired causal ladder, leaking back in through the vehicle list. It is enforced now: `agent._named_in` admits an ADDED vehicle only if its symbol or company name appears in a TITLE of the event's uncapped relevance match (`_filter_event(..., cap=0)`; `cap` is a reading budget, not a relevance judgement). Existing vehicles pass untouched, so an agent can still drop one. **MEASURED IN A REAL RUN, and it is much weaker than it looks.** `cbt_3yr_v25_vehgate`, the first 37-scan curation with the gate live, refused **4 tickers out of 88 vehicle additions (4.5%)** (SMCI on ev5, NXE+UROY on ev26, LMT on ev111), and **all four still entered the book through a different event**. Net effect on what the book HOLDS: about zero. What it actually buys is journal integrity: an event's vehicle list now reflects what the press named for THAT catalyst, not a change in the portfolio.

An earlier reconstruction put the refusal rate at 20%; that used the raw 30-day corpus window, while the real run matches against `gslice`, whose wider pool lets far more tickers through. **Do not quote the 20%.** The gate's strength is bounded by how wide the uncapped match is (319 articles matched one event, 488 another), and against a haystack that size almost any large cap appears in some headline. Fixing `_filter_event`'s over-collection (see TODO.md) is what would give this gate teeth. It is also P&L-neutral by construction: never-named tickers do not underperform (median $2,655 vs $2,362 on v24; $2,975 vs $2,235 on v23). A/B honesty: on RNMBY the basket formed cleanly (RNMBY + BAE + Saab + Thales) yet the *single* purest name still won (+251% vs +235%), baskets help when the best vehicle is ambiguous, not when it is already the clear winner.

## The weekly agent loop: Reflexion-style hindsight **[CURRENT]**

Each held event's agent, each week: (1) pulls news targeted to its own catalyst (including resolution signals like a ceasefire); (2) reads its **full journal arc since entry** and writes a weekly `hindsight` self-critique of last week's call *before* deciding (a Reflexion-style step against repeat-the-same-mistake inertia); (3) runs an explicit exit-on-resolution check against the whole arc; (4) writes a note with a short assessment, the `thesis_live` / exit call (the only thing that drives the hold/exit), and hot-linked sources. Discovery is aggregate (you can't target-search an event you haven't found); only *monitoring* a held event uses its own targeted search, so it doesn't bias discovery.

# Event / gem / journal model: the contract for evaluation

This is the single source of truth for how events, gems, and journals are named, structured, and
stored, frozen *before* the BWET → BWET+2 → all-gems evaluation, since that phase compares runs
against each other and needs stable identity. It is authoritative over the older notes above.
Each item is tagged **[CURRENT]** (already in the code) or **[PROPOSED]** (agreed direction, not yet
built).

## Vocabulary (say it once, use it everywhere)
- **gem** = a *ticker*, the stable, ground-truth unit (e.g. `BWET`); the rows of `gems.json`.
- **event** = one *catalyst* (a war, an election, a supply shock), the durable thing that names gems
  and can last weeks→years.
- **vehicle** = a ticker an event *currently holds*. A vehicle and a gem are the same kind of thing
  (a ticker); "gem" is the evaluation/ground-truth word, "vehicle" is the in-flight word. An event
  owns an evolving *set* of vehicles.

## Vehicle admissibility (what can be a vehicle)
A ticker qualifies as a vehicle iff: (a) the **press names it**; (b) it's **retail-tradable and
yfinance-priceable**; and (c) the **mechanical mean-variance optimizer can size it as a held position
WITHOUT the LLM making a magnitude / leverage / expiry call**. That admits **US-listed stocks, ADRs,
ETFs, ETNs** (BWET is an ETN), and equity wrappers like REITs/CEFs and bond/commodity ETFs (they're
just ETFs/equities). It **excludes options and futures**: both require a strike/expiry/leverage
decision, i.e. *magnitude*, which violates the load-bearing no-magnitude guardrail (non-negotiable #1)
and can't be priced/sized cleanly; commodity/rate exposure is taken via ETFs/ETNs instead. **Leveraged and inverse ETFs are refused outright** (2x/3x/-1x and the "Ultra"/"UltraPro"/"Direxion
Daily" family): the scout prompt names them as never-propose. They reset leverage daily and bleed from
volatility decay, so they are day-trade instruments, not something to hold while a catalyst plays out,
and the path-dependence corrupts the mu/Sigma fit as well. This used to read "technically admissible
but discouraged"; it is a hard rule now. Spot crypto and prediction markets (Polymarket) are out of
scope: the former leaves US-listed, the latter is an event-probability signal rather than a
mean-variance-sizable position.

**Four gates enforce the boundary, in order.**

1. **Shape** (offline, free). `score.validate_tickers` normalizes the symbol (`$RGTI`,
   `NASDAQ:RGTI`, `(RGTI)` all collapse to `RGTI`) and then requires a plausible US symbol. This
   catches company names, prose, empty strings and foreign-exchange suffixes (`CSL.AX`, `7203.T`).
   Rejections are LOUD by design: the bug this exists to kill is a curator-picked position silently
   vanishing from the book with nothing in the output saying so.
2. **Resolution, before rejection.** A reject whose reason is name-shaped (rather than "no price
   history" or "not listed until") is sent to `agent.resolve_us_ticker`, a web lookup mapping a
   company to its US symbol (Rheinmetall to RNMBY, RIGETTI COMPUTING to RGTI), and re-validated. Only
   if that fails is the symbol dropped. It runs for a candidate's primary ticker inside `scout()` and
   again for `peers`, which formerly went through nothing at all. Look-ahead-safe: a name-to-symbol
   mapping is a static fact and only the symbol is extracted. `search_query` is REQUIRED on this
   call; omitting it silently turns web search into a no-op on the OpenRouter path and leaves a cheap
   model reciting tickers from memory.
3. **Listing date** (network, cached). The symbol must have traded on or before the decision date.
   This is a look-ahead guard, not a typo check: without it a backtest can buy a company that had not
   listed yet.
4. **Funding gates**, which decide sizing rather than admissibility: a liquidity floor
   (`min_dollar_volume_usd`) keeps a thinly-traded name from occupying a watchlist slot, and a
   death-spiral exclusion (`exclude_young_reverse_split`) refuses to fund a recently-listed company
   carrying a punitive reverse split.

## Identity: three layers (the crux for cross-run comparison)
- **`evN`** **[CURRENT]**, a within-run counter (`ev1`, `ev2`, …). **Ephemeral**: it restarts each
  run, so it is NOT a stable key. Use it only as a handle inside one run.
- **`catalyst` + `slug`.** `catalyst` is the human-readable name **[CURRENT]**; **[PROPOSED]** add a
  stable `slug` derived from it (e.g. `iran-tanker-freight`, `pentagon-defense-spend`) so an event is
  recognizable across runs and in diffs.
- **Evaluation keys on the gem ticker, not the event id** **[CURRENT capability]**. "how well did it
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

## Journal entry: frozen schema (one row per event × week) **[CURRENT]**
```json
{ "date": "2026-02-20",
  "thesis_live": true,                    // THE hold/exit switch (catalyst active?), drives the trade
  "vehicles": ["BWET"],                   // current best vehicle(s) for the event this week
  "hindsight": "prior call holds",        // weekly SELF-CRITIQUE of last week's call (anti-inertia)
  "exit_advice": "exit on ceasefire / Hormuz reopens / rates roll over",
  "assessment": "<=40 words: what changed + the read, continuous with the prior note",
  "news_claims": "press cites ~240% YTD", // ATTRIBUTION ONLY, never our forecast, never feeds sizing
  "sources": ["url", "url"] }
```
The journal is the agent's **memory** *and* the human audit trail. No magnitude/target/size field
exists (Pydantic guardrail). NOTE: the memory model changed, the agent now reads its **whole arc
since entry**, not just the prior entry, plus writes a weekly `hindsight` self-critique. See
"Memory, exits & scope (2026-06)" below for why and the anti-anchoring tradeoff.

## Lifecycle **[CURRENT]**
- **Born.** Scout proposes a candidate → deterministic same-ticker guard (a held ticker belongs to
  its event) → else LLM matcher assigns it to a live event or mints a new `evN`.
- **Evolves.** The per-event agent may add/swap vehicles week to week (vehicle-selection as a
  time series), with reasons in the journal.
- **Dies.** `status` → `exited` when `thesis_live=false` for `exit_patience_scans` consecutive
  reads, or unmentioned for `max_stale_scans` scans (`firehose._stateful_watch`). Both clocks come
  from the profile; the module constants are only fallbacks. `catalyst_resolved` is NOT an exit here
  (see `_stateful_watch`); it acts as an entry block instead.

## Sticky hold (hysteresis) **[CURRENT]**

`firehose._stateful_watch(scans)` turns the **stateless** weekly scans into a **sticky position
portfolio**, and it's what the backtest sizes each week (`watch = _stateful_watch(scans)` at the top of
`backtest()`), not the raw per-week `thesis_live`.

**The problem.** Each weekly scan is an independent read: a name can be `thesis_live=true` one week,
go unmentioned the next (the press just didn't cover it that week), then return. Holding strictly on
"is it in *this* week's scan" would churn positions on coverage gaps and one-off noise, paying
costs and, worse, dropping a still-valid thesis on silence. The GDELT-noise run exposed exactly this
trigger-happy exit.

**The mechanism, easy to enter, deliberately hard to exit.** Walking anchors in order, it carries per-ticker state (`holding`, a `dead` counter, a `stale` counter) and each week:
- **Enter / refresh.** Any ticker read `thesis_live=true` → held, both counters reset to 0. Entry
  is immediate (one live read); any live mention re-arms a held name's patience.
- **Explicitly flagged dead** (held, this week `thesis_live=false`) → `dead += 1`; exit only at
  `exit_patience_scans` consecutive dead reads. One "thesis is over" week does **not** exit.
- **Unmentioned** (held, absent from this week's scan) → `stale += 1`; exit at `max_stale_scans`
  silent weeks. Silence ≠ death, but indefinite silence eventually exits.

**Why asymmetric.** 1 read to enter vs. 2 consecutive explicit-dead reads (active resolution) or 4 silent weeks (passive timeout) to exit; that asymmetry *is* the stickiness. The counters track
*consecutive* conditions: a dead-flag resets `stale`, a live-flag resets both.

**Knob status.** Both clocks are profile knobs now, `exit_patience_scans` and `max_stale_scans`, so
they are swept like `concentration_cap` and the rest. `EXIT_PATIENCE` and `MAX_STALE` survive in
`firehose.py` only as fallbacks for a profile that omits them. This section previously said they were
hardcoded and were candidates for promotion; the promotion happened.

## Memory, exits & scope: 2026-06 revisions **[CURRENT]**

Diagnosing three backtest failures (BWET/MP under-concentrated, SMR held its whole post-peak
decline) drove a batch of agent changes. The root cause of the SMR miss: **exits under-fired**, because the agent only saw last week's note, so as the prose chain drifted ("ADVANCE Act signed" → "AI-power
demand continues") it lost the *specific discrete catalyst* and defaulted to holding.

- **Full-arc memory (replaces one-week memory).** `event_agent_v2` now receives the WHOLE journal
  since entry via `_journal_digest()` (one compact line/week: date · live · vehicle · assessment;
  entry week always shown, capped ~20 weeks). The event stays the durable unit; the **vehicle may
  evolve** and the arc shows that lineage. *Tradeoff:* the original one-week design was deliberately
  anti-anchoring (amnesia prevents repeating a stale call); full memory reintroduces anchoring risk
  (the "Diplomacy-A2A" effect, agents repeating a mistake turn after turn). The mitigation is the
  self-critique step below, not amnesia.
- **Exit-on-resolution (`EVENT_AGENT_SYSTEM`).** The weekly exit check is forceful and re-read
  against the *whole* journal: flip `thesis_live=FALSE` the week the specific catalyst RESOLVES
  (bill signed/voted, approval granted/denied, deal closed, emergency ended, chokepoint reopened) ,
  EVEN IF the stock is still rising and even if a broader theme lingers. Crowding/hype is still NOT
  an exit. (Verified: re-scan exits SMR ~the ADVANCE-Act signing / mid-July peak, vs holding to Sept.)
- **Weekly self-critique / `hindsight` (Reflexion).** Before deciding, the agent writes a ≤20-word
  critique of last week's call and lets it CHANGE this week's call ("prior call holds" if it was
  right). This is the anti-anchoring mechanism that replaces one-week amnesia. *Open issue:* in
  practice the field often defaults to "prior call holds"; it may need a sharper prompt to force a
  genuine re-examination.
- **Scope guard. US-listed only.** `scout()` drops any candidate whose ticker contains a `.`
  (yfinance US tickers have none; a `.AX/.L/.TO/.HK/...` suffix is a foreign exchange). The prompt
  also says to name the US ADR (CSLLY, not CSL.AX) or skip. This is a code guard (doesn't trust the
  LLM) plus prompt, added after deepseek picked `CSL.AX`, which both violated scope and polluted the
  shared price panel (foreign trading calendar). The LLM verified to name the ADR under the new prompt.
- **Curator-model knob + bake-off.** `model:` in `investor_profile.md` (resolved by
  `optimizer.resolve_curator_model`: `mimo|sonnet|opus|llama4|deepseek|grok4|gemini`) selects the
  curator LLM; the scan stamps a `<scan>.meta.json` sidecar so dashboards show which model produced
  each book. A 7-model bake-off (sweeps page, top plot) re-scored each model's 6-gem books on shared
  panels; **deepseek-V3 (the cheap default)** caught all 3 gems at the lowest cost.
- **Agent-journal arc view.** Scans persist `hindsight`/`assessment`/`exit_advice` onto each pick;
  the gem dashboard renders an "Agent journal, week-by-week (per event)" section so the arc is
  inspectable (spot anchoring / missed exits).
- **`thesis_floor`, tried and ROLLED BACK.** A mechanical floor that guaranteed a min weight to the
  LLM-named "lead" gem (to stop mean-variance rotating off it). The sweep showed it near-inert once
  exits work (cap already concentrates), so it was removed entirely in favor of the memory/exit fix.
  Recorded here so it isn't re-litigated.

**Known risk, engineered-catalyst exit timing [flag for forward eval].** The exit-on-resolution
logic is built around catalysts that resolve on an *economic/physical* clock (bill signed, chokepoint
reopened, deal closed). A subset of named gems may instead move on an *engineered* clock, a policy
decision steered by the very people the press names as beneficiaries (the "conspiracy" reading of a
Zero-Hedge-style who's-who piece). For entry this changes nothing: the observable signature is still
"press names the ticker early while framed as under-the-radar," and the strategy is agnostic about
*why* it pops (non-negotiable #1 already bars us from betting on the conspiracy narrative or on
magnitude). But the **exit** is harder: an engineered catalyst decays on a *political* clock (policy reverses, principals rotate out, the news cycle turns), none of which the fundamentals telegraph, and
none of which maps cleanly onto our "specific discrete catalyst RESOLVES" test. Flagged as something
the forward eval should watch for (held positions whose thesis went stale politically, not
economically, and that our exit logic held too long). NOT a rung to build, a "who named it" source
tier is downstream of the forward scoreboard proving the plain news firehose pays (scope discipline).

**Caveat (carried throughout):** the curator LLM is **stochastic**, re-scans give different draws,
so single-run before/after comparisons are noisy. Treat per-draw deltas as suggestive, not proof;
the forward eval (or multi-seed averaging) is the real test.

## Profile knobs are partitioned by where they act **[CURRENT]**

`src/provenance.py` classifies every knob in the investor profile as either a CURATION knob or a
BOOK knob, and fails the build while any knob is unclassified, so adding a knob forces a decision
about its blast radius before it can ship.

- **CURATION knobs** act upstream of the journal: which articles are read, which the scout is shown,
  which events open and when they retire. Change one and the existing journal could never have been
  produced under it, so the run is invalid and the news must be re-read at LLM cost.
- **BOOK knobs** act at replay time over a fixed journal: sizing, culling, rebalancing. Change one
  and the same curation simply produces a different book, so the page just needs a rebuild.

`max_watchlist` is the type specimen: `watchlist_cap()` is called only in `firehose.backtest`, never
in the scan path. Getting the boundary wrong is expensive in both directions. Treat a book knob as
curation and every sizing tweak demands a re-curation; treat a curation knob as book and you publish
a page whose settings table describes something the journal never ran under.

## Storage & format
- **JSON, not a database** **[CURRENT]**, at ~5-year scale this is small data (see below); JSON is
  human-readable, git-diffable, and native to the LLM output.
- **[PROPOSED] Split source-of-record from analysis substrate.** Today `agent_events.json` /
  `agent_journals.json` are one nested array *re-dumped wholesale each run*, so a one-entry change
  rewrites the whole file (noisy diffs, unsafe hand-edits). Move the journal to **JSONL (one line per
  event-week entry)** or one-file-per-event so re-runs/fixes produce *localized* diffs and one event
  can be revised without touching the rest. Derive flat `events` / `entries` / `decisions` tables
  (CSV/parquet) for re-reads & visualization, regenerated, never hand-edited.
- **[PROPOSED] What's committed.** The full journal is a regenerated build artifact → don't commit it
  by default (churn). Commit the small scan-log + harness report, plus a **frozen "golden" snapshot
  per evaluated run** so the evaluated state is pinned and re-readable.
- **[CURRENT] Golden regression snapshot.** `data/golden/bwet/` freezes the scan log + price panel +
  `fm` knobs + expected backtest output; `scripts/check_golden.py` replays it to prove a CODE change
  didn't move the portfolio (deterministic, isolates code from LLM noise and yfinance price drift).
  `scripts/build_golden.py` regenerates it for an intentional, vetted baseline change. NOTE: with a
  tight `min_trade_size` the optimizer is knife-edge, float-precision differences (e.g. in-memory vs
  CSV-loaded prices) can flip which single name a week funds; the golden derives `expected` from the
  same CSV the check reads, so it's internally consistent.
- **[PROPOSED] `decisions` provenance log.** Persist per-week scout candidates, matcher assignments,
  same-ticker-guard hits, and invalid-ticker drops, currently computed in-run and lost. Cheap to
  emit, impossible to reconstruct later; needed to audit *what the agents did*, not just what survived.

## Retrieval **[CURRENT]**

Live retrieval is a web search plus a daily pull, described in the README. Two properties matter to
the design and are worth restating here.

**The pull is unrepeatable.** Search results are not re-queryable and articles are edited, paywalled
or deleted, so a day not captured is a permanent hole. That is why the pull runs daily and writes an
append-only archive rather than being re-derived at each rebalance.

**No search tool offers true point-in-time retrieval.** Date filters leak articles published after
the cutoff, results are ranked by what later became famous, and what comes back is today's edited
page rather than what was published at the time. `src/search.py` re-enforces the bound client-side
off each article's published date, which narrows the leak without closing it. The consequence is
structural and is stated in the README as well: a clean retrospective test is not achievable, every
backtest figure is a ceiling, and the forward paper trade is the verdict.

The machinery for assembling a historical corpus is not documented here. See `local/`.

## Scale ballpark (~5-year weekly backtest)
~260 weekly scans · **~50–80 distinct events** (≤~150 worst case) · **~65–100 distinct gems/vehicles**
· **~1,000–1,500 journal entries** · ~3–8 concurrent live events · **~1–2 MB** on disk. Small data ,
the format choice is about ergonomics (re-reads, diffs, revisions, cross-run comparison), not scale.

## Harvesting the distribution: the eval strategy **[CURRENT]**

Event-driven runs are heavy-tailed: BWET is a tail outlier, and below it sit progressively more numerous, smaller analogs. So the objective is to **harvest the distribution**, reliably ride the many medium-tier events, not to time one jackpot. The **locked ambition test set** is `data/fixtures/gems.json` (14 gems, window 2022-09 → present, US-listed incl. ADRs/ETFs), balanced across verticals (AI, nuclear, crypto, healthcare, defense, shipping, EM-energy, materials, consumer, precious-metals) and geopolitical types (war ×2, election, trade-war):

> CVNA ~100× · PLTR 32× · NVDA 17× · SMR 16× · SMCI 14×↘ · MSTR 13× · HIMS 11× · RNMBY 8× · BWET ~8× · MP 6.5× · YPF 4.4× · GDX 3.5× · URA 3.2×, plus PTON (a slow-fizzle *negative control* for the exit engine).

Of these, **6 are built and tested so far** (BWET, MP, GDX, SMR, RNMBY, GEO+MSTR); the rest remain the locked ambition. The eval measures **recall** (how many gems the firehose catches) and the **exit engine** (does it cut a decaying thesis); **precision** (false positives, does it also grab hyped names that fizzle?) is measured separately by the realistic GDELT-noise run.

## Backtest surfaces & pipeline **[CURRENT]**

**Pipeline.** `firehose.py` runs the single-scan curator; `agent.py` runs the scout→event-agent curator (the current engine). Both hand the live watchlist to the reused mean-variance optimizer (`investor_profile.md` knobs); `scripts/run_harness.py` scores either against the gem set; the dashboard renders the portfolio. Every LLM call is priced into `data/llm_costs.csv`.

**Two backtest surfaces.**
- `firehose.py --fixture`, a look-ahead-clean **mechanics** test against a fixed article set (perfect-retrieval assumption): given the early articles, the engine enters BWET on its first under-the-radar write-up and rides it while the Iran/Hormuz thesis is live (~+220% vs SPY ~+9%, BWET-only). An upper bound on the mechanics, not lift.
- `firehose.py --gdelt --seed <file>`, a **realistic** backtest: real date-honored GDELT headlines per week (`src/gdelt.py`) + the early niche pieces GDELT misses, seeded at their true dates. The curator must *find* the gem in genuine noise, the fast dev loop for hunting weaknesses (it drove a sticky-hold, selectivity/vehicle-selection, and ticker-validation hardening). **The [live dashboards](README.md#live-dashboard) render this surface with `--enrich` Wayback ledes added** (event-first agent + GDELT/Wayback/seeds, English-filtered), one per gem (BWET, MP, GEO+MSTR, RNMBY, GDX, SMR), each showing the catalyst-gated agent finding its gem in genuine noise, holding while the thesis is live, and exiting when the catalyst resolves. Retrieval is clean now (non-English **0%**). Sizing knobs (`concentration_cap`, `min_trade_size`, `lookback_period_days`, `risk_aversion`) were settled on the [parameter-sweep dashboard](https://joehahn.github.io/geo-herd-rider/sweeps/); the gem dashboards render the chosen defaults (cap 1.0 · lookback 14 · min_trade 0.0 · risk_aversion 0.1 · max_agents 7 · spy_agent 5 · gold-agent 5, model Sonnet-5), a concentrated, low-risk-aversion tilt (a forward-test candidate, not validated). Returns are hindsight upper bounds.

## Backtest roadmap **[CURRENT]**

We harden the engine on a widening historical slice, one rung at a time:
1. **BWET alone**, lock the mechanics on the single motivating gem (enter early, ride, exit on resolution).
2. **BWET + its two nearest-in-time gems**, confirm the scout/matcher keep separate events separate and the optimizer shares capital sanely across a handful of concurrent events.
3. **The full locked gem set** (`data/fixtures/gems.json`), recall / precision / tail / exit across all verticals and geopolitical types.

Later phases extend beyond backtesting and are intentionally out of scope for this README; they'll be folded back in once we get there.
