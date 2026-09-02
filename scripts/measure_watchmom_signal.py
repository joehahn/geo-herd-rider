#!/usr/bin/env python3
"""measure_watchmom_signal.py — does the watchlist's momentum ON the curation date predict the month?

THE QUESTION. At each curation the curator hands the optimizer a slate. Panel 2's blue line is that
slate's mean trailing 21-day change, and mu is a monotone transform of it. So: when the new slate is
already moving, does the book harvest more over the following month than when it is not?

TWO ANSWERS, because they are different questions:
  book   -- the book's own fractional change over the period. What was actually harvested, after the
            cull picked 2-3 names and the optimizer sized them.
  watch  -- the equal-weight forward return of the WHOLE slate over the same period. The curator's
            signal unmasked by sizing; if this is flat the slate had nothing to harvest.

RUN IT ACROSS CURATIONS, not on one (CLAUDE.md #6). Each run contributes ~4 (bootstrap) to ~36 (3-year)
paired periods; a relationship worth acting on should show the same sign in most of them.

FREE: journals, frozen panels, no LLM.

    scripts/measure_watchmom_signal.py
"""
from __future__ import annotations

import collections
import glob
import statistics as st
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, pearsonr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import firehose as fh          # noqa: E402
import optimizer               # noqa: E402


def load_scans(run: Path) -> dict:
    s = pd.read_csv(run / "firehose_scans.csv")
    out: dict = collections.defaultdict(list)
    for wk, g in s.groupby("week"):
        ts = pd.Timestamp(str(wk) + " 16:30", tz="America/New_York")
        out[ts] += [{"ticker": str(r.ticker).strip().upper(),
                     "thesis": ("" if pd.isna(r.thesis) else str(r.thesis)),
                     "thesis_live": bool(r.thesis_live),
                     "catalyst_resolved": bool(r.catalyst_resolved), "evidence_urls": []}
                    for r in g.itertuples() if isinstance(r.ticker, str) and str(r.ticker).strip()]
    return dict(sorted(out.items()))


POOL: list = []          # (slate d, book excess of SPY) over every period of every run


def main() -> int:
    rows_all = []
    print(f"{'run':26} {'n':>3}  {'rho(d,book)':>13} {'rho(d,slate)':>14} {'rho(d,excess)':>13} {'rho(d,SPY)':>11}")
    for rd in sorted(glob.glob(str(ROOT / "data" / "cb*"))):
        run = Path(rd)
        if not all((run / f).exists() for f in
                   ("firehose_scans.csv", "panel.csv", "volume.csv", "corpactions.json")):
            continue
        fm = optimizer.load_financial_model(str(ROOT / (
            "investor_profile.forward.md" if run.name.startswith("cbs") else "investor_profile.backtest.md")))
        lb = int(fm.get("optimizer_lookback_days") or 21)
        scans = load_scans(run)
        panel = pd.read_csv(run / "panel.csv", index_col=0, parse_dates=True)
        if panel.index.tz is not None:
            panel.index = panel.index.tz_localize(None)
        watch = fh._stateful_watch(scans, seed=[], fm=fm)
        bt = fh.backtest(scans, fm, capital=50000, daily=True, panel=panel,
                         freeze_panel=str(run / "panel.csv"))
        d = (bt.get("daily") or {}).get("dates") or []
        v = (bt.get("daily") or {}).get("value") or []
        if not d:
            continue
        di = {x: i for i, x in enumerate(d)}

        def at(day):                                  # last book value on/before `day`
            for k in range(len(d) - 1, -1, -1):
                if d[k] <= day:
                    return v[k]
            return v[0]

        def px(t, day):                               # last close on/before `day`
            if t not in panel.columns:
                return None
            i = panel.index.searchsorted(pd.Timestamp(day), side="right") - 1
            if i < 0:
                return None
            x = panel[t].iloc[i]
            return None if pd.isna(x) else float(x)

        anch = sorted(watch)
        pairs = []
        for k in range(len(anch) - 1):
            a0, a1 = str(anch[k].date()), str(anch[k + 1].date())
            names = [t for t in watch[anch[k]] if t in panel.columns]
            ds, fwd = [], []
            for t in names:
                p_now, p_then = px(t, a0), px(t, str((anch[k] - pd.Timedelta(days=lb)).date()))
                p_next = px(t, a1)
                if p_now and p_then and p_now > 0 and p_then > 0:
                    ds.append(p_now / p_then - 1)
                    if p_next and p_next > 0:
                        fwd.append(p_next / p_now - 1)
            if len(ds) < 5 or len(fwd) < 5:
                continue
            b0, b1 = at(a0), at(a1)
            # SPY over the SAME period, so the book's return can be read net of the market. A slate
            # is hot mostly when the market is hot, so the raw correlation is partly market timing.
            s0, s1 = px("SPY", a0), px("SPY", a1)
            spy = (s1 / s0 - 1) if (s0 and s1) else np.nan
            pairs.append((float(np.mean(ds)), (b1 / b0 - 1) if b0 else np.nan,
                          float(np.mean(fwd)), spy))
        if len(pairs) < 4:
            print(f"  {run.name:24} {len(pairs):3d}   too few periods"); continue
        X = np.array([p[0] for p in pairs]); YB = np.array([p[1] for p in pairs])
        YW = np.array([p[2] for p in pairs]); SP = np.array([p[3] for p in pairs])
        ok = ~np.isnan(YB) & ~np.isnan(SP)
        rb = spearmanr(X[ok], YB[ok]).statistic
        rw = spearmanr(X, YW).statistic
        rx = spearmanr(X[ok], YB[ok] - SP[ok]).statistic          # book EXCESS of SPY
        rs = spearmanr(X[ok], SP[ok]).statistic                   # is d just calling the market?
        rows_all.append((run.name, len(pairs), rb, rw, rx, rs))
        for _x, _b, _w2, _s2 in pairs:
            if not (np.isnan(_b) or np.isnan(_s2)):
                POOL.append((_x, _b - _s2, run.name))
        print(f"  {run.name:24} {len(pairs):3d}  {rb:+13.2f} {rw:+14.2f} {rx:+13.2f} {rs:+11.2f}")
    if not rows_all:
        print("no runs"); return 1
    print(f"\n  ACROSS {len(rows_all)} CURATIONS, {sum(r[1] for r in rows_all)} periods")
    for i, lbl in ((2, "BOOK return next period      "), (3, "SLATE equal-weight fwd return"),
                   (4, "BOOK return NET OF SPY       "), (5, "SPY return (market timing?)  ")):
        v = [r[i] for r in rows_all]
        print(f"    rho(slate d, {lbl}) : median {st.median(v):+.2f}   "
              f"positive in {sum(1 for x in v if x > 0)} of {len(v)}")
    # WHERE THE RELATIONSHIP LIVES. A correlation says "monotone-ish"; the question worth acting on
    # is whether a COLD slate is worth sitting out. Quartiles of slate d, against what the book then
    # harvested net of SPY. Pooled across runs, which OVERSTATES n -- the curations share a corpus
    # and a window, so these periods are far from independent. Read the shape, not the p-value.
    if POOL:
        X = np.array([q[0] for q in POOL]); Y = np.array([q[1] for q in POOL])
        cut = np.quantile(X, [0.25, 0.5, 0.75])
        lab = ["Q1 coldest", "Q2", "Q3", "Q4 hottest"]
        idx = np.digitize(X, cut)
        print(f"\n  SLATE d AT CURATION vs BOOK RETURN NET OF SPY, pooled ({len(POOL)} periods, "
              f"NOT independent)")
        print(f"    {'bucket':12} {'slate d range':>20} {'n':>4}  {'median excess':>14} {'mean':>8} {'win%':>6}")
        for b in range(4):
            m = idx == b
            if m.sum() < 5:
                continue
            print(f"    {lab[b]:12} {X[m].min():+9.1%}..{X[m].max():+8.1%} {int(m.sum()):4d}  "
                  f"{np.median(Y[m]):+13.2%} {Y[m].mean():+8.2%} {100*(Y[m] > 0).mean():5.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
