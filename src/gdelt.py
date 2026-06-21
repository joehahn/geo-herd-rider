"""gdelt.py — look-ahead-clean historical news retrieval (GDELT 2.0 Doc API).

The free, date-honoring firehose for BACKTESTING (the discipline-#6 dev loop, fast bug-hunting —
forward is too slow to iterate against). Unlike Tavily / Anthropic web_search, which silently
ignore date bounds, GDELT's `startdatetime`/`enddatetime` are enforced server-side: a query
as-of a past week returns only articles GDELT had seen by then — real point-in-time retrieval.

Honest caveats (both proven): GDELT (1) under-indexes niche trade press, so it MISSES the early
"under-the-radar" pieces that carry the alpha — those are seeded separately in the backtest; and
(2) returns headline-level records only (no body). No API key; rate-limited to ~1 request / 5s.
"""
from __future__ import annotations

import time

import pandas as pd
import requests

BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
MIN_INTERVAL = 6.0          # GDELT asks for <= 1 request / 5s; give it a little room
_last = [0.0]


def _throttle() -> None:
    dt = time.monotonic() - _last[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _last[0] = time.monotonic()


def _fmt(d) -> str:
    return str(d)[:10].replace("-", "") + "000000"


def search(query: str, start_date, end_date, max_results: int = 60, retries: int = 2) -> list[dict]:
    """Date-bounded GDELT news search; `enddatetime` is the enforced look-ahead bound.
    Returns normalized {published_date, source, title, snippet, url} (title-level only)."""
    params = {"query": query, "mode": "ArtList", "format": "json", "maxrecords": max_results,
              "startdatetime": _fmt(start_date), "enddatetime": _fmt(end_date), "sort": "datedesc"}
    arts = []
    for _ in range(retries + 1):
        _throttle()
        try:
            r = requests.get(BASE, params=params, timeout=30,
                             headers={"User-Agent": "geo-herd-rider/1.0"})
            if r.headers.get("content-type", "").startswith("application/json"):
                arts = r.json().get("articles", []) or []
                break
        except Exception:  # noqa: BLE001 — a miss shouldn't sink the backtest
            arts = []
        time.sleep(MIN_INTERVAL)   # rate-limit text / transient error -> back off and retry
    out = []
    for a in arts:
        sd = str(a.get("seendate", ""))
        pub = f"{sd[0:4]}-{sd[4:6]}-{sd[6:8]}" if len(sd) >= 8 else ""
        if not pub or not a.get("url"):
            continue
        out.append({"published_date": pub, "source": a.get("domain", ""),
                    "title": a.get("title", ""), "snippet": a.get("title", ""),
                    "url": a.get("url", "")})
    return out


def pool(queries: list[str], start, end, chunk_days: int = 30, per: int = 60) -> list[dict]:
    """Deduped article pool across queries, fetched in date chunks for even time coverage
    (datedesc + a record cap would otherwise over-weight the latest weeks)."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    edges = list(pd.date_range(start, end, freq=f"{chunk_days}D"))
    if not edges or edges[-1] < end:
        edges.append(end)
    seen: dict[str, dict] = {}
    for q in queries:
        for i in range(len(edges) - 1):
            for a in search(q, edges[i], edges[i + 1], per):
                seen.setdefault(a["url"], a)
    return list(seen.values())
