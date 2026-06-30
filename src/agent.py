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

US-LISTED ONLY. Every ticker must trade on a US exchange (NYSE/Nasdaq) — a plain symbol with NO
exchange suffix. NEVER a foreign-exchange listing (no ".AX", ".L", ".TO", ".HK", ".DE", ".T", etc.).
If the company is foreign, name its US ADR (e.g. CSLLY, not CSL.AX; TM, not 7203.T) or skip it.

BE RUTHLESSLY SELECTIVE. Propose a ticker ONLY if the press frames it as a STANDOUT, SUSTAINED
thesis-driven mover with a real, nameable catalyst — NOT a one-off mention, a routine daily gainer,
or a name buried in a list. Most weeks warrant 0-1 candidates; rarely more than 2. When in doubt,
propose nothing. Prefer the PUREST vehicle for a theme (a rate/commodity ETN or clean pure-play
over diluted operators; a single ADR over a broad ETF).

CATALYST GATE (the hard filter — this is the bet). Propose a ticker ONLY if the press ties it to a
SPECIFIC, DATABLE, RESOLVABLE catalyst: a discrete event with a knowable resolution — a war/
chokepoint, an export ban or tariff, a regulatory approval or named bill, an agency emergency
declaration, a named contract/partnership/deal, a supply shock. That resolution is what later flips
the position to EXIT. JUDGE BY THE STRONGEST REASON TO OWN IT, NOT THE WEAKEST: if a specific
catalyst is present, KEEP the name even when the coverage ALSO wraps it in a theme, valuation, or
technical story (e.g. "AI-power demand AND a reactor APPROVAL" -> keep; the approval is the
catalyst). The reject list below applies ONLY to a name whose SOLE rationale is:
  - theme / secular-momentum  ("AI power demand benefits utilities", "next wave after AI")
  - valuation / positioning   ("undervalued", "hedge-fund accumulation", "13F", "cheap as ever")
  - technical / chart         ("golden cross", "breakout", "high dividend yield")
  - generic macro             ("rate-cut rally", "sector rotation")
  - hype / narrative          ("IPO hype", "meme", "everyone piling in")
A named catalyst that later FAILS is fine — you couldn't have known. A PURE theme with no resolution
is NOT — it rides through every crash and bleeds. The thesis you write MUST name a SPECIFIC, DATABLE
EVENT — a discrete thing with a knowable date and resolution ("China bans rare-earth exports",
"ADVANCE Act nuclear bill signed", "Iran war spikes tanker rates") — NOT an open-ended trend phrased
as ongoing "news / demand / growth / approval news" ("reactor approval news", "AI power demand",
"rare-earth strength"), which can never be marked RESOLVED and so never exits. If the only phrasing
you can give is an ongoing trend, it's a theme — drop it. Drop only names that are theme/value/hype
AND NOTHING ELSE.

You forecast NOTHING. The "thesis" MUST BE THE DATABLE CATALYST EVENT, never the umbrella theme:
write "ADVANCE Act nuclear bill signed" NOT "NuScale gains on AI power demand"; write "China bans
rare-earth exports" NOT "rare-earth demand". If your thesis can't be marked RESOLVED on a date it is
a theme — rewrite it as the event or drop the name. Output ONLY JSON: {"candidates":[{"ticker":"BWET",
"thesis":"<=12 words: the catalyst EVENT","why_now":"<=12 words"}]}. Empty is the common, correct answer."""

AGENT_SYSTEM = """You manage ONE event for an event-driven book. You are given the event, YOUR
prior weekly note (your memory), and THIS week's news for this event. Write the new weekly note.

Decide:
  thesis_live  — the HOLD/EXIT switch. TRUE only while the SPECIFIC catalyst you entered on is still
                 PENDING / unresolved. Flip it to FALSE the WEEK that catalyst RESOLVES — the discrete
                 event you were early to has now HAPPENED and is public/priced:
                   - a bill/policy is SIGNED (or voted down); a regulator GRANTS or denies approval;
                   - a named deal/contract CLOSES or is announced; an emergency is declared then ENDS;
                   - a war/chokepoint/supply shock reverses (ceasefire, route reopens, shock passes).
                 EXIT THEN EVEN IF THE STOCK IS STILL RISING, and even if a broader THEME lingers: once
                 the event occurs the early-gem edge is gone (the catalyst is no longer news). Do NOT
                 keep a resolved catalyst alive by leaning on a surrounding secular theme — e.g. "the
                 reactor/ADVANCE Act was SIGNED, but AI-power demand continues" => the ACT resolved, so
                 thesis_live=FALSE (the lingering theme is NOT your datable catalyst).
                 The ONLY thing that is NOT a reason to exit: mainstream hype / crowding ("up 600%,
                 everyone in"). Resolution = the EVENT happened; crowding = the trade got popular —
                 exit on the former, NEVER on the latter.
                 ON ENTRY be skeptical: thesis_live=true requires a SPECIFIC, DATABLE, RESOLVABLE
                 catalyst (war/chokepoint, export ban/tariff, regulatory approval/bill, agency
                 declaration, named deal, supply shock). A real catalyst earns thesis_live=true EVEN IF
                 the coverage also carries a theme/valuation angle. Set thesis_live=FALSE NOW only when
                 the event is SOLELY a theme/secular-momentum story, a valuation call ("undervalued",
                 "13F"), a technical signal ("golden cross"), generic macro, hype, or a one-off mention
                 with no resolvable catalyst at all.
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
    exit_case: str = ""              # devil's-advocate: strongest reason the thesis is already over
    catalyst_resolved: bool = False  # binary: has the entry catalyst already happened? -> forces exit
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
        if m.ticker and "." not in m.ticker:   # SCOPE GUARD: yfinance US tickers carry no dot;
            out.append(m.model_dump())          #   a ".AX/.L/.TO/.HK/..." suffix is a FOREIGN exchange
        elif m.ticker:                          #   listing -> drop (the curator should name the US ADR)
            print(f"  scope: dropped foreign-exchange ticker {m.ticker} ({anchor.date()})", file=sys.stderr)
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
    "required": ["exit_case", "catalyst_resolved", "thesis_live", "exit_advice", "assessment", "news_claims", "vehicles", "sources"],
    "properties": {"exit_case": {"type": "string"}, "catalyst_resolved": {"type": "boolean"},
        "thesis_live": {"type": "boolean"},
        "exit_advice": {"type": "string"}, "assessment": {"type": "string"},
        "news_claims": {"type": "string"},
        "vehicles": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}}}}

EVENT_AGENT_SYSTEM = """You manage ONE event for an event-driven book. You are given: the event's
CATALYST (FIXED — the discrete thing you entered on), its KNOWN vehicles, your FULL weekly journal
for this event since entry (your memory — the whole arc, not just last week), and this week's news.
Write the new note.

FIRST, ARGUE FOR EXIT (devil's advocate — do this BEFORE deciding, EVERY week, against your WHOLE
journal): state the SINGLE strongest reason this thesis is ALREADY OVER — the catalyst has resolved
or decayed (`exit_case`, <=20 words). Write "none" ONLY after genuinely looking and finding nothing.
This is NOT a "was I right?" review (that just rubber-stamps the hold) — it is the case AGAINST the
position, made fresh each week, which is what defeats hold-inertia.

THEN answer `catalyst_resolved` (true/false): re-reading your ENTIRE journal, has the SPECIFIC
catalyst you entered on already OCCURRED / passed / closed / been signed — a bill signed or
voted-down, approval granted/denied, named deal closed, emergency declared-then-ended,
war/chokepoint/supply shock reversed — in THIS week OR ANY PRIOR week (even one you did not flag at
the time)? If true, the catalyst is public and priced, your edge is gone, and thesis_live MUST be
false — EVEN IF the stock is still rising or a broader THEME lingers. The ONLY thing that is NOT
resolution: mainstream hype / crowding ("up 600%, everyone in"). thesis_live=TRUE only while the
specific catalyst is still PENDING (catalyst_resolved=false).

YOUR BINARY MUST FOLLOW YOUR OWN ARGUMENT: if the exit_case you just wrote says the catalyst has
ALREADY happened / been signed / was granted / is "backward-looking" / "already resolved", then you
MUST set catalyst_resolved=TRUE. Do NOT write an exit_case that concludes "it already resolved" and
then leave catalyst_resolved=false and hold — that contradiction IS the inertia trap this is meant to
break.

USE THE WHOLE JOURNAL. The CATALYST is fixed; the best VEHICLE (ticker) MAY EVOLVE as the event
develops — pick the purest CURRENT vehicle(s) from the known set (1-2 max; cleanest pure-play /
rate-or-commodity ETN / single ADR; drop a vehicle that is no longer the best). The event is the
durable unit; the ticker can change with it.

You never forecast HOW HIGH (no price target / size — sizing is mechanical); you only judge
composition, the exit, and which vehicle. Output ONLY JSON: {"exit_case":"...","catalyst_resolved":false,"thesis_live":true,
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


def _journal_digest(entries: list[dict], keep: int = 20) -> str:
    """Compact week-by-week journal so the agent sees the FULL arc of an event since entry — the
    catalyst it entered on, how the VEHICLE evolved, and every live/exit read — not just last week.
    One line per week: date | live | vehicles | assessment. The entry week is always shown."""
    if not entries:
        return "(none — this is the first week of this event)"

    def line(e):
        veh = ",".join(e.get("vehicles", [])) or "-"
        return f"{e.get('date', '?')} live={e.get('thesis_live')} veh=[{veh}] {e.get('assessment', '')}".strip()
    if len(entries) <= keep:
        return "\n".join(line(e) for e in entries)
    head = [line(entries[0]), f"... ({len(entries) - keep - 1} earlier weeks omitted) ..."]
    return "\n".join(head + [line(e) for e in entries[-keep:]])


def event_agent_v2(client, anchor, event, entries, news):
    digest = _journal_digest(entries)
    entry_wk = entries[0]["date"] if entries else anchor.date().isoformat()
    nb = _block(news) if news else "(no fresh coverage for this event this week)"
    user = (f"Event catalyst (FIXED — what you entered on): {event['catalyst']}\nEntered: {entry_wk}\n"
            f"Known vehicles: {', '.join(sorted(event['vehicles']))}\nWeek ending {anchor.date()}.\n\n"
            f"Your journal so far (oldest -> newest):\n{digest}\n\nThis week's news:\n{nb}\n\n"
            "Re-check the EXIT condition against your WHOLE journal, then write this week's note and "
            "pick the current vehicle(s) (JSON).")
    txt = client.complete(EVENT_AGENT_SYSTEM, user, use_web_search=False, stage="agent",
                          label=f"event-{event['id']}-{anchor.date()}", json_schema=EVENT_AGENT_SCHEMA)
    try:
        e = JournalEntry(**_extract(txt))
    except Exception:  # noqa: BLE001
        e = JournalEntry()
    # #3: the binary FORCES the exit — a resolved catalyst can't be held out of inertia (the LLM
    # selects whether it resolved; the exit is mechanical, like the scope guard — non-negotiable #1).
    live = e.thesis_live and not e.catalyst_resolved
    veh = [v.strip().upper() for v in e.vehicles if v.strip()]
    veh = [v for v in veh if v in event["vehicles"]] or sorted(event["vehicles"])[:1]   # known only; fallback
    return {"date": anchor.date().isoformat(), "thesis_live": live,
            "exit_case": e.exit_case, "catalyst_resolved": e.catalyst_resolved,
            "exit_advice": e.exit_advice, "assessment": e.assessment,
            "news_claims": e.news_claims, "sources": [u for u in e.sources if u][:6], "vehicles": veh}


def run_event_agent_scans(start, end, rebalance_days, model, workers, queries=None, seed=None,
                          pool_chunk_days=90, pool_per=150, provider="anthropic", targeted=False,
                          enrich=False, enrich_fetch=True) -> dict:
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
    stats_path = str(REPO_ROOT / "data" / "windows" / "retrieval_stats.json")
    gpool = gd.pool(qs, win_start, anchors[-1], chunk_days=pool_chunk_days, per=pool_per,
                    cache_path=str(cache_f), stats_path=stats_path)
    seeds = firehose._fixture_articles(seed) if seed else []
    print(f"  pool {len(gpool)} + {len(seeds)} seeds; running event-agents ...", file=sys.stderr)

    events: dict[str, dict] = {}   # id -> {id, catalyst, status, vehicles:set, entries:[]}
    out: dict[pd.Timestamp, list[dict]] = {}
    nid = [0]
    rsig = hashlib.md5(f"EV{provider}{model}{start}{end}{rebalance_days}{seed}{targeted}{enrich}{enrich_fetch}{qs}".encode()).hexdigest()[:10]
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
            wayback.enrich(gslice, a.date().isoformat(), cache_path=enrich_cache,
                           fetch=enrich_fetch, stats_path=stats_path)
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
            return ev, event_agent_v2(client, a, ev, ev["entries"], _filter_event(win, ev))

        # provenance: was this ticker NAMED in a hand-seed article this week (vs found in the real
        # GDELT firehose)? Word-boundary match against the seed slice's text. Lets the dashboard show
        # whether a discovery is the solution's own (gdelt) or carried by the seed overlay.
        import re as _re
        seed_blob = " ".join((s.get("title", "") + " " + s.get("snippet", "")) for s in seed_slice).upper()
        def _src(tk):
            return "seed" if _re.search(rf"\b{_re.escape(tk.upper())}\b", seed_blob) else "gdelt"

        picks = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for ev, entry in (ex.map(work, live_events) if live_events else []):
                ev["entries"].append(entry)
                ev["status"] = "live" if entry["thesis_live"] else "exited"
                for tk in entry["vehicles"]:
                    picks.append({"ticker": tk, "thesis": ev["catalyst"],
                                  "thesis_live": entry["thesis_live"], "src": _src(tk),
                                  "exit_case": entry.get("exit_case", ""),
                                  "catalyst_resolved": entry.get("catalyst_resolved", False),
                                  "assessment": entry.get("assessment", ""),
                                  "exit_advice": entry.get("exit_advice", ""),
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
