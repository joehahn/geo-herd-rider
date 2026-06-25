"""agent.py — the per-event agent loop: scout -> fan-out -> (journal) -> picks.

The variant the harness A/Bs against the single-scan baseline. Each weekly anchor:
  1. SCOUT (one aggregate call) reads the week's firehose and proposes candidate events.
  2. FAN-OUT: for every open event + new candidate, a per-event agent reads its prior journal
     entry (memory) + this week's news targeted to that event, then writes a new entry — an
     assessment, the thesis_live/exit call, and hotlinked sources.
  3. The live events' tickers become the week's picks (same shape the backtest/optimizer expects).

Journals are the agent's memory and carry the thesis forward (continuity -> steadier exits). In
backtest they live in memory and are dumped at the end (data/windows/agent_journals.json); in
forward they'd be per-event files + dashboard pages.

GUARDRAIL: the LLM never forecasts HOW HIGH (magnitude/target — never feeds sizing, which is
mechanical). It DOES judge WHEN TO EXIT — when the catalyst resolves (the thesis_live call). See
agent_design.md.

Look-ahead: backtest retrieval is the date-bounded GDELT pool (+ seeds) filtered to each event;
targeted live search is clean only forward. All backtest numbers are upper bounds.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
import costs  # noqa: E402
import gdelt as gd  # noqa: E402
import firehose  # noqa: E402
import llm  # noqa: E402
from util import scan_anchors  # noqa: E402

# Strict JSON schemas for the structured-output path (OpenRouter/DeepSeek — guarantees parseable
# JSON; the Anthropic path ignores these and parses free-form). additionalProperties=false keeps
# the model from inventing fields — incl. a price target (the no-magnitude guardrail, at the wire).
SCOUT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["candidates"],
               "properties": {"candidates": {"type": "array", "items": {
                   "type": "object", "additionalProperties": False,
                   "required": ["ticker", "thesis", "why_now"],
                   "properties": {"ticker": {"type": "string"}, "thesis": {"type": "string"},
                                  "why_now": {"type": "string"}}}}}}
AGENT_SCHEMA = {"type": "object", "additionalProperties": False,
               "required": ["thesis_live", "exit_advice", "assessment", "news_claims", "sources"],
               "properties": {"thesis_live": {"type": "boolean"},
                              "exit_advice": {"type": "string"}, "assessment": {"type": "string"},
                              "news_claims": {"type": "string"},
                              "sources": {"type": "array", "items": {"type": "string"}}}}

CANDIDATE_CAP = 3        # max candidate events the scout proposes per week (bound the fan-out)
WINDOW_CAP = 80          # max firehose headlines shown to the scout per week

SCOUT_SYSTEM = """You are a markets desk scanning a week of financial-news headlines to DISCOVER
candidate hidden-gem events — a specific US-listed ticker (incl. ADRs / theme ETFs) the press is
naming as a thesis-driven mover, ideally still early/under-the-radar.

BE RUTHLESSLY SELECTIVE. Propose a ticker ONLY if the press frames it as a STANDOUT, SUSTAINED
thesis-driven mover with a real, nameable catalyst — NOT a one-off mention, a routine daily gainer,
or a name buried in a list. Most weeks warrant 0-1 candidates; rarely more than 2. When in doubt,
propose nothing. Prefer the PUREST vehicle for a theme (a rate/commodity ETN or clean pure-play
over diluted operators; a single ADR over a broad ETF).

You forecast NOTHING. Output ONLY JSON: {"candidates":[{"ticker":"BWET","thesis":"<=12 words: the
catalyst","why_now":"<=12 words"}]}. Empty is the common, correct answer."""

AGENT_SYSTEM = """You manage ONE event for an event-driven book. You are given the event, YOUR
prior weekly note (your memory), and THIS week's news for this event. Write the new weekly note.

Decide:
  thesis_live  — TRUE while the driving CATALYST is still active/unresolved; FALSE once it RESOLVES
                 (ceasefire signed and shipping resumes, chokepoint reopens, the supply shock ends,
                 the policy passes/fails). This is the HOLD/EXIT switch. Use common sense about WHEN
                 the event is over — that is your job. Mainstream hype ("up 600%, everyone in") is
                 NOT resolution; do NOT exit just because a trade has gotten crowded.
                 BUT BE SKEPTICAL ON ENTRY: if this event has NO clear, ongoing catalyst — it was a
                 one-off mention, a routine gainer, or there's no real sustained thesis here — set
                 thesis_live=FALSE NOW. Do not keep noise alive; only a genuine, still-active
                 catalyst earns thesis_live=true.
  exit_advice  — <=20 words: the concrete condition that would end the thesis.
  assessment   — <=40 words: what changed this week and your read, continuous with your prior note.
  news_claims  — OPTIONAL <=12 words: attribute any size/return figure to the PRESS ("press cites
                 ~600% YTD"). NEVER your own price target or magnitude forecast — you do not predict
                 how high it goes.

Output ONLY JSON: {"thesis_live":true,"exit_advice":
"...","assessment":"...","news_claims":"","sources":["url","url"]}."""


class ScoutCandidate(BaseModel):
    """A discovered candidate event. extra='ignore' drops anything the LLM adds beyond these."""
    model_config = ConfigDict(extra="ignore")
    ticker: str
    thesis: str = ""
    why_now: str = ""

    @field_validator("ticker")
    @classmethod
    def _up(cls, v: str) -> str:
        return str(v).strip().upper()


class JournalEntry(BaseModel):
    """A weekly per-event note. GUARDRAIL (non-negotiable #1): there is NO field for a price
    target / magnitude / position size, and extra='ignore' means any such key the LLM emits is
    SILENTLY DROPPED here — it can never reach the optimizer. The LLM only sets composition,
    the thesis_live/exit call, and prose. news_claims is attribution of what the PRESS says."""
    model_config = ConfigDict(extra="ignore")
    thesis_live: bool = True
    exit_advice: str = ""
    assessment: str = ""
    news_claims: str = ""        # attribution only ("press cites ~600% YTD"), never our forecast
    sources: list[str] = []
    vehicles: list[str] = []     # event-first only: the current best vehicle(s) for this event


def _extract(text: str) -> dict:
    t = text.strip()
    if "```" in t:
        for c in reversed(t.split("```")):
            c = c.strip()
            c = c[4:].strip() if c.startswith("json") else c
            if c.startswith("{"):
                return json.loads(c)
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e > s:
        return json.loads(t[s:e + 1])
    return {}


def _block(arts: list[dict]) -> str:
    return "\n".join(f"[{a.get('published_date','')} | {a.get('source','')}] {a.get('title','')}"
                     f" — {a.get('snippet','')[:200]} ({a.get('url','') or 'no url'})" for a in arts)


def scout(client, anchor: pd.Timestamp, arts: list[dict]) -> list[dict]:
    if not arts:
        return []
    user = (f"Week ending {anchor.date()}. Headlines:\n\n{_block(arts)}\n\n"
            "Which tickers is the press naming as thesis-driven movers? Output the JSON.")
    txt = client.complete(SCOUT_SYSTEM, user, use_web_search=False, stage="agent",
                          label=f"scout-{anchor.date()}", json_schema=SCOUT_SCHEMA)
    cands = _extract(txt).get("candidates", [])
    out = []
    for c in cands[:CANDIDATE_CAP]:
        try:
            m = ScoutCandidate(**c)          # validates + drops any extra (e.g. a price target)
        except Exception:  # noqa: BLE001
            continue
        if m.ticker:
            out.append(m.model_dump())
    return out


def _filter_pool(arts: list[dict], event: dict) -> list[dict]:
    """Filter an article set to this event's coverage (ticker or thesis keywords)."""
    tk = event["ticker"].lower()
    kws = [w for w in event.get("thesis", "").lower().replace(",", " ").split() if len(w) > 4]
    hits = []
    for a in arts:
        hay = (a.get("title", "") + " " + a.get("snippet", "")).lower()
        if tk in hay or any(k in hay for k in kws):
            hits.append(a)
    return hits


_event_pools: dict[str, list] = {}   # ticker -> its own GDELT pool (memoized per run)


def _event_terms(event: dict) -> list[str]:
    """Monitoring queries for a HELD event: its ticker + a key thesis phrase. This is legitimate
    (tracking a position we already hold), NOT discovery — so it does not bias what we discover."""
    tk = event["ticker"]
    words = [w for w in event.get("thesis", "").replace(",", " ").split() if len(w) > 4][:3]
    qs = [tk]
    if words:
        qs.append('"' + " ".join(words) + '"')
    return qs


def targeted_pool(event: dict, win_start, win_end, chunk_days, per) -> list[dict]:
    """Per-event targeted retrieval: GDELT search on the EVENT'S OWN terms (incl. its resolution
    coverage, e.g. a ceasefire), cached on disk + memoized. Build these SEQUENTIALLY (the GDELT
    throttle isn't thread-safe), then the fan-out reads them instantly."""
    import hashlib
    tk = event["ticker"]
    if tk in _event_pools:
        return _event_pools[tk]
    qs = _event_terms(event)
    key = hashlib.md5(f"evt{tk}{qs}{pd.Timestamp(win_start).date()}{pd.Timestamp(win_end).date()}".encode()).hexdigest()[:10]
    cache_f = REPO_ROOT / "data" / "windows" / f"gdelt_event_{key}.json"
    pool = gd.pool(qs, win_start, win_end, chunk_days=chunk_days, per=per, cache_path=str(cache_f))
    _event_pools[tk] = pool
    return pool


def event_agent(client, anchor: pd.Timestamp, event: dict, prior: dict | None,
                news: list[dict]) -> dict:
    pj = json.dumps(prior, default=str) if prior else "(none — this is the first week)"
    nb = _block(news) if news else "(no fresh coverage for this event this week)"
    user = (f"Event: {event['ticker']} — {event.get('thesis','')}\nWeek ending {anchor.date()}.\n"
            f"Your prior note: {pj}\n\nThis week's news for this event:\n{nb}\n\nWrite the new note (JSON).")
    txt = client.complete(AGENT_SYSTEM, user, use_web_search=False, stage="agent",
                          label=f"agent-{event['ticker']}-{anchor.date()}", json_schema=AGENT_SCHEMA)
    d = _extract(txt)
    try:
        e = JournalEntry(**d)                # any magnitude/target key in d is dropped here
    except Exception:  # noqa: BLE001
        e = JournalEntry()                   # malformed -> safe default (thesis_live stays true)
    return {"date": anchor.date().isoformat(), "thesis_live": e.thesis_live,
            "exit_advice": e.exit_advice, "assessment": e.assessment,
            "news_claims": e.news_claims, "sources": [u for u in e.sources if u][:6]}


def run_agent_scans(start, end, rebalance_days, model, workers, queries=None, seed=None,
                    pool_chunk_days=90, pool_per=150, provider="anthropic", targeted=True) -> dict:
    """Scout -> per-event fan-out across the window. Returns {anchor: [picks]} like the single
    scan, so backtest()/scoring are unchanged. Weeks run SEQUENTIALLY (journals are stateful);
    the fan-out within a week runs in parallel. provider/model are provider-agnostic (llm.py),
    so the SAME loop runs on Opus or on a cheap OpenRouter model (DeepSeek) for dev/testing."""
    import hashlib
    client = llm.make_client(provider, model)
    print(f"Agent: provider={provider} model={model}", file=sys.stderr)
    anchors = scan_anchors(start, end, rebalance_days)
    qs = queries or firehose.GDELT_QUERIES
    win_start = anchors[0] - pd.Timedelta(days=35)
    key = hashlib.md5(f"{qs}{win_start.date()}{anchors[-1].date()}{pool_chunk_days}{pool_per}".encode()).hexdigest()[:10]
    cache_f = REPO_ROOT / "data" / "windows" / f"gdelt_pool_{key}.json"
    cache_f.parent.mkdir(parents=True, exist_ok=True)
    print(f"Agent: scout->fan-out over {len(anchors)} weeks; pool fetch/resume ...", file=sys.stderr)
    gpool = gd.pool(qs, win_start, anchors[-1], chunk_days=pool_chunk_days, per=pool_per,
                    cache_path=str(cache_f))
    seeds = firehose._fixture_articles(seed) if seed else []
    print(f"  pool {len(gpool)} + {len(seeds)} seeds; running agents ...", file=sys.stderr)

    journals: dict[str, dict] = {}   # ticker -> {ticker, thesis, status, entries:[]}
    out: dict[pd.Timestamp, list[dict]] = {}
    # per-week checkpoint so a long agent run survives sleep/kill and RESUMES (the loop is otherwise
    # in-memory; journals were dumped only at the end). Keyed by the run's params.
    import os
    rsig = hashlib.md5(f"{provider}{model}{start}{end}{rebalance_days}{seed}{targeted}{qs}".encode()).hexdigest()[:10]
    resume_f = REPO_ROOT / "data" / "windows" / f"agent_resume_{rsig}.json"
    done: set[str] = set()
    if resume_f.exists():
        st = json.loads(resume_f.read_text())
        journals, done = st["journals"], set(st["done"])
        out = {pd.Timestamp(k): v for k, v in st["out"].items()}
        print(f"  resuming agent run: {len(done)}/{len(anchors)} weeks already done", file=sys.stderr)
    for a in anchors:
        if a.isoformat() in done:        # already computed in a prior (interrupted) run
            continue
        win = (firehose._window(seeds, a, rebalance_days)
               + sorted(firehose._window(gpool, a, rebalance_days),
                        key=lambda x: x.get("published_date", ""), reverse=True)[:WINDOW_CAP])
        cands = scout(client, a, win)
        open_ev = [{"ticker": t, "thesis": j["thesis"]} for t, j in journals.items()
                   if j["status"] == "live"]
        seen = {e["ticker"] for e in open_ev}
        events = open_ev + [c for c in cands if c["ticker"] not in seen]

        # targeted retrieval: build each event's own GDELT pool SEQUENTIALLY (throttle-safe), then
        # the per-event agents (parallel) read them instantly. Monitoring a held event != discovery.
        # Skipped in the fast variant (targeted=False) — agents read the broad pool filtered to them.
        if targeted:
            for ev in events:
                targeted_pool(ev, win_start, anchors[-1], pool_chunk_days, pool_per)

        def work(ev):
            j = journals.get(ev["ticker"])
            prior = j["entries"][-1] if j and j["entries"] else None
            tnews = (firehose._window(targeted_pool(ev, win_start, anchors[-1], pool_chunk_days, pool_per),
                                      a, rebalance_days) if targeted else [])  # event's own coverage
            bnews = _filter_pool(win, ev)                            # broad pool + seeds, filtered to event
            seen_urls, news = set(), []
            for art in tnews + bnews:                               # targeted first; dedup by url
                u = art.get("url", "")
                if u not in seen_urls:
                    seen_urls.add(u); news.append(art)
            return ev, event_agent(client, a, ev, prior, news[:20])

        picks = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for ev, entry in ex.map(work, events) if events else []:
                j = journals.setdefault(ev["ticker"], {"ticker": ev["ticker"],
                                                        "thesis": ev["thesis"], "status": "live",
                                                        "entries": []})
                j["entries"].append(entry)
                j["status"] = "live" if entry["thesis_live"] else "exited"
                picks.append({"ticker": ev["ticker"], "thesis": ev["thesis"],
                              "thesis_live": entry["thesis_live"],
                              "evidence_urls": entry["sources"]})
        out[a] = picks
        done.add(a.isoformat())
        tmp = f"{resume_f}.tmp"                       # atomic checkpoint after each week
        with open(tmp, "w") as fh:
            json.dump({"journals": journals, "done": sorted(done),
                       "out": {k.isoformat(): v for k, v in out.items()}}, fh, default=str)
        os.replace(tmp, resume_f)
    (REPO_ROOT / "data" / "windows" / "agent_journals.json").write_text(
        json.dumps(list(journals.values()), indent=2, default=str))
    return out


# ============================================================================================
# Event-first variant: an EVENT (one catalyst) is the durable unit and owns an EVOLVING set of
# vehicles; a matching step groups this week's candidates into existing events (so RNMBY/RHMTY/
# LMT collapse into one defense event), and the per-event agent picks the current best vehicle(s).
# ============================================================================================

EVENT_MATCH_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["matches"],
    "properties": {"matches": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["ticker", "event"],
        "properties": {"ticker": {"type": "string"}, "event": {"type": "string"}}}}}}

MATCH_SYSTEM = """You group market candidates into EVENTS. An event is ONE underlying catalyst (a
war, an election, a supply shock, a tech wave); MANY tickers can express the same event — e.g.
Rheinmetall's two ADRs RNMBY/RHMTY are one company; SMR/OKLO/CCJ/CEG are all the nuclear-for-AI
event; URA and CCJ are both the uranium event. Given the OPEN events (id + catalyst) and this
week's CANDIDATES (ticker + thesis), assign each candidate to the open event it belongs to (by id)
or "new" if it is a genuinely different catalyst. Output ONLY JSON:
{"matches":[{"ticker":"BWET","event":"<id>|new"}]}."""

EVENT_AGENT_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["thesis_live", "exit_advice", "assessment", "news_claims", "vehicles", "sources"],
    "properties": {"thesis_live": {"type": "boolean"},
        "exit_advice": {"type": "string"}, "assessment": {"type": "string"},
        "news_claims": {"type": "string"},
        "vehicles": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}}}}

EVENT_AGENT_SYSTEM = """You manage ONE event for an event-driven book. You are given the event's
catalyst, its KNOWN vehicles (tickers seen expressing it), your prior weekly note, and this week's
news. Write the new note.

Decide thesis_live / exit_advice / assessment / news_claims as a single-event tracker
would (thesis_live = is the CATALYST still active; flip false only on resolution; be skeptical of
one-off noise; mainstream hype is NOT resolution). PLUS pick **vehicles**: the 1-2
PUREST tickers to HOLD for this event now, chosen from its known vehicles — prefer the cleanest
pure-play / rate-or-commodity ETN / single ADR over diluted, redundant, or tangential names (do
NOT hold five vehicles for one event). You may drop a vehicle that's no longer the best.

You never forecast HOW HIGH (no price target / size — sizing is mechanical); you only judge
composition, the exit, and which vehicle. Output ONLY JSON: {"thesis_live":true,
"exit_advice":"...","assessment":"...","news_claims":"",
"vehicles":["TICKER"],"sources":["url"]}."""


def match_to_events(client, anchor, candidates, events):
    """Map this week's candidates to existing open events (by id) or 'new'. One batched call."""
    if not candidates:
        return {}
    live = {eid: ev for eid, ev in events.items() if ev["status"] == "live"}
    if not live:
        return {c["ticker"]: "new" for c in candidates}
    open_list = "\n".join(f"- {eid}: {ev['catalyst']}" for eid, ev in live.items())
    cand_list = "\n".join(f"- {c['ticker']}: {c['thesis']}" for c in candidates)
    user = f"OPEN EVENTS:\n{open_list}\n\nCANDIDATES:\n{cand_list}\n\nAssign each candidate. Output JSON."
    txt = client.complete(MATCH_SYSTEM, user, use_web_search=False, stage="agent",
                          label=f"match-{anchor.date()}", json_schema=EVENT_MATCH_SCHEMA)
    out = {}
    for m in _extract(txt).get("matches", []):
        tk = str(m.get("ticker", "")).strip().upper()
        if tk:
            out[tk] = str(m.get("event", "new")).strip()
    return out


def _filter_event(arts, event):
    """Broad-pool articles relevant to an event: mention any of its vehicles or catalyst keywords."""
    veh = {v.lower() for v in event["vehicles"]}
    kws = [w for w in event["catalyst"].lower().replace(",", " ").split() if len(w) > 4]
    hits = []
    for a in arts:
        hay = (a.get("title", "") + " " + a.get("snippet", "")).lower()
        if any(v in hay for v in veh) or any(k in hay for k in kws):
            hits.append(a)
    return hits[:20]


def event_agent_v2(client, anchor, event, prior, news):
    pj = json.dumps(prior, default=str) if prior else "(none — first week of this event)"
    nb = _block(news) if news else "(no fresh coverage for this event this week)"
    user = (f"Event catalyst: {event['catalyst']}\nKnown vehicles: {', '.join(sorted(event['vehicles']))}\n"
            f"Week ending {anchor.date()}.\nYour prior note: {pj}\n\nThis week's news:\n{nb}\n\n"
            "Write the new note and pick the current vehicle(s) (JSON).")
    txt = client.complete(EVENT_AGENT_SYSTEM, user, use_web_search=False, stage="agent",
                          label=f"event-{event['id']}-{anchor.date()}", json_schema=EVENT_AGENT_SCHEMA)
    try:
        e = JournalEntry(**_extract(txt))
    except Exception:  # noqa: BLE001
        e = JournalEntry()
    veh = [v.strip().upper() for v in e.vehicles if v.strip()]
    veh = [v for v in veh if v in event["vehicles"]] or sorted(event["vehicles"])[:1]   # known only; fallback
    return {"date": anchor.date().isoformat(), "thesis_live": e.thesis_live,
            "exit_advice": e.exit_advice, "assessment": e.assessment, "news_claims": e.news_claims,
            "sources": [u for u in e.sources if u][:6], "vehicles": veh}


def run_event_agent_scans(start, end, rebalance_days, model, workers, queries=None, seed=None,
                          pool_chunk_days=90, pool_per=150, provider="anthropic", targeted=False,
                          enrich=False) -> dict:
    """Event-first engine: scout -> match candidates into events -> per-event agent picks current
    vehicle(s). The watchlist is the union of each live event's current vehicles. Returns
    {anchor: picks} like the other engines, so backtest()/scoring are unchanged. Per-week resume.

    enrich=True: fill each week's GDELT headlines with their as-of-date Wayback lede (so the
    curator sees the ticker the headline omits), look-ahead-clean (snapshot <= anchor)."""
    import hashlib
    import os
    if enrich:
        import wayback
    client = llm.make_client(provider, model)
    print(f"Event-agent: provider={provider} model={model}", file=sys.stderr)
    anchors = scan_anchors(start, end, rebalance_days)
    qs = queries or firehose.GDELT_QUERIES
    win_start = anchors[0] - pd.Timedelta(days=35)
    key = hashlib.md5(f"{qs}{win_start.date()}{anchors[-1].date()}{pool_chunk_days}{pool_per}".encode()).hexdigest()[:10]
    cache_f = REPO_ROOT / "data" / "windows" / f"gdelt_pool_{key}.json"
    cache_f.parent.mkdir(parents=True, exist_ok=True)
    gpool = gd.pool(qs, win_start, anchors[-1], chunk_days=pool_chunk_days, per=pool_per, cache_path=str(cache_f))
    seeds = firehose._fixture_articles(seed) if seed else []
    print(f"  pool {len(gpool)} + {len(seeds)} seeds; running event-agents ...", file=sys.stderr)

    events: dict[str, dict] = {}   # id -> {id, catalyst, status, vehicles:set, entries:[]}
    out: dict[pd.Timestamp, list[dict]] = {}
    nid = [0]
    rsig = hashlib.md5(f"EV{provider}{model}{start}{end}{rebalance_days}{seed}{targeted}{enrich}{qs}".encode()).hexdigest()[:10]
    enrich_cache = str(REPO_ROOT / "data" / "windows" / f"wayback_{key}.json")
    resume_f = REPO_ROOT / "data" / "windows" / f"agent_resume_{rsig}.json"
    done: set[str] = set()
    if resume_f.exists():
        st = json.loads(resume_f.read_text())
        events = {k: {**v, "vehicles": set(v["vehicles"])} for k, v in st["events"].items()}
        done = set(st["done"]); nid = [st["nid"]]
        out = {pd.Timestamp(k): v for k, v in st["out"].items()}
        print(f"  resuming: {len(done)}/{len(anchors)} weeks done", file=sys.stderr)

    # provenance log: the exact per-week article set (headline + final snippet) the curator read,
    # so we can later audit what it saw — e.g. did a Wayback lede name the ticker it then picked.
    prov_f = REPO_ROOT / "data" / "windows" / f"agent_provenance_{rsig}.json"
    provenance: dict = json.loads(prov_f.read_text()) if prov_f.exists() else {}

    for a in anchors:
        if a.isoformat() in done:
            continue
        gslice = sorted(firehose._window(gpool, a, rebalance_days),
                        key=lambda x: x.get("published_date", ""), reverse=True)[:WINDOW_CAP]
        if enrich:
            wayback.enrich(gslice, a.date().isoformat(), cache_path=enrich_cache)
        seed_slice = firehose._window(seeds, a, rebalance_days)
        win = seed_slice + gslice
        provenance[a.isoformat()] = [
            {"src": src,
             "wayback_hit": src == "gdelt" and bool(x.get("snippet") and x.get("snippet") != x.get("title")),
             "published_date": x.get("published_date", ""), "source": x.get("source", ""),
             "title": x.get("title", ""), "snippet": x.get("snippet", ""), "url": x.get("url", "")}
            for src, lst in (("seed", seed_slice), ("gdelt", gslice)) for x in lst]
        cands = scout(client, a, win)
        # DETERMINISTIC same-ticker guard: a ticker already held by a LIVE event belongs to that
        # event — never open a duplicate (this is what fragmented BWET into 3). Only genuinely NEW
        # tickers go to the (fallible) LLM matcher for cross-ticker grouping.
        held_to_event = {v: eid for eid, ev in events.items() if ev["status"] == "live"
                         for v in ev["vehicles"]}
        new_cands = [c for c in cands if c["ticker"] not in held_to_event]
        match = match_to_events(client, a, new_cands, events) if new_cands else {}
        for c in new_cands:
            tk, eid = c["ticker"], match.get(c["ticker"], "new")
            if eid in events and events[eid]["status"] == "live":
                events[eid]["vehicles"].add(tk)
            else:
                nid[0] += 1
                events[f"ev{nid[0]}"] = {"id": f"ev{nid[0]}", "catalyst": c["thesis"],
                                         "status": "live", "vehicles": {tk}, "entries": []}
        live_events = [ev for ev in events.values() if ev["status"] == "live"]

        def work(ev):
            prior = ev["entries"][-1] if ev["entries"] else None
            return ev, event_agent_v2(client, a, ev, prior, _filter_event(win, ev))

        picks = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for ev, entry in (ex.map(work, live_events) if live_events else []):
                ev["entries"].append(entry)
                ev["status"] = "live" if entry["thesis_live"] else "exited"
                for tk in entry["vehicles"]:
                    picks.append({"ticker": tk, "thesis": ev["catalyst"],
                                  "thesis_live": entry["thesis_live"],
                                  "evidence_urls": entry["sources"]})
        out[a] = picks
        done.add(a.isoformat())
        tmp = f"{resume_f}.tmp"
        with open(tmp, "w") as fh:
            json.dump({"events": {k: {**v, "vehicles": sorted(v["vehicles"])} for k, v in events.items()},
                       "done": sorted(done), "nid": nid[0],
                       "out": {k.isoformat(): v for k, v in out.items()}}, fh, default=str)
        os.replace(tmp, resume_f)
        prov_tmp = f"{prov_f}.tmp"
        Path(prov_tmp).write_text(json.dumps(provenance, default=str))
        os.replace(prov_tmp, prov_f)
    (REPO_ROOT / "data" / "windows" / "agent_events.json").write_text(
        json.dumps([{**v, "vehicles": sorted(v["vehicles"])} for v in events.values()], indent=2, default=str))
    return out
