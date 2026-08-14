"""evscore.py — rank LIVE EVENTS on what the PRESS is doing, never on a forecast.

WHY NOT AN LLM RANKER. Three attempts in this project have produced nothing: conviction scores came
out ~random, a cheap picker landed BELOW random, and (2026-08-13, same cap / corpus / filter, only the
ranker varying) the sonnet5 event-picker scored -4.2% mean against its mechanical null's -4.4% with an
identical top decile. Zero lift, three times. So the RANKING is arithmetic here, and the cheap LLM is
kept for the two jobs it is actually better at (see `llm_scores` below).

Arithmetic also buys two things an LLM ranking cannot:
  REPRODUCIBLE  the whole free tuning loop (6,300 sweep cells at $0) depends on a fixed curation. A
                per-scan model score makes the book non-reproducible -- picker.py already had cumulative
                P&L removed in July for exactly that reason.
  AUDITABLE     "culled: 2 non-syndicated sources vs 9" is checkable. "the model ranked it 7th" is not,
                which is precisely why the picker could never be debugged.

NONE OF THESE IS A FORECAST (CLAUDE.md #1). Every metric is an OBSERVATION about this scan's coverage:
how many independent desks are naming it, how loudly, whether that is rising or falling. The thesis is
"get to where the smarter part of the herd is already heading, AS PUBLISHED" -- these measure the
publishing, not the outcome.
"""
from __future__ import annotations

import re

# Reuse the discovery gate's vocabulary so "what gets in" and "what stays in" are judged on one standard.
from agent import SUPERLATIVE, _filter_event  # noqa: F401


def _syndication_key(a: dict) -> str:
    """Collapse a wire story to ONE source.

    Five mastheads carrying one AP piece is ONE desk noticing, not five. Without this, `source_breadth`
    would reward syndication -- the loudest possible signal of the herd having ALREADY arrived, i.e. the
    exact opposite of what this strategy wants to buy."""
    syn = a.get("syndicated_sources")
    if isinstance(syn, str) and syn.startswith("["):
        try:
            import ast
            syn = ast.literal_eval(syn)
        except Exception:  # noqa: BLE001
            syn = None
    if isinstance(syn, list) and syn:
        return sorted(syn)[0]                 # every copy of one story maps to the same key
    return (a.get("source") or "?").lower()


def event_metrics(event: dict, arts: list[dict], prev: dict | None = None) -> dict:
    """The four FREE metrics for one live event, from this scan's matching articles.

    `prev` is this event's metrics from the previous scan, for velocity. Returns raw counts -- the
    weighting happens in `score`, so the inputs stay inspectable on their own."""
    hits = _filter_event(arts, event, cap=0)          # cap=0 -> every match, not the agent's 20-slot slice
    srcs = {_syndication_key(a) for a in hits}
    authors = {(a.get("author") or "").strip().lower() for a in hits if (a.get("author") or "").strip()}
    sup = sum(1 for a in hits if SUPERLATIVE.search(a.get("title") or ""))
    n = len(hits)
    prev_n = (prev or {}).get("mentions", 0)
    return {"mentions": n,
            "source_breadth": len(srcs),
            "author_breadth": len(authors),
            "superlatives": sup,
            # velocity = this scan's coverage against last scan's. An OBSERVATION of whether the press is
            # picking the story up or dropping it -- not a claim about where the price goes.
            "velocity": (n - prev_n) / max(prev_n, 1) if prev is not None else 0.0}


# Fixed weights, NOT fitted. Fitting them to this backtest's outcomes is precisely the overfitting
# CLAUDE.md #6 warns about, and today's sweep already showed knobs flipping sign between curations.
# Source breadth leads because independent pickup is the least gameable of the four: superlatives are
# one desk's house style, and mention count is inflated by bot mills.
# Breadth SATURATES, and velocity carries the weight. Raw breadth rewards the most CROWDED story --
# measured on the merged corpus, a DRAM event scored 351 sources against a uranium event's 189, so a
# level-based score would systematically prefer the LATE rung. That inverts non-negotiable #2: the bet
# is a name the press has started naming while it is still under-owned. So breadth enters as log1p
# (a handful of independent desks is validation that it is not one crank; forty is the herd arriving,
# and the 40th adds almost nothing), while VELOCITY -- the story being picked up right now -- leads.
WEIGHTS = {"source_breadth": 2.0, "superlatives": 1.5, "velocity": 4.0, "author_breadth": 0.5}


def score(m: dict, llm: dict | None = None) -> float:
    """Combine the metrics into one rank key. Higher = more worth a slot."""
    import math
    s = (WEIGHTS["source_breadth"] * math.log1p(m["source_breadth"])
         + WEIGHTS["superlatives"] * math.log1p(m["superlatives"])
         + WEIGHTS["velocity"] * max(-1.0, min(3.0, m["velocity"]))    # clamp: a 0->1 jump is not 100x
         + WEIGHTS["author_breadth"] * math.log1p(m["author_breadth"]))
    if llm:
        # driver_strength 0-3, and an ETA bonus: a catalyst due in DAYS is worth a slot over one due in
        # months, because the slot frees up sooner. Both are read out of the press, not predicted.
        s += 2.0 * float(llm.get("driver_strength", 0) or 0)
        s += {"days": 3.0, "weeks": 1.5, "months": 0.0, "": 0.0}.get(str(llm.get("eta", "")).lower(), 0.0)
    return s


def rank(events: list[dict], arts: list[dict], prev: dict | None = None,
         llm: dict | None = None) -> list[tuple[str, float, dict]]:
    """[(event_id, score, metrics)] best first. `prev`/`llm` are keyed by event id."""
    out = []
    for ev in events:
        m = event_metrics(ev, arts, (prev or {}).get(ev["id"]))
        out.append((ev["id"], score(m, (llm or {}).get(ev["id"])), m))
    out.sort(key=lambda x: -x[1])
    return out
