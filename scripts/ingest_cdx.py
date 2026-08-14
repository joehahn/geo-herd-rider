#!/usr/bin/env python3
"""ingest_cdx.py — pull a publisher's back-catalogue out of the Wayback CDX index.

WHY. The corpus is built from GDELT, and GDELT does not crawl the specialty ETF desks that carry the
EARLY rung of a gem story. Measured 2026-08-12 over the 3-year corpus: etf.com contributes ZERO of
128,565 articles, etftrends.com 143. Those are the outlets that publish "best-performing ETF of 2026,
flying under the radar" WEEKS before Business Times or CNBC pick it up -- and naming it on the first
or second rung is the entire edge (README, "Where the edge actually was"). No amount of curator
tuning can admit an article class the corpus does not contain.

Wayback has them: 164,605 archived etftrends.com URLs in the backtest window, and archive.org's
availability API confirms etf.com snapshots too (its Cloudflare wall blocks LIVE fetches, but the
snapshot is served by archive.org, so the wall does not apply here).

TWO PASSES, cheap first:
  --slugs   (default) enumerate CDX and derive each title from the URL slug. ONE request per domain,
            no page fetches, seconds not days. These outlets put the headline in the path
            ("/best-performing-etf-of-2026-flying-under-the-radar/"), and the scout reads TITLES, so
            a slug-derived headline is a real candidate -- it lands as a headline-only article,
            exactly the class that is already 401 of the current pool.
  --fetch   fetch each snapshot for the real lede. SLOW (archive.org gives ~11/min, so tens of hours)
            and only worth paying once the slug pass shows these outlets surface gems we are missing.

Output is pool.json-shaped, so `ingest.py --no-discover` and the curator consume it unchanged.

    python scripts/ingest_cdx.py --domains etf.com,etftrends.com --out data/cdx_etf
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CDX = "http://web.archive.org/cdx/search/cdx"

# URL paths that are never an article: section fronts, tag/author indexes, assets, paginated listings.
SKIP = re.compile(r"/(tag|tags|author|authors|category|categories|page|feed|wp-|search|about|"
                  r"privacy|terms|contact|subscribe|newsletter|sitemap)(/|$)|"
                  r"\.(jpg|jpeg|png|gif|svg|css|js|xml|pdf|ico|woff2?)$", re.I)
SLUG_STOP = {"index", "html", "htm", "amp", "www", "com"}


def _get(url: str, timeout: int = 240, tries: int = 6) -> str:
    """archive.org rate-limits the CDX endpoint hard -- it REFUSES the connection rather than
    returning 429, which reads like an outage. Back off and retry; that is the whole difference
    between 'etf.com is not archived' (wrong) and 'ask again in a minute' (right)."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "geo-herd-rider/1.0 (research)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            wait = min(120, 15 * 2 ** i)
            print(f"    cdx {type(e).__name__}; retry {i+1}/{tries} in {wait}s", file=sys.stderr, flush=True)
            time.sleep(wait)
    raise RuntimeError(f"CDX failed after {tries} tries: {last}")


def enumerate_domain(domain: str, start: str, end: str) -> list[tuple[str, str]]:
    """[(timestamp, original_url)] for one domain, deduped by urlkey by the index itself."""
    # NO DATE WINDOW, and we keep the EARLIEST snapshot per URL as the publication proxy.
    #
    # The snapshot timestamp is when archive.org CRAWLED the page, not when it was published. Passing
    # from=<window start> made the earliest *in-window* capture the answer, so a 2014 article first
    # crawled in 2014 but re-crawled in 2023 was stamped 2023 and entered the corpus as current news.
    # Measured 2026-08-13 on the URLs that carry a date in their path: 8,785 of 8,936 (98%) were given
    # the WRONG YEAR, with the real publication years peaking in 2014-2016. The curator then opened
    # events on catalysts that had resolved a decade earlier.
    #
    # Querying the whole archive and taking the FIRST capture is a much better estimate -- archive.org
    # generally first sees a page near publication -- and `_publication_date` below prefers the date in
    # the URL path whenever the publisher put one there, which is exact.
    u = (f"{CDX}?url={domain}/*"
         f"&fl=timestamp,original&filter=statuscode:200&collapse=urlkey&limit=400000")
    first: dict[str, str] = {}
    for line in _get(u).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            ts, url = parts
            key = url.split("?")[0].rstrip("/").lower()
            if key not in first or ts < first[key]:      # EARLIEST capture wins
                first[key] = ts
                first[key + "\x00url"] = url
    return [(first[k], first[k + "\x00url"]) for k in first if not k.endswith("\x00url")]


_PATH_DATE = re.compile(r"/(20\d\d)/(\d{1,2})(?:/(\d{1,2}))?/")


def publication_date(url: str, ts: str) -> str:
    """Best available publication date: the URL path if the publisher put one there, else the
    EARLIEST archive capture. The path form is exact; first-capture is a proxy that is usually within
    days of publication and is never the decade-late re-crawl the snapshot timestamp gives."""
    m = _PATH_DATE.search(url)
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3) or 1)
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y}-{mo:02d}-{d:02d}"
    return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"


def slug_title(url: str) -> str:
    """'/best-performing-etf-of-2026-flying-under-the-radar/' -> a headline.

    Query strings are dropped FIRST: the same article appears many times over with different
    utm_source values, and without stripping them one story counts as a dozen."""
    path = url.split("?")[0].rstrip("/")
    seg = path.rsplit("/", 1)[-1]
    seg = re.sub(r"\.(html?|php|amp)$", "", seg, flags=re.I)
    words = [w for w in re.split(r"[-_]+", seg) if w and w.lower() not in SLUG_STOP]
    if len(words) < 3:                       # too short to be a headline -- a section front, not a story
        return ""
    if re.fullmatch(r"\d+", "".join(words)):
        return ""
    return " ".join(words).replace("  ", " ").strip().capitalize()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domains", default="etf.com,etftrends.com")
    ap.add_argument("--start", default="20230801")
    ap.add_argument("--end", default="20260812")
    ap.add_argument("--out", default="data/cdx_etf")
    a = ap.parse_args(argv)
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)

    arts, stats = [], {}
    for dom in [d.strip() for d in a.domains.split(",") if d.strip()]:
        raw_p = out / f"cdx_{dom}.json"
        if raw_p.exists():                                  # the enumeration is the fragile part; cache it
            rows = json.loads(raw_p.read_text())
            print(f"  {dom}: {len(rows):,} URLs (cached)", flush=True)
        else:
            print(f"  {dom}: enumerating CDX ...", flush=True)
            rows = enumerate_domain(dom, a.start, a.end)
            raw_p.write_text(json.dumps(rows))
            print(f"  {dom}: {len(rows):,} archived URLs", flush=True)
        seen, kept = set(), 0
        for ts, url in rows:
            if SKIP.search(url):
                continue
            key = url.split("?")[0].rstrip("/").lower()     # collapse utm_source variants of one story
            if key in seen:
                continue
            seen.add(key)
            title = slug_title(url)
            if not title:
                continue
            arts.append({"published_date": publication_date(url, ts), "source": dom,
                         "title": title, "snippet": title, "url": url.split("?")[0],
                         "language": "English", "queries": "['cdx-backfill']",
                         "lede_source": "slug", "syndication": "1"})
            kept += 1
        stats[dom] = {"cdx_urls": len(rows), "articles": kept}
        print(f"  {dom}: {kept:,} article-shaped titles from slugs", flush=True)

    arts.sort(key=lambda x: x["published_date"])
    pool = {"start": a.start, "end": a.end, "chunk_days": 0, "articles": arts}
    (out / "pool.json").write_text(json.dumps(pool, default=str))
    (out / "cdx_stats.json").write_text(json.dumps(stats, indent=1))
    print(f"\n  wrote {out/'pool.json'}: {len(arts):,} articles "
          f"{arts[0]['published_date'] if arts else '-'} .. {arts[-1]['published_date'] if arts else '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
