# TODO — backlog (not yet scoped into a scoreboard-gated step)

Actionable ideas parked here until promoted into a scoreboard-gated step. See
[`CLAUDE.md`](CLAUDE.md) for the rules and [`README.md`](README.md) for the current design.

## Current plan — ordered (2026-07-03, soonest first)

1. **Complete GDX seeding + analysis** — genuine-seed re-runs (in progress). Names GDX → add to sweeps + un-gray; else lock the triple-confirmed non-fit.
2. **Review RNMBY gem** — add its missing storyline, drop the stale 2022 seed, decide sonnet4→sonnet5 re-scan.
3. **Replace synthetic seeds with news-derived, all gems** — real URLs + verbatim ledes for every gem.
4. **Review agent-conviction mechanics** — verify conviction assignment + the max_agents / spy-floor ranking do what we think; never leaks into sizing.
5. **1-ticker-per-agent (current) vs 1-agent-juggles-many** — scoreboard A/B; user prefers multi-ticker; maturity-tag (early/crested gate) folds in here.
6. **Update README + diagrams** — refresh to the settled design.
7. **GDELT → BigQuery migration** — kill the cold-scan hangs.
8. **Single data pull 2024 → end-of-BWET era** (after BigQuery) — replace the overlapping-scan hodgepodge.
9. **Pivot to forward testing (LAST)** — the only clean scoreboard (`forward.py`); run it after the infra is solid.

**Done:** label seeds synthetic, review GDX, delete unused exit knobs, Sonnet-5 eval (now default), 7-model bake-off, SPY-as-idle-holding.

**Dropped (not must-have; revisit only if forward proves out):** regime-contrast study, seedless backtest v1, structural-graph curator features, telegraphers/influencers roster, Fable-5 eval, resolved-catalyst-ledger windowing.

## #2 — Replace synthetic seeds with genuine articles (= P3 above)

The 21 seed entries in `data/fixtures/*seed*.json` are **hand-authored catalyst descriptions**,
not retrieved articles (20 of 21 have blank URLs), and they are pre-written in the scout's own
"still-early / under-the-radar / smart-money-first" framing — so seeded backtests are hindsight
**upper bounds**, not proof the live firehose would have surfaced the early naming. Replace them
with **genuine dated articles** (real URLs, publish dates verified to predate the move).

- **Caveat:** no search tool gives true point-in-time retrieval (non-negotiable #4), so this is
  partial mitigation, not a clean test — the forward paper trade remains the only clean test.
- **Already done (#1, 2026-07-03):** the synthetic nature is now labeled honestly — dashboard
  Plot-1 ⭐ hover + caption say "synthetic seed … not a retrieved article", and the README's
  inaccurate "real early articles, harvested / genuine, date-stamped news" language was corrected.

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

## Evaluate stronger curator models — Sonnet 5 / Fable 5 (not urgent)

**Claude Fable 5** (`claude-fable-5`, announced 2026-07-01) is the priciest frontier tier —
**$10/$50** per M in/out (vs sonnet-4.6 $3/$15, opus-4.8 ~$5/$25) — built for the hardest,
longest-horizon reasoning ("plans across stages, checks its own work"). It lands exactly on our weak
spot: the nuanced entry/exit judgments (pending-vs-in-force catalyst, don't-re-chase-a-resolved-catalyst).
A 3-gem scan would run ~$12–18+, so it's only worth it **if the scoreboard shows lift over sonnet** (#3).
Best used via the **advisor strategy** — cheap worker (sonnet/llama4) for the high-volume scout/agent
calls, consult Fable 5 only at the hard exit/entry decisions (near-frontier judgment, not frontier cost).
Integration: add `claude-fable-5` to `CURATOR_MODELS`; `src/llm.py` already uses the adaptive-thinking +
effort path it needs. Safety classifiers (cyber/bio) + 30-day retention don't affect our news-curation use.

**Claude Sonnet 5** is "near-Opus intelligence at Sonnet pricing" — a plausible
better-and-cheaper replacement for the current `sonnet` (claude-sonnet-4-6) curator. In our test,
sonnet-4.6 already dominated llama4 (5 named events for MP vs 13–27; +436%/−25.5% MP; clean
discrete exits), so Sonnet 5 should be at least as good and cheaper (intro **$2/$10** vs 4.6's
$3/$15 through 2026-08-31, roughly cost-neutral after its 1.0–1.35× tokenizer bump).

- **Integration is one line** — `src/llm.py` already sets no `temperature`/`top_p`/`top_k` and
  already uses `thinking:{type:adaptive}` + `output_config:{effort:...}` (Sonnet 5's required model;
  `budget_tokens`/extended-thinking are gone). Just add the model ID to `CURATOR_MODELS` in
  `src/optimizer.py` (need the exact ID) and set `model: sonnet5` in `investor_profile.md`.
- **Validate:** one 3-gem re-scan on the warm caches (~$3–4 intro, ~1 hr), A/B vs sonnet-4.6 on
  returns/sprawl/exits; keep only if it adds lift (scoreboard, CLAUDE.md #3/#5).
- **Won't fix the open-ended-catalyst exit** (MP-type) — that's not model-IQ; an ongoing curb never
  "resolves". The price trailing-stop remains that fix regardless of curator model.
- **Advisor-strategy option** (Sonnet 5 executor + Opus advisor at key moments) maps naturally onto
  our engine: cheap per-event agents on Sonnet 5, consult Opus only at the hard calls (the matcher /
  the exit). Near-frontier judgment at Sonnet cost. Park as a later upgrade.

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

## Regime-contrast study: loud geopolitics vs. quiet stretches

Run the solution across **matched multi-month windows** and check whether it behaves the way
the thesis predicts — finds signal when the world is loud, and a clean **null** when it isn't.

- **~3 loud, market-moving geopolitical events**, each lasting several months:
  1. The Iran war (Israel–Iran escalation, 2024–25).
  2. _TBD_ (candidates: a Russia–Ukraine phase; the 2025 tariff war; a Fed pivot window).
  3. _TBD_.
- **~3 multi-month "quiet" stretches** with no dominant geopolitical driver, as controls.
- Restricted to **the period Polymarket data exists** (roughly 2024+ for rich coverage).

**Success looks like:** the curated middle-band portfolio beats SPY in the loud windows (a "hidden
gem"), and shows **no edge** in the quiet windows (confirms the null — the system shouldn't
manufacture signal from noise). Either outcome is informative; a manufactured-signal result in
quiet periods would be a red flag worth acting on.

### Prerequisites & honest caveats (read before starting)

- **Hindsight contamination (the big one).** The curator LLM was trained past 2024–25, so a
  retrospective run over these windows is contaminated at the *curation* layer — not fixable
  by price hygiene. Treat this as a **structural sanity check** (does behaviour differ
  sensibly across regimes?), not clean proof. The cleanest version uses windows *after* the
  model's training cutoff, or a model with an earlier cutoff — which collapses toward the
  forward eval ([`src/forward.py`](src/forward.py)).
- **Polymarket historical odds.** The free CLOB endpoint gives coarse/empty data for resolved
  markets (deferred decision #2). Including the probability signal here needs on-chain
  reconstruction or a third-party historical dump; otherwise run the trigger→curator→backtest
  legs odds-free and add probability only in the forward eval.
- **Inputs to build.** A per-window `events.csv` of that period's real triggers (loud events
  + a representative sample for the quiet windows), then `map_event → score → curator --backtest`
  per window, compared against each window's SPY.
- **Pre-register the contrast** before running (which windows, what "edge" threshold) so the
  loud-vs-quiet comparison can't be tuned to the data — same discipline as the Step-1 bar.

## Curator model bake-off: which LLM is good enough?

Hold the events + scoring **fixed** and vary **only the curator model** — Opus 4.8, Sonnet 4.6,
and open-weight models via OpenRouter — then measure whether the cheaper curators produce
ladders that *score* comparably (excess vs SPY on the same window). The curator's causal-ladder
reasoning is the variable under test, so this is **not** a reason to downgrade the eval model;
it's a separate experiment about the model itself.

**Why it's worth it:** forward operation runs many triggers over time, so "cheapest model that's
good enough" is a real cost question (the absolute savings on any one backtest are small — the
*answer* is the prize). It's also sharp goal-2 content: "benchmarked frontier vs. open-weight
LLMs on multi-hop causal-ladder curation."

**Recycle from [`diplomacy-A2A`](https://github.com/joehahn/diplomacy-A2A):**
- the `LLMClient` interface pattern (`diplomacy_a2a/llm/` — provider-agnostic, `AnthropicClient`
  the only impl today) → the seam `map_event.py` lacks; add an OpenRouter impl alongside.
- the model-capability comparison methodology (`results/model-capability/findings.md`,
  counterbalanced across models — already included an open-weight model, MiMo).

**Mechanics & prereqs:**
- OpenRouter is OpenAI-compatible → open-weight path = `openai` SDK + OpenRouter `base_url` +
  `OPENROUTER_API_KEY` in `.env` (gitignored, like the Anthropic key). Deliberately steps
  outside the Claude-only setup.
- **Scaffold first:** refactor `map_event.py` behind an `LLMClient`-style interface (Anthropic +
  OpenRouter impls), so the curator is one flag. Defer until there's a window to test on.

**Controls & caveats:**
- **Web-search confound.** `map_event.py` uses Anthropic server-side `web_search` for
  pre-catalyst context; open-weight models via OpenRouter don't have it. Opus-with-search vs.
  open-weight-without-search isn't apples-to-apples — run the bake-off with **web search OFF for
  all models** to isolate pure reasoning (or wire a shared search tool for all).
- Open-weight doesn't fix hindsight contamination — these models also have training cutoffs that
  may postdate the events.
- Open-weight JSON reliability varies; `map_event._extract_json` is already tolerant, but expect
  more parse retries.

## Manage the telegraphers & influencers (a curated trigger-source roster)

Build and maintain a roster of the social-media figures whose posts are worth ingesting as
triggers — the "where the herd is heading, a little sooner" sources for Step 3's
politician/business-leader feed. Trump is the prototype; the point is to manage the *set*.

- **Tier by signal type** (the two are not the same bet):
  1. **Genuine telegraphing of intent** — a real causal ladder the curator can ladder down
     (post → policy/business change → vertical → instruments). Highest value, the middle-band
     bet. Candidates: **Trump** (tariff/trade threats, Fed criticism, named-company attacks —
     the *category* of post routes the vertical), **Elon Musk** (Tesla/SpaceX/xAI direction,
     crypto), **Bill Ackman** (activist positions, macro theses), policy officials (Commerce/
     Treasury on trade, HHS → pharma).
  2. **Pure sentiment / reflexivity** — meme-stock & crypto-principal posts (Ryan Cohen, Keith
     Gill, Cathie Wood, CZ, Vitalik). The "signal" *is* the crowd reaction; no deeper chain, so
     it's hop-1-obvious and decays fast. Likely **exclude** or down-weight — front-running a
     stampede isn't the thesis.
- **What "manage" means:** a maintained list (handle, platform, tier, post-category→vertable
  routing notes), an add/drop process gated by the scoreboard (a source is kept only if it adds
  lift — non-negotiable #3), and de-duplication against Polymarket discovery (an influencer post
  and a moving market may be the same event).
- **Prereqs / open questions:** post-archive data access + date-filtering for look-ahead hygiene
  (same unsolved question as the seedless backtest's Trump-tweet feed); rate/cost of high-volume
  posters (Musk); and confirming this stays gated behind the Polymarket-signal forward result
  before it becomes Step 3 work (CLAUDE.md scope discipline).

## Seedless backtest v1 — does it find the event on its own?

Drop the hand-seeded per-window `events.csv`. Instead feed the solution a stream of historical
inputs and let it **discover its own triggers**, then run the pipeline forward over the stream.

- **Inputs (start at Haiku's ~mid-2025 training cutoff → present):** historical news, ticker
  data (yfinance), and **Trump-only tweets** (for now — narrowest, highest-reach trigger source).
  Post-cutoff framing is deliberate (discipline #5): the curator is largely blind to these
  outcomes, so the run is far less contaminated than a pre-cutoff one.
- **What we're testing (the deep dive, not the return):**
  1. **Detection** — does the solution flag the expected geopolitical event (the 2026 Iran
     run-up: carriers to the Med, the "help is on the way" Trump tweet) as a *potential* event,
     and convert potential → actual right before/during the strike? Or does it latch onto
     something else? Either is informative.
  2. **Logic** — dive into and **visualize the LLM's decision trees / ladders** to spot-check
     the reasoning (is the carriers→Hormuz→tanker→dry-bulk chain the kind of thing it builds, or
     is it storytelling?). The decision tree is the artifact to inspect, separate from the P&L.
- **Caveats:** still partly contaminated (search returns articles written with hindsight even
  under `before:<date>`); Trump-tweet + news data access is the new data-access question to scope
  (cost, archive, date-filtering). Return is an upper bound (discipline #5).
- **Relation to the forward engine:** this is the retrospective rehearsal of the autonomous
  seedless loop (discover → potential→actual via Polymarket odds → ladder → portfolio), minus the
  dashboard. The clean version is still forward.

## Structural-graph curator features (convergence + centrality) — NOT EV search

The implication "tree" is really a DAG: independent chains converge on the same instrument.
Worth exploiting **as topology, never as magnitude**. The trap to avoid: ranking branches by
*expected profit* requires payoff estimates on nodes + probabilities on edges — if the LLM
supplies those numbers it is the falsified wave-tilt mistake reborn (non-negotiable #1), and a
profit-maximizing graph search structurally rewards depth/connectivity, i.e. exactly the hop-4+
storytelling the thesis warns against. So the idea is **structural features**, not graph search:

- **Convergence count** — how many *independent* ladders reach a node. A count, not a return
  forecast, so it's discipline-safe; a ticker corroborated by N separate triggers is higher
  conviction. Today `map_event.py` flattens each event to one ladder + scalar `chain_depth`, so
  convergence is invisible to the curator.
- **Centrality / chokepoints** — high-betweenness nodes (oil, SPY) are seen by herd *and* smart
  money → already priced → avoid. The middle-band heuristic restated graph-theoretically.
- **Probabilities stay external** — edge "will the upstream resolve?" odds come from Polymarket
  (Step 2's whole point), never the LLM. Features get **scoreboard-gated** like everything else.

**Feasibility probe already run (2026-06-19):** convergence exists but is sparse — across the 26
events in `data/events_mapped.csv`, 8 tickers are named by >1 event (QQQ×3; XLE/F/GM/COIN/TSLA/
ORCL/ITA×2) vs 25 singletons. So the structure is real but thin. The full test — does
convergence predict excess return? — is **underpowered today** (only 5 trades in
`backtest_trades.csv`); defer it until more trades accrue. **Gated behind Step 2's forward lift**
(scope discipline) — don't build the per-event graph until convergence is shown to pay.

## SPY as the idle/fallback holding (deferred 2026-06-22)

Make **SPY** (or a small SPY sleeve) the default holding when no gem is live, instead of the
current **cash @ 0%** — capture market beta while idle rather than sitting flat. A *risk*
improvement, and complementary to (not a replacement for) the forward scoreboard, which stays the
validation gate. Note: with SPY as both the idle holding *and* the benchmark, the portfolio only
outperforms via the gems' **excess over SPY** — exactly the quantity we want to isolate, so the
scoreboard becomes even more essential. Decided to defer; no code change for now.
