#!/usr/bin/env python3
"""null_pc_gap.py — is the funded-minus-watched pc gap SELECTION, or is it MOMENTUM?

`pc_gap` (sweep_optimizer._pc_scores) says the tickers the optimizer funded rose ~2pt/period more
than the watchlist they were drawn from. Two things could produce that number and only one of them
is worth having, so this script runs both controls:

  NULL A -- MATCHED RANDOM. Each week the optimizer funded m_k of the W_k names on that week's
      watchlist. Draw m_k of them AT RANDOM instead, same weeks, same counts, and recompute the gap.
      Repeat B times. E[gap] = 0 by construction (a uniform draw's expected mean IS the watchlist
      mean), so this is not asking whether random does worse -- it is measuring how big a gap PURE
      SAMPLING NOISE produces at these week-by-week sample sizes, which at m_k ~ 2-3 out of ~6 is a
      lot more than the naive ticker-week error bar suggests. The observed gap's percentile in that
      distribution is the honest significance.

  CONTROL B -- MOMENTUM MATCHED. The same m_k slots filled by firehose._trend_rank at the same
      as-of day and the same lookback -- i.e. what a naive "buy whatever has been going up" rule
      would have funded out of the identical candidate pool. This is the control the random null
      CANNOT give: the mean-variance sizing reads the same lookback window the trend rank does, so
      a gap that merely reproduces momentum is circular, not skill. Read the two numbers as a pair:
      A says the gap is bigger than chance, B says whether it is bigger than the free version.

WHY THE GAP AND NOT THE LEVEL. pc_fund_med on its own moves when the WATCHLIST moves, so a cell
could score well by curating better rather than by funding better. The gap is paired within the week
and within the same candidate pool, so the watchlist cancels and only the funding choice is left.

    python scripts/null_pc_gap.py --run data/cbt_3yr_v21_evscans12 --draws 5000
    python scripts/null_pc_gap.py --sample 200        # how often the gap clears its own null, grid-wide
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

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

# The cells reported in detail. Chosen to span the SIGN of the gap rather than to flatter it: the
# live config, the two grid corners where funding is barely constrained (a loose optimizer funds
# almost the whole watchlist, so there is little to select), and the two knobs measured to flip the
# gap negative on the marginals -- max_watchlist 20 and drop_unfunded_weeks 2.
CELLS = [(6, 0.25, 21, 0, 16.0, 0.2),
         (4, 0.25, 21, 0, 16.0, 0.2),
         (20, 0.25, 21, 0, 16.0, 0.2),
         (6, 1.00, 21, 0, 0.5, 0.0),
         (6, 0.25, 21, 2, 16.0, 0.2)]

_W: dict = {}


def _init(fm0, scans, panel, anchors, draws):
    _W.update(fm0=fm0, scans=scans, panel=panel, anchors=anchors, draws=draws)


def weekly(fm, scans, panel, anchor_set) -> list[dict]:
    """One record per rebalance period: the watchlist, what was funded, and every ticker's pc.

    Everything downstream is resampling arithmetic over these records, so the expensive part (the
    backtest) runs ONCE per cell however many draws the null takes."""
    b = fh.backtest(scans, fm, capital=float(fm.get("initial_investment_usd", 50_000)),
                    daily=True, panel=panel)
    sd = sorted(scans)
    days = panel[score.BENCHMARK].dropna().index
    reb = [score.entry_index(days, a.strftime("%Y-%m-%dT%H:%M:%S%z"), fm.get("t_update_days"))
           for a in sd]
    watch, d = b.get("watch") or {}, b.get("daily") or {}
    pos_of = {s: i for i, s in enumerate(d.get("dates") or [])}
    al = d.get("alloc") or {}
    out = []
    for k in range(len(sd) - 1):
        i, j = reb[k], reb[k + 1]
        if i is None or j is None or j <= i:
            continue
        d0, d1 = days[i], days[j]
        pos = pos_of.get(d0.strftime("%Y-%m-%d"))
        pc, funded = {}, []
        for t in watch.get(sd[k], ()):
            if t in anchor_set or t == score.BENCHMARK or t not in panel.columns:
                continue
            p0, p1 = panel.loc[d0, t], panel.loc[d1, t]
            if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
                continue
            pc[t] = float(p1 / p0 - 1)
            if pos is not None and al.get(t) and al[t][pos] > 0.01:
                funded.append(t)
        # A week is informative only when the optimizer actually CHOSE -- funded some but not all.
        # Weeks where every watched name got capital contribute exactly 0 to the gap and to every
        # null draw alike, so they are dropped from both rather than padding both with zeros.
        if len(pc) >= 2 and 0 < len(funded) < len(pc):
            out.append({"asof": d0, "pc": pc, "funded": funded, "final": b.get("final")})
    return out, b.get("final")


def _gap(recs, pick) -> float:
    """Mean over weeks of (mean pc of the picked names - mean pc of that week's whole watchlist)."""
    g = [statistics.fmean([r["pc"][t] for t in pick(r)]) - statistics.fmean(r["pc"].values())
         for r in recs]
    return 100 * statistics.fmean(g) if g else float("nan")


def run_cell(combo) -> dict:
    fm = {**_W["fm0"], **dict(zip(KEYS, combo))}
    recs, final = weekly(fm, _W["scans"], _W["panel"], _W["anchors"])
    if len(recs) < 5:
        return {"cell": list(combo), "weeks": len(recs), "final": final}
    obs = _gap(recs, lambda r: r["funded"])
    # NULL A. One RNG seeded per cell so the run is reproducible and two cells do not share a draw
    # sequence (which would correlate their percentiles for no reason).
    rng = random.Random(hash(combo) & 0xFFFFFFFF)
    pool = [(list(r["pc"]), len(r["funded"])) for r in recs]
    base = [statistics.fmean(r["pc"].values()) for r in recs]
    null = []
    for _ in range(_W["draws"]):
        g = [statistics.fmean([recs[i]["pc"][t] for t in rng.sample(names, m)]) - base[i]
             for i, (names, m) in enumerate(pool)]
        null.append(100 * statistics.fmean(g))
    null.sort()
    import bisect
    pct = 100 * bisect.bisect_left(null, obs) / len(null)
    # CONTROL B. Same slots, filled by the trailing risk-adjusted return the cull itself ranks on.
    lb = int(fm.get("lookback_period_days", 21))

    def _mom(r):
        sc = fh._trend_rank(list(r["pc"]), _W["panel"], r["asof"], lb)
        return sorted(r["pc"], key=lambda t: (-sc.get(t, float("-inf")), t))[:len(r["funded"])]
    mom = _gap(recs, _mom)
    mpct = 100 * bisect.bisect_left(null, mom) / len(null)
    return {"cell": list(combo), "weeks": len(recs), "final": final,
            "obs": round(obs, 3), "mom": round(mom, 3),
            "obs_pct": round(pct, 2), "mom_pct": round(mpct, 2),
            "obs_minus_mom": round(obs - mom, 3),
            "null_sd": round(statistics.pstdev(null), 3),
            "null_p95": round(null[int(0.95 * len(null))], 3),
            "funded_per_wk": round(statistics.fmean(len(r["funded"]) for r in recs), 2),
            "watched_per_wk": round(statistics.fmean(len(r["pc"]) for r in recs), 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default=canon.CANON_RUN)
    ap.add_argument("--draws", type=int, default=5000)
    ap.add_argument("--sample", type=int, default=0,
                    help="also run N RANDOM grid cells (fewer draws each) to answer the general "
                         "question: across the grid, how often does the gap clear its own null?")
    ap.add_argument("--workers", type=int, default=9)
    ap.add_argument("--out", default="data/null_pc_gap.json")
    a = ap.parse_args(argv)
    run = ROOT / a.run
    fm0 = optimizer.load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    scans = load_scans(run)
    panel = pd.read_csv(run / "panel.csv", index_col=0, parse_dates=True)
    anchors = set(fh.anchor_tickers(fm0))
    _init(fm0, scans, panel, anchors, a.draws)

    print(f"  run {a.run} · {len(scans)} scans · {a.draws:,} draws per cell\n")
    hdr = (f"{'cell':32s} {'final':>10s} {'wk':>3s} {'fund/wk':>8s} "
           f"{'OBS':>7s} {'%ile':>6s} {'MOM':>7s} {'%ile':>6s} {'obs-mom':>8s} {'null sd':>8s}")
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    detail = []
    for c in CELLS:
        r = run_cell(c)
        detail.append(r)
        if "obs" not in r:
            print(f"{str(tuple(c)):32s}  too few informative weeks ({r['weeks']})")
            continue
        print(f"{str(tuple(c)):32s} ${r['final']:9,.0f} {r['weeks']:3d} {r['funded_per_wk']:4.1f}"
              f"/{r['watched_per_wk']:<3.1f} {r['obs']:+7.2f} {r['obs_pct']:5.1f}% "
              f"{r['mom']:+7.2f} {r['mom_pct']:5.1f}% {r['obs_minus_mom']:+8.2f} {r['null_sd']:8.2f}")

    grid = []
    if a.sample:
        import concurrent.futures as cf
        import itertools
        S = json.loads((ROOT / canon.CANON_SWEEP).read_text())
        allc = [tuple(c[k] for k in KEYS) for c in S["cells"]]
        del S
        rng = random.Random(12345)
        pick = rng.sample(allc, min(a.sample, len(allc)))
        print(f"\n  grid sample: {len(pick)} random cells x 400 draws ...", flush=True)
        with cf.ProcessPoolExecutor(max_workers=a.workers, initializer=_init,
                                    initargs=(fm0, scans, panel, anchors, 400)) as ex:
            for r in ex.map(run_cell, pick, chunksize=2):
                if "obs" in r:
                    grid.append(r)
        ok95 = sum(1 for r in grid if r["obs_pct"] >= 95)
        okmom = sum(1 for r in grid if r["obs"] > r["mom"])
        print(f"  {len(grid)} cells with >=5 informative weeks")
        print(f"    gap clears its OWN matched-random null at p95 : {ok95:4d} / {len(grid)} "
              f"({100*ok95/len(grid):.0f}%)")
        print(f"    gap beats the MOMENTUM control                : {okmom:4d} / {len(grid)} "
              f"({100*okmom/len(grid):.0f}%)")
        print(f"    median observed gap {statistics.median(r['obs'] for r in grid):+.2f}pt · "
              f"median momentum gap {statistics.median(r['mom'] for r in grid):+.2f}pt · "
              f"median null sd {statistics.median(r['null_sd'] for r in grid):.2f}pt")
    (ROOT / a.out).write_text(json.dumps({"run": a.run, "draws": a.draws,
                                          "detail": detail, "grid": grid}, indent=1))
    print(f"\n  wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
