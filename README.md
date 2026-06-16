# geo-herd-rider

**Author:** Joe Hahn  
**Email:** jmh.datasciences@gmail.com  
**Date:** 2026-Jun-16 <br>
**branch:** main

**Our model of the market.** Investors move as a slow herd: it drifts toward greener fields but is held back by poor signals, conflicting directions, and friction. The edge is never in predicting *how big* a move will be — it is in being early to *where the herd is already heading*. Every event throws off a tree of downstream implications, and the money is in the **middle band** of that tree: deep enough that the herd hasn't priced it, shallow enough that the causality still holds. Hop-1 calls are already grazed; hop-4+ chains are seductive storytelling that rarely pays.

**What this repo does.** An LLM reads a stream of signals, reasons out the laddered chain of implications behind a geopolitical or macro event, and curates a watchlist; a plain mean-variance optimizer then weights it. The LLM picks composition, direction, and the ladder — but never magnitude (a rule earned the hard way). A scoreboard backtest decides whether each source or curator change actually adds lift before it stays. **Today** it runs end-to-end on a single trigger source (politician/business-leader posts) and clears its pre-registered bar; **next** it adds prediction-market probabilities and smart-money confirmation (Fed comms, congressional trades), one scoreboard-gated step at a time.

## The implication ladder

Every triggering event spawns a tree of downstream implications, and the tree has a shape that tells you where the money is:

- **Hop 1 — direct and obvious.** "Iran war de-escalates → oil falls." The herd is here in minutes; futures reprice on the headline. Already grazed, no edge — this is exactly the leaf that *lost* in geo-wave-rider.
- **Hop 2–3 — the middle band.** Cheaper energy → input-cost relief for airlines and freight and chemicals; Gulf stability → infrastructure capex; disinflation → room for the Fed to cut → rate-sensitive names. The herd takes days to weeks to arrive, and the causality still holds.
- **Hop 4+ — deep and speculative.** "Freed-up dollars → hiring boom → AI boom → robotics boom." The herd is nowhere near it, but now the logic is a story with a dozen ways to be wrong. This is the trap: an AI will generate gorgeous deep ladders all day, and most of them never pay.

**The whole bet is the middle band** — deep enough that the herd hasn't arrived, shallow enough that the causality is still real. The curator's job is not to *find* implications (that's infinite and easy) but to generate the full tree and locate the band that is both unpriced and correct. The carriers→Hormuz→tanker-rates→dry-bulk chain that motivated this family is the canonical example.

## Four signals, four jobs

The sources are not interchangeable inputs; each answers a different question in the ladder:

- **Trigger** — *what starts a ladder.* Posts by high-reach figures: politicians (Trump) and business leaders (Musk, Dimon).
- **Probability** — *will the upstream event actually resolve?* Polymarket and other prediction-market odds, so a ladder is timed and sized against a real probability rather than a maybe.
- **Confirmation** — *is the smart edge of the herd already turning?* Fed communications, big-bank principals, and congressional-trading disclosures. The aim is to ride just behind the smart money and ahead of everyone else, not to beat the smart money.
- **Sizing** — mechanical. A standard mean-variance optimizer weights whatever watchlist results. The LLM never touches the numbers.

The machine, end to end: *trigger → probability → AI causal ladder → smart-money confirmation → mechanical sizing → a scoreboard that keeps the whole thing honest.*

## Why it's built this way

The naive version was already falsified next door. geo-wave-rider scored 26 telegraphs and the blanket "ride the post" strategy missed its pre-registered bar on both counts (median −0.73% vs SPY, 42% hit rate) — a clean no-go. But the same run showed the only structure worth chasing was exactly where this model predicts: the deep, multi-hop chains off quieter sources beat the loud, obvious calls, which were the losers. So geo-herd-rider is the disciplined response — be selective toward deep chains, fuse several sources so no single weak signal carries the book, and keep geo-wave-rider's scoring harness as the **scoreboard** that decides whether each new source or curator change actually adds lift before it's allowed to stay. The full prior result is preserved in [`prior-work/geo-wave-rider-phase1.md`](prior-work/geo-wave-rider-phase1.md).

## Status

**Step 1 (one source, end to end) done and passing; Step 2 (prediction markets) is next.** The mean-variance optimizer is reused verbatim from `portfolio-wave-rider` (`src/optimizer.py`); the signal mapper and scoring harness recycled from `geo-wave-rider` (`src/map_event.py`, `src/score.py`) are now evolved into the middle-band curator and a per-event-horizon scoreboard backtest (`src/curator.py --backtest`). On the seed 26-event set the curated book clears its pre-registered bar — annualized excess over SPY buy-and-hold > 0. The design is pre-registered in [`SPEC.md`](SPEC.md) and the build proceeds in scoreboard-gated baby steps laid out there. Note the backtest is retrospective and hindsight-contaminated, so the clean test is a forward paper trade in a later step.

## Setup

```bash
git clone <this repo>
cd geo-herd-rider
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Notes

Developed with [Claude Code](https://claude.com/claude-code). See [`CLAUDE.md`](CLAUDE.md) for the rules Claude follows in this repo, [`SPEC.md`](SPEC.md) for the pre-registered design and the baby-step plan, and [`prior-work/`](prior-work/) for the geo-wave-rider null that motivates it.

## Disclaimer

Technical demo. Not financial advice. Historical performance is not predictive. Do not trade real money on this output.

## License

MIT.
