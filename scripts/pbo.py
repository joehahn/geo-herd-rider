#!/usr/bin/env python3
"""pbo.py — Probability of Backtest Overfitting via Combinatorially Symmetric Cross-Validation.

THE PROBLEM THIS SOLVES. This repo picks one config out of a 6,300-cell sweep run on ONE history. The
in-sample winner is then, by construction, the cell that best fit that history -- and Bailey, Borwein,
Halbert and Lopez de Prado showed high backtest performance is easy to manufacture from a handful of
configurations alone. Everything in CLAUDE.md #6 is a hand-rolled version of this worry; CSCV is the
formal instrument.

WHAT CSCV DOES. Split the return path into S equal blocks. For every balanced way of splitting those
blocks into a TRAIN half and a TEST half (C(16,8) = 12,870 of them):
    1. rank all configs by the candidate ranker, computed on the TRAIN blocks only
    2. take that ranker's winner
    3. find where the winner sits, by out-of-sample Sharpe, among all configs on the TEST blocks
    4. record its relative rank w in (0,1), and the logit lambda = log(w / (1-w))
PBO is the share of splits where lambda < 0 -- i.e. where the in-sample champion came out BELOW the
out-of-sample median. PBO ~ 0.5 means the selection carries no information; low PBO means it does.

WHY THIS AND NOT THE TWO-CURATION TEST IT REPLACES. The earlier test used two curations as two folds.
n=2 is why five different candidate rankers all flipped sign between them: with one pair there is no
way to tell a real effect from which fold you started on. CSCV gives 12,870 folds from the same data.

BLOCKS ARE CONTIGUOUS AND NOT PURGED. A curation has a genuine time arrow -- events open, persist and
exit across weeks -- so adjacent blocks share live positions and are not independent. Lopez de Prado's
purging/embargo would drop the boundary observations; it is NOT done here, which biases PBO DOWNWARD
(train and test are more alike than they should be). Read the number as a floor.

    python scripts/pbo.py --sweep data/sweep_v9.json
"""
from __future__ import annotations
import argparse, itertools, json, math, sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep", default="data/sweep_v9.json")
    a = ap.parse_args(argv)
    S = json.loads((ROOT / a.sweep).read_text())
    cells = [c for c in S["cells"] if c.get("blocks")]
    if not cells:
        print("  no block stats -- re-run sweep_optimizer.py", file=sys.stderr)
        return 1
    NB = len(cells[0]["blocks"])
    # (config, block, stat) where stat = n, sum r, sum r^2, sum r+, sum r-.  All ADDITIVE, so any
    # union of blocks is a plain sum along axis 1 -- which is what makes 12,870 splits tractable.
    B = np.array([c["blocks"] for c in cells], dtype=np.float64)
    N = B.shape[0]
    combos = list(itertools.combinations(range(NB), NB // 2))
    print(f"  {N:,} configs · {NB} blocks · {len(combos):,} balanced splits", flush=True)

    def stats(idx):
        """(n, mean, sd, pos, neg) for every config over the given block indices."""
        s = B[:, list(idx), :].sum(axis=1)
        n = np.maximum(s[:, 0], 1.0)
        mu = s[:, 1] / n
        var = np.maximum(s[:, 2] / n - mu * mu, 1e-18)
        return n, mu, np.sqrt(var), s[:, 3], s[:, 4]

    def sharpe(idx):
        _, mu, sd, _, _ = stats(idx)
        return mu / sd * math.sqrt(252)

    def rank01(v, hi=True):
        """0..1 rank, 1 = best. NaNs sink to worst."""
        v = np.where(np.isfinite(v), v, -np.inf if hi else np.inf)
        o = np.argsort(v if hi else -v, kind="stable")
        r = np.empty(N); r[o] = np.arange(N) / (N - 1)
        return r

    def sel_sharpe(idx):   return sharpe(idx)
    def sel_negcanc(idx):
        _, _, _, pos, neg = stats(idx)
        return -np.abs(neg) / np.maximum(pos, 1e-12)
    def sel_robust(idx):
        _, _, _, pos, neg = stats(idx)
        canc = np.abs(neg) / np.maximum(pos, 1e-12)
        return -(rank01(-canc) + rank01(neg)) / 2      # low cancellation AND small downside sum
    WHOLE = {"winners": np.array([c.get("winners") or 0 for c in cells], dtype=float),
             "capital_hit": np.array([c.get("capital_hit") or 0 for c in cells], dtype=float)}

    RANKERS = {
        "Sharpe (in-sample)":  sel_sharpe,
        "-cancellation":       sel_negcanc,
        "robust (canc+downside)": sel_robust,
        # WHOLE-RUN metrics do not vary with the split, so their "selection" is the same config every
        # time. That is not a bug: it is exactly what picking a config off a whole-history number DOES,
        # and CSCV then measures how that single choice fares out-of-sample across 12,870 test halves.
        "winners (whole-run)":     lambda idx: WHOLE["winners"],
        "capital_hit (whole-run)": lambda idx: WHOLE["capital_hit"],
    }
    out = {}
    for name, fn in RANKERS.items():
        lam = np.empty(len(combos)); below = 0
        for j, tr in enumerate(combos):
            te = [i for i in range(NB) if i not in tr]
            best = int(np.nanargmax(fn(tr)))
            oos = sharpe(te)
            oos = np.where(np.isfinite(oos), oos, -np.inf)
            rank = int((oos < oos[best]).sum())          # how many it beat out-of-sample
            w = (rank + 1) / (N + 1)
            lam[j] = math.log(w / (1 - w)); below += (w < 0.5)
        out[name] = {"pbo": round(100 * below / len(combos), 1),
                     "median_lambda": round(float(np.median(lam)), 3),
                     "n_splits": len(combos)}
        print(f"    {name:<26} PBO {out[name]['pbo']:5.1f}%   median lambda "
              f"{out[name]['median_lambda']:+.2f}", flush=True)
    (ROOT / "data/pbo.json").write_text(json.dumps(out, indent=1))
    print("\n  wrote data/pbo.json")
    print("  PBO = share of splits where the in-sample pick landed BELOW the out-of-sample median.")
    print("  50% = the ranker carries no information. Lower is better.")
    print("  Blocks are NOT purged, so train and test share live positions -- read PBO as a FLOOR.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
