"""evrank.py — the event ranker that replaces evscore's velocity+breadth cull.

WHY evscore WAS RETIRED. It could not tell this book's best event from its worst: on
cbt_3yr_v31_exposure, ev207 (falsifiable exit that fired on its own terms, six dated milestones, five
distinct vehicle theses) and ev214 ("Cameco HOPES to repeat its 2018 success", twelve scans of "no
tariff decision", non-events logged as `happened`, four duplicate clauses) BOTH scored 17.2 -- and on
lifetime average evscore ranked ev214 ABOVE ev207. Its dominant term, velocity at weight 4.0, was
+0% for both, so the score collapsed onto three log-saturated counts of how much generic press exists
around a keyword. It also had no freshness reserve: velocity is 0.0 for any event with no prior scan,
so it ranked newborns last -- the opposite of what non-negotiable #2 wants.

WHAT THIS RANKS ON, and the evidence for each term. Scored per event, per scan, from that event's
own record UP TO THAT SCAN -- no look-ahead. NO prices, NO returns, NO P&L are ever shown to the
judge, the same discipline as the Fable-5 audit that chose the event-agent model.

  catalyst_strength  IN THE KEY   the only component of the post-hoc audit that separated events
                                  ending on the agent's judgement from ones a counter retired
                                  (4.0 vs 3.0 median over 303 v31 events).
  market_impact      IN THE KEY   events scoring >=4 lasted longer than base: 61% ran past one scan
                                  against a 44% base rate, +17 points. ONE JOURNAL ONLY -- the
                                  equivalent breadth finding REVERSED between v31 and v30 the same
                                  day, so this term is the least trustworthy of the three.
  arc_progress       IN THE KEY   the dynamic term, and the best-evidenced: events that end on the
                                  merits go a median of ONE scan without a new milestone, events a
                                  counter retires go FIVE. Replicated on v31 AND v30.
  thesis_alignment   low weight   measured FLAT on exit type (2.0 vs 2.0). Kept because a basket
                                  nobody can explain is a real defect, not because it predicts.
  exit_quality       NOT IN KEY   measured BACKWARDS -- timer-retired events score HIGHER (5 vs 4),
                                  because a clean two-sided exit on an occurrence that never arrives
                                  is exactly the ev214 shape. Reported only.
  spans_a_month      IN THE KEY   as a flat +1.0 bonus, on STRATEGIC grounds rather than measured
                                  ones: this book earns on events that run a month or more, so a
                                  same-scan event is worth less of a slot. The measurement does NOT
                                  yet support it -- binarised it gave +1 point of lift over always
                                  answering yes, which it did for 88% of events -- so the bonus is
                                  kept small enough not to override the evidenced terms. See
                                  SPAN_BONUS.

THE WEIGHTS ARE NOT FITTED. Equal weight on the three evidenced terms and half on alignment. Fitting
them to this backtest's outcomes is the overfitting CLAUDE.md #6 warns about, and today's two sweeps
flipped four of five knobs between neighbouring curations.

evscore IS NOT DELETED. Leaving `picker_model` blank still selects it, which keeps the mechanical
null this has to beat -- "without a control, 'the picker helped' cannot be distinguished from
'capping concurrency helped'".
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import re
import sys
from pathlib import Path

import llm
import picker_log
from optimizer import resolve_curator_model

_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "windows" / "evrank_cache.json"

SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["catalyst_strength", "thesis_alignment", "exit_quality",
                       "market_impact", "arc_progress", "spans_a_month", "why"],
          "properties": {"catalyst_strength": {"type": "integer"},
                         "thesis_alignment": {"type": "integer"},
                         "exit_quality": {"type": "integer"},
                         "market_impact": {"type": "integer"},
                         "arc_progress": {"type": "integer"},
                         "spans_a_month": {"type": "boolean"},
                         "why": {"type": "string"}}}

SYSTEM = """You rank market events a curator is tracking, so the weakest can be dropped when more are
live than can be followed. You never see prices, returns or position sizes and must never guess at
them: you judge the EVENT AS WRITTEN and how far it has got, not whether it made money.

Score each 0-5 unless stated.

catalyst_strength — is the catalyst ONE SPECIFIC OCCURRENCE, with a subject, a status and ideally a
  date? "US DOE loans $1B to restart Three Mile Island, November 19 2025" is 5. "Cameco hopes to
  repeat its 2018 success in fending off tariffs, no date announced" is 1: a company's hope is not an
  occurrence and nothing was initiated. A standing condition that can plausibly END (a chokepoint
  closed, output cuts in force) is legitimate at 3-4; a permanent condition or a theme is 0-1.

thesis_alignment — does EVERY vehicle have its own reason to be attached, following from THIS
  catalyst? Five tickers sharing one sentence is 1, however true the sentence. A clause restating the
  catalyst backwards ("cuts lift crude" -> "benefits from higher oil prices") is 1-2. Distinct
  specific mechanisms are 4-5.

exit_quality — is the exit condition OBSERVABLE, and do its two branches point in OPPOSITE
  directions? "exit if/when the DOE decision is granted or refused" is 5: granted and refused are
  opposites and either is datable. CHECK THE BRANCHES, do not just count them -- "exit if/when
  US-Iran tensions ease or resolution is reached" reads two-sided and is NOT: easing and resolution
  are the same direction, so nothing settles it if tensions instead escalate, and it scores 1-2.
  "exit if/when the 120% upside is realized or fails to materialize" is 0: neither branch is
  observable. An exit that merely restates the catalyst is 2.

market_impact — how much of the market this occurrence could reach: a chokepoint carrying a fifth of
  seaborne oil is 5; one small company's product launch is 1. REACH only — never direction or size
  of any price move.

arc_progress — has this event MOVED since it opened? Read the milestone trail and the assessments.
  New dated milestones and assessments that report something changing are 4-5. A trail that repeats
  the same standing state ("no decision yet", "curbs remain unresolved") scan after scan is 0-1,
  however much coverage it attracts. An event on its first scan is 3: unproven, not stalled.

spans_a_month — TRUE if this event, measured FROM WHEN IT OPENED, looks likely to run A MONTH OR
  MORE before its exit condition settles. Not "how much is left from here" -- an event already eight
  scans old that settles next week still ran for months, and that is what this asks. The boundary
  resolves TRUE: about a month counts as a month or more. FALSE only for an event that should open
  and settle inside a single scan -- a decision landing this week, a deal already closing. Decide
  from whether the exit names something SCHEDULED (a dated decision, a vote, an expiry) and how far
  out it sits.

why — one sentence under 25 words naming the weakest of the six."""

# Equal weight on the three evidenced terms, half on alignment. Not fitted -- see the module docstring.
WEIGHTS = {"catalyst_strength": 1.0, "market_impact": 1.0, "arc_progress": 1.0,
           "thesis_alignment": 0.5}

# A FLAT BONUS FOR AN EVENT EXPECTED TO OUTLAST A MONTH, added 2026-09-08 at the user's direction.
# THE EVIDENCE FOR IT IS WEAK AND IS RECORDED HERE SO NOBODY MISTAKES IT FOR MEASURED: binarised on
# 303 v31 events, "expected >= 2 months" answered YES to 88% of them and its yes-group lasted past
# one scan 45% of the time against a 44% base -- +1 point of lift, i.e. none. The integer version it
# replaced piled 63% of events onto "6" and "12" against an actual median of ONE scan.
# THE RATIONALE IS STRATEGIC, not statistical: this book appears to earn on events that run a month
# or more, so an event expected to settle inside one scan is worth less of a slot even if the
# measurement cannot yet see it. Kept DELIBERATELY SMALL -- 1.0 against a key that spans 5.0-13.5 in
# practice -- so it breaks ties toward longer events without overriding the three evidenced terms.
# EASY TO RE-TEST: every component is stamped per scan, so changing this constant and re-ranking is
# a replay, not a curation.
SPAN_BONUS = 1.0


def score_of(v: dict) -> float:
    """The rank key. exit_quality stays out: it measured BACKWARDS (median 5 for timer-retired events
    against 4 for ones that ended on the agent's judgement), because a clean two-sided exit on an
    occurrence that never arrives is exactly the ev214 shape."""
    s = sum(w * float(v.get(k, 0) or 0) for k, w in WEIGHTS.items())
    return s + (SPAN_BONUS if v.get("spans_a_month") else 0.0)


def _brief(a: dict) -> str:
    """What the judge sees for one event, from its record UP TO THIS SCAN."""
    ms = a.get("milestones") or []
    lines = []
    for m in ms[:8]:
        if isinstance(m, dict):
            lines.append(f"[{m.get('kind','?')}] {m.get('when') or 'no date'} {m.get('what','')}")
        elif str(m).strip():
            lines.append(str(m))
    exp = a.get("exposure") or []
    ex = [f"{c.get('ticker')}: {c.get('why')}" for c in exp if isinstance(c, dict)]
    notes = a.get("recent_assessments") or []
    return (f"CATALYST: {a.get('catalyst')}\n"
            f"PENDING NEXT: {a.get('pending_next')}\n"
            f"EXIT CONDITION: {a.get('exit_condition')}\n"
            f"VEHICLES: {', '.join(a.get('vehicles') or [])}\n"
            f"VEHICLE THESES:\n  " + ("\n  ".join(ex) or "(none stated)") + "\n"
            f"MILESTONES SO FAR:\n  " + ("\n  ".join(lines) or "(none)") + "\n"
            f"SCANS LIVE SO FAR: {a.get('weeks_alive')}\n"
            f"RECENT NOTES:\n  " + ("\n  ".join(str(x) for x in notes[-3:]) or "(none)"))


def make_ranker(fm: dict, cache_path: Path | None = None):
    """(pick_fn, stats_fn) — the same contract picker.make_picker returns, so nothing downstream
    changes. pick_fn(cand_meta, max_keep, context) -> ordered keep-list of event ids."""
    short = fm.get("picker_model") or "sonnet5"
    workers = int(fm.get("workers", 24) or 24)      # the same width the event agents run at
    effort = str(fm.get("picker_effort", "low")).lower()
    mid, prov = resolve_curator_model(short)
    client = llm.make_client(prov, mid)
    path = cache_path or _CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(path.read_text()) if path.exists() else {}
    calls = [0]

    def _cache_key(a: dict) -> str:
        # KEYED ON THE EVENT'S STATE, not its id: an event whose record has not changed since last
        # scan gets the same score for free, which is most of them on a long run.
        return hashlib.sha256((short + "|" + SYSTEM + "|" + _brief(a)).encode()).hexdigest()[:20]

    def _score_one(a: dict) -> dict:
        brief = _brief(a)
        calls[0] += 1
        try:
            txt = client.complete(SYSTEM, brief, use_web_search=False, label=f"evrank-{a.get('ticker')}",
                                  stage="evrank", json_schema=SCHEMA, effort=effort)
            m = re.search(r"\{.*\}", txt, re.S)
            v = json.loads(m.group(0) if m else txt)
            return v
        except Exception as e:  # noqa: BLE001 -- a ranking failure must never sink a scan
            print(f"  evrank error on {a.get('ticker')} ({type(e).__name__}: {e})", file=sys.stderr)
            return {}

    def pick(cand_meta: list[dict], max_keep: int, context: str = "") -> list[str]:
        # SCORED IN PARALLEL. Sequentially this was ~24 round trips per scan at ~5s each and it
        # DOUBLED curation time -- the 6-month probe ran 24 min for 7 scans, projecting ~124 min for
        # the full run against v31's 67. The model was not the problem: the bake-off puts grok-low
        # at the FASTEST of eight arms (45 min) and second on clean rate, so the latency was all in
        # waiting on one call before starting the next. The curation already runs a 24-worker pool
        # for the event agents; this borrows the same width.
        # CACHE HITS COST NOTHING and stay on this thread -- only genuine calls are dispatched.
        _todo = [a for a in cand_meta if _cache_key(a) not in cache]
        if _todo:
            with cf.ThreadPoolExecutor(max_workers=min(len(_todo), workers)) as _ex:
                for a, v in zip(_todo, _ex.map(_score_one, _todo)):
                    if v:
                        cache[_cache_key(a)] = v
            path.write_text(json.dumps(cache))
        scored = []
        for a in cand_meta:
            v = cache.get(_cache_key(a)) or {}
            # A FAILED SCORE SORTS LAST BUT DOES NOT CRASH, and is visibly distinguishable from a
            # genuine 0 in the log below.
            scored.append((score_of(v) if v else float("-inf"), a["ticker"], v, int(a.get("weeks_alive") or 0)))
        scored.sort(key=lambda x: (-x[0], x[1]))
        # FRESHNESS RESERVE, the same two tiers `_ranked_cull` uses on the watchlist -- and the thing
        # evscore never had. Its velocity term is 0.0 for any event with no prior scan, so it ranked
        # newborns LAST, which is the opposite of non-negotiable #2 and the likely mechanism behind
        # the 52-61% cull-at-birth that got max_events uncapped in the first place.
        # THIS RANKER IS ALREADY FAIRER without it -- catalyst_strength, market_impact and
        # thesis_alignment all read text that exists at admission, and the arc_progress rubric scores
        # a first-scan event 3 ("unproven, not stalled") rather than 0. The reserve is belt as well as
        # braces: a GUARANTEE of slots rather than an instruction the model may not follow.
        # THE VALUES ARE BORROWED from cull_fresh_slots/cull_fresh_scans, which were measured for a
        # SIX-slot watchlist, not a 24-event cap. Proportionally 2-of-6 is a third and 2-of-24 is a
        # twelfth, so this reserve is much weaker here than there. Whether the event cull wants its
        # own pair of knobs is an open question and deliberately not answered by inventing one.
        _fs = int(fm.get("cull_fresh_slots", 2) or 0)
        _fn = int(fm.get("cull_fresh_scans", 2) or 0)
        keep = []
        if _fs > 0 and _fn > 0:
            fresh = [x for x in scored if x[3] < _fn][:_fs]
            keep = [t for _, t, _, _ in fresh]
        keep += [t for _, t, _, _ in scored if t not in set(keep)]
        keep = keep[:max_keep]
        picker_log.log("evrank", {"context": context, "model": short, "max_keep": max_keep,
                                  "scores": {t: {"key": s, **v} for s, t, v, _ in scored},
                                  "fresh_reserved": _fs, "fresh_within": _fn,
                                  "kept": keep,
                                  "culled": [t for _, t, _, _ in scored if t not in set(keep)]})
        # HAND THE SCORES BACK so process_week can stamp them onto this scan's entries. Without this
        # they exist only in decisions.jsonl (off unless --decisions) and the reports -- the thing a
        # reader actually opens -- could not say why an event was kept or dropped. Attached to the
        # function rather than returned, so the (pick_fn, stats_fn) contract is unchanged.
        pick.last_scores = {t: {"key": round(s, 2), **v} for s, t, v, _ in scored}
        return keep

    pick.last_scores = {}
    return pick, lambda: (calls[0], f"{short} ({prov}) evrank")
