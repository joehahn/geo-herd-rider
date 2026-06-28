---
# Active optimizer settings (committed, reproducible). The curator/backtest/dashboard read
# this file via optimizer.load_financial_model(). Only the knobs below are LIVE — i.e. actually
# applied by the code today. To tune the solution during optimization, edit these. All knobs are
# flat top-level keys (one per line) so a diff renders each change vertically.
model: deepseek                   # Curator LLM that reads the firehose. Choices (measured $/3-gem scan):
                                  #   deepseek = deepseek-chat V3 (OpenRouter) ~$0.1  -- DEFAULT: caught all 3 gems, cheapest
                                  #   llama4   = llama-4-maverick (OpenRouter) ~$0.3
                                  #   mimo     = xiaomi/mimo-v2.5-pro (OpenRouter) ~$0.4
                                  #   sonnet   = claude-sonnet-4-6 (Anthropic)  ~$3.6
                                  #   grok4    = x-ai/grok-4.3 (OpenRouter)      ~$3.7
                                  #   opus     = claude-opus-4-8 (Anthropic)     ~$4.4
                                  #   Scans read this knob; dashboards display the model that produced each book.
initial_investment_usd: 50000     # Day-0 dollars to allocate. The optimizer
                                  #   works in FRACTIONS, so this is scale-only: it sets the dollar
                                  #   labels (dashboard, reports), never the picks/weights/returns %.
concentration_cap: 0.7            # Per-position max weight in the week's basket.
                                  #   1.0 -> let mean-variance tilt freely (min_trade_size still
                                  #   prunes sub-floor dribbles); low -> forced equal-ish split.
risk_aversion: 1.0                # lambda in mean-variance utility (μᵀw − λ·wᵀΣw).
max_tickers_per_event: 16         # Cap on tickers kept per event (the "limit the options"
                                  #   knob). Truncates each basket to the first N. Tune later:
                                  #   2 (1-2 names), 7 (3-7), 16 (8-16). Current baskets are ~3-5.
t_update_days: 1                  # Business days from event detection (post-close ~4:30pm
                                  #   cron) to execution, entering at that day's close. 1=next
                                  #   session, 2/3=wait. (0.5/next-morning-open needs intraday data.)
min_trade_size: 0.1               # Drop holdings smaller than this; reallocate
lookback_period_days: 14          # Trailing window (calendar days, ending at entry) for the
                                  #   optimizer's mu/Sigma fit. Short (45) = recent-only, noisier.
rebalance_days: 7                 # The single cadence knob: the firehose scans/rebalances every
                                  #   N days AND reads that same trailing news window. 7 = weekly. One
                                  #   parameter controls both (read "the news since the last scan").
risk_free_rate: 0.04              # reporting only (Sharpe); not in the weight optimization.
---

# Notes

geo-herd-rider sizes mechanically — the LLM never touches the numbers. These settings feed only
the mean-variance optimizer that weights each curated event's basket.

`rebalance_days` is the **one cadence knob** — it sets both how often the firehose re-scans/
re-optimizes and the trailing news window each scan reads (they're the same thing: the news that
arrived since the last scan).

`concentration_cap` and `min_trade_size` are the **two we'll sweep later** to optimize the
size/concentration tradeoff; they're sizing-only (applied at backtest time), so changing them
re-scores the book without re-running the curator.

**Not yet wired** (loaded but ignored, kept out of the block above): `max_watchlist_size` (no
single rolling watchlist here). See README / `optimizer._FINANCIAL_MODEL_DEFAULTS`.
