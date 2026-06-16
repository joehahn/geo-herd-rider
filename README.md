# herd-wave-rider

**Author:** Joe Hahn  
**Email:** jmh.datasciences@gmail.com  
**Date:** 2026-Jun-16 <br>
**branch:** main

This Claude Code project is the third in a family. [`portfolio-wave-rider`](https://github.com/joehahn/portfolio-wave-rider) showed that pairing an LLM watchlist curator with a plain mean-variance optimizer beats the optimizer alone, as long as the LLM only decides *which* tickers to hold and never how much. [`geo-wave-rider`](https://github.com/joehahn/geo-wave-rider) then tested a faster, event-driven idea — can you ride a politician's market-moving post? — and returned a clean *no* for the naive version, while pointing at the one place an edge might survive. herd-wave-rider takes that spine and that lesson and builds the smarter thing they imply: an AI that reads a widening stream of signals, reasons out the **laddered chain of implications** behind a geopolitical or macro event, and curates a portfolio aimed at **where the smarter part of the investing herd is heading — arriving a little sooner than the rest of it.**

**The model, in one line.** The mass of investors is a slow herd: it streams toward greener fields, but it is slowed by poor signals, conflicting directions, and friction. The edge is never in predicting how big a move will be — it is in being early to where the herd is already going. So this project does not forecast magnitude (the LLM is forbidden from it, a rule earned the hard way in the sibling project); it traces causal chains and gets to the end of them before the crowd.

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

The naive version was already falsified next door. geo-wave-rider scored 26 telegraphs and the blanket "ride the post" strategy missed its pre-registered bar on both counts (median −0.73% vs SPY, 42% hit rate) — a clean no-go. But the same run showed the only structure worth chasing was exactly where this model predicts: the deep, multi-hop chains off quieter sources beat the loud, obvious calls, which were the losers. So herd-wave-rider is the disciplined response — be selective toward deep chains, fuse several sources so no single weak signal carries the book, and keep geo-wave-rider's scoring harness as the **scoreboard** that decides whether each new source or curator change actually adds lift before it's allowed to stay. The full prior result is preserved in [`prior-work/geo-wave-rider-phase1.md`](prior-work/geo-wave-rider-phase1.md).

## Status

**Scaffolding — Step 0 (foundation) done; Step 1 (one source, end to end) is next.** The mean-variance optimizer is reused verbatim from `portfolio-wave-rider` (`src/optimizer.py`); the multi-source signal mapper and the scoring harness are recycled from `geo-wave-rider` (`src/map_event.py`, `src/score.py`) and will be evolved into the causal-chain curator and the scoreboard backtest. No portfolio has been curated or backtested yet. The design is pre-registered in [`SPEC.md`](SPEC.md), and the build proceeds in scoreboard-gated baby steps laid out there.

## Setup

```bash
git clone <this repo>
cd herd-wave-rider
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
