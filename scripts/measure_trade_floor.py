#!/usr/bin/env python3
"""measure_trade_floor.py — should `min_trade_size` be back in the sweep grid?

WHY ASK. The axis was dropped on 2026-09-01 because floor 0 beat 0.2 "at every cap" -- but by only
1.4x, which the profile note itself flags as inside the noise band, and it was measured when the
book funded 2-3 names. At concentration_cap 0.25 it now funds 5-6, and the floor's whole effect is
its INTERACTION with the cap, which is the thing sweep_optimizer.py exists to see.

WHY NOT JUST RE-ADD IT. Four values x 5,040 cells is ~2.3 hours. This is the 3-knob slice that can
actually interact -- floor x cap x width -- with lookback/drop/risk pinned at the profile: 224 cells,
~2 minutes. If the axis shows structure here it has earned its place in the canonical grid; if it is
flat, the 2026-09-01 decision is confirmed at 1% of the cost.

INFEASIBLE CELLS ARE MARKED, NOT SILENTLY SCORED. A floor above the cap cannot be satisfied, and
curator.py resolves that by letting the CAP win and the floor yield -- so such a cell silently
becomes a floor-0 cell. Scoring it as evidence about that floor would be reading a duplicate as a
measurement.

REPORTS CAPTURE, not just cancellation. Sharpe-ranked grids have pointed the opposite way from
escalator harvesting all through this work, so both columns are printed.

    python scripts/measure_trade_floor.py [--run PATH] [--workers N]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import itertools
import statistics as st
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import firehose as fh          # noqa: E402
import optimizer               # noqa: E402
from measure_orphan_rule import funded, load_scans   # noqa: E402

FLOORS = [0.0, 0.05, 0.10, 0.20]
CAPS   = [0.15, 0.20, 0.25, 0.40, 0.60, 0.80, 1.00]
WIDTHS = [4, 6, 8, 12, 16, 20, 24, 30]
H = 21                      # forward window for "escalator", in bars

_G = {}


def _init(run: str):
    _G["run"] = Path(run)
    _G["fm"] = optimizer.load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    _G["scans"] = load_scans(_G["run"])
    _G["panel"] = pd.read_csv(_G["run"] / "panel.csv", index_col=0, parse_dates=True)
    idx = _G["panel"].index
    _G["pidx"] = idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx
    _G["alw"] = {str(t).upper() for t in (_G["fm"].get("always_include") or [])}


def _fmax(t, i0):
    panel = _G["panel"]
    if t not in panel.columns:
        return None
    s = panel[t].to_numpy()
    if i0 >= len(s) - 1:
        return None
    p0 = s[i0]
    if not (p0 == p0) or p0 <= 0:
        return None
    seg = s[i0 + 1:i0 + 1 + H]
    seg = seg[seg == seg]
    return None if len(seg) == 0 else seg.max() / p0


def _cell(c):
    mts, cap, mw = c
    fm = {**_G["fm"], "min_trade_size": mts, "concentration_cap": cap, "max_watchlist": mw}
    bt = fh.backtest(_G["scans"], fm, capital=50000, panel=_G["panel"],
                     freeze_panel=str(_G["run"] / "panel.csv"))
    f = funded(bt)
    alw, pidx = _G["alw"], _G["pidx"]
    n, big, esc, mo = [], [], 0, 0
    for d, w in f.items():
        held = {t: v for t, v in w.items() if v > 0.01 and t not in alw}
        n.append(len(held))
        big.append(max(held.values(), default=0.0))
        ts = pd.Timestamp(d)
        ts = ts.tz_localize(None) if ts.tz else ts
        i0 = int(pidx.searchsorted(ts, side="right")) - 1
        if i0 < 0:
            continue
        h = [t for t in held if (_fmax(t, i0) or 0) >= 1.5]
        esc += len(h)
        mo += 1 if h else 0
    return {"mts": mts, "cap": cap, "mw": mw,
            "infeasible": mts > cap,                 # floor above cap: curator.py drops the floor
            "final": float(bt.get("final") or 0), "funded": st.median(n) if n else 0,
            "biggest": max(big) if big else 0.0, "esc": esc, "months": mo, "rebal": len(f)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="data/cbt_3yr_v30_evrank")
    ap.add_argument("--workers", type=int, default=10)
    a = ap.parse_args()
    cells = list(itertools.product(FLOORS, CAPS, WIDTHS))
    print(f"  {len(cells)} cells over min_trade_size x concentration_cap x max_watchlist "
          f"({a.run}); lookback/drop/risk pinned at the profile\n", flush=True)
    out = []
    with cf.ProcessPoolExecutor(max_workers=a.workers, initializer=_init,
                                initargs=(str(ROOT / a.run),)) as ex:
        for i, r in enumerate(ex.map(_cell, cells, chunksize=4), 1):
            out.append(r)
            if i % 56 == 0:
                print(f"    {i}/{len(cells)}", flush=True)

    ok = [r for r in out if not r["infeasible"]]
    inf = [r for r in out if r["infeasible"]]
    print(f"\n  {len(inf)} cells INFEASIBLE (floor > cap; curator.py drops the floor) -- excluded\n")
    print("  BY FLOOR, over the feasible cells:")
    print(f"    {'floor':>6}{'n':>5}{'median final':>15}{'median funded':>15}"
          f"{'ESCALATORS: median':>21}{'best':>7}")
    for mts in FLOORS:
        g = [r for r in ok if r["mts"] == mts]
        if not g:
            continue
        print(f"    {mts:>6.2f}{len(g):>5}{st.median(r['final'] for r in g):>15,.0f}"
              f"{st.median(r['funded'] for r in g):>15.0f}"
              f"{st.median(r['esc'] for r in g):>21.0f}{max(r['esc'] for r in g):>7}")
    print("\n  AT THE LIVE CAP/WIDTH (cap 0.25, max_watchlist 16):")
    for r in sorted([r for r in out if r["cap"] == 0.25 and r["mw"] == 16], key=lambda x: x["mts"]):
        tag = "  INFEASIBLE" if r["infeasible"] else ""
        print(f"    floor {r['mts']:.2f}  final ${r['final']:>10,.0f} · funded {r['funded']:.0f}"
              f" · biggest {100*r['biggest']:.0f}% · escalators {r['esc']} in {r['months']}/{r['rebal']}{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
