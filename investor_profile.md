---
# Active optimizer settings (committed, reproducible). The curator/backtest/dashboard read
# this file via optimizer.load_financial_model(). Only the knobs below are LIVE — i.e. actually
# applied by the code today. To tune the solution during optimization, edit these.
concentration_cap: 0.3334         # LIVE. Per-position max weight inside each event's basket.
                                  #   high -> pile into the optimizer's pick; low -> equal-ish.
financial_model:
  risk_aversion: 1.0              # LIVE. lambda in mean-variance utility (μᵀw − λ·wᵀΣw).
  max_tickers_per_event: 16       # LIVE. Cap on tickers kept per event (the "limit the options"
                                  #   knob). Truncates each basket to the first N. Tune later:
                                  #   2 (1-2 names), 7 (3-7), 16 (8-16). Current baskets are ~3-5.
  t_update_days: 1                # LIVE. Business days from event detection (post-close ~4:30pm
                                  #   cron) to execution, entering at that day's close. 1=next
                                  #   session, 2/3=wait. (0.5/next-morning-open needs intraday data.)
  min_trade_size: 0.20            # LIVE. Drop basket positions below this fraction and renormalize
                                  #   (pile into the few larger names). ~1/N caps funded names near
                                  #   N: 0.20 -> ~<=5, 0.34 -> ~<=3, 0.05 -> ~<=20. 0 disables.
  lookback_period_days: 45        # LIVE. Trailing window (calendar days, ending at entry) for the
                                  #   optimizer's mu/Sigma fit. Short (45) = recent-only, noisier.
  news_lookback_days: 7           # NOT YET WIRED. Trailing window of aggregate news/tweets the news
                                  #   curator reads each run (the planned aggregate-news redesign).
  rebalance_days: 7              # NOT YET WIRED. Re-synthesize the portfolio every N days (weekly).
  risk_free_rate: 0.04            # reporting only (Sharpe); not in the weight optimization.
---

# Notes

geo-herd-rider sizes mechanically — the LLM never touches the numbers. These settings feed only
the mean-variance optimizer that weights each curated event's basket.

**Not yet wired** (loaded but ignored in this architecture, kept out of the block above to avoid
implying they work): `max_watchlist_size` (no single rolling watchlist here), `rebalance_period`
(per-event-horizon, not periodic). See README / `optimizer._FINANCIAL_MODEL_DEFAULTS`.
