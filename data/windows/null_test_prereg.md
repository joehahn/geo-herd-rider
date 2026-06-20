# Quiet-window null test — PRE-REGISTRATION

Fixed **before** fetching/scouting/laddering the quiet window, per SPEC discipline #5 (don't
tune to the data). Committed prior to running.

## Question
Does the de-biased pipeline (Trump feed → LLM scout → ladder → middle-band backtest) find a
**clear edge when the world is loud** (the 2026 Iran war) and a **null when it's quiet**? A
signal-finder that "wins" in calm periods too is manufacturing signal from noise — a red flag.

## Windows (identical pipeline & config on both)
- **LOUD (already run):** 2026-01-26 → 2026-06-15 (the Iran war).
- **QUIET (this test):** **2023-07-01 → 2023-10-31** — Trump out of office, no dominant
  geopolitical market-moving event; his posts are mostly legal/campaign. Chosen as a calm
  control where a working system *should* find little.

## Config (held fixed across both windows)
- Triggers: `trump_feed.py` over the window → `select_triggers.py` scout on **Opus** (the
  selection model that worked).
- Ladder: **DeepSeek V3.2, no web search** (the cheap, same-regime config; loud-window result
  was full-set +46%, middle-band +13% excess vs SPY).
- Backtest: `curator.py --backtest`, depth-only middle band, per-event horizon vs SPY.

## Pre-registered expectation
- **Pass (signal is real):** quiet-window middle-band annualized excess is ~null — not a clear
  positive edge. Concretely: quiet excess **< +10%** and **materially smaller than the loud
  window's** (loud was +13% mid-band on this config).
- **Red flag (manufacturing signal):** quiet-window middle-band excess is **large and positive**
  (say > +20%, i.e. comparable to the loud window). That would mean the pipeline produces a
  "win" from noise and the loud-window result is suspect.
- Also informative: a good scout should keep **few** triggers in a quiet window (little concrete
  market-moving news to find).

## Caveat (stated up front)
Single windows, tiny N, high variance, retrospective (hindsight-tinged). This is a **structural
sanity check, not proof** — the clean verdict remains the forward eval. We read direction, not
a precise number.
