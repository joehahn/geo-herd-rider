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

import wayback

GATHER_SYSTEM = (
    "You are the news firehose. Search the web for the given week's financial-press coverage that "
    "NAMES specific stocks, ETFs, or ADRs as notable movers — biggest gainers, standout trades, names "
    "surging or sinking on a catalyst (a war/chokepoint, an export ban or tariff, a named bill, a "
    "regulatory/agency action, a supply shock, a deal, an earnings/vote/ruling event). Search "
    "EXTENSIVELY and adaptively: run many queries across sectors, and when a result names a mover, "
    "spawn follow-up searches on that name/catalyst to surface the articles that explicitly name the "
    "ticker. Do NOT decide which are the best gems — only SURFACE every article where the press names a "
    "ticker as a mover, for a downstream scout to curate. Cap every search to news on/before the "
    "week-ending date."
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
            "Find this week's financial-press articles that NAME specific tickers/ETFs/ADRs as movers, "
            "across every sector. Surface as many such ticker-naming articles as you can.")
    kw = {"model": model, "max_tokens": 1500, "system": GATHER_SYSTEM,
          "tools": [{"type": "web_search_20260209", "name": "web_search"}],
          "messages": [{"role": "user", "content": user}]}
    queries: list[str] = []
    results: dict[str, dict] = {}
    for _ in range(6):
        resp = client.messages.create(**kw)
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
    return {"queries": queries, "results": list(results.values())}


def gather(client, model: str, anchor: pd.Timestamp, lookback_days: int,
           capture: dict | None = None, workers: int = 8, cap: int = 80) -> list[dict]:
    """Live firehose gather -> a date-clean, window-filtered arts pool for the scout.

    client: a raw anthropic.Anthropic() (web search is Anthropic-only). Returns arts sorted
    newest-first, capped to `cap`. Fills `capture` (raw queries + all results) for the Phase-B archive.
    """
    raw = _run_search(client, model, anchor, "")
    lo = (anchor - pd.Timedelta(days=lookback_days)).date().isoformat()
    hi = anchor.date().isoformat()

    def build(r):
        lede, date = _freeze(r["url"])
        return {"title": r.get("title", ""), "url": r["url"], "published_date": date or "",
                "source": urlparse(r["url"]).netloc, "snippet": lede}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        built = list(ex.map(build, raw["results"]))

    # FAIL CLOSED: keep only articles with a parseable date INSIDE the window (lo, hi]. Undateable or
    # future-dated (the before:-leak) are dropped — never leak an unconfirmable article to the scout.
    kept = [a for a in built if a["published_date"] and lo < a["published_date"][:10] <= hi]
    kept.sort(key=lambda a: a["published_date"], reverse=True)
    if capture is not None:
        capture["queries"] = raw["queries"]
        capture["results"] = [{**r, "in_window": any(a["url"] == r["url"] for a in kept)}
                              for r in raw["results"]]
    return kept[:cap]
