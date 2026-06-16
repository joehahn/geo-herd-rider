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

Deferred (SPEC): the rolling, periodically-rebalanced backtest (needs the cadence
decision), short handling (the optimizer is long-only), and the live LLM curator-agent
reasoning over fresh multi-source signals.
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


def middle_band_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean mask selecting the middle band of the implication tree."""
    return (df["chain_depth"] >= MIN_CHAIN_DEPTH) & (~df["audience_breadth"].isin(EXCLUDE_AUDIENCE))


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
    args = ap.parse_args(argv)

    mapped = pd.read_csv(args.mapped)
    selected = curate(mapped)
    wl = long_watchlist(selected)

    print(f"Curator kept {len(selected)}/{len(mapped)} triggers as the middle band "
          f"(chain_depth >= {MIN_CHAIN_DEPTH}, audience not in {sorted(EXCLUDE_AUDIENCE)}).")
    print(f"Long watchlist ({len(wl)}): {', '.join(wl)}\n")

    if args.scored.exists():
        selection_report(pd.read_csv(args.scored))

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
