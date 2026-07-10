#!/usr/bin/env python3
"""gem_capture_readout.py — the make-or-break diagnostic for the forward firehose.

The whole strategy rests on the firehose CAPTURING gem-class coverage: the press naming a specific,
still-EARLY / under-the-radar ticker on a discrete catalyst (the BWET / MP / DRAM pattern) — not
generic "3 stocks to buy" listicles about mega-caps. This reads the accumulating daily pulls
(data/forward/daily/*.json) and scores, per day and overall:

  * outlet mix   — SPECIALTY desk (etf.com/benzinga/seekingalpha/…) vs MAJOR wire vs LISTICLE mill
  * early-framing — fraction of titles/snippets that frame the name as early/under-the-radar
  * catalyst      — fraction tied to a discrete, datable catalyst
  * ticker names  — distinct tickers named, and what fraction are obvious MEGA-CAPS (a mainstream
                    tell: high mega-cap share = the firehose is surfacing the herd, not gems)

It is a RETRIEVAL-health readout (does the pool contain the right *kind* of article), NOT a returns
or lift measure — outcomes are judged only by the forward paper trade. Run it as the new-prompt pool
deepens; a healthy trend is specialty-share and early-framing UP, mega-cap share DOWN.

    python scripts/gem_capture_readout.py [--daily-dir data/forward/daily] [--since 2026-07-10]
"""
from __future__ import annotations

import argparse
import glob
import json
import re
from collections import Counter
from pathlib import Path

# source buckets (lowercase domain substrings). SPECIALTY = the desks that carry the early gem call;
# LISTICLE = the "N stocks to buy" mills (mainstream, aggregate known names); MAJOR = general wires.
SPECIALTY = ("etf.com", "etftrends", "seekingalpha", "benzinga", "stocktitan", "tipranks",
             "marketbeat", "simplywall", "barchart", "stocktwits", "etfdb")
LISTICLE = ("fool.com", "247wallst", "nerdwallet", "kiplinger", "money.usnews", "investorplace",
            "zacks")
MAJOR = ("yahoo", "cnbc", "reuters", "bloomberg", "wsj.com", "marketwatch", "forbes",
         "businessinsider", "apnews", "investing.com", "morningstar")

_EARLY = re.compile(r"under[- ]the[- ]radar|overlooked|still early|flying under|under-owned|"
                    r"before the crowd|hidden gem|unnoticed|undiscovered|small[- ]cap|niche", re.I)
_CATALYST = re.compile(r"\bwar\b|tariff|export ban|export control|sanction|shortage|supply (?:shock|crunch|"
                       r"squeeze|cut)|ceasefire|chokepoint|blockade|rare earth|critical mineral|\bFDA\b|"
                       r"approval|contract win|awarded|\bdeal\b|merger|acquisition|election|\bvote\b|"
                       r"embargo|nuclear|uranium|freight rate", re.I)
_TICKER = re.compile(r"\(([A-Z]{1,5})\)|(?:NASDAQ|NYSE|NYSEARCA|AMEX)[:\s]+([A-Z]{1,5})")
# obvious mega/large-caps — a name here is a mainstream tell, not an under-the-radar gem
MEGACAP = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AMD", "AVGO", "NFLX",
           "AMAT", "COST", "DIS", "INTC", "ORCL", "CRM", "ADBE", "PEP", "KO", "JPM", "BAC", "WMT",
           "XOM", "CVX", "LLY", "NVO", "UNH", "V", "MA", "HD", "PG", "JNJ", "QCOM", "TXN", "MU",
           "BA", "GE", "F", "GM", "PYPL", "UBER", "SHOP", "PLTR", "SMCI", "MSTR", "COIN", "SPY", "QQQ"}


def _bucket(source: str) -> str:
    s = (source or "").lower()
    if any(k in s for k in SPECIALTY):
        return "specialty"
    if any(k in s for k in LISTICLE):
        return "listicle"
    if any(k in s for k in MAJOR):
        return "major"
    return "other"


def _tickers(text: str) -> set[str]:
    return {g for m in _TICKER.findall(text or "") for g in m if g}


def _score(pool: list[dict]) -> dict:
    n = len(pool)
    buckets = Counter(_bucket(a.get("source", "")) for a in pool)
    early = sum(1 for a in pool if _EARLY.search(f"{a.get('title','')} {a.get('snippet','')}"))
    catal = sum(1 for a in pool if _CATALYST.search(f"{a.get('title','')} {a.get('snippet','')}"))
    ticks: set[str] = set()
    for a in pool:
        ticks |= _tickers(f"{a.get('title','')} {a.get('snippet','')}")
    mega = ticks & MEGACAP
    return {"n": n, "buckets": buckets, "early": early, "catal": catal,
            "ticks": ticks, "mega": mega}


def _pct(x: int, n: int) -> str:
    return f"{100 * x / n:4.0f}%" if n else "  — "


def _report(label: str, s: dict) -> None:
    n = s["n"]
    b = s["buckets"]
    print(f"  {label:12} n={n:3}  "
          f"specialty {_pct(b['specialty'], n)}  listicle {_pct(b['listicle'], n)}  "
          f"major {_pct(b['major'], n)}  other {_pct(b['other'], n)}")
    print(f"  {'':12}        early-framed {_pct(s['early'], n)}  catalyst {_pct(s['catal'], n)}  "
          f"| tickers {len(s['ticks'])}  mega-cap {len(s['mega'])} "
          f"({_pct(len(s['mega']), len(s['ticks']) or 1)} of named)")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily-dir", default="data/forward/daily")
    ap.add_argument("--since", default=None, help="only files with date >= this (YYYY-MM-DD)")
    a = ap.parse_args(argv)

    files = sorted(glob.glob(str(Path(a.daily_dir) / "*.json")))
    if a.since:
        files = [f for f in files if Path(f).stem >= a.since]
    if not files:
        print(f"  no daily pulls in {a.daily_dir}" + (f" since {a.since}" if a.since else ""))
        return

    print(f"  GEM-CLASS CAPTURE — {len(files)} daily pull(s) "
          f"({Path(files[0]).stem} .. {Path(files[-1]).stem})")
    print("  (healthy trend: specialty% + early-framed% UP, mega-cap% DOWN)\n")
    seen: dict[str, dict] = {}
    for f in files:                                         # per-day, dedup within day by url
        pool = {x.get("url"): x for x in json.loads(Path(f).read_text()).get("pool", []) if x.get("url")}
        _report(Path(f).stem, _score(list(pool.values())))
        seen.update(pool)                                  # accumulate across days (dedup by url)
    print()
    agg = _score(list(seen.values()))
    _report("OVERALL", agg)
    top = Counter(a.get("source", "?") for a in seen.values()).most_common(8)
    print(f"\n  top sources: {top}")
    if agg["ticks"]:
        gems = sorted(agg["ticks"] - MEGACAP)
        print(f"  non-mega tickers named ({len(gems)}): {gems}")


if __name__ == "__main__":
    main()
