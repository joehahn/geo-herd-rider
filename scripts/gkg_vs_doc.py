#!/usr/bin/env python3
"""gkg_vs_doc.py — the Stage-1 gate: does the GKG/BigQuery pool beat the DOC-API pool it replaces?

Compares `src/gkg.pool()` against an ALREADY-CACHED `src/gdelt.pool()` run over the same window, so
the DOC side of the comparison costs nothing (no re-fetch, no re-throttling) and the numbers are the
ones the repo actually produced. Three questions, in order of importance:

  1. RECALL FLOOR — what share of the DOC pool's articles does GKG also surface? A replacement that
     silently loses articles is a regression no matter how fast it is. DOC-only articles are printed
     with their source so the loss is diagnosable (usually: the headline carried no beat keyword and
     was found only by GDELT's full-text index, or the domain is on mill_block).
  2. HEADROOM — how many articles does GKG add that the DOC API never returned? This is the recall
     win the `_VEHICLE` AND-clause was costing (see gkg.py's module docstring).
  3. COST — wall seconds and GB billed vs the DOC run's measured seconds and 429 count.

Honest framing: overlap is measured on URL, and the two engines index on different clocks (GKG's
`_PARTITIONTIME` is INGEST time, the DOC API's `seendate` is when GDELT saw it), so the window edges
never line up exactly. `--pad` widens the GKG window and the comparison is then restricted to the
DOC pool's own date range, which removes most of that artifact but not all of it.

    python scripts/gkg_vs_doc.py --doc data/backtest_bwet_v2/gdelt_pool.json
    python scripts/gkg_vs_doc.py --doc ... --pad 3 --chunk-days 14
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import gkg  # noqa: E402
import retstats  # noqa: E402


def canon(url: str) -> str:
    """URL key for overlap: drop scheme, www., query, fragment and a trailing slash. The two engines
    report the same article with different tracking tails often enough that a raw string compare
    understates the overlap."""
    try:
        s = urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip().lower()
    host = (s.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return urlunsplit(("", host, (s.path or "/").rstrip("/") or "/", "", "")).lstrip("/")


def load_doc(path: Path) -> list[dict]:
    d = json.loads(path.read_text())
    return d.get("articles", d) if isinstance(d, dict) else d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc", required=True, help="cached DOC-API pool json to compare against")
    ap.add_argument("--pad", type=int, default=2, help="days to widen the GKG window (ingest-clock skew)")
    ap.add_argument("--chunk-days", type=int, default=30)
    ap.add_argument("--show", type=int, default=20, help="how many DOC-only articles to list")
    a = ap.parse_args(argv)

    doc = load_doc(Path(a.doc))
    dates = sorted(x["published_date"] for x in doc if x.get("published_date"))
    if not dates:
        return ap.error(f"{a.doc} has no dated articles")
    lo, hi = dates[0], dates[-1]
    import pandas as pd
    glo = (pd.Timestamp(lo) - pd.Timedelta(days=a.pad)).date().isoformat()
    ghi = (pd.Timestamp(hi) + pd.Timedelta(days=a.pad)).date().isoformat()

    doc_stats = {}
    for p in (Path(a.doc).parent / "retrieval_stats.json", ROOT / "data/windows/retrieval_stats.json"):
        if p.exists():
            doc_stats = retstats.load(str(p)).get("gdelt", {}) or doc_stats
            break

    print(f"DOC pool : {len(doc):5d} articles  {lo} .. {hi}  ({a.doc})")
    if doc_stats:
        print(f"           {doc_stats.get('requests')} requests, {doc_stats.get('http_429')} x 429, "
              f"{doc_stats.get('elapsed_s')}s, {doc_stats.get('items_per_min')} items/min")
    print(f"GKG query: {glo} .. {ghi} (+/-{a.pad}d pad)\n")

    t0 = time.monotonic()
    g = gkg.pool(glo, ghi, chunk_days=a.chunk_days)
    elapsed = time.monotonic() - t0

    # restrict both sides to the DOC pool's own date range before comparing
    g_in = [x for x in g if lo <= x["published_date"] <= hi]
    dset = {canon(x["url"]) for x in doc}
    gset = {canon(x["url"]) for x in g_in}
    both = dset & gset
    doc_only = dset - gset
    gkg_only = gset - dset

    print(f"\n{'=' * 68}")
    print(f"GKG pool : {len(g):5d} articles ({len(g_in)} inside the DOC date range)")
    print(f"           {gkg._STAT['queries']} BigQuery queries, {gkg._STAT['billed_gb']:.1f} GB billed, "
          f"{elapsed:.0f}s")
    print(f"\n1. RECALL FLOOR   {len(both):5d}/{len(dset)} DOC articles also in GKG "
          f"({100 * len(both) / len(dset):.1f}%)")
    print(f"2. HEADROOM       {len(gkg_only):5d} articles GKG found that DOC never returned "
          f"({100 * len(gkg_only) / max(len(dset), 1):.0f}% of the DOC pool's size)")
    if doc_stats.get("elapsed_s"):
        print(f"3. COST           {doc_stats['elapsed_s']:.0f}s / {doc_stats.get('http_429', 0)} x 429  ->  "
              f"{elapsed:.0f}s / 0 x 429   ({doc_stats['elapsed_s'] / max(elapsed, 1):.0f}x faster)")

    dbyu = {canon(x["url"]): x for x in doc}
    print(f"\nDOC-only by source (the recall the swap would cost):")
    for s, c in collections.Counter(dbyu[u].get("source", "?") for u in doc_only).most_common(15):
        print(f"  {c:5d}  {s}")
    blocked = gkg._mill_block()
    n_blocked = sum(1 for u in doc_only if gkg._domain_in(dbyu[u].get("source", ""), blocked))
    print(f"\n  of which {n_blocked} are on the profile's mill_block list (dropped ON PURPOSE, not lost)")

    print(f"\nSample DOC-only headlines (first {a.show}):")
    for u in list(doc_only)[:a.show]:
        x = dbyu[u]
        print(f"  [{x.get('source', '?')[:24]:24s}] {str(x.get('title'))[:80]}")

    gbyu = {canon(x["url"]): x for x in g_in}
    print(f"\nSample GKG-only headlines (first {a.show}) -- the headroom:")
    for u in list(gkg_only)[:a.show]:
        x = gbyu[u]
        print(f"  [{x.get('source', '?')[:24]:24s}] {str(x.get('title'))[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
