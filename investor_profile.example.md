---
# Investor profile — copy to investor_profile.md and edit. The curator/optimizer reads the
# `financial_model` block plus the top-level `concentration_cap`. Missing keys fall back to
# code defaults (risk_aversion 1.0, concentration_cap 0.25, max_watchlist_size 12).
concentration_cap: 0.70           # per-position max weight. HIGH (0.7) lets the mean-variance
                                  # optimizer pile into the winner within each event's basket;
                                  # LOW (0.25) forces diversification. pwr's winning backtest used 0.70.
financial_model:
  risk_aversion: 0.5              # λ in mean-variance utility (μᵀw − λ·wᵀΣw). Lower = chase return,
                                  # concentrate; higher = penalize variance, spread out.
  risk_free_rate: 0.04            # ≈ 1y Treasury; baseline in the Sharpe calc.
  lookback_period: 1.5y           # history window for estimating μ and Σ.
  max_watchlist_size: 8           # cap on names considered at once. NOTE: not yet enforced in
                                  # geo-herd-rider (it optimizes per-event baskets, not one
                                  # rolling watchlist) — see README. concentration_cap is the
                                  # active lever here.
---

# Notes

geo-herd-rider sizes mechanically: the LLM never touches the numbers. These settings only feed
the mean-variance optimizer that weights each curated event's basket. To concentrate the book
into fewer, higher-conviction names, raise `concentration_cap` (toward 0.70) and/or lower
`risk_aversion` (toward 0.5).
