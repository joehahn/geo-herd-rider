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

import trace

BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
MIN_INTERVAL = 15.0         # GDELT throttles harder than its stated 1 req/5s; 15s gets calls to
                            # SUCCEED on the first try, which is far faster overall than a retry
                            # storm (10s still triggered ~2-3 retries/chunk -> ~43s/chunk).
_last = [0.0]
# retrieval-health counters (process-cumulative; pool() snapshots them per run)
_STAT = {"requests": 0, "http_429": 0, "http_5xx": 0, "timeout": 0, "other_err": 0}


def _reset_stat() -> None:
    for k in _STAT:
        _STAT[k] = 0


def _throttle() -> None:
    dt = time.monotonic() - _last[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _last[0] = time.monotonic()


def _fmt(d) -> str:
    return str(d)[:10].replace("-", "") + "000000"


def search(query: str, start_date, end_date, max_results: int = 60, retries: int = 1,
           english_only: bool = True) -> list[dict]:
    """Date-bounded GDELT news search; `enddatetime` is the enforced look-ahead bound.
    Returns normalized {published_date, source, title, snippet, url, language} (title-level only).

    english_only appends GDELT's `sourcelang:english` operator so foreign-language outlets are
    dropped at fetch time (they're noise for a US-listed-equities curator and archive poorly)."""
    q = f"{query} sourcelang:english" if english_only else query
    params = {"query": q, "mode": "ArtList", "format": "json", "maxrecords": max_results,
              "startdatetime": _fmt(start_date), "enddatetime": _fmt(end_date), "sort": "datedesc"}
    arts = []
    for _ in range(retries + 1):
        _throttle()
        _STAT["requests"] += 1
        try:
            r = requests.get(BASE, params=params, timeout=30,
                             headers={"User-Agent": "geo-herd-rider/1.0"})
            if r.status_code == 429:
                _STAT["http_429"] += 1
            elif r.status_code >= 500:
                _STAT["http_5xx"] += 1
            if r.headers.get("content-type", "").startswith("application/json"):
                arts = r.json().get("articles", []) or []
                break
        except requests.exceptions.Timeout:
            _STAT["timeout"] += 1
        except Exception:  # noqa: BLE001 — a miss shouldn't sink the backtest
            _STAT["other_err"] += 1
        time.sleep(MIN_INTERVAL)   # rate-limit text / transient error -> back off and retry
    out = []
    for a in arts:
        sd = str(a.get("seendate", ""))
        pub = f"{sd[0:4]}-{sd[4:6]}-{sd[6:8]}" if len(sd) >= 8 else ""
        if not pub or not a.get("url"):
            continue
        out.append({"published_date": pub, "source": a.get("domain", ""),
                    "title": a.get("title", ""), "snippet": a.get("title", ""),
                    "url": a.get("url", ""), "language": a.get("language", "")})
    trace.log("search", engine="gdelt", query=query, start=str(start_date)[:10],
              end=str(end_date)[:10], n_results=len(out))
    return out


def pool(queries: list[str], start, end, chunk_days: int = 30, per: int = 60,
         cache_path=None, english_only: bool = True, stats_path: str | None = None) -> list[dict]:
    """Deduped article pool across queries, fetched in date chunks for even time coverage
    (datedesc + a record cap would otherwise over-weight the latest weeks).

    If `cache_path` is given, the pool is checkpointed after EVERY (query, chunk) — so a long
    throttled fetch survives interruption (laptop sleep, kill) and RESUMES from where it left
    off on the next call. Atomic writes (tmp + replace) avoid a corrupt half-file."""
    import json
    import os
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    edges = list(pd.date_range(start, end, freq=f"{chunk_days}D"))
    if not edges or edges[-1] < end:
        edges.append(end)

    _reset_stat()
    t0 = time.monotonic()
    from_cache = False
    seen: dict[str, dict] = {}
    done: set[str] = set()
    if cache_path and os.path.exists(cache_path):
        data = json.loads(open(cache_path).read())
        if isinstance(data, list):           # legacy complete pool (old format) — reuse as-is
            _write_stats(stats_path, list(data), 0.0, from_cache=True)
            return data
        seen = {a["url"]: a for a in data.get("articles", [])}
        done = set(data.get("done", []))
        from_cache = bool(done)

    total = len(queries) * (len(edges) - 1)
    for qi, q in enumerate(queries):
        for ci in range(len(edges) - 1):
            kk = f"{qi}:{ci}"
            if kk in done:
                continue
            from_cache = False
            for a in search(q, edges[ci], edges[ci + 1], per, english_only=english_only):
                seen.setdefault(a["url"], a)
            done.add(kk)
            if cache_path:
                tmp = f"{cache_path}.tmp"
                with open(tmp, "w") as fh:
                    json.dump({"articles": list(seen.values()), "done": sorted(done),
                               "progress": f"{len(done)}/{total}"}, fh)
                os.replace(tmp, cache_path)
    arts = list(seen.values())
    _write_stats(stats_path, arts, time.monotonic() - t0, from_cache=from_cache)
    return arts


def _write_stats(stats_path: str | None, arts: list[dict], elapsed: float, from_cache: bool) -> None:
    if not stats_path:
        return
    import retstats
    n = len(arts)
    non_en = sum(1 for a in arts if a.get("language") and a.get("language") != "English")
    reqs = _STAT["requests"]
    retstats.merge(stats_path, "gdelt", {
        "items": n, "non_english": non_en,
        "non_english_pct": round(100 * non_en / n, 1) if n else 0.0,
        "requests": reqs, "http_429": _STAT["http_429"], "http_5xx": _STAT["http_5xx"],
        "timeout": _STAT["timeout"], "other_err": _STAT["other_err"],
        "elapsed_s": round(elapsed, 1),
        "items_per_min": round(60 * n / elapsed, 1) if elapsed > 0 else None,
        "from_cache": from_cache,
    })
