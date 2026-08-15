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
import re
import sys
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
from concurrent.futures import TimeoutError as _FutTimeout
from pathlib import Path
from threading import Thread as _Thread

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
import costs  # noqa: E402
import firehose  # noqa: E402
import llm  # noqa: E402
import picker_log  # noqa: E402
import score  # noqa: E402  ticker normalization + tradeability guard
from util import scan_anchors  # noqa: E402

# Strict JSON schemas for the structured-output path (OpenRouter/DeepSeek — guarantees parseable
# JSON; the Anthropic path ignores these and parses free-form). additionalProperties=false keeps
# the model from inventing fields — incl. a price target (the no-magnitude guardrail, at the wire).
SCOUT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["candidates"],
               "properties": {"candidates": {"type": "array", "items": {
                   "type": "object", "additionalProperties": False,
                   "required": ["ticker", "company", "thesis", "why_now", "pending_next", "peers"],
                   "properties": {"ticker": {"type": "string"}, "company": {"type": "string"},
                                  "thesis": {"type": "string"}, "why_now": {"type": "string"},
                                  "pending_next": {"type": "string"},
                                  "peers": {"type": "array", "items": {"type": "string"}}}}}}}
AGENT_SCHEMA = {"type": "object", "additionalProperties": False,
               "required": ["thesis_live", "exit_advice", "assessment", "news_claims", "sources"],
               "properties": {"thesis_live": {"type": "boolean"},
                              "exit_advice": {"type": "string"}, "assessment": {"type": "string"},
                              "news_claims": {"type": "string"},
                              "sources": {"type": "array", "items": {"type": "string"}}}}

CANDIDATE_CAP = 3        # DEFAULT for the `max_new_events` knob: max NEW events the scout admits per week
                         #   (bounds event-agent creation -> weekly LLM cost). 0 = uncapped inflow.
                         #   TODO: when unbounded-discovery is enabled, replace the take-first-N cull below
                         #   with a mechanical diversity/novelty tiebreak (spread across themes, prefer
                         #   themes not already live) — NOT reward-ranking, NOT source-count.
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

BE SELECTIVE ON QUALITY, NOT ON COUNT. Propose a ticker only if the press frames it as a STANDOUT,
SUSTAINED thesis-driven mover with a real, nameable catalyst — NOT a one-off mention, a routine daily
gainer, or a name buried in a list. But do NOT ration yourself: propose EVERY name in this week's
headlines that clears the bar. Some weeks that is none; a week with a big supply shock or a policy
shift may well carry five or more, and holding back a name that qualifies is a miss, not discipline.
Prefer the PUREST vehicle for a theme (a rate/commodity ETN or clean pure-play over diluted
operators; a single ADR over a broad ETF).

SUPERLATIVE FRAMING IS EVIDENCE, NOT NOISE — but ONLY the RUN-SCALE kind. The tell is a superlative
describing a LARGE, ALREADY-SUSTAINED move plus language saying the crowd has not arrived yet:
  ADMIT  "best-performing ETF of the year", "up over 600%", "a 1,300% rally", "skyrocketing",
         "flying under the radar", "little-known", "still early", "obscure", "nobody is talking about"
  REJECT "hits a new 52-week / 1-year high", "shares jump 5%", "Q3 earnings beat", "tops estimates",
         "overtakes X in market cap", "potential recovery", "analyst raises target", "gaps up"
The difference is MAGNITUDE and DURATION, not the presence of an excited verb. A multi-hundred-percent
run that the press is still calling under-the-radar is the signal; a new high or a good quarter is a
routine daily event and is NOT. When in doubt, ask: does this headline describe a move measured in
MULTIPLES over months, or a move measured in percent over a day? Only the first qualifies, and it
must still sit on top of a nameable catalyst — a superlative ALONE is momentum and is rejected.

The progression to watch, and the reason this works: "under the radar" -> "skyrocketing, still under
the radar" -> "a 1,300% rally" -> mainstream. Each rung is a step from the smart money toward the slow
herd. Naming it on the FIRST or SECOND rung is the whole edge; by the last rung the move is largely
spent.
NEVER propose a LEVERAGED or INVERSE ETF (2x/3x/-1x/-2x/-3x — e.g. NUGT, JNUG, AGQ, DUST, SOXL, SOXS,
TQQQ, SQQQ, UVXY, and any "Ultra"/"UltraPro"/"Direxion Daily"/"ProShares Ultra" product). They RESET
leverage DAILY and bleed from volatility decay, so they are day-trade instruments, NOT hold-the-catalyst
gems — name the UNDERLYING single-stock or the PLAIN 1x ETF/commodity instead.

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

NOT-YET-RESOLVED (the gate this list kept missing). The catalyst must still be PENDING or IN FORCE at
this week's date. Before proposing, ask: "what FUTURE event would flip this position to EXIT?" If the
honest answer is "the thing already happened", REJECT it — you would be buying news that is already
priced, and the event agent will close it on its very next read. Measured 2026-08-10: 44% of all
events opened lived exactly ONE scan, because past-tense headlines were being admitted as live theses.
  REJECT (resolved — the whole catalyst is in the past and nothing remains):
    "FDA approves X" (the approval IS the event), "Ghana grants the mining lease", "PDD surpassed
    Alibaba in market cap", "Q3 earnings beat", "the merger closed", "gold steadied ahead of CPI"
    once the CPI print has landed.
  KEEP (a resolution is still ahead of us):
    - IN FORCE and reversible — an export ban, sanctions, a closed chokepoint, a war, an outage. The
      condition persists and constrains supply until it LIFTS, and the lifting is the exit.
    - PENDING and dated — an FDA decision date not yet reached, a scheduled vote, a court date.
    - RESOLVED BUT WITH A NAMED NEXT LEG — an approval already granted where the press names a dated
      follow-on (launch date, capacity milestone, a supply contract still to be signed). Write the
      thesis around THAT leg, not the part that already happened.
A past-tense headline is fine as EVIDENCE that a condition exists; it is not itself a live catalyst.

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
  exit_advice  — <=20 words: the OBSERVABLE EVENT whose OCCURRENCE ends the thesis, and roughly WHEN
                 it is due ("Q3 earnings confirm DRAM pricing", "FDA decision by ~Mar 2026", "mine
                 reaches commercial output ~mid-2026"). Name the thing the thesis is WAITING FOR.
                 Do NOT write a reversal that may never arrive ("exit if export curbs are lifted",
                 "exit on a ceasefire") — a condition with no due date can never be checked off, so
                 the position rides until it times out. If the driver is a PERMANENT condition with
                 no pending milestone, this is a theme, not a catalyst: set thesis_live=false.
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
    pending_next: str = ""         # the concrete thing that has NOT happened yet; empty/none -> rejected
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


# DISCOVERY GATE. The press tell for a gem is a SUPERLATIVE plus an under-the-radar framing -- "best
# performing ETF of 2026, flying under the radar", "a 1,300% rally", "skyrocketing, still little known".
# That vocabulary is what separates a gem call from routine coverage, and it is detectable with a regex,
# so it costs nothing and is fully auditable.
#
# APPLIED TO THE SCOUT POOL ONLY. The event agents keep reading the FULL corpus via _filter_event,
# because tracking an event needs its ORDINARY follow-up ("chokepoint reopens", "contract signed") which
# carries no superlative at all. Filtering globally would starve them, and with gate_silent+max_stale_scans
# that reads as manufactured silence -- events would die of a drought we invented, and it would look
# exactly like an over-eager exit prompt.
SUPERLATIVE = re.compile(
    r"under[- ]the[- ]radar|flying under|little[- ]known|lesser[- ]known|obscure|overlooked|unnoticed|"
    r"under[- ]followed|hidden gem|nobody is talking|no one is talking|still early|"
    r"best[- ]performing|top[- ]performing|outperform|"
    r"\b\d{3,}\s*%|\b\d+(?:\.\d+)?\s*x\b|skyrocket|soar|surg(?:e|ed|ing)\b|rally of|"
    r"\bmultiplied\b|\bdoubl(?:e|ed|ing)\b|\btripl(?:e|ed|ing)\b", re.I)


def superlative_pool(arts: list[dict]) -> list[dict]:
    """The scout's DISCOVERY slice: articles whose headline carries the gem tell.

    Measured 2026-08-12 on the 208k-article corpus: keeps 7.6% (a median 411 per monthly scan, against
    3,000-4,700 unfiltered). Two things follow. The scout is 91% of the LLM bill and now reads ~10x less.
    And an added source can no longer be CROWDED OUT -- adding etf.com to the raw pool made discovery
    worse (48 tickers lost, including QUBT/RGTI/HL) because more candidates chased the same admission
    slots; filtered, etf.com and etftrends become the #2 and #6 contributors to what the scout sees."""
    return [a for a in arts if SUPERLATIVE.search((a.get("title") or ""))]


# The binding truncation on what the curator reads. Was a bare [:200] here, BELOW the 280 that
# lede.apply had already cut to -- so two separate hardcoded limits disagreed and the smaller one won
# silently. 200 chars of a listicle is its throat-clearing ("We recently compiled a list of..."), which
# is why the scout kept seeing "RKLB is skyrocketing" with no reason attached. Now one profile knob.
MAX_ARTICLE_CHARS = 800          # overridden per-run from the profile; see process_week


def _block(arts: list[dict], max_chars: int | None = None) -> str:
    n = max_chars or MAX_ARTICLE_CHARS
    return "\n".join(f"[{a.get('published_date','')} | {a.get('source','')}] {a.get('title','')}"
                     f" — {a.get('snippet','')[:n]} ({a.get('url','') or 'no url'})" for a in arts)


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


_NOTHING_PENDING = {"none", "n/a", "na", "nothing", "null", "-", "already happened",
                    "nothing pending", "no pending catalyst", "resolved", "unknown"}
_SCOUT_DEADLINE = 420    # seconds for ALL scout chunks of one scan; a wedged call must not stall a run
SCOUT_CHUNK = 200        # headlines per scout call; 0 = one call over the whole pool (pre-2026-08-10)


def _gem_beats() -> set:
    """The gem-beat query strings from retrieval_config.json -- the strategy's own early-gem
    vocabulary (uranium squeeze, rare-earth curbs, export bans, war chokepoints, under-the-radar
    framing) as opposed to the generic sector-coverage beats."""
    try:
        import json as _j
        cfg = _j.loads((REPO_ROOT / "retrieval_config.json").read_text())
        return {b["query"] for b in cfg.get("gem_beats", [])}
    except Exception:  # noqa: BLE001
        return set()


def _scout_chunks(arts: list[dict], size: int) -> list[list[dict]]:
    """Split the week's pool into topic-coherent chunks, one scout call each.

    BY BEAT, not by arbitrary slice. Each call then judges "is this a standout WITHIN uranium /
    rare earths / defence", which is a far easier question than "is this a standout among 1,480
    mixed headlines" -- and the second question is what the single-call scout was being asked.
    Measured 2026-08-10: it read the whole pool and still proposed a median of ONE name per scan,
    unchanged across two prompt rewrites. relevance.py already batches at this size for the same
    reason; the scout was the last stage still doing a single mega-pass.

    An article carrying several beats lands in several chunks -- that is fine, the union dedupes by
    ticker. Ordering is deterministic (beat name, then url) so the same pool always chunks the same
    way and a rerun is comparable."""
    if not size:
        return [arts], [set()]
    by_beat: dict = {}
    for a in arts:
        for b in (a.get("queries") or ["(no beat)"]):
            by_beat.setdefault(b, []).append(a)
    out: list[list[dict]] = []
    beats_of: list[set] = []          # parallel to `out`: which beat(s) each chunk was built from
    bin_: list = []
    bin_beats: set = set()
    for b in sorted(by_beat):
        grp = sorted(by_beat[b], key=lambda x: x.get("url", ""))
        if len(grp) >= size:                        # a big beat gets its own chunk(s)
            for i in range(0, len(grp), size):
                out.append(grp[i:i + size]); beats_of.append({b})
            continue
        # PACK the small beats together instead of one call per beat. The beat distribution is very
        # skewed -- a naive one-chunk-per-beat split produced 56 chunks for 4k articles, many of size
        # 1, each still paying a full system prompt for nothing.
        if len(bin_) + len(grp) > size and bin_:
            out.append(bin_); beats_of.append(set(bin_beats)); bin_, bin_beats = [], set()
        bin_ += grp; bin_beats.add(b)
    if bin_:
        out.append(bin_); beats_of.append(set(bin_beats))
    if not out:
        return [arts], [set()]
    return out, beats_of


def _scout_once(client, anchor, chunk: list[dict], rblock: str, label: str,
                block: str | None = None) -> list[dict]:
    """One scout call over one chunk -> raw candidate dicts (unvalidated).

    `block` lets the caller pass pre-rendered text (the ticker-grouped view) instead of a flat
    headline list; everything downstream is identical."""
    body = block if block is not None else _block(chunk)
    # GROUPED MODE NEEDS ITS OWN FRAMING. The list prompt below says "Headlines:" and "read the WHOLE
    # list", which invites the model to pick the standout ACROSS everything in the call -- so a call
    # holding bundles for RKLB, ASTS and LMT would tend to yield one candidate, not three. The bundles
    # are independent tickers and each deserves its own verdict, so say so explicitly.
    if block is not None:
        lead = (f"Week ending {anchor.date()}. Below are SEPARATE per-ticker news bundles, each headed "
                f"=== TICKER === and ordered oldest-first.\n\n{body}\n{rblock}\n"
                "JUDGE EACH BUNDLE INDEPENDENTLY. They are different companies competing for nothing -- "
                "one strong bundle does not disqualify another, and there is NO QUOTA. Propose a "
                "candidate from EVERY bundle that qualifies, and none from those that do not.\n"
                "READ A BUNDLE AS ONE STORY, not as separate headlines. The articles are the same "
                "ticker's recent coverage in date order, so a bundle can show you a CAUSE and its "
                "EFFECT together -- 'wins $5.6B contract' early and 'stock is soaring' later are one "
                "driver, and the pairing is the signal. A bundle of nothing but 'stock jumped' with no "
                "cause anywhere in it is NOT a candidate.\n")
    else:
        lead = f"Week ending {anchor.date()}. Headlines:\n\n{body}\n{rblock}\n"
    user = (lead +
            "WORK IN TWO PASSES, in this order:\n"
            "  PASS 1 - ENUMERATE. Read EVERYTHING above and note every ticker whose coverage carries "
            "either (a) a nameable catalyst, or (b) run-scale superlative framing. Do not judge yet; "
            "just gather.\n"
            "  PASS 2 - FILTER. For EACH survivor write `pending_next`: the concrete thing that has "
            "NOT happened yet and whose happening would END this thesis -- a decision date, a vote, a "
            "ruling, a deal still to close, a curb still to be lifted. If the only thing you can name "
            "is the event that ALREADY happened (the contract WAS awarded, the merger WAS announced, "
            "earnings WERE reported, the approval WAS granted), then there is nothing pending: write "
            "\"none\" and DROP the candidate. A catalyst in the past tense is news the market has "
            "already priced. Propose every survivor; there is no quota.\n"
            "This is ONE SLICE of a larger week, so judge it on its own merits -- do not hold back "
            "because you expect better names elsewhere. If nothing here qualifies, propose nothing. "
            "Output the JSON.")
    try:
        txt = client.complete(SCOUT_SYSTEM, user, use_web_search=False, stage="agent",
                              label=label, json_schema=SCOUT_SCHEMA)
        return _extract(txt).get("candidates", [])
    except Exception as e:  # noqa: BLE001 - one bad chunk must not lose the whole week
        print(f"  scout chunk failed ({type(e).__name__}) {label}", file=sys.stderr)
        return []


# ---- TICKER-GROUPED SCOUT INPUT -------------------------------------------------------------------
# The scout used to read a flat list of gate-passing headlines, one line each, and judge every line on
# its own. That cannot work for the case this project cares most about. Measured on the 2025-04-16
# window: of 53 Rocket Lab articles, the 3 that passed the gate were ALL "RKLB is skyrocketing" -- the
# gate keeps magnitude language and rejects plain catalyst reporting, so "Joins Space Force Launch
# Program" and "Jumps On Two Hypersonic Testing Contracts" never reached the scout. It was handed a
# move with no cause and correctly refused to open an event.
#
# Grouped, the same window gives the scout ONE Rocket Lab bundle holding both halves in date order, and
# the question changes from "does this headline name a pending catalyst?" (unanswerable from a listicle
# lede) to "does this ticker's recent coverage describe a live driver?" -- which is the question the
# strategy actually needs answered.
#
# The gate still decides WHICH tickers are worth looking at, so the attention filter and its ~10x cost
# saving survive; it no longer decides WHAT the scout may read about them. That mirrors the privilege
# event agents have always had via _filter_event, which reads the full window.
SCOUT_ARTICLES_PER_CALL = 30     # batching budget ONLY -- never truncates a group
MAX_ARTICLE_ORGS = 4             # above this an article is a listicle; see orgs.group
GROUP_BY_TICKER = True           # False restores the flat beat-chunked scout input


def _group_block(key: str, arts: list[dict], max_chars: int) -> str:
    """One ticker-group, oldest first, as the scout sees it."""
    head = f"=== {key.upper()} — {len(arts)} article(s) ==="
    return head + "\n" + _block(arts, max_chars=max_chars)


# _thin() DELETED 2026-08-15. It capped a group at max_group_articles and was a hack: measured across
# six windows, the biggest group the corpus ever produces is NVDA at 274 articles = 89k chars ~ 22k
# tokens, which is 2.2% of llama-4-maverick's 1M context. The cap was never protecting context, only
# shaving cost -- and it did so by DELETING NEWS, which is the one thing this pipeline cannot afford.
# It had already been caught dropping "Joins Space Force Launch Program" from the Rocket Lab group,
# the single article the whole grouping design exists to surface.
#
# A ticker's group is now passed WHOLE. The remaining budget below decides only how many SMALL groups
# share a call -- an ATTENTION question, not a truncation one -- and a group larger than the budget
# simply gets a call to itself, intact.

def _scout_groups(gated: list[dict], full_pool: list[dict], canon: dict,
                  max_article_orgs: int, articles_per_call: int) -> list[list[tuple]]:
    """[[(org, [articles]), ...], ...] -- ticker-groups packed into per-call batches.

    SEEDED BY THE GATE, FILLED FROM THE FULL POOL. Only entities the gate flagged get a group (so the
    scout's attention is still bought by a move-signal or gem framing), but the group is then filled
    from every article about that entity in the window, which is where the driver lives."""
    import orgs as _orgs
    seeds: list = []
    for a in gated:
        for k in _orgs.article_orgs(a, canon):
            if k not in seeds:
                seeds.append(k)
    full = _orgs.group(full_pool, max_article_orgs=max_article_orgs, canon=canon)
    groups = []
    for k in seeds:
        arts = full.get(k) or []
        if not arts:
            continue
        groups.append((k, arts))            # WHOLE group: never drop a ticker's news
    # BIN-PACK BY ARTICLE COUNT, not a fixed groups-per-call. With 8 groups per call a 256-article
    # NVDA bundle shared a call with seven others and drowned them -- the model sees one enormous
    # story and seven footnotes, which is the crowding this design set out to remove.
    #
    # One call per group fixes that completely but triples the scan: SCOUT_SYSTEM is 12.5k chars, so
    # 67 groups pay that overhead 67 times -- measured, 87k -> 277k tokens for one window.
    # Bin-packing to an article budget gets the same isolation for +26%: measured on the same window,
    # 16 calls and 110k tokens. A group at or over the budget lands in a call of its own; small ones
    # (the median group is 2 articles) share, which is nearly free.
    #
    # The budget IS max_group_articles, so one knob does both jobs: it caps the biggest single group
    # AND guarantees no call can hold more than one such group's worth.
    groups.sort(key=lambda kv: -len(kv[1]))            # densest first: big groups get their own call
    budget = articles_per_call or 10 ** 9
    batches, cur, n = [], [], 0
    for g in groups:
        if cur and n + len(g[1]) > budget:
            batches.append(cur)
            cur, n = [], 0
        cur.append(g)
        n += len(g[1])
        if n >= budget:
            batches.append(cur)
            cur, n = [], 0
    if cur:
        batches.append(cur)
    return batches


def scout(client, anchor: pd.Timestamp, arts: list[dict], retired: str = "",
          max_new_events: int = CANDIDATE_CAP, chunk_size: int = SCOUT_CHUNK,
          full_pool: list[dict] | None = None, canon: dict | None = None,
          max_article_orgs: int = 4) -> list[dict]:
    """`arts` is the GATED slice (what earns attention). When `full_pool` is given the scout reads
    TICKER-GROUPS built from it instead of a flat headline list -- see _scout_groups."""
    if not arts:
        return []
    rblock = (f"\nALREADY-RESOLVED — DO NOT RE-PROPOSE these on lingering hype (the catalyst already "
              f"happened/ended, so the edge is GONE even if the press keeps citing it):\n{retired}\n"
              if retired else "")
    batches = (_scout_groups(arts, full_pool, canon or {}, max_article_orgs, SCOUT_ARTICLES_PER_CALL)
               if full_pool else [])
    if batches:
        blocks = ["\n\n".join(_group_block(k, v, MAX_ARTICLE_CHARS) for k, v in b) for b in batches]
        print(f"  scout: {sum(len(b) for b in batches)} ticker-groups from {len(arts)} gated "
              f"articles -> {len(blocks)} call(s)", file=sys.stderr, flush=True)
        per = [[] for _ in blocks]

        def _rung(i, bl):
            try:
                per[i] = _scout_once(client, anchor, [], rblock,
                                     f"scout-{anchor.date()}-g{i}", block=bl) or []
            except Exception as e:  # noqa: BLE001 -- one bad batch must not take the scan down
                print(f"  scout group-batch {i} failed: {type(e).__name__}: {e}", file=sys.stderr)

        threads = [_Thread(target=_rung, args=(i, bl), daemon=True, name=f"scoutg-{i}")
                   for i, bl in enumerate(blocks)]
        for t in threads:
            t.start()
        _dl = _time.monotonic() + _SCOUT_DEADLINE
        for t in threads:
            t.join(max(0.0, _dl - _time.monotonic()))
        # UNION by ticker, exactly as the chunked path does. No _gem flag here: grouping is by ENTITY,
        # not by beat, so a candidate has no single originating beat to score. max_new_events therefore
        # truncates on arrival order (densest-coverage groups first) rather than on gem-beat rank.
        merged: dict = {}
        for grp in per:
            for c in grp:
                if not isinstance(c, dict):
                    print(f"  scout: dropped non-object candidate {c!r}", file=sys.stderr)
                    continue
                k = str(c.get("ticker", "")).strip().upper()
                if not k:
                    continue
                if k in merged:
                    merged[k]["peers"] = list(dict.fromkeys(
                        list(merged[k].get("peers") or []) + list(c.get("peers") or [])))
                else:
                    merged[k] = dict(c)
                    merged[k]["_gem"] = False
        cands = list(merged.values())
        print(f"  scout: {sum(len(g) for g in per)} raw -> {len(cands)} unique", file=sys.stderr)
    else:
        chunks, chunk_beats = _scout_chunks(arts, chunk_size)
        gem = _gem_beats()
        if len(chunks) == 1:
            cands = _scout_once(client, anchor, chunks[0], rblock, f"scout-{anchor.date()}")
        else:
            # BOUNDED WAIT. ex.map() blocks forever on a future that never returns, so one wedged HTTP
            # call freezes the entire backtest -- observed 2026-08-11, a run sat at 0% CPU for 54 minutes
            # with no retry logged. The per-request timeout in llm.py does not help if the hang is below
            # it. A chunk that misses the deadline is dropped (its beat is simply unscouted this scan),
            # which loses a little recall and never the run.
            # DAEMON THREADS, NOT ThreadPoolExecutor. A wedged chunk has to be abandonable at TWO points,
            # and the executor blocks at both:
            #   1. `with ThreadPoolExecutor(...)` calls shutdown(wait=True) on exit -- it waits for exactly
            #      the thread the deadline just gave up on. f.cancel() does NOT help: it is a no-op once a
            #      future is running. Observed 2026-08-14 on the first forward scan -- the 420s deadline
            #      fired, printed its message, and the process then sat at 0.1% CPU for 19 more minutes
            #      with no LLM call, i.e. the "0% CPU for 54 minutes" stall this guard was meant to prevent.
            #   2. Even with shutdown(wait=False), the pool's threads are NON-DAEMON and Python joins them
            #      at interpreter exit, so the scan finishes its work and then hangs on the way out --
            #      under cron that strands a process every day. Verified both by wedging a chunk on purpose.
            # Daemon threads fix both: abandoned instantly here, and never block process exit.
            per = [[] for _ in chunks]
            done = [False] * len(chunks)

            def _run(i, ch):
                try:
                    per[i] = _scout_once(client, anchor, ch, rblock, f"scout-{anchor.date()}-{i}") or []
                except Exception as e:  # noqa: BLE001 -- one bad chunk must not take the scan down
                    print(f"  scout chunk {i} failed: {type(e).__name__}: {e}", file=sys.stderr)
                finally:
                    done[i] = True

            threads = [_Thread(target=_run, args=(i, ch), daemon=True, name=f"scout-{i}")
                       for i, ch in enumerate(chunks)]
            for t in threads:
                t.start()
            _deadline = _time.monotonic() + _SCOUT_DEADLINE
            for t in threads:
                t.join(max(0.0, _deadline - _time.monotonic()))
            if not all(done):
                n_lost = sum(1 for d in done if not d)
                print(f"  scout: {n_lost}/{len(chunks)} chunks timed out after {_SCOUT_DEADLINE}s; "
                      f"proceeding without them", file=sys.stderr)
            gi = 0
            # UNION by ticker: the same name surfacing in two beats is one candidate, not two. First
            # occurrence keeps its thesis; peers are merged so no vehicle is lost to chunk boundaries.
            merged: dict = {}
            for gi, grp in enumerate(per):
                for c in grp:
                    # A cheap scout occasionally emits a bare string where the schema asks for an object
                    # ("NVDA" instead of {"ticker":"NVDA",...}). Crashed the 3-year v8 run at scan 34/37
                    # after 70 minutes, so treat it as the malformed candidate it is and drop it -- one
                    # bad row must never cost the whole curation.
                    if not isinstance(c, dict):
                        print(f"  scout: dropped non-object candidate {c!r}", file=sys.stderr)
                        continue
                    k = str(c.get("ticker", "")).strip().upper()
                    if not k:
                        continue
                    if k in merged:
                        merged[k]["peers"] = list(dict.fromkeys(
                            list(merged[k].get("peers") or []) + list(c.get("peers") or [])))
                    else:
                        merged[k] = dict(c)
                        merged[k]["_gem"] = bool(gem & chunk_beats[gi]) if gi < len(chunk_beats) else False
            # GEM-BEAT PREFERENCE. Chunking is BY BEAT, so every candidate already knows which beat
            # surfaced it -- provenance for free, no extra LLM call. Measured 2026-08-11 on the 3-year
            # run: events whose opening evidence was purely gem-beat cancelled at 11%, versus 89% for
            # all events, and the relationship is monotone in gem share. So a gem-beat candidate outranks
            # a coverage-beat one, which turns max_new_events from arbitrary truncation (cands[:N] over
            # dict order) into a real quality gate.
            cands = sorted(merged.values(), key=lambda c: (not c.get("_gem", False),))
            print(f"  scout: {len(chunks)} chunks -> {sum(len(g) for g in per)} raw -> {len(cands)} unique "
                  f"({sum(1 for c in cands if c.get('_gem'))} gem-beat)",
                  file=sys.stderr)
    out, _dropped_resolved = [], []
    for c in (cands if not max_new_events else cands[:max_new_events]):   # max_new_events=0 -> uncapped inflow
        # ENFORCED, not merely instructed. Measured 2026-08-11: 8 of 9 one-scan events were past-tense
        # catalysts ("contract awarded", "merger announced", "earnings reported") that the event agent
        # closed one scan later with "already announced and public". The NOT-YET-RESOLVED prose rule
        # sits 60% into a 12k-char prompt and was being ignored; a required schema field the model must
        # fill, checked here, is a gate. Same commit-then-check shape that fixed the exit side.
        _pn = str(c.get("pending_next") or "").strip().lower()
        if (not _pn) or _pn in _NOTHING_PENDING or len(_pn) < 8:
            _dropped_resolved.append(str(c.get("ticker", "")))
            continue
        try:
            m = ScoutCandidate(**{k: v for k, v in c.items() if not k.startswith("_")})
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
    if _dropped_resolved:
        print(f"  scout: dropped {len(_dropped_resolved)} already-resolved candidate(s) "
              f"({', '.join(_dropped_resolved[:6])}) ({anchor.date()})", file=sys.stderr)
    picker_log.log("scout", {"context": str(anchor.date()), "max_new_events": max_new_events,   # OFF unless enabled
                             "chunks": len(chunks), "dropped_resolved": _dropped_resolved,
                             "proposed": [{"ticker": c.get("ticker", ""), "company": c.get("company", ""),
                                           "thesis": c.get("thesis", "")} for c in cands],
                             "admitted": [p["ticker"] for p in out]})
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
    pool = firehose.news_pool(qs, win_start, win_end, chunk_days=chunk_days, per=per,
                              cache_path=str(cache_f))
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
    gpool = firehose.news_pool(qs, win_start, anchors[-1], chunk_days=pool_chunk_days, per=pool_per,
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
    "required": ["exit_case", "catalyst_resolved", "thesis_live", "exit_advice", "milestones", "assessment", "news_claims", "vehicles", "sources"],
    "properties": {"exit_case": {"type": "string"}, "catalyst_resolved": {"type": "boolean"},
        "thesis_live": {"type": "boolean"},
        "exit_advice": {"type": "string"},
        "milestones": {"type": "array", "items": {"type": "string"}},   # ordered catalyst-progress events (the arc)
        "assessment": {"type": "string"},
        "news_claims": {"type": "string"},
        "vehicles": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}}}}

# CONVICTION RETIRED 2026-08-14. The agent used to rate `conviction` 1-10 here, plus a PRICED-IN DECAY
# and a SILENCE DECAY rule that stepped it up and down. All three are gone, for two reasons:
#   MEASURED WORTHLESS. conviction ranked ~random against its own null (picker.py, evscore.py both record
#   the replay). The event cull is arithmetic coverage-rank now and the portfolio cull is the picker or a
#   price-trend rank -- firehose.py's cull comment already read "No gates, no conviction".
#   INERT BY CONSTRUCTION. Both decay rules explicitly left catalyst_resolved=false and thesis_live=true,
#   so they moved ONLY the score. With nothing reading the score, ~600 chars of prompt per event-agent
#   call bought a number that changed no decision -- while inviting a future reader to wire it back in.
# The SILENCE-WEEK code path (_silence_entry) survives untouched: skipping the LLM call on a no-coverage
# week is a real cost saving. It simply no longer carries a score. Judgment the agent still owns: the
# live/exit switch, the standing exit condition, the milestone arc, and which vehicles express it.
EVENT_AGENT_SYSTEM = """You manage ONE event for an event-driven book. You are given: the event's
CATALYST (FIXED — the discrete thing you entered on), its KNOWN vehicles, your FULL weekly journal
for this event since entry (your memory — the whole arc, not just last week), and this week's news.
Write the new note.

FIRST, TEST YOUR OWN STANDING EXIT CONDITION. Your journal carries the `exit_advice` you wrote when
you entered and every week since: "exit if/when <observable thing>". Read it and answer ONE concrete
question against THIS WEEK'S NEWS and every prior week — HAS THAT THING HAPPENED? It is a factual
check, not a fresh opinion. If it has, the thesis is over: say so in `exit_case`, and set
catalyst_resolved=true if the trigger you named WAS the catalyst resolving. THE DUE DATE COUNTS TOO:
if the milestone you named was due and has now passed — it happened, or it quietly did not — the
thesis is equally over, because what you were waiting for is no longer ahead of you. Re-deriving the exit case
from scratch each week is how a position drifts past its own stated trigger -- you already committed
to what would end this, so check that first, before forming any new view.

THEN ARGUE FOR EXIT ANYWAY (devil's advocate — do this BEFORE deciding, EVERY week, against your WHOLE
journal): state the SINGLE strongest reason this thesis is ALREADY OVER — the catalyst has RESOLVED
(occurred / closed / been signed) or its DRIVING CONDITION has REVERSED (curbs lifted, ceasefire,
chokepoint reopened, shortage ended) (`exit_case`, <=20 words). Write "none" ONLY after genuinely
looking and finding nothing. This is NOT a "was I right?" review (that just rubber-stamps the hold) —
it is the case AGAINST the position, made fresh each week, which is what defeats hold-inertia.

A QUIET STRETCH IS NOT AN EXIT — BUT A FINISHED CATALYST IS. Do NOT exit on "staleness", "aging
thesis", "edge decayed", or "N quiet weeks with no fresh news" while the catalyst you named is STILL
PENDING. That guard protects a live catalyst that has gone quiet; it does NOT protect a catalyst that
has already happened, or one whose due date has passed, or a thesis resting on a PERMANENT condition
(standing export controls, an ongoing war, a secular trend) with no pending milestone left. Those are
over, and holding them until the age cap retires them is the failure this paragraph must not cause. A live catalyst can go silent for weeks and then RE-ACCELERATE (a follow-on deal, an
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
(catalyst_resolved stays false): a structural driver proves itself by delivering FRESH milestones, not by
the mere passage of time, so treat prolonged quiet as a reason to scrutinise whether the driver is still
running — never as an exit on its own. Don't confuse a single milestone passing amid ONGOING coverage
(compounding) with true silence (fading). (c) RE-ANCHOR: the driver is fixed, but update your thesis to its
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
lands this week; never pad with speculation. This is the evidence trail behind your live/exit call — a LIVE
driver keeps throwing off fresh milestones; a stalled/resolved one stops (feed that into the exit logic above).

Output ONLY JSON: {"exit_case":"...","catalyst_resolved":false,"thesis_live":true,
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


EVENT_NEWS_CAP = 20   # default; overridden by the profile's `event_news_cap` (see process_week)


def _filter_event(arts, event, cap: int = EVENT_NEWS_CAP):
    """The articles ONE event-agent re-reads this scan, BEST FIRST.

    Ranked, not merely truncated. This used to return `hits[:cap]` in pool order -- which the
    backtest sorts most-recent-first -- so each agent saw the NEWEST `cap` matches rather than the
    most relevant. The median event-week matches ~55 articles, so ~35 were discarded on publication
    timestamp alone, and a mining.com piece from Monday lost to twenty bot posts from Friday.

    The ranking is structural and deterministic -- no LLM, and deliberately NO fitted per-source
    weights, which would be tuning retrieval to this backtest's own outcomes (CLAUDE.md #6):
      +3  one of the event's VEHICLES appears in the TITLE  -> the article is about this NAME
      +2  a catalyst keyword appears in the TITLE           -> the article is about this THESIS
      +1  a GEM beat surfaced it                            -> the strategy's own early-gem vocabulary
      recency breaks ties.
    With the slice ranked, `cap` decides only HOW MUCH evidence is seen, not WHICH."""
    veh = {v.lower() for v in event["vehicles"]}
    kws = [w for w in event["catalyst"].lower().replace(",", " ").split() if len(w) > 4]
    gem = _gem_beats()
    scored = []
    for a in arts:
        title = (a.get("title") or "").lower()
        hay = title + " " + (a.get("snippet") or "").lower()
        if not (any(v in hay for v in veh) or any(k in hay for k in kws)):
            continue
        s = (3 if any(v in title for v in veh) else 0)
        s += (2 if any(k in title for k in kws) else 0)
        s += (1 if gem & set(a.get("queries") or []) else 0)
        scored.append((-s, a.get("published_date", "") or "", a))
    scored.sort(key=lambda x: (x[0], [-ord(c) for c in x[1]]))   # score desc, then newest first
    hits = [a for _, _, a in scored]
    return hits[:cap] if cap else hits


def _journal_digest(entries: list[dict], keep: int = 20) -> str:
    """Compact week-by-week journal so the agent sees the FULL arc of an event since entry — the
    catalyst it entered on, how the VEHICLE evolved, and every live/exit read — not just
    last week. One line per week: date | live | vehicles | assessment | standing exit
    condition, plus a trailing milestone trail. Carrying these forward makes them LOAD-BEARING: the
    agent re-reads its own prior read, its 'exit-if' trigger, and the
    milestone arc each week and tests them against the news, instead of re-deriving (or forgetting)
    them. The entry week is always shown."""
    if not entries:
        return "(none — this is the first week of this event)"

    def line(e):
        veh = ",".join(e.get("vehicles", [])) or "-"
        xa = (e.get("exit_advice", "") or "").strip()
        base = (f"{e.get('date', '?')} live={e.get('thesis_live')} "
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


def event_agent_v2(client, anchor, event, entries, news, effort="high"):
    digest = _journal_digest(entries)
    entry_wk = entries[0]["date"] if entries else anchor.date().isoformat()
    nb = _block(news) if news else "(no fresh coverage for this event this week)"
    user = (f"Event catalyst (FIXED — what you entered on): {event['catalyst']}\nEntered: {entry_wk}\n"
            f"Known vehicles: {', '.join(sorted(event['vehicles']))}\nWeek ending {anchor.date()}.\n\n"
            f"Your journal so far (oldest -> newest):\n{digest}\n\nThis week's news:\n{nb}\n\n"
            "Re-check the EXIT condition against your WHOLE journal, then write this week's note and "
            "pick the current vehicle(s) (JSON).")
    txt = client.complete(EVENT_AGENT_SYSTEM, user, use_web_search=False, stage="agent",
                          label=f"event-{event['id']}-{anchor.date()}", json_schema=EVENT_AGENT_SCHEMA, effort=effort)
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
            "exit_advice": e.exit_advice,
            "milestones": [str(m).strip() for m in (e.milestones or []) if str(m).strip()][:6],
            "assessment": e.assessment,
            "news_claims": e.news_claims, "sources": [u for u in e.sources if u][:6], "vehicles": veh}


def _carry_forward(anchor, ev) -> dict:
    """SILENCE WEEK (no pooled article mentions this event's vehicles or catalyst): reproduce
    event_agent_v2's DETERMINISTIC no-news behavior WITHOUT an LLM call. The EVENT_AGENT_SYSTEM prompt
    instructs holding steady when there is no fresh coverage — the thesis stays live (silence is not an
    exit) and the vehicles are unchanged. (It used to also step a `conviction` score down 1 per silent
    week; that score was retired 2026-08-14 as measured-worthless and decision-inert, but skipping the
    LLM call on a silent week is a real saving, so this path stays.) Emitting that
    mechanically leaves the scans/portfolio identical to a live run while skipping the (dominant)
    silence-week judgment calls. A resolution/exit can only come FROM news, which IS fresh coverage -> the
    real agent runs then, so no exit is ever missed. Returns the same dict shape as event_agent_v2."""
    prev = ev["entries"][-1] if ev["entries"] else {}
    veh = prev.get("vehicles") or sorted(ev["vehicles"])[:1]    # no news -> vehicles unchanged from last week
    return {"date": anchor.date().isoformat(), "thesis_live": True, "exit_case": prev.get("exit_case", ""),
            "catalyst_resolved": False, "exit_advice": prev.get("exit_advice", ""),
            "milestones": prev.get("milestones", []),
            "assessment": "No fresh coverage this week — held mechanically (no LLM call).",
            "news_claims": "", "sources": [], "vehicles": veh}


def _validate_candidates(cands: list[dict], anchor, client=None) -> list[dict]:
    """Normalize, RESOLVE, then drop scout candidates whose symbols aren't tradeable — loudly. The
    single gate between the curator LLM's free-text output and the price layer.

    Before this existed, an event agent emitting the vehicle `RIGETTI COMPUTING` (a company name) or
    a symbol with no yfinance history (measured: `IFIN`, the sole funded pick of a 3-week smoke run)
    flowed straight through to score.fetch_panel, which returned nothing and dropped the position
    with NO trace in the output. A backtest then under-reported the positions the curator actually
    took, and the equity curve looked fine. Silent is the failure mode that matters here, hence the
    per-week print.

    Also a LOOK-AHEAD guard: score.validate_tickers rejects a symbol whose first trade postdates the
    anchor, so a backtest cannot buy a company that had not listed on the decision date.

    Normalization is applied too, so `$RGTI` / `NASDAQ:RGTI` / `(RGTI)` all collapse to `RGTI`
    instead of fragmenting one position into several unpriceable ones.

    RESOLUTION, not just rejection. `scout()` already runs a company NAME through
    `resolve_us_ticker` (a look-ahead-safe static name<->symbol lookup) — but ONLY for a candidate's
    PRIMARY ticker. Its `peers` list went through nothing at all, which is exactly how the vehicle
    `RIGETTI COMPUTING` reached an event's basket. So a name-shaped reject is sent to the SAME
    resolver here and re-validated; only if that fails is the symbol dropped. Otherwise this guard
    would discard a real position (RIGETTI COMPUTING -> RGTI) that the codebase already knows how to
    recover. Pass `client=None` to skip resolution (offline replays)."""
    if not cands:
        return cands
    as_of = anchor.date().isoformat()
    raw = {c["ticker"] for c in cands} | {p for c in cands for p in (c.get("peers") or [])}
    ok, bad = score.validate_tickers(sorted(raw), as_of=as_of)
    okset, rescued = set(ok), {}

    # second chance for the name-shaped rejects only: a bad SHAPE may be a resolvable company name,
    # whereas "no price history" / "not listed until ..." are verdicts about a real symbol and final.
    if client is not None:
        for orig, reason in list(bad.items()):
            if "company name" not in reason and "US-symbol shape" not in reason:
                continue
            sym = resolve_us_ticker(client, orig)
            if not sym:
                continue
            good, _ = score.validate_tickers([sym], as_of=as_of)
            if good:
                rescued[score.normalize_ticker(orig)] = good[0]
                okset.add(good[0])
                bad.pop(orig, None)

    def _fix(q: str) -> str:
        n = score.normalize_ticker(q)
        return rescued.get(n, n)

    kept = []
    for c in cands:
        tk = _fix(c["ticker"])
        if tk not in okset:
            continue                       # primary symbol unusable -> the whole candidate goes
        c = dict(c, ticker=tk,
                 peers=[p for p in (_fix(q) for q in (c.get("peers") or []))
                        if p in okset and p != tk])
        kept.append(c)
    if rescued:
        print(f"    ticker guard ({as_of}) resolved {len(rescued)}: "
              + "; ".join(f"{k!r} -> {v}" for k, v in rescued.items()), file=sys.stderr, flush=True)
    if bad:
        print(f"    !! ticker guard ({as_of}) rejected {len(bad)}: "
              + "; ".join(f"{k!r} -> {v}" for k, v in list(bad.items())[:6])
              + (f" (+{len(bad) - 6} more)" if len(bad) > 6 else ""), file=sys.stderr, flush=True)
    return kept


def process_week(client, anchor, pool, events, retired, nid, week_idx,
                 curator_memory_weeks=8, workers=8, src_fn=None, scout_client=None, gate_silent=True,
                 max_new_events=CANDIDATE_CAP, event_agent_effort="high",
                 event_news_cap=EVENT_NEWS_CAP, max_event_scans=0, discovery_filter=False,
                 max_events=0, picker=None, ev_metrics=None):
    """ONE event-first week on an article POOL: scout -> same-ticker guard + matcher -> event agents.
    Mutates `events` and `retired` IN PLACE; returns (picks, nid). This is the SHARED curator engine
    used by BOTH the backtest (agent.run_event_agent_scans, GDELT+seed pool) and the forward driver
    (forward_engine.run_week, live-gather pool) — so the two run byte-identical logic and a settled
    forward solution can be re-backtested just by swapping the pool source. `src_fn(tk)->str` labels a
    pick's provenance (default 'live'); the backtest passes a seed-vs-gdelt labeler.

    Two-tier LLM split: `scout_client` runs the cheap, high-volume extraction/routing stages (scout +
    matcher); `client` runs the judgment stage (the event agents). `scout_client` defaults to `client`,
    so single-client callers keep the pre-split behavior byte-for-byte.

    `gate_silent` (default True): skip the LLM event-agent on any live event with NO fresh coverage this
    week (empty `_filter_event`) — reproducing its deterministic silence-decay mechanically (see
    `_carry_forward`). Returns-neutral by construction; cuts the dominant silence-week judgment calls.
    Set False to force a live agent on every event (the pre-gate baseline, for A/B)."""
    scout_client = scout_client or client
    src_fn = src_fn or (lambda tk: "live")
    if curator_memory_weeks == 0:                          # 0 = feature OFF (scout not reminded at all)
        rmem = ""
    else:                                                  # <0 = whole history; >0 = last N weeks only
        rmem = "\n".join(f"- {t}: {c}" for t, (c, ri) in retired.items()
                         if curator_memory_weeks < 0 or (week_idx - int(ri)) < curator_memory_weeks)
    # The scout sees only the superlative slice; `pool` itself is untouched, so the event agents below
    # still call _filter_event over the FULL corpus.
    spool = superlative_pool(pool) if discovery_filter else pool
    if discovery_filter:
        print(f"  discovery gate: {len(spool)} of {len(pool)} articles carry the gem tell",
              file=sys.stderr, flush=True)
    # TICKER-GROUPED INPUT. `spool` (the gated slice) still decides WHICH entities earn attention;
    # `pool` (the full window) supplies what the scout may READ about them. Passing both is what pairs
    # "RKLB is skyrocketing" with "$5.6B Neutron win" -- the gate admits the first and rejects the
    # second, so on a flat list the scout only ever saw a move with no cause.
    _canon = None
    if GROUP_BY_TICKER and any(a.get("orgs") for a in pool):
        try:
            import orgs as _orgs
            _canon = _orgs.build_canon(pool)
        except Exception as e:  # noqa: BLE001 -- fall back to the flat path rather than lose the scan
            print(f"  scout: grouping unavailable ({type(e).__name__}: {e})", file=sys.stderr)
    cands = scout(scout_client, anchor, spool, retired=rmem, max_new_events=max_new_events,
                  full_pool=(pool if _canon else None), canon=_canon,
                  max_article_orgs=MAX_ARTICLE_ORGS)
    # DETERMINISTIC same-ticker guard: a ticker already held by a LIVE event belongs to that event —
    # never open a duplicate. Only genuinely NEW tickers go to the (fallible) LLM matcher.
    held_to_event = {v: eid for eid, ev in events.items() if ev["status"] == "live"
                     for v in ev["vehicles"]}
    # A blank thesis is unusable downstream -- it becomes an event with no catalyst, writes NaN to
    # firehose_scans.csv, and can never be judged resolved. Drop it at the door.
    cands = [c for c in cands if str(c.get("thesis") or "").strip()]
    new_cands = [c for c in cands if c["ticker"] not in held_to_event]
    # TICKER GUARD: normalize + verify tradeability BEFORE an unusable symbol can open an event and
    # burn an event-agent call on it (see _validate_candidates).
    new_cands = _validate_candidates(new_cands, anchor, client=scout_client)
    match = match_to_events(scout_client, anchor, new_cands, events) if new_cands else {}
    for c in new_cands:
        tk, eid = c["ticker"], match.get(c["ticker"], "new")
        peers = {q for q in c.get("peers", []) if q != tk}   # already normalized + validated above
        if eid in events and events[eid]["status"] == "live":
            events[eid]["vehicles"] |= {tk, *peers}
        else:
            nid += 1
            events[f"ev{nid}"] = {"id": f"ev{nid}", "catalyst": c["thesis"],
                                  "status": "live", "vehicles": {tk, *peers}, "entries": []}
    # AGE CAP -- the mechanical backstop for a catalyst that never resolves. The design contract is
    # that a catalyst is "specific, datable, resolvable"; an event still live after `max_event_scans`
    # has, by that definition, turned out to be a THEME. Getting the LLM to call catalyst_resolved
    # reliably has failed three times (see the catalyst-gate memories), and MAX_STALE only fires on
    # SILENCE -- a theme like "uranium supply shortage" is covered every week, so it never goes stale
    # and never exits. Measured 2026-08-10: events ran 68 and 78 scans (2.5-3 YEARS) on exactly that
    # failure. This retires them without an LLM call, which also stops paying for them.
    # The scout may re-propose the thesis on fresh evidence; that is the intended escape hatch.
    if max_event_scans:
        for ev in list(events.values()):
            if ev["status"] == "live" and len(ev.get("entries") or []) >= max_event_scans:
                ev["status"] = "exited"
                for tk in ev["vehicles"]:
                    retired[tk] = (f"{ev['catalyst']} (aged out after {max_event_scans} scans)", week_idx)
                print(f"  aged out after {len(ev['entries'])} scans: {ev['catalyst'][:60]}", file=sys.stderr)
    # CONCURRENCY CAP. `max_events` bounds how many events may be LIVE at once, and the picker decides
    # which survive. This is deliberately NOT `max_new_events`: an admission cap discards candidates
    # permanently and unexamined at the door (measured 2026-08-12: 1,412 of 1,556 proposals binned, and
    # adding a source made discovery WORSE because the extra candidates just crowded the same 4 slots).
    # A concurrency cap keeps everything rankable -- a strong thesis arriving in a busy week competes
    # again next scan instead of being lost.
    #
    # The picker ranks on catalyst ARC (early/building over crested), never on predicted return, which is
    # what keeps it inside non-negotiable #1. It needs a STRONG model: measured 2026-07-14, sonnet5 hit
    # the 83rd percentile while a cheap picker came in BELOW random, so a weak picker is worse than none.
    # picker=None is the MECHANICAL CONTROL, not a disabled feature: it keeps the OLDEST max_events
    # (insertion order), which is the null any LLM ranker must beat. Without a control, "the picker
    # helped" cannot be distinguished from "capping concurrency helped".
    if max_events:
        _ev_metrics = ev_metrics if ev_metrics is not None else {}
        _live = [ev for ev in events.values() if ev["status"] == "live"]
        if len(_live) > max_events:
            meta = [{"ticker": ev["id"], "vehicles": sorted(ev["vehicles"]),
                     "catalyst": ev["catalyst"],
                     "milestones": (ev.get("entries") or [{}])[-1].get("milestones", []),
                     "exit_condition": (ev.get("entries") or [{}])[-1].get("exit_advice", ""),
                     "weeks_alive": len(ev.get("entries") or [])} for ev in _live]
            if picker is not None:
                keep = set(picker(meta, max_events, context=str(anchor.date())))
                _how = "picker"
            else:
                # DEFAULT: arithmetic ranking on this scan's PRESS COVERAGE (src/evscore.py) -- source
                # breadth, superlative count, coverage velocity, author breadth. Not a forecast, and
                # not an LLM: an LLM ranker has now failed to beat its own null three times here.
                import evscore  # noqa: PLC0415
                ranked = evscore.rank(_live, pool, prev=_ev_metrics)
                keep = {eid for eid, _, _ in ranked[:max_events]}
                for eid, sc, m in ranked:
                    _ev_metrics[eid] = m
                _how = "coverage-rank"
                print("    " + " · ".join(f"{eid}:{sc:.0f}(s{m['source_breadth']}/x{m['superlatives']})"
                                          for eid, sc, m in ranked[:6]), file=sys.stderr)
            for ev in _live:
                if ev["id"] not in keep:
                    ev["status"] = "exited"
                    for tk in ev["vehicles"]:
                        retired[tk] = (f"{ev['catalyst']} (picker-culled)", week_idx)
            print(f"  event-cull [{_how}]: "
                  f"{len(_live)} live -> kept {len(keep & {e['id'] for e in _live})} (cap {max_events})",
                  file=sys.stderr, flush=True)

    merged = _consolidate_events(events)                   # weekly dup-catalyst merge
    if merged:
        print(f"  consolidated {merged} duplicate-catalyst event(s) ({anchor.date()})", file=sys.stderr)
    live_events = [ev for ev in events.values() if ev["status"] == "live"]

    def work(ev):
        news = _filter_event(pool, ev, cap=event_news_cap)
        if gate_silent and not news:                       # silence week -> mechanical carry-forward, NO LLM call
            return ev, _carry_forward(anchor, ev)
        return ev, event_agent_v2(client, anchor, ev, ev["entries"], news, effort=event_agent_effort)

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
                              "assessment": entry.get("assessment", ""),
                              "exit_advice": entry.get("exit_advice", ""),
                              "milestones": entry.get("milestones", []),
                              "evidence_urls": entry["sources"]})
    return picks, nid


def run_event_agent_scans(start, end, rebalance_days, model, workers, queries=None, seed=None,
                          pool_chunk_days=90, pool_per=150, provider="anthropic", targeted=False,
                          enrich=False, enrich_fetch=True, curator_memory_weeks=8, news_cap=WINDOW_CAP,
                          arm="fuller") -> dict:
    """Event-first engine: scout -> match candidates into events -> per-event agent picks current
    vehicle(s). The watchlist is the union of each live event's current vehicles. Returns
    {anchor: picks} like the other engines, so backtest()/scoring are unchanged. Per-week resume.

    enrich=True: fill each week's GDELT headlines with their as-of-date Wayback lede (so the
    curator sees the ticker the headline omits), look-ahead-clean (snapshot <= anchor).

    `arm` selects which text the curator READS once the ledes are fetched (see lede.ARMS): "clean"
    is the only look-ahead-safe choice and the only one whose results are quotable; "fuller" (the
    default) prefers the clean lede and falls back to a fast `lede_live` where one exists."""
    import hashlib
    import os
    if enrich:
        import lede
    client = llm.make_client(provider, model)
    print(f"Event-agent: provider={provider} model={model}", file=sys.stderr)
    anchors = scan_anchors(start, end, rebalance_days)
    qs = queries or firehose.GDELT_QUERIES
    win_start = anchors[0] - pd.Timedelta(days=35)
    key = hashlib.md5(f"{qs}{win_start.date()}{anchors[-1].date()}{pool_chunk_days}{pool_per}".encode()).hexdigest()[:10]
    cache_f = REPO_ROOT / "data" / "windows" / f"gdelt_pool_{key}.json"
    cache_f.parent.mkdir(parents=True, exist_ok=True)
    stats_path = str(REPO_ROOT / "data" / "windows" / "retrieval_stats.json")
    gpool = firehose.news_pool(qs, win_start, anchors[-1], chunk_days=pool_chunk_days, per=pool_per,
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
            # Two-speed ledes: the archive pass fills `lede` (clean), then apply() picks the arm that
            # fills `snippet` -- the field the curator actually reads. With only the wayback pass run
            # here, "fuller" resolves to the clean lede for every article that has one, and falls back
            # to `lede_live` only where some other pass already supplied it.
            lede.enrich_wayback(gslice, a.date().isoformat(), cache_path=enrich_cache,
                                fetch=enrich_fetch, stats_path=stats_path)
            # SAME limit as _block: apply() used to cut at 280 and _block again at 200, so the
            # text was truncated twice by two different hardcoded numbers.
            lede.apply(gslice, arm=arm, max_chars=MAX_ARTICLE_CHARS)
        seed_slice = firehose._window(seeds, a, rebalance_days)
        win = seed_slice + gslice
        provenance[a.isoformat()] = [
            {"src": src,
             # explicit provenance now that clean/live ledes live in separate fields; the old
             # "snippet != title" heuristic could not tell a clean lede from a biased one.
             "wayback_hit": src == "gdelt" and x.get("lede_source") == "wayback",
             "lede_source": x.get("lede_source") or "none",
             "snippet_source": x.get("snippet_source") or "headline",
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
