#!/usr/bin/env python3
"""measure_stale_cap.py — how many WATCHLIST SLOTS are held by names the press has gone quiet on?

THE QUESTION. `_ranked_cull` allocates max_watchlist slots on recency (a freshness reserve) and
trailing risk-adjusted return. Nothing the curator judged about the EVENT reaches that decision.
`max_stale_scans` is the one lever that frees a slot on a press signal: it drops a held ticker after
that many consecutive scans without a mention. It is a BOOK knob -- `_stateful_watch` runs at replay
time -- so this whole sweep is FREE and re-curates nothing.

WHY IT MATTERS NOW. Measured on v27 and v28 independently, live events that exit on the merits go
quiet for a median of ONE scan while events retired by a timer go quiet for EIGHT. The event-level
cap (`max_silent_scans`) is a CURATION knob and testing it costs a run; this ticker-level sibling
tests the same thesis -- "silence should free the slot sooner" -- for nothing.

JUDGE ON MECHANISM (CLAUDE.md #6). What reproduces is how many slots silence is occupying and how
many funded positions the cap removes. The P&L column is printed last, deliberately without a
verdict: these are paired replays of ONE curation, not independent samples.

    python scripts/measure_stale_cap.py [run ...]
"""
from __future__ import annotations

import collections
import statistics as st
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import firehose as fh          # noqa: E402
import optimizer               # noqa: E402
from measure_orphan_rule import funded, load_scans   # noqa: E402  -- reuse, don't reinvent

CAPS = (8, 2, 3, 4, 5, 6)   # baseline (the profile's value) FIRST, so the deltas have a reference
RUNS = ("data/cbt_3yr_v27_catalyst", "data/cbt_3yr_v28_exposure")
QUIET = 3                       # "silent" = this many consecutive scans with no mention


def _silence(scans: dict) -> dict:
    """{anchor: {ticker: consecutive scans since it was last NAMED}}. A ticker held past that is
    riding on stickiness, not on coverage."""
    anchors, seen, out = sorted(scans), {}, {}
    for i, a in enumerate(anchors):
        named = {str(p["ticker"]).upper() for p in scans[a]}
        for t in named:
            seen[t] = i
        out[a] = {t: i - seen.get(t, i) for t in seen}
    return out


def main() -> int:
    runs = [Path(x) for x in (sys.argv[1:] or RUNS)]
    for rd in runs:
        run = rd if rd.is_absolute() else ROOT / rd
        need = ("firehose_scans.csv", "panel.csv", "volume.csv", "corpactions.json")
        if not all((run / f).exists() for f in need):
            print(f"  {run.name}: missing {[f for f in need if not (run/f).exists()]} -- skipped")
            continue
        fm = optimizer.load_financial_model(
            str(ROOT / ("investor_profile.forward.md" if run.name.startswith("cbs")
                        else "investor_profile.backtest.md")))
        scans = load_scans(run)
        panel = pd.read_csv(run / "panel.csv", index_col=0, parse_dates=True)
        quiet_at = _silence(scans)
        base_funded, base_final = None, None

        print(f"\n  {run.name}  (max_watchlist={fh.watchlist_cap(fm)}, "
              f"profile max_stale_scans={fm.get('max_stale_scans')})")
        # MEASURED ON THE FUNDED BOOK, not on `_stateful_watch`'s output: that returns the live
        # CANDIDATE POOL (~115-158 names) and the cull to max_watchlist happens inside backtest().
        # Reading slot occupancy off the pool answers a question nobody asked.
        print("     cap    funded posn-scans on a name silent >=3    SHARE OF CAPITAL on one"
              "    funded posns lost vs base   turnover")
        for cap in CAPS:
            f2 = {**fm, "max_stale_scans": cap}
            bt = fh.backtest(scans, f2, capital=50000, panel=panel,
                             freeze_panel=str(run / "panel.csv"))
            f_now = funded(bt)
            if base_funded is None:
                base_funded, base_final = f_now, bt.get("final")
            # anchors in the BOOK are rebalance dates; map each to the nearest scan anchor at or
            # before it, because silence is counted in scans, not calendar days.
            sa = sorted(quiet_at)
            import bisect
            npos = nstale = 0
            wt_all = wt_stale = 0.0
            for d, ww in f_now.items():
                j = bisect.bisect_right(sa, pd.Timestamp(d).tz_localize(sa[0].tz)
                                        if sa and sa[0].tz else pd.Timestamp(d)) - 1
                q = quiet_at[sa[j]] if 0 <= j < len(sa) else {}
                for tk, v in ww.items():
                    if v <= 0.01:
                        continue
                    npos += 1; wt_all += v
                    if q.get(str(tk).upper(), 0) >= QUIET:
                        nstale += 1; wt_stale += v
            # turnover: names entering the funded book per rebalance
            dates = sorted(f_now)
            churn = [len(set(f_now[d]) - set(f_now[dates[i - 1]])) for i, d in enumerate(dates) if i]
            lost = sum(1 for d, ww in base_funded.items() for tk, v in ww.items()
                       if v > 0.01 and tk not in (f_now.get(d) or {}))
            print(f"     {cap:>3}    {nstale:>5d} of {npos:<5d} ({100*nstale/max(npos,1):>4.1f}%)"
                  f"              {100*wt_stale/max(wt_all,1e-9):>5.1f}%"
                  f"              {lost:>5d}"
                  f"                 {st.mean(churn) if churn else 0:>5.2f}")
            if base_final and cap != CAPS[0]:
                print(f"           P&L (NOT a verdict -- paired replays of one curation): "
                      f"${bt.get('final', 0):,.0f} vs ${base_final:,.0f} "
                      f"({bt.get('final', 0)/base_final:.2f}x)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
