"""search.py — look-ahead-safe web search for the non-Anthropic ladder (Tavily free tier).

OpenRouter models have no built-in, date-controllable web search, and the bake-off showed web
search is the biggest lever in the ladder (Opus collapses to ~SPY without it). This gives the
cheap path a real one: Tavily's `end_date` filters results to those published BEFORE the
catalyst, so the hard look-ahead guarantee holds server-side — the same discipline as
Anthropic's `before:<date>`. Free tier (~1000 credits/month); needs TAVILY_API_KEY.

Honest caveat: searching the live web for a PAST event still surfaces less pre-catalyst
material than a true point-in-time archive would, so historical backtests stay an upper bound.
Tavily is cleanest in FORWARD use, where "search now for a just-happened event" is exactly
look-ahead-correct — which is the project's clean test anyway.
"""
from __future__ import annotations

import os
from email.utils import parsedate_to_datetime

import requests

URL = "https://api.tavily.com/search"
TIMEOUT = 20


def _published_after(result: dict, after_date: str) -> bool:
    """True if the result's published_date is on/after after_date (YYYY-MM-DD); undateable -> False."""
    raw = result.get("published_date") or ""
    try:
        return parsedate_to_datetime(raw).date().isoformat() >= str(after_date)[:10]
    except Exception:  # noqa: BLE001
        try:
            return str(raw)[:10] >= str(after_date)[:10]
        except Exception:  # noqa: BLE001
            return False


def _published_before(result: dict, before_date: str) -> bool:
    """True if the result's published_date is on/before before_date (YYYY-MM-DD).

    Tavily's server-side end_date is NOT reliably honored (it returns post-cutoff articles),
    so we re-enforce the look-ahead bound CLIENT-SIDE off each result's published_date. A
    result with no parseable date is DROPPED — fail closed, never leak an undateable article."""
    raw = result.get("published_date") or ""
    try:
        dt = parsedate_to_datetime(raw)            # RFC-2822, e.g. "Sat, 25 Apr 2026 11:30:01 GMT"
        return dt.date().isoformat() <= str(before_date)[:10]
    except Exception:  # noqa: BLE001
        try:
            return str(raw)[:10] <= str(before_date)[:10]   # ISO fallback
        except Exception:  # noqa: BLE001
            return False


def search(query: str, before_date: str | None = None, max_results: int = 5,
           start_date: str | None = None) -> list[dict]:
    """News results for `query`, restricted to those published on/before `before_date`
    (YYYY-MM-DD). Tavily's server-side end_date leaks future articles, so we over-fetch and
    enforce the bound client-side off published_date. Returns [] if no key/no hits."""
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return []
    # over-fetch when filtering: the early "under-the-radar" pieces rank below the later
    # blockbuster coverage, so we need a deep pull to recover any that pre-date the cutoff.
    pull = max(max_results * 4, 20) if before_date else max_results
    body = {"query": query[:400], "topic": "news", "max_results": pull,
            "search_depth": "basic"}
    if before_date:
        body["end_date"] = str(before_date)[:10]   # belt-and-suspenders; not trusted alone
    if start_date:
        body["start_date"] = str(start_date)[:10]
    try:
        r = requests.post(URL, json=body, headers={"Authorization": f"Bearer {key}"},
                          timeout=TIMEOUT)
        r.raise_for_status()
        res = r.json().get("results", [])
    except Exception:  # noqa: BLE001 — a search miss shouldn't sink the ladder; fall back to priors
        return []
    if before_date:
        res = [x for x in res if _published_before(x, before_date)]
    if start_date:
        res = [x for x in res if _published_after(x, start_date)]
    return res[:max_results]


def context(query: str, before_date: str | None = None, max_results: int = 5) -> str:
    """A prompt-ready background block from look-ahead-safe search, or "" if nothing found."""
    res = search(query, before_date, max_results)
    if not res:
        return ""
    lines = [f"- {x.get('title', '').strip()}: {x.get('content', '').strip()[:300]}" for x in res]
    return ("Background from a news search restricted to before the catalyst date "
            f"(use only as pre-catalyst context):\n" + "\n".join(lines))
