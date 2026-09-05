#!/usr/bin/env python3
"""measure_dead_thesis.py — how long does the book hold a position whose thesis is OVER?

NOT THE ORPHAN RULE. scripts/measure_orphan_rule.py tests a ticker its own LIVE event stopped
listing, and measured that as coverage noise (median 0.93x, better in 4 of 17). This asks a
different question: the event itself is RETIRED — exited, aged out at max_event_scans, or its last
verdict said the thesis is dead — and the book is still funding one of its vehicles.

WHY IT IS A DIFFERENT QUESTION. A retired event stops producing scan rows entirely, so its vehicles
do not look "dropped", they look SILENT, and silence is what stickiness is built to survive. The
name is then carried for max_stale_scans, which at the profile's 8 and a monthly cadence is eight
MONTHS after the thesis ended. Measured across the published curation reports, 34 of 42 scans hold
at least one such position, and at CBT's final anchor all three funded names are in that state:
DJT 30.8% on ev148 (exited), ETHA 17.8% on ev144 (aged out), LMT 40.0% on ev197 (exited).

BOOK-PATH, so this is free: max_stale_scans is read only by firehose._watch_clocks, reached only
from backtest(). The journals are fixed on disk and no curation is re-run.

JUDGE IT ON MECHANISM (CLAUDE.md #6). The reproducible number is the share of FUNDED position-scans
resting on a dead thesis, and how it moves with the clock. The P&L column prints last and without a
verdict: paired replays of one corpus are not independent samples.

    scripts/measure_dead_thesis.py            # sweeps max_stale_scans over every replayable run
"""
from __future__ import annotations

import glob
import json
import statistics as st
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import firehose as fh                                    # noqa: E402
import optimizer                                         # noqa: E402
from measure_orphan_rule import funded, load_scans       # noqa: E402  one loader, not two

CLOCKS = (8, 4, 2, 1)          # max_stale_scans: the profile's value first, then shorter
ARMS = ("clock only", "drop when the thesis retires")


def live_vehicles(run: Path) -> dict:
    """{anchor_date: {tickers whose thesis is LIVE as of that date}}, from the journal.

    The same rule the curation reports use: an event is live at an anchor when its last entry on or
    before it says thesis_live AND it has not been retired by then. A ticker is on a live thesis if
    any such event lists it. Everything else the book funds is resting on a thesis that is over.
    """
    j = json.loads((run / "journal.json").read_text())
    ev = j.get("events") or {}
    out: dict = {}
    dates = sorted({str(x.get("date", ""))[:10]
                    for e in ev.values() for x in (e.get("entries") or [])})
    for d in dates:
        alive: set = set()
        for e in ev.values():
            ents = [x for x in (e.get("entries") or []) if str(x.get("date", ""))[:10] <= d]
            if not ents or not ents[-1].get("thesis_live", True):
                continue
            # retired on or before this anchor? the last entry is the retirement date
            if e.get("status") != "live" and str(ents[-1].get("date", ""))[:10] < d \
                    and len(ents) < len(e.get("entries") or []):
                continue
            if e.get("status") != "live" and len(ents) == len(e.get("entries") or []) \
                    and str(ents[-1].get("date", ""))[:10] < d:
                continue
            alive |= {str(v).upper() for v in (e.get("vehicles") or [])}
        out[d] = alive
    return out


def main() -> int:
    global DROP
    DROP = "--drop" in sys.argv          # ON: also drop a held name once its thesis is retired
    print(f"  arm: {ARMS[1] if DROP else ARMS[0]}\n")
    prof = {"cbs": "investor_profile.forward.md"}
    per_clock: dict = {c: [] for c in CLOCKS}
    for rd in sorted(glob.glob(str(ROOT / "data" / "cb*"))):
        run = Path(rd)
        if not all((run / f).exists() for f in ("firehose_scans.csv", "panel.csv", "volume.csv",
                                                "corpactions.json", "journal.json")):
            continue
        fm0 = optimizer.load_financial_model(
            str(ROOT / (prof["cbs"] if run.name.startswith("cbs") else "investor_profile.backtest.md")))
        scans = load_scans(run)
        panel = pd.read_csv(run / "panel.csv", index_col=0, parse_dates=True)
        alive = live_vehicles(run)
        print(f"\n  {run.name}")
        for c in CLOCKS:
            fm = {**fm0, "max_stale_scans": c}
            bt = fh.backtest(scans, fm, capital=50000, panel=panel,
                             freeze_panel=str(run / "panel.csv"),
                             live_vehicles=(alive if DROP else None))
            f = funded(bt)
            # THE BOOK IS PRICED DAILY, the journal is written at ANCHORS, so an exact date join
            # matches nothing (it returned 0/0 on the first run of this script). A position held on
            # day d rests on the thesis state as of the last scan on or before d, which is the join
            # that means something anyway.
            import bisect
            _ad = sorted(alive)
            tot = dead = 0
            for d, w in f.items():
                i = bisect.bisect_right(_ad, str(d)[:10]) - 1
                if i < 0:
                    continue
                a = alive[_ad[i]]
                for t, v in w.items():
                    if v <= 0.01:
                        continue
                    tot += 1
                    dead += 0 if t in a else 1
            share = 100 * dead / max(tot, 1)
            per_clock[c].append((run.name, share, bt.get("final")))
            print(f"    max_stale_scans {c:>2}  funded position-scans on a DEAD thesis "
                  f"{dead:5d}/{tot:5d} ({share:4.1f}%)   ${bt.get('final', 0):>11,.0f}", flush=True)
    if not any(per_clock.values()):
        print("no runs with a scan log, a frozen panel and a journal")
        return 1
    print("\n  MECHANISM (median across runs)")
    base = {n: (s, v) for n, s, v in per_clock[CLOCKS[0]]}
    for c in CLOCKS:
        rows = per_clock[c]
        rat = [v / base[n][1] for n, _, v in rows if base.get(n) and base[n][1]]
        print(f"    max_stale_scans {c:>2}  dead-thesis share {st.median(r[1] for r in rows):5.1f}%"
              + ("" if c == CLOCKS[0] else
                 f"   ·   P&L vs the profile's {CLOCKS[0]} (NOT a verdict): median "
                 f"{st.median(rat):.2f}x, better in {sum(1 for x in rat if x > 1)} of {len(rat)}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
