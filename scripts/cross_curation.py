#!/usr/bin/env python3
"""cross_curation.py — choose optimizer knobs ACROSS curations, and validate the choice out of sample.

WHY THIS EXISTS. Non-negotiable #6: one curation's P&L cannot adjudicate anything -- the same settings
run twice gave median finals of $117,200 and $62,997. A 7,200-cell sweep is not 7,200 samples, it is
ONE book viewed 7,200 ways, so a lucky curation lifts every cell at once and an in-sample sweep peak
is indistinguishable from luck.

THE FIX, and the only selection method here that has ever validated out of sample: rank every cell
WITHIN its own sweep (that cancels the curation-level luck exactly), then average those percentile
ranks ACROSS curations. Leave-one-curation-out says whether the average transfers to a book it was
never chosen from.

THIS FILE EXISTS BECAUSE THE 2026-08-31 RUN OF THAT METHOD WAS NOT COMMITTED. It chose the live cell
[20, 0.40, 30, 0, 8.0, 0.20] and is quoted in investor_profile.backtest.md (K=13, rho 0.292,
Spearman-Brown 0.84, LOO 79.7th percentile), but it lived in a scratchpad and nothing on disk can
reproduce it.

DEDUPE BY RUN DIR, NOT BY FILE. data/ holds 19 old-grid sweeps over only 13 distinct curations:
sweep_bw22/bw23 are bit-identical in `final`, as are v22/v23 and wk22/wk23. Counting files would put
a twin of the held-out curation inside the training fold and inflate both rho and the LOO number.
`loo_region.py` (2026-08-19) keys by file stem and has this bug.

    scripts/cross_curation.py                                  # every sweep on the majority grid
    scripts/cross_curation.py --pin min_trade_size=0.0         # the slice the sizing fix left valid
    scripts/cross_curation.py --metric final --width 50
"""
from __future__ import annotations

import argparse
import glob
import itertools
import json
import statistics as st
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parent.parent

# sign: +1 = bigger is better.
METRICS = {"sharpe": 1, "final": 1, "ann": 1, "gain_pain": 1, "cancelled": -1, "max_drawdown": -1}


def load(pins: dict, metric: str, min_cells: int, grid_cells: int = 0) -> tuple[list, dict, list]:
    """Return (tags, {tag: {cell: value}}, keys) for one grid signature, one sweep per run dir."""
    by_run: dict[str, tuple[float, str, dict, list]] = {}
    for sf in sorted(glob.glob(str(ROOT / "data" / "sweep_*.json"))):
        d = json.loads(Path(sf).read_text())
        grid = d.get("grid") or {}
        if not grid or metric not in (d["cells"][0] if d["cells"] else {}):
            continue
        keys = [k for k in grid if k not in pins]
        cells = {}
        for c in d["cells"]:
            if c.get(metric) is None:
                continue
            if any(c.get(k) != v for k, v in pins.items()):
                continue
            cells[tuple(c[k] for k in keys)] = c[metric]
        if len(cells) < min_cells:
            continue
        if grid_cells and len(d["cells"]) != grid_cells:
            continue
        # one sweep per curation: keep the newest, so a re-sweep supersedes its predecessor.
        mt = Path(sf).stat().st_mtime
        run = d["run"]
        if run not in by_run or mt > by_run[run][0]:
            by_run[run] = (mt, Path(sf).stem.replace("sweep_", ""), cells, keys)

    # keep only the grid signature the majority of curations share -- mixing 6,300- and 7,200-cell
    # grids collapses the common cells to a few hundred and measures a different question.
    sig = collections_mode([tuple(v[3]) + tuple(sorted(v[2])[:1]) for v in by_run.values()])
    keep = {r: v for r, v in by_run.items() if tuple(v[3]) + tuple(sorted(v[2])[:1]) == sig}
    common = set.intersection(*[set(v[2]) for v in keep.values()])
    tags = [(v[1], r, len(v[2])) for r, v in sorted(keep.items(), key=lambda kv: kv[1][1])]
    vals = {v[1]: {c: v[2][c] for c in common} for v in keep.values()}
    return tags, vals, list(keep.values())[0][3]


def collections_mode(xs):
    return max(set(xs), key=xs.count)


def pctile_ranks(cells: list, vals: dict, sgn: int) -> np.ndarray:
    """Percentile rank of each cell within one curation. 100 = best in that sweep."""
    v = sgn * np.array([vals[c] for c in cells], float)
    order = np.argsort(np.argsort(v))
    return 100.0 * order / (len(v) - 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", default="sharpe", choices=sorted(METRICS))
    ap.add_argument("--pin", action="append", default=[], help="knob=value, e.g. min_trade_size=0.0")
    ap.add_argument("--width", type=int, nargs="*", default=[1, 10, 50],
                    help="region widths to validate (top-N cells by mean percentile rank)")
    ap.add_argument("--top", type=int, default=10, help="how many winning cells to print")
    ap.add_argument("--ref", default="", help="reference cell as knob=value,... (e.g. the live config)")
    ap.add_argument("--min-cells", type=int, default=1000)
    ap.add_argument("--grid-cells", type=int, default=0,
                    help="require exactly this many cells per sweep, to pick ONE grid generation "
                         "(7200 = the 2026-08-24 grid, 6300 = the one before it). 0 = majority grid.")
    a = ap.parse_args()

    pins = {}
    for p in a.pin:
        k, _, v = p.partition("=")
        pins[k] = float(v) if "." in v else int(v)

    tags, vals, keys = load(pins, a.metric, a.min_cells, a.grid_cells)
    cells = sorted(next(iter(vals.values())))
    sgn = METRICS[a.metric]
    print(f"metric {a.metric}   pins {pins or '{}'}   knobs {keys}")
    print(f"{len(tags)} curations x {len(cells)} common cells\n")
    for t, run, n in tags:
        print(f"  {t:26s} {run:34s} {n} cells")

    P = {t: pctile_ranks(cells, vals[t], sgn) for t in vals}
    M = np.array([P[t] for t in vals])

    rho = spearmanr(M.T).statistic
    off = [rho[i][j] for i, j in itertools.combinations(range(len(M)), 2)] if len(M) > 2 else [rho]
    r = float(np.mean(off))
    K = len(M)
    print(f"\nmean pairwise rank rho between curations : {r:.3f}  (min {min(off):+.2f} max {max(off):+.2f})")
    print(f"Spearman-Brown reliability at K={K}       : {K * r / (1 + (K - 1) * r):.3f}"
          f"   (one sweep alone: {r:.3f})")

    mean_rank = M.mean(axis=0)
    order = np.argsort(-mean_rank)
    print(f"\nTOP {a.top} CELLS by mean percentile rank across all {K} curations")
    print(f"  {'rank':>4}  {'  '.join(f'{k[:13]:>13s}' for k in keys)}   mean%  worst%   n>=50%")
    for i in order[:a.top]:
        col = M[:, i]
        print(f"  {list(order).index(i) + 1:>4}  "
              f"{'  '.join(f'{cells[i][j]:>13g}' for j in range(len(keys)))}   "
              f"{mean_rank[i]:5.1f}  {col.min():5.1f}   {int((col >= 50).sum())}/{K}")

    if a.ref:
        ref = tuple((float(v) if "." in v else int(v))
                    for k, _, v in (p.partition("=") for p in a.ref.split(",")) if k not in pins)
        if ref in set(cells):
            i = cells.index(ref)
            print(f"\nREFERENCE cell {ref}: mean percentile {mean_rank[i]:.1f}, "
                  f"grid rank {list(order).index(i) + 1} of {len(cells)}, "
                  f"beats-median in {int((M[:, i] >= 50).sum())}/{K} curations")
        else:
            print(f"\nREFERENCE cell {ref} is not on this grid")

    # ---- leave-one-curation-out: choose on K-1, score on the curation never seen.
    print(f"\nLEAVE-ONE-CURATION-OUT (choose on {K - 1}, score the held-out {1}); random = 50.0")
    for w in a.width:
        outs = []
        for h in range(K):
            train = np.delete(M, h, axis=0).mean(axis=0)
            pick = np.argsort(-train)[:w]
            outs.append(float(np.mean(M[h][pick])))
        print(f"  top-{w:<4d} out-of-sample percentile: {st.mean(outs):5.1f}   "
              f"(worst fold {min(outs):5.1f}, best {max(outs):5.1f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
