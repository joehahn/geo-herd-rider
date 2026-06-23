# TODO — backlog (not yet scoped into a scoreboard-gated step)

Actionable ideas parked here until promoted into a scoreboard-gated step. See
[`CLAUDE.md`](CLAUDE.md) for the rules and [`README.md`](README.md) for the current design.

## Standing risks (carried from the retired SPEC)

Deep ladders are seductive storytelling; public events get priced fast; survivorship bias is
everywhere; the herd is faster than it looks; and a retrospective backtest cannot prove a forward
edge (every historical number here is an upper bound — the forward eval is the only clean test).
The design is meant to fail loudly and cheaply when a rung doesn't pay.

## Regime-contrast study: loud geopolitics vs. quiet stretches

Run the solution across **matched multi-month windows** and check whether it behaves the way
the thesis predicts — finds signal when the world is loud, and a clean **null** when it isn't.

- **~3 loud, market-moving geopolitical events**, each lasting several months:
  1. The Iran war (Israel–Iran escalation, 2024–25).
  2. _TBD_ (candidates: a Russia–Ukraine phase; the 2025 tariff war; a Fed pivot window).
  3. _TBD_.
- **~3 multi-month "quiet" stretches** with no dominant geopolitical driver, as controls.
- Restricted to **the period Polymarket data exists** (roughly 2024+ for rich coverage).

**Success looks like:** the curated middle-band book beats SPY in the loud windows (a "hidden
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
  seedless loop (discover → potential→actual via Polymarket odds → ladder → book), minus the
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
validation gate. Note: with SPY as both the idle holding *and* the benchmark, the book only
outperforms via the gems' **excess over SPY** — exactly the quantity we want to isolate, so the
scoreboard becomes even more essential. Decided to defer; no code change for now.
