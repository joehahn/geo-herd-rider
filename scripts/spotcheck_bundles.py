#!/usr/bin/env python3
"""spotcheck_bundles.py — read actual bundles, both eras, and judge them by eye.

WHY, given FBS panels 12-14 already say it is working: aggregates have been wrong twice on this
exact feature today. Bundle coverage read 9.2% -> 42.1% (healthy) while the curation it produced
was WORSE on five of six metrics, because the number counted articles reaching a 2+ bundle and the
damage was done by singletons it could not see. A metric summarises; it cannot tell you the
articles in a bundle are actually about the same thing.

Prints the same views for the GKG era (what the backtest reads, and what bundling was designed
against) and the websearch era (LLM-tagged), so the question is always "does the AFTER look like
the BEFORE" rather than "does the after look plausible".

  python scripts/spotcheck_bundles.py --n 6 --out data/bundle_spotcheck.txt
"""
from __future__ import annotations
import argparse, collections, random, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import bootstrap_corpus as bs, orgs as _o  # noqa: E402


def sample_bundles(b: dict, rng, n: int) -> list:
    """STRATIFIED, not top-N. Showing only the biggest bundles is how a bundler looks good: the
    NVDA bundle is coherent in any scheme. The interesting cases are mid-sized and singleton."""
    big = sorted((k for k, v in b.items() if len(v) >= 8), key=lambda k: -len(b[k]))
    mid = [k for k, v in b.items() if 2 <= len(v) <= 7]
    one = [k for k, v in b.items() if len(v) == 1]
    out = []
    for pool, lab in ((big, "LARGE"), (mid, "MID"), (one, "SINGLETON")):
        pick = pool[:n] if lab == "LARGE" else (rng.sample(pool, min(n, len(pool))) if pool else [])
        out += [(lab, k) for k in pick]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="bundles per size class per era")
    ap.add_argument("--heads", type=int, default=5, help="headlines shown per bundle")
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--out", default="data/bundle_spotcheck.txt")
    a = ap.parse_args()

    arts, meta = bs.load(org_tagger=bs.profile_org_tagger())
    H = meta["handoff"]
    canon = _o.build_canon(arts)
    tmap = _o.ticker_map(arts, canon)
    rng = random.Random(a.seed)
    L = []

    def emit(s=""):
        L.append(s)

    for lab, g in (("GKG / BACKTEST ERA  (pre-handoff, tags from GDELT V2Organizations)",
                    [x for x in arts if (x.get("published_date") or "")[:10] < H]),
                   (f"WEBSEARCH ERA  (post-handoff, tags from {bs.profile_org_tagger()})",
                    [x for x in arts if (x.get("published_date") or "")[:10] >= H])):
        b = _o.group(g, canon=canon, tmap=tmap)
        emit("=" * 100)
        emit(lab)
        emit(f"  {len(g):,} articles, {len(b):,} bundles")
        emit("=" * 100)
        for cls, k in sample_bundles(b, rng, a.n):
            v = b[k]
            emit(f"\n  [{cls}] bundle '{k}'  ({len(v)} articles)")
            for x in v[:a.heads]:
                d = (x.get("published_date") or "")[:10]
                t = (x.get("title") or "").strip()[:96]
                emit(f"      {d}  {t}")
            if len(v) > a.heads:
                emit(f"      ... and {len(v)-a.heads} more")
        # REPLICATION, the mechanism bundling actually runs on: one article, several bundles.
        multi = [x for x in g if len(_o.article_orgs(x, canon, tmap)) >= 3]
        emit(f"\n  --- articles assigned to 3+ bundles ({len(multi):,} of {len(g):,}) ---")
        for x in (rng.sample(multi, min(6, len(multi))) if multi else []):
            emit(f"      {(x.get('title') or '')[:88]}")
            emit(f"        -> {_o.article_orgs(x, canon, tmap)}")
        # and the ones that reach NO bundle
        none = [x for x in g if not _o.article_orgs(x, canon, tmap)]
        emit(f"\n  --- articles in NO bundle ({len(none):,} of {len(g):,}) — should be macro/policy ---")
        for x in (rng.sample(none, min(6, len(none))) if none else []):
            emit(f"      {(x.get('title') or '')[:88]}")
        emit()

    txt = "\n".join(L)
    Path(a.out).write_text(txt)
    print(txt)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
