"""wayback.py — look-ahead-clean snippet enrichment via the Wayback Machine.

GDELT gives date-honest DISCOVERY but headline-only content; the headline names the theme, rarely
the ticker (the "(BWET)" lives in the lede). This module fetches the AS-OF-DATE article snapshot
from archive.org and extracts its lede/meta-description, so the curator sees the ticker-naming
snippet without importing the future:

  - CDX is queried with `to=<cutoff>` and we take the LATEST snapshot AT-OR-BEFORE the scan anchor,
    so the content is what existed by the decision date (no look-ahead).
  - URL-keyed archival retrieval — no relevance ranking, no today's-edited-page. The three leaks
    that disqualify a historical web search (date-leak, edited content, hindsight ranking) are all
    absent here. See agent_design.md "Retrieval: GDELT and seeds".

Enrichment, not discovery: it can only fetch URLs GDELT already surfaced. Coverage is partial
(not every niche URL is archived near its date) — misses degrade gracefully to headline-only.

    python src/wayback.py <url> <cutoff YYYY-MM-DD>     # smoke-test one URL
"""
from __future__ import annotations

import gzip
import html
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CDX = "http://web.archive.org/cdx/search/cdx"
MIN_INTERVAL = 1.5          # base throttle (s): ~40 req/min, safely under CDX's documented ~60/min
_RETRY_CODES = {429, 500, 502, 503, 504}   # transient HTTP statuses worth retrying
# Identify the client with contact info — archive.org asks automated clients to do so.
_UA = "geo-herd-rider/1.0 (+https://github.com/joehahn/geo-herd-rider; jmh.datasciences@gmail.com)"
_last = [0.0]


class WaybackTransient(Exception):
    """A retryable failure (429/5xx/timeout) that exhausted retries — couldn't DETERMINE coverage.
    Distinct from a confirmed 'not archived' (None), so callers don't cache it as a permanent miss."""


def _throttle() -> None:
    dt = time.monotonic() - _last[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _last[0] = time.monotonic()


def _get(url: str, timeout: int = 30, tries: int = 4) -> bytes:
    """GET with retry+backoff on transient errors (429/5xx/timeout/conn). Returns bytes on 200;
    re-raises a non-retryable HTTPError (e.g. 404) for the caller to treat as a confirmed miss;
    raises WaybackTransient once retries are exhausted (so it is NOT recorded as a permanent miss)."""
    delay, last = 8.0, None
    for _ in range(tries):
        _throttle()
        wait = delay
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        except urllib.error.HTTPError as e:
            if e.code not in _RETRY_CODES:
                raise                                   # 404 etc — let caller decide (confirmed miss)
            last = e
            ra = e.headers.get("Retry-After") if e.headers else None   # honor server's backoff hint
            if ra and ra.isdigit():
                wait = int(ra)
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            last = e
        time.sleep(min(wait, 120))                      # cap any single wait
        delay *= 2
    raise WaybackTransient(f"{tries} tries exhausted: {last}")


def snapshot(url: str, cutoff: str) -> str | None:
    """Wayback timestamp of the latest snapshot of `url` AT-OR-BEFORE `cutoff` (YYYY-MM-DD).
    Returns the timestamp (hit), None for a CONFIRMED 'no snapshot by then', or raises
    WaybackTransient if it couldn't determine (so the caller won't cache a permanent miss)."""
    to = cutoff.replace("-", "") + "235959"
    q = (f"{CDX}?output=json&limit=-1&filter=statuscode:200&to={to}"
         f"&url={urllib.request.quote(url, safe='')}")
    try:
        rows = json.loads(_get(q).decode("utf-8", "ignore"))    # WaybackTransient propagates
    except urllib.error.HTTPError:
        return None                                             # CDX 4xx -> confirmed no snapshot
    except json.JSONDecodeError:
        return None
    if not rows or len(rows) < 2:
        return None
    return rows[-1][1]          # [header, ...rows]; row[1] = timestamp


def _extract_lede(h: str) -> str | None:
    """og:description -> meta description -> first substantive <p>. Returns clean text or None."""
    def meta(key: str, attr: str) -> str | None:
        pats = [rf'<meta[^>]+{attr}=["\']{re.escape(key)}["\'][^>]*content=["\'](.*?)["\']',
                rf'<meta[^>]+content=["\'](.*?)["\'][^>]*{attr}=["\']{re.escape(key)}["\']']
        for p in pats:
            m = re.search(p, h, re.I | re.S)
            if m and m.group(1).strip():
                return html.unescape(m.group(1)).strip()
        return None
    d = meta("og:description", "property") or meta("description", "name")
    if d:
        return d
    for m in re.finditer(r"<p[^>]*>(.*?)</p>", h, re.I | re.S):
        txt = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        if len(txt) > 60:
            return txt
    return None


def lede(url: str, cutoff: str) -> str | None:
    """The as-of-date lede for `url` (snapshot <= cutoff). Returns the lede (hit), None for a
    CONFIRMED miss (no snapshot, or snapshot has no extractable lede), or raises WaybackTransient
    if archive.org couldn't be reached after retries."""
    ts = snapshot(url, cutoff)                          # WaybackTransient propagates
    if not ts:
        return None
    try:
        h = _get(f"http://web.archive.org/web/{ts}id_/{url}").decode("utf-8", "ignore")
    except urllib.error.HTTPError:
        return None                                    # snapshot fetch 4xx -> confirmed no lede
    return _extract_lede(h)


def enrich(articles: list[dict], cutoff: str, cache_path: str | None = None,
           max_chars: int = 280, fetch: bool = True) -> list[dict]:
    """Fill each article's `snippet` with its as-of-date (<= cutoff) Wayback lede, in place-ish
    (returns the list). Only enriches GDELT records (snippet missing or == title); seeds already
    carry a real snippet and are left alone.

    Cache semantics (the correctness fix): a confirmed result is cached — the lede *string* for a
    hit, or `false` for a confirmed 'not archived'. A TRANSIENT failure (rate-limit/5xx/timeout) is
    NOT cached, so a re-run retries it instead of recording a permanent miss. Legacy `null` cache
    entries (which conflated the two) are treated as 'retry'. Misses keep snippet = title."""
    cache: dict = {}
    if cache_path and os.path.exists(cache_path):
        cache = json.loads(Path(cache_path).read_text())
    n_hit = n_miss = n_defer = n_new = 0
    for a in articles:
        url, title = a.get("url", ""), a.get("title", "")
        if not url or (a.get("snippet") and a.get("snippet") != title):
            continue                                   # no url, or already has a real snippet (seed)
        cached = cache.get(url)
        if fetch and (url not in cache or cached is None):   # unattempted/legacy-null -> (re)attempt
            try:
                res = lede(url, cutoff)                 # str (hit) | None (confirmed miss)
            except WaybackTransient:
                n_defer += 1
                continue                               # don't cache; a re-run retries it
            cache[url] = res if res else False         # hit -> string; confirmed miss -> False
            n_new += 1
            if cache_path:
                tmp = f"{cache_path}.tmp"
                Path(tmp).write_text(json.dumps(cache))
                os.replace(tmp, cache_path)
            cached = cache[url]
        if isinstance(cached, str) and cached:
            a["snippet"] = cached[:max_chars]
            n_hit += 1
        else:
            n_miss += 1                                # confirmed 'not archived' (False)
    mode = "" if fetch else " [cache-only, no archive.org calls]"
    print(f"  wayback enrich{mode}: {n_hit} enriched, {n_miss} not-in-cache/unarchived, {n_defer}"
          f" deferred, {n_new} newly fetched, cutoff<={cutoff}", file=sys.stderr)
    return articles


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) < 2:
        print("usage: python src/wayback.py <url> <cutoff YYYY-MM-DD>", file=sys.stderr)
        return 2
    url, cutoff = argv[0], argv[1]
    ts = snapshot(url, cutoff)
    print(f"snapshot <= {cutoff}: {ts or '(none archived by then)'}")
    print(f"lede: {lede(url, cutoff) or '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
