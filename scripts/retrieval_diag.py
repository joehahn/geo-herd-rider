#!/usr/bin/env python3
"""retrieval_diag.py — retrieval diagnostics for the firehose, against the seed gems (ground truth).

For each seed gem it walks 2-week windows across the gem's era, queries Tavily (date-ranged, look-
ahead-clean via search.py), and records every article that NAMES the gem: date, outlet, framing
(catalyst / under-the-radar / momentum). Answers: how EARLY + how DENSELY does the firehose surface
each gem, from WHERE, framed HOW. Writes data.json; retrieval_diag_render.py turns it into a dashboard.

    python scripts/retrieval_diag.py [--gems BWET,MP] [--out docs_preview/retrieval_diag/data.json]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from collections import Counter
from urllib.parse import urlparse
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from util import load_dotenv  # noqa: E402
load_dotenv()
import search  # noqa: E402
import pandas as pd  # noqa: E402

# gem: (names-to-match, queries, period start, period end, peak) — peak from the seed/known run
GEMS = {
 "BWET": (["bwet","breakwave"], ["Breakwave tanker ETF BWET","tanker freight rates ETF Iran Hormuz","best performing ETF under the radar tanker"], "2026-02-15","2026-05-10","2026-04-25"),
 "MP":   (["mp materials"], ["MP Materials rare earth stock","MP Materials Pentagon DoD deal","MP Materials stock"], "2025-03-01","2025-08-01","2025-07-10"),
 "SMR":  (["nuscale"], ["NuScale SMR nuclear stock","NuScale ADVANCE Act reactor","NuScale small modular reactor stock"], "2024-04-01","2024-08-01","2024-07-10"),
 "RNMBY":(["rheinmetall","rnmby"], ["Rheinmetall defense stock rearmament","Rheinmetall RNMBY Europe defense spending","Rheinmetall stock"], "2025-02-01","2025-07-01","2025-06-15"),
 "MSTR": (["microstrategy","mstr"], ["MicroStrategy MSTR bitcoin stock","MicroStrategy Saylor bitcoin","MicroStrategy stock election"], "2024-07-01","2024-11-20","2024-11-18"),
 "GEO":  (["geo group","the geo group"], ["GEO Group private prison stock","GEO Group immigration Trump stock","GEO Group stock"], "2024-08-01","2024-11-20","2024-11-06"),
}
UNDER=["under the radar","little-known","overlooked","flying under","hidden gem","still early","obscure","niche","under-the-radar","under-owned"]
CATALYST=["deal","contract","award","approval"," bill "," act ","war","sanction","tariff","export","shortage","supply","election","vote","fda","merger","acquisition","pentagon","order"]
MOM=["surg","soar","rally","best perform","biggest gain","jump","spike","%","record high","skyrocket","rocket","climb"]
def frame(txt):
    t=txt.lower()
    if any(u in t for u in UNDER): return "under-radar"
    if any(c in t for c in CATALYST): return "catalyst"
    if any(m in t for m in MOM): return "momentum"
    return "other"

def run(gems, out):
    data={}
    for gk in gems:
        names,queries,start,end,peak=GEMS[gk]
        wins=[(w.date().isoformat(),(w+pd.Timedelta(days=14)).date().isoformat())
              for w in pd.date_range(start,end,freq="14D")]
        series=[]; allhits={}
        for lo,hi in wins:
            found={}
            for q in queries:
                try:
                    for r in search.search(q, before_date=hi, start_date=lo, max_results=8):
                        blob=(str(r.get("title",""))+str(r.get("content","")))[:700]
                        if any(n in (blob+str(r.get("url",""))).lower() for n in names):
                            found[r.get("url")]={"date":str(r.get("published_date",""))[:16],"src":urlparse(str(r.get("url",""))).netloc.replace("www.",""),"title":str(r.get("title",""))[:90],"frame":frame(blob)}
                except Exception: pass
            allhits.update(found)
            series.append({"win":lo,"n":len(found)})
            print(f"   {gk} {lo}..{hi}: {len(found)}", flush=True)
        hits=list(allhits.values())
        dates=sorted(h["date"] for h in hits if h["date"])
        data[gk]={"peak":peak,"start":start,"end":end,"series":series,
                  "earliest":dates[0] if dates else None,"total":len(hits),
                  "outlets":Counter(h["src"] for h in hits).most_common(10),
                  "framing":Counter(h["frame"] for h in hits),
                  "samples":sorted(hits,key=lambda h:h["date"])[:6]}
        print(f"  == {gk}: earliest {data[gk]['earliest']} (peak {peak}) | {len(hits)} articles | framing {dict(data[gk]['framing'])}", flush=True)
    Path(out).write_text(json.dumps(data,indent=1,default=str))
    print(f"\nwrote {out}", flush=True)

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--gems",default=",".join(GEMS)); ap.add_argument("--out",default="docs_preview/retrieval_diag/data.json")
    a=ap.parse_args(); run([g for g in a.gems.split(",") if g in GEMS], a.out); print("DIAG DONE",flush=True)
