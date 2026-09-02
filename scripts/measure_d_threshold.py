#!/usr/bin/env python3
"""measure_d_threshold.py — is "sit out when the slate is cold (or blazing)" worth trading?

THE RULE. At each curation, deploy only when the slate's mean trailing d falls in [lo, hi); otherwise
hold cash for that month. CBT panel 3 shows why anyone would want it: on the canonical run d<0 gives
-0.4%/month at a 36% win rate and d>=+7% gives +1.4% at 29%, while the middle gives ~+10% at 80%+.

THE ARITHMETIC IS EXACT, not an approximation. The book rebalances from scratch at every anchor, so
skipping a period really is multiplying by 1.0 -- no path dependence to model.

THE TRAP THIS EXISTS TO AVOID. [0, +7) was READ OFF the canonical run's 35 periods. Scoring it on a
set containing that run is in-sample, and two free parameters fit to 35 points will always find
something. So the headline number here is LEAVE-ONE-CURATION-OUT: pick (lo, hi) on 16 curations by
their pooled per-period geometric mean, score the winner on the 17th, rotate. The in-sample figure is
printed alongside precisely so the gap between them is visible.

    scripts/measure_d_threshold.py
"""
from __future__ import annotations

import collections
import glob
import statistics as st
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import firehose as fh          # noqa: E402
import optimizer               # noqa: E402

LOS = [-99, -4, -2, 0, 1, 2, 3]
HIS = [4, 5, 6, 7, 8, 10, 99]


def pairs_for(run: Path) -> list[tuple[float, float]]:
    fm = optimizer.load_financial_model(str(ROOT / (
        "investor_profile.forward.md" if run.name.startswith("cbs") else "investor_profile.backtest.md")))
    lb = int(fm.get("optimizer_lookback_days") or 21)
    s = pd.read_csv(run / "firehose_scans.csv")
    sc: dict = collections.defaultdict(list)
    for wk, g in s.groupby("week"):
        ts = pd.Timestamp(str(wk) + " 16:30", tz="America/New_York")
        sc[ts] += [{"ticker": str(r.ticker).strip().upper(),
                    "thesis": ("" if pd.isna(r.thesis) else str(r.thesis)),
                    "thesis_live": bool(r.thesis_live),
                    "catalyst_resolved": bool(r.catalyst_resolved), "evidence_urls": []}
                   for r in g.itertuples() if isinstance(r.ticker, str) and str(r.ticker).strip()]
    sc = dict(sorted(sc.items()))
    panel = pd.read_csv(run / "panel.csv", index_col=0, parse_dates=True)
    if panel.index.tz is not None:
        panel.index = panel.index.tz_localize(None)
    w = fh._stateful_watch(sc, seed=[], fm=fm)
    bt = fh.backtest(sc, fm, capital=50000, daily=True, panel=panel,
                     freeze_panel=str(run / "panel.csv"))
    dd = (bt.get("daily") or {}).get("dates") or []
    vv = [float(x) for x in (bt.get("daily") or {}).get("value") or []]
    if not dd:
        return []

    def at(day):
        for k in range(len(dd) - 1, -1, -1):
            if dd[k] <= day:
                return vv[k]
        return vv[0]

    def px(t, day):
        if t not in panel.columns:
            return None
        i = panel.index.searchsorted(pd.Timestamp(day), side="right") - 1
        if i < 0:
            return None
        v = panel[t].iloc[i]
        return None if pd.isna(v) else float(v)

    anch = sorted(w)
    out = []
    for k in range(len(anch) - 1):
        a0, a1 = anch[k], anch[k + 1]
        s0, s1 = str(a0.date()), str(a1.date())
        back = str((a0 - pd.Timedelta(days=lb)).date())
        ds = []
        for t in w[a0]:
            pn, pt = px(t, s0), px(t, back)
            if pn and pt and pt > 0:
                ds.append(pn / pt - 1)
        b0, b1 = at(s0), at(s1)
        if len(ds) >= 5 and b0:
            out.append((100 * float(np.mean(ds)), b1 / b0 - 1))
    return out


def geo(rs: list[float]) -> float:
    """Per-period geometric mean of a set of periods, in percent. Comparable across set sizes."""
    return 100 * (float(np.prod([1 + r for r in rs])) ** (1.0 / len(rs)) - 1) if rs else 0.0


def score(sets: list[list[tuple[float, float]]], lo: float, hi: float) -> float:
    """Pooled per-period geometric mean of the RULE: kept periods earn r, skipped earn 0."""
    rs = [(r if lo <= d < hi else 0.0) for s in sets for d, r in s]
    return geo(rs)


def main() -> int:
    data = {}
    for rd in sorted(glob.glob(str(ROOT / "data" / "cb*"))):
        run = Path(rd)
        if not all((run / f).exists() for f in
                   ("firehose_scans.csv", "panel.csv", "volume.csv", "corpactions.json")):
            continue
        pr = pairs_for(run)
        if len(pr) >= 4:
            data[run.name] = pr
    names = list(data)
    print(f"{len(names)} curations, {sum(len(v) for v in data.values())} periods\n")

    base = score(list(data.values()), -1e9, 1e9)
    best, bl, bh = -1e9, None, None
    for lo in LOS:
        for hi in HIS:
            if hi <= lo:
                continue
            v = score(list(data.values()), lo, hi)
            if v > best:
                best, bl, bh = v, lo, hi
    print(f"  always deploy            : {base:+.3f}%/period")
    print(f"  IN-SAMPLE best [{bl:+.0f},{bh:+.0f})   : {best:+.3f}%/period   "
          f"(this is the number NOT to believe)")
    print(f"  the eyeballed [0,+7)     : {score(list(data.values()), 0, 7):+.3f}%/period")

    print("\n  LEAVE-ONE-CURATION-OUT: choose (lo,hi) on the other 16, score on the held-out one")
    won, outs, picks = 0, [], collections.Counter()
    for h in names:
        tr = [data[k] for k in names if k != h]
        b, l2, h2 = -1e9, None, None
        for lo in LOS:
            for hi in HIS:
                if hi <= lo:
                    continue
                v = score(tr, lo, hi)
                if v > b:
                    b, l2, h2 = v, lo, hi
        picks[(l2, h2)] += 1
        rule = score([data[h]], l2, h2)
        alw = score([data[h]], -1e9, 1e9)
        outs.append(rule - alw)
        won += rule > alw
        print(f"    {h:24} picked [{l2:+.0f},{h2:+.0f})  rule {rule:+7.2f}%  vs always {alw:+7.2f}%  "
              f"{'WIN ' if rule > alw else 'lose'} {rule - alw:+6.2f}")
    print(f"\n  OUT-OF-SAMPLE: rule beats always-deploy in {won} of {len(names)} curations, "
          f"median edge {st.median(outs):+.2f}%/period")
    print(f"  thresholds chosen: {dict(picks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
