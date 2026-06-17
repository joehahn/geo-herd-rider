# geo-herd-rider

**Author:** Joe Hahn  
**Email:** jmh.datasciences@gmail.com  
**Date:** 2026-Jun-16 <br>
**branch:** main

**Our model of the market.** Three groups move a price. **Smart money** — insiders and genuinely expert investors — has a real edge and moves first. The **slow herd** arrives late and, once it piles in, flattens the move: the opportunity is gone. We are neither. We have no inside information and no deep-investor edge; what we have is **data** (news, posts, reports, prediction markets) and **AI to read it**. The play is to use those leading indicators to infer *where the smart money is already heading* and position **between the smart money and the herd** — late enough that the direction is readable, early enough to capture some of the move before the herd arrives and prices it away.

Every event throws off a tree of downstream implications, and that in-between window lives in the **middle band** of the tree: deep enough that the herd hasn't priced it, shallow enough that the causality still holds. Hop-1 calls are grazed by the smart money in minutes; hop-4+ chains are seductive storytelling that rarely pays. We never try to predict *how big* a move will be — only its direction and the chain that gets there; sizing is left to a mechanical optimizer downstream.

**What this repo does.** An LLM reads a stream of signals, reasons out the laddered chain of implications behind a geopolitical or macro event, and curates a watchlist; a plain mean-variance optimizer then weights it. The LLM picks composition, direction, and the ladder — but never magnitude. A scoreboard backtest decides whether each source or curator change actually adds lift before it stays. **Today** it runs end-to-end on a single trigger source (politician/business-leader posts) — clearing its pre-registered backtest bar — plus a Polymarket probability signal and a look-ahead-clean forward logger; **next** it adds smart-money confirmation (Fed comms, congressional trades), one scoreboard-gated step at a time.

## The implication ladder

Every triggering event spawns a tree of downstream implications, and the tree has a shape that tells you where the money is:

- **Hop 1 — direct and obvious.** "Iran war de-escalates → oil falls." The smart money is here in minutes; futures reprice on the headline. Already grazed, no edge.
- **Hop 2–3 — the middle band.** Cheaper energy → input-cost relief for airlines and freight and chemicals; Gulf stability → infrastructure capex; disinflation → room for the Fed to cut → rate-sensitive names. The herd takes days to weeks to arrive, and the causality still holds.
- **Hop 4+ — deep and speculative.** "Freed-up dollars → hiring boom → AI boom → robotics boom." The herd is nowhere near it, but now the logic is a story with a dozen ways to be wrong. This is the trap: an AI will generate gorgeous deep ladders all day, and most of them never pay.

**The whole bet is the middle band** — deep enough that the herd hasn't arrived, shallow enough that the causality is still real. The curator's job is not to *find* implications (that's infinite and easy) but to generate the full tree and locate the band that is both unpriced and correct.

The canonical example, generalized from the move that motivated this family: **aircraft carriers steam to the western Mediterranean → the market reads a rising risk that the Strait of Hormuz is choked → tanker rates spike → dry-bulk rates follow.**

- **Tanker rates** are the price to charter an oil tanker (the *wet-bulk* freight market, tracked by indices like the Baltic Dirty Tanker Index). A Hormuz threat — the chokepoint for roughly a fifth of seaborne oil — forces tankers onto longer reroutes, drives up war-risk insurance, and tightens available capacity, so the rate to move a barrel by sea jumps. This leg is obvious and gets grazed fast.
- **Dry-bulk rates** are the price to ship *dry* commodities — iron ore, coal, grain — in the holds of bulk carriers (the *dry-bulk* freight market, tracked by the Baltic Dry Index). They rise a hop later: the same rerouting, higher bunker-fuel costs, and insurance pressure spill out of oil shipping into freight broadly, and vessels diverted or delayed tighten dry-bulk capacity too. This quieter knock-on is the middle band — the herd hasn't priced it yet, but the causal link still holds.

## Four signals, four jobs

The sources are not interchangeable inputs; each answers a different question in the ladder:

- **Trigger** — *what starts a ladder.* Posts by high-reach figures: politicians (Trump) and business leaders (Musk, Dimon).
- **Probability** — *will the upstream event actually resolve?* Polymarket and other prediction-market odds, so a ladder is timed and sized against a real probability rather than a maybe.
- **Confirmation** — *is the smart edge of the herd already turning?* Fed communications, big-bank principals, and congressional-trading disclosures. The aim is to ride just behind the smart money and ahead of everyone else, not to beat the smart money.
- **Sizing** — mechanical. A standard mean-variance optimizer weights whatever watchlist results. The LLM never touches the numbers.

The machine, end to end: *trigger → probability → AI causal ladder → smart-money confirmation → mechanical sizing → a scoreboard that keeps the whole thing honest.*

## Status

Built in scoreboard-gated baby steps (full plan in [`SPEC.md`](SPEC.md)):

- **Step 1 _(done, passing)_** — the middle-band curator + a per-event-horizon backtest vs SPY buy-and-hold (`src/curator.py --backtest`). Optimizer reused verbatim from `portfolio-wave-rider`, mapper and scorer from `geo-wave-rider`.
- **Step 2 _(built; lift pending)_** — a Polymarket probability signal (`src/polymarket.py`, with `--discover` to surface hot events), a curator-named `polymarket_query`, and a forward paper-trade logger (`src/forward.py`). Catch: Polymarket has no usable history for resolved markets and the curator LLM was trained past the events, so retrospective numbers are hindsight-contaminated. The only clean test is forward, so that's where the lift gets measured.
- **Step 3 _(waiting)_** — Fed + congressional-trade confirmation, gated until the scoreboard says Step 2 pays.

First playtest — the 2026 Trump–Iran war (`data/windows/iran.csv`), Haiku curator, under $1: +12.8% median excess vs SPY, 70% hit on the matured trades. Encouraging, but mostly the obvious "war → long oil" megaphone call the thesis says is already grazed — the middle-band filter kept 1 of 15. The real test is the quiet windows, still to come.

## Setup

```bash
git clone <this repo>
cd geo-herd-rider
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# The LLM curator calls the Anthropic API — bring your own key.
cp .env.example .env        # then edit .env, or just export the var:
export ANTHROPIC_API_KEY=sk-ant-...
```

`.env` is gitignored, so your key is never committed.

## Run it

The pipeline is three scoreboard-gated stages, raw events → curated portfolio:

```bash
# 1. Curate: the LLM maps each trigger to a causal ladder (tickers, direction,
#    chain_depth, audience_breadth), look-ahead-safe. Writes data/events_mapped.csv.
#    Costs Anthropic tokens; uses claude-opus-4-8 + web search by default.
python src/map_event.py                     # or --limit 3 for a cheap smoke test

# 2. Score: mechanical scoreboard — per-event excess vs SPY, net of costs.
python src/score.py                         # writes data/events_scored.csv

# 3. Curate + backtest: middle-band selection → mean-variance optimizer →
#    per-event-horizon backtest vs SPY buy-and-hold, with the Step-1 gate.
python src/curator.py --backtest
```

Step 2 adds a Polymarket probability signal — free, keyless, look-ahead-safe:

```bash
python src/polymarket.py "Fed rate cut 2026"          # live YES odds for a market
python src/polymarket.py "..." --as-of 2025-01-15     # odds at/before a past date
```

It's evaluated forward, not retrospectively: the free history endpoint returns nothing for
already-resolved markets, and coverage skews political/macro — see `src/polymarket.py` and
[`SPEC.md`](SPEC.md) (deferred decision #2).

Polymarket also works as an **event-discovery** feed — it prices *events*, not sectors, so a
market that's both watched and moving is a live upstream event the curator can ladder down to
a vertical and instruments:

```bash
python src/polymarket.py --discover   # hot/moving markets -> candidate triggers (no tokens)
```

The **forward logger** is the look-ahead-clean eval surface — log each decision (curated
ladder + live odds) as a fresh trigger arrives, settle it after the horizon:

```bash
# add fresh triggers to data/forward_events.csv (hand-picked, or from --discover), then:
python src/forward.py --add        # map + fetch live odds + log (needs API key)
python src/forward.py --settle     # score positions whose horizon has elapsed
python src/forward.py --report     # forward scoreboard: excess vs SPY, by cohort, calibration
```

A worked 26-event dataset is already committed (`events.csv` + `data/*.csv`), so you can
run step 3 immediately to reproduce the result without spending any tokens. Re-run step 1
only to regenerate the curation from scratch (note: a retrospective run by a model trained
past these events is hindsight-contaminated — see [`SPEC.md`](SPEC.md)).

## Notes

Developed with [Claude Code](https://claude.com/claude-code). See [`CLAUDE.md`](CLAUDE.md) for the rules Claude follows in this repo, [`SPEC.md`](SPEC.md) for the pre-registered design and the baby-step plan, [`TODO.md`](TODO.md) for backlog ideas not yet scoped, and [`prior-work/`](prior-work/) for the earlier experiments this design builds on.

## Disclaimer

Technical demo. Not financial advice. Historical performance is not predictive. Do not trade real money on this output.

## License

MIT.
