#!/usr/bin/env python3
"""ingest.py — build a backtest news corpus: GKG discovery + ledes. NO LLM, NO curator, NO cost.

The first step of the backtest loop, deliberately separated from curation so retrieval can be
iterated on for free. Discovery rows are cached by (window, keyword-hash) and ledes by URL, so a
re-run after editing retrieval_config.json's FILTERS re-derives the pool without touching BigQuery,
and a re-run after editing its KEYWORDS re-queries only what actually changed.

    # 1 year, fast biased ledes (~15 min end to end)
    python scripts/ingest.py --start 2025-07-04 --end 2026-07-03 --out data/backtest_1yr --live

    # later, the clean arm over the same corpus (slow; --gentle for an unattended overnight run)
    python scripts/ingest.py --out data/backtest_1yr --wayback --gentle --no-discover

CHUNK ALIGNMENT MATTERS. Discovery chunks are keyed by their date range, so two runs share cached
rows only when their chunk boundaries line up. Windows are therefore chosen a whole number of WEEKS
apart: the 1-year window (2025-07-04 .. 2026-07-03) sits exactly 104 weeks inside the 3-year window
(2023-07-07 .. 2026-07-03), so extending to 3 years re-scans only the older 2 years instead of
paying ~227 GB again for ground already covered. Both end at 2026-07-03, forward day-1, so the
backtest timeline butts directly against the live forward series without overlapping it.

The clean (Wayback) arm is intentionally NOT the default. It runs at 1.85-5.8 items/min, i.e. days
to weeks for a corpus this size, and a measured comparison on 142 articles carrying both arms found
97% byte-identical text. Prefer `--live` for the corpus and a stratified `--wayback` SAMPLE to
measure the residual bias (see --sample).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import firehose  # noqa: E402
import lede as lede_mod  # noqa: E402


def _load(pool_path: Path) -> list[dict]:
    if not pool_path.exists():
        sys.exit(f"{pool_path} not found -- run with --start/--end to discover first")
    d = json.loads(pool_path.read_text())
    return d.get("articles", d) if isinstance(d, dict) else d


def _save(pool_path: Path, arts: list[dict], meta: dict) -> None:
    tmp = pool_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**meta, "articles": arts}, default=str))
    tmp.replace(pool_path)


def report(arts: list[dict]) -> dict:
    """Corpus health at a glance -- the numbers the RBT dashboard will plot, printed so a headless
    ingest is still legible."""
    cov = lede_mod.coverage(arts)
    days = {a.get("published_date", "")[:10] for a in arts if a.get("published_date")}
    beats = Counter(q for a in arts for q in (a.get("queries") or []))
    return {
        **cov,
        "distinct_days": len(days),
        "per_day": round(len(arts) / max(len(days), 1), 1),
        "sources": len({a.get("source", "") for a in arts}),
        "with_author": sum(1 for a in arts if a.get("author")),
        "beats_fired": len(beats),
        "syndicated": sum(1 for a in arts if (a.get("syndication") or 1) > 1),
        "top_beats": beats.most_common(5),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="corpus dir (pool.json + lede caches live here)")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--chunk-days", type=int, default=7, help="BigQuery chunk size; keep at 7 so "
                                                              "windows share cached rows")
    ap.add_argument("--no-discover", action="store_true", help="skip discovery; enrich the existing pool")
    ap.add_argument("--live", action="store_true", help="fast biased lede+byline pass (~43 articles/s)")
    ap.add_argument("--wayback", action="store_true", help="clean as-of lede pass (SLOW: 2-6/min)")
    ap.add_argument("--gentle", action="store_true", help="overnight Wayback pace (~1 req/s, 4 workers)")
    ap.add_argument("--misses-only", action="store_true",
                    help="wayback: restrict to articles with NO text at all (coverage backfill). "
                         "Combine with --sample to also measure live-vs-clean drift on the rest.")
    ap.add_argument("--sample", type=int, default=0,
                    help="with --wayback: enrich only a random N-article sample (stratified by month) "
                         "-- the honest way to MEASURE live-arm bias without a multi-week clean pass")
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pool_path = out / "pool.json"
    meta: dict = {}

    if not a.no_discover:
        if not (a.start and a.end):
            return ap.error("discovery needs --start and --end (or pass --no-discover)")
        t0 = time.monotonic()
        print(f"DISCOVERY {a.start} .. {a.end} (engine per profile, {a.chunk_days}d chunks)", flush=True)
        arts = firehose.news_pool(firehose.GDELT_QUERIES, a.start, a.end,
                                  chunk_days=a.chunk_days,
                                  # NO pool cache. The expensive layer (raw BigQuery rows) is cached
                                  # and vocabulary-keyed; a pool cache on top only saves ~3 minutes of
                                  # local filtering and, being keyed on neither the filter CODE nor the
                                  # config, silently returns a stale corpus after any filter change --
                                  # which is exactly what happened once already. Re-derive every time.
                                  cache_path=None,
                                  stats_path=str(out / "retrieval_stats.json"))
        meta = {"start": a.start, "end": a.end, "chunk_days": a.chunk_days}
        # RE-APPLY CACHED ENRICHMENT. Discovery returns FRESH article records, so writing them
        # straight out silently discards every lede fetched on a previous run -- which is exactly
        # what happened: a re-derive wiped 3,985 archived ledes (hours of archive.org time) out of
        # pool.json while leaving them safe in the cache, so the provenance and drift panels went
        # blank. The caches are URL-keyed and cost nothing to replay, so re-derivation must be
        # lossless. fetch=False means cache-only: no network, no archive.org call.
        wb = out / "wayback_cache.json"
        if wb.exists():
            lede_mod.enrich_wayback(arts, a.end, cache_path=str(wb), fetch=False, per_article=True)
        _save(pool_path, arts, meta)
        print(f"  -> {len(arts)} articles in {time.monotonic() - t0:.0f}s -> {pool_path}", flush=True)
    else:
        arts = _load(pool_path)
        print(f"loaded {len(arts)} articles from {pool_path}", flush=True)

    if a.live:
        t0 = time.monotonic()
        print(f"LIVE LEDES + bylines over {len(arts)} articles ...", flush=True)
        lede_mod.enrich_live(arts, cache_path=str(out / "lede_live_cache.json"),
                             stats_path=str(out / "retrieval_stats.json"))
        _save(pool_path, arts, meta)
        print(f"  -> live pass done in {time.monotonic() - t0:.0f}s", flush=True)

    if a.wayback:
        # TWO DISTINCT JOBS, deliberately separated -- they answer different questions.
        #   misses-only : articles with NO text at all. Wayback is often the ONLY possible source
        #                 (a `removed` 404 or a `url_recycled` URL exists nowhere else), so this is
        #                 about COVERAGE.
        #   sample      : articles that already have a live lede, stratified by month. Fetching the
        #                 clean version of text we already have is not about coverage at all -- it
        #                 measures how far the fast arm DRIFTS from the archive, per period. That is
        #                 the bias band the dashboard needs, and it costs ~1h instead of ~33h.
        cutoff = max((x.get("published_date", "") for x in arts), default="")[:10]
        rng = random.Random(0)              # fixed seed: the sample is reproducible across re-runs

        if a.misses_only:
            target = [x for x in arts if not x.get("lede_live") and not x.get("lede")]
            print(f"WAYBACK (coverage): {len(target):,} articles with no text at all", flush=True)
            lede_mod.enrich_wayback(target, cutoff, cache_path=str(out / "wayback_cache.json"),
                                    gentle=a.gentle, per_article=True,
                                    stats_path=str(out / "retrieval_stats.json"))
            _save(pool_path, arts, meta)

        if a.sample:
            have = [x for x in arts if x.get("lede_live") and not x.get("lede")]
            by_month: dict[str, list] = {}
            for x in have:
                by_month.setdefault((x.get("published_date") or "")[:7], []).append(x)
            per = max(1, a.sample // max(len(by_month), 1))
            target = [x for m in sorted(by_month)
                      for x in rng.sample(by_month[m], min(per, len(by_month[m])))]
            print(f"WAYBACK (bias sample): {len(target):,} of {len(have):,} live-lede articles, "
                  f"{len(by_month)} months x ~{per}", flush=True)
            lede_mod.enrich_wayback(target, cutoff, cache_path=str(out / "wayback_cache.json"),
                                    gentle=a.gentle, per_article=True,
                                    stats_path=str(out / "retrieval_stats.json"))
            _save(pool_path, arts, meta)

        if not (a.misses_only or a.sample):
            lede_mod.enrich_wayback(arts, cutoff, cache_path=str(out / "wayback_cache.json"),
                                    gentle=a.gentle, per_article=True,
                                    stats_path=str(out / "retrieval_stats.json"))
            _save(pool_path, arts, meta)

    print("\nCORPUS REPORT")
    for k, v in report(arts).items():
        print(f"  {k:18s} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
