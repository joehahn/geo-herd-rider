# CC-NEWS enrichment spike — coverage report & recommendation

**Date:** 2026-06-26 · **Branch:** `spike/ccnews` · **Code:** `src/ccnews.py` (new; nothing wired into the firehose)

## TL;DR

**Recommendation: do NOT replace Wayback enrichment with CC-NEWS.** On a real GDELT slice,
CC-NEWS exact-URL coverage is **18.8%** vs Wayback's **~80%** — and the miss is *structural*: it
falls almost entirely on the US/English-language financial press (Yahoo Finance, Motley Fool,
Insider Monkey, Benzinga, Morningstar) that names US-listed gems. CC-NEWS is also far more
expensive per lookup (no URL index → you must scan ~1 GB/file WARCs; ~23 GB / ~34 min to cover a
single day). It **is** genuinely date-honest — arguably more so than Wayback — but date-honesty was
never the gap; **retrieval coverage** is, and CC-NEWS makes it worse. Keep Wayback. CC-NEWS is at
most a *complementary* source for the international/wire outlets it does cover, not a replacement.

## What was measured

- **Contract built:** `src/ccnews.py` exposes `body(url, cutoff_date) -> str | None`, mirroring
  `wayback.lede(url, cutoff)`. It returns the as-written lede from an immutable CC-NEWS record whose
  crawl timestamp is `<= cutoff` (see "Date-honesty" below). Lede extraction **reuses
  `wayback._extract_lede`** so CC-NEWS and Wayback ledes are directly comparable.
- **Sample:** the 405 articles in `data/windows/gdelt_pool_5f7b5ca525.json` **published 2026-06-12**
  (the densest single day in the pool; the pool's domain mix is representative —
  Yahoo Finance / Motley Fool / Insider Monkey dominate).
- **Method:** stream-parsed **every** CC-NEWS WARC crawled **2026-06-12 … 06-13** (22 files,
  ~23 GB, **493,980** response records), keeping the body of any record whose URL is a target.
  Two days so crawl-lag (publish→crawl) gets a fair chance.

## Coverage results (target day 2026-06-12, n=405)

| metric | value | vs Wayback |
|---|---|---|
| **exact-URL coverage** | **76 / 405 = 18.8%** | Wayback ~80% |
| domain-present ceiling | 98 / 405 = 24.2% | — |
| URL hit-rate *within* covered domains | 76 / 98 = 78% | — |
| ledes extracted from hits | 76 / 76 = 100% | parity (same extractor) |
| crawl-lag recovery (06-13 files) | +2 of 76 | minimal |

Artifacts: `data/windows/ccnews_coverage_2026-06-12.json`, `data/windows/ccnews_store_2026-06-12.json`.

### The ceiling is a domain wall, not a crawl-window problem

Hit rate splits cleanly by outlet type (HIT/TOTAL among the 405):

```
US / English-finance press — 0%        international / wire press — ~complete
  finance.yahoo.com   0/9                www.livemint.com          7/7
  www.benzinga.com    0/5                www.finanzen.ch           6/6
  www.bnnbloomberg.ca 0/4                www.finanznachrichten.de  5/6
  www.morningstar.com 0/3                timesofindia.indiatimes   3/3
  www.aktiencheck.de  0/3                www.ibtimes.com.au        3/3
  (Motley Fool / Insider Monkey: absent from all 493,980 records)
```

CC-NEWS's seed list skews toward international, non-paywalled, RSS/sitemap-driven outlets. The 60 MB
raw-census probe told the same story up front (Taiwan Yahoo, livedoor JP, Turkish/Italian/German/
Serbian news, ~0 US-finance). Widening the crawl window won't fix it — the 06-13 files added only
+2 hits — because the misses are domains CC-NEWS **never crawls**, not articles it crawled late.

### Lede quality caveat

The recurring hits are **RTTNews syndicated market-wraps** ("The Japanese stock market has tracked
to the upside…") — they name the *theme/region*, rarely the *ticker*. The headline→ticker gap that
Wayback enrichment exists to close (the "(BWET)" in the lede) is only partly closed by the CC-NEWS
ledes we *do* get.

## Access mechanics (the operational cost)

- **Storage:** WARCs at `https://data.commoncrawl.org/crawl-data/CC-NEWS/YYYY/MM/` (also `s3://commoncrawl/...`,
  no-auth). **312 files for June 2026, ~1.07 GB each, ~10–13/day**, filenames stamped with crawl time.
- **No URL index.** CC-NEWS is **absent** from the hosted CDX index server
  (`index.commoncrawl.org/collinfo.json` lists only CC-MAIN) **and** from the cc-index columnar
  table. This is the decisive operational difference from Wayback: Wayback's CDX answers "is this URL
  archived ≤ cutoff?" in one cheap GET; CC-NEWS forces a **full WARC scan** of the crawl window.
- **Cost to cover one day:** ~23 GB downloaded, ~494k records parsed, **~34 min wall** (~11 MB/s) to
  resolve 405 URLs. Marginal cost per *extra* URL on an already-scanned day ≈ 0, but the fixed
  per-day-of-coverage cost is large. For the firehose's real cadence (≤80 URLs/week spread across a
  ~7-day window) you'd scan ~7 days of WARCs (~70 files, ~75 GB, ~2 h) per weekly slice — far more
  than Wayback, for *lower* coverage. A production design would build a persistent
  `url -> (warc, offset, length)` index once and range-GET records, but that's a real ingestion
  pipeline, not a drop-in, and it still can't cover domains CC-NEWS doesn't crawl.
- **Parsing:** `warcio` stream-parses straight from the HTTP response (no 1 GB/file disk writes);
  added to the venv. Transient 503/Slow-Down from `data.commoncrawl.org` handled with backoff.

## Date-honesty (the one place CC-NEWS wins — but it's not the bottleneck)

CC-NEWS clears all of CLAUDE.md #4 cleanly, and is **structurally more honest than Wayback**:

1. **Immutable, point-in-time.** Each record lives in a write-once WARC whose filename embeds the
   crawl timestamp. `body()` enforces look-ahead hygiene by reading **only** WARC files stamped
   `<= cutoff` — no "closest snapshot" heuristic, no chance of a later-edited capture (Wayback's CDX
   can hand back a snapshot whose *content* post-dates the news).
2. **Crawl ≈ publish.** CC-NEWS captures within hours of publication, so the as-of content is the
   as-written content.
3. **Enrichment, not discovery** — exactly like Wayback: it can only fetch URLs GDELT already
   surfaced (you can't ask CC-NEWS "what tanker news existed in March"). GDELT still discovers.

But date-honesty was never the gap. Wayback already satisfies #4. The thing that limits realistic
vehicle recall is **coverage of the early/naming article**, and CC-NEWS covers *less* of it.

## Bottom line

| | Wayback | CC-NEWS |
|---|---|---|
| exact-URL coverage (this sample) | ~80% | **18.8%** |
| covers US/English-finance press | yes | **no (~0%)** |
| URL-addressable lookup | yes (CDX GET) | **no (full WARC scan)** |
| cost to resolve ~400 URLs/day | minutes, fetch-only-what-you-need | ~23 GB / ~34 min |
| date-honest | yes | yes (marginally stronger) |
| lede names the ticker | curated/meta-desc | often wire-wrap boilerplate |

CC-NEWS does not clear the only bar that matters here (coverage ≥ Wayback). **Stay on Wayback.**
If we ever want a *second* date-honest body source for the international/wire outlets CC-NEWS covers
well (livemint, finanzen.*, Times of India, ibtimes), `src/ccnews.py` is ready to be pointed at a
prebuilt offset index — but that's additive, gated by the scoreboard, and not this rung.
