#!/usr/bin/env python3
"""Measure the OCCURRENCE gate on `pending_next`, offline, over already-curated journals.

WHY. A `pending_next` naming a price move, a trend, or another analyst's opinion -- "the
realization of the 120% upside", "sustained homebuilder demand", "Canaccord revises IREN rating
again" -- is not an event. Its exit condition can never fire, so nothing can retire it on the
merits. This prototypes rejecting those at the gate in `agent.py` that already drops an empty or
`_NOTHING_PENDING` value, and measures the consequence WITHOUT spending a curation -- the
measure-before-shipping discipline the exposure-length gate earned the hard way (it read well and
killed `BTC -- leading cryptocurrency`).

FAILS OPEN, like `_restates_resolved`: reject ONLY when a non-occurrence head noun is present and
no occurrence noun is. `max_group_articles` and `max_article_orgs` are the standing warning --
both were subtractive filters added to fix something else, and both deleted real news.

    python scripts/measure_occurrence_gate.py [--show <label>]

Reports, per journal: what the gate rejects, and -- the part that decides whether it is worth a
re-curation -- whether the rejected events behave any worse than the admitted ones.
"""
import argparse
import collections
import json
import pathlib
import re
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from agent import _NOTHING_PENDING, names_occurrence   # noqa: E402  -- ONE implementation,
                                                      # landed in agent.py 2026-09-06

RUNS = (("cbt v27", "data/cbt_3yr_v27_catalyst"),
        ("cbt v28", "data/cbt_3yr_v28_exposure"),
        ("cbs v12", "data/cbs_v12"))



# ---- unit cases: the shapes this must and must not kill --------------------------------------
CASES = [
    # themes, price moves, opinions -> REJECT
    ("the realization of the copper miner's 120% upside potential", False),
    ("sustained homebuilder demand amid higher mortgage rates", False),
    ("Canaccord revises IREN rating or price target again", False),
    ("a sustained rise in gold and silver prices", False),
    ("sustained growth in natural gas demand from AI operations", False),
    ("the realization of PIMCO's predicted wave of distress", False),
    ("continued momentum in datacenter buildout", False),
    ("US housing demand continues to grow", False),
    ("customer adoption and revenue growth", False),
    ("B. Riley revises price target on TTM Technologies", False),
    # real occurrences -> ADMIT, including ones carrying a blacklisted word or a modifier
    ("completion of Globalstar's satellite upgrades", True),
    ("Samsung's Q2 2024 earnings report", True),
    ("the DOE decision on the $1B Three Mile Island restart loan", True),
    ("FDA approval of Gedatolisib", True),
    ("the Fed's September rate decision", True),
    ("actual rate cuts by US Fed", True),
    ("the merger closing", True),
    ("first power delivered from the restarted reactor", True),
    ("OPEC+ meeting on production quotas", True),
    ("Phase 3 trial readout for the lead candidate", True),
    ("the DOJ ruling on the acquisition", True),
    ("release of further clinical data", True),
    ("further US export restrictions on chipmaking tools", True),
    ("the Iran war ends or escalates further", True),
    ("partnership achieves $15M profit target in 2026", True),
    ("the implementation of Medicare's Local Coverage Determination", True),
    ("electronica 2024 showcases GaN and SiC technologies", True),
    ("commercial uptake and sales milestones", True),
    ("further Mpox outbreak response or vaccine demand", True),
]


def _pendings(run):
    j = pathlib.Path(run, "journal.json")
    if not j.exists():
        return []
    ev = json.loads(j.read_text())["events"]
    out = [(k, e, str(e.get("pending_next") or "").strip()) for k, e in ev.items()]
    return [r for r in out if r[2] and r[2].lower() not in _NOTHING_PENDING]


def _live(e):
    return [x for x in (e.get("entries") or []) if x.get("thesis_live")]


def _behaviour(sub, lab):
    """Do these events behave any worse? Mechanism, not P&L (CLAUDE.md #6)."""
    n = len(sub)
    if not n:
        return
    ever = [(k, e) for k, e, _ in sub if _live(e)]
    surv = [(k, e) for k, e in ever if len(e.get("entries") or []) >= 2]
    aged = sum(1 for _, e in surv if e["entries"][-1].get("thesis_live"))
    ls = [len(_live(e)) for _, e in ever] or [0]
    print(f"    {lab:<14} n={n:3d} · ever went live {len(ever):3d} ({100*len(ever)/n:2.0f}%)"
          f" · median live scans {st.median(ls):.0f}"
          f" · ended still-live {aged:3d} ({100*aged/max(len(surv), 1):2.0f}% of survivors)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", default="", help="print every rejection for this label, e.g. 'cbt v28'")
    a = ap.parse_args()

    wrong = [(t, w) for t, w in CASES if names_occurrence(t) != w]
    print(f"  unit cases: {len(CASES) - len(wrong)}/{len(CASES)} pass")
    for t, w in wrong:
        print(f"      WRONG (want admit={w}) {t!r}")

    for lab, run in RUNS:
        rows = _pendings(run)
        if not rows:
            continue
        rej = [r for r in rows if not names_occurrence(r[2])]
        adm = [r for r in rows if names_occurrence(r[2])]
        print(f"\n  {lab}: {len(rows)} events with a pending_next · "
              f"gate rejects {len(rej)} ({100*len(rej)/len(rows):.1f}%)")
        _behaviour(rej, "REJECTS")
        _behaviour(adm, "ADMITS")
        vr = sum(len(e.get("vehicles") or []) for _, e, _ in rej if _live(e))
        va = sum(len(e.get("vehicles") or []) for _, e, _ in adm if _live(e))
        print(f"    vehicle slots on live events: {vr} of {vr + va} "
              f"({100 * vr / max(vr + va, 1):.1f}%) would be gated")
        if lab == a.show:
            for k, _, t in rej:
                print(f"        {k:>7}  {t[:88]}")

        # HOW EVENTS ACTUALLY END. The gate above is about admission; this is about retirement,
        # and it is the larger defect: an event that ends STILL LIVE was retired by a timer
        # (`max_stale_scans`, `max_event_scans`), not by its thesis decaying.
        ev = json.loads(pathlib.Path(run, "journal.json").read_text())["events"]
        last = max(x["date"] for e in ev.values() for x in (e.get("entries") or []))
        surv = [e for e in ev.values()
                if len(e.get("entries") or []) >= 2 and _live(e)]
        openend = [e for e in surv
                   if e["entries"][-1].get("thesis_live") and e["entries"][-1]["date"] == last]
        aged = [e for e in surv
                if e["entries"][-1].get("thesis_live") and e["entries"][-1]["date"] != last]
        print(f"    retirement of live events: {len(surv)} lived >1 scan · "
              f"decayed {len(surv) - len(aged) - len(openend)} · "
              f"AGED OUT ON A TIMER {len(aged)} ({100 * len(aged) / max(len(surv), 1):.0f}%) · "
              f"open at final scan {len(openend)}")
        print(f"      aged out at scan count: "
              f"{sorted(collections.Counter(len(e['entries']) for e in aged).items())}")


if __name__ == "__main__":
    main()
