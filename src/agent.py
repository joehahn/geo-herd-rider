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
                   "required": ["ticker", "company", "thesis", "why_now", "peers"],
                   "properties": {"ticker": {"type": "string"}, "company": {"type": "string"},
                                  "thesis": {"type": "string"}, "why_now": {"type": "string"},
                                  "peers": {"type": "array", "items": {"type": "string"}}}}}}}
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

US-TRADEABLE + SEASONED. Every pick must be tradeable from a US exchange (NYSE / Nasdaq / OTC ADR) AND
must already have a few WEEKS of trading history — a brand-new IPO or just-merged SPAC canNOT be sized by
the mechanical mean-variance optimizer (it drops names lacking enough price history), so SKIP a name that
just started trading and revisit once it seasons. ALWAYS fill
`company` with the issuer's full name. For `ticker`: if it is a US name, or you are confident of its US
ADR symbol, put that (e.g. CSLLY, TM). If the company is FOREIGN and you are NOT sure of its US symbol,
DO NOT skip a strong gem — put your best-known ticker (even a foreign one like RHM.DE, or just repeat the
company) in `ticker`; a downstream resolver will web-search the US-listed symbol from `company`. Never
drop a real thesis merely because you can't recall the exact ticker.

BE RUTHLESSLY SELECTIVE. Propose a ticker ONLY if the press frames it as a STANDOUT, SUSTAINED
thesis-driven mover with a real, nameable catalyst — NOT a one-off mention, a routine daily gainer,
or a name buried in a list. Most weeks warrant 0-1 candidates; rarely more than 2. When in doubt,
propose nothing. Prefer the PUREST vehicle for a theme (a rate/commodity ETN or clean pure-play
over diluted operators; a single ADR over a broad ETF).

PEER BASKET (extra vehicles for ONE catalyst — NOT extra catalysts). Keep the single purest name as
`ticker`, but ALSO list in `peers` the OTHER US-listed tickers that express the SAME catalyst — direct
same-thesis plays (a chip-export-control catalyst -> the other named chipmakers + the semis ETF; an
FDA drug approval -> the drug's licensing partners). These
ride as extra vehicles on that ONE event and the mechanical optimizer sizes them + drops the weak ones,
so you no longer throw the peers away. RULES: `peers` are SAME-catalyst ONLY (0-4); NEVER list a name
driven by a DIFFERENT catalyst — that is a separate candidate or nothing (this is what keeps the basket
from drifting into unrelated gems); US-listed only (name the US ADR, no foreign suffix).

CATALYST GATE (the hard filter — this is the bet). THE HIGHEST-VALUE CATALYST IS A NATIONAL OR
INTERNATIONAL SUPPLY-DEMAND SHIFT: a concrete, NAMED change in the real supply or demand for a
product, commodity, or asset at national/international scale — an event that CUTS SUPPLY (a chokepoint,
an export ban, sanctions, a major outage) or LIFTS DEMAND (a law/policy/election that mandates
spending, a security or infrastructure program, a named multi-year deal), whether DIRECTLY (the event
moves the product) or INDIRECTLY (an event that makes such a shift highly credible — e.g. an election
that forces a coming policy). RANK THESE FIRST — this is the pattern behind the biggest movers; other
concrete datable catalysts still qualify, but a supply/demand shift is the strongest reason to own a
name. Propose a ticker ONLY if the press ties it to a SPECIFIC, DATABLE, RESOLVABLE catalyst: a discrete event with a knowable resolution — a war/
chokepoint, an export ban or tariff, a regulatory approval or named bill, an agency emergency
declaration, a named contract/partnership/deal, a supply shock, OR a SCHEDULED, DATED FUTURE EVENT the
name is demonstrably rising in ANTICIPATION of — a national election, an FDA/PDUFA decision date, a
scheduled regulatory vote, a court-ruling date — a KNOWN date with a binary/knowable outcome. That
resolution (for a dated future event, the known date itself) is what later flips the position to EXIT.
Anticipation qualifies ONLY with a SPECIFIC DATED event whose date you can name — NEVER open-ended
"rising demand / sentiment / growth / interest" with no date (that is still momentum — reject it). JUDGE BY THE STRONGEST REASON TO OWN IT, NOT THE WEAKEST: if a specific
catalyst is present, KEEP the name even when the coverage ALSO wraps it in a theme, valuation, or
technical story (e.g. "an EV-demand theme AND a battery-plant permit" -> keep; the permit is the
catalyst). The reject list below applies ONLY to a name whose SOLE rationale is:
  - theme / secular-momentum  ("reshoring benefits industrials", "next wave after AI")
  - valuation / positioning   ("undervalued", "hedge-fund accumulation", "13F", "cheap as ever")
  - technical / chart         ("golden cross", "breakout", "high dividend yield")
  - generic macro             ("rate-cut rally", "sector rotation")
  - hype / narrative          ("IPO hype", "meme", "everyone piling in")
A named catalyst that later FAILS is fine — you couldn't have known. A PURE theme with no resolution
is NOT — it rides through every crash and bleeds. The thesis you write MUST name a SPECIFIC, DATABLE
EVENT — a discrete thing with a knowable date and resolution ("the FDA approves a first-in-class drug",
"the CHIPS Act is signed", "the Suez Canal blockage spikes freight rates") — NOT an open-ended trend phrased
as ongoing "news / demand / growth / approval news" ("chip-subsidy news", "EV demand",
"freight-rate strength"), which can never be marked RESOLVED and so never exits. If the only phrasing
you can give is an ongoing trend, it's a theme — drop it. Drop only names that are theme/value/hype
AND NOTHING ELSE.

EARLY / BUILDING CATALYST — NAME IT WHILE IT'S STILL FORMING (this is the edge). Catch the ticker
while its catalyst is BUILDING and the name is still under-owned — do NOT wait for the acute peak. An
ESCALATING geopolitical or supply event — a RISING chokepoint risk (a canal, a strait, a pipeline) as tensions
build, a developing conflict, a tightening export/supply squeeze — that is ALREADY moving a NAMED,
still-niche/under-owned ticker IS a live, unresolved catalyst: propose it NOW. Do not wait for the
discrete acute trigger (the blockade declared, the ban signed) — by then the herd has arrived and the
edge is gone. Tell a BUILDING EVENT from a theme: the event has an ACTOR + LOCATION + MECHANISM + an
escalation/resolution path (a named actor + chokepoint + the rate it moves, e.g. a pipeline sabotage
lifting natural-gas prices) AND the press still calls the vehicle "niche / under-owned" -> KEEP (write the
thesis as the escalating event, e.g. "a pipeline sabotage lifts natural-gas prices"); a pure theme has only diffuse demand and no actor/event ("electrification demand") -> drop.
"Still under-owned / niche while climbing on a forming geopolitical shock" is the IDEAL early-gem buy
— that framing is a KEEP, not momentum. (This is BEFORE resolution; the resolved-catalyst rule below
still applies once the event actually resolves.)

ANTICIPATORY POLICY / SPENDING CATALYST — a CONCRETE regime event that FORCES a coming policy or
spending shift is ALSO live/early, even before the enacting bill lands. When a datable event — an
election result, a change of government, an alliance rupture or a withdrawn security guarantee, a
formal pledge/mandate — makes a large policy or spending response HIGHLY CREDIBLE and the press already
names the specific frontline beneficiary, propose it NOW; do NOT wait for the enacting action. Example:
"an incoming administration's pledged infrastructure package -> domestic steelmakers, the direct
beneficiary" is a KEEP at the ELECTION, not something to hold off until the appropriations bill
actually passes — by that enacting vote the herd has already arrived. The guard against theme-creep:
there must be a SPECIFIC triggering EVENT (the election / the rupture, datable) + a NAMED,
still-under-owned beneficiary + a DIRECT mechanism (the pledge must now be funded -> the named beneficiary). Diffuse "defense
spending will rise" with no triggering event is still a theme -> drop.

You forecast NOTHING. The "thesis" MUST BE THE DATABLE CATALYST EVENT, never the umbrella theme:
write "the CHIPS Act is signed" NOT "chipmakers gain on AI demand"; write "the FDA approves a
first-in-class drug" NOT "biotech demand". If your thesis can't be marked RESOLVED on a date it is
a theme — rewrite it as the event or drop the name.

DON'T CHASE A RESOLVED CATALYST. If a ticker's driving catalyst has ALREADY RESOLVED (the ceasefire
was signed, the ban was lifted, the ruling came down), the edge is GONE — even if the press KEEPS
hyping the name for weeks afterward ("prices still elevated", "tensions linger"). Lingering hype
about a catalyst that already happened is NOT a fresh catalyst. If the user message lists a ticker's
catalyst as ALREADY-RESOLVED, do NOT re-propose that ticker unless a genuinely NEW, distinct catalyst
has since emerged (a SECOND, different datable event — not a restatement of the resolved one).

Output ONLY JSON: {"candidates":[{"ticker":"XYZ",
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
    company: str = ""              # company name (for the US-ticker resolver when the ADR symbol is obscure)
    thesis: str = ""
    why_now: str = ""
    peers: list[str] = []          # same-catalyst peer vehicles: extra US tickers for THIS event's basket

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
    conviction: int = 5              # 1-10 CATALYST QUALITY (specificity/under-radar) for the top-N shortlist — COMPOSITION, never a return/size forecast
    exit_advice: str = ""
    milestones: list[str] = []   # ordered catalyst-progress events (the arc); qualitative, NEVER magnitude
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


RESOLVER_SYSTEM = """You are a ticker-lookup utility. Given a company name (and maybe a foreign
ticker), web-search and return the symbol it trades under on a US exchange (NYSE / Nasdaq / OTC ADR) —
a plain symbol with NO exchange suffix. Prefer the sponsored ADR. If the company has NO US listing at
all, return null. Return ONLY JSON: {"ticker": "XYZ"} or {"ticker": null}. Emit NOTHING else — no
price, no news, no commentary. A name<->symbol mapping is a STATIC fact; never report anything
time-varying."""

_TICKER_CACHE: dict[str, str | None] = {}   # per-run memo: company/foreign-ticker -> US symbol (skip re-searching)


def resolve_us_ticker(client, company: str, hint: str = "") -> str | None:
    """Live web-search resolution of a NAMED company -> its US-listed symbol. Look-ahead-SAFE: a
    name<->ticker mapping is a static fact (RNMBY was RNMBY in 2025 and now); only the symbol is
    extracted, all time-varying content (price/news) is discarded. Returns a dot-free US symbol or None.
    Runs as a SEPARATE call from the scout's web-search-free reasoning, so the scout stays look-ahead
    clean and only this narrow ticker lookup touches the web."""
    key = (company.strip() or hint.strip()).upper()
    if not key:
        return None
    if key in _TICKER_CACHE:
        return _TICKER_CACHE[key]
    q = (f"Company: {company or hint}\n" + (f"Foreign/known ticker: {hint}\n" if hint else "")
         + "What is its US-listed ticker symbol? Output the JSON.")
    us = None
    try:
        txt = client.complete(RESOLVER_SYSTEM, q, use_web_search=True, stage="agent",
                              label=f"resolve-{key[:20]}")
        tk = str(_extract(txt).get("ticker") or "").strip().upper()
        us = tk if (tk and "." not in tk and tk.isalnum()) else None
    except Exception:  # noqa: BLE001
        us = None
    _TICKER_CACHE[key] = us
    return us


def scout(client, anchor: pd.Timestamp, arts: list[dict], retired: str = "") -> list[dict]:
    if not arts:
        return []
    rblock = (f"\nALREADY-RESOLVED — DO NOT RE-PROPOSE these on lingering hype (the catalyst already "
              f"happened/ended, so the edge is GONE even if the press keeps citing it):\n{retired}\n"
              if retired else "")
    user = (f"Week ending {anchor.date()}. Headlines:\n\n{_block(arts)}\n{rblock}\n"
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
        tk = m.ticker.strip()
        us_like = bool(tk) and "." not in tk and tk.isalpha() and len(tk) <= 6
        if not us_like and (m.company or tk):   # foreign / dotted / company-as-ticker -> RESOLVE the US symbol live
            resolved = resolve_us_ticker(client, m.company, hint=tk)
            if resolved:
                print(f"  resolver: {(m.company or tk)!r} -> {resolved} ({anchor.date()})", file=sys.stderr)
                tk = resolved
        if tk and "." not in tk:                # SCOPE GUARD: a dot = FOREIGN exchange listing -> drop
            m.ticker = tk
            out.append(m.model_dump())
        elif tk:
            print(f"  scope: dropped unresolved foreign ticker {tk} ({anchor.date()})", file=sys.stderr)
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
    _ph = hashlib.md5((SCOUT_SYSTEM + AGENT_SYSTEM).encode()).hexdigest()[:6]  # prompt-aware: edits bust the cache
    _sh = hashlib.md5(Path(seed).read_bytes()).hexdigest()[:8] if seed and Path(seed).exists() else ""  # seed-CONTENT-aware: editing a seed's articles busts the cache
    rsig = hashlib.md5(f"{provider}{model}{start}{end}{rebalance_days}{seed}{_sh}{targeted}{_ph}{qs}".encode()).hexdigest()[:10]
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
war, an election, a supply shock, a tech wave); MANY tickers can express the same event — e.g. a
company's two ADRs are one; several oil refiners riding one OPEC cut are one event; several
homebuilders riding one rate-cut are one event. Given the OPEN events (id + catalyst) and this
week's CANDIDATES (ticker + thesis), assign each candidate to the open event it belongs to (by id)
or "new" if it is a genuinely different catalyst.

DEFAULT TO MERGING. Assign a candidate to an existing open event whenever they share the SAME
underlying driver — same commodity, same sector/policy shock (a chip export control, a central-bank
rate decision, an OPEC supply cut), same war / election / supply event — EVEN IF the tickers differ or
the thesis is worded differently. A chip designer, a foundry, and an equipment maker riding one
export-control are ONE event; do NOT open three. Use "new" ONLY when a candidate's catalyst is CLEARLY unrelated to
every open event. When unsure, MERGE — fragmenting one catalyst across several events is the single
biggest error to avoid here. Output ONLY JSON: {"matches":[{"ticker":"XYZ","event":"<id>|new"}]}."""

EVENT_AGENT_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["exit_case", "catalyst_resolved", "thesis_live", "conviction", "exit_advice", "milestones", "assessment", "news_claims", "vehicles", "sources"],
    "properties": {"exit_case": {"type": "string"}, "catalyst_resolved": {"type": "boolean"},
        "thesis_live": {"type": "boolean"},
        "conviction": {"type": "integer"},   # 1-10 CATALYST QUALITY (specific/datable/under-radar) — NOT a return forecast
        "exit_advice": {"type": "string"},
        "milestones": {"type": "array", "items": {"type": "string"}},   # ordered catalyst-progress events (the arc)
        "assessment": {"type": "string"},
        "news_claims": {"type": "string"},
        "vehicles": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}}}}

EVENT_AGENT_SYSTEM = """You manage ONE event for an event-driven book. You are given: the event's
CATALYST (FIXED — the discrete thing you entered on), its KNOWN vehicles, your FULL weekly journal
for this event since entry (your memory — the whole arc, not just last week), and this week's news.
Write the new note.

FIRST, ARGUE FOR EXIT (devil's advocate — do this BEFORE deciding, EVERY week, against your WHOLE
journal): state the SINGLE strongest reason this thesis is ALREADY OVER — the catalyst has RESOLVED
(occurred / closed / been signed) or its DRIVING CONDITION has REVERSED (curbs lifted, ceasefire,
chokepoint reopened, shortage ended) (`exit_case`, <=20 words). Write "none" ONLY after genuinely
looking and finding nothing. This is NOT a "was I right?" review (that just rubber-stamps the hold) —
it is the case AGAINST the position, made fresh each week, which is what defeats hold-inertia.

A QUIET STRETCH IS NOT AN EXIT. Do NOT exit on "staleness", "aging thesis", "edge decayed", "premium
already absorbed / priced-in", or "N quiet weeks with no fresh news" while the DRIVING CONDITION is
still in force. A live catalyst can go silent for weeks and then RE-ACCELERATE (a follow-on deal, an
escalation, a government stake) — exiting on silence forfeits exactly that second leg. HOLD through
silence; exit ONLY on the resolution / reversal above (or catalyst_resolved below).

THEN answer `catalyst_resolved` (true/false): re-reading your ENTIRE journal, has the SPECIFIC
catalyst you entered on already OCCURRED / passed / closed / been signed — a bill signed or
voted-down, approval granted/denied, named deal closed, emergency declared-then-ended,
war/chokepoint/supply shock reversed — in THIS week OR ANY PRIOR week (even one you did not flag at
the time)? If true, the catalyst is public and priced, your edge is gone, and thesis_live MUST be
false — EVEN IF the stock is still rising or a broader THEME lingers. The ONLY thing that is NOT
resolution: mainstream hype / crowding ("up 600%, everyone in") does not BY ITSELF make the binary
true. thesis_live=TRUE only while the specific catalyst is still PENDING (catalyst_resolved=false).

THIRD EXIT — THE WINDOW CLOSES WHEN THE HERD FULLY ARRIVES (set thesis_live=false even with
catalyst_resolved=false). You entered because the press named this EARLY / under-the-radar; that edge
is spent when BOTH hold: (a) the catalyst's ARC IS COMPLETE — the shock, the response, and the
follow-on are all public and NO CONCRETE next catalyst is still ahead (nothing SCHEDULED / ANNOUNCED /
formally-expected left to land — a set summit date, a filed deal awaiting a known ruling, a scheduled
vote); AND (b) coverage has turned MAINSTREAM-SATURATED (front-page,
retail frenzy, sell-side chasing raised targets, "everyone's in"). When BOTH hold, the smart-money
window has closed and the early edge is gone — exit. CRUCIAL — "pending ahead" means CONCRETE, NOT
SPECULATIVE: a rumor, a "maybe", a question-mark headline ("Is a summit coming?"), an "analysts wonder
if" — these do NOT count as a pending catalyst and do NOT keep the position alive; if the only thing
left ahead is speculation, the ARC IS COMPLETE. This is still NOT mid-run hype: while a CONCRETE next
step is genuinely scheduled ahead, crowding is just noise — HOLD. The single test: is a CONCRETE,
announced/scheduled catalyst still ahead? Yes -> hold (ignore the crowd); No (only speculation left)
AND the crowd has fully arrived -> exit. (silence-while-under-the-radar = HOLD; loud-and-done = EXIT.)

STRUCTURAL vs ACUTE — DON'T FORCE AN ACUTE EXIT ON AN OPEN-ENDED DRIVER. An ACUTE catalyst is a single
datable event that cleanly resolves (a bill signed, a merger closed, a ceasefire, a chokepoint reopened)
— the rules above apply as written. A STRUCTURAL / open-ended catalyst is a multi-year regime shift that
keeps throwing off NEW concrete milestones (a trade-war escalation -> a first tariff round -> retaliation ->
a second round -> further curbs; a de-dollarization / reserve pivot; a standing export-curb regime). For a
STRUCTURAL driver: (a) the Third-Exit "arc complete / nothing concrete ahead" test does NOT fire merely
because ONE scheduled milestone (e.g. a single summit) has passed — the driver keeps generating new
milestones, so a gap between them is a QUIET STRETCH, not completion. Exit a structural driver ONLY on a
genuine REVERSAL of the driver itself — a concrete COUNTER-event (a trade deal that ends the tariff war,
the curbs lifted, the reserve pivot unwound, the funding cut) — never on silence, an "aging thesis", or a
single milestone passing while the buildout plainly continues. (b) Silence is NOT a hard exit
(catalyst_resolved stays false) — but it IS a fade: a structural driver earns high conviction by delivering
FRESH milestones, not by the mere passage of time, so genuine prolonged quiet steps conviction DOWN per the
SILENCE DECAY rule below (recovering when fresh coverage resumes). Don't confuse a single milestone passing
amid ONGOING coverage (compounding) with true silence (fading). (c) RE-ANCHOR: the driver is fixed, but update your thesis to its
FRESHEST concrete milestone — if you entered on "the administration threatens sweeping tariffs" and the driver then became
"the first tariff round takes effect," THAT round is the live catalyst now; clinging to the
ORIGINAL milestone while the driver has moved on is anchoring, and forces an exit for the wrong reason.

YOUR BINARY MUST FOLLOW YOUR OWN ARGUMENT: if the exit_case you just wrote says the catalyst has
ALREADY happened / been signed / was granted / is "backward-looking" / "already resolved", then you
MUST set catalyst_resolved=TRUE. Do NOT write an exit_case that concludes "it already resolved" and
then leave catalyst_resolved=false and hold — that contradiction IS the inertia trap this is meant to
break.

USE THE WHOLE JOURNAL. The CATALYST is fixed, but its event carries a BASKET of same-catalyst vehicles
(the primary plus its peers) — propose ALL of them that still credibly express the LIVE catalyst (the
mechanical optimizer sizes them and drops the weak ones, so you never pre-pick the one winner). Keep
the cleanest pure-plays AND the peers; drop only a vehicle whose OWN thesis no longer holds. Do NOT add
a name from a DIFFERENT catalyst. The event is the durable unit; its basket of tickers can change with it.

You never forecast HOW HIGH (no price target / size — sizing is mechanical); you only judge
composition, the exit, and which vehicle.

RATE `conviction` 1-10 — the QUALITY of THIS catalyst, so a shortlist can keep only the strongest
events. This is NOT a return/price forecast (never guess how much it will move — that is forbidden
and destroys value). Score ONLY the catalyst itself: 10 = a specific, datable, thesis-driven,
still-early / under-the-radar shock with a clean pure-play vehicle (a named war/curb/bill/supply-
shock the press explicitly ties to this ticker); 1 = vague, generic, already-mainstream, or a routine
business item (an analyst rating, a small partnership, a run-of-the-mill earnings beat). Judge the
CATALYST's specificity + magnitude-of-event + how under-the-radar it still is — never the expected
return.

PRICED-IN DECAY (the SOFT resolution). As a thesis gets ABSORBED, step conviction DOWN. When the
coverage flips from "still under-owned / more upside ahead" to "fully valued / consensus / the move
has largely happened" and no fresh catalyst lies ahead, the EDGE is gone even though the driver has
NOT reversed — mark conviction LOW (3-4) so the position is out-competed and culled from the shortlist.
This priced-in fade is a SOFT resolution: leave catalyst_resolved=false / thesis_live=true (you are NOT
calling a hard exit, just letting a played-out name lose the fitness contest). It is DISTINCT from a
STRUCTURAL, open-ended buildout still delivering fresh milestones — keep THAT high; do NOT fade a live
open-ended driver merely because time has passed or coverage went quiet (silence alone is not priced-in —
that is the SILENCE DECAY rule below, a separate signal).

SILENCE DECAY. The press covers a live ticker-trend OFTEN and LOUDLY, so SILENCE about this event's trend
(no fresh press on the move, explicit or implied) is itself a fade signal. On EACH weekly refresh (every
rebalance) with NO fresh coverage of this ticker's trend, step conviction DOWN by 1 from your PRIOR score —
so continued silence COMPOUNDS week over week toward the cull floor, while a single fresh trend-story RESETS
it back up. This is a SOFT fade (leave catalyst_resolved=false), and it applies to STRUCTURAL buildouts too
(they keep conviction only while delivering fresh milestones, not by the passage of time).

`exit_advice` (<=20 words) is the STANDING EXIT CONDITION: the concrete, observable trigger that would
END this thesis — phrase it as "exit if/when <observable event>" (e.g. "exit if a Hormuz reopening or
ceasefire looks imminent"). It is a forward CONDITION, not a hold/sell verdict — `thesis_live` already
carries hold-vs-exit, so NEVER write "Hold" / "Sell" / "none" here while the thesis is live. RESTATE THE
SAME standing condition every week (carry it forward from your journal); REVISE it when the catalyst's
arc genuinely moves the trigger — e.g. an acute shock matures into a structural driver, or a new
near-term milestone becomes the thing to watch — but do NOT churn the wording week to week for no reason.

`milestones` (ordered list, <=6 short items, oldest -> newest) — the catalyst's ARC as concrete progress
events (e.g. ["Israel-Iran strikes","Hormuz transit threatened","tankers reroute","US sets Iran deadline"]).
CARRY FORWARD the list from your journal and APPEND a new item ONLY when a concrete development actually
lands this week; never pad with speculation. This is the evidence trail behind your conviction and exit
call — a LIVE driver keeps throwing off fresh milestones; a stalled/resolved one stops (feed that into the
SILENCE DECAY and exit logic above).

Output ONLY JSON: {"exit_case":"...","catalyst_resolved":false,"thesis_live":true,"conviction":7,
"exit_advice":"...","milestones":["...","..."],"assessment":"...","news_claims":"",
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


def _norm_catalyst(s: str) -> str:
    """Normalize a catalyst string for duplicate detection: lowercase, alphanumerics only."""
    import re
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _consolidate_events(events: dict) -> int:
    """Consolidation-of-agents pass (deterministic safety net for the per-candidate matcher's
    under-merges under load): merge LIVE events that share the SAME catalyst (normalized-identical)
    into the earliest one — fold the duplicates' vehicles in and retire them (status='merged', so they
    stop spawning an agent). Catches e.g. IBM & QBTS both 'quantum computing', VLO & NRG both
    'California pays Valero'. Returns how many events it retired."""
    import re
    def evnum(eid):
        m = re.search(r"\d+", eid)
        return int(m.group()) if m else 0
    keep_by_cat, merged = {}, 0
    for eid, ev in sorted(((e, v) for e, v in events.items() if v["status"] == "live"),
                          key=lambda x: evnum(x[0])):
        key = _norm_catalyst(ev["catalyst"])
        if not key:
            continue
        if key in keep_by_cat:                       # duplicate catalyst -> fold into the survivor
            events[keep_by_cat[key]]["vehicles"] |= ev["vehicles"]
            ev["status"] = "merged"
            merged += 1
        else:
            keep_by_cat[key] = eid
    return merged


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
    catalyst it entered on, how the VEHICLE evolved, and every live/exit/conviction read — not just
    last week. One line per week: date | live | conviction | vehicles | assessment | standing exit
    condition, plus a trailing milestone trail. Carrying these forward makes them LOAD-BEARING: the
    agent re-reads its own prior conviction (for SILENCE DECAY), its 'exit-if' trigger, and the
    milestone arc each week and tests them against the news, instead of re-deriving (or forgetting)
    them. The entry week is always shown."""
    if not entries:
        return "(none — this is the first week of this event)"

    def line(e):
        veh = ",".join(e.get("vehicles", [])) or "-"
        xa = (e.get("exit_advice", "") or "").strip()
        base = (f"{e.get('date', '?')} live={e.get('thesis_live')} conv={e.get('conviction', '?')} "
                f"veh=[{veh}] {e.get('assessment', '')}").strip()
        return base + (f" | exit-if: {xa}" if xa else "")
    if len(entries) <= keep:
        body = "\n".join(line(e) for e in entries)
    else:
        head = [line(entries[0]), f"... ({len(entries) - keep - 1} earlier weeks omitted) ..."]
        body = "\n".join(head + [line(e) for e in entries[-keep:]])
    ms = [str(m).strip() for m in (entries[-1].get("milestones") or []) if str(m).strip()]
    if ms:
        body += "\n\nMilestones logged so far (carry forward; append only genuinely new ones): " + " -> ".join(ms)
    return body


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
            "conviction": int(getattr(e, "conviction", 5) or 5),
            "exit_advice": e.exit_advice,
            "milestones": [str(m).strip() for m in (e.milestones or []) if str(m).strip()][:6],
            "assessment": e.assessment,
            "news_claims": e.news_claims, "sources": [u for u in e.sources if u][:6], "vehicles": veh}


def process_week(client, anchor, pool, events, retired, nid, week_idx,
                 curator_memory_weeks=8, workers=8, src_fn=None, scout_client=None):
    """ONE event-first week on an article POOL: scout -> same-ticker guard + matcher -> event agents.
    Mutates `events` and `retired` IN PLACE; returns (picks, nid). This is the SHARED curator engine
    used by BOTH the backtest (agent.run_event_agent_scans, GDELT+seed pool) and the forward driver
    (forward_engine.run_week, live-gather pool) — so the two run byte-identical logic and a settled
    forward solution can be re-backtested just by swapping the pool source. `src_fn(tk)->str` labels a
    pick's provenance (default 'live'); the backtest passes a seed-vs-gdelt labeler.

    Two-tier LLM split: `scout_client` runs the cheap, high-volume extraction/routing stages (scout +
    matcher); `client` runs the judgment stage (the event agents). `scout_client` defaults to `client`,
    so single-client callers keep the pre-split behavior byte-for-byte."""
    scout_client = scout_client or client
    src_fn = src_fn or (lambda tk: "live")
    if curator_memory_weeks == 0:                          # 0 = feature OFF (scout not reminded at all)
        rmem = ""
    else:                                                  # <0 = whole history; >0 = last N weeks only
        rmem = "\n".join(f"- {t}: {c}" for t, (c, ri) in retired.items()
                         if curator_memory_weeks < 0 or (week_idx - int(ri)) < curator_memory_weeks)
    cands = scout(scout_client, anchor, pool, retired=rmem)
    # DETERMINISTIC same-ticker guard: a ticker already held by a LIVE event belongs to that event —
    # never open a duplicate. Only genuinely NEW tickers go to the (fallible) LLM matcher.
    held_to_event = {v: eid for eid, ev in events.items() if ev["status"] == "live"
                     for v in ev["vehicles"]}
    new_cands = [c for c in cands if c["ticker"] not in held_to_event]
    match = match_to_events(scout_client, anchor, new_cands, events) if new_cands else {}
    for c in new_cands:
        tk, eid = c["ticker"], match.get(c["ticker"], "new")
        peers = {q.strip().upper() for q in c.get("peers", [])          # same-catalyst basket peers
                 if q.strip() and "." not in q and q.strip().upper() != tk}
        if eid in events and events[eid]["status"] == "live":
            events[eid]["vehicles"] |= {tk, *peers}
        else:
            nid += 1
            events[f"ev{nid}"] = {"id": f"ev{nid}", "catalyst": c["thesis"],
                                  "status": "live", "vehicles": {tk, *peers}, "entries": []}
    merged = _consolidate_events(events)                   # weekly dup-catalyst merge
    if merged:
        print(f"  consolidated {merged} duplicate-catalyst event(s) ({anchor.date()})", file=sys.stderr)
    live_events = [ev for ev in events.values() if ev["status"] == "live"]

    def work(ev):
        return ev, event_agent_v2(client, anchor, ev, ev["entries"], _filter_event(pool, ev))

    picks = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for ev, entry in (ex.map(work, live_events) if live_events else []):
            ev["entries"].append(entry)
            ev["status"] = "live" if entry["thesis_live"] else "exited"
            if entry.get("catalyst_resolved"):             # remember so the scout won't re-chase
                for tk in ev["vehicles"]:
                    retired[tk] = (f"{ev['catalyst']} (resolved {anchor.date()})", week_idx)
            for tk in entry["vehicles"]:
                picks.append({"ticker": tk, "thesis": ev["catalyst"],
                              "thesis_live": entry["thesis_live"], "src": src_fn(tk),
                              "exit_case": entry.get("exit_case", ""),
                              "catalyst_resolved": entry.get("catalyst_resolved", False),
                              "conviction": entry.get("conviction", 5),
                              "assessment": entry.get("assessment", ""),
                              "exit_advice": entry.get("exit_advice", ""),
                              "milestones": entry.get("milestones", []),
                              "evidence_urls": entry["sources"]})
    return picks, nid


def run_event_agent_scans(start, end, rebalance_days, model, workers, queries=None, seed=None,
                          pool_chunk_days=90, pool_per=150, provider="anthropic", targeted=False,
                          enrich=False, enrich_fetch=True, curator_memory_weeks=8, news_cap=WINDOW_CAP) -> dict:
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
    retired: dict[str, tuple] = {}   # ticker -> (catalyst-resolved string, week idx) for the scout guard;
                                     #   curator_memory_weeks: 0 = OFF, <0 = whole history, >0 = last N weeks
    out: dict[pd.Timestamp, list[dict]] = {}
    nid = [0]
    _ph = hashlib.md5((SCOUT_SYSTEM + MATCH_SYSTEM + EVENT_AGENT_SYSTEM).encode()).hexdigest()[:6]  # prompt-aware: edits bust the cache
    _sh = hashlib.md5(Path(seed).read_bytes()).hexdigest()[:8] if seed and Path(seed).exists() else ""  # seed-CONTENT-aware: editing a seed's articles (not just its path) busts the cache
    rsig = hashlib.md5(f"EV{provider}{model}{start}{end}{rebalance_days}{seed}{_sh}{targeted}{enrich}{enrich_fetch}{curator_memory_weeks}{_ph}{qs}".encode()).hexdigest()[:10]
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

    for i, a in enumerate(anchors):
        if a.isoformat() in done:
            continue
        _gsorted = sorted(firehose._window(gpool, a, rebalance_days),
                          key=lambda x: x.get("published_date", ""), reverse=True)
        gslice = _gsorted[:news_cap] if news_cap else _gsorted   # news_cap=0 -> UNCAPPED (keep all)
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
        import re as _re
        seed_blob = " ".join((sd.get("title", "") + " " + sd.get("snippet", "")) for sd in seed_slice).upper()
        def _src(tk):
            return "seed" if _re.search(rf"\b{_re.escape(tk.upper())}\b", seed_blob) else "gdelt"
        picks, nid[0] = process_week(client, a, win, events, retired, nid[0], i,
                                     curator_memory_weeks=curator_memory_weeks, workers=workers, src_fn=_src)
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
