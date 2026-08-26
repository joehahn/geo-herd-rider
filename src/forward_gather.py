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

import json as _json
import re
import sys as _sys
import time as _time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import lede as _lede   # the ONE text-provenance definition

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
# INGEST PARAMS, from retrieval_config.json -- the same file gkg.py reads, so the two ingests
# cannot drift apart. They used to be duplicated in both investor_profiles with nothing keeping the
# copies equal. See that file's _domain_steering_note.
_STEER = _json.loads((Path(__file__).resolve().parent.parent / "retrieval_config.json").read_text())
_SPECIALTY_ALLOW = list(_STEER.get("specialty_allow") or [])  # GEM pass allowlist (reaches Cloudflare-walled etf.com)
_MILL_BLOCK = list(_STEER.get("mill_block") or [])            # COVERAGE pass blocklist (kills listicle mills)

# SHARED BEAT SET — the SINGLE SOURCE OF TRUTH for BOTH engines, so the Tavily backtest is a valid proxy
# for the Anthropic forward (SAME queries, different engine). Phrased as plain natural-language (no boolean
# OR / quotes) so the Anthropic model AND Tavily's semantic search run them the same way. forward_gather_tavily
# imports these + the domain lists to run the identical two-pass sweep. GEM beats -> allowlist pass;
# COVERAGE beats -> blocklist pass. The ONLY residual gap: Anthropic also spawns adaptive follow-ups (Tavily
# runs the fixed list only) -> the backtest is a valid but CONSERVATIVE proxy (under-finds vs the forward).
#
# THE STRINGS NOW COME FROM retrieval_config.json, WHICH IS AUTHORITATIVE (2026-08-14).
# They used to be hardcoded here and hand-copied into retrieval_config.json, which that file's own header
# claimed was "lifted byte-for-byte" — nothing enforced it, and it silently drifted: a beat prune and an
# FDA-beat rename landed in the JSON only, so for days the FORWARD pull kept spending live web searches on
# three pruned momentum beats ("best performing stock", "biggest stock gainers", "stock surges skyrockets
# all-time high record") and on a stale "upcoming FDA election vote" beat, while the BACKTEST measured a
# different vocabulary. Reading one file removes the class of bug rather than re-syncing by hand.
#
# The rationale comments that used to annotate each beat are preserved in retrieval_config.json alongside
# the strings they explain (each beat carries its own `origin` + `keywords`), so the provenance moved with
# the data instead of being stranded here.
def _load_beats() -> tuple[list[str], list[str]]:
    """(gem, coverage) beat queries from retrieval_config.json.

    FAILS LOUDLY. An empty or missing config must never degrade to a silent fallback list: a stale
    hardcoded copy is exactly how the drift above went unnoticed, and a forward pull that quietly runs
    the wrong beats is unrepeatable — the day's news cannot be re-fetched later (see forward_daily.sh)."""
    p = Path(__file__).resolve().parent.parent / "retrieval_config.json"
    cfg = _json.loads(p.read_text())
    gem = [b["query"] for b in cfg.get("gem_beats") or []]
    cov = [b["query"] for b in cfg.get("coverage_beats") or []]
    if not gem or not cov:
        raise ValueError(f"{p} has no gem_beats/coverage_beats; refusing to gather on an empty beat set")
    return gem, cov


GEM_BEATS, COVERAGE_BEATS = _load_beats()

# (The former hardcoded GEM_BEATS/COVERAGE_BEATS literals, with their per-beat rationale comments,
#  were deleted here on 2026-08-14 when retrieval_config.json became authoritative. They are in git
#  history if that reasoning is ever needed; do NOT reintroduce a literal list — see _load_beats.)
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
            cur = seen.get(u)
            if cur is None:
                seen[u] = a
                continue
            qs = cur.setdefault("queries", [])       # same URL from both engines -> merge beat tags
            for q in a.get("queries", []):
                if q not in qs:
                    qs.append(q)
            # PREFER THE COPY THAT ACTUALLY HAS TEXT. This used to keep whichever pool was passed
            # FIRST, wholesale, and every caller passes Anthropic first -- so when both engines
            # returned a URL we kept Anthropic's snippet and discarded Tavily's. Anthropic's snippet
            # comes from our own _freeze() fetch, which fails on Benzinga, Seeking Alpha and Yahoo;
            # Tavily returns the body itself. Measured on the bootstrap: the `both` bucket was
            # 57.9% text-less, the WORST of any engine, which is only possible if the merge is
            # choosing the worse copy. Third instance of the same pattern -- text already paid for,
            # thrown away -- after the [:300] ingest cap and lede.apply's 280.
            if _lede.provenance(cur) == "headline only" and _lede.provenance(a) != "headline only":
                win = {**a, "queries": qs}
                for k, v in cur.items():             # never lose a field the winner happens to lack
                    if k != "queries" and v and not win.get(k):
                        win[k] = v
                seen[u] = win
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
           workers: int = 12, cap: int = 0, freeze_cap: int = 0) -> list[dict]:
    """Live firehose gather -> a date-clean, window-filtered arts pool for the scout.

    client: a raw anthropic.Anthropic() (web search is Anthropic-only). Returns arts sorted
    newest-first, capped to `cap`. Fills `capture` (raw queries + all results) for the Phase-B archive.

    A gather can return 1000+ results; fetching every one to date+freeze it is far too slow. So we
    first TRIAGE by URL date (no fetch): drop anything whose URL date is confirmably out of window,
    keep in-window (priority) + undated (need a fetch to decide), cap at `freeze_cap`, and only then
    fetch/freeze that subset. The full window filter still runs on the fetched dates (fail closed).

    `freeze_cap` IS A SAFETY CEILING, NOT A TARGET -- 0 = no ceiling, and 0 is the intended setting.
    It was 160, which made the pool size a CONSTANT when it should float with how much news there
    was. Measured 2026-08-25, same anchor, same lookback, only the cap differing:

        freeze_cap=160   401 raw -> 397 triaged -> 160 fetched -> 41 in-window    471s
        freeze_cap=500   418 raw -> 409 triaged -> 409 fetched -> 109 in-window   556s

    +166% articles for +18% wall-clock, because the fetches are parallel across `workers` -- the cap
    was guarding a cost that barely exists. Deliberately NOT re-tuned to 500: replacing one magic
    number with a bigger one ages exactly like the 3d/7d/14d ladder in forward.py did. Same shape as
    relevance.rank_pool's `keep` ("a SAFETY CEILING, not a target ... an earlier version made it a
    quota, which is wrong in both directions") and the `0 = uncapped` convention of news_cap and
    max_events. Pass a positive value only as a runaway guard.

    CAVEAT worth carrying: undateable drops scale with fetches too (13 -> 76 in the run above), and
    they are discarded fail-closed. The honest gain is 41 -> 109 with a larger fail-closed pile
    behind it, and that pile is its own (cheaper) lever, since those articles are already paid for.
    """
    _WS = "web_search_20260209"

    def _search_with_backoff(system, tool, label):
        """Run one pass; if it comes back with QUERIES BUT NO RESULTS, wait and retry.

        That exact signature -- searches issued, nothing returned -- is server-side web-search rate
        limiting, not an empty internet. Observed 2026-08-14: a lone GEM pass returned 165 results, but
        the COVERAGE pass right behind it returned 0 from 0 queries, and the model's own text said "I've
        hit a hard limit on web search calls for this session". Two 24-use passes back to back can
        exhaust the allowance, and the old code treated the resulting emptiness as a normal quiet day."""
        r = {"queries": [], "results": []}
        for attempt in range(3):
            try:
                r = _run_search(client, model, anchor, system, tool, label)
            except Exception as e:  # noqa: BLE001
                # AN EXCEPTION MUST NOT SINK THE WHOLE GATHER. This used to propagate: on 2026-08-15
                # pass 2 raised `400 container_id is required when there are pending tool uses
                # generated by code execution with tools`, the exception left pull_day entirely, and
                # the day was recorded as "daily pull failed" with NO file written -- discarding
                # pass 1's results AND the Tavily half, which had done nothing wrong. The pull is
                # unrepeatable, so that day is gone for good. It read afterwards as a cron that never
                # fired; it was this. Reproduced live on 2026-08-25, so it is not historical.
                print(f"    {label}: gather pass FAILED ({type(e).__name__}: {str(e)[:160]})",
                      file=_sys.stderr, flush=True)
                if attempt == 2:
                    return r                              # keep whatever earlier attempts produced
                _time.sleep(30 * (attempt + 1))
                continue
            if r["results"] or not r["queries"]:
                return r                                  # got results, or genuinely searched nothing
            wait = 60 * (attempt + 1)
            print(f"    {label}: {len(r['queries'])} searches returned 0 results "
                  f"(web-search rate limit); waiting {wait}s and retrying", flush=True)
            _time.sleep(wait)
        return r

    # allowed_callers: LEFT AT THE DEFAULT, deliberately. Tried and reverted 2026-08-26.
    #
    # On web_search_20260209 this defaults to ["code_execution_20260120"] -- "dynamic filtering",
    # where Claude writes and runs code that filters results before they reach its context. The
    # theory was that this discards articles a FIREHOSE wants: we would rather our own gate and the
    # scout judged relevance, not the gather model. Setting ["direct"] bypasses it.
    #
    # MEASURED, and the theory did not survive. On the comparable metric -- RAW results the search
    # returned, before any of our filtering -- direct gave 342 against dynamic's 389. No gain, if
    # anything slightly worse. Anthropic's own numbers point the same way for their intended use:
    # dynamic filtering is +11% performance on BrowseComp/DeepsearchQA at 24% fewer input tokens.
    #
    # And it is not the binding constraint anyway. Of a day's raw results, 68% are dropped
    # OUT-OF-WINDOW because the tool has no recency parameter at all (max_uses, allowed_domains,
    # blocked_domains, user_location -- that is the whole list; Tavily has `days`). That loss is
    # structural and no caller setting touches it.
    #
    # What DID move the number was ours: removing our own freeze_cap of 160 took in-window yield
    # from a median of 33 to 79 on the first uncapped day. One variable, 2.4x, free.
    #
    # Do not re-try this without an alternating-day design -- a same-day A/B is confounded, since one
    # pass already trips the web-search rate limit ("22 searches returned 0 results") and whichever
    # arm ran second would look worse for reasons unrelated to filtering.
    gem = _search_with_backoff(GEM_SYSTEM,                                     # pass 1: specialty-allowlisted gem sweep
                               {"type": _WS, "name": "web_search", "max_uses": 24,
                                "allowed_domains": _SPECIALTY_ALLOW}, "gem")
    _time.sleep(20)          # let the web-search allowance recover before pass 2 (see _search_with_backoff)
    cov = _search_with_backoff(COVERAGE_SYSTEM,                                # pass 2: broad sweep, mills blocked
                               {"type": _WS, "name": "web_search", "max_uses": 24,
                                "blocked_domains": _MILL_BLOCK}, "coverage")
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
    survivors = [r for _, r in (triaged[:freeze_cap] if freeze_cap else triaged)]

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

    # FUNNEL LOG. This pipeline can return zero for at least four unrelated reasons -- no search results,
    # everything triaged out on URL date, every freeze-fetch failing, or a window too narrow for what the
    # engine returns -- and they are indistinguishable from the outside. That ambiguity is what let the
    # daily pull report "anthropic 0" for 32 days without anyone being able to say why. One line, every run.
    _undated = sum(1 for a in built if not a["published_date"])
    _out = len(built) - len(kept) - _undated
    print(f"    gather funnel [{lo}..{hi}]: {len(raw['results'])} raw -> {len(triaged)} triaged -> "
          f"{len(survivors)} fetched -> {len(kept)} in-window "
          f"(dropped: {_out} out-of-window, {_undated} undateable)", flush=True)
    kept.sort(key=lambda a: a["published_date"], reverse=True)
    # cap=0 MEANS UNCAPPED, NOT "KEEP NOTHING". `kept[:0]` is the empty list, and that one slice is what
    # returned "anthropic 0" on every daily pull for 32 days: pull_day passes cap=0 for exactly the reason
    # its docstring gives ("the daily pull must keep every day's news"), and every article the gather had
    # just found, dated and frozen was thrown away on the last line. It never raised and never logged, so
    # the union line read "anthropic 0 + tavily 139" like a slow news day. 0 = uncapped is the convention
    # everywhere else here (news_cap, max_new_events both document it); this now honours it.
    result = kept[:cap] if cap else kept
    if capture is not None:
        kept_urls = {a["url"] for a in result}
        capture["queries"] = raw["queries"]
        capture["arts"] = result                              # the FROZEN in-window pool (archive reuses it — no re-fetch)
        capture["results"] = [{**r, "in_window": r["url"] in kept_urls} for r in raw["results"]]
    return result
