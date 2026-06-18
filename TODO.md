# TODO — backlog (not yet scoped into the SPEC baby-step ladder)

Actionable ideas parked here until promoted into a scoreboard-gated step. See
[`SPEC.md`](SPEC.md) for the committed plan and [`CLAUDE.md`](CLAUDE.md) for the rules.

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
