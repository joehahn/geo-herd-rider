# CLAUDE.md

Operating rules for Claude Code in the `geo-herd-rider` repo. Read this and
[`SPEC.md`](SPEC.md) before doing anything here.

## What this project is

An LLM-curated, multi-source, event-driven portfolio. An AI reads a stream of signals
(politician/business-leader posts, prediction markets, Fed comms, congressional trades),
reasons out the **laddered chain of implications** behind an event, and curates a watchlist
that a plain mean-variance optimizer then weights. The thesis: get to where the smarter
part of the investing herd is heading, a little sooner than the rest. Full design in
`SPEC.md`; the empirical justification (a falsified naive version) in `prior-work/`.

## Lineage — reuse, don't reinvent

- **Optimizer** is reused verbatim from `portfolio-wave-rider` (`src/optimizer.py`). Don't
  reimplement mean-variance; extend that file if you need more.
- **Signal mapper + scoreboard** are recycled from `geo-wave-rider` (`src/map_event.py`,
  `src/score.py`). `map_event.py` is the *ancestor* of the causal-chain curator (still a
  paper-trade mapper today); `score.py` is the *ancestor* of the scoreboard backtest.
  Evolving these is Step 1 — see SPEC.

## Non-negotiables

1. **The LLM never forecasts magnitude.** It picks composition, direction, and the causal
   ladder — never expected return, weights, or position size. Sizing is mechanical and
   downstream. This is the load-bearing lesson from `portfolio-wave-rider`'s wave-tilt
   postmortem (numeric LLM tilts on μ destroyed value). Do not relax it.
2. **The middle band is the bet.** The curator's value is locating implications that are
   deep enough the herd hasn't priced them but shallow enough the causality holds — not
   generating the deepest, most impressive-sounding ladder. Hop-1 obvious calls are
   already grazed; hop-4+ chains are storytelling.
3. **The scoreboard is the filter, not the curator's confidence.** A ladder, a source, or
   a curator change is kept only if the backtest shows it adds lift over the prior config.
   Numbers come from Python (`src/score.py`, `src/optimizer.py`), never from the LLM.
4. **Look-ahead hygiene everywhere.** Curator reasons only from pre-catalyst info
   (`before:<date>`); scoring fetches prices with explicit `start=/end=` bounds. Preserve
   this — a leak invalidates results silently. And note retrospective backtests are
   hindsight-contaminated, so a forward paper-traded eval is the only clean test.
5. **Don't tune the pre-registered lift bar to the data** once it's fixed (SPEC, deferred
   decision #1). Same discipline that made geo-wave-rider credible.

## Scope discipline (baby steps)

Build one rung at a time, each gated by the scoreboard. **Next increment is Step 1: reframe
the curator + wire the scoreboard for the single trigger source only.** Do NOT add
Polymarket / Fed / congressional feeds or build a forward logger until Step 1 pays.
Confirm scope before jumping ahead.

## Conventions

- Python 3.12, std `venv`, deps in `requirements.txt`. Match the terse,
  docstring-heavy style already in `src/`.
- Deterministic work in Python; judgment (the causal ladder, the curation) in the LLM.
- Default model for LLM work is `claude-opus-4-8`; `claude-sonnet-4-6` is the cheaper
  option for high-volume curator/backtest runs.
- Outputs land in `data/`; `data/prices_cache/` is gitignored. Don't commit the venv.
- Commit/push only when asked. Commit-message trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
