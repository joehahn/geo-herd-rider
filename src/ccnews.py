"""ccnews.py — look-ahead-clean body/lede enrichment via Common Crawl's CC-NEWS dataset.

A SPIKE / candidate replacement for `wayback.py` enrichment. Same job: given a GDELT-discovered
article URL, return its AS-WRITTEN body/lede from an immutable, point-in-time record — so the
curator sees the ticker-naming lede without importing the future. Integration contract mirrors
`wayback.lede`:

    body(url, cutoff_date) -> str | None

CC-NEWS is *more* date-honest than Wayback by construction: every record lives in a WARC file
whose name embeds the crawl timestamp (`CC-NEWS-YYYYMMDDhhmmss-NNNNN.warc.gz`), the files are
WRITE-ONCE and never edited, and the crawl happens within hours of publication. So "use only a
record crawled on or before cutoff" reduces to "only read WARC files whose timestamp <= cutoff" —
no CDX "closest snapshot" heuristic, no risk of a later-edited capture. (agent_design.md
"Decision matrix": this is the *Content* column for *Backtest*.)

THE CATCH — there is no hosted URL index for CC-NEWS (it is absent from the CDX index server
`index.commoncrawl.org/collinfo.json` and from the cc-index columnar table, both of which cover
only CC-MAIN). So unlike Wayback (CDX answers "is this URL archived?" in one cheap GET), a CC-NEWS
lookup requires SCANNING the WARC files for the crawl window: download ~1 GB/file, ~10 files/day,
stream-parse the `WARC-Target-URI` of every record. This module therefore works in two phases:

  1. harvest(target_urls, crawl_start, crawl_end, store) — stream the window's WARCs once, keep
     the body for any record whose URL is in `target_urls`, write a {url: {body, crawl_date}} store.
     (Production would instead persist a url->(warc, offset, length) OFFSET INDEX and range-GET the
     record on demand; for a coverage spike, harvesting the bodies straight into a store is simpler
     and answers the same question.)
  2. body(url, cutoff) — read that store; return the body iff its crawl_date <= cutoff.

Run as a script to measure coverage against a GDELT pool:

    python src/ccnews.py measure data/windows/gdelt_pool_5f7b5ca525.json 2026-06-12 [--ndays 2]
"""
from __future__ import annotations

import gzip
import io
import json
import os
import sys
import time
import urllib.request
import urllib.parse as up
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data" / "windows"
BASE = "https://data.commoncrawl.org"
_UA = "geo-herd-rider/1.0 (+https://github.com/joehahn/geo-herd-rider; jmh.datasciences@gmail.com)"
DEFAULT_STORE = DATA / "ccnews_store.json"
_RETRY = {429, 500, 502, 503, 504}      # data.commoncrawl.org throttles with 503/503-Slow-Down


def _open(url: str, timeout: int = 120, tries: int = 5):
    """urlopen with exponential backoff on CC's transient throttling (503/5xx/429)."""
    import urllib.error
    delay = 5.0
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code not in _RETRY or k == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError) as e:           # noqa: F841
            if k == tries - 1:
                raise
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")

# Reuse the exact lede extractor Wayback uses, so CC-NEWS and Wayback ledes are directly comparable.
sys.path.insert(0, str(REPO_ROOT / "src"))
from wayback import _extract_lede  # noqa: E402


# ---------------------------------------------------------------------------- WARC path discovery
_PATHS_CACHE: dict[str, list[str]] = {}


def warc_paths(year: int, month: int) -> list[str]:
    """All CC-NEWS WARC paths for a month, newest-last. Cached per (year,month).
    Each line: crawl-data/CC-NEWS/YYYY/MM/CC-NEWS-YYYYMMDDhhmmss-NNNNN.warc.gz"""
    key = f"{year:04d}/{month:02d}"
    if key in _PATHS_CACHE:
        return _PATHS_CACHE[key]
    url = f"{BASE}/crawl-data/CC-NEWS/{key}/warc.paths.gz"
    raw = _open(url, timeout=60).read()
    paths = gzip.decompress(raw).decode().splitlines()
    _PATHS_CACHE[key] = paths
    return paths


def _warc_ts(path: str) -> str:
    """The crawl timestamp embedded in a WARC filename -> 'YYYY-MM-DD' (the immutable capture date)."""
    name = path.rsplit("/", 1)[-1]            # CC-NEWS-20260612122837-08252.warc.gz
    stamp = name.split("-")[2]                # 20260612122837
    return f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}"


def paths_in_window(start: str, end: str) -> list[str]:
    """Every CC-NEWS WARC whose crawl date is in [start, end] (inclusive), across month boundaries."""
    s, e = start.replace("-", ""), end.replace("-", "")
    months = sorted({(int(d[:4]), int(d[4:6])) for d in (s, e)})
    out = []
    for y, m in months:
        for p in warc_paths(y, m):
            if s <= _warc_ts(p).replace("-", "") <= e:
                out.append(p)
    return sorted(out)


# ---------------------------------------------------------------------------- WARC streaming
def stream_records(warc_path: str, want: set[str] | None, on_url=None) -> dict[str, dict]:
    """Stream one WARC straight from HTTP (no full-file disk write). For each `response` record,
    call on_url(url) if given (cheap census), and if `want` is None or the URL is in `want`, extract
    its lede and keep it. Returns {url: {"body": lede|None, "crawl_date": 'YYYY-MM-DD'}} for kept URLs.

    `want=None` with a body extraction would be huge; pass on_url for a URL-only census instead and
    leave want as an (empty) set so nothing is extracted."""
    from warcio.archiveiterator import ArchiveIterator
    crawl_date = _warc_ts(warc_path)
    found: dict[str, dict] = {}
    resp = _open(f"{BASE}/{warc_path}", timeout=180)
    for rec in ArchiveIterator(resp):
        if rec.rec_type != "response":
            continue
        u = rec.rec_headers.get_header("WARC-Target-URI")
        if not u:
            continue
        if on_url is not None:
            on_url(u)
        if want is not None and u in want and u not in found:
            try:
                h = rec.content_stream().read().decode("utf-8", "ignore")
            except Exception:
                h = ""
            found[u] = {"body": _extract_lede(h), "crawl_date": crawl_date}
    return found


# ---------------------------------------------------------------------------- harvest + contract
def harvest(target_urls: set[str], crawl_start: str, crawl_end: str,
            store_path: str | Path = DEFAULT_STORE, census: bool = True,
            max_files: int | None = None) -> dict:
    """Scan every CC-NEWS WARC crawled in [crawl_start, crawl_end], capturing the body of any record
    whose URL is in `target_urls`. Writes a {url: {body, crawl_date}} store (atomic). Returns a
    measurement dict (counts, per-domain census, timing). One pass over the window; resumable-ish via
    the store (already-found URLs are skipped on a re-run that reloads the store)."""
    store: dict = {}
    if Path(store_path).exists():
        store = json.loads(Path(store_path).read_text())
    paths = paths_in_window(crawl_start, crawl_end)
    if max_files:
        paths = paths[:max_files]
    census_domains: dict[str, int] = {}
    census_urls: set[str] = set()
    on_url = None
    if census:
        def on_url(u):                                   # noqa: E731 — tally the whole CC-NEWS census
            census_urls.add(u)
            census_domains[up.urlparse(u).netloc] = census_domains.get(up.urlparse(u).netloc, 0) + 1
    t0 = time.monotonic()
    bytes_note = []
    for i, p in enumerate(paths, 1):
        want = {u for u in target_urls if u not in store}
        try:
            got = stream_records(p, want, on_url=on_url)
        except Exception as e:
            print(f"  [{i}/{len(paths)}] {p.rsplit('/',1)[-1]}  ERROR {type(e).__name__}: {e}",
                  file=sys.stderr)
            continue
        for u, rec in got.items():
            store[u] = rec
        tmp = f"{store_path}.tmp"
        Path(tmp).write_text(json.dumps(store))
        os.replace(tmp, store_path)
        print(f"  [{i}/{len(paths)}] {p.rsplit('/',1)[-1]}  +{len(got)} matched "
              f"(store={len(store)})  census_urls={len(census_urls)}", file=sys.stderr)
    elapsed = time.monotonic() - t0
    return {
        "crawl_window": [crawl_start, crawl_end], "warc_files": len(paths),
        "store_size": len(store), "census_urls": len(census_urls),
        "census_domains": census_domains, "elapsed_s": round(elapsed, 1),
    }


def body(url: str, cutoff_date: str, store_path: str | Path = DEFAULT_STORE) -> str | None:
    """CONTRACT (mirrors wayback.lede): the as-written CC-NEWS body/lede for `url`, using ONLY a
    record crawled on or before `cutoff_date` (YYYY-MM-DD). Returns the lede (hit), or None for a
    confirmed miss (not in CC-NEWS by the cutoff, or no extractable lede). Date hygiene is enforced
    on the record's immutable crawl date — a record crawled after the cutoff is invisible."""
    if not Path(store_path).exists():
        return None
    store = json.loads(Path(store_path).read_text())
    rec = store.get(url)
    if not rec or rec.get("crawl_date", "9999") > cutoff_date:
        return None
    return rec.get("body") or None


# ---------------------------------------------------------------------------- measurement CLI
def measure(pool_path: str, target_day: str, ndays: int = 2, max_files: int | None = None) -> dict:
    """Coverage of CC-NEWS for the GDELT pool articles PUBLISHED on `target_day`. Scans CC-NEWS WARCs
    crawled in [target_day, target_day+ndays-1] (crawl lags publish), then reports exact-URL coverage
    and domain coverage. Prints a report; returns the metrics dict."""
    pool = json.loads(Path(pool_path).read_text())
    arts = pool["articles"] if isinstance(pool, dict) else pool
    targets = [a for a in arts if a.get("published_date") == target_day and a.get("url")]
    target_urls = {a["url"] for a in targets}
    tgt_domains = {a["url"]: up.urlparse(a["url"]).netloc for a in targets}
    print(f"GDELT pool: {len(arts)} articles; {len(target_urls)} published on {target_day}",
          file=sys.stderr)
    from datetime import date
    y, m, d = map(int, target_day.split("-"))
    end = date.fromordinal(date(y, m, d).toordinal() + ndays - 1).isoformat()
    store_path = DATA / f"ccnews_store_{target_day}.json"
    res = harvest(target_urls, target_day, end, store_path=store_path, census=True,
                  max_files=max_files)
    # exact-URL coverage
    store = json.loads(Path(store_path).read_text())
    hit = [u for u in target_urls if u in store]
    hit_body = [u for u in hit if store[u].get("body")]
    # domain coverage (which target domains appear ANYWHERE in the CC-NEWS census)
    census_domains = set(res["census_domains"])
    dom_covered = {u for u, dm in tgt_domains.items() if dm in census_domains}
    res.update({
        "target_day": target_day, "targets": len(target_urls),
        "exact_url_hits": len(hit), "exact_url_with_body": len(hit_body),
        "exact_coverage_pct": round(100 * len(hit) / len(target_urls), 1) if target_urls else 0,
        "domain_present_hits": len(dom_covered),
        "domain_coverage_pct": round(100 * len(dom_covered) / len(target_urls), 1) if target_urls else 0,
    })
    print("\n===== CC-NEWS COVERAGE =====", file=sys.stderr)
    for k in ("target_day", "targets", "warc_files", "census_urls", "elapsed_s",
              "exact_url_hits", "exact_url_with_body", "exact_coverage_pct",
              "domain_present_hits", "domain_coverage_pct"):
        print(f"  {k}: {res[k]}", file=sys.stderr)
    Path(DATA / f"ccnews_coverage_{target_day}.json").write_text(
        json.dumps({k: v for k, v in res.items() if k != "census_domains"}, indent=2))
    return res


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "measure":
        pool, day = argv[1], argv[2]
        nd = 2
        mf = None
        if "--ndays" in argv:
            nd = int(argv[argv.index("--ndays") + 1])
        if "--max-files" in argv:
            mf = int(argv[argv.index("--max-files") + 1])
        measure(pool, day, ndays=nd, max_files=mf)
        return 0
    if argv[0] == "body":                                 # body <url> <cutoff> [store]
        sp = argv[3] if len(argv) > 3 else DEFAULT_STORE
        print(body(argv[1], argv[2], sp) or "(none)")
        return 0
    print("usage: ccnews.py measure <pool.json> <YYYY-MM-DD> [--ndays N] [--max-files N]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
