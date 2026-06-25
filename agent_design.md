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

## Vehicle admissibility (what can be a vehicle)
A ticker qualifies as a vehicle iff: (a) the **press names it**; (b) it's **retail-tradable and
yfinance-priceable**; and (c) the **mechanical mean-variance optimizer can size it as a held position
WITHOUT the LLM making a magnitude / leverage / expiry call**. That admits **US-listed stocks, ADRs,
ETFs, ETNs** (BWET is an ETN), and equity wrappers like REITs/CEFs and bond/commodity ETFs (they're
just ETFs/equities). It **excludes options and futures** — both require a strike/expiry/leverage
decision, i.e. *magnitude*, which violates the load-bearing no-magnitude guardrail (non-negotiable #1)
and can't be priced/sized cleanly; commodity/rate exposure is taken via ETFs/ETNs instead. Leveraged/
inverse ETFs are *technically* admissible but discouraged (path-dependent decay corrupts multi-week
holds + the μ/Σ fit). Spot crypto and prediction markets (Polymarket) are out of scope (former leaves
US-listed; latter is an event-probability signal, not a mean-variance-sizable position).

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

## Sticky hold (hysteresis) **[CURRENT]**

`firehose._stateful_watch(scans)` turns the **stateless** weekly scans into a **sticky position
portfolio** — and it's what the backtest sizes each week (`watch = _stateful_watch(scans)` at the top of
`backtest()`), not the raw per-week `thesis_live`.

**The problem.** Each weekly scan is an independent read: a name can be `thesis_live=true` one week,
go unmentioned the next (the press just didn't cover it that week), then return. Holding strictly on
"is it in *this* week's scan" would churn positions on coverage gaps and one-off noise — paying
costs and, worse, dropping a still-valid thesis on silence. The GDELT-noise run exposed exactly this
trigger-happy exit.

**The mechanism — easy to enter, deliberately hard to exit.** Walking anchors in order, it carries
per-ticker state — `holding`, a `dead` counter, a `stale` counter — and each week:
- **Enter / refresh** — any ticker read `thesis_live=true` → held, both counters reset to 0. Entry
  is immediate (one live read); any live mention re-arms a held name's patience.
- **Explicitly flagged dead** (held, this week `thesis_live=false`) → `dead += 1`; exit only at
  **`EXIT_PATIENCE` = 2** consecutive dead reads. One "thesis is over" week does **not** exit.
- **Unmentioned** (held, absent from this week's scan) → `stale += 1`; exit at **`MAX_STALE` = 4**
  silent weeks. Silence ≠ death, but indefinite silence eventually exits.

**Why asymmetric.** 1 read to enter vs. 2 consecutive explicit-dead reads (active resolution) or 4
silent weeks (passive timeout) to exit — that asymmetry *is* the stickiness. The counters track
*consecutive* conditions: a dead-flag resets `stale`, a live-flag resets both.

**Knob status.** `EXIT_PATIENCE` and `MAX_STALE` are **hardcoded module constants in `firehose.py`**,
not in `investor_profile.md` — so unlike `concentration_cap` / `min_trade_size` they aren't swept
through config. They are behavior-affecting (guarded by the golden regression check) and are
**candidates to promote into `investor_profile.md`** if exit-stickiness tuning is wanted.

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
  by default (churn). Commit the small scan-log + harness report, plus a **frozen "golden" snapshot
  per evaluated run** so the evaluated state is pinned and re-readable.
- **[CURRENT] Golden regression snapshot.** `data/golden/bwet/` freezes the scan log + price panel +
  `fm` knobs + expected backtest output; `scripts/check_golden.py` replays it to prove a CODE change
  didn't move the portfolio (deterministic — isolates code from LLM noise and yfinance price drift).
  `scripts/build_golden.py` regenerates it for an intentional, vetted baseline change. NOTE: with a
  tight `min_trade_size` the optimizer is knife-edge — float-precision differences (e.g. in-memory vs
  CSV-loaded prices) can flip which single name a week funds; the golden derives `expected` from the
  same CSV the check reads, so it's internally consistent.
- **[PROPOSED] `decisions` provenance log.** Persist per-week scout candidates, matcher assignments,
  same-ticker-guard hits, and invalid-ticker drops — currently computed in-run and lost. Cheap to
  emit, impossible to reconstruct later; needed to audit *what the agents did*, not just what survived.

## Retrieval: GDELT and seeds **[CURRENT]**

How the backtest "reads the firehose" — the date-honest news source plus a patch for its blind spot.
Both are **backtest** mechanisms; live/forward retrieval is a separate concern (out of scope here).

### GDELT (`src/gdelt.py`) — the look-ahead-clean firehose
The **GDELT 2.0 DOC API** is the one retrieval source whose date bounds are **enforced server-side**:
a query as-of a past week returns only articles GDELT had indexed *by then* (real point-in-time
retrieval). This is what makes a retrospective backtest defensible — most tools leak the future
(Anthropic `before:` and Tavily `end_date` both return post-cutoff articles; see `src/search.py`).

- **Query.** `search(query, start, end, max_results=60)` → one `GET` to
  `https://api.gdeltproject.org/api/v2/doc/doc` with `mode=ArtList`, `format=json`,
  `startdatetime`/`enddatetime` (`YYYYMMDD000000`, the enforced look-ahead bound), `sort=datedesc`.
  No API key; rate-limited (`MIN_INTERVAL=15s` — GDELT throttles harder than its stated 1 req/5s).
  Quirk: GDELT needs **single words or quoted phrases** — bare multi-word queries return nothing.
- **Queries are theme-level, NEVER the ticker.** Pointing GDELT at "BWET" would hand it the answer;
  the curator must still *discover* the name from theme noise.
- **The query set is gem-agnostic, derived from `SCAN_SYSTEM` — not from the gems.** This is the
  crux that keeps backtest retrieval honest. Forward, the curator (`firehose.scan`) generates its
  own web-search queries from `SCAN_SYSTEM`, which names its intent gem-agnostically: *"a standout
  trade on a live thesis (geopolitics, energy/shipping, tariffs, Fed, a sector catalyst)."* So the
  backtest GDELT query set (`run_harness.HARNESS_QUERIES`) **mirrors that prompt**, not the winners:
  - **discovery superlatives** (cross-vertical): `'"best performing stock"'`, `'"biggest gainers"'`,
    `'"best performing etf"'`;
  - **the macro beats the prompt names**: `geopolitics`, `war`, `shipping`, `tariffs`, `'"interest rates"'`
    (`war` retrieves kinetic-conflict coverage the literal term `geopolitics` misses on GDELT);
  - **an EVEN top-level sector sweep** — the COMPLETE 11-GICS partition + crypto: technology /
    energy / financial / healthcare / industrial / materials / consumer / utilities / real-estate /
    telecom (Communication Services) / crypto — chosen so every gem is reachable via its **sector**,
    never its **sub-niche**. Gem-agnostic *by construction* (a complete partition privileges nothing).
  - **a small emerging-tech THEME layer** — `space` / `robotics` / `quantum` / `nuclear` stocks. This
    one is a *pre-registered forward thesis* (from portfolio-wave-rider), **not** part of the
    gem-agnostic partition: GICS is too coarse to surface a niche space/robotics/quantum/SMR gem (a
    space play is buried in Industrials). Provenance keeps it honest — it's an **independent forward
    thesis fixed before the eval** (CLAUDE.md #5), not reverse-engineered from our winners — but it
    *is* thesis-driven, so recall on themed gems (SMR/URA via `nuclear stocks`) is **partly
    thesis-aided**, reported as such. The line we hold: broad standing themes are allowed; **ticker-
    exact sub-niches are not** — `uranium` (→URA), `"rare earth"` (→MP), `"weight loss drug"` (→HIMS),
    `Milei` (→YPF) stay excluded (those reverse-engineer the query from the specific winner).
  Why this matters: deriving terms *per gem* (e.g. `uranium`, `"rare earth"`, `"weight loss drug"`,
  `Milei` — the old list) reverse-engineers the query from the winners, which inflates recall **by
  construction** and predicts nothing forward. Pre-registering such a list does NOT fix it —
  freezing the answer key in advance is still using the answer key. Querying at the **standard
  sector level** (materials, not "rare earth") is the de-contamination: it's the partition a desk
  watches regardless of outcome, so overlap with a gem's sector is editorial coverage, not hindsight.
- **The list is FROZEN, not LLM-generated per week (backtest only).** Forward, letting the model
  pick weekly queries is correct and clean. In backtest the curator is trained past these events, so
  asking it "what would you search the week of 2026-02-06" leaks (it'd search tanker rates *because*
  it knows BWET ran). So backtest uses the fixed `HARNESS_QUERIES` stand-in. (`firehose.GDELT_QUERIES`
  is a separate, event-scoped default — Iran beats like `Hormuz`/`"tanker rates"` — for a single-event
  run; legitimate there because the event, not the ticker, picks the beat.)
- **Pool.** `pool(queries, start, end, chunk_days=30, per=60, cache_path=…)` runs every query across
  **date chunks** (so `datedesc`+`maxrecords` doesn't over-weight the latest weeks — forces even
  time coverage), dedupes by URL, and **checkpoints after every (query, chunk)** so a long throttled
  fetch survives sleep/kill and **resumes** (atomic tmp+replace). Cached pools are gitignored
  (`gdelt_pool_*.json` broad; `gdelt_event_*.json` per-event).
- **Returns headline-level only — no body:** `{published_date, source (domain), title, snippet
  (=title), url}`; records missing a date or URL are dropped.
- **Two roles.** Single-scan `--gdelt` builds ONE broad pool for discovery (each week feeds ≤
  `GDELT_WEEK_CAP=80` headlines to the curator). The event-first agent's `targeted_pool` builds a
  PER-EVENT pool from that event's own terms (incl. resolution coverage, e.g. a ceasefire) to
  *monitor* it — discovery stays broad, only monitoring is targeted, so targeting can't bias what's
  discovered.
- **Caveat that drives seeding:** GDELT **under-indexes niche trade press**, so it MISSES the early
  under-the-radar pieces (the etf.com "flown under the radar" BWET write-up) and only picks a gem up
  once mainstream piles in — i.e. *late*. **Why it misses them:**
  - **Source coverage skews mainstream.** GDELT crawls a fixed, large-but-finite list of monitored
    outlets weighted toward high-volume, widely-syndicated mainstream news; small specialist
    publications (etf.com, maritime/freight trade desks, niche finance blogs) are sparsely monitored
    or absent — so the early piece often isn't in the index at all.
  - **Low-volume articles rank/surface poorly.** An "under-the-radar" piece is by definition one
    outlet with few republications; even when GDELT has it, the per-query `maxrecords` cap plus
    relevance/volume ordering let the flood of mainstream coverage for a theme crowd it out of the
    returned set.
  - **Indexing lag.** GDELT picks a story up once it propagates across its monitored sources, which
    biases what's retrievable toward the moment a story goes mainstream — exactly *after* the early
    naming we want.

### Seeds — patching the early-coverage blind spot
A **seed** is a hand-collected real article GDELT misses, recorded with its **true publish date** and
injected into the firehose so the curator can see it the week it actually appeared.

- **Format** — a JSON file with an `articles: [{published_date, source, title, snippet, url}]` list
  (`data/fixtures/firehose_bwet.json`, `data/fixtures/gems_seeds.json`); `--seed <file>`.
- **Date-honest injection** — each weekly scan slices seeds to its trailing window via `_window`
  (`lo < published_date <= anchor`), exactly like GDELT articles, so a seed is never visible before
  its true date. Seeds are placed **first and never truncated** by `GDELT_WEEK_CAP` (the cap only
  drops surplus GDELT headlines), so a niche early piece can't be crowded out by mainstream noise.
- **`--fixture` vs `--gdelt --seed`** — `--fixture` runs *only* the seed set (assumes perfect
  retrieval → a clean **mechanics** test, no GDELT noise). `--gdelt --seed` is the **realistic** run:
  GDELT noise the curator must hunt in, plus the early pieces seeded back at true dates.
- **Honest status (the reason this is a backtest-only shortcut):** clean point-in-time retrieval of
  these niche early pieces is **not achievable with available search tools**, so seeding *grants* the
  early naming rather than proving we could retrieve it. Every seeded number is therefore an **upper
  bound** — it shows what the mechanics do *given* the early article, not that we'd have found it.

### Decision matrix — which retrieval tool, by time direction
The settled architecture: **each direction uses the tool that is look-ahead-clean *for that
direction*.** A historical web search is *not* a peer of GDELT — it silently re-imports the future.

| | Discovery (which articles existed) | Content (headline + snippet) |
|---|---|---|
| **Backtest** | **GDELT** (date-honest, chronological) | seeds (default) + **Wayback** enrich (built, opt-in `--enrich`, under validation) |
| **Forward** | **Anthropic web_search** (clean by construction) | web_search (returns excerpts) |

**Why GDELT for backtest discovery — three independent axes, all clean:**
1. **Date bounds** are server-enforced (`enddatetime`) — a past-week scan can't see later articles.
2. **Content is as-of** at the index level (it returns what was indexed by then), not today's edited page.
3. **Ranking is chronological** (`sort=datedesc`), NOT relevance/authority — so an article isn't
   boosted because it *later* became famous. Consumer search (Google/Bing/Anthropic web_search) ranks
   by accumulated links/clicks that pile up *after* the news date, which floats a gem's early article
   to the top of a historical query — pure hindsight. GDELT has no such lever.

**Why historical web_search is disqualified for backtest** (doable, but deceptive): it fails all
three — `before:`/`end_date` **leak** post-cutoff articles (and forward there's no future to leak, so
the same tool is *clean* forward); it returns **today's edited** page; and it is **relevance-ranked**,
boosting what hindsight made important. The leak is categorical (6 months of hindsight to grab in
backtest vs. nothing forward); ranking + edits are large-for-gems matters of degree.

**The headline→snippet asymmetry (why Wayback exists).** GDELT returns **headlines only**, and
empirically the headline names the *theme/event* but rarely the *ticker* (0 of 18 seed headlines name
the ticker — the "(BWET)" lives in the lede). So a GDELT-discovered gem often gives the curator the
right vertical but not the vehicle — the documented realistic-GDELT failure ("right theme, wrong
vehicle: GGAL not YPF, CCJ not URA"). Today **seeds mask this**, because seed records carry a curated
snippet that *does* name the ticker — which is an extra reason seeded numbers are upper bounds.
**Wayback enrichment** is the look-ahead-clean fix: GDELT *discovers* the URL; Wayback fetches that
URL's **as-of-date** snapshot (CDX, snapshot ≤ anchor) and extracts the lede/meta-description — adding
the ticker-naming snippet without any of web_search's three leaks (it's URL-keyed archival, so no
ranking, no edits, no date-leak). It is **enrichment, not discovery** (can't ask Wayback "tanker news
in March") — so the stack stays **GDELT discover + Wayback enrich + seeds for GDELT's discovery
misses** (the niche pieces GDELT never indexes; Wayback can't enrich a URL we don't have).

**Status / scope.** Wayback enrichment is **built** (`src/wayback.py`, opt-in `--enrich` on
`run_harness --event-first`) and **currently being validated on the BWET era** — *not yet the
default*; seeding still supplies the naming, and whether `--enrich` lifts realistic vehicle recall is
the open question. It earns its keep most at the **all-gems rung** (where wrong-vehicle matters and
hand-seeding the naming for 13+ gems stops being honest). Implementation already bounds the work:
enriches only the per-week curator slice (≤80/wk), meta-description only, URL-keyed disk cache
(`wayback_*.json`), and coverage gaps degrade gracefully to headline-only.

## Scale ballpark (~5-year weekly backtest)
~260 weekly scans · **~50–80 distinct events** (≤~150 worst case) · **~65–100 distinct gems/vehicles**
· **~1,000–1,500 journal entries** · ~3–8 concurrent live events · **~1–2 MB** on disk. Small data —
the format choice is about ergonomics (re-reads, diffs, revisions, cross-run comparison), not scale.
