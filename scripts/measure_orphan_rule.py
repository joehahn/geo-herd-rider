#!/usr/bin/env python3
"""measure_orphan_rule.py — does dropping ORPHANED VEHICLES from the watchlist help?

THE RULE. A ticker is orphaned when the catalyst that admitted it is re-read at a scan and no longer
lists it as a vehicle. That is not the same absence the stale clock handles: "the catalyst went quiet"
is a coverage gap and stickiness exists to survive it, while "the catalyst was re-read and dropped
this name" is the agent revoking the thesis. Today both look identical (no scan row) and the name is
carried for max_stale_scans either way.

WHY IT MATTERS. On cbs_v11, 42 of 127 watchlist names (33%) at the final scan sat in no live event's
vehicle list. BDTX -- 31% of the published recommendation -- was named in ONE entry, dropped from its
event at the very next scan, and never mentioned again.

BOOK-PATH, so this is free to measure: _stateful_watch is reached only from backtest(), the journals
are fixed on disk, and no curation is re-run.

JUDGE IT ON MECHANISM (CLAUDE.md #6). What reproduces is how many names the rule removes and whether
they were ever FUNDED. The P&L column is printed last and deliberately without a verdict: one curation
cannot adjudicate, and 14 paired replays of one corpus are not 14 independent samples.

    scripts/measure_orphan_rule.py                 # every run dir that has a scan log and a panel
"""
from __future__ import annotations

import collections
import glob
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import firehose as fh          # noqa: E402
import optimizer               # noqa: E402


def load_scans(run: Path) -> dict:
    s = pd.read_csv(run / "firehose_scans.csv")
    out: dict = collections.defaultdict(list)
    for wk, g in s.groupby("week"):
        ts = pd.Timestamp(str(wk) + " 16:30", tz="America/New_York")
        out[ts] += [{"ticker": str(r.ticker).strip().upper(), "thesis": ("" if pd.isna(r.thesis) else str(r.thesis)),
                     "thesis_live": bool(r.thesis_live),
                     "catalyst_resolved": bool(r.catalyst_resolved), "evidence_urls": []}
                    for r in g.itertuples()
                    if isinstance(r.ticker, str) and str(r.ticker).strip()]
    return dict(sorted(out.items()))


def funded(bt: dict) -> dict:
    """{date: {ticker: weight}} for every rebalance, off the `held` string backtest already writes."""
    out = {}
    for r in bt.get("rows") or []:
        w = {}
        for part in (r.get("held") or "").split(";"):
            if ":" in part:
                t, v = part.rsplit(":", 1)
                try:
                    w[t] = float(v)
                except ValueError:
                    pass
        out[r["date"]] = w
    return out


def main() -> int:
    global K
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"  orphan patience K={K} consecutive scans\n")
    prof = {"cbs": "investor_profile.forward.md"}
    rows = []
    for rd in sorted(glob.glob(str(ROOT / "data" / "cb*"))):
        run = Path(rd)
        # frozen gate caches REQUIRED, not optional: without them backtest() fetches volume and
        # corp actions live (see scripts/freeze_gate_caches.py), which is slow, rate-limited, and
        # would price the two arms of this paired test against different gate data.
        if not all((run / f).exists() for f in
                   ("firehose_scans.csv", "panel.csv", "volume.csv", "corpactions.json")):
            continue
        fm = optimizer.load_financial_model(
            str(ROOT / (prof["cbs"] if run.name.startswith("cbs") else "investor_profile.backtest.md")))
        scans = load_scans(run)
        panel = pd.read_csv(run / "panel.csv", index_col=0, parse_dates=True)
        w_off = fh._stateful_watch(scans, seed=[], fm=fm)
        w_on = fh._stateful_watch(scans, seed=[], fm=fm, drop_orphans=K)
        anchors = sorted(w_off)
        dropped = sum(len(set(w_off[a]) - set(w_on[a])) for a in anchors)
        share = 100 * dropped / max(sum(len(w_off[a]) for a in anchors), 1)
        # did the rule ever remove a name the optimizer was actually FUNDING?
        _orig = fh._stateful_watch
        bt_off = fh.backtest(scans, fm, capital=50000, panel=panel,
                             freeze_panel=str(run / "panel.csv"))
        fh._stateful_watch = lambda *a, **k: _orig(*a, **{**k, "drop_orphans": K})
        try:
            bt_on = fh.backtest(scans, fm, capital=50000, panel=panel,
                                freeze_panel=str(run / "panel.csv"))
        finally:
            fh._stateful_watch = _orig
        f_off, f_on = funded(bt_off), funded(bt_on)
        hit = sum(1 for d, w in f_off.items()
                  for t, v in w.items() if v > 0.01 and t not in (f_on.get(d) or {}))
        rows.append((run.name, len(anchors), dropped, share, hit,
                     bt_off.get("final"), bt_on.get("final")))
        print(f"  {run.name:26} scans {len(anchors):3d}  orphans dropped {dropped:5d} ({share:4.1f}% of "
              f"watch-scans)  funded positions removed {hit:3d}   ${bt_off['final']:>11,.0f} -> "
              f"${bt_on['final']:>11,.0f}", flush=True)
    if not rows:
        print("no runs with both a scan log and a frozen panel"); return 1
    import statistics as st
    print(f"\n  MECHANISM  median orphan share {st.median(r[3] for r in rows):.1f}% of watch-scans; "
          f"funded positions removed in {sum(1 for r in rows if r[4])} of {len(rows)} runs "
          f"({sum(r[4] for r in rows)} in total)")
    rat = [r[6] / r[5] for r in rows if r[5]]
    print(f"  P&L (NOT a verdict -- one corpus, paired replays)  median {st.median(rat):.2f}x, "
          f"better in {sum(1 for x in rat if x > 1)} of {len(rat)}, range {min(rat):.2f}-{max(rat):.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
