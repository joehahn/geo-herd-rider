"""curator.py — look-ahead-safe mean-variance sizing for the firehose.

Only the sizing helper survives from the retired decision-tree curator: `_optimized_weights`
(called by firehose.py and scripts/build_dashboard.py) and its cap/floor sizing helpers. The
middle-band selection, the per-event-horizon backtest, and the CLI were deleted with the rest of
the decision-tree path (the mapper/scorer they relied on are gone). The LLM never touches these
numbers — sizing is purely mechanical.
"""
from __future__ import annotations

import math

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
    return _cap_floor_weights(usable, fit[usable], fm)


def _min_names(cap: float) -> int:
    """Smallest basket that can absorb a full book under `cap` (cap 0.4 -> 3, since 2x0.4 < 1)."""
    return max(1, math.ceil(1.0 / cap - 1e-9)) if 0.0 < cap < 1.0 else 1


def _feasible_k(cap: float, mts: float, n: int) -> list[int]:
    """Basket sizes that can satisfy BOTH knobs: k*mts <= 1 <= k*cap, i.e. ceil(1/cap)..floor(1/mts),
    clipped to the candidates on hand. Empty means the two knobs genuinely conflict."""
    lo = _min_names(cap)
    hi = math.floor(1.0 / mts + 1e-9) if mts > 0 else n
    lo, hi = max(1, lo), min(n, hi)
    return list(range(lo, hi + 1))


def _box_solve(sub: list[str], fit: pd.DataFrame, fm: dict, cap: float,
               mts: float) -> tuple[dict[str, float], float] | None:
    """Mean-variance over `sub` with both knobs as BOX BOUNDS -- min_weight=mts, max_weight=cap.
    Feasible by construction when len(sub) is in `_feasible_k`. Returns (weights, objective) so
    baskets of different size compare on the same mu^T w - lambda w^T Sigma w."""
    lam = float(fm["risk_aversion"])
    returns = compute_returns(fit[sub].dropna())
    mu, cov = returns["mean"], returns["cov"]
    if len(sub) == 1:
        t = sub[0]
        return {t: 1.0}, float(mu.get(t, 0.0) - lam * cov.loc[t, t])
    opt = optimize_portfolio(returns, objective="mean_variance", risk_aversion=lam,
                             max_weight=cap, min_weight=mts)
    if not opt.get("success"):
        return None
    w = dict(opt["weights"])
    v = pd.Series(w).reindex(mu.index).fillna(0.0)
    return w, float(v @ mu - lam * (v @ cov @ v))


def _cap_floor_weights(usable: list[str], fit: pd.DataFrame, fm: dict) -> dict[str, float]:
    """Size a basket under concentration_cap and min_trade_size, treating BOTH as box bounds on the
    QP. A cap IS a box upper bound, so one solve enforces it exactly; there is nothing to iterate.

    History, because this file got it wrong twice and the second way looked convincing.

    (1) Until 2026-08-31 `_apply_min_trade` ran AFTER the capped solve: it deleted every sub-floor
    name and renormalized the survivors to sum to 1, which silently undid the cap the solver had just
    honoured. A lone survivor sitting at the cap became 100%. That put 690 of 774 days over a 0.4 cap
    and 147 days in a single name -- the cap was not a limit on concentration, it was a GUARANTEE of
    it. The canonical book fell from $790K to $158K once the cap was actually enforced, so that
    headline was earned by breaking the risk control, not by the curation.

    (2) The waterfall that briefly replaced it -- freeze cap-binding names, re-optimize the residual
    over what is left -- enforced the cap but is WRONG for mean-variance: re-optimizing the residual
    without the frozen names in it discards their covariance with the free names, sizing the leftover
    as if the capped positions were not in the portfolio. It also fills greedily, which minimizes what
    is left for the last name and so manufactures floor violations it cannot repair (cap 0.4 / floor
    0.3 returned 40/40/20 in 33 of 37 rebalances though 40/30/30 satisfies both). It only looked right
    at 0.4/0.2 because 1 - 2(0.4) lands exactly on 0.2 -- arithmetic luck, not a property.

    With the floor at 0 this reduces to the ORIGINAL single capped solve, verified equal on the
    canonical journal to within 0.005% across caps 0.2-1.0 with zero cap violations. The floor branch
    is kept only so that turning the floor back on cannot resurrect (1)."""
    cap = float(fm.get("concentration_cap", 1.0) or 1.0)
    mts = float(fm.get("min_trade_size", 0.0))
    equal = {t: 1.0 / len(usable) for t in usable}

    if mts <= 0:                    # the common path: one exact solve, cap as a box bound
        got = _box_solve(usable, fit, fm, cap, 0.0)
        return got[0] if got else equal

    ks = _feasible_k(cap, mts, len(usable))
    if not ks:                      # knobs conflict -> the CAP wins, the floor yields (risk control)
        got = _box_solve(usable, fit, fm, cap, 0.0)
        return got[0] if got else equal

    # Nested descent S_n > ... > S_kmin, dropping whoever the CAPPED solve starves most. Which k names
    # is the one part that stays heuristic; re-solving at each step keeps covariance in the ranking.
    want, kmin = set(ks), min(ks)
    subsets, pool = {}, list(usable)
    while True:
        if len(pool) in want:
            subsets[len(pool)] = list(pool)
        if len(pool) <= kmin:
            break
        got = _box_solve(pool, fit, fm, cap, 0.0)
        if not got:
            break
        pool.remove(min(pool, key=lambda t: got[0].get(t, 0.0)))

    best = None
    for k in sorted(subsets):
        got = _box_solve(subsets[k], fit, fm, cap, mts)
        if got and (best is None or got[1] > best[1]):
            best = (got[0], got[1], k)
    if best is None:
        got = _box_solve(usable, fit, fm, cap, 0.0)
        return got[0] if got else equal
    _K_CHOSEN.append(best[2])
    return best[0]


_K_CHOSEN: list[int] = []           # diagnostic: SUBSET size chosen per rebalance (not funded names)
