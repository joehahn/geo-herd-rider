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
from util import load_dotenv as _load_dotenv, news_domains, scan_anchors, MAX_TEXT  # noqa: E402

MODEL = "claude-opus-4-8"
WORKERS = 8

# Generic market/theme queries for the GDELT firehose — deliberately NOT "BWET"/"Breakwave"
# (that would hand-point at the answer). The analyst watches the right beats; the curator must
# still discover the ticker. Tune freely.
GDELT_QUERIES = [
    "ETF", "Hormuz", '"tanker rates"', '"freight rates"',
    '"best performing"', '"oil price"', '"energy stocks"', '"stock surge"',
]  # GDELT needs single words or QUOTED phrases — bare multi-word queries return nothing.
GDELT_WEEK_CAP = 80          # max GDELT headlines fed to the LLM per week (seeds always kept)

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
         domains: list[str]) -> list[dict]:
    """Firehose scan as of `anchor` (look-ahead-safe). Returns the press-named gems."""
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
    for _ in range(6):
        resp = client.messages.create(**kw)
        u = costs.extract(resp.usage)
        for k in tally:
            tally[k] += u.get(k, 0)
        text = "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    costs.record("firehose", model, f"scan-{anchor.date()}", tally)
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
        import gdelt as gd
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
        gpool = gd.pool(qs, win_start, anchors[-1], chunk_days=pool_chunk_days, per=pool_per,
                        cache_path=str(cache_f))   # resumable: survives sleep/kill, resumes next run
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


EXIT_PATIENCE = 2   # consecutive EXPLICIT thesis-dead reads before exiting (hysteresis vs churn)
MAX_STALE = 4       # weeks a held name may go UNMENTIONED before we drop it (no thesis confirmation)


def _stateful_watch(scans: dict) -> dict:
    """Turn the stateless per-week scans into a STICKY position portfolio (fixes choppy holds).

    A name ENTERS when first read thesis_live=True, and stays held through coverage gaps and
    one-off noise. It EXITS on a CONFIRMED catalyst death (thesis_live=False on >=EXIT_PATIENCE
    consecutive *reads*), prolonged silence (unmentioned >=MAX_STALE weeks), or — the moment the
    agent flags catalyst_resolved=True — a HARD exit that honors that verdict without hysteresis
    (the catalyst is definitively done). Single-week flip-flops no longer churn the position."""
    anchors = list(scans)
    holding, dead, stale, out = {}, {}, {}, {}
    for a in anchors:
        resolved = {p["ticker"] for p in scans[a] if p.get("catalyst_resolved")}
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
                if dead[t] >= EXIT_PATIENCE:
                    del holding[t]
            else:                            # unmentioned this week — tolerate, but not forever
                stale[t] += 1
                if stale[t] >= MAX_STALE:
                    del holding[t]
        out[a] = sorted(holding)
    return out


def _agent_precision(scans: dict, panel) -> list:
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
    return sorted(rows, key=lambda r: (r["ret"] is None, r["ret"] or 0))


OVERLAY, OVERLAY_ANCHOR = "BWET", "2026-02-20"  # the motivating gem + carrier->W.Med transit


def backtest(scans: dict, fm: dict, capital: float = 50_000.0, daily: bool = False,
             panel: pd.DataFrame | None = None,
             overlay: str = OVERLAY, overlay_anchor: str = OVERLAY_ANCHOR) -> dict:
    """Weekly-rebalanced portfolio from the firehose watchlist vs SPY. With daily=True, also
    returns a daily value/allocation series (weekly weights held across days) for the dashboard.

    `panel` lets a caller inject a FROZEN adjusted-close panel (DatetimeIndex, tz-naive) instead of
    fetching live — used by the golden-snapshot regression replay so results are deterministic
    (live yfinance prices drift day to day). Default None = fetch live, as before."""
    lookback = int(fm.get("lookback_period_days", curator.BACKTEST_LOOKBACK_DAYS))
    prune_k = int(fm.get("prune_zero_weight_weeks", 0) or 0)    # 0 = off; drop a name after K zero-wt weeks
    trail_stop = float(fm.get("trailing_stop_pct", 0) or 0)     # 0 = off; force-exit a held name once it's this fraction below its trailing high (mechanical peak-exit)
    max_agents = int(fm.get("max_agents", 0) or 0)             # 0 = off; keep only the top-N agents (by catalyst conviction) in the weekly watchlist
    spy_agent = int(fm.get("spy_agent_conviction", 0) or 0)     # SPY as an always-on "agent" that always recommends SPY: a synthetic candidate at this
    bench = score.BENCHMARK                                     #   conviction that events must OUT-RANK to be held; else capital parks in SPY. 0 = off.
    anchors = list(scans)
    watch = _stateful_watch(scans)  # sticky hold + hard-exit on catalyst_resolved
    tickers = {score.BENCHMARK, overlay} | {t for w in watch.values() for t in w}
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
    zero_streak, pruned = {}, set()   # visibility: drop chronically-unfunded names; cap funded concurrency
    trail_hi, stopped = {}, set()     # mechanical trailing-stop state (per currently-held name)
    conv = {}                         # running last-known catalyst-conviction per ticker (for the max_agents cap)
    for k, a in enumerate(anchors):
        for p in scans[a]:            # carry the latest catalyst-conviction the agent assigned each ticker
            conv[p["ticker"]] = p.get("conviction", conv.get(p["ticker"], 5))
        i = score.entry_index(days, a.strftime("%Y-%m-%dT%H:%M:%S%z"), fm.get("t_update_days"))
        reb.append(None if i is None else i)
        if i is not None:
            d = days[i]
            held = set(watch[a])
            for t in list(trail_hi):                  # names no longer held -> reset their trailing state
                if t not in held:
                    trail_hi.pop(t, None); stopped.discard(t)
            wl = []
            for t in watch[a]:
                if t in pruned:
                    continue
                if trail_stop and t != bench and t in valid:   # mechanical peak-exit (NOT the LLM using price)
                    s = panel[t].dropna().loc[:d]
                    if len(s):
                        px = float(s.iloc[-1])
                        trail_hi[t] = max(trail_hi.get(t, px), px)
                        if px <= trail_hi[t] * (1 - trail_stop):
                            stopped.add(t)
                    if t in stopped:
                        continue                       # stopped out -> force exit this week
                wl.append(t)
            # SPY agent-agent: an always-on agent that always recommends SPY -- a synthetic candidate at
            # spy_agent conviction that competes in the weekly max_agents ranking like any other event.
            # A weaker-conviction event that ranks below it is displaced when the watchlist is full; when
            # nothing beats it, capital parks in SPY. Replaces the mechanical hold_benchmark add.
            cand = list(wl)
            if spy_agent and bench in valid:
                conv[bench] = spy_agent
                if bench not in cand:
                    cand.append(bench)
            if max_agents and len(cand) > max_agents:  # keep only the top-N candidates by conviction (SPY competes)
                keep = set(sorted(cand, key=lambda t: (-conv.get(t, 0), t))[:max_agents])
                cand = [t for t in cand if t in keep]
            uni = cand
            w = (curator._optimized_weights(uni, panel, days[i], fm, lookback) or {}) if uni else {}
            if prune_k:                                       # a name the optimizer keeps starving -> drop
                for t in wl:
                    zero_streak[t] = 0 if w.get(t, 0) > 1e-9 else zero_streak.get(t, 0) + 1
                    if zero_streak[t] >= prune_k:
                        pruned.add(t)
            watch[a] = [t for t in cand if t != bench]        # event watchlist (SPY funded via the weights)
            week_w[k] = w

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
           "agent_precision": _agent_precision(scans, panel)}   # unmasked curator-skill metric
    if daily:
        out["daily"] = _daily_series(panel, days, reb, week_w, capital, overlay, overlay_anchor)
    return out


def _daily_series(panel, days, reb, week_w, capital, overlay=OVERLAY, overlay_anchor=OVERLAY_ANCHOR) -> dict | None:
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
        ai = next((i for i, d in enumerate(d_idx) if d >= pd.Timestamp(overlay_anchor)), None)
        if ai is not None and pd.notna(ov.iloc[ai]) and ov.iloc[ai] > 0:
            scale = values[ai] / float(ov.iloc[ai])
            overlay_vals = [None if pd.isna(v) else round(float(v) * scale, 2) for v in ov.tolist()]
    alloc = alloc.loc[:, (alloc.abs().sum() > 1e-9)]
    cash = [max(0.0, round(1 - float(alloc.loc[d].sum()), 4)) for d in d_idx]
    return {"dates": [d.strftime("%Y-%m-%d") for d in d_idx], "value": values, "spy": spy_val,
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
                    help="override; default = initial_investment_usd from investor_profile.md")
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
    fm = load_financial_model(str(REPO_ROOT / "investor_profile.md"))
    rebalance = args.rebalance_days if args.rebalance_days is not None else int(fm.get("rebalance_days", 7))
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
