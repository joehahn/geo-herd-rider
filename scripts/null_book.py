#!/usr/bin/env python3
"""null_book.py — can a config beat a MATCHED-RANDOM book drawn from its own watchlist?

Table 8 ranks regions on SHARPE ALONE, so everything the SBT page recommends rests on Sharpe
measuring the config rather than the curation. This is the control for that, and it is the same
device scripts/null_pc_gap.py applies to pc_gap, lifted from one statistic to the whole equity curve.

THE NULL. Hold the curation, the weekly watchlists, the rebalance dates, the CASH level and the
ANCHOR weights all fixed. Take the non-anchor weight VECTOR the optimizer chose that week -- not just
its size, the actual numbers -- and reassign it, shuffled, to names drawn at random from that same
week's watchlist. Concentration, turnover profile and exposure are preserved by construction; the
only thing randomised is WHICH of the curator's names got the money. Replay, and read Sharpe off the
resulting daily series exactly as sweep_optimizer.metrics does.

WHAT THE THREE NUMBERS MEAN. The null's MEDIAN is what the watchlist alone delivers -- the part of
the book that is the curator's, not the optimizer's. The observed value's PERCENTILE in that
distribution is the part the config actually adds. The null's SPREAD is how much of the difference
between two cells on this page is simply which names the draw happened to land on.

WHY THE WEIGHT VECTOR IS PERMUTED RATHER THAN EQUAL-WEIGHTED. An equal-weight null changes two
things at once (which names, and how concentrated), so a config could beat it purely by being
concentrated. Permuting the real vector isolates identity, which is the only thing the optimizer's
name-picking controls.

    python scripts/null_book.py --draws 2000
    python scripts/null_book.py --sample 200 --draws 400
"""
from __future__ import annotations

import argparse
import bisect
import json
import random
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import firehose as fh  # noqa: E402
import optimizer  # noqa: E402
import provenance as canon  # noqa: E402
import score  # noqa: E402
from sweep_optimizer import load_scans  # noqa: E402

KEYS = ["max_watchlist", "concentration_cap", "lookback_period_days",
        "drop_unfunded_weeks", "risk_aversion", "min_trade_size"]
CELLS = [(6, 0.25, 21, 0, 16.0, 0.2),
         (4, 0.25, 21, 0, 16.0, 0.2),
         (20, 0.25, 21, 0, 16.0, 0.2),
         (6, 1.00, 21, 0, 0.5, 0.0),
         (6, 0.25, 21, 2, 16.0, 0.2)]
MET = ("sharpe", "final", "ann", "max_drawdown", "cancelled")
_W: dict = {}


def _init(fm0, scans, panel, anchors, draws):
    _W.update(fm0=fm0, scans=scans, panel=panel, anchors=anchors, draws=draws)


def _book(R: np.ndarray, wmat: np.ndarray, cap: float, rf: float) -> dict:
    """Every metric this script reports, off one (days x tickers) weight matrix. Deliberately the
    SAME arithmetic as firehose._daily_series -- value compounds day by day on the weights standing
    at the open, so the observed row reproduces the sweep's own Sharpe to the cent."""
    r = np.nan_to_num(R)
    port = (wmat * r).sum(axis=1)
    port[0] = 0.0                                  # day 1 of the trace earns nothing, as in _daily_series
    val = cap * np.cumprod(1 + port)
    vprev = np.empty_like(val); vprev[0] = cap; vprev[1:] = val[:-1]
    gain = (wmat * r * vprev[:, None]).sum(axis=0)  # per-ticker P&L; sums to the book's total
    rets = port[1:]
    sd = rets.std(ddof=1)
    sharpe = float((rets.mean() - rf / 252.0) / sd * np.sqrt(252)) if sd else None
    peak = np.maximum.accumulate(val)
    mdd = float(np.max((peak - val) / peak)) if len(val) else 0.0
    yrs = max(len(val), 1) / 252.0
    pos, neg = gain[gain > 0].sum(), gain[gain < 0].sum()
    return {"sharpe": sharpe, "final": float(val[-1]),
            "ann": float((val[-1] / cap) ** (1 / yrs) - 1) * 100 if val[-1] > 0 else None,
            "max_drawdown": 100 * mdd,
            "cancelled": float(100 * abs(neg) / pos) if pos else None}


def run_cell(combo) -> dict:
    fm = {**_W["fm0"], **dict(zip(KEYS, combo))}
    panel, scans, anchor_set = _W["panel"], _W["scans"], _W["anchors"]
    cap = float(fm.get("initial_investment_usd", 50_000))
    rf = float(fm.get("risk_free_rate", 0.04))
    b = fh.backtest(scans, fm, capital=cap, daily=True, panel=panel)
    sd_scans = sorted(scans)
    days = panel[score.BENCHMARK].dropna().index
    reb = [score.entry_index(days, a.strftime("%Y-%m-%dT%H:%M:%S%z"), fm.get("t_update_days"))
           for a in sd_scans]
    starts = [x for x in reb if x is not None]
    if not starts:
        return {"cell": list(combo)}
    d_idx = days[starts[0]:]
    # WEEK -> (weights, that week's eligible watchlist). Read the weights back off the daily alloc
    # rather than the log string, which is printed at 2dp and would round a 1.4% position to the
    # wrong side of the funded threshold.
    d = b.get("daily") or {}
    pos_of = {s: i for i, s in enumerate(d.get("dates") or [])}
    al = d.get("alloc") or {}
    watch = b.get("watch") or {}
    cols = sorted(set(al) | {t for w in watch.values() for t in w} | anchor_set)
    cix = {t: i for i, t in enumerate(cols)}
    R = panel[cols].reindex(d_idx).pct_change().to_numpy(dtype=float)
    segs = []                                   # (day position in d_idx, anchor weights, pick weights, pool)
    for k, a in enumerate(sd_scans):
        if reb[k] is None:
            continue
        p = pos_of.get(days[reb[k]].strftime("%Y-%m-%d"))
        if p is None:
            continue
        anc = {t: al[t][p] for t in al if t in anchor_set and al[t][p] > 1e-9}
        pick = {t: al[t][p] for t in al if t not in anchor_set and al[t][p] > 1e-9}
        pool = [t for t in watch.get(a, ()) if t not in anchor_set and t in cix]
        segs.append((p, anc, list(pick.values()), pool, list(pick)))
    if not segs:
        return {"cell": list(combo)}

    def _wmat(assign) -> np.ndarray:
        """assign(seg) -> list of (ticker, weight). Filled forward to the next rebalance."""
        m = np.zeros((len(d_idx), len(cols)))
        for s, (p, anc, wv, pool, real) in enumerate(segs):
            hi = segs[s + 1][0] if s + 1 < len(segs) else len(d_idx)
            for t, w in list(anc.items()) + assign(s):
                m[p:hi, cix[t]] = w
        return m

    obs = _book(R, _wmat(lambda s: list(zip(segs[s][4], segs[s][2]))), cap, rf)
    rng = random.Random(hash(combo) & 0xFFFFFFFF)
    null = {m: [] for m in MET}
    n_free = sum(1 for p, anc, wv, pool, real in segs if len(pool) > len(wv))
    for _ in range(_W["draws"]):
        def _a(s, rng=rng):
            p, anc, wv, pool, real = segs[s]
            if len(pool) <= len(wv):        # nothing to choose between: the draw IS the real book
                return list(zip(real, wv))
            names = rng.sample(pool, len(wv))
            shuf = wv[:]; rng.shuffle(shuf)
            return list(zip(names, shuf))
        r = _book(R, _wmat(_a), cap, rf)
        for m in MET:
            if r[m] is not None:
                null[m].append(r[m])
    out = {"cell": list(combo), "weeks": len(segs), "free_weeks": n_free,
           "picks_per_wk": round(statistics.fmean(len(s[2]) for s in segs), 2),
           "pool_per_wk": round(statistics.fmean(len(s[3]) for s in segs), 2)}
    for m in MET:
        v = sorted(null[m])
        if not v or obs[m] is None:
            continue
        out[m] = {"obs": round(obs[m], 4),
                  "null_med": round(statistics.median(v), 4),
                  "null_p05": round(v[int(0.05 * len(v))], 4),
                  "null_p95": round(v[int(0.95 * len(v))], 4),
                  "pct": round(100 * bisect.bisect_left(v, obs[m]) / len(v), 2)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default=canon.CANON_RUN)
    ap.add_argument("--draws", type=int, default=2000)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--workers", type=int, default=9)
    ap.add_argument("--out", default="data/null_book.json")
    a = ap.parse_args(argv)
    run = ROOT / a.run
    fm0 = optimizer.load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    scans = load_scans(run)
    panel = pd.read_csv(run / "panel.csv", index_col=0, parse_dates=True)
    anchors = set(fh.anchor_tickers(fm0))
    _init(fm0, scans, panel, anchors, a.draws)
    print(f"  run {a.run} · {a.draws:,} draws per cell · null = same weights, random names\n")
    print(f"  {'cell':30s} {'picks/pool':>11s} | "
          + " | ".join(f"{m:>26s}" for m in ("SHARPE obs / null med / %ile", "final obs / null med / %ile")))
    detail = []
    for c in CELLS:
        r = run_cell(c)
        detail.append(r)
        if "sharpe" not in r:
            print(f"  {str(tuple(c)):30s}  (no informative weeks)"); continue
        s, f = r["sharpe"], r["final"]
        print(f"  {str(tuple(c)):30s} {r['picks_per_wk']:4.1f}/{r['pool_per_wk']:<6.1f} | "
              f"{s['obs']:7.2f} {s['null_med']:7.2f} {s['pct']:6.1f}% | "
              f"${f['obs']:10,.0f} ${f['null_med']:10,.0f} {f['pct']:6.1f}%")
    grid = []
    if a.sample:
        import concurrent.futures as cf
        S = json.loads((ROOT / canon.CANON_SWEEP).read_text())
        allc = [tuple(c[k] for k in KEYS) for c in S["cells"]]
        del S
        pick = random.Random(12345).sample(allc, min(a.sample, len(allc)))
        print(f"\n  grid sample: {len(pick)} random cells x {a.draws} draws ...", flush=True)
        with cf.ProcessPoolExecutor(max_workers=a.workers, initializer=_init,
                                    initargs=(fm0, scans, panel, anchors, a.draws)) as ex:
            for r in ex.map(run_cell, pick, chunksize=2):
                if "sharpe" in r:
                    grid.append(r)
        print(f"  {len(grid)} cells\n")
        for m in MET:
            g = [r[m] for r in grid if m in r]
            hi = sum(1 for x in g if x["pct"] >= 95)
            lo = sum(1 for x in g if x["pct"] <= 5)
            # ORIENTATION-NEUTRAL LABELS. `pct` is always the percentile of the observed value in
            # the ascending null, so for sharpe/final/ann a HIGH percentile is the config winning and
            # for max_drawdown/cancelled a LOW one is -- printing "beats its null" for all five would
            # read the last two exactly backwards.
            print(f"    {m:13s} obs > null p95 {hi:4d}/{len(g)} ({100*hi/len(g):3.0f}%) · "
                  f"obs < null p05 {lo:4d} ({100*lo/len(g):3.0f}%) · "
                  f"median %ile {statistics.median(x['pct'] for x in g):5.1f} · "
                  f"median obs {statistics.median(x['obs'] for x in g):10,.2f} vs "
                  f"null med {statistics.median(x['null_med'] for x in g):10,.2f}")
    (ROOT / a.out).write_text(json.dumps({"run": a.run, "draws": a.draws,
                                          "detail": detail, "grid": grid}, indent=1))
    print(f"\n  wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
