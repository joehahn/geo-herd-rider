"""Leave-one-curation-out validation of the region that SBT panel 8 publishes.

Choose on 14 curations, score the choice on the held-out 15th, repeat 15 times. One harness answers
three questions so the numbers stay mutually consistent: which METRIC the region should be built on,
whether a region beats picking individual cells, and how WIDE the selection should be.

Measured 2026-08-19 on 15 curations x 6,300 cells -- out-of-sample percentile of the held-out grid:
    metric      final 74.3 | sharpe 77.0 | cancelled 73.1 | gain_pain 69.8 | ann 66.7 | maxDD 59.0
    width       top-1 62.3 (worst fold 1.0) | top-10 78.6 | top-50 80.0 | top-500 77.2
Panel 8's caption quotes these; re-run if the curation set changes.

Run:  .venv/bin/python scripts/loo_region.py        (~10 min, reads every data/sweep_*.json)
"""
# One harness, three questions, so the numbers on the page are mutually consistent:
#  (a) which METRIC should the region be built on, (b) region vs picking cells,
#  (c) how WIDE should the selection be.  Always: choose on 14 curations, score on the 15th.
import json, glob, statistics as st, math
from pathlib import Path
KEYS = ["max_watchlist","concentration_cap","lookback_period_days",
        "drop_unfunded_weeks","risk_aversion","min_trade_size"]
raw = {}
for sf in sorted(glob.glob("data/sweep_*.json")):
    if any(x in sf for x in ("max_events","check")): continue
    d = json.loads(Path(sf).read_text())
    if list(d.get("grid") or {}) != KEYS: continue
    cs = [c for c in d["cells"] if c.get("cancelled") is not None]
    if len(cs) >= 3000: raw[Path(sf).stem.replace("sweep_","")] = cs
tags = list(raw)
grid = {i: sorted({c[k] for c in raw[tags[0]]}) for i,k in enumerate(KEYS)}
fin = {t: {tuple(c[k] for k in KEYS): c["final"] for c in raw[t]} for t in tags}
MET = {"final": (1,"final"), "cancelled": (-1,"cancelled"),
       "ann": (1,"ann"), "sharpe": (1,"sharpe"), "gain_pain": (1,"gain_pain"),
       "max_drawdown": (-1,"max_drawdown")}
mv = {m: {t: {tuple(c[k] for k in KEYS): c[m] for c in raw[t] if c.get(m) is not None}
          for t in tags} for m in MET}

def pct(vals, x): return 100*sum(1 for v in vals if v < x)/len(vals)

def region(train, m):
    sgn = MET[m][0]; reg = {}
    for i in range(6):
        stat = {}
        for v in grid[i]:
            rel = []
            for t in train:
                d = mv[m][t]; med = st.median(d.values())
                sub = [x for tup,x in d.items() if tup[i]==v]
                rel.append(sgn * st.median(sub)/med if med else 0.0)
            stat[v] = (st.mean(rel), st.stdev(rel)/math.sqrt(len(rel)))
        b = max(stat, key=lambda v: stat[v][0]); cut = stat[b][0]-stat[b][1]
        reg[i] = [v for v in grid[i] if stat[v][0] >= cut]
    return [t for t in fin[tags[0]] if all(t[i] in reg[i] for i in range(6))]

print("(a) METRIC the region is built on -> out-of-sample percentile of held-out FINAL")
for m in MET:
    p, n = [], []
    for held in tags:
        tr = [t for t in tags if t != held]; R = region(tr, m)
        n.append(len(R)); p.append(pct(list(fin[held].values()),
                                      st.median([fin[held][t] for t in R])))
    print("   %-13s mean %5.1f  median %5.1f  worst %5.1f   (region size %s)"
          % (m, st.mean(p), st.median(p), min(p), round(st.mean(n))))

def topn(train, n):
    sc = {}
    for t in train:
        d = fin[t]; med = st.median(d.values())
        for tup,f in d.items(): sc.setdefault(tup,[]).append(f/med)
    return sorted(sc, key=lambda k:-st.median(sc[k]))[:n]

print("\n(c) HOW WIDE -> top-N cells by median, out-of-sample percentile")
for n in (1,4,10,25,50,100,200,500,1000):
    p=[]
    for held in tags:
        T = topn([t for t in tags if t!=held], n)
        p.append(pct(list(fin[held].values()), st.median([fin[held][t] for t in T])))
    print("   top %-5d mean %5.1f  median %5.1f  worst %5.1f" % (n, st.mean(p), st.median(p), min(p)))

print("\nknob composition of the top-50 (all 15 curations), share of the 50:")
T = topn(tags, 50)
for i,k in enumerate(KEYS):
    cnt = {}
    for t in T: cnt[t[i]] = cnt.get(t[i],0)+1
    print("   %-22s %s" % (k, "  ".join("%s:%d" % (v,c) for v,c in
          sorted(cnt.items(), key=lambda x:-x[1]))))
