"""compare_retired_guard.py -- judge the catalyst-keyed retired guard on MECHANISM, not P&L.

CLAUDE.md #6 is the whole reason this file exists. Two runs of the SAME settings gave median finals
of $117,200 and $62,997, and one cell swung $588,538 -> $75,132 on LLM sampling alone. So a P&L
difference under ~2x between these two curations is UNMEASURABLE and must not be used to adjudicate
the change. What reproduces is mechanism: who got proposed, who got killed, who reached the judge.

THE PRE-REGISTERED NUMBERS, fixed BEFORE the new curation finished:

  1. SCOUT RECALL on gap weeks carrying a dated event. The defect: a retired ticker could not be
     re-proposed even when the press named a new dated catalyst. Baseline on v18 --
     BE 0%, CORZ 8%, NVTS 20%, BKSY 0%, GNENF 0%. This should RISE.
  2. BANNED-PROPOSAL KILL RATE. On v18 a proposal that leaked past the ban was killed by the event
     agent 70% of the time, vs 5% for un-banned proposals. That separation is the roster's signal
     and the change must PRESERVE it. If it collapses toward 5%, the gate stopped discriminating
     and we merely loosened the ban -- a FAILURE even if returns rise.
  3. INFLOW. Proposals and distinct tickers. A large jump means the gate is not gating.

Reported side by side; the verdict lines say which way each number had to move.
"""
from __future__ import annotations
import argparse, collections, csv, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEM = 8

# exit -> peak windows, and the corpus keys that name the company (see TODO.md 2026-08-22)
GAPS = {"BE":    ("2026-02-26", "2026-06-22", ["bloom energy"]),
        "CORZ":  ("2024-06-06", "2026-06-18", ["core scientific"]),
        "NVTS":  ("2025-07-01", "2026-05-26", ["navitas"]),
        "BKSY":  ("2025-07-01", "2026-05-28", ["blacksky"]),
        "GNENF": ("2026-03-28", "2026-05-06", ["greenland", "tanbreez"])}


def load(run: Path):
    rows = list(csv.DictReader((run / "firehose_scans.csv").open()))
    weeks = sorted({r["week"][:10] for r in rows})
    idx = {w: i for i, w in enumerate(weeks)}
    props, restated = collections.defaultdict(set), 0
    for l in (run / "decisions.jsonl").open():
        d = json.loads(l)
        if d.get("kind") != "scout":
            continue
        i = idx.get(d["context"][:10])
        if i is None:
            continue
        for p in d.get("proposed") or []:
            props[i].add(p["ticker"])
        restated += len(d.get("restated_resolved") or [])
    live = {(r["ticker"], idx[r["week"][:10]]): str(r.get("thesis_live")) == "True" for r in rows}
    banned_from = collections.defaultdict(list)
    for r in rows:
        if str(r.get("catalyst_resolved")) == "True":
            banned_from[r["ticker"]].append(idx[r["week"][:10]])
    roster = collections.defaultdict(set)
    for t, ws in banned_from.items():
        for w0 in ws:
            for i in range(w0, min(w0 + MEM, len(weeks))):
                roster[i].add(t)
    return dict(weeks=weeks, idx=idx, props=props, live=live, roster=roster, restated=restated)


def recall(st, arts):
    out = {}
    for t, (a, b, keys) in GAPS.items():
        gw = [w for w in st["weeks"] if a < w <= b]
        with_press = set()
        for x in arts:
            d = str(x.get("published_date") or "")[:10]
            if not (a <= d <= b):
                continue
            blob = ((x.get("title") or "") + " " + str(x.get("text") or x.get("lede") or ""))[:3000].lower()
            if any(k in blob for k in keys):
                nxt = [w for w in gw if w >= d]
                if nxt:
                    with_press.add(nxt[0])
        got = {w for w in with_press if t in st["props"].get(st["idx"][w], set())}
        out[t] = (len(got), len(with_press))
    return out


def killrate(st):
    L, C = collections.Counter(), collections.Counter()
    for i in range(len(st["weeks"])):
        for t in st["props"].get(i, set()):
            o = next((st["live"][(t, j)] for j in (i, i + 1) if (t, j) in st["live"]), None)
            if o is None:
                continue
            (L if t in st["roster"].get(i, set()) else C)[o] += 1
    def pct(c):
        n = sum(c.values())
        return (100 * c[False] / n if n else float("nan")), n
    return pct(L), pct(C)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data/cbt_3yr_v18")
    ap.add_argument("--new", default="data/cbt_3yr_v20_catalystkey")
    ap.add_argument("--corpus", default="data/backtest_3yr_v5")
    a = ap.parse_args()
    pool = json.loads((ROOT / a.corpus / "pool.json").read_text())
    arts = pool.get("articles", pool)
    B, N = load(ROOT / a.base), load(ROOT / a.new)

    print(f"BASE {a.base}   NEW {a.new}\n")
    print("1. SCOUT RECALL on gap weeks that carried press coverage   (must RISE)")
    rb, rn = recall(B, arts), recall(N, arts)
    tb = tn = wb = 0
    for t in GAPS:
        (gb, nb), (gn, nn) = rb[t], rn[t]
        tb += gb; tn += gn; wb += nb
        print(f"   {t:7} base {gb:2}/{nb:2} ({100*gb/max(nb,1):3.0f}%)   new {gn:2}/{nn:2} ({100*gn/max(nn,1):3.0f}%)")
    print(f"   {'TOTAL':7} base {tb:2}/{wb:2} ({100*tb/max(wb,1):3.0f}%)   new {tn:2}/{wb:2} ({100*tn/max(wb,1):3.0f}%)")

    print("\n2. KILL-RATE SEPARATION -- the roster's signal   (must be PRESERVED)")
    for lbl, st in (("base", B), ("new", N)):
        (kl, nl), (kc, nc) = killrate(st)
        print(f"   {lbl:5} banned-proposal kill {kl:4.0f}% (n={nl:3})   un-banned kill {kc:4.0f}% (n={nc:3})"
              f"   separation {kl-kc:+4.0f}pp")

    print("\n3. INFLOW   (a large jump means the gate is not gating)")
    for lbl, st in (("base", B), ("new", N)):
        tot = sum(len(v) for v in st["props"].values())
        dis = len({t for v in st["props"].values() for t in v})
        print(f"   {lbl:5} proposals {tot:5}   distinct tickers {dis:4}   restatement-drops {st['restated']:4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
