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

import requests

URL = "https://api.tavily.com/search"
TIMEOUT = 20


def search(query: str, before_date: str | None = None, max_results: int = 5) -> list[dict]:
    """News results for `query`, restricted to those published before `before_date`
    (YYYY-MM-DD) via Tavily's server-side end_date filter. Returns [] if no key/no hits."""
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return []
    body = {"query": query[:400], "topic": "news", "max_results": max_results,
            "search_depth": "basic"}
    if before_date:
        body["end_date"] = str(before_date)[:10]
    try:
        r = requests.post(URL, json=body, headers={"Authorization": f"Bearer {key}"},
                          timeout=TIMEOUT)
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception:  # noqa: BLE001 — a search miss shouldn't sink the ladder; fall back to priors
        return []


def context(query: str, before_date: str | None = None, max_results: int = 5) -> str:
    """A prompt-ready background block from look-ahead-safe search, or "" if nothing found."""
    res = search(query, before_date, max_results)
    if not res:
        return ""
    lines = [f"- {x.get('title', '').strip()}: {x.get('content', '').strip()[:300]}" for x in res]
    return ("Background from a news search restricted to before the catalyst date "
            f"(use only as pre-catalyst context):\n" + "\n".join(lines))
