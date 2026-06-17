# geo-herd-rider — spec (the implication-ladder portfolio)

**Author:** Joe Hahn (jmh.datasciences@gmail.com)
**Status:** Step 1 done (curator + scoreboard backtest, single trigger source, PASS).
Step 2 in progress (Polymarket odds module built; probability signal reshaped to a forward
eval — see deferred #2). Seed brief — read this first.
**Lineage:** a fresh repo that crosses the proven production spine of
[`portfolio-wave-rider`](https://github.com/joehahn/portfolio-wave-rider)
(LLM-curated watchlist → mean-variance optimizer → dashboard/backtest, where the LLM
never forecasts magnitude) with the multi-source signal layer and falsification
scoreboard prototyped in [`geo-wave-rider`](https://github.com/joehahn/geo-wave-rider)
(see [`prior-work/geo-wave-rider-phase1.md`](prior-work/geo-wave-rider-phase1.md)).

## The thesis in one paragraph

`portfolio-wave-rider` rides slow thematic waves on a quarterly cadence. geo-herd-rider
keeps that exact spine but swaps the *thesis* and widens the *signal layer*. The new
thesis is a **three-tier market**: **smart money** (insiders and genuine experts) has a
real edge and moves first; the **slow herd** arrives late and flattens the move once it
piles in; and we are neither — no inside information, no deep-investor edge, only **data**
(posts, news, reports, prediction markets) and **AI to read it**. The play is to use those
leading indicators to infer *where the smart money is already heading* and position
**between the smart money and the herd** — late enough that the direction is readable,
early enough to capture some of the move before the herd prices it away. We never forecast
how big a move will be — only its direction and the chain that gets there. The curator's
core skill is **causal-chain reasoning**: take a geopolitical or macro event and trace its
laddered downstream implications to the instruments at the end of the chain — aircraft
carriers to the western Med → a Strait-of-Hormuz bottleneck → tanker rates (the cost to
charter oil tankers) spike → dry-bulk rates (the cost to ship dry commodities like grain,
coal, and iron ore) follow a hop later, generalized.

## Why this design, and not the naive one

geo-wave-rider already falsified the naive version — "ride the loud post" had no edge
after costs (median −0.73% vs SPY, 42% hit, a clean NO-GO). That null is the
*justification* for this design, not a contradiction of it, and it pointed at *where* the
only flicker of signal lived: **deep causal chains off quieter sources** (chain-depth-3:
+9.42% median, 71% hit), not the megaphone calls everyone reads instantly (those lost).
geo-herd-rider is the disciplined response: be selective toward deep chains, fuse
multiple sources so no single weak signal carries the book, and keep the scoreboard as
the filter that decides what to trust. Full prior result in `prior-work/`.

## The core model: the implication ladder

Every triggering event spawns a **tree of laddered implications** with a characteristic
shape that defines where the edge is:

- **Hop 1 (direct / obvious).** "Iran war de-escalates → crude falls." The smart money is
  here in *minutes*; futures reprice on the headline. **Already grazed — no edge.**
- **Hop 2–3 (the middle band).** Cheaper energy → input-cost relief for energy *consumers*
  (airlines, freight, chemicals); Gulf stability → infrastructure capex; disinflation →
  Fed room to cut → rate-sensitives. The herd arrives over **days to weeks**, and the
  causality still holds. **This is where the money is.**
- **Hop 4+ (deep / speculative).** "Freed-up dollars → hiring boom → AI boom → robotics
  boom." The herd is nowhere near — but the causality is now a story with many ways to be
  wrong. **This is the trap:** AI generates gorgeous deep ladders effortlessly, and most
  don't pay.

**The thesis, stated falsifiably: the edge lives in the middle band of the tree — deep
enough that the herd hasn't arrived, shallow enough that the logic is still reliable.**
The curator's job is not "find implications" (easy, infinite) but **generate the full
tree and locate the band that is both unpriced and correct.** Worked examples to test the
curator against: Ukraine war ends → Putin's grip loosens → Russia/Europe repricing;
"Trump revealed China is less influential than assumed" → supply-chain / EM-weighting
implications.

## The four signal roles (multi-source fusion)

Sources are not interchangeable inputs; each answers a different question in the ladder:

1. **Trigger** — *what starts a ladder.* Posts by high-reach figures: politicians (Trump)
   **and** business leaders (Musk, Dimon). geo-wave-rider's feed + mapper is the seed.
2. **Probability** — *will the upstream event actually resolve?* **Polymarket** /
   prediction-market odds. Lets the system time and size a ladder against a real
   probability instead of a maybe.
3. **Confirmation** — *is the smart edge of the herd already turning?* **Fed** comms,
   big-bank principals (Dimon), and **congressional-trading disclosures** (Pelosi-watch).
   Ride *just behind* the smart money and *ahead* of the rest — don't try to beat it.
4. **Sizing** — mechanical. The **mean-variance optimizer** weights whatever watchlist
   results. **The LLM never forecasts magnitude** — composition, direction, ladder only.

Machine shape: *trigger → probability → AI causal ladder → smart-money confirmation →
mechanical sizing → scoreboard.*

## Architecture (reuse the sibling's spine)

- **Inputs:** `investor_profile.md` (the *herd / diffusion-lag thesis*, constraints,
  optimizer settings) and `holdings.csv` (the watchlist). A signal-source registry
  generalizes `portfolio-wave-rider`'s `news_sources.md`.
- **Curator subagent (the new brain):** evolves `portfolio-wave-rider`'s `watchlist-curator`
  (which already does add/remove-only with `before:` look-ahead discipline and a
  self-critique pass) by adding implication-ladder reasoning over the multi-source stream.
  Proposes adds/removes tagged with the causal chain, `chain_depth`, `audience_breadth`,
  and triggering source. Composition only; no weights, no magnitude.
- **Optimizer:** reused verbatim from `portfolio-wave-rider` — `src/optimizer.py`
  (`mu^T w − λ·w^T Σ w`).
- **Scoreboard:** geo-wave-rider's `src/score.py` (excess-vs-SPY, hit rate, path-shape,
  chain-depth) becomes the **per-source / per-feature evaluation** — what decides whether
  a signal source or curator change actually adds lift before it stays.
- **Dashboard / backtest / cron:** adapted from `portfolio-wave-rider` in a later step.

## What keeps it honest (pre-registered discipline)

1. **The scoreboard is the filter, not the curator's confidence.** A ladder is kept only
   if the backtest shows the resulting trades beat SPY / buy-and-hold.
2. **Per-source lift bar, fixed before running.** A new source or curator change is
   retained only if it improves the backtest over the prior config by a pre-registered
   margin. *(Threshold TBD — fix before the first run.)*
3. **LLM picks composition, direction, ladder; never magnitude.** Carried over from the
   sibling's wave-tilt postmortem (numeric LLM tilts destroyed value). Non-negotiable.
4. **Look-ahead hygiene everywhere**, plus the honest caveat that retrospective backtests
   are hindsight-contaminated — so a **forward, paper-traded evaluation matters even more
   here**, and is the only clean test.

## Baby-step ladder (each rung gated by the scoreboard)

- **Step 0 — this SPEC + foundation.** *(done)* Optimizer reused, signal+scoreboard
  recycled, prior-work captured, repo scaffolded.
- **Step 1 — one source, end to end.** *(done — PASS)* `src/curator.py` curates the
  middle band (chain_depth ≥ 2, non-megaphone) into a long watchlist, weights it with the
  reused optimizer, and the scoreboard backtest measures it vs SPY buy-and-hold at
  per-event-horizon cadence (`--backtest`). Retrospective result on the seed 26-event set:
  middle band +43% annualized excess vs SPY (PASS, bar = >0); per-event selection median
  +7.7% vs full-set −0.7%. Caveat: hindsight-contaminated and a strong-bull sample (the
  drop cohort is also positive) — the clean test is a forward paper trade (a later step).
- **Step 2 — add Polymarket** (event discovery + probability/eligibility). *(in progress)*
  `src/polymarket.py` reads market odds (live + best-effort historical), free and keyless,
  look-ahead-safe by construction. Data-access finding (deferred #2): resolved-market
  history is unavailable on the free endpoint and coverage skews political/macro, so the
  retrospective lift test is deferred — the probability signal is evaluated via **forward
  live logging**, not a seed-data backtest. Keep iff lift (measured forward).
- **Step 3 — add Fed comms + congressional trades** (confirmation). Each gated by lift.
- **Step 4 — multi-source fusion** into one watchlist, with per-source attribution.

## Scope for the next increment (Step 2, continued)

The probability signal's plumbing is built (`src/polymarket.py`: live + best-effort
historical odds, free/keyless, look-ahead-safe). The data-access finding (deferred #2)
forced a reshape: resolved-market history is unavailable and coverage skews political/
macro, so Polymarket **cannot** be lift-tested retrospectively on the seed events — it must
be evaluated **forward**. Sub-steps:
1. *(done)* The curator (`map_event.py`) now emits `polymarket_query` — the LLM phrases
   the resolvable question (or null); it no longer guesses the odds at all (the old
   `prediction_market_odds` LLM field is gone — non-negotiable #1 tightened). The odds are
   mechanical (`polymarket.py`); `score.py` calibrates on the fetched `polymarket_odds`.
2. *(next)* Build the **forward logger** that records, per fresh trigger, the live odds at
   decision time — the clean eval surface. Only then can the probability signal's lift be
   measured.
Still **no** Fed / congressional feeds until the probability signal demonstrably pays.

## Deferred decisions (revisit when the need arises)

1. **The lift bar** — *FIXED at Step 1 (do not re-tune):* a config passes iff its curated
   book's **annualized excess over SPY buy-and-hold, net of costs, is > 0** (`curator.py`
   `GATE_ANNUAL_EXCESS`). A later forward eval may add a hit-rate prong.
2. **Data access** — *Polymarket RESOLVED at Step 2:* Gamma (discovery) + CLOB
   (`/prices-history`) are free and keyless, and the contract is look-ahead-safe. But the
   free history endpoint returns coarse/empty data for **already-resolved** markets, and
   coverage skews political/macro (near-zero for single-company/equity). So a retrospective
   Polymarket lift test isn't viable on the seed events — the clean use is **live, forward
   logging** (`src/polymarket.py`). Congressional-trade source (QuiverQuant / Capitol
   Trades) and Fed-comms ingestion remain open (Steps 3+).
3. **Cadence** — *FIXED at Step 1:* **per-event horizon** (enter at the trigger, hold the
   event's `horizon_days`, exit; the daily book holds equal capital across active trades,
   cash when idle). Revisit if multi-source fusion needs a common grid.

## Risks (named, not hidden)

Deep ladders are seductive storytelling; public events get priced fast; survivorship bias
is everywhere; the herd is faster than it looks; and a retrospective backtest cannot fully
prove a forward edge. The design is built to fail loudly and cheaply when a rung doesn't
pay — the same way geo-wave-rider's Phase 1 did.
