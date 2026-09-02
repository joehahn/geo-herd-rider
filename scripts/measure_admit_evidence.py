#!/usr/bin/env python3
"""measure_admit_evidence.py — does the article that ADMITS a ticker actually name it?

THE CASE THAT PROMPTED THIS. BDTX held 31% of the 2026-08-25 recommendation. It entered at one scan
under ev230 ("Potential new treatments"), and the entry's cited evidence is an insidermonkey listicle
titled "Why Syndax Pharmaceuticals (SNDX) is one of the best small-cap stocks to buy" -- an article
about a DIFFERENT company. If admissions like that systematically underperform, the check is cheap
and belongs at the scout.

THE MEASUREMENT. For every (event, vehicle) pair, find the entry where that vehicle FIRST appears,
resolve its cited source URLs back to corpus articles, and ask whether any of them names the vehicle:
its SYMBOL in the title, or an org tag / title company whose learned ticker map points at it. Then
compare the vehicle's own forward return over its live span, named vs unnamed.

DELIBERATELY NOT a domain test. TODO.md already records that blocking listicle domains is rejected and
would be harmful -- they are 9.8% of the corpus and their events do BETTER. The question here is not
where the article came from, it is whether it is about the company being bought.

FREE: journals and corpora are fixed on disk, no curation and no LLM.

    scripts/measure_admit_evidence.py                 # cbs_v11 + the canonical CBT lineage
"""
from __future__ import annotations

import collections
import json
import re
import statistics as st
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import orgs  # noqa: E402
import bootstrap_corpus as bc  # noqa: E402

RUNS = ["data/cbs_v11", "data/cbt_3yr_v25_vehgate", "data/cbt_3yr_v24_wirelede",
        "data/cbt_3yr_v23_silence", "data/cbt_3yr_v22_resolver"]
SYM = re.compile(r"\b([A-Z]{1,5})\b")


def corpus_for(run: str) -> dict:
    """{url: article} for whichever pool that run read."""
    if "cbs" in run:
        arts, _ = bc.load(org_tagger=bc.profile_org_tagger())
    else:
        pool = json.loads((ROOT / "data/backtest_3yr_v5/pool.json").read_text())
        arts = pool.get("articles", pool) if isinstance(pool, dict) else pool
    return {a.get("url"): a for a in arts if a.get("url")}, arts


def names_it(art: dict, tk: str, tmap: dict) -> bool:
    """Does this article name that ticker -- by symbol, by org tag, or by title company?"""
    if tk in orgs.title_tickers(art):
        return True
    text = " ".join(str(art.get(k) or "") for k in ("title", "lede", "lede_live", "snippet"))
    if re.search(rf"\b{re.escape(tk)}\b", text):
        return True
    key = tmap.get(tk)                      # the company the corpus learned for this symbol
    if key:
        for o in (art.get("orgs") or []):
            if orgs.normalise(str(o)) == key:
                return True
    return False


def main() -> int:
    for run in RUNS:
        rd = ROOT / run
        if not (rd / "journal.json").exists() or not (rd / "panel.csv").exists():
            print(f"  {run}: skipped (no journal or no frozen panel)"); continue
        by_url, arts = corpus_for(run)
        tmap = orgs.ticker_map(arts)
        panel = pd.read_csv(rd / "panel.csv", index_col=0, parse_dates=True)
        if panel.index.tz is not None:
            panel.index = panel.index.tz_localize(None)
        ev = json.loads((rd / "journal.json").read_text())
        ev = ev.get("events") or ev
        rows = []
        for e in ev.values():
            ents = e.get("entries") or []
            first_seen: dict = {}
            for i, x in enumerate(ents):
                for v in (x.get("vehicles") or []):
                    first_seen.setdefault(v, i)
            for tk, i in first_seen.items():
                if tk not in panel.columns:
                    continue
                srcs = [by_url[u] for u in (ents[i].get("sources") or []) if u in by_url]
                if not srcs:
                    continue                       # no citable evidence -> a different question
                named = any(names_it(a, tk, tmap) for a in srcs)
                d0 = pd.Timestamp(str(ents[i].get("date"))[:10])
                d1 = pd.Timestamp(str(ents[-1].get("date"))[:10])
                s = panel[tk].loc[(panel.index >= d0) & (panel.index <= d1)].dropna()
                b = panel["SPY"].loc[(panel.index >= d0) & (panel.index <= d1)].dropna()
                if len(s) < 5 or len(b) < 5 or s.iloc[0] <= 0:
                    continue
                rows.append((tk, named, float(s.iloc[-1] / s.iloc[0] - 1) - float(b.iloc[-1] / b.iloc[0] - 1)))
        if not rows:
            print(f"  {run}: no resolvable admissions"); continue
        yes = [r[2] for r in rows if r[1]]
        no = [r[2] for r in rows if not r[1]]
        print(f"\n  {run}   {len(rows)} admissions with citable evidence "
              f"({100*len(no)/len(rows):.0f}% cite nothing that names the ticker)")
        for lbl, v in (("article NAMES it   ", yes), ("article does NOT   ", no)):
            if len(v) >= 3:
                print(f"     {lbl} n={len(v):4d}  median excess vs SPY {st.median(v):+7.1%}  "
                      f"mean {np.mean(v):+7.1%}  positive {100*sum(1 for x in v if x>0)/len(v):4.0f}%")
            else:
                print(f"     {lbl} n={len(v):4d}  too few")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
