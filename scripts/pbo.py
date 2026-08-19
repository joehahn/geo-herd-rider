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

ROOT = Path(__file__).resolve().parent.parent


def _agg(blocks, idx):
    """Exact (n, mean, stdev, pos, neg) over a union of blocks -- every stored field is additive."""
    n = s1 = s2 = pos = neg = 0.0
    for i in idx:
        b = blocks[i]
        n += b[0]; s1 += b[1]; s2 += b[2]; pos += b[3]; neg += b[4]
    if n < 3:
        return None
    mu = s1 / n
    var = max(s2 / n - mu * mu, 1e-18)
    return n, mu, math.sqrt(var), pos, neg


def sharpe(blocks, idx):
    a = _agg(blocks, idx)
    return None if a is None else a[1] / a[2] * math.sqrt(252)


def cancel(blocks, idx):
    """Portfolio-level analogue of the cancellation metric: |down moves| / up moves, on these blocks."""
    a = _agg(blocks, idx)
    return None if a is None or a[3] <= 0 else abs(a[4]) / a[3]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep", default="data/sweep_v9.json")
    ap.add_argument("--splits", type=int, default=0, help="0 = all C(S,S/2); else sample this many")
    a = ap.parse_args(argv)
    S = json.loads((ROOT / a.sweep).read_text())
    cells = [c for c in S["cells"] if c.get("blocks")]
    if not cells:
        print("  no block stats in this sweep -- re-run sweep_optimizer.py", file=sys.stderr)
        return 1
    NB = len(cells[0]["blocks"])
    keys = list(S["grid"])
    print(f"  {len(cells):,} configs · {NB} blocks · C({NB},{NB//2}) = "
          f"{math.comb(NB, NB//2):,} balanced splits", flush=True)

    # candidate rankers, each a function of (cell, block-index-list) -> score, HIGHER IS BETTER
    RANKERS = {
        "Sharpe":            lambda c, i: sharpe(c["blocks"], i),
        "-cancellation":     lambda c, i: (lambda v: None if v is None else -v)(cancel(c["blocks"], i)),
        "robust (canc+DD)":  None,     # needs cross-config ranks; handled below
        "winners (whole-run)": lambda c, i: c.get("winners"),
        "capital_hit (whole-run)": lambda c, i: c.get("capital_hit"),
    }
    combos = list(itertools.combinations(range(NB), NB // 2))
    out = {}
    for name, fn in RANKERS.items():
        lam, below = [], 0
        for tr in combos:
            te = [i for i in range(NB) if i not in tr]
            if name == "robust (canc+DD)":
                # rank-average of cancellation and drawdown-proxy on the TRAIN blocks. Drawdown is not
                # additive across blocks, so its stand-in is the block-set's downside sum -- the same
                # quantity the real metric is trying to punish.
                cc = [(cancel(c["blocks"], tr), c) for c in cells]
                dd = [(-_agg(c["blocks"], tr)[4] if _agg(c["blocks"], tr) else None, c) for c in cells]
                ok = [i for i in range(len(cells)) if cc[i][0] is not None and dd[i][0] is not None]
                rc = {i: r for r, i in enumerate(sorted(ok, key=lambda i: cc[i][0]))}
                rd = {i: r for r, i in enumerate(sorted(ok, key=lambda i: dd[i][0]))}
                best = min(ok, key=lambda i: rc[i] + rd[i])
            else:
                sc = [(fn(c, tr), i) for i, c in enumerate(cells)]
                sc = [(v, i) for v, i in sc if v is not None]
                if not sc:
                    continue
                best = max(sc)[1]
            oos = [(sharpe(c["blocks"], te), i) for i, c in enumerate(cells)]
            oos = [(v, i) for v, i in oos if v is not None]
            oos.sort()
            rank = next(r for r, (_, i) in enumerate(oos) if i == best)
            w = (rank + 1) / (len(oos) + 1)
            lam.append(math.log(w / (1 - w)))
            below += (w < 0.5)
        out[name] = {"pbo": 100 * below / len(lam), "median_lambda": sorted(lam)[len(lam) // 2],
                     "n_splits": len(lam)}
        print(f"    {name:<26} PBO {out[name]['pbo']:5.1f}%   median lambda {out[name]['median_lambda']:+.2f}",
              flush=True)
    (ROOT / "data/pbo.json").write_text(json.dumps(out, indent=1))
    print(f"\n  wrote data/pbo.json")
    print("  PBO = share of splits where the in-sample winner landed BELOW the out-of-sample median.")
    print("  ~50% means the ranker carries no information. Lower is better. Not purged, so read as a floor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
