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
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

import costs
import trace
import wayback
from optimizer import load_financial_model

# forward firehose = a TWO-PASS gather (validated 2026-07-10: web_search allowed_domains works AND reaches
# etf.com despite its Cloudflare wall — the search index isn't blocked like a scraper). Pass 1 (GEM) runs
# the early-framing + catalyst->beneficiary beats RESTRICTED to specialty desks via allowed_domains, so the
# gem-class niche coverage GDELT couldn't reach is forced to the top. Pass 2 (COVERAGE) runs the broad
# sector sweep unrestricted but with blocked_domains killing the "N stocks to buy" listicle mills. Domain-
# steering is a far stronger lever than prompt wording (an A/B of soft prioritization barely moved).
# allowed/blocked_domains are TOOL-level (apply to every search in a call) -> hence two separate passes.

# The steering lists live in investor_profile (specialty_allow / mill_block) for VISIBILITY — one place to
# see what the firehose steers to; optimizer._FINANCIAL_MODEL_DEFAULTS is the fallback. Curate by OUTLET
# TYPE (specialty desk vs listicle mill), NEVER by "this outlet named a winner" (that's leaked-signal tuning).
_FGM = load_financial_model(str(Path(__file__).resolve().parent.parent / "investor_profile.forward.md"))
_SPECIALTY_ALLOW = list(_FGM.get("specialty_allow") or [])   # GEM pass allowlist (reaches Cloudflare-walled etf.com)
_MILL_BLOCK = list(_FGM.get("mill_block") or [])             # COVERAGE pass blocklist (kills listicle mills)

# SHARED BEAT SET — the SINGLE SOURCE OF TRUTH for BOTH engines, so the Tavily backtest is a valid proxy
# for the Anthropic forward (SAME queries, different engine). Phrased as plain natural-language (no boolean
# OR / quotes) so the Anthropic model AND Tavily's semantic search run them the same way. forward_gather_tavily
# imports these + the domain lists to run the identical two-pass sweep. GEM beats -> allowlist pass;
# COVERAGE beats -> blocklist pass. The ONLY residual gap: Anthropic also spawns adaptive follow-ups (Tavily
# runs the fixed list only) -> the backtest is a valid but CONSERVATIVE proxy (under-finds vs the forward).
GEM_BEATS = [
    # early-framing (still-under-the-radar) — trimmed 6->3 (the set was ~60% self-redundant in the backtest;
    # keep one stock / catalyst / ETF framing each — none is load-bearing for any gem's key article)
    "under the radar small cap stock", "overlooked stock catalyst", "niche ETF surging",
    # catalyst -> named beneficiary (a discrete datable event and the ticker it lifts)
    "war chokepoint stock beneficiary", "export ban tariff sanctions stock beneficiary",
    "supply shortage supply shock stock", "rare earth critical minerals stock",
    "memory chip DRAM shortage stock",       # dropped "tanker shipping freight rates ETF" 2026-07-12:
    # redundant (17% unique, all macro noise / 0 gems) + off-target (pulled uranium/supply not tanker);
    # tanker/BWET coverage lives in "shipping maritime stocks" + "niche ETF surging" + the ETF-superlative beat
    "uranium nuclear fuel supply squeeze stock", "upcoming FDA election vote stock anticipation",
]
COVERAGE_BEATS = [
    "technology stocks", "energy stocks", "financial stocks", "healthcare stocks", "industrial stocks",
    "materials stocks", "consumer stocks", "utility stocks", "real estate stocks", "telecom stocks",
    "shipping maritime stocks", "cryptocurrency stocks", "space stocks", "robotics stocks",
    "quantum stocks", "nuclear stocks",
    # added to close coverage gaps the backtest exposed (no defense beat drove RNMBY under-coverage;
    # AI/biotech had specialty desks in the allowlist but no beat steering to them)
    "defense aerospace stocks", "artificial intelligence semiconductor stocks", "biotech pharma stocks",
    # gold was diluted under "materials" (0.7% of pool, hurt GDX recall) -> dedicated gold + silver beats
    # (HL's silver-rally coverage was ~20 of the missed target squares)
    "gold silver mining stocks", "silver mining stocks",
    # supply/demand-shock clusters where dramatic gems appear but the broad "materials/energy" beats were
    # too generic to rank: logistics BEYOND maritime; industrial base metals (tariff/EV/AI-demand movers,
    # CLF-class); energy distribution + the AI-datacenter power-demand grid theme
    "freight trucking rail air cargo logistics stocks",
    "steel copper lithium aluminum coal mining stocks",
    "electric power grid pipeline energy infrastructure stocks",
    # superlative framing — the missed target squares ARE superlatives ("Surges to All-Time High",
    # "Skyrockets 380%", "Rockets to record"); a superlative beat targets that exact class
    "stock surges skyrockets all-time high record",
    # ETF-variant beats (several gems ARE ETFs: GDX/BWET/DRAM) with superlative framing — the wrappers'
    # coverage is "best-performing / skyrocketing / little-known ETF" (BWET, DRAM) which the "stocks" beats miss
    "best performing ETF little-known skyrocketing surging",
    "gold silver miners ETF surging record high",
    # thematic-sector ETF beats — the sectors that spawn hot single-theme ETF gems (like GDX/BWET/DRAM).
    # Forward-BREADTH insurance: backtest recall won't move (no other ETF gem in the GT to catch), but this
    # positions the live forward to catch the next thematic ETF the moment it's named. Boring index sectors
    # (utility/telecom/consumer/real-estate/financial) deliberately skipped — they don't spawn ETF gems.
    "nuclear uranium ETF surging", "robotics automation ETF surging", "quantum computing ETF surging",
    "space defense ETF surging", "crypto blockchain ETF surging", "semiconductor memory chip ETF surging",
    "best performing stock", "biggest stock gainers",
]
GEM_SYSTEM = (
    "You are the news firehose surfacing EARLY, still-under-the-radar gem-class coverage for a scout — the "
    "press naming a specific US-listed stock, ETF, or ADR on a discrete catalyst BEFORE the crowd. Run ONE "
    "web search for EACH of these beats; do not skip any:\n  " + " | ".join(GEM_BEATS) + "\n"
    "THEN spawn a FEW targeted follow-ups on each specific name/catalyst that surfaces, to pull the article "
    "that explicitly names the ticker. Cap every search to news on/before the week-ending date."
)
COVERAGE_SYSTEM = (
    "You are the news firehose running the broad sector sweep so no theme is missed. Surface articles where "
    "the press NAMES a specific US-listed stock, ETF, or ADR as a mover on a catalyst. Run ONE web search "
    "for EACH of these beats:\n  " + " | ".join(COVERAGE_BEATS) + "\n"
    "THEN a FEW targeted follow-ups on names that surface. Cap every search to news on/before the week-ending date."
)


def merge_pools(*pools) -> list[dict]:
    """Union article pools from multiple gather engines (Anthropic + Tavily), deduped by URL so their
    COMPLEMENTARY reach combines: Anthropic reaches Cloudflare-walled etf.com; Tavily reaches the Dow Jones
    sites (WSJ / MarketWatch / Investors.com) that block Anthropic's crawler. Query tags are merged."""
    seen: dict[str, dict] = {}
    for p in pools:
        for a in p or []:
            u = a.get("url")
            if not u:
                continue
            if u not in seen:
                seen[u] = a
            else:                                    # same URL from both engines -> merge beat tags
                for q in a.get("queries", []):
                    if q not in seen[u].setdefault("queries", []):
                        seen[u]["queries"].append(q)
    return list(seen.values())

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


def _run_search(client, model: str, anchor: pd.Timestamp, system: str, tool: dict,
                label: str, posts_block: str = "") -> dict:
    """One Anthropic adaptive web-search pass under `system` + `tool` (its allowed/blocked_domains set the
    domain steering); returns {'queries':[...], 'results':[{url,title,page_age}]}. `label` tags cost/trace."""
    user = (f"Week ending {anchor.date()} (use before:{anchor.date()} on every search).\n{posts_block}"
            "Run the beat sweep, then a few targeted follow-ups, to surface this week's articles that "
            "NAME specific US-listed tickers/ETFs/ADRs as movers.")
    kw = {"model": model, "max_tokens": 1500, "system": system,
          "tools": [tool],
          "messages": [{"role": "user", "content": user}]}
    queries: list[str] = []
    results: dict[str, dict] = {}
    _curq: str | None = None                        # the query whose results are currently streaming back
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
                    _curq = str(q)
                    queries.append(_curq)
            elif b.type == "web_search_tool_result" and isinstance(getattr(b, "content", None), list):
                for r in b.content:
                    if getattr(r, "type", "") == "web_search_result" and getattr(r, "url", None):
                        ex = results.setdefault(r.url, {"url": r.url, "title": getattr(r, "title", ""),
                                                        "page_age": getattr(r, "page_age", None), "queries": []})
                        if _curq and _curq not in ex["queries"]:   # tag each result with the search(es) that surfaced it
                            ex["queries"].append(_curq)
        if resp.stop_reason == "pause_turn":
            kw["messages"].append({"role": "assistant", "content": resp.content})
            continue
        break
    costs.record("forward-gather", model, f"{label}-{anchor.date()}", tally)   # ALL forward spend is logged
    trace.log("llm", stage="forward-gather", label=f"{label}-{anchor.date()}", model=model,
              system=system, user=user,
              response=f"[{label}: {len(queries)} searches -> {len(results)} results]",
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


def _page_age_date(page_age, ref: pd.Timestamp | None = None) -> str | None:
    """The web_search result's `page_age` -> YYYY-MM-DD (no fetch). This is the date source for Cloudflare-
    walled specialty desks (etf.com) that 403 the fetch AND have no URL date — without it the fail-closed
    filter drops the very gem-class articles the allowlist surfaced. Handles BOTH forms page_age comes in:
    absolute ('March 4, 2026') and relative ('3 days ago', 'yesterday'), the latter resolved against `ref`
    (defaults to today — correct for the live daily pull, which runs ~at the anchor). Returns None if
    unparseable; the (lo, hi] window filter still enforces look-ahead downstream."""
    if not page_age:
        return None
    s0 = str(page_age).strip().lower()
    ref = (ref or pd.Timestamp.today()).normalize()
    if s0 in ("today", "just now", "now"):
        return ref.date().isoformat()
    if s0 == "yesterday":
        return (ref - pd.Timedelta(days=1)).date().isoformat()
    m = re.match(r"(\d+)\s+(hour|day|week|month|year)s?\s+ago", s0)
    if m:
        n = int(m.group(1))
        delta = {"hour": pd.Timedelta(hours=n), "day": pd.Timedelta(days=n), "week": pd.Timedelta(weeks=n),
                 "month": pd.Timedelta(days=30 * n), "year": pd.Timedelta(days=365 * n)}[m.group(2)]
        return (ref - delta).date().isoformat()
    try:
        d = pd.to_datetime(str(page_age), errors="coerce")
    except Exception:  # noqa: BLE001
        return None
    if pd.isna(d):
        return None
    s = d.date().isoformat()
    return s if "2000-01-01" < s <= ref.date().isoformat() else None


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
    _WS = "web_search_20260209"
    gem = _run_search(client, model, anchor, GEM_SYSTEM,                       # pass 1: specialty-allowlisted gem sweep
                      {"type": _WS, "name": "web_search", "max_uses": 24, "allowed_domains": _SPECIALTY_ALLOW}, "gem")
    cov = _run_search(client, model, anchor, COVERAGE_SYSTEM,                  # pass 2: broad sweep, mills blocked
                      {"type": _WS, "name": "web_search", "max_uses": 24, "blocked_domains": _MILL_BLOCK}, "coverage")
    merged: dict[str, dict] = {}                                               # merge both passes, UNIONing query tags
    for r in gem["results"] + cov["results"]:
        ex = merged.get(r["url"])
        if ex:
            for q in r.get("queries", []):
                if q not in ex.setdefault("queries", []):
                    ex["queries"].append(q)
        else:
            merged[r["url"]] = r
    raw = {"queries": gem["queries"] + cov["queries"], "results": list(merged.values())}
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
        date = date or _url_date(r["url"]) or _page_age_date(r.get("page_age"))   # walled desks: page_age saves them
        return {"title": r.get("title", ""), "url": r["url"], "published_date": date or "",
                "source": urlparse(r["url"]).netloc, "snippet": lede, "queries": r.get("queries", [])}

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
