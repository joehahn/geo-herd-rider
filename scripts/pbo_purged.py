#!/usr/bin/env python3
"""pbo_purged.py — PURGED Combinatorially Symmetric Cross-Validation, parallel over splits.

WHY PURGING CHANGES THE ANSWER. Plain CSCV splits the history into train and test halves and assumes
an observation in one says nothing about the other. That is false for a held portfolio: a position
opened in a training week is still held through the test weeks that follow, so the two halves share
the SAME trade. The in-sample winner then looks good out-of-sample partly because it is being scored
on the tail of its own training positions. Lopez de Prado's fix is to PURGE -- drop training
observations whose holding window overlaps the test set -- plus an EMBARGO after each test block to
kill the serial correlation that survives the cut.

THE EMBARGO IS FIXED, NOT PER-POSITION, and that is a deliberate approximation. Exact purging needs
each position's own holding span; here `max_event_scans` bounds any event at 12 scans and the cadence
is monthly, so an embargo of EMBARGO_D trading days around every test block is conservative -- it
removes more than strictly required rather than less. Getting this wrong in the other direction is
what makes an unpurged PBO look better than it is.

  UNPURGED PBO IS A FLOOR. PURGED PBO IS THE NUMBER. If a ranker's PBO climbs sharply once purged,
  its apparent skill was position overlap.

Parallel over the 12,870 splits, which are independent. ~10 cores, a few minutes.

    python scripts/pbo_purged.py --sweep data/sweep_v9_daily.json --embargo 21
"""
from __future__ import annotations
import argparse, itertools, json, math, os, sys
from pathlib import Path
import concurrent.futures as cf

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
_S: dict = {}


def _init(R, edges, embargo, whole):
    _S["R"], _S["edges"], _S["emb"], _S["whole"] = R, edges, embargo, whole


def _mask(idx, edges, T):
    m = np.zeros(T, dtype=bool)
    for i in idx:
        m[edges[i]:edges[i + 1]] = True
    return m


def _one(arg):
    """One split: pick by each ranker on the PURGED train days, score on the test days."""
    tr, te = arg
    R, edges, emb = _S["R"], _S["edges"], _S["emb"]
    T = R.shape[1]
    te_m = _mask(te, edges, T)
    tr_m = _mask(tr, edges, T)
    # PURGE + EMBARGO: drop any training day within `emb` of a test day, on either side. np.convolve
    # on the test mask gives exactly that neighbourhood in one pass.
    if emb > 0:
        grow = np.convolve(te_m.astype(np.int32), np.ones(2 * emb + 1, dtype=np.int32), mode="same") > 0
        tr_m &= ~grow
    if tr_m.sum() < 30 or te_m.sum() < 30:
        return None
    def stat(m):
        X = R[:, m]
        mu = X.mean(axis=1)
        sd = np.maximum(X.std(axis=1), 1e-12)
        pos = np.where(X > 0, X, 0).sum(axis=1)
        neg = np.where(X < 0, X, 0).sum(axis=1)
        return mu, sd, pos, neg
    mu_i, sd_i, pos_i, neg_i = stat(tr_m)
    mu_o, sd_o, _, _ = stat(te_m)
    oos = mu_o / sd_o
    N = R.shape[0]
    def rank01(v, hi=True):
        v = np.where(np.isfinite(v), v, -np.inf if hi else np.inf)
        o = np.argsort(v if hi else -v, kind="stable")
        r = np.empty(N); r[o] = np.arange(N) / (N - 1)
        return r
    canc = np.abs(neg_i) / np.maximum(pos_i, 1e-12)
    sel = {"Sharpe (in-sample)": mu_i / sd_i,
           "-cancellation": -canc,
           "robust (canc+downside)": (rank01(-canc) + rank01(neg_i)) / 2,
           "winners (whole-run)": _S["whole"]["winners"],
           "capital_hit (whole-run)": _S["whole"]["capital_hit"]}
    out = {}
    for k, v in sel.items():
        best = int(np.nanargmax(v))
        rank = int((oos < oos[best]).sum())
        w = (rank + 1) / (N + 1)
        out[k] = math.log(w / (1 - w))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep", default="data/sweep_v9_daily.json")
    ap.add_argument("--embargo", type=int, default=21, help="trading days purged either side of test")
    ap.add_argument("--blocks", type=int, default=16)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    a = ap.parse_args(argv)
    S = json.loads((ROOT / a.sweep).read_text())
    cells = [c for c in S["cells"] if c.get("daily_r")]
    if not cells:
        print("  no daily_r in this sweep -- re-run sweep_optimizer.py", file=sys.stderr)
        return 1
    T = min(len(c["daily_r"]) for c in cells)
    R = np.array([c["daily_r"][:T] for c in cells], dtype=np.float32)
    whole = {"winners": np.array([c.get("winners") or 0 for c in cells], dtype=np.float32),
             "capital_hit": np.array([c.get("capital_hit") or 0 for c in cells], dtype=np.float32)}
    NB = a.blocks
    edges = [round(i * T / NB) for i in range(NB + 1)]
    combos = [(tr, [i for i in range(NB) if i not in tr])
              for tr in itertools.combinations(range(NB), NB // 2)]
    print(f"  {R.shape[0]:,} configs · {T} days · {NB} blocks · {len(combos):,} splits · "
          f"embargo {a.embargo}d · {a.workers} workers", flush=True)
    res: dict = {}
    with cf.ProcessPoolExecutor(max_workers=a.workers, initializer=_init,
                                initargs=(R, edges, a.embargo, whole)) as ex:
        for r in ex.map(_one, combos, chunksize=24):
            if r is None:
                continue
            for k, v in r.items():
                res.setdefault(k, []).append(v)
    out = {}
    for k, lam in res.items():
        lam = np.array(lam)
        out[k] = {"pbo": round(float(100 * (lam < 0).mean()), 1),
                  "median_lambda": round(float(np.median(lam)), 3), "n_splits": len(lam)}
        print(f"    {k:<26} PBO {out[k]['pbo']:5.1f}%   median lambda {out[k]['median_lambda']:+.2f}",
              flush=True)
    (ROOT / "data/pbo_purged.json").write_text(json.dumps(
        {"embargo_days": a.embargo, "blocks": NB, "rankers": out}, indent=1))
    print(f"\n  wrote data/pbo_purged.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
