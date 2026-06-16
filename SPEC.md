# geo-herd-rider — spec (the implication-ladder portfolio)

**Author:** Joe Hahn (jmh.datasciences@gmail.com)
**Status:** Step 1 done (curator + scoreboard backtest, single trigger source, PASS).
Next up: Step 2 (Polymarket). Seed brief — read this first.
**Lineage:** a fresh repo that crosses the proven production spine of
[`portfolio-wave-rider`](https://github.com/joehahn/portfolio-wave-rider)
(LLM-curated watchlist → mean-variance optimizer → dashboard/backtest, where the LLM
never forecasts magnitude) with the multi-source signal layer and falsification
scoreboard prototyped in [`geo-wave-rider`](https://github.com/joehahn/geo-wave-rider)
(see [`prior-work/geo-wave-rider-phase1.md`](prior-work/geo-wave-rider-phase1.md)).

## The thesis in one paragraph

`portfolio-wave-rider` rides slow thematic waves on a quarterly cadence. geo-herd-rider
keeps that exact spine but swaps the *thesis* and widens the *signal layer*. The new
thesis is the **investor-herd diffusion model**: the mass of investors moves slowly
toward greener fields, slowed by poor signals, conflicting directions, and friction. The
edge is not predicting how big a move will be — it is **being early to where the smarter
part of the herd is already heading, and arriving a little sooner than the rest of the
herd.** The curator's new core skill is **causal-chain reasoning**: take a geopolitical
or macro event and trace its laddered downstream implications to the instruments at the
end of the chain — the carriers→Hormuz→tanker-rates→dry-bulk logic, generalized.

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

- **Hop 1 (direct / obvious).** "Iran war de-escalates → crude falls." The herd is here in
  *minutes*; futures reprice on the headline. **Already grazed — no edge.**
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
- **Step 2 — add Polymarket** (event discovery + probability/eligibility). Keep iff lift.
- **Step 3 — add Fed comms + congressional trades** (confirmation). Each gated by lift.
- **Step 4 — multi-source fusion** into one watchlist, with per-source attribution.

## Scope for the next increment (Step 2)

Step 1 paid (curated book PASSES the fixed bar). Next: **add Polymarket** for event
discovery + probability/eligibility, gated by the same lift bar — kept only if it improves
the backtest over the Step-1 trigger-only config. Still **no** Fed / congressional feeds
and no forward logger until Polymarket pays. First decision to resolve: deferred #2 (data
access — which Polymarket endpoint, at what cost). Confirm scope before building.

## Deferred decisions (revisit when the need arises)

1. **The lift bar** — *FIXED at Step 1 (do not re-tune):* a config passes iff its curated
   book's **annualized excess over SPY buy-and-hold, net of costs, is > 0** (`curator.py`
   `GATE_ANNUAL_EXCESS`). A later forward eval may add a hit-rate prong.
2. **Data access** — Polymarket API, congressional-trade source (QuiverQuant / Capitol
   Trades), Fed-comms ingestion: which are in reach, and at what cost? *(open — Step 2.)*
3. **Cadence** — *FIXED at Step 1:* **per-event horizon** (enter at the trigger, hold the
   event's `horizon_days`, exit; the daily book holds equal capital across active trades,
   cash when idle). Revisit if multi-source fusion needs a common grid.

## Risks (named, not hidden)

Deep ladders are seductive storytelling; public events get priced fast; survivorship bias
is everywhere; the herd is faster than it looks; and a retrospective backtest cannot fully
prove a forward edge. The design is built to fail loudly and cheaply when a rung doesn't
pay — the same way geo-wave-rider's Phase 1 did.
