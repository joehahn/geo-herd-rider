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
                       fm: dict, lookback_days: int, floor_ticker: str | None = None) -> dict[str, float] | None:
    """Mean-variance weights for one basket, fit on a trailing lookback that ENDS at entry
    (look-ahead-safe — no price on/after entry informs the weights). Drops tickers lacking full
    history over the window; returns None only if none survive.

    Falls back to equal weight when the optimizer is infeasible — notably when the
    concentration_cap is too low for the basket size (k tickers can't sum to 1 if cap*k < 1, e.g.
    cap 0.25 with a 2-3 name basket). Without this, a low cap silently DROPS small baskets,
    biasing membership by basket size."""
    lb_start = entry_date - pd.Timedelta(days=lookback_days)
    fit = panel.loc[(panel.index >= lb_start) & (panel.index < entry_date), event_tickers]
    usable = [t for t in event_tickers if t in fit.columns and fit[t].notna().all()]
    if not usable:
        return None
    equal = {t: 1.0 / len(usable) for t in usable}
    if len(usable) == 1:
        return equal  # optimizer is a no-op on a single asset
    returns = compute_returns(fit[usable].dropna())
    opt = optimize_portfolio(returns, objective="mean_variance",
                             risk_aversion=fm["risk_aversion"], max_weight=fm["concentration_cap"])
    w = _apply_min_trade(opt["weights"] if opt.get("success") else equal, fm)
    return _apply_thesis_floor(w, fm, floor_ticker)


def _apply_thesis_floor(weights: dict[str, float], fm: dict, floor_ticker: str | None) -> dict[str, float]:
    """Guarantee a minimum weight to the highest-conviction live name (the 'gem' — passed as
    floor_ticker, the earliest-discovered still-live event), then scale the rest to fill the
    remainder. Mechanically encodes the strategy's premise ("concentrate on the named gem while
    its thesis is live") so backward-looking mean-variance can't rotate the book off the gem right
    before its catalyst pays. 0 disables. Deterministic — the LLM never sets a weight."""
    f = float(fm.get("thesis_floor", 0.0))
    if f <= 0 or not floor_ticker or floor_ticker not in weights or len(weights) < 2:
        return weights
    f = min(f, float(fm.get("concentration_cap", 1.0)))  # floor can't exceed the per-position cap
    if weights[floor_ticker] >= f:
        return weights                                   # mean-variance already meets the floor
    others = {t: w for t, w in weights.items() if t != floor_ticker}
    s = sum(others.values())
    scaled = {t: w / s * (1.0 - f) for t, w in others.items()} if s > 0 else {}
    return {floor_ticker: f, **scaled}


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
