"""firehose.py — the simplified solution: monitor the news firehose for called-out gems.

The pivot away from the causal decision-tree. We are not screening all tickers to discover gems;
the financial press already does that and prints the ticker by name (CNBC/ETF.com/24-7 named BWET
weeks before it tripled). So: each weekly run, read the firehose (news search + Trump posts,
look-ahead-safe), keep the tickers the press explicitly calls out as thesis-driven movers that are
still EARLY / under-the-radar (room to run), hand them to the optimizer as the watchlist, hold
while the thesis stays live, and drop before the crest when it goes consensus/decaying.

Entry, sizing, exit:
  - ENTRY: a ticker the press names as an early thesis-driven mover (stage 'early'/'building').
  - SIZING: the reused mean-variance optimizer + investor_profile knobs.
  - EXIT: it falls out of the weekly watchlist — the press stops calling it a live/early buy, or
    flags it 'crested'. (The "when do we drop BWET?" question, answered by the firehose itself.)

Reuses: trump_feed, the Anthropic web_search, curator._optimized_weights (sizing), score (prices,
entry timing, T_UPDATE_DAYS), costs, and the investor_profile knobs. No causal ladder.

    python src/firehose.py --start 2026-02-13 --end 2026-06-18 --model claude-opus-4-8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
import trump_feed  # noqa: E402
import costs  # noqa: E402
import score  # noqa: E402
import curator  # noqa: E402
from optimizer import load_financial_model  # noqa: E402
from util import resolve_cadence, load_dotenv as _load_dotenv, news_domains, scan_anchors, MAX_TEXT  # noqa: E402

MODEL = "claude-opus-4-8"
WORKERS = 8

# GDELT firehose queries — GEM-AGNOSTIC BY CONSTRUCTION and FROZEN before the eval (CLAUDE.md #5).
# The prior list drifted BWET-ward (Hormuz/tanker/oil/energy) — it lit up in the 2026 Iran war but
# went dark elsewhere, i.e. it hand-pointed at one gem's thesis. This is the vetted set from
# run_harness.py: discovery superlatives + the macro beats SCAN_SYSTEM names + an EVEN GICS sector
# sweep (a complete partition privileges no theme) + a pre-registered emerging-tech theme layer.
# Gem sub-niches ("uranium"/"rare earth"/"weight loss drug") are DROPPED — reverse-engineered from
# known winners. Canonical shared set (run_harness.HARNESS_QUERIES aliases this).
# NOTE: a SPACE is GDELT implicit AND — "energy stocks" = energy AND stocks, far more recall than
# FACTORED beats: shared synonym lists composed into queries so beats are cheap to add/drop as the
# backtest evolves (edit a dict entry, not a hand-tuned string). GDELT does NOT stem (deliberate) ->
# singular/plural are OR-enumerated; SPACE = implicit AND; "quoted" = exact phrase. Gem-agnostic:
# standard GICS sectors + the original emerging themes + generic catalyst topics (no hindsight terms).
_VEHICLE = '(stock OR stocks OR ETF OR ETFs OR shares OR equities)'
_MOVERS = '(best OR top OR biggest OR surging OR rallying OR soaring OR breakout OR outperforming)'
_EARLY = '("under the radar" OR overlooked OR undiscovered OR unnoticed OR niche OR "still early" OR obscure)'
# sector / theme topics -> (topic)(vehicle). Kept DISTINCT (not merged) to preserve unique coverage.
_SECTORS = {
    "technology":    '(technology OR tech OR semiconductor OR semiconductors OR software OR "artificial intelligence" OR AI)',
    "energy":        '(energy OR oil OR gas OR petroleum OR "oil and gas" OR drilling)',
    "financials":    '(financial OR financials OR bank OR banks OR insurance OR fintech OR lender)',
    "healthcare":    '(health OR healthcare OR biotech OR pharma OR pharmaceutical OR pharmaceuticals OR medical)',
    "industrials":   '(industrial OR industrials OR manufacturing OR machinery OR "capital goods")',
    "materials":     '(materials OR mining OR miner OR miners OR metals OR chemical OR chemicals)',
    "consumer":      '(consumer OR retail OR retailer OR apparel OR "consumer goods")',
    "utilities":     '(utility OR utilities OR "electric power" OR "power grid" OR "water utility")',
    "real_estate":   '("real estate" OR REIT OR REITs OR property OR homebuilder OR homebuilders)',
    "communication": '(telecom OR telecommunications OR "communication services" OR media OR streaming)',
    "space":         '(space OR aerospace OR satellite OR defense OR defence OR drone OR drones)',
    "robotics":      '(robotics OR robot OR automation OR "machine learning")',
    "quantum":       '(quantum OR "quantum computing")',
    "nuclear":       '(nuclear OR "small modular reactor" OR SMR OR fusion)',
    "crypto":        '(cryptocurrency OR crypto OR bitcoin OR blockchain OR "digital asset" OR stablecoin)',
}
# catalyst topics -> (topic) ONLY. NO vehicle/superlative: that 3rd AND-clause would drop causal
# articles ("Iran shuts Hormuz") that never say "stock" or "surging" -- the exact news the scout
# reasons from (Boolean AND can only shrink recall).
_CATALYSTS = {
    "geopolitics":  '(geopolitics OR geopolitical OR war OR conflict OR military OR invasion OR ceasefire OR "national security" OR sanctions)',
    "shipping":     '(shipping OR freight OR tanker OR tankers OR "supply chain" OR chokepoint OR blockade OR port OR ports OR strait)',
    "trade":        '(tariff OR tariffs OR "trade war" OR embargo OR "export control" OR "export ban" OR quota)',
    "rates":        '("interest rate" OR "interest rates" OR inflation OR "Federal Reserve" OR "rate cut" OR "rate hike" OR yields)',
    "supply_shock": '(shortage OR "supply shock" OR "supply cut" OR "supply crunch" OR "demand surge" OR disruption OR outage)',
}
GDELT_QUERIES = (
    [f"{_MOVERS} {_VEHICLE}", f"{_EARLY} {_VEHICLE}"]     # discovery: (superlative)(vehicle)
    + [f"{_t} {_VEHICLE}" for _t in _SECTORS.values()]   # sectors / themes: (topic)(vehicle)
    + list(_CATALYSTS.values())                          # catalysts: (topic) only -- raw event news
)
GDELT_WEEK_CAP = 80          # max GDELT headlines fed to the LLM per week (seeds always kept)


def news_pool(queries, start, end, chunk_days: int = 30, per: int = 60, cache_path=None,
              stats_path=None, engine: str | None = None, profile: str | None = None) -> list[dict]:
    """THE backtest discovery entry point — dispatches to the configured retrieval engine and returns
    the same article records either way ({published_date, source, title, snippet, url, queries}).

    `engine` (default: the profile's `retrieval_engine`):
      "gkg" — GDELT's GKG on BigQuery (src/gkg.py). One date-partitioned SQL query per chunk, no
              per-request throttle, and a semantic theme+subject-org gate instead of the DOC API's
              lexical `(stock OR ETF OR ...)` AND-clause.
      "doc" — the legacy GDELT DOC API (src/gdelt.py). Keyless, so it needs no gcp-key.json, but
              measured on this repo at 67% HTTP-429 and ~28 items/min.

    IMPORTANT — `queries` is engine-specific and NOT translated between the two. The DOC engine takes
    this module's GDELT_QUERIES (boolean AND/OR strings tuned for a lexical index). GKG takes beat
    query strings from retrieval_config.json and matches their `keywords` atoms against title+URL.
    Passing GDELT_QUERIES through to GKG would match nothing, so when the engine is "gkg" the
    argument is IGNORED and the full beat set is used. Pass an explicit subset only if you mean beat
    query strings.

    The engine name is woven into `cache_path` so the two engines never read each other's pool — they
    match on different surfaces (full text vs title+URL) and their pools are not interchangeable."""
    eng = (engine or load_financial_model(
        profile or str(REPO_ROOT / "investor_profile.backtest.md")).get("retrieval_engine", "gkg")).lower()
    if cache_path:                                  # <dir>/gdelt_pool.json -> <dir>/gdelt_pool.gkg.json
        p = Path(cache_path)
        cache_path = str(p.with_suffix(f".{eng}{p.suffix}"))
    if eng == "doc":
        import gdelt as gd          # local, like the other call site: keeps import cost off the path
        return gd.pool(queries, start, end, chunk_days=chunk_days, per=per,
                       cache_path=cache_path, stats_path=stats_path)
    if eng != "gkg":
        raise ValueError(f"unknown retrieval_engine {eng!r}; expected 'gkg' or 'doc'")
    import gkg                      # local: pulls in google-cloud-bigquery, unwanted on the doc path
    return gkg.pool(start, end, queries=None, cache_path=cache_path, stats_path=stats_path,
                    profile=profile, chunk_days=max(chunk_days, 7))

SCAN_SYSTEM = """You are a markets desk reading the week's news firehose to find HIDDEN GEMS the
financial press is already calling out — tickers a journalist explicitly names as a thesis-driven
mover, ideally while still EARLY / under-the-radar (room to run).

You read: (1) this week's Donald Trump Truth Social posts (given), and (2) the news you SEARCH.
SEARCH the week's market coverage for stories that NAME a specific US-listed ticker or fund as a
standout trade on a live thesis (geopolitics, energy/shipping, tariffs, Fed, a sector catalyst).
Append 'before:<cron date>' to every query and DISCARD anything dated after it (no look-ahead).

BE SELECTIVE — keep only the FEW clearest standout movers (typically 0-3, sometimes none); skip
names merely mentioned in passing. KEEP a ticker only if the PRESS explicitly names it (don't
infer your own). VEHICLE SELECTION: when several tickers express the same thesis, name the SINGLE
PUREST vehicle — a rate/commodity ETN/pure-play over diluted operators (BWET, not FRO/STNG); a
clean single ADR over a broad country ETF. Scope = US-listed INCLUDING ADRs and country/theme
ETFs (a foreign event is named via its US-listed ADR/ETF, e.g. YPF/ARGT, never a foreign ticker).

CATALYST GATE (the hard filter — this is the bet). Keep a ticker ONLY if the press ties it to a
SPECIFIC, DATABLE, RESOLVABLE catalyst: a discrete event with a knowable resolution — a war/
chokepoint, an export ban or tariff, a regulatory approval or named bill, an agency emergency
declaration, a named contract/partnership/deal, a supply shock. That resolution is what later
flips thesis_live FALSE. JUDGE BY THE STRONGEST REASON TO OWN IT, NOT THE WEAKEST: if a specific
catalyst is present, KEEP the name even when the coverage ALSO wraps it in a theme, valuation, or
technical story (e.g. "AI-power demand AND a reactor APPROVAL" -> keep; the approval is the
catalyst). The reject list below applies ONLY to a name whose SOLE rationale is:
  - theme / secular-momentum  ("AI power demand benefits utilities", "next wave after AI")
  - valuation / positioning   ("undervalued", "hedge-fund accumulation", "13F", "cheap as ever")
  - technical / chart         ("golden cross", "breakout", "high dividend yield")
  - generic macro             ("rate-cut rally", "sector rotation")
  - hype / narrative          ("IPO hype", "meme", "everyone piling in")
A named catalyst that later FAILS is fine — you couldn't have known. A PURE theme with no resolution
is NOT — it rides through every crash and bleeds. Drop only names that are theme/value/hype AND
NOTHING ELSE.

For each kept ticker decide:
  thesis        — the SPECIFIC catalyst event, <=12 words (e.g. "Iran war spikes tanker freight
                  rates", "China bans rare-earth exports"). Name the EVENT, not a trend or a
                  valuation. If you can only describe a theme, you should have dropped it.
  thesis_live   — TRUE while the catalyst is ACTIVE/UNRESOLVED; stays TRUE through mainstream hype
                  ("up 600%, everyone piling in" is NOT thesis death). FALSE only when the CATALYST
                  resolves (ceasefire, chokepoint reopens, shock ends). HOLD/EXIT switch.

You forecast NOTHING — no magnitude, target, weight, or probability. Output ONLY JSON:
{"picks":[{"ticker":"BWET","thesis":"<=12 words","thesis_live":true,
"evidence_urls":["news URLs"]}]}. Empty is fine: {"picks":[]}."""


def _extract_json(text: str) -> dict:
    t = text.strip()
    if "```" in t:
        for chunk in reversed(t.split("```")):
            c = chunk.strip()
            if c.startswith("json"):
                c = c[4:].strip()
            if c.startswith("{"):
                return json.loads(c)
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e > s:
        return json.loads(t[s:e + 1])
    raise ValueError("no JSON object in model output")


def scan(client, model: str, anchor: pd.Timestamp, posts: pd.DataFrame,
         domains: list[str], capture: dict | None = None) -> list[dict]:
    """Firehose scan as of `anchor` (look-ahead-safe). Returns the press-named gems.
    If `capture` (a dict) is passed, it is filled with the raw web-search inputs this scan saw —
    `capture["queries"]` (the search terms the model ran) and `capture["results"]` (deduped
    [{url,title,page_age}]) — for the forward archive (freeze-at-decision-time; see forward.py)."""
    lines = [f"[{r.created_at.tz_convert('America/New_York').date()}] {r.text[:MAX_TEXT]}"
             for r in posts.itertuples()]
    prefer = ", ".join(domains) if domains else "major financial news outlets"
    user = (f"Week ending {anchor.date()} (use before:{anchor.date()} on every search).\n"
            f"This week's high-reach posts:\n\n" + "\n".join(lines or ["(none)"])
            + f"\n\nSearch the week's market news (prefer: {prefer}). Which tickers is the press "
            "naming as thesis-driven movers, and at what stage? Output the JSON.")
    tools = [{"type": "web_search_20260209", "name": "web_search"}]
    messages = [{"role": "user", "content": user}]
    kw = {"model": model, "max_tokens": 3000, "system": SCAN_SYSTEM, "tools": tools, "messages": messages}
    tally = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "web_searches": 0}
    text = ""
    queries: list[str] = []
    results: dict[str, dict] = {}   # url -> {url,title,page_age}, deduped across round-trips
    for _ in range(6):
        resp = client.messages.create(**kw)
        u = costs.extract(resp.usage)
        for k in tally:
            tally[k] += u.get(k, 0)
        text = "".join(b.text for b in resp.content if b.type == "text")
        for b in resp.content:                          # harvest the raw web-search inputs seen
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
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    costs.record("firehose", model, f"scan-{anchor.date()}", tally)
    if capture is not None:
        capture["queries"] = queries
        capture["results"] = list(results.values())
    try:
        picks = _extract_json(text).get("picks", [])
    except Exception:  # noqa: BLE001
        return []
    for p in picks:
        p["ticker"] = str(p.get("ticker", "")).strip().upper()
        p["anchor"] = anchor
    return [p for p in picks if p["ticker"]]


FIXTURE_SYSTEM = """You are a markets desk reading the financial press to find HIDDEN GEMS — a
ticker a journalist explicitly NAMES as a thesis-driven mover. Below is the press coverage
available as of this week (and nothing later).

BE SELECTIVE. Most headlines are noise. Keep only the FEW clearest standout movers — typically
0-3 names per week, sometimes none. Skip anything merely mentioned in passing, part of a long
list, or routine coverage. A week with no real gem should return {"picks":[]}.

VEHICLE SELECTION. When several tickers express the SAME thesis, name the SINGLE PUREST vehicle,
not the crowd:
  - a rate/commodity ETN or pure-play over diluted operator equities (BWET, not FRO/DHT/STNG);
  - the cleanest single ADR over a broad country ETF when the press points there (a bank ADR
    over the diversified ETF for an Argentina move);
  - the most-levered direct beneficiary over a tangential one.
Scope = US-listed instruments, INCLUDING ADRs and country/theme ETFs (a foreign event is named
via its US-listed ADR/ETF, e.g. YPF / ARGT, never a foreign-exchange ticker).

For each kept ticker decide:
  thesis        — the driving catalyst, <=12 words (e.g. "Iran war spikes tanker freight rates").
  thesis_live   — TRUE while that catalyst is still ACTIVE / UNRESOLVED as of this week. It stays
                  TRUE through mainstream hype: "up 600%, everyone piling in" is NOT thesis death.
                  Flip to FALSE only when the CATALYST ITSELF resolves — ceasefire signed, chokepoint
                  reopened, the supply shock ends, rates actually rolling over.
                  This is the HOLD/EXIT switch.

Do NOT equate a big % gain with "late". You forecast NOTHING — no magnitude, target, weight, or
probability. Output ONLY JSON: {"picks":[{"ticker":"BWET","thesis":"<=12 words","thesis_live":
true,"evidence_urls":["..."]}]}."""


def _fixture_articles(path: str) -> list[dict]:
    return json.loads(Path(path).read_text()).get("articles", [])


def scan_fixture(client, model: str, anchor: pd.Timestamp, articles: list[dict]) -> list[dict]:
    """Look-ahead-clean scan against a fixed article set (perfect-retrieval simulation).
    Only articles published on/before the anchor are visible."""
    cut = anchor.date().isoformat()
    seen = [a for a in articles if str(a.get("published_date", ""))[:10] <= cut]
    if not seen:
        return []
    block = "\n".join(f"[{a['published_date']} | {a.get('source','')}] {a.get('title','')} — "
                      f"{a.get('snippet','')} ({a.get('url','') or 'no url'})" for a in seen)
    user = (f"Week ending {cut}. Press coverage available as of this week:\n\n{block}\n\n"
            "Which tickers is the press naming, on what thesis, at what stage? Output the JSON.")
    resp = client.messages.create(model=model, max_tokens=1500, system=FIXTURE_SYSTEM,
                                  messages=[{"role": "user", "content": user}])
    costs.record("firehose", model, f"fixture-{cut}", costs.extract(resp.usage))
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        picks = _extract_json(text).get("picks", [])
    except Exception:  # noqa: BLE001
        return []
    for p in picks:
        p["ticker"] = str(p.get("ticker", "")).strip().upper()
        p["anchor"] = anchor
    return [p for p in picks if p["ticker"]]


def _window(articles, anchor, lookback_days):
    """Articles published in (anchor - lookback, anchor], i.e. this week's trailing firehose."""
    lo = (anchor - pd.Timedelta(days=lookback_days)).date().isoformat()
    cut = anchor.date().isoformat()
    return [a for a in articles if a.get("published_date") and lo < a["published_date"] <= cut]


def run_scans(start, end, rebalance_days, model, workers, fixture=None, gdelt=False,
              seed=None, lookback_days=None, queries=None, pool_chunk_days=30,
              pool_per=60) -> dict[pd.Timestamp, list[dict]]:
    # one cadence knob: scans step every rebalance_days, and the news window each scan reads
    # defaults to that same interval ("the news since the last scan"). lookback_days overrides
    # it only for the rare sparse-coverage smoothing case.
    lookback_days = rebalance_days if lookback_days is None else lookback_days
    import anthropic
    client = anthropic.Anthropic()
    anchors = scan_anchors(start, end, rebalance_days)
    if fixture:
        articles = _fixture_articles(fixture)
        print(f"Firehose: FIXTURE scan of {len(anchors)} weeks vs {len(articles)} articles "
              f"({model}); retrieval assumed perfect, mechanics only.", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            pairs = list(zip(anchors, ex.map(lambda a: scan_fixture(client, model, a, articles), anchors)))
        return dict(sorted(pairs))
    if gdelt:
        import hashlib
        seeds = _fixture_articles(seed) if seed else []
        qs = queries or GDELT_QUERIES
        win_start = anchors[0] - pd.Timedelta(days=35)  # generous, cadence-independent (per-week _window slices it)
        # cache the (slow, throttled) pool keyed by queries+window, so logic/prompt iterations are fast
        key = hashlib.md5(f"{qs}{win_start.date()}{anchors[-1].date()}{pool_chunk_days}{pool_per}".encode()).hexdigest()[:10]
        cache_f = REPO_ROOT / "data" / "windows" / f"gdelt_pool_{key}.json"
        cache_f.parent.mkdir(parents=True, exist_ok=True)
        print(f"Firehose: GDELT scan of {len(anchors)} weeks ({len(qs)} queries, +{len(seeds)} "
              f"seeds); pool fetch/resume (checkpointed, ~10s/query-chunk) ...", file=sys.stderr)
        gpool = news_pool(qs, win_start, anchors[-1], chunk_days=pool_chunk_days, per=pool_per,
                          cache_path=str(cache_f))   # engine per profile; resumable across kills
        print(f"  GDELT pool: {len(gpool)} deduped articles ({cache_f.name}).", file=sys.stderr)

        def one(a):
            seen = _window(seeds, a, lookback_days)
            gwin = sorted(_window(gpool, a, lookback_days), key=lambda x: x["published_date"],
                          reverse=True)[:GDELT_WEEK_CAP]
            return scan_fixture(client, model, a, seen + gwin)  # seeds first, never truncated

        with ThreadPoolExecutor(max_workers=workers) as ex:
            pairs = list(zip(anchors, ex.map(one, anchors)))
        return dict(sorted(pairs))
    posts = trump_feed.candidate_posts(start, end)
    domains = news_domains()
    print(f"Firehose: scanning {len(anchors)} weeks via {model} ...", file=sys.stderr)

    def one(a):
        lo = a - pd.Timedelta(days=lookback_days)
        wk = posts[(posts["created_at"] > lo) & (posts["created_at"] <= a)]
        return a, scan(client, model, a, wk, domains)

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for a, picks in ex.map(one, anchors):
            out[a] = picks
    return dict(sorted(out.items()))


def _live(p: dict) -> bool:
    return bool(p.get("thesis_live", True))


# These count SCANS, not calendar weeks -- so changing rebalance_period silently changes their
# REAL-TIME horizon (at biweekly, 4 scans of silence is 8 weeks, not 4). They are profile knobs as of
# 2026-08-09 for exactly that reason: a hardcoded 4 that was right weekly is wrong biweekly, and a
# constant nobody can see is the worst place for a cadence-sensitive number.
EXIT_PATIENCE = 2   # consecutive EXPLICIT thesis-dead SCANS before exiting (hysteresis vs churn)
MAX_STALE = 4       # SCANS a held name may go UNMENTIONED before we drop it (no thesis confirmation)


def _watch_clocks(fm: dict | None) -> tuple[int, int]:
    """(exit_patience, max_stale) in SCANS. Profile overrides the module defaults."""
    fm = fm or {}
    return (int(fm.get("exit_patience_scans", EXIT_PATIENCE) or EXIT_PATIENCE),
            int(fm.get("max_stale_scans", MAX_STALE) or MAX_STALE))


def _trend_rank(tickers: list[str], panel, asof, lookback: int) -> dict:
    """Per-name trailing risk-adjusted return: mean/sd of daily returns over `lookback`.

    PER NAME, not a joint optimisation over the candidate set. With a 30-day lookback and ~40 live
    candidates the covariance matrix is rank-deficient, so joint mean-variance cannot rank that
    universe -- asking the optimizer directly for its top-N was measured at the 13th percentile,
    worse than random. A single-asset statistic is well-conditioned at n=30. Rank with this, then
    let the optimizer do the joint work on the survivors.

    Missing/short history scores -inf so it sorts last rather than crashing the cull."""
    out = {}
    for tk in tickers:
        try:
            col = panel[tk].loc[:asof].dropna().iloc[-lookback:]
            if len(col) < 10:
                out[tk] = float("-inf"); continue
            r = col.pct_change().dropna()
            sd = float(r.std())
            out[tk] = float(r.mean() / sd) if sd > 0 else 0.0
        except Exception:  # noqa: BLE001
            out[tk] = float("-inf")
    return out


def _ranked_cull(ev: list[str], keep: int, panel, asof, lookback: int,
                 first_k: dict, k: int, fresh_slots: int, fresh_scans: int) -> list[str]:
    """Choose which `keep` live events hold capital, mechanically.

    Replaces `ev[:keep]` over an ALPHABETICAL list -- which, once live events outnumbered the cap,
    was allocating capital by first letter. Measured against a 60-seed random null: alphabetical
    67th percentile, trailing-return 83rd, freshness+trend 83rd, oldest-first 53rd.

    TWO TIERS, because the two signals see different things:
      1. FRESHNESS RESERVE -- up to `fresh_slots` for events first seen within `fresh_scans` scans.
         A trailing statistic cannot see a catalyst younger than its own window, and a fresh thesis
         is pre-run-up by construction, so a pure trend rank would evict exactly the early gems this
         project exists to catch (non-negotiable #2).
      2. TREND -- the rest by trailing risk-adjusted return.

    NON-DESTRUCTIVE: _stateful_watch recomputes the full live set every scan, so a name culled here
    returns next scan if its trend turns. This is rotation, not eviction."""
    # keep=0 is UNCAPPED (watchlist_cap: "0 = uncapped"), not "keep nothing". Without this the
    # function falls through to `[:0]` and returns an empty watchlist -- the same slice that returned
    # "anthropic 0" on 32 consecutive daily pulls before it was found. Safe today only because the
    # sole caller guards with `if max_watch and ...`; a second caller would not know to.
    if not keep or len(ev) <= keep:
        return ev
    fresh = [t for t in ev if (k - first_k.get(t, k)) < fresh_scans]
    fresh = sorted(fresh, key=lambda t: first_k.get(t, k), reverse=True)[:max(0, fresh_slots)]
    rest = [t for t in ev if t not in set(fresh)]
    sc = _trend_rank(rest, panel, asof, lookback)
    rest = sorted(rest, key=lambda t: (-sc.get(t, float("-inf")), t))
    return (fresh + rest)[:keep]


def anchor_tickers(fm: dict) -> list[str]:
    """The PERMANENT optimizer anchors (`always_include`), appended after the watchlist cull.

    Falls back to the pre-2026-08-09 pair (SPY + `defensive_ticker`) when a profile predates the knob,
    so old profiles and the archived run configs keep reproducing their original books."""
    raw = fm.get("always_include")
    if raw is None:
        defv = str(fm.get("defensive_ticker", "GLD") or "").upper()
        raw = [score.BENCHMARK] + ([defv] if defv else [])
    return list(dict.fromkeys(str(t).strip().upper() for t in (raw or []) if str(t).strip()))


def watchlist_cap(fm: dict) -> int:
    """The cap on tickers that may hold capital. `max_watchlist` is the current name; `max_agents` is
    the deprecated alias still written by the sweeps, the gem dashboards and the frozen forward
    profile. 0 = uncapped."""
    v = fm.get("max_watchlist")
    return int((fm.get("max_agents", 0) if v is None else v) or 0)


def _stateful_watch(scans: dict, seed: list[str] | None = None, fm: dict | None = None) -> dict:
    """Turn the stateless per-week scans into a STICKY position portfolio (fixes choppy holds).

    A name ENTERS when first read thesis_live=True, and stays held through coverage gaps and
    one-off noise. It EXITS on a CONFIRMED catalyst death (thesis_live=False on >=EXIT_PATIENCE
    consecutive *reads*), prolonged silence (unmentioned >=MAX_STALE weeks), or — the moment the
    agent flags catalyst_resolved=True — a HARD exit that honors that verdict without hysteresis
    (the catalyst is definitively done). Single-week flip-flops no longer churn the position.

    `seed` = the `starter_watchlist` INCEPTION holdings. They enter at week 0 with no thesis behind
    them, so they age out on the normal MAX_STALE clock (no agent ever mentions them) -- day-0 capital
    has a home and the curator's own picks displace it over the first few weeks rather than by fiat."""
    exit_patience, max_stale = _watch_clocks(fm)
    anchors = list(scans)
    holding, dead, stale, out = {}, {}, {}, {}
    for t in (seed or []):
        holding[t] = True; dead[t] = 0; stale[t] = 0
    for a in anchors:
        # catalyst_resolved is DELIBERATELY IGNORED (2026-08-12). Replaying the 3-year v10 book with
        # the flag honoured vs ignored gave $763,866 / 31.8% cancelled / Sharpe 1.67 against
        # $764,075 / 31.3% / 1.70 -- i.e. it changes nothing, or marginally hurts. It fired on 7% of
        # entries and three separate attempts to make it fire more never moved the book, because the
        # exit that actually matters is thesis_live (removing THAT costs $257k and 0.32 Sharpe).
        # The flag is still read from the scan rows and rendered, so old runs keep their history.
        resolved: set = set()
        live = {p["ticker"] for p in scans[a] if _live(p)} - resolved
        flagged_dead = {p["ticker"] for p in scans[a] if not _live(p)}
        for t in resolved:                   # catalyst RESOLVED -> honor the agent's verdict, exit NOW
            holding.pop(t, None); dead.pop(t, None); stale.pop(t, None)
        for t in live:                       # (re)enter / refresh
            holding[t] = True; dead[t] = 0; stale[t] = 0
        for t in list(holding):
            if t in live:
                continue
            if t in flagged_dead:
                dead[t] += 1; stale[t] = 0
                if dead[t] >= exit_patience:
                    del holding[t]
            else:                            # unmentioned this week — tolerate, but not forever
                stale[t] += 1
                if stale[t] >= max_stale:
                    del holding[t]
        out[a] = sorted(holding)
    return out


def _agent_precision(scans: dict, panel, fm: dict | None = None) -> list:
    """CURATOR-QUALITY metric, UNMASKED by the optimizer: for EVERY agent the curator created
    (one per distinct thesis/catalyst), the standalone return of its ticker over the span it was
    thesis_live — i.e. 'if you'd simply held what this agent named while it said hold, did it rise?'
    Independent of sizing/caps, so it measures the scout/agent's skill at picking good theses vs
    manufacturing losers. Returns a per-agent list (ticker, thesis, first/last live week, return)."""
    ag: dict = {}
    for a in sorted(scans):
        for p in scans[a]:
            if not p.get("thesis_live"):
                continue
            e = ag.setdefault(p["thesis"], {"ticker": p["ticker"], "first": a, "last": a})
            e["last"] = a
    def _naive(ts):
        ts = pd.Timestamp(ts)
        return ts.tz_localize(None) if ts.tzinfo is not None else ts
    rows = []
    for th, e in ag.items():
        tk, ret = e["ticker"], None
        if panel is not None and tk in panel.columns:
            s = panel[tk].dropna()
            if getattr(s.index, "tz", None) is not None:   # tz-robust: match the panel index to the anchors
                s = s.copy(); s.index = s.index.tz_localize(None)
            try:
                lo = s.loc[:_naive(e["first"])]; hi = s.loc[:_naive(e["last"])]
                if len(lo) and len(hi):
                    ret = round(float(hi.iloc[-1] / lo.iloc[-1] - 1), 4)
            except Exception:  # noqa: BLE001
                ret = None
        rows.append({"ticker": tk, "thesis": th, "first": _naive(e["first"]).date().isoformat(),
                     "last": _naive(e["last"]).date().isoformat(), "ret": ret})
    # the always-on ANCHOR agents (always_include: SPY/GLD/BIL): standalone return over the full window.
    # They are not curator picks, so they don't score as precision -- they're the floor the picks are read against.
    anchors = sorted(scans)
    if fm and anchors and panel is not None:
        for defv in anchor_tickers(fm):
            if defv == score.BENCHMARK or defv not in panel.columns:
                continue                       # SPY is already the benchmark line; don't double-count it
            ds = panel[defv].dropna()
            if getattr(ds.index, "tz", None) is not None:
                ds = ds.copy(); ds.index = ds.index.tz_localize(None)
            try:
                lo = ds.loc[:_naive(anchors[0])]; hi = ds.loc[:_naive(anchors[-1])]
                if len(lo) and len(hi):
                    rows.append({"ticker": defv, "thesis": f"anchor ({defv}) floor agent",
                                 "first": _naive(anchors[0]).date().isoformat(),
                                 "last": _naive(anchors[-1]).date().isoformat(),
                                 "ret": round(float(hi.iloc[-1] / lo.iloc[-1] - 1), 4)})
            except Exception:  # noqa: BLE001
                pass
    return sorted(rows, key=lambda r: (r["ret"] is None, r["ret"] or 0))


OVERLAY, OVERLAY_ANCHOR = "BWET", "2026-02-20"  # the motivating gem + carrier->W.Med transit


def backtest(scans: dict, fm: dict, capital: float = 50_000.0, daily: bool = False,
             panel: pd.DataFrame | None = None, vol_panel: pd.DataFrame | None = None,
             overlay: str = OVERLAY, overlay_anchor: str = OVERLAY_ANCHOR, picker=None,
             seed_holdings: dict | None = None) -> dict:
    """Weekly-rebalanced portfolio from the firehose watchlist vs SPY. With daily=True, also
    returns a daily value/allocation series (weekly weights held across days) for the dashboard.

    `picker` (opt-in) = a callable(cand_meta, max_keep) -> ordered keep-list (see src/picker.make_picker).
    When passed, the max_watchlist cull ranks EVENT-agents via the LLM picker rather than the default
    price-trend rank (conviction, the original ranker, was retired 2026-08-14), and
    the always_include anchors are dropped as competing agents — they're appended to the optimizer AFTER
    the cull. When None (default: all dashboards/sweeps), behavior is byte-identical to before (no LLM).

    `seed_holdings` = {ticker: weight} the book OPENS with, for a run that continues a prior book
    rather than starting from cash. It replaces `starter_watchlist` as the inception holding (see
    below) and fixes the FIRST rebalance's weights, after which the optimizer re-weights normally.

    `panel` lets a caller inject a FROZEN adjusted-close panel (DatetimeIndex, tz-naive) instead of
    fetching live — used by the golden-snapshot regression replay so results are deterministic
    (live yfinance prices drift day to day). Default None = fetch live, as before."""
    lookback = int(fm.get("lookback_period_days", curator.BACKTEST_LOOKBACK_DAYS))
    cull_rank = str(fm.get("cull_rank", "trend") or "trend").lower()
    fresh_slots = int(fm.get("cull_fresh_slots", 3) or 0)
    fresh_scans = int(fm.get("cull_fresh_scans", 2) or 0)
    max_watch = watchlist_cap(fm)          # PORTFOLIO cull: keep top-N tickers/event-agents. 0 = uncapped.
    always = anchor_tickers(fm)            # permanent anchors, appended AFTER the cull; idle capital parks here
    # SEED HOLDINGS REPLACE THE STARTER WATCHLIST, when supplied. `starter_watchlist` exists so that
    # DAY-0 CAPITAL HAS A HOME in a book that starts from cash -- "the curator's own picks displace it
    # over the first few weeks rather than by fiat" (see _stateful_watch). A bootstrap CONTINUING a
    # prior book has no such gap: its day-0 capital is the inherited position. Leaving the starter in
    # let AAPL/GOOGL/AMZN compete for slots against the very book being continued, and on the first
    # CBS rebalance AAPL took a quarter of the portfolio while two of the three names actually
    # inherited from the backtest went unfunded.
    seed_holdings = {str(t).strip().upper(): float(w)
                     for t, w in (seed_holdings or {}).items() if str(t).strip() and w}
    # THE BUY-AND-HOLD CONTROL IS NOT THE INCEPTION HOLDING. `starter_watchlist` plays two roles
    # that used to share one variable: it is where day-0 capital parks in a book starting from cash,
    # AND it is the boring-basket control the curated book is measured against. `seed_holdings`
    # replace the FIRST role on a continuation book -- but not the second: the control has to be the
    # same basket on every page or CBT and CBS are not comparable. Sharing the variable silently
    # swapped CBS's control to the inherited book while the label still said starter_watchlist.
    bh_basket = [str(t).strip().upper() for t in (fm.get("starter_watchlist") or []) if str(t).strip()]
    starter = bh_basket if not seed_holdings else sorted(seed_holdings)
    anchors = list(scans)
    watch = _stateful_watch(scans, seed=starter, fm=fm)  # inception holdings + sticky hold + hard-exit on catalyst_resolved
    tickers = ({score.BENCHMARK, overlay} | set(always) | set(starter) | set(bh_basket)
               | {t for w in watch.values() for t in w})
    start = (anchors[0] - pd.Timedelta(days=lookback + 14)).strftime("%Y-%m-%d")
    end = (anchors[-1] + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
    if panel is None:
        panel = score.fetch_panel(sorted(tickers), start, end, use_cache=False)
    days = panel[score.BENCHMARK].dropna().index

    # ticker validation: drop names with no price data (hallucinated/delisted, e.g. the GDELT BBRD)
    valid = {t for t in tickers if t in panel.columns and panel[t].notna().any()}
    dropped = sorted(t for w in watch.values() for t in w if t not in valid)
    if dropped:
        print(f"  dropped {len(set(dropped))} unpriced/invalid tickers: {sorted(set(dropped))}",
              file=sys.stderr)
    watch = {a: [t for t in w if t in valid] for a, w in watch.items()}

    # rebalance trading day for each anchor (anchor close + T_UPDATE_DAYS), and that week's weights
    reb, week_w = [], {}
    first_k, meta = {}, {}                      # PICKER context (built only when picker set): first-week seen + event metadata
    # UNFUNDED PRUNE. A name the optimizer refuses to fund for `drop_unfunded_weeks` CONSECUTIVE weeks
    # leaves the watchlist. Persistence is what makes this work: a single week's mean-variance weights
    # are noise at this lookback (30 daily obs, 15-26 candidates -> rank-deficient Sigma), but N straight
    # rejections is not. Measured 2026-08-09 against a MATCHED null -- same number of drops, same weeks,
    # random victims -- it lands 88th-100th percentile at every N in 2..8, so the SIGNAL is real even
    # though the best-scoring N is not (the dollar peak at 3 is one path's noise).
    #
    # RE-ENTRY is what keeps this from being an early-gem killer. A fresh catalyst is pre-run-up by
    # construction, so a backward-looking optimizer rejects it during exactly the weeks it is most worth
    # holding; a permanent drop turns that lag into a life sentence (non-negotiable #2). Two ways back:
    #   unfunded_cooldown_weeks  N weeks after the drop the name is eligible again, streak cleared. 0 = never.
    #   unfunded_reentry_on_new_catalyst  the name returns the moment the curator names it under a
    #                            DIFFERENT thesis than the one it was dropped on -- a new bet, not the old one.
    drop_unfunded = int(fm.get("drop_unfunded_weeks", 0) or 0)   # 0 = OFF
    cooldown = int(fm.get("unfunded_cooldown_weeks", 0) or 0)    # 0 = drops are permanent (pre-2026-08-09 behavior)
    reentry_new_cat = bool(fm.get("unfunded_reentry_on_new_catalyst", False))
    dropped_at: dict[str, tuple[int, str]] = {}                  # ticker -> (week dropped, thesis it was dropped on)
    unfunded_streak = {}

    def _is_dropped(t: str, k: int, wk_thesis: dict) -> bool:
        """Still excluded this week? Checks both re-entry routes."""
        rec = dropped_at.get(t)
        if rec is None:
            return False
        dk, dth = rec
        if cooldown and (k - dk) >= cooldown:
            del dropped_at[t]; unfunded_streak[t] = 0
            return False
        # released only when the name is being carried for a reason it was NOT dropped on
        if reentry_new_cat and wk_thesis.get(t) and dth not in wk_thesis[t]:
            del dropped_at[t]; unfunded_streak[t] = 0
            return False
        return True
    for k, a in enumerate(anchors):
        for p in scans[a]:                      # first-seen scan per ticker -- the ranked cull's freshness tier
            first_k.setdefault(p["ticker"], k)
        if picker is not None:                  # the metadata the picker ranks on (catalyst arc; NO conviction, NO P&L)
            for p in scans[a]:
                t = p["ticker"]
                meta[t] = {"catalyst": (p.get("thesis") or "")[:160],
                           "milestones": str(p.get("milestones") or "")[:200],
                           "exit_condition": str(p.get("exit_advice") or p.get("exit_case") or "")[:140]}
        i = score.entry_index(days, a.strftime("%Y-%m-%dT%H:%M:%S%z"), fm.get("t_update_days"))
        reb.append(None if i is None else i)
        if i is not None:
            # PORTFOLIO cull: keep the top-N tickers, then append the always_include anchors to the optimizer.
            # Anchors are NOT competing agents -- they ride post-cull so idle capital always has a home.
            # No gates, no conviction: the picker ranks (LLM keep-list); without one, a deterministic keep-first-N.
            # ALL of this week's theses per ticker, not just one. A ticker can be a vehicle of two live
            # events at once (it joins the second as a PEER, which the same-ticker guard does not cover
            # -- measured at 9% of ticker-scans), and a dict comprehension would silently keep whichever
            # pick came last, answering "has the thesis changed?" against an arbitrary one of two.
            wk_thesis: dict = {}
            for p in scans[a]:
                wk_thesis.setdefault(p["ticker"], set()).add(p.get("thesis") or "")
            ev = [t for t in watch[a] if not _is_dropped(t, k, wk_thesis)]   # live events (unfunded-pruned ones excluded)
            live_ev = list(ev)                                        # all live this week -> feeds the unfunded streak
            if max_watch and len(ev) > max_watch:
                if picker is not None:
                    cm = [{"ticker": t, **meta.get(t, {}), "weeks_alive": k - first_k.get(t, k)} for t in ev]
                    keep = picker(cm, max_watch, context=str(a.date()))
                    ev = [t for t in keep if t in ev][:max_watch] or ev[:max_watch]
                elif cull_rank == "trend":
                    ev = _ranked_cull(ev, max_watch, panel, days[i], lookback,
                                      first_k, k, fresh_slots, fresh_scans)
                else:
                    ev = ev[:max_watch]         # legacy keep-first-N over sorted(holding) = ALPHABETICAL
            uni = list(dict.fromkeys(ev + [t for t in always if t in valid]))
            watch[a] = ev
            if seed_holdings and k == 0:
                # THE FIRST REBALANCE IS THE HANDOVER, not an optimisation. A continuation book opens
                # holding what the prior book held; the optimizer takes over from the NEXT rebalance.
                # Only names with a price on the day survive -- a seeded ticker the panel cannot price
                # would silently take a weight and never move.
                _sd = {t: v for t, v in seed_holdings.items()
                       if t in panel.columns and pd.notna(panel.loc[days[i], t])}
                _tot = sum(_sd.values()) or 1.0
                w = {t: v / _tot for t, v in _sd.items()}
                if len(_sd) != len(seed_holdings):
                    print(f"  seed: {len(seed_holdings) - len(_sd)} of {len(seed_holdings)} seeded "
                          f"tickers had no price on {days[i].date()} and were dropped", flush=True)
            else:
                w = (curator._optimized_weights(uni, panel, days[i], fm, lookback) or {}) if uni else {}
            # ANCHOR FALLBACK. The comment above says idle capital "always has a home", and until
            # 2026-08-22 that was not true: the anchors ride in `uni` but go through the SAME
            # all()-history filter as everything else, so whatever killed the optimizer killed them
            # too, and `or {}` then discarded the lot. The book held NOTHING -- not even BIL -- for
            # 35% of the backtest. Park in the anchors instead, which is what always_include is for.
            if not w:
                _anc = [t for t in always if t in valid and t in panel.columns
                        and pd.notna(panel.loc[days[i], t])]
                if _anc:
                    w = {t: 1.0 / len(_anc) for t in _anc}
                    print(f"  {days[i].date()}: optimizer returned nothing -- parking in anchors "
                          f"{_anc}", file=sys.stderr)
            week_w[k] = w
            if drop_unfunded > 0:               # an event unfunded (optimizer weight ~0) for N straight weeks is culled
                funded = {t for t in w if w.get(t, 0) > 0.01}
                for t in live_ev:
                    if t in funded:
                        unfunded_streak[t] = 0
                    else:
                        unfunded_streak[t] = unfunded_streak.get(t, 0) + 1
                        if unfunded_streak[t] >= drop_unfunded and t not in dropped_at:
                            _th = wk_thesis.get(t) or {""}
                            dropped_at[t] = (k, sorted(_th)[0])

    value, spyval, log = capital, capital, []
    rows = [{"date": str(days[reb[0]].date()) if reb[0] else str(anchors[0].date()),
             "value": capital, "spy": capital, "held": ""}]
    for k in range(len(anchors) - 1):
        i, j = reb[k], reb[k + 1]
        if i is None or j is None or j <= i:
            continue
        d0, d1, w = days[i], days[j], week_w.get(k, {})
        ret = sum(w.get(t, 0) * (panel.loc[d1, t] / panel.loc[d0, t] - 1)
                  for t in w if pd.notna(panel.loc[d0, t]) and pd.notna(panel.loc[d1, t]))
        value *= (1 + ret)
        spyval *= panel.loc[d1, score.BENCHMARK] / panel.loc[d0, score.BENCHMARK]
        held = ";".join(f"{t}:{w[t]:.2f}" for t in sorted(w, key=lambda x: -w[x]))
        rows.append({"date": str(d1.date()), "value": round(value, 2), "spy": round(spyval, 2),
                     "held": held})
        log.append({"week": str(anchors[k].date()), "watchlist": ";".join(watch[anchors[k]]),
                    "weights": held, "week_return": round(ret, 4)})
    out = {"final": value, "spy_final": spyval, "rows": rows, "log": log, "weeks": len(anchors),
           "watch": {a: watch[a] for a in anchors},   # pruned sticky watch, so the dashboard matches
           "agent_precision": _agent_precision(scans, panel, fm)}   # unmasked curator-skill metric
    if daily:
        out["daily"] = _daily_series(panel, days, reb, week_w, capital, overlay, overlay_anchor,
                                     buyhold=[t for t in bh_basket if t in valid])
    return out


def _daily_series(panel, days, reb, week_w, capital, overlay=OVERLAY, overlay_anchor=OVERLAY_ANCHOR,
                  buyhold: list[str] | None = None) -> dict | None:
    """Daily value/alloc: hold each week's weights from its rebalance day until the next."""
    starts = [r for r in reb if r is not None]
    if not starts:
        return None
    d_idx = days[starts[0]:]
    seg = {reb[k]: week_w.get(k, {}) for k in week_w if reb[k] is not None}  # pos -> weights
    daily_ret = panel.pct_change()
    all_t = sorted({t for w in seg.values() for t in w})
    alloc = pd.DataFrame(0.0, index=d_idx, columns=all_t)
    cur, val, values = {}, capital, []
    gain: dict = {}                       # per-ticker cumulative $ P&L (sums to total portfolio gain)
    gain_series = {t: [] for t in all_t}  # per-DAY cumulative $ gain per ticker (for the per-agent plot)
    for n, d in enumerate(d_idx):
        pos = days.get_loc(d)
        if pos in seg:
            cur = seg[pos]
        if n > 0:
            vprev = val                   # dollars at start of day d earn day d's price move
            for t in cur:
                r = daily_ret.loc[d, t]
                if pd.notna(r):
                    gain[t] = gain.get(t, 0.0) + vprev * cur[t] * r
            val *= 1 + sum(cur.get(t, 0) * daily_ret.loc[d, t] for t in cur
                           if pd.notna(daily_ret.loc[d, t]))
        values.append(round(val, 2))
        for t in cur:
            alloc.loc[d, t] = cur[t]
        for t in all_t:                   # snapshot the running cumulative gain (flatlines after exit)
            gain_series[t].append(round(gain.get(t, 0.0), 2))
    spy = panel[score.BENCHMARK].reindex(d_idx).ffill()
    spy_val = [round(capital * v, 2) for v in (spy / spy.iloc[0]).tolist()]
    overlay_vals = None
    if overlay in panel.columns:
        ov = panel[overlay].reindex(d_idx).ffill()
        # normalize the gem overlay to `capital` (initial_investment_usd) at DAY 1 of the trace, so it starts
        # at the SAME $ as the portfolio + SPY (both begin at capital) and is directly comparable across the era.
        base = next((float(ov.iloc[i]) for i in range(len(ov)) if pd.notna(ov.iloc[i]) and ov.iloc[i] > 0), None)
        if base:
            overlay_vals = [None if pd.isna(v) else round(float(v) / base * capital, 2) for v in ov.tolist()]
    # BUY-AND-HOLD baseline: the `starter_watchlist`, bought equal-DOLLAR on day 1 and never touched.
    # Same `capital` and same day-1 start as the curated book and SPY, so all three curves are directly
    # comparable. Weights drift after day 1 -- that is the point: this is what NOT rebalancing looks like.
    bh_val = None
    held = [t for t in (buyhold or []) if t in panel.columns]
    if held:
        norm = panel[held].reindex(d_idx).ffill()
        base = norm.iloc[0]
        held = [t for t in held if pd.notna(base[t]) and base[t] > 0]
        if held:
            bh = (norm[held] / base[held]).mean(axis=1)     # equal-dollar at inception == mean of price relatives
            bh_val = [None if pd.isna(v) else round(capital * float(v), 2) for v in bh.tolist()]

    alloc = alloc.loc[:, (alloc.abs().sum() > 1e-9)]
    cash = [max(0.0, round(1 - float(alloc.loc[d].sum()), 4)) for d in d_idx]
    return {"dates": [d.strftime("%Y-%m-%d") for d in d_idx], "value": values, "spy": spy_val,
            "bh": bh_val, "bh_tickers": held,
            "overlay": overlay_vals, "overlay_ticker": overlay, "overlay_anchor": overlay_anchor,
            "alloc": {t: [round(x, 4) for x in alloc[t]] for t in alloc.columns}, "cash": cash,
        "gain": {t: round(v, 2) for t, v in gain.items()}, "gain_series": gain_series}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2026-02-13")
    ap.add_argument("--end", default="2026-06-18")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--capital", type=float, default=None,
                    help="override; default = initial_investment_usd from investor_profile.backtest.md")
    ap.add_argument("--scan-only", action="store_true", help="print the weekly scans, skip backtest")
    ap.add_argument("--fixture", default=None,
                    help="path to a fixed article set (perfect-retrieval mechanics test, no live search)")
    ap.add_argument("--gdelt", action="store_true",
                    help="realistic backtest firehose: real date-honored GDELT headlines per week")
    ap.add_argument("--seed", default=None,
                    help="article set to inject into the GDELT firehose (the early niche pieces GDELT misses)")
    ap.add_argument("--rebalance-days", type=int, default=None,
                    help="scan/rebalance cadence in days; also the news window (default: rebalance_days from profile)")
    ap.add_argument("--lookback-days", type=int, default=None,
                    help="override the news window only (advanced; defaults to the rebalance cadence)")
    ap.add_argument("--out", default=str(REPO_ROOT / "data" / "windows" / "firehose_scans.json"))
    args = ap.parse_args(argv)

    _load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        return 2
    fm = load_financial_model(str(REPO_ROOT / "investor_profile.backtest.md"))
    rebalance = args.rebalance_days if args.rebalance_days is not None else resolve_cadence(fm)
    lookback = args.lookback_days if args.lookback_days is not None else fm.get("news_lookback_days")

    scans = run_scans(args.start, args.end, rebalance, args.model, args.workers,
                      fixture=args.fixture, gdelt=args.gdelt, seed=args.seed, lookback_days=lookback)
    serial = {str(a.date()): scans[a] for a in scans}
    for v in serial.values():
        for p in v:
            p.pop("anchor", None)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(serial, indent=2, default=str))

    print("\n=== weekly firehose picks (press-named gems) ===")
    for a in scans:
        live = [f"{p['ticker']}[{'LIVE' if p.get('thesis_live', True) else 'EXIT'}]"
                for p in scans[a]]
        print(f"  {a.date()}: {', '.join(live) if live else '—'}")
    if args.scan_only:
        return 0

    cap = args.capital if args.capital is not None else float(fm.get("initial_investment_usd", 50_000))
    bt = backtest(scans, fm, cap)
    print(f"\n=== weekly-rebalanced firehose portfolio vs SPY ({bt['weeks']} weeks) ===")
    print(f"  firehose: ${cap:,.0f} -> ${bt['final']:,.0f} "
          f"({bt['final']/cap-1:+.1%})")
    print(f"  SPY:      ${cap:,.0f} -> ${bt['spy_final']:,.0f} "
          f"({bt['spy_final']/cap-1:+.1%})")
    # when did BWET enter / exit?
    bwet = [r["week"] for r in bt["log"] if "BWET" in r["watchlist"]]
    if bwet:
        print(f"  BWET held weeks: {bwet[0]} .. {bwet[-1]} ({len(bwet)} weeks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
