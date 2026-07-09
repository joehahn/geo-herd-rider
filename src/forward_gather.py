"""forward_gather.py — the LIVE firehose gather for the forward paper trade.

Anthropic adaptive web search (goal-directed: the financial press naming specific tickers/ETFs as
movers) -> a DATE-CLEAN, window-filtered pool of articles the event-first scout then reads. This is
the live equivalent of the backtest's GDELT pool.

Look-ahead hygiene (non-negotiable #4): each article's publish date is extracted from its page
(HTML meta -> URL), and the pool is bounded to (anchor - lookback, anchor]. An article that is
future-dated OR whose date can't be parsed is DROPPED — fail closed, like search.py, so a
`before:`-leak (Anthropic returns post-cutoff articles) can't contaminate the scout.
"""
from __future__ import annotations

import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pandas as pd

import costs
import trace
import wayback

# The gather's fixed coverage sweep MIRRORS the backtest's GDELT beats (firehose.GDELT_QUERIES) so the
# forward firehose searches the SAME universe — deterministic sector coverage + backtest parity. The
# model runs these as the base sweep, THEN spawns adaptive follow-ups on the specific names it surfaces.
GATHER_SYSTEM = (
    "You are the news firehose assembling this week's coverage for a downstream scout. Your ONLY job is "
    "to SURFACE articles where the financial press NAMES a specific US-listed stock, ETF, or ADR as a "
    "notable mover on a catalyst — do NOT decide which are the best gems, and do NOT run an unbounded "
    "number of searches.\n"
    "COVERAGE — run ONE web search for EACH of these beats so no sector is missed (this is the base sweep):\n"
    "  superlatives: 'best performing stock', 'biggest gainers', 'best performing ETF'\n"
    "  macro:        geopolitics, war, shipping, tariffs, 'interest rates'\n"
    "  sectors:      technology / energy / financial / healthcare / industrial / materials / consumer / "
    "utility / real estate / telecom stocks\n"
    "  themes:       cryptocurrency, 'space stocks', 'robotics stocks', 'quantum stocks', 'nuclear stocks'\n"
    "  early:        'under the radar' stock, 'flying under the radar' ETF, 'overlooked' stock catalyst, "
    "'still early' rally, niche ETF surging\n"
    "THEN ADAPT: when a beat surfaces a specific named mover or catalyst, spawn a FEW targeted follow-up "
    "searches on that name/catalyst to pull the articles that explicitly name the ticker. Aim for ~25-45 "
    "searches total. Cap every search to news on/before the week-ending date."
)

_UA = {"User-Agent": "Mozilla/5.0 (geo-herd-rider forward gather)"}
# publish-date signals in article HTML, most-reliable first
_META_DATE = [
    r'article:published_time"\s+content="([0-9]{4}-[0-9]{2}-[0-9]{2})',
    r'"datePublished"\s*:\s*"([0-9]{4}-[0-9]{2}-[0-9]{2})',
    r'property="og:updated_time"\s+content="([0-9]{4}-[0-9]{2}-[0-9]{2})',
    r'name="(?:date|publishdate|pubdate|dc.date)"\s+content="([0-9]{4}-[0-9]{2}-[0-9]{2})',
    r'<time[^>]+datetime="([0-9]{4}-[0-9]{2}-[0-9]{2})',
]
_URL_DATE = re.compile(r"/(20\d\d)[/-](\d{1,2})[/-](\d{1,2})(?:[/-]|\b)")


def _extract_date(html: str, url: str) -> str | None:
    """Best-effort publish date (YYYY-MM-DD) from the page HTML, then the URL path. None if neither."""
    for pat in _META_DATE:
        m = re.search(pat, html or "", re.I)
        if m:
            return m.group(1)
    m = _URL_DATE.search(url or "")
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return None


def _freeze(url: str) -> tuple[str, str | None]:
    """Fetch the live article once -> (lede, published_date). Both best-effort; a fetch miss -> ('', None)."""
    try:
        req = urllib.request.Request(url, headers=_UA)
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return "", None
    return (wayback._extract_lede(html) or ""), _extract_date(html, url)


def _run_search(client, model: str, anchor: pd.Timestamp, posts_block: str) -> dict:
    """Anthropic adaptive web-search gather; return {'queries':[...], 'results':[{url,title,page_age}]}."""
    user = (f"Week ending {anchor.date()} (use before:{anchor.date()} on every search).\n{posts_block}"
            "Run the beat sweep, then a few targeted follow-ups, to surface this week's articles that "
            "NAME specific US-listed tickers/ETFs/ADRs as movers.")
    kw = {"model": model, "max_tokens": 1500, "system": GATHER_SYSTEM,
          "tools": [{"type": "web_search_20260209", "name": "web_search"}],
          "messages": [{"role": "user", "content": user}]}
    queries: list[str] = []
    results: dict[str, dict] = {}
    tally = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "web_searches": 0}
    for _ in range(6):
        resp = client.messages.create(**kw)
        u = costs.extract(resp.usage)
        for k in tally:
            tally[k] += u.get(k, 0)
        for b in resp.content:
            if b.type == "server_tool_use" and getattr(b, "name", "") == "web_search":
                q = (getattr(b, "input", None) or {}).get("query")
                if q:
                    queries.append(str(q))
            elif b.type == "web_search_tool_result" and isinstance(getattr(b, "content", None), list):
                for r in b.content:
                    if getattr(r, "type", "") == "web_search_result" and getattr(r, "url", None):
                        results.setdefault(r.url, {"url": r.url, "title": getattr(r, "title", ""),
                                                   "page_age": getattr(r, "page_age", None)})
        if resp.stop_reason == "pause_turn":
            kw["messages"].append({"role": "assistant", "content": resp.content})
            continue
        break
    costs.record("forward-gather", model, f"gather-{anchor.date()}", tally)   # ALL forward spend is logged
    trace.log("llm", stage="forward-gather", label=f"gather-{anchor.date()}", model=model,
              system=GATHER_SYSTEM, user=user,
              response=f"[gather: {len(queries)} searches -> {len(results)} results]",
              web_search_queries=queries, **tally)
    for _tq in queries:
        trace.log("search", engine="anthropic", query=_tq)
    return {"queries": queries, "results": list(results.values())}


def _url_date(url: str) -> str | None:
    """Publish date from the URL path alone (no fetch), e.g. /2026/07/07/ -> 2026-07-07. None if absent."""
    m = _URL_DATE.search(url or "")
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def gather(client, model: str, anchor: pd.Timestamp, lookback_days: int, capture: dict | None = None,
           workers: int = 12, cap: int = 80, freeze_cap: int = 160) -> list[dict]:
    """Live firehose gather -> a date-clean, window-filtered arts pool for the scout.

    client: a raw anthropic.Anthropic() (web search is Anthropic-only). Returns arts sorted
    newest-first, capped to `cap`. Fills `capture` (raw queries + all results) for the Phase-B archive.

    A gather can return 1000+ results; fetching every one to date+freeze it is far too slow. So we
    first TRIAGE by URL date (no fetch): drop anything whose URL date is confirmably out of window,
    keep in-window (priority) + undated (need a fetch to decide), cap at `freeze_cap`, and only then
    fetch/freeze that subset. The full window filter still runs on the fetched dates (fail closed).
    """
    raw = _run_search(client, model, anchor, "")
    lo = (anchor - pd.Timedelta(days=lookback_days)).date().isoformat()
    hi = anchor.date().isoformat()

    triaged = []                                   # (priority, result): 0 = url-date in window, 1 = undated
    for r in raw["results"]:
        d = _url_date(r["url"])
        if d is None:
            triaged.append((1, r))                 # undated -> must fetch to decide
        elif lo < d <= hi:
            triaged.append((0, r))                 # in-window by URL -> priority
        # else: URL date is out of window (stale or future leak) -> DROP without fetching
    triaged.sort(key=lambda t: t[0])
    survivors = [r for _, r in triaged[:freeze_cap]]

    def build(r):
        lede, date = _freeze(r["url"])
        return {"title": r.get("title", ""), "url": r["url"], "published_date": date or "",
                "source": urlparse(r["url"]).netloc, "snippet": lede}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        built = list(ex.map(build, survivors))

    # FAIL CLOSED: keep only articles with a parseable date INSIDE the window (lo, hi]. Undateable or
    # future-dated (the before:-leak) are dropped — never leak an unconfirmable article to the scout.
    kept = [a for a in built if a["published_date"] and lo < a["published_date"][:10] <= hi]
    kept.sort(key=lambda a: a["published_date"], reverse=True)
    result = kept[:cap]
    if capture is not None:
        kept_urls = {a["url"] for a in result}
        capture["queries"] = raw["queries"]
        capture["arts"] = result                              # the FROZEN in-window pool (archive reuses it — no re-fetch)
        capture["results"] = [{**r, "in_window": r["url"] in kept_urls} for r in raw["results"]]
    return result
