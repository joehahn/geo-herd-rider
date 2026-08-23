"""compare_event_timer.py -- did raising max_event_scans stop the timer killing live theses?

PRE-REGISTERED 2026-08-22, BEFORE the arm finished. Three numbers, each with the direction it had
to move written down in advance so the result cannot be rationalised afterwards:

  1. AT-CAP SHARE must FALL. At cap 6, 56.9% of v20's events die pinned at exactly the cap -- the
     span histogram is a wall. The mb runs at cap 12 sit at 14-17%. If the arm stays near 57%, the
     timer was not what was binding and the whole diagnosis is wrong.
  2. TARGET LIVE-WEEKS must RISE above v20's 56. mb1 also ran at cap 12 and FELL to 52, but mb1
     confounds this with max_events=16; with that removed the number has to go the other way.
  3. INFLOW must HOLD near v20's 174 events. If it collapses toward mb1's 96, something other than
     the timer changed and the comparison is void.

BASELINE IS v20_catalystkey, NOT v18. src/agent.py carries the catalyst-keyed retired guard, so v18
would differ in two things at once; v20 is the same code at cap 6.

NOT REPORTED: P&L. CLAUDE.md #6 -- two runs of identical settings gave median finals of $117,200 and
$62,997, so any difference under ~2x here is unmeasurable and must not adjudicate the change.
"""
from __future__ import annotations
import argparse, collections, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARG = ["BE", "INTC", "WPM", "GNENF", "BOIL", "BKSY", "CORZ", "NVTS", "IREN", "AMD", "CCJ", "SOXQ", "PSX"]


def analyse(run: str):
    J = json.loads((ROOT / "data" / run / "journal.json").read_text())
    ev = []
    for s in ("events", "retired"):
        for v in J.get(s, {}).values():
            ev.extend(v if isinstance(v, list) else [v])
    spans, live = [], collections.defaultdict(set)
    for e in ev:
        if not isinstance(e, dict) or not e.get("entries"):
            continue
        lw = [x for x in e["entries"] if x.get("thesis_live")]
        if lw:
            spans.append(len(lw))
        for x in lw:
            for t in (x.get("vehicles") or e.get("vehicles") or []):
                live[t].add(x["date"])
    kinds = collections.Counter()
    for v in J.get("retired", {}).values():
        s = str(v[0] if isinstance(v, (list, tuple)) else v)
        kinds["timer" if "aged out" in s else ("cause" if "resolved" in s else "other")] += 1
    return spans, live, kinds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="cbt_3yr_v20_catalystkey")
    ap.add_argument("--new", default="cbt_3yr_v21_evscans12")
    ap.add_argument("--base-cap", type=int, default=6)
    ap.add_argument("--new-cap", type=int, default=12)
    a = ap.parse_args()
    (sb, lb, kb), (sn, ln, kn) = analyse(a.base), analyse(a.new)

    cb = sum(1 for x in sb if x >= a.base_cap); cn = sum(1 for x in sn if x >= a.new_cap)
    print(f"BASE {a.base} (cap {a.base_cap})   NEW {a.new} (cap {a.new_cap})\n")
    print("1. AT-CAP SHARE   (must FALL; mb runs at cap 12 sit at 14-17%)")
    print(f"   base {cb:4}/{len(sb):4} = {100*cb/max(len(sb),1):5.1f}%"
          f"      new {cn:4}/{len(sn):4} = {100*cn/max(len(sn),1):5.1f}%")
    tb = sum(kb.values()) or 1; tn = sum(kn.values()) or 1
    print(f"   died on TIMER   base {100*kb['timer']/tb:4.1f}%   new {100*kn['timer']/tn:4.1f}%")

    b2 = sum(len(lb.get(t, ())) for t in TARG); n2 = sum(len(ln.get(t, ())) for t in TARG)
    print(f"\n2. TARGET LIVE-WEEKS   (must RISE above {b2})")
    print(f"   {'tk':8}{'base':>6}{'new':>6}{'delta':>7}")
    for t in TARG:
        x, y = len(lb.get(t, ())), len(ln.get(t, ()))
        print(f"   {t:8}{x:>6}{y:>6}{y-x:>+7}")
    print(f"   {'TOTAL':8}{b2:>6}{n2:>6}{n2-b2:>+7}")

    print(f"\n3. INFLOW   (must HOLD near {len(sb)}; mb1's confounded run collapsed to 96)")
    print(f"   events with a live week   base {len(sb):4}   new {len(sn):4}")
    print(f"   median live span          base {sorted(sb)[len(sb)//2]:4}   new {sorted(sn)[len(sn)//2]:4}")

    ok1, ok2, ok3 = (100*cn/max(len(sn),1)) < 30, n2 > b2, len(sn) > 0.75 * len(sb)
    print("\nVERDICT (pre-registered thresholds)")
    for lbl, ok in (("1 at-cap share fell below 30%", ok1),
                    ("2 target live-weeks rose", ok2),
                    ("3 inflow held above 75% of base", ok3)):
        print(f"   {'PASS' if ok else 'FAIL'}  {lbl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
