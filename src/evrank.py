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

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ranked"],
          "properties": {"ranked": {"type": "array", "items": {"type": "string"}},
                         "why": {"type": "object",
                                 "additionalProperties": {"type": "string"}}}}

SYSTEM = """You rank the market events a news-reading curator is tracking, best first, so the ones
below the line can be ignored this scan.

WHAT THIS BOOK IS BETTING ON. It buys a ticker the financial press has ALREADY NAMED while the story
is still early and under-noticed, and holds until the awaited thing actually happens. The edge is
TIMING -- being on a named opportunity slightly before the crowd -- not size. Two shapes are exactly
what it wants:
  - a specific new development that plainly lifts a named company (a contract, an approval, a deal)
  - a little-known vehicle already moving on a story the wider market has not priced

YOU NEVER SEE PRICES, RETURNS OR POSITION SIZES and must never guess at them. Do NOT rank by how
much money an event might make: that is a forecast, and non-negotiable #1 of this book is that you
never make one. Rank by how EARLY, how UNDER-NOTICED and how SPECIFIC each opportunity is, read off
the event as written.

A NEW EVENT HAS ALMOST NO HISTORY AND THAT IS NOT A MARK AGAINST IT. An event on its first scan may
be a far better opportunity than one grinding on for months with nothing new. Judge the opportunity,
not the paperwork.

BE RUTHLESS. Only a handful of these can be funded at once, and everything below the line is
invisible to the book. A well-formed event that the whole market can already see is worth less here
than a specific early one it cannot.

Return EVERY event id exactly once, best first, and a short reason for the strongest and weakest few.
Output ONLY JSON: {"ranked":["evN","evM",...],"why":{"evN":"<=15 words"}}"""

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

    def _cache_key(ids, briefs) -> str:
        # KEYED ON THE WHOLE SCAN, because the judgement is now one comparison across all live
        # events rather than a score per event. A scan whose events are unchanged is free.
        return hashlib.sha256((short + "|" + SYSTEM + "|" + "\n".join(briefs)).encode()).hexdigest()[:20]

    def pick(cand_meta: list[dict], max_keep: int, context: str = "") -> list[str]:
        # ONE CALL, ALL EVENTS, RANKED. This replaced five sub-scores summed with unfitted weights.
        # WHY: measured over both full curations, the composite separated forward up-escalators at
        # -0.19 to -0.47 on v34 and about -0.06 on v33 -- anti-predictive or chance, never useful --
        # while four of its five terms did not discriminate at all once event lifetime was
        # controlled, and the evidence recorded for arc_progress WAS that confound. One holistic
        # judgement scored +0.17 on v34 and +0.02 on v33: never worse, sometimes better.
        # THE HONEST CLAIM IS COST AND SIMPLICITY, NOT EDGE. The v34 advantage did NOT replicate on
        # v33, so it is not established. What is: $0.05 a run against $0.90 (one call per scan, not
        # one per event), and five metrics, WEIGHTS and SPAN_BONUS deleted -- and complexity is where
        # every bug of the 2026-09-09 debugging session actually lived.
        ids = [str(a["ticker"]) for a in cand_meta]
        briefs = [f"{a['ticker']} | " + _brief(a) for a in cand_meta]
        ck = _cache_key(ids, briefs)
        order = cache.get(ck)
        why = {}
        if not order:
            calls[0] += 1
            try:
                txt = client.complete(SYSTEM, "EVENTS:\n\n" + "\n\n".join(briefs),
                                      use_web_search=False, label=f"evrank-{context}",
                                      stage="evrank", json_schema=SCHEMA, effort=effort)
                m = re.search(r"\{.*\}", txt, re.S)
                v = json.loads(m.group(0) if m else txt)
                order = [x for x in (v.get("ranked") or []) if x in set(ids)]
                why = {k: str(w) for k, w in (v.get("why") or {}).items() if k in set(ids)}
                # A TRUNCATED ORDER IS NOT A RANKING. Anything the model left out is appended in
                # its existing order rather than silently dropped -- an omitted event must not be
                # culled just because the model stopped typing.
                if len(order) < len(ids) * 0.8:
                    print(f"  evrank: model returned {len(order)}/{len(ids)} at {context}; "
                          f"the remainder keep their incoming order", file=sys.stderr)
                order += [t for t in ids if t not in set(order)]
                cache[ck] = order
                path.write_text(json.dumps(cache))
            except Exception as e:  # noqa: BLE001 -- a ranking failure must never sink a scan
                print(f"  evrank error at {context} ({type(e).__name__}: {e}); "
                      f"keeping incoming order", file=sys.stderr)
                order = list(ids)
        rank = {t: i for i, t in enumerate(order)}
        # NO FRESHNESS RESERVE. The prompt tells the judge outright that a new event's thin
        # history is not a demerit, and a second mechanism guaranteeing the same thing is how the
        # five-metric rubric accreted in the first place. It was also never load-bearing: measured
        # over the canonical run, newborn events were culled at 32% against 36% for older ones, so
        # they were not the ones being squeezed. Two profile knobs go with it.
        keep = order[:max_keep]
        # AN ORDER, NOT A SCORE. The ranker produces a ranking; `pct` is that rank expressed as a
        # position within ITS OWN scan, because scans hold different numbers of events and a bare
        # rank of 7 means different things among 12 events and among 40. It is NOT a quality score
        # and must never be read as one -- inventing a number so downstream code keeps working is
        # how a ranking gets mistaken for a measurement.
        n = max(1, len(order))
        _kept = set(keep)
        scores = {t: {"pct": round((n - rank[t]) / n, 3), "rank": rank[t] + 1, "of": n,
                      # WHETHER THIS RANKING ACTUALLY RETIRED IT. The cull is the only use this
                      # order is put to, so the outcome belongs with the decision rather than being
                      # re-derived downstream from a cap the report would have to be handed.
                      "kept": t in _kept,
                      **({"why": why[t]} if t in why else {})} for t in order}
        picker_log.log("evrank", {"context": context, "model": short, "max_keep": max_keep,
                                  "scores": scores, "kept": keep,
                                  "culled": [t for t in order if t not in set(keep)]})
        pick.last_scores = scores
        return keep

    pick.last_scores = {}
    return pick, lambda: (calls[0], f"{short} ({prov}) evrank")
