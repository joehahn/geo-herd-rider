"""curator.py — look-ahead-safe mean-variance sizing for the firehose.

Only the sizing helper survives from the retired decision-tree curator: `_optimized_weights`
(called by firehose.py and scripts/build_dashboard.py) and its `_apply_min_trade` companion. The
middle-band selection, the per-event-horizon backtest, and the CLI were deleted with the rest of
the decision-tree path (the mapper/scorer they relied on are gone). The LLM never touches these
numbers — sizing is purely mechanical.
"""
from __future__ import annotations

import pandas as pd

from optimizer import compute_returns, optimize_portfolio

BACKTEST_LOOKBACK_DAYS = 547  # default trailing window (calendar days) for the optimizer's mu/Sigma fit


def _optimized_weights(event_tickers: list[str], panel: pd.DataFrame, entry_date: pd.Timestamp,
                       fm: dict, lookback_days: int) -> dict[str, float] | None:
    """Mean-variance weights for one basket, fit on a trailing lookback that ENDS at entry
    (look-ahead-safe — no price on/after entry informs the weights). Drops tickers lacking full
    history over the window; returns None only if none survive.

    Falls back to equal weight when the optimizer is infeasible — notably when the
    concentration_cap is too low for the basket size (k tickers can't sum to 1 if cap*k < 1, e.g.
    cap 0.25 with a 2-3 name basket). Without this, a low cap silently DROPS small baskets,
    biasing membership by basket size."""
    lb_start = entry_date - pd.Timedelta(days=lookback_days)
    fit = panel.loc[(panel.index >= lb_start) & (panel.index < entry_date), event_tickers]
    # DROP NON-TRADING ROWS BEFORE THE all() CHECK. A panel row where almost nothing is priced is a
    # US market holiday, not data -- it exists only because some ticker in the universe trades on a
    # foreign exchange. On the 2026-08-22 canonical book that was ONE London listing, SATS.L, which
    # put 21 holiday rows (July 4th, Labor Day, Thanksgiving, MLK, Presidents' Day) into the panel
    # with every one of the other 437 tickers NaN.
    #
    # That was catastrophic against `notna().all()` below: a single NaN anywhere in the window
    # disqualifies a ticker, so a window containing one holiday row disqualified EVERY US ticker at
    # once -- the anchors included -- leaving `usable` empty, returning None, and parking the entire
    # book in cash for that whole rebalance period. 46% of possible windows contain such a row; 11
    # rebalances landed on one, which is 267 of 753 days (35% of the backtest) holding nothing.
    # It never crashed and never warned: the equity curve simply went flat for a month, flattering
    # drawdown and Sharpe while depressing return.
    if len(fit.columns):
        _live_rows = fit.notna().mean(axis=1) > 0.5      # majority priced == a real trading day
        if _live_rows.any():
            fit = fit.loc[_live_rows]
    usable = [t for t in event_tickers if t in fit.columns and fit[t].notna().all()]
    if not usable:
        return None
    equal = {t: 1.0 / len(usable) for t in usable}
    if len(usable) == 1:
        return equal  # optimizer is a no-op on a single asset
    returns = compute_returns(fit[usable].dropna())
    opt = optimize_portfolio(returns, objective="mean_variance",
                             risk_aversion=fm["risk_aversion"], max_weight=fm["concentration_cap"])
    return _apply_min_trade(opt["weights"] if opt.get("success") else equal, fm)


def _apply_min_trade(weights: dict[str, float], fm: dict) -> dict[str, float]:
    """Minimum POSITION-WEIGHT floor (NOT a turnover/trade-delta threshold): drop any name whose
    TARGET weight (its fraction of the basket from the optimizer) is below min_trade_size, then
    renormalize the survivors — forcing capital to PILE INTO the few larger names instead of
    dribbling across many. ~1/N caps funded names near N (0.20 -> ~<=5, 0.34 -> ~<=3). 0 disables."""
    mts = float(fm.get("min_trade_size", 0.0))
    if mts <= 0:
        return weights
    kept = {t: w for t, w in weights.items() if w >= mts}
    if not kept:  # everything below the floor -> keep just the single largest position
        top = max(weights, key=weights.get)
        kept = {top: weights[top]}
    s = sum(kept.values())
    return {t: w / s for t, w in kept.items()}
