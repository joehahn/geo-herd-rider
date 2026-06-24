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
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CDX = "http://web.archive.org/cdx/search/cdx"
MIN_INTERVAL = 0.6          # polite throttle between archive.org requests (s)
_last = [0.0]


def _throttle() -> None:
    import time as _t
    dt = _t.monotonic() - _last[0]
    if dt < MIN_INTERVAL:
        _t.sleep(MIN_INTERVAL - dt)
    _last[0] = _t.monotonic()


def _get(url: str, timeout: int = 25) -> bytes:
    _throttle()
    req = urllib.request.Request(url, headers={"User-Agent": "geo-herd-rider/1.0"})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def snapshot(url: str, cutoff: str) -> str | None:
    """Wayback timestamp of the latest snapshot of `url` AT-OR-BEFORE `cutoff` (YYYY-MM-DD), or
    None if unarchived by then. `to=` bounds server-side; `limit=-1` returns the most recent."""
    to = cutoff.replace("-", "") + "235959"
    q = (f"{CDX}?output=json&limit=-1&filter=statuscode:200&to={to}"
         f"&url={urllib.request.quote(url, safe='')}")
    try:
        rows = json.loads(_get(q).decode("utf-8", "ignore"))
    except Exception:  # noqa: BLE001 — a miss must not sink the scan
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
    """The as-of-date lede for `url` (snapshot <= cutoff), or None if unarchived/unextractable."""
    ts = snapshot(url, cutoff)
    if not ts:
        return None
    try:
        h = _get(f"http://web.archive.org/web/{ts}id_/{url}").decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return None
    return _extract_lede(h)


def enrich(articles: list[dict], cutoff: str, cache_path: str | None = None,
           max_chars: int = 280) -> list[dict]:
    """Fill each article's `snippet` with its as-of-date (<= cutoff) Wayback lede, in place-ish
    (returns the list). Only enriches GDELT records (snippet missing or == title); seeds already
    carry a real snippet and are left alone. URL-keyed disk cache so a re-run is cheap and a long
    throttled pass resumes. Misses keep snippet = title (graceful degradation)."""
    cache: dict[str, str | None] = {}
    if cache_path and os.path.exists(cache_path):
        cache = json.loads(Path(cache_path).read_text())
    n_hit = n_miss = n_new = 0
    for a in articles:
        url, title = a.get("url", ""), a.get("title", "")
        if not url or (a.get("snippet") and a.get("snippet") != title):
            continue                                   # no url, or already has a real snippet (seed)
        if url not in cache:
            cache[url] = lede(url, cutoff)
            n_new += 1
            if cache_path:
                tmp = f"{cache_path}.tmp"
                Path(tmp).write_text(json.dumps(cache))
                os.replace(tmp, cache_path)
        snip = cache[url]
        if snip:
            a["snippet"] = snip[:max_chars]
            n_hit += 1
        else:
            n_miss += 1
    print(f"  wayback enrich: {n_hit} enriched, {n_miss} unarchived (kept headline), "
          f"{n_new} fetched, cutoff<={cutoff}", file=sys.stderr)
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
