"""curator.py — Step 1: the watchlist curator (single trigger source).

Evolves map_event.py from a per-event paper-trade mapper into a watchlist curator.
The curator's job is SELECTION: of all the implication ladders the triggers throw off,
keep the **middle band** — the signals deep enough that the herd hasn't priced them
(chain_depth >= 2) and not the loud, obvious megaphone calls everyone reads instantly —
then hand the resulting long watchlist to the mean-variance optimizer.

Two views, both runnable now from the committed data (no new API calls):

  1. SELECTION vs the scoreboard — does the middle band beat the full set and the
     loud/shallow complement on excess-vs-SPY? This is the Step-1 question: does the
     curator's selection add lift? Uses data/events_scored.csv (the scoreboard).
  2. PORTFOLIO — the spine end to end: optimize the curated long watchlist as of a date,
     with look-ahead-safe prices (trailing lookback ending as_of). Reuses geo's
     score.fetch_panel (explicit start/end) + portfolio-wave-rider's optimizer.

The LLM never forecasts magnitude here either — chain_depth/audience/direction come from
the mapping layer; this module only filters and weights mechanically.

  3. BACKTEST — the Step-1 gate. Run each curated long event as a trade: at entry,
     mean-variance-optimize that event's basket on a look-ahead-safe trailing lookback,
     hold for the event's horizon, exit. Aggregate the trades into a deployed-capital
     CAGR and compare to SPY buy-and-hold over the same windows. The pre-registered
     verdict (fixed before running, SPEC deferred decisions #1 + #3): per-event-horizon
     cadence, and the curated book PASSES iff its annualized excess over SPY buy-and-hold
     (net of costs) is > 0. The bar is not tuned to the data.

The LLM never forecasts magnitude here either — chain_depth/audience/direction come from
the mapping layer; this module only filters and weights mechanically.

Deferred (SPEC): short handling (the optimizer is long-only), and the live LLM
curator-agent reasoning over fresh multi-source signals. And the honest caveat: this is a
retrospective backtest (hindsight-contaminated) — the clean test is a forward paper trade.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

import score
from optimizer import compute_returns, load_financial_model, optimize_portfolio

REPO_ROOT = Path(__file__).resolve().parent.parent
MAPPED_CSV = REPO_ROOT / "data" / "events_mapped.csv"
SCORED_CSV = REPO_ROOT / "data" / "events_scored.csv"

# The middle band: the thesis's bet. Deep enough the herd hasn't arrived, not a megaphone.
MIN_CHAIN_DEPTH = 2
EXCLUDE_AUDIENCE = {"megaphone"}
MIN_BAND = 3  # below this the audience filter is treated as degenerate (see middle_band_mask)

# Pre-registered Step-1 gate (fixed BEFORE running — SPEC deferred decisions #1 + #3,
# do not tune to the data): per-event-horizon cadence, and the curated book passes iff
# its annualized excess over SPY buy-and-hold, net of costs, is strictly positive.
GATE_ANNUAL_EXCESS = 0.0
BACKTEST_LOOKBACK_DAYS = 547  # ~18mo trailing window for the per-event optimizer fit


def middle_band_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean mask selecting the middle band of the implication tree.

    Depth (hops from the trigger) is the primary, load-bearing dimension. The audience
    exclusion (drop 'megaphone' calls) is a secondary screen that discriminates only on
    MULTI-source feeds; on a single-source megaphone feed — every trigger a Trump post — it
    is constant and collapses the band to ~nothing (Iran-window A/B: 1 of 175 survived). So
    apply it only while it leaves a usable book; otherwise fall back to depth-only. This
    preserves the validated multi-source Step-1 (the GW set keeps the audience screen) while
    rescuing single-source feeds. Validated on one window — the clean test is the forward eval."""
    depth_ok = df["chain_depth"] >= MIN_CHAIN_DEPTH
    band = depth_ok & ~df["audience_breadth"].isin(EXCLUDE_AUDIENCE)
    return band if band.sum() >= MIN_BAND else depth_ok


def curate(mapped: pd.DataFrame) -> pd.DataFrame:
    """Select the middle-band events; the curator's composition decision."""
    return mapped[middle_band_mask(mapped)].copy()


def long_watchlist(selected: pd.DataFrame) -> list[str]:
    """The long-only watchlist the optimizer can weight (it can't short)."""
    tickers: list[str] = []
    for _, r in selected[selected["direction"] == "long"].iterrows():
        tickers += [t.strip().upper() for t in str(r["mapped_tickers"]).split(";") if t.strip()]
    return sorted(set(tickers))


def _fmt(x) -> str:
    return f"{x * 100:+.2f}%" if pd.notna(x) else "n/a"


def selection_report(scored: pd.DataFrame) -> None:
    """Does the middle band beat the full set and the loud/shallow complement?"""
    mask = middle_band_mask(scored)
    cohorts = {
        "full set         ": scored,
        "middle band (kept)": scored[mask],
        "complement (drop) ": scored[~mask],
    }
    print("Curator selection vs the scoreboard (excess-vs-SPY, net of costs):")
    print(f"  {'cohort':<20} {'n':>3}  {'median':>8}  {'mean':>8}  {'hit':>5}")
    for name, c in cohorts.items():
        if len(c):
            print(f"  {name:<20} {len(c):>3}  {_fmt(c['excess_return'].median()):>8}  "
                  f"{_fmt(c['excess_return'].mean()):>8}  {c['hit'].mean() * 100:>4.0f}%")


def _optimized_weights(event_tickers: list[str], panel: pd.DataFrame, entry_date: pd.Timestamp,
                       fm: dict, lookback_days: int) -> dict[str, float] | None:
    """Mean-variance weights for one event's basket, fit on a trailing lookback that
    ENDS at entry (look-ahead-safe — no price on/after entry informs the weights).
    Drops tickers lacking full history over the window; returns None if none survive."""
    lb_start = entry_date - pd.Timedelta(days=lookback_days)
    fit = panel.loc[(panel.index >= lb_start) & (panel.index < entry_date), event_tickers]
    usable = [t for t in event_tickers if t in fit.columns and fit[t].notna().all()]
    if not usable:
        return None
    if len(usable) == 1:
        return {usable[0]: 1.0}  # optimizer is a no-op on a single asset
    returns = compute_returns(fit[usable].dropna())
    opt = optimize_portfolio(returns, objective="mean_variance",
                             risk_aversion=fm["risk_aversion"], max_weight=fm["concentration_cap"])
    return opt["weights"] if opt.get("success") else None


def _event_trade(event: pd.Series, panel: pd.DataFrame, fm: dict, lookback_days: int) -> dict | None:
    """One curated long event as a trade: optimizer-weighted basket return over its
    per-event horizon, paired with SPY over the identical window, net of the haircut."""
    tickers = [t.strip().upper() for t in str(event["mapped_tickers"]).split(";") if t.strip()]
    spy = panel[score.BENCHMARK].dropna()
    days = spy.index
    ei = score.entry_index(days, event["telegraph_ts"])
    if ei is None:
        return None
    xi = score.exit_index(days, ei, event["horizon_days"])
    if xi is None:
        return None
    entry_d, exit_d = days[ei], days[xi]

    weights = _optimized_weights(tickers, panel, entry_d, fm, lookback_days)
    if not weights:
        return None
    held = list(weights)
    win = panel.loc[entry_d:exit_d, held]
    if win.isna().any().any():
        return None
    rel = win.iloc[-1] / win.iloc[0] - 1.0
    strat_raw = float(sum(weights[t] * rel[t] for t in held))

    thin = any(t in score.THIN_TICKERS for t in held)
    haircut = score.HAIRCUT_THIN if thin else score.HAIRCUT_DEFAULT
    strat = strat_raw - haircut
    spy_ret = float(spy.loc[exit_d] / spy.loc[entry_d] - 1.0)
    return {
        "event_id": event["event_id"],
        "entry_date": str(entry_d.date()), "exit_date": str(exit_d.date()),
        "held_days": int((exit_d - entry_d).days),
        "weights": ";".join(f"{t}:{weights[t]:.2f}" for t in held),
        "strategy_return": round(strat, 4), "spy_return": round(spy_ret, 4),
        "excess_return": round(strat - spy_ret, 4),
        "_weights": weights, "_entry": entry_d, "_exit": exit_d, "_haircut": haircut,
    }


def _daily_curve(trades: list[dict], panel: pd.DataFrame) -> tuple[float, float, int]:
    """Overlap-aware annualization. Build a daily portfolio that holds, each session,
    equal capital across whatever event trades are active (cash when none), then compare
    its CAGR over the campaign span to SPY held continuously over the same span. Returns
    (ann_strat, ann_spy, n_trading_days). This is the honest way to aggregate per-event-
    horizon trades that overlap in calendar time — sequential compounding double-counts.
    """
    span = panel.loc[min(t["_entry"] for t in trades): max(t["_exit"] for t in trades)]
    daily_ret = panel.pct_change()
    legs = pd.DataFrame(index=span.index)  # one column per trade, NaN when not held
    for i, t in enumerate(trades):
        held = list(t["_weights"])
        w = pd.Series(t["_weights"])
        leg = (daily_ret.loc[t["_entry"]: t["_exit"], held] * w).sum(axis=1)
        leg.iloc[0] = -t["_haircut"]  # charge the round-trip cost on entry day
        legs[i] = leg.reindex(span.index)
    strat_daily = legs.mean(axis=1).fillna(0.0)  # equal capital across active legs; cash if idle
    n = len(span)
    ann_strat = float((1.0 + strat_daily).prod() ** (252.0 / n) - 1.0) if n else float("nan")
    spy_daily = daily_ret[score.BENCHMARK].reindex(span.index).fillna(0.0)
    ann_spy = float((1.0 + spy_daily).prod() ** (252.0 / n) - 1.0) if n else float("nan")
    return ann_strat, ann_spy, n


def backtest_report(mapped: pd.DataFrame, fm: dict, lookback_days: int = BACKTEST_LOOKBACK_DAYS) -> None:
    """Per-event-horizon portfolio backtest of each cohort vs SPY buy-and-hold, with the
    pre-registered Step-1 verdict for the curated middle band."""
    longs = mapped[mapped["direction"].str.lower() == "long"].copy()
    mask = middle_band_mask(longs)
    cohorts = {"full set": longs, "middle band (kept)": longs[mask], "complement (drop)": longs[~mask]}

    # One panel spanning every cohort's lookback start .. latest exit (look-ahead-safe bounds).
    tickers = {score.BENCHMARK}
    for cell in longs["mapped_tickers"]:
        tickers.update(t.strip().upper() for t in str(cell).split(";") if t.strip())
    tele = pd.to_datetime(longs["telegraph_ts"], utc=True).dt.tz_localize(None)
    start = (tele.min() - pd.Timedelta(days=lookback_days + 7)).strftime("%Y-%m-%d")
    end = (tele.max() + pd.Timedelta(days=int(longs["horizon_days"].max()) + 14)).strftime("%Y-%m-%d")
    print(f"\nBacktest: fetching {len(tickers)} tickers, {start} .. {end} ...")
    panel = score.fetch_panel(sorted(tickers), start, end, use_cache=False)

    print("\nPer-event-horizon portfolio backtest (daily equal-capital book vs SPY "
          "buy-and-hold, mean-variance weights, net of costs):")
    print(f"  {'cohort':<20} {'trades':>6}  {'ann.strat':>9}  {'ann.SPY':>9}  {'ann.excess':>10}")
    verdict_excess = None
    kept_trades: list[dict] = []
    for name, c in cohorts.items():
        trades = [t for t in (_event_trade(ev, panel, fm, lookback_days) for _, ev in c.iterrows()) if t]
        if not trades:
            print(f"  {name:<20} {0:>6}  {'n/a':>9}  {'n/a':>9}  {'n/a':>10}")
            continue
        ann_s, ann_b, _ = _daily_curve(trades, panel)
        excess = ann_s - ann_b
        if name.startswith("middle band"):
            verdict_excess = excess
            kept_trades = trades
        print(f"  {name:<20} {len(trades):>6}  {_fmt(ann_s):>9}  {_fmt(ann_b):>9}  {_fmt(excess):>10}")

    if kept_trades:  # audit trail for the book we actually bet (the middle band)
        cols = ["event_id", "entry_date", "exit_date", "held_days", "weights",
                "strategy_return", "spy_return", "excess_return"]
        out = REPO_ROOT / "data" / "backtest_trades.csv"
        pd.DataFrame(kept_trades)[cols].to_csv(out, index=False)
        print(f"\n  wrote {len(kept_trades)} middle-band trades -> {out}")

    print("\n" + "-" * 60)
    print("Pre-registered Step-1 gate (per-event horizon; fixed before running):")
    print(f"  curated annualized excess vs SPY buy-and-hold > {GATE_ANNUAL_EXCESS:+.0%}")
    if verdict_excess is None:
        print("  VERDICT: n/a — no curated trades scored")
    else:
        passed = verdict_excess > GATE_ANNUAL_EXCESS
        print(f"  middle band: {_fmt(verdict_excess)}  ->  {'PASS' if passed else 'FAIL'}")
        print(f"  VERDICT: {'GO -> Step 2 (add Polymarket)' if passed else 'NO-GO -> revisit the curator'}")
    print("-" * 60)


def build_portfolio(watchlist: list[str], as_of: str, lookback_days: int = 547) -> dict:
    """Optimize the curated long watchlist as of `as_of` (look-ahead-safe prices)."""
    fm = load_financial_model(str(REPO_ROOT / "investor_profile.md"))  # falls back to defaults
    start = (pd.Timestamp(as_of) - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end = (pd.Timestamp(as_of) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    panel = score.fetch_panel(watchlist, start, end, use_cache=False)
    # keep tickers with full history over the window (drops delisted/late-listed names)
    usable = [t for t in watchlist if t in panel.columns and panel[t].notna().all()]
    dropped = [t for t in watchlist if t not in usable]
    panel = panel[usable].dropna()
    returns = compute_returns(panel)
    opt = optimize_portfolio(
        returns, objective="mean_variance",
        risk_aversion=fm["risk_aversion"], max_weight=fm["concentration_cap"],
    )
    return {"as_of": as_of, "watchlist": usable, "dropped": dropped, "optimization": opt}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Step 1 curator: middle-band selection + optimize.")
    ap.add_argument("--mapped", type=Path, default=MAPPED_CSV)
    ap.add_argument("--scored", type=Path, default=SCORED_CSV)
    ap.add_argument("--as-of", default=None, help="portfolio as-of date (default: last telegraph)")
    ap.add_argument("--no-portfolio", action="store_true", help="selection view only")
    ap.add_argument("--backtest", action="store_true",
                    help="run the per-event-horizon portfolio backtest + Step-1 gate")
    args = ap.parse_args(argv)

    mapped = pd.read_csv(args.mapped)
    selected = curate(mapped)
    wl = long_watchlist(selected)

    print(f"Curator kept {len(selected)}/{len(mapped)} triggers as the middle band "
          f"(chain_depth >= {MIN_CHAIN_DEPTH}, audience not in {sorted(EXCLUDE_AUDIENCE)}).")
    print(f"Long watchlist ({len(wl)}): {', '.join(wl)}\n")

    if args.scored.exists():
        selection_report(pd.read_csv(args.scored))

    if args.backtest:
        backtest_report(mapped, load_financial_model(str(REPO_ROOT / "investor_profile.md")))

    if not args.no_portfolio and wl:
        as_of = args.as_of or str(pd.to_datetime(mapped["telegraph_ts"], utc=True).max().date())
        print(f"\nCurated portfolio, optimized as of {as_of} "
              f"(mean-variance, look-ahead-safe trailing lookback):")
        res = build_portfolio(wl, as_of)
        opt = res["optimization"]
        if res["dropped"]:
            print(f"  dropped (no usable price history): {', '.join(res['dropped'])}")
        if opt.get("success"):
            for t, w in sorted(opt["weights"].items(), key=lambda kv: -kv[1]):
                if w > 0.005:
                    print(f"    {t:<6} {w * 100:5.1f}%")
            print(f"  expected annual return {_fmt(opt['expected_annual_return'])}, "
                  f"vol {_fmt(opt['annual_volatility'])}, Sharpe "
                  f"{opt['sharpe_ratio']:.2f}" if opt.get("sharpe_ratio") else "")
        else:
            print(f"  optimizer failed: {opt.get('message')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
