"""forward_gather_tavily.py — Tavily-backed firehose gather (opt-in alt to Anthropic/Brave).

Same `gather(...)` interface as `forward_gather`, but built on Tavily's DATE-BOUNDED news search
(`search.py`) instead of the Anthropic `web_search` tool. Deterministic (a fixed beat sweep, no LLM
to drive it), free (Tavily tier), and — unlike Anthropic/Brave, which ignore query-string date
operators — it actually honors a date range, so it can reach OLD weeks for backfill.

Forward DEFAULT stays Anthropic/Brave; this is opt-in (`--gather tavily` / `gather_engine: tavily`),
mainly for the throwaway multi-week backfill that proves the pipeline + feeds the dashboard.
"""
from __future__ import annotations

import concurrent.futures as cf
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import pandas as pd

import search
import trace
# ALIGNED with the Anthropic forward gather: import the SAME beat set + domain lists so the Tavily
# backtest runs the identical two-pass sweep (gem beats + specialty allowlist; coverage beats + mill
# blocklist). This is what makes the backtest a valid proxy for forward retrieval — same queries,
# same domain steering, only the engine differs. (Residual gap: Anthropic spawns adaptive follow-ups;
# Tavily runs the fixed list -> conservative proxy.)
from forward_gather import GEM_BEATS, COVERAGE_BEATS, _SPECIALTY_ALLOW, _MILL_BLOCK

BEATS = list(GEM_BEATS) + list(COVERAGE_BEATS)   # full sweep, for capture/reporting
# (beat, include_domains, exclude_domains) — mirrors forward_gather's two passes
_TASKS = ([(b, _SPECIALTY_ALLOW, None) for b in GEM_BEATS]
          + [(b, None, _MILL_BLOCK) for b in COVERAGE_BEATS])


def _pdate(r: dict) -> str | None:
    raw = r.get("published_date") or ""
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except Exception:  # noqa: BLE001
        return str(raw)[:10] if str(raw)[:4].isdigit() else None


def gather(client, model: str, anchor: pd.Timestamp, lookback_days: int, capture: dict | None = None,
           workers: int = 8, cap: int = 80, freeze_cap: int = 160, dated: bool = False) -> list[dict]:
    """Date-bounded Tavily beat sweep -> a window-filtered arts pool for the scout. `client`/`model`/
    `dated`/`freeze_cap` are accepted for interface parity with forward_gather but unused (Tavily needs
    no LLM to drive it, and its date range is a real server-side filter)."""
    lo = (anchor - pd.Timedelta(days=lookback_days)).date().isoformat()
    hi = anchor.date().isoformat()
    pool: dict[str, dict] = {}

    def _q(task: tuple):
        beat, inc, exc = task
        return beat, search.search(beat, before_date=hi, start_date=lo, max_results=8,
                                   include_domains=inc, exclude_domains=exc)

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for beat, res in ex.map(_q, _TASKS):
            trace.log("search", engine="tavily", query=beat, n_results=len(res))
            for r in res:
                d = _pdate(r)
                url = r.get("url")
                if url and d and lo < d <= hi:          # (anchor-lookback, anchor], fail closed on undateable
                    ex_r = pool.setdefault(url, {"title": r.get("title", ""), "url": url, "published_date": d,
                                                 "source": urlparse(url).netloc,
                                                 "snippet": (r.get("content", "") or "")[:300], "queries": []})
                    if beat not in ex_r["queries"]:     # tag which beat(s) surfaced it (Plot 13/14 attribution)
                        ex_r["queries"].append(beat)

    arts = sorted(pool.values(), key=lambda x: x["published_date"], reverse=True)[:cap]
    if capture is not None:
        capture["arts"] = arts
        capture["queries"] = list(BEATS)
        capture["results"] = [{"url": a["url"], "title": a["title"],
                               "published_date": a["published_date"], "in_window": True} for a in arts]
    print(f"  tavily gather {hi}: {len(arts)} in-window articles", flush=True)
    return arts
