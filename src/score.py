"""score.py — Layer 3 of the geo-wave-rider Phase 1 pipeline.

The MECHANICAL scorer. No judgment: it fills prices via yfinance, computes each
paper trade's return versus SPY over the mapped horizon, applies a costs/slippage
haircut, and reports the result against the pre-registered decision rule.

    Input : data/events_mapped.csv   (from map_event.py)
    Output: data/events_scored.csv   (per-event excess, hit, path shape)
            + a printed report (medians, hit rate, basket-vs-SPY, chain-depth
              and audience-breadth breakdowns, prediction-market calibration)

Per-event definitions (from SPEC.md)
------------------------------------
    entry  = first tradeable close AFTER telegraph_ts (no look-ahead — if the
             telegraph posted after the 16:00 ET close, entry is the NEXT
             session's close; otherwise the same session's close).
    exit   = first trading day on/after entry_date + horizon_days.
    strategy_return = equal-weight basket return over [entry, exit], sign-flipped
                      for shorts.
    spx_return      = SPY return over the exact same window.
    excess_return   = strategy_return - spx_return     (strips the market tide).
    hit             = excess_return (net of haircut) > 0.

Costs/slippage haircut: ~15 bps round trip by default; thin ETFs (e.g. BWET) are
flagged and charged more, since their real slippage is worse.

Herd-model additions (Joe's reframe — see README)
-------------------------------------------------
Beyond the endpoint excess, we record the SHAPE of the excess-return path across
the holding window — the herd model's signature:
    instant_pop : most of the excess lands on day 1, then flat   -> already priced
    drift       : excess accrues gradually toward the final value -> herd arriving
    reversion   : excess peaks then gives most of it back         -> noise/overshoot
    fizzle      : final excess <= 0
and we break the base rate down by `chain_depth` and `audience_breadth` to test
whether edge concentrates in deep chains off quiet sources.

Look-ahead hygiene reused from portfolio-wave-rider: prices are pulled with
explicit ``start=/end=`` date bounds (never a relative period), so nothing after
a trade's exit date can leak into its return.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parent.parent
MAPPED_CSV = REPO_ROOT / "data" / "events_mapped.csv"
SCORED_CSV = REPO_ROOT / "data" / "events_scored.csv"
PRICE_CACHE = REPO_ROOT / "data" / "prices_cache" / "panel.csv"

BENCHMARK = "SPY"

# t_update_days: business days from the (post-close, ~4:30pm cron) detection of an event to when
# the human actually executes — enter that many trading days after the first actable close, at
# that day's CLOSE. 1 = next session, 2/3 = wait 2/3 business days. (A fractional 0.5 = next-
# morning OPEN fill is NOT modeled here — that needs intraday data; integer days only.)
T_UPDATE_DAYS = 1

# Pre-registered decision rule (fixed BEFORE running — do not tune to the data).
GATE_MEDIAN_EXCESS = 0.03   # median per-event excess return must exceed +3%
GATE_HIT_RATE = 0.55        # hit rate must exceed 55%

# Costs/slippage haircut, round trip, as a fraction.
HAIRCUT_DEFAULT = 0.0015    # ~15 bps for liquid names
HAIRCUT_THIN = 0.0050       # ~50 bps for thin ETFs where real slippage is worse
# Known-thin instruments to flag (extend as needed).
THIN_TICKERS = {"BWET", "BDRY", "BOAT", "SEA"}

ET = "America/New_York"
MARKET_CLOSE_HOUR = 16  # 16:00 ET


# --------------------------------------------------------------------------- #
# Price fetching (look-ahead-safe: explicit start/end, adjusted close)         #
# --------------------------------------------------------------------------- #
def fetch_panel(tickers: list[str], start: str, end: str, use_cache: bool = True) -> pd.DataFrame:
    """Adjusted-close panel for `tickers` over [start, end] (DatetimeIndex, tz-naive).

    Cached to data/prices_cache/panel.csv so a re-score is offline and reproducible.
    """
    tickers = sorted(set(tickers))
    if use_cache and PRICE_CACHE.exists():
        cached = pd.read_csv(PRICE_CACHE, index_col=0, parse_dates=True)
        if set(tickers).issubset(cached.columns) and cached.index.min() <= pd.Timestamp(start) \
                and cached.index.max() >= pd.Timestamp(end):
            return cached[tickers]

    raw = yf.download(tickers, start=start, end=end, interval="1d",
                      auto_adjust=True, progress=False)
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"yfinance returned no data for {tickers}")
    # MultiIndex columns for 2+ tickers; flat for 1.
    prices = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    if isinstance(prices, pd.Series):
        prices = prices.to_frame(tickers[0])
    elif list(prices.columns) == ["Close"]:
        prices = prices.rename(columns={"Close": tickers[0]})
    prices.index = pd.to_datetime(prices.index).tz_localize(None)

    PRICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    prices.to_csv(PRICE_CACHE)
    return prices


# --------------------------------------------------------------------------- #
# Trade window resolution                                                       #
# --------------------------------------------------------------------------- #
def entry_index(trading_days: pd.DatetimeIndex, telegraph_ts: str,
                t_update_days: int = None) -> int | None:
    """Position in `trading_days` of the entry close, modeling the update lag.

    First the ACTABLE close: posted before 16:00 ET on a trading day -> that day's close;
    otherwise (after close, or a non-trading day) -> the next trading day's close. Then enter
    `t_update_days` trading days later, at that day's close (default T_UPDATE_DAYS = 1, the gap
    between the post-close cron and the human placing the trade). Returns None if it runs off
    the end of the data.
    """
    lag = T_UPDATE_DAYS if t_update_days is None else int(t_update_days)
    ts = pd.Timestamp(telegraph_ts)
    ts_et = ts.tz_convert(ET) if ts.tzinfo is not None else ts.tz_localize(ET)
    day = ts_et.normalize().tz_localize(None)
    same_day_actable = ts_et.hour < MARKET_CLOSE_HOUR

    base = None
    for i, d in enumerate(trading_days):
        if d < day:
            continue
        base = i if (d == day and same_day_actable) else (i if d > day else i + 1)
        break
    if base is None:
        return None
    idx = base + lag
    return idx if idx < len(trading_days) else None


def exit_index(trading_days: pd.DatetimeIndex, entry_i: int, horizon_days: int) -> int | None:
    """First trading day on/after entry_date + horizon_days (calendar)."""
    target = trading_days[entry_i] + pd.Timedelta(days=int(horizon_days))
    for i in range(entry_i + 1, len(trading_days)):
        if trading_days[i] >= target:
            return i
    return None


# --------------------------------------------------------------------------- #
# Path shape (the herd-model signature)                                         #
# --------------------------------------------------------------------------- #
def classify_path(excess_path: np.ndarray) -> tuple[str, float]:
    """Classify the cumulative-excess path; return (label, front_loading).

    excess_path[k] = (basket cum return to day k) - (SPY cum return to day k),
    direction already applied, indexed from entry (path[0] == 0).
    front_loading = day-1 excess / final excess (how much landed immediately).
    """
    if len(excess_path) < 3:
        return "n/a", float("nan")
    final = excess_path[-1]
    day1 = excess_path[1]
    peak = float(np.max(excess_path))
    front = day1 / final if final != 0 else float("nan")

    if final <= 0:
        return "fizzle", front
    if peak > 0 and final < 0.5 * peak:
        return "reversion", front
    if np.isfinite(front) and front >= 0.7:
        return "instant_pop", front
    return "drift", front


# --------------------------------------------------------------------------- #
# Scoring                                                                       #
# --------------------------------------------------------------------------- #
def score_event(event: pd.Series, panel: pd.DataFrame) -> dict | None:
    tickers = [t.strip().upper() for t in str(event["mapped_tickers"]).split(";") if t.strip()]
    have = [t for t in tickers if t in panel.columns and panel[t].notna().any()]
    if not have:
        return {"event_id": event["event_id"], "status": f"no prices for {tickers}"}

    # Timing runs off SPY's calendar (always available), so a thin or not-yet-listed
    # basket ticker can't break entry/exit resolution.
    spy = panel[BENCHMARK].dropna()
    days = spy.index

    ei = entry_index(days, event["telegraph_ts"])
    if ei is None:
        return {"event_id": event["event_id"], "status": "no entry session"}
    xi = exit_index(days, ei, event["horizon_days"])
    if xi is None:
        return {"event_id": event["event_id"], "status": "window beyond available data"}

    win = days[ei : xi + 1]
    sign = 1.0 if str(event["direction"]).lower() == "long" else -1.0

    # Keep only basket tickers with full price data over this window (e.g. drop a
    # ticker that delisted mid-window or hadn't listed yet at entry).
    bk_all = panel[have].reindex(win)
    usable = [t for t in have if bk_all[t].notna().all()]
    if not usable:
        return {"event_id": event["event_id"], "status": f"no window data for {have}"}
    have = usable
    bk = bk_all[have]
    basket_cum = (bk / bk.iloc[0]).mean(axis=1) - 1.0       # avg of per-ticker cum returns
    spy_cum = spy.loc[win] / spy.loc[win].iloc[0] - 1.0
    excess_path = (sign * basket_cum - spy_cum).to_numpy()

    strategy_return = sign * float(basket_cum.iloc[-1])
    spx_return = float(spy_cum.iloc[-1])
    excess_raw = strategy_return - spx_return

    thin = any(t in THIN_TICKERS for t in have)
    haircut = HAIRCUT_THIN if thin else HAIRCUT_DEFAULT
    excess_net = excess_raw - haircut

    path_shape, front_loading = classify_path(excess_path)
    # Odds come mechanically from Polymarket (src/polymarket.py --enrich), not the LLM.
    odds = event.get("polymarket_odds", "")

    return {
        "event_id": event["event_id"],
        "direction": event["direction"],
        "mapped_tickers": ";".join(have),
        "horizon_days": int(event["horizon_days"]),
        "chain_depth": int(event["chain_depth"]),
        "audience_breadth": event["audience_breadth"],
        "entry_date": str(days[ei].date()),
        "exit_date": str(days[xi].date()),
        "strategy_return": round(strategy_return, 4),
        "spx_return": round(spx_return, 4),
        "excess_return_raw": round(excess_raw, 4),
        "excess_return": round(excess_net, 4),     # net of haircut — the scored value
        "hit": bool(excess_net > 0),
        "haircut": haircut,
        "thin_etf": thin,
        "path_shape": path_shape,
        "front_loading": round(front_loading, 3) if np.isfinite(front_loading) else "",
        "polymarket_odds": odds,
        "status": "ok",
    }


# --------------------------------------------------------------------------- #
# Reporting                                                                     #
# --------------------------------------------------------------------------- #
def _fmt_pct(x: float) -> str:
    return f"{x * 100:+.2f}%" if pd.notna(x) else "n/a"


def report(scored: pd.DataFrame, panel: pd.DataFrame) -> None:
    ok = scored[scored["status"] == "ok"].copy()
    n = len(ok)
    print("\n" + "=" * 68)
    print("geo-wave-rider — Phase 1 base-rate report")
    print("=" * 68)
    skipped = scored[scored["status"] != "ok"]
    print(f"events scored: {n}    skipped: {len(skipped)}")
    for _, r in skipped.iterrows():
        print(f"   - {r['event_id']}: {r['status']}")
    if n == 0:
        print("\nNothing scored — run map_event.py first, or check ticker coverage.")
        return

    med = ok["excess_return"].median()
    mean = ok["excess_return"].mean()
    q1, q3 = ok["excess_return"].quantile([0.25, 0.75])
    hit_rate = ok["hit"].mean()

    print("\nPer-event excess return (net of haircut, vs SPY):")
    print(f"   median : {_fmt_pct(med)}")
    print(f"   mean   : {_fmt_pct(mean)}")
    print(f"   IQR    : {_fmt_pct(q1)}  ..  {_fmt_pct(q3)}")
    print(f"   hit rate : {hit_rate * 100:.1f}%   ({int(ok['hit'].sum())}/{n})")

    # Equal-weight basket-of-all-events vs SPY (mean across events of each leg).
    print("\nEqual-weight basket of all events vs SPY:")
    print(f"   mean strategy_return : {_fmt_pct(ok['strategy_return'].mean())}")
    print(f"   mean spx_return      : {_fmt_pct(ok['spx_return'].mean())}")
    print(f"   mean excess (raw)    : {_fmt_pct(ok['excess_return_raw'].mean())}")

    print("\nPath shape (herd-model signature):")
    for shape, grp in ok.groupby("path_shape"):
        print(f"   {shape:<11} n={len(grp):<3} median excess {_fmt_pct(grp['excess_return'].median())}")

    print("\nBy chain_depth (diffusion-lag proxy — deeper = slower herd):")
    for depth, grp in ok.groupby("chain_depth"):
        print(f"   depth {depth}: n={len(grp):<3} median {_fmt_pct(grp['excess_return'].median())}"
              f"  hit {grp['hit'].mean() * 100:.0f}%  drift {(grp['path_shape'] == 'drift').mean() * 100:.0f}%")

    print("\nBy audience_breadth (louder = more grazed):")
    order = ["megaphone", "broad", "niche", "quiet"]
    for aud in order:
        grp = ok[ok["audience_breadth"] == aud]
        if len(grp):
            print(f"   {aud:<10} n={len(grp):<3} median {_fmt_pct(grp['excess_return'].median())}"
                  f"  hit {grp['hit'].mean() * 100:.0f}%")

    # Polymarket calibration on the subset where mechanical odds were supplied
    # (src/polymarket.py --enrich); empty unless the mapped events were enriched.
    odds_col = pd.to_numeric(ok.get("polymarket_odds", pd.Series(dtype=float)), errors="coerce")
    cal = ok[odds_col.notna()]
    if len(cal):
        cal = cal.copy()
        cal["odds"] = pd.to_numeric(cal["polymarket_odds"])
        agree = cal[cal["odds"] >= 0.5]  # market judged the action likely
        print(f"\nPolymarket calibration (n={len(cal)} with odds):")
        print(f"   odds>=50% (market agrees action happens): n={len(agree)} "
              f"hit {agree['hit'].mean() * 100:.0f}%" if len(agree) else "   (no >=50% odds)")

    # Pre-registered gate.
    pass_med = med > GATE_MEDIAN_EXCESS
    pass_hit = hit_rate > GATE_HIT_RATE
    verdict = "GO -> design Phase 2" if (pass_med and pass_hit) else "NO-GO -> write the null result"
    print("\n" + "-" * 68)
    print("Pre-registered decision rule (fixed before running):")
    print(f"   median excess > +3% : {_fmt_pct(med)}  -> {'PASS' if pass_med else 'FAIL'}")
    print(f"   hit rate    > 55%   : {hit_rate * 100:.1f}%  -> {'PASS' if pass_hit else 'FAIL'}")
    print(f"   VERDICT: {verdict}")
    print("-" * 68)


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mechanical scorer (Phase 1, layer 3).")
    ap.add_argument("--mapped", type=Path, default=MAPPED_CSV)
    ap.add_argument("--out", type=Path, default=SCORED_CSV)
    ap.add_argument("--no-cache", action="store_true", help="ignore the price cache")
    args = ap.parse_args(argv)

    if not args.mapped.exists():
        print(f"ERROR: {args.mapped} not found. Run map_event.py first.")
        return 2

    events = pd.read_csv(args.mapped)

    # Global date span: from the earliest telegraph to the latest possible exit,
    # padded so entry/exit lookups always have a session to land on.
    tele = pd.to_datetime(events["telegraph_ts"], utc=True).dt.tz_localize(None)
    max_horizon = int(events["horizon_days"].max())
    start = (tele.min() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    end = (tele.max() + pd.Timedelta(days=max_horizon + 14)).strftime("%Y-%m-%d")

    tickers = {BENCHMARK}
    for cell in events["mapped_tickers"]:
        tickers.update(t.strip().upper() for t in str(cell).split(";") if t.strip())

    print(f"Fetching {len(tickers)} tickers, {start} .. {end} ...")
    panel = fetch_panel(sorted(tickers), start, end, use_cache=not args.no_cache)

    rows = [score_event(ev, panel) for _, ev in events.iterrows()]
    scored = pd.DataFrame([r for r in rows if r is not None])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(args.out, index=False)
    print(f"Wrote {len(scored)} rows -> {args.out}")

    report(scored, panel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
