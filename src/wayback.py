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

import datetime
import gzip
import html
import json
import os
import random
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CDX = "http://web.archive.org/cdx/search/cdx"
AVAIL = "http://archive.org/wayback/available"
# THE SNAPSHOT LOOKUP IS THE WHOLE COST. This module used to resolve every article's timestamp with a
# CDX range scan (from=/to=/filter=), which walks a URL's capture history server-side -- expensive,
# slow, and the thing archive.org throttles first. We then "fixed" the resulting 504s by cutting our
# own concurrency, i.e. treating the symptom. The availability API answers the same question
# ("closest snapshot at-or-before T") as a single indexed point lookup. PWR has always used it and
# backfills in a fraction of the time. CDX is kept only as a fallback when availability returns
# nothing, since the two indexes disagree at the margins.
MIN_INTERVAL = 0.25         # politeness pace (s) between request STARTS, matching PWR. The old 1.5s was
                            # compensation for CDX being heavy; a point lookup does not need it.
_RETRY_CODES = {429, 500, 502, 503, 504}   # transient HTTP statuses worth retrying
# Identify the client with contact info — archive.org asks automated clients to do so.
_UA = "geo-herd-rider/1.0 (+https://github.com/joehahn/geo-herd-rider; jmh.datasciences@gmail.com)"
_last = [0.0]
_throttle_lock = threading.Lock()    # guards _last[0] slot reservation for concurrent enrich fetches
_ENRICH_WORKERS = 6                  # concurrent lede() fetches. Kept modest because archive.org tightened
                                     # its limits after the Oct-2024 outage; the pacer, not this number,
                                     # is what protects it now that the lookup is a cheap point query.
_BACKOFF_BASE = 1.0                  # was 8.0 -- an 8s first retry meant one blip cost more wall time than
_BACKOFF_CAP = 20.0                  # the request it was retrying. Jittered below, per archive.org guidance.
_THIN_HTML = 1000                    # an archived body shorter than this is a partial/throttled capture,
                                     # NOT a genuine "no lede" -- never cache it as a confirmed miss.
_CKPT_EVERY = 20                     # flush the cache + print progress every N resolved URLs, so the
                                     # multi-hour overnight pass is resumable and observable
_CDX_FROM_DAYS = 120                 # CDX scan lower bound: cutoff-120d (news captured near publish);
                                     # without a `from=` the full-history scan 504s/times out on busy URLs
# retrieval-health counters (process-cumulative across a run)
_STAT = {"requests": 0, "http_429": 0, "http_5xx": 0, "timeout": 0, "wall_s": 0.0}


class WaybackTransient(Exception):
    """A retryable failure (429/5xx/timeout) that exhausted retries — couldn't DETERMINE coverage.
    Distinct from a confirmed 'not archived' (None), so callers don't cache it as a permanent miss."""


def _throttle() -> None:
    """Thread-safe rate limiter: hands out request START slots spaced >= MIN_INTERVAL apart, but
    RESERVES the slot under the lock and sleeps outside it — so concurrent callers (the enrich
    thread pool) get evenly staggered starts (<= ~40/min, the same safe rate as the old serial
    path) while their multi-second archive.org latencies overlap instead of serializing."""
    with _throttle_lock:
        now = time.monotonic()
        slot = max(now, _last[0] + MIN_INTERVAL)   # next free slot, at least MIN_INTERVAL after last
        _last[0] = slot
    wait = slot - now
    if wait > 0:
        time.sleep(wait)


_SESSION = None
_SESSION_LOCK = threading.Lock()


def _session():
    """One pooled requests.Session for archive.org. urllib opened a fresh TCP+TLS connection per
    request; every lookup and fetch here hits the SAME host, so keep-alive removes a full handshake
    from each one."""
    global _SESSION
    if _SESSION is None:
        with _SESSION_LOCK:
            if _SESSION is None:
                import requests
                s = requests.Session()
                ad = requests.adapters.HTTPAdapter(pool_connections=_ENRICH_WORKERS,
                                                   pool_maxsize=_ENRICH_WORKERS * 2, max_retries=0)
                s.mount("http://", ad)
                s.mount("https://", ad)
                s.headers.update({"User-Agent": _UA})
                _SESSION = s
    return _SESSION


def _get_json(url: str, params: dict, timeout: int = 12) -> dict:
    """GET returning parsed JSON, with the same pacing/retry discipline as _get. Used by the
    availability API."""
    import requests
    last = None
    for attempt in range(4):
        _throttle()
        _STAT["requests"] += 1
        try:
            r = _session().get(url, params=params, timeout=timeout)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                _STAT["http_429" if r.status_code == 429 else "http_5xx"] += 1
                ra = r.headers.get("Retry-After", "")
                if ra.isdigit():
                    time.sleep(min(float(ra), _BACKOFF_CAP))
                last = requests.exceptions.HTTPError(str(r.status_code))
            else:
                return r.json()
        except Exception as e:  # noqa: BLE001
            _STAT["timeout"] += 1
            last = e
        if attempt < 3:
            # jitter so concurrent workers do not retry in lockstep and manufacture a fresh burst
            time.sleep(min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_CAP) * (1 + random.random()))
    raise WaybackTransient(f"availability API failed: {last}")


def _get(url: str, timeout: int = 60, tries: int = 4) -> bytes:
    """GET with retry+backoff on transient errors (429/5xx/timeout/conn). Returns bytes on 200;
    re-raises a non-retryable HTTPError (e.g. 404) for the caller to treat as a confirmed miss;
    raises WaybackTransient once retries are exhausted (so it is NOT recorded as a permanent miss)."""
    delay, last = _BACKOFF_BASE, None
    for _ in range(tries):
        _throttle()
        wait = delay
        t0 = time.monotonic()
        _STAT["requests"] += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            _STAT["wall_s"] += time.monotonic() - t0
            return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        except urllib.error.HTTPError as e:
            _STAT["wall_s"] += time.monotonic() - t0
            if e.code not in _RETRY_CODES:
                raise                                   # 404 etc — let caller decide (confirmed miss)
            last = e
            _STAT["http_429" if e.code == 429 else "http_5xx"] += 1
            ra = e.headers.get("Retry-After") if e.headers else None   # honor server's backoff hint
            if ra and ra.isdigit():
                wait = int(ra)
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            _STAT["wall_s"] += time.monotonic() - t0
            _STAT["timeout"] += 1
            last = e
        # jitter: archive.org guidance and scraping practice both call for it so parallel
        # workers do not retry in lockstep
        time.sleep(min(wait, _BACKOFF_CAP) * (1 + random.random()))
        delay *= 2
    raise WaybackTransient(f"{tries} tries exhausted: {last}")


def snapshot(url: str, cutoff: str) -> str | None:
    """Timestamp of the latest snapshot of `url` AT-OR-BEFORE `cutoff` (YYYY-MM-DD), or None for a
    CONFIRMED "nothing archived by then". Raises WaybackTransient when it could not be determined, so
    the caller never caches a blip as a permanent miss.

    Tries the AVAILABILITY API first -- one indexed point lookup, and the reason this is fast. Falls
    back to a CDX range scan only when availability reports nothing, because the two indexes disagree
    at the margins and CDX occasionally holds a capture availability does not surface."""
    day = cutoff.replace("-", "")
    try:
        snap = (_get_json(AVAIL, {"url": url, "timestamp": day + "235959"})
                .get("archived_snapshots", {}).get("closest", {}))
        ts = snap.get("timestamp", "") if snap.get("available") else ""
        if ts and ts[:8] <= day:
            return ts
    except WaybackTransient:
        pass                                    # fall through to CDX rather than fail the article

    # CDX fallback. Bounded with `from=`: without it CDX walks a URL's ENTIRE capture history, which
    # is O(captures) and 504s on heavily-archived URLs.
    frm = (datetime.date.fromisoformat(cutoff)
           - datetime.timedelta(days=_CDX_FROM_DAYS)).strftime("%Y%m%d") + "000000"
    q = (f"{CDX}?output=json&limit=-1&filter=statuscode:200&from={frm}&to={day}235959"
         f"&url={urllib.request.quote(url, safe='')}")
    try:
        rows = json.loads(_get(q).decode("utf-8", "ignore"))
    except urllib.error.HTTPError:
        return None
    except json.JSONDecodeError:
        return None
    return rows[-1][1] if rows and len(rows) >= 2 else None


_MIN_LEDE = 40           # reject sub-fragment extractions (e.g. a meta value truncated at an apostrophe)
_MAX_LEDE = 600          # a lede is a sentence or two; longer = we grabbed a blob, so cap/skip

# Markup / script / analytics debris that is NOT prose. Defense in depth behind the per-tag meta
# parse: whatever slips through must never reach the curator, which cannot tell a lede from a
# <script> tag and will happily "reason" about chartbeat config.
_MARKUP_RE = re.compile(
    r"<script|<meta\b|<div\b|<span\b|<link\b|/>|data-next-head|width=device-width|charset\s*=|"
    r"function\s*\(|googletag|chartbeat|window\.|\{\s*\"@context|;\}|&&|=>", re.I)


def _looks_like_markup(text: str) -> bool:
    """True when an 'extracted lede' is really HTML/JS debris rather than a sentence."""
    return bool(_MARKUP_RE.search(text or ""))


# ---------------------------------------------------------------------- bylines
# Meta keys that carry a byline, in preference order. `byl` is the NYT's; parsely/sailthru are the
# two analytics packages that most reliably tag an author when the standard meta is missing.
_AUTHOR_META_KEYS = ("author", "article:author", "byl", "parsely-author", "sailthru.author",
                     "dc.creator", "citation_author", "twitter:creator")

# Bylines that name a WIRE or the SITE ITSELF, not a person. Kept out so a per-author view tracks
# real writers rather than publishers -- the failure mode is a chart where "Reuters" is the top author.
_PSEUDO_AUTHORS = {
    "reuters", "associated press", "the associated press", "ap", "bloomberg", "bloomberg news",
    "business wire", "businesswire", "pr newswire", "prnewswire", "globe newswire", "globenewswire",
    "accesswire", "access newswire", "newsfile corp", "cision", "stock titan", "stocktitan",
    "marketbeat", "market beat", "zacks", "zacks equity research", "the motley fool", "motley fool",
    "benzinga", "benzinga newsdesk", "investing.com", "gurufocus", "simply wall st", "tipranks",
}
# Publishers whose "author" metadata is the ISSUING COMPANY of a press release, not a writer.
_WIRE_PUBLISHERS = ("prnewswire", "businesswire", "business wire", "globenewswire", "globe newswire",
                    "accesswire", "newsfile", "einpresswire", "issuewire")
_PSEUDO_SUBSTR = ("staff", "newsroom", "editorial", "news desk", "newsdesk", "press release",
                  "newswire", "correspondent", "research team", " team", "contributor", ".com",
                  "editor", "transcribing", "redakt")


def clean_author(author: str | None, publisher: str | None = None) -> str:
    """The byline if it looks like a real person, else "".

    Drops wire/brand/newsroom pseudo-authors and the site-name-as-byline case (author == publisher).
    A multi-part byline ("Jane Doe; Zacks Equity Research") keeps only its real-person segments."""
    if not author:
        return ""
    a = " ".join(str(author).split())
    # A PR wire's "byline" is the ISSUING COMPANY, never a journalist ("SK Telecom" on prnewswire).
    # Drop the whole field rather than record a press release's subject as its author.
    if publisher and any(w in str(publisher).lower() for w in _WIRE_PUBLISHERS):
        return ""
    # Split multi-part bylines on ; , and "and", keeping only the real-person segments. The comma case
    # is the common one: "Jonathan Ponciano, The Motley Fool".
    parts = [p.strip() for p in re.split(r"[;,]|\band\b", a, flags=re.I) if p.strip()]
    if len(parts) > 1:
        real = [p for p in parts if p.lower() not in _PSEUDO_AUTHORS
                and not any(s in p.lower() for s in _PSEUDO_SUBSTR)]
        if real:
            a = "; ".join(real)
    a = re.sub(r"^\s*(by|written by|posted by)\s+", "", a, flags=re.I).strip(" ,|-")
    al = a.lower()
    if not a or len(a) > 80:
        return ""
    if a.startswith("@"):                    # a social handle is not a byline (e.g. "@mint")
        return ""
    # The site's OWN name as a byline. A raw string compare never fires, because the byline is a
    # display name ("Manila Times") and the publisher is a domain ("manilatimes.net") -- measured:
    # 394 bylines (2.1%) were the publisher, and they cluster at the TOP of a per-author chart, so
    # they were the most visible thing wrong with it. Reduce both to alphanumeric brand tokens and
    # compare those instead.
    if publisher:
        pub_tok = re.sub(r"[^a-z0-9]", "", re.sub(r"\.[a-z]{2,}$", "", str(publisher).lower()))
        name_tok = re.sub(r"[^a-z0-9]", "", al)
        # substring only when the token is long enough that a collision is implausible
        if name_tok and pub_tok and (name_tok == pub_tok
                                     or (len(name_tok) >= 5 and name_tok in pub_tok)
                                     or (len(pub_tok) >= 5 and pub_tok in name_tok)):
            return ""
    if al in _PSEUDO_AUTHORS or any(s in al for s in _PSEUDO_SUBSTR):
        return ""
    # A real byline has at least two name-ish words; single tokens are almost always a brand.
    if len(a.split()) < 2:
        return ""
    return a if re.search(r"[A-Za-z]{2}", a) else ""


_TITLE_META_KEYS = ("og:title", "twitter:title")


def extract_page_title(h: str) -> str:
    """The page's OWN headline (og:title / twitter:title / <title>), or "".

    Used to detect a recycled URL by comparing headline-to-headline. Comparing a stored headline
    against extracted BODY text does not work: when a page has no meta description, _extract_lede
    falls back to the first sizeable <p>, which is often a mid-article continuation paragraph that
    legitimately shares no words with its own headline. Measured: that mismatch made a body-overlap
    test reject 73.5% of the text it saw, wrongly."""
    h = (h or "")[:200_000]
    for tag in re.finditer(r"<meta\b[^>]*>", h, re.I):
        tg = tag.group(0)
        ident = re.search(r'\b(?:name|property)\s*=\s*(["\'])(.*?)\1', tg, re.I)
        content = re.search(r'\bcontent\s*=\s*(["\'])(.*?)\1', tg, re.I | re.S)
        if ident and content and ident.group(2).strip().lower() in _TITLE_META_KEYS:
            v = html.unescape(content.group(2)).strip()
            if len(v) >= 12:
                return v
    m = re.search(r"<title[^>]*>(.*?)</title>", h, re.I | re.S)
    return html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""


def extract_author(h: str, publisher: str | None = None) -> str:
    """Author byline from a page's metadata, or "". Two sources, meta tags then JSON-LD.

    A SECOND parse over the same html, deliberately separate from _extract_lede so the lede text
    stays byte-identical (cached ledes are unaffected by adding this). Both the Wayback and live
    fetchers already hold the html, so this costs a cheap re-parse and NO extra network."""
    h = (h or "")[:200_000]
    for tag in re.finditer(r"<meta\b[^>]*>", h, re.I):
        t = tag.group(0)
        ident = re.search(r'\b(?:name|property)\s*=\s*(["\'])(.*?)\1', t, re.I)
        content = re.search(r'\bcontent\s*=\s*(["\'])(.*?)\1', t, re.I | re.S)
        if ident and content and ident.group(2).strip().lower() in _AUTHOR_META_KEYS:
            got = clean_author(html.unescape(content.group(2)), publisher)
            if got:
                return got
    # JSON-LD: "author": "Name" | {"name": "Name"} | [{"name": "Name"}, ...]
    for m in re.finditer(r'"author"\s*:\s*(\{[^{}]*\}|\[[^\[\]]*\]|"[^"]{2,80}")', h, re.I | re.S):
        blob = m.group(1)
        nm = re.search(r'"name"\s*:\s*"([^"]{2,80})"', blob) if blob[0] in "{[" else None
        cand = nm.group(1) if nm else (blob.strip('"') if blob[0] == '"' else "")
        got = clean_author(html.unescape(cand), publisher)
        if got:
            return got
    return ""


def _extract_lede(h: str) -> str | None:
    """First good lede: og:description -> meta description -> twitter:description -> first real <p>.
    Returns clean text in [_MIN_LEDE, _MAX_LEDE] chars, or None.

    Robustness fixes over the naive original: the `content` attribute's quote is captured with a
    BACKREFERENCE so an apostrophe inside a double-quoted value ("...isn't...") no longer truncates
    the match; HTML entities are unescaped; sub-fragment results (<40 chars) are rejected; and the
    <p> fallback skips whole-page blobs (a malformed/nested <p> can swallow the entire document).

    Input is bounded to the first ~200 KB: meta/og descriptions live in <head> and the first real
    <p> is near the top, but some archived pages are multi-MB — and the `(.*?)</p>` scan over a
    huge body is O(n^2), which (since `re` holds the GIL) can pin the whole process at 100% CPU for
    many minutes on one pathological page. Truncating caps that worst case without losing the lede."""
    h = h[:200_000]

    # Parse meta tags ONE TAG AT A TIME. The previous version searched the whole document for
    # `content="..." ... name="description"`, and with re.S the lazy `(.*?)` happily spanned tag
    # BOUNDARIES: on any Next.js page it matched the viewport tag's content="width=device-width",
    # ran through the intervening <script> tags, and stopped at a later description attribute --
    # yielding raw markup as the "lede". Measured at 9% of archived ledes before this fix. Scanning
    # each <meta ...> in isolation makes the cross-tag match impossible by construction.
    metas = []
    for tag in re.finditer(r"<meta\b[^>]*>", h, re.I):
        t = tag.group(0)
        content = re.search(r'\bcontent\s*=\s*(["\'])(.*?)\1', t, re.I | re.S)
        if not content:
            continue
        ident = re.search(r'\b(?:name|property)\s*=\s*(["\'])(.*?)\1', t, re.I)
        if ident:
            metas.append((ident.group(2).strip().lower(), content.group(2)))

    for key in ("og:description", "description", "twitter:description"):
        for name, val in metas:
            if name != key:
                continue
            v = html.unescape(val).strip()
            if len(v) >= _MIN_LEDE and not _looks_like_markup(v):
                return v[:_MAX_LEDE]

    for m in re.finditer(r"<p[^>]*>(.*?)</p>", h, re.I | re.S):
        txt = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        if 60 <= len(txt) <= 2000 and not _looks_like_markup(txt):
            return txt[:_MAX_LEDE]                   # skip tiny fragments AND whole-page blobs
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


def _ckey(url: str, cutoff: str) -> str:
    """Cache key. MUST include the cutoff: the same URL asked for at different as-of dates is a
    DIFFERENT question, and a URL-only key silently returns whichever answer was cached first. That
    is how a corpus-wide backfill using one late cutoff could poison every future per-week fetch."""
    return f"{url}|{cutoff}"


def _needs(a: dict, field: str) -> bool:
    """True when `a` still needs an archive fetch to fill `field`. For the legacy `snippet` target a
    GDELT record is identified by snippet missing or == title (a seed carries a real snippet already
    and is left alone); for any other target, simply 'not filled yet'."""
    if field == "snippet":
        return not (a.get("snippet") and a.get("snippet") != a.get("title", ""))
    return not a.get(field)


def enrich(articles: list[dict], cutoff: str, cache_path: str | None = None,
           max_chars: int = 280, fetch: bool = True, stats_path: str | None = None,
           field: str = "snippet", workers: int | None = None,
           min_interval: float | None = None,
           per_cutoff: dict[str, str] | None = None) -> list[dict]:
    """Fill each article's `field` with its as-of-date (<= cutoff) Wayback lede, in place-ish
    (returns the list). Only enriches records that still need it (see `_needs`).

    `field` is the target key. The default `"snippet"` is the legacy behaviour every existing caller
    relies on. `src/lede.py` passes `field="lede"` so the look-ahead-CLEAN lede lands in its own slot
    alongside the fast-but-biased `lede_live`, instead of the two overwriting each other in `snippet`.
    Every hit is stamped `lede_source="wayback"` so provenance survives into the dashboards.

    `per_cutoff` maps url -> its OWN as-of date, overriding the single `cutoff` per article. This is
    the look-ahead-correct mode and the one a corpus-wide backfill must use: a single late cutoff asks
    archive.org for the page as it stood MONTHS after publication, which is exactly the contamination
    the archived lede exists to avoid. (Measured on the 1-year corpus: a global cutoff sat a median of
    180 days -- and up to 364 -- after the article's own date, for 73% of the corpus.)

    `workers` / `min_interval` override the module defaults for one call — that is how the overnight
    GENTLE fill (~1 req/s, 4 workers) avoids archive.org's throttle without changing the daytime pace.

    Cache semantics (the correctness fix): a confirmed result is cached — the lede *string* for a
    hit, or `false` for a confirmed 'not archived'. A TRANSIENT failure (rate-limit/5xx/timeout) is
    NOT cached, so a re-run retries it instead of recording a permanent miss. Legacy `null` cache
    entries (which conflated the two) are treated as 'retry'. Misses leave `field` unset."""
    global MIN_INTERVAL
    per_cutoff = per_cutoff or {}
    _saved_interval, _saved_workers = MIN_INTERVAL, _ENRICH_WORKERS
    if min_interval is not None:
        MIN_INTERVAL = min_interval
    n_workers = workers or _ENRICH_WORKERS
    cache: dict = {}
    if cache_path and os.path.exists(cache_path):
        cache = json.loads(Path(cache_path).read_text())

    # PASS 1 — collect the unique URLs that still need an archive.org fetch this round.
    need: list[str] = []
    if fetch:
        seen: set[str] = set()
        for a in articles:
            url = a.get("url", "")
            if not url or url in seen or not _needs(a, field):
                continue                               # no url, dup, or already filled (seed)
            k = _ckey(url, per_cutoff.get(url, cutoff))
            if k not in cache or cache.get(k) is None:       # unattempted/legacy-null -> (re)attempt
                seen.add(url); need.append(url)

    # PASS 2 — fetch them CONCURRENTLY. lede() does the slow CDX + snapshot round-trips; _throttle
    # keeps request STARTS rate-capped while the pool overlaps their multi-second latency (the win
    # over the old serial loop). Transient failures aren't cached, so a re-run retries them.
    n_defer = n_new = 0
    deferred: set[str] = set()

    def _flush() -> None:
        """Atomically persist the cache so far (tmp + replace, so a kill mid-write can't corrupt it)."""
        if not cache_path:
            return
        tmp = f"{cache_path}.tmp"
        Path(tmp).write_text(json.dumps(cache))
        os.replace(tmp, cache_path)

    if need:
        t_start = time.monotonic()
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = {ex.submit(lede, url, per_cutoff.get(url, cutoff)): url for url in need}
            for i, fut in enumerate(as_completed(futs), 1):
                url = futs[fut]
                try:
                    res = fut.result()                 # str (hit) | None (confirmed miss)
                except WaybackTransient:
                    n_defer += 1
                    deferred.add(url)                  # don't cache; not tallied as a miss
                except Exception as e:                 # noqa: BLE001
                    # ANY other per-URL failure -- a DNS blip, a malformed snapshot, a decode error on
                    # one archived page -- used to propagate and kill the whole pass. That is an
                    # 11-hour unattended run thrown away by one bad URL, so treat it exactly like a
                    # transient: defer it, leave it uncached, and keep going. Named on stderr so a
                    # systematic fault is still visible rather than silently swallowed.
                    n_defer += 1
                    deferred.add(url)
                    if n_defer <= 20 or n_defer % 500 == 0:
                        print(f"    wayback: deferring {url[:70]} ({type(e).__name__}: {e})",
                              file=sys.stderr, flush=True)
                else:
                    cache[_ckey(url, per_cutoff.get(url, cutoff))] = res if res else False
                    n_new += 1
                # CHECKPOINT + PROGRESS. This pass is the SLOW arm -- measured at ~3.2 items/min, i.e.
                # hours per window -- and it is meant to run unattended overnight. Writing the cache
                # only at the end meant a laptop sleep, a kill, or an archive.org wobble threw away
                # the entire night's work, and offered no way to see how far along it was. Flushing
                # every _CKPT_EVERY results makes the run RESUMABLE for free (a re-run is all cache
                # hits up to the interruption) and the progress line makes it observable.
                if i % _CKPT_EVERY == 0:
                    _flush()
                    el = time.monotonic() - t_start
                    rate = 60 * i / el if el > 0 else 0
                    eta = (len(need) - i) / rate if rate > 0 else 0
                    print(f"    wayback: {i}/{len(need)} ({rate:.1f}/min, ~{eta:.0f} min left, "
                          f"{n_new} resolved, {n_defer} deferred)", file=sys.stderr, flush=True)
        if n_new:
            _flush()

    # PASS 3 — apply cached ledes to snippets and tally (deferred URLs stay unattempted, uncounted).
    n_hit = n_miss = 0
    for a in articles:
        url = a.get("url", "")
        if not url or not _needs(a, field):
            continue
        if url in deferred:
            continue
        cached = cache.get(_ckey(url, per_cutoff.get(url, cutoff)))
        if isinstance(cached, str) and cached:
            a[field] = cached[:max_chars]
            a["lede_source"] = "wayback"               # provenance: look-ahead-CLEAN
            n_hit += 1
        else:
            n_miss += 1                                # confirmed 'not archived' (False) or uncached
    mode = "" if fetch else " [cache-only, no archive.org calls]"
    print(f"  wayback enrich{mode} -> {field}: {n_hit} enriched, {n_miss} not-in-cache/unarchived, "
          f"{n_defer} deferred, {n_new} newly fetched, cutoff<={cutoff}", file=sys.stderr)
    _write_stats(stats_path, cache)
    MIN_INTERVAL = _saved_interval                     # restore: a gentle call must not slow the process
    return articles


def _write_stats(stats_path: str | None, cache: dict) -> None:
    """Write the cumulative wayback section: the 3-way miss split + lede quality (from the cache),
    and the process-cumulative request/error/timing counters (from _STAT)."""
    if not stats_path:
        return
    import retstats
    import statistics
    lens = sorted(len(v) for v in cache.values() if isinstance(v, str) and v)
    lede_n = len(lens)
    no_snap = sum(1 for v in cache.values() if v is False)
    deferred = sum(1 for v in cache.values() if v is None)
    looked = lede_n + no_snap + deferred
    pct = lambda n: round(100 * n / lede_n, 1) if lede_n else 0.0   # noqa: E731
    reqs, wall = _STAT["requests"], _STAT["wall_s"]
    retstats.merge(stats_path, "wayback", {
        "looked_up": looked, "lede": lede_n, "confirmed_no_snapshot": no_snap,
        "transient_deferred": deferred,
        "join_rate_pct": round(100 * lede_n / looked, 1) if looked else 0.0,
        "lede_len_median": int(statistics.median(lens)) if lens else 0,
        "lede_pct_ge50": pct(sum(1 for L in lens if L >= 50)),
        "lede_pct_ge80": pct(sum(1 for L in lens if L >= 80)),
        "lede_pct_ge100": pct(sum(1 for L in lens if L >= 100)),
        "requests": reqs, "http_429": _STAT["http_429"], "http_5xx": _STAT["http_5xx"],
        "timeout": _STAT["timeout"], "elapsed_s": round(wall, 1),
        "items_per_min": round(60 * reqs / wall, 1) if wall > 0 else None,
    })


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
