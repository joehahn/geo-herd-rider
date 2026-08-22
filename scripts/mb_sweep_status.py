#!/usr/bin/env python3
"""mb_sweep_status.py — progress, spend and HEALTH of the min_bundle_articles sweep.

Reports minutes remaining and projected $ from measured rate, not from the estimate, and spot-checks
each arm for the ways a long unattended curation fails QUIETLY: a stalled log, a swallowed traceback,
the grouping self-check firing, a scout that stops proposing, the demote counter behaving wrongly for
the arm's floor. A log whose last line is a plausible progress update reads exactly like a job that
is still working.
"""
import csv, datetime, json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
LOG = Path("/tmp/min_bundle_sweep.log")
ARMS = [1, 2, 3]
WEEKS = 37


def main() -> int:
    txt = LOG.read_text(errors="replace") if LOG.exists() else ""
    now = datetime.datetime.now(datetime.timezone.utc)

    # ---- progress -------------------------------------------------------------------------------
    starts = {int(m.group(1)): datetime.datetime.fromisoformat(m.group(2).replace("Z", "+00:00"))
              for m in re.finditer(r"=== mb(\d) START (\S+) ===", txt)}
    dones = {int(m.group(1)): (m.group(2), datetime.datetime.fromisoformat(m.group(3).replace("Z", "+00:00")))
             for m in re.finditer(r"=== mb(\d) DONE rc=(\d+) (\S+) ===", txt)}
    done_weeks = 0
    print("ARM PROGRESS")
    for n in ARMS:
        d = ROOT / f"data/cbt_3yr_mb{n}"
        w = len(list((d / "archive").glob("*.json"))) if (d / "archive").exists() else 0
        done_weeks += w
        if n in dones:
            rc, t = dones[n]
            st = f"DONE rc={rc}" + ("" if rc == "0" else "   <-- NONZERO EXIT")
        elif n in starts:
            st = f"running {(now - starts[n]).total_seconds()/60:.0f} min"
        else:
            st = "queued"
        nid = 0
        jf = d / "journal.json"
        if jf.exists():
            try: nid = json.loads(jf.read_text()).get("nid", 0)
            except Exception: pass
        print(f"  mb{n}  {w:2}/{WEEKS} weeks   {nid:4} events   {st}")

    total = WEEKS * len(ARMS)
    t0 = min(starts.values()) if starts else now
    elapsed = (now - t0).total_seconds() / 60
    rate = elapsed / done_weeks if done_weeks else 0
    remain = (total - done_weeks) * rate
    print(f"\n  {done_weeks}/{total} weeks · {elapsed:.0f} min elapsed · "
          f"{rate:.2f} min/week · ~{remain:.0f} MIN REMAINING "
          f"(ETA {(now + datetime.timedelta(minutes=remain)):%H:%M} UTC)")

    # ---- spend, measured from the ledger --------------------------------------------------------
    led = ROOT / "data/llm_costs.csv"
    spent = 0.0
    if led.exists() and starts:
        cut = min(starts.values()).replace(tzinfo=None)
        for r in csv.DictReader(led.open()):
            try:
                if datetime.datetime.fromisoformat(r["ts"]).replace(tzinfo=None) >= cut:
                    spent += float(r.get("cost_usd") or 0)
            except Exception:
                pass
    proj = spent / done_weeks * total if done_weeks else 0
    print(f"  ${spent:.2f} spent · ${proj:.2f} PROJECTED TOTAL "
          f"(${proj/len(ARMS):.2f}/arm; pre-run estimate was $6.42/arm, $19.26 total)")

    # ---- health: the quiet failures -------------------------------------------------------------
    print("\nHEALTH")
    bad = 0
    if LOG.exists():
        age = (now.timestamp() - LOG.stat().st_mtime) / 60
        flag = "  <-- STALLED?" if age > 10 and len(dones) < len(ARMS) else ""
        print(f"  log last written {age:.1f} min ago{flag}")
        bad += bool(flag)
    checks = [
        ("grouping self-check fired (articles LOST)", r"scout grouping DROPPED"),
        ("traceback",                                  r"Traceback \(most recent call last\)"),
        ("scout returned nothing all window",          r"scout: 0 raw -> 0 unique"),
        ("beat bundling unavailable",                  r"beat bundling unavailable"),
        ("resume message (unexpected restart)",        r"RESUME: \d+ weeks done"),
    ]
    for label, pat in checks:
        n = len(re.findall(pat, txt))
        mark = "  <-- LOOK" if n and "nothing all window" not in label else ""
        if mark: bad += 1
        print(f"  {label:44} {n:4}{mark}")

    # demote counter must be 0 for mb1 (floor 1 demotes nothing) and >0 for mb2/mb3
    print("\n  demote counter per arm (mb1 MUST be 0; mb2/mb3 must be > 0):")
    for n in ARMS:
        seg = txt.split(f"=== mb{n} START")[-1] if f"=== mb{n} START" in txt else ""
        seg = seg.split("=== mb")[0] if n < 3 and "=== mb" in seg[10:] else seg
        tot = sum(int(x) for x in re.findall(r"scout: (\d+) company bundle\(s\) under", seg))
        ok = (tot == 0) if n == 1 else (tot > 0 or f"=== mb{n} START" not in txt)
        print(f"    mb{n}: {tot:6,} bundles demoted   {'ok' if ok else '<-- WRONG FOR THIS FLOOR'}")
        bad += (not ok)
    print(f"\n{'NO ISSUES DETECTED' if not bad else f'{bad} THING(S) TO LOOK AT'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
