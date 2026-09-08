#!/usr/bin/env python3
"""score_event_quality.py — a PROCESS score for each curated event, judged after the fact.

WHY. The scorer the event-cull actually uses (evscore) cannot tell this book's best event from its
worst: on cbt_3yr_v31_exposure, ev207 (falsifiable exit that fired on its own terms, six dated
milestones, five distinct vehicle theses, zero flags) and ev214 ("Cameco HOPES to repeat its 2018
success", twelve scans of "no tariff decision", non-events logged as `happened` milestones, four
duplicate clauses) BOTH score 17.2. Its dominant term, velocity at weight 4.0, was +0% for both, so
the score collapsed onto three log-saturated counts of how much generic press exists around a
keyword.

WHAT THIS SCORES, and what it deliberately does not. Four PROCESS judgements plus one duration
estimate, with NO prices, NO returns and NO P&L anywhere in the prompt -- the same discipline as the
Fable-5 audit that chose the event-agent model (4,527 calls judged on catalyst datable / write-up
within its sources / exit call coherent, with no prices in front of the judge). It never asks
"will this make money", which is the question measured ~random three times here (conviction,
a cheap picker, and the sonnet5 event-picker at -4.2% against a -4.4% null).

VALIDATION IS THE POINT. Unlike evscore, every component can be checked against outcomes already on
disk: did the event exit on the AGENT'S judgement or on a counter, and did it actually span the
month-plus this book seems to need? A score that cannot separate those is no better than the one it
would replace, and that is the bar it has to clear before anything is re-curated.

    python scripts/score_event_quality.py --run data/cbt_3yr_v31_exposure --limit 40
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import llm  # noqa: E402
from util import load_dotenv  # noqa: E402
from agent import as_milestone, as_set  # noqa: E402

SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["catalyst_strength", "thesis_alignment", "exit_quality",
                 "market_impact", "expected_span_months", "why"],
    "properties": {"catalyst_strength": {"type": "integer"},
                   "thesis_alignment": {"type": "integer"},
                   "exit_quality": {"type": "integer"},
                   "market_impact": {"type": "integer"},
                   "expected_span_months": {"type": "integer"},
                   "why": {"type": "string"}}}

SYSTEM = """You audit the RECORD of a market event a curator tracked. You never see prices, returns
or position sizes, and you must never guess at them: you are judging how the event was WRITTEN and
whether it was a coherent thing to track, not whether it made money.

Score each 0-5, where 0 is absent or incoherent and 5 is exemplary.

catalyst_strength — is the catalyst ONE SPECIFIC OCCURRENCE, logically stated, with a subject, a
  status and (ideally) a date? "US DOE loans $1B to restart Three Mile Island, November 19 2025" is
  5. "Cameco hopes to repeat its 2018 success in fending off tariffs, no date announced" is 1: a
  company's hope is not an occurrence and nothing was initiated. A standing condition that can
  plausibly END (a chokepoint closed, output cuts in force) is legitimate and scores 3-4; a
  permanent condition or a theme scores 0-1.

thesis_alignment — does EVERY vehicle have its own reason to be attached, and does that reason
  actually follow from THIS catalyst? Five tickers sharing one sentence is 1, however true the
  sentence. A clause that merely restates the catalyst backwards ("cuts lift crude" ->
  "benefits from higher oil prices") is 1-2. Distinct, specific mechanisms are 4-5.

exit_quality — is the exit condition OBSERVABLE and does it name both directions? "exit if/when the
  DOE decision is granted or refused" is 5. "exit if/when the 120% upside is realized or fails to
  materialize" is 0: nothing can settle it. An exit that merely restates the catalyst is 2.

market_impact — how much of the market could this occurrence move, judged from the occurrence
  itself: a chokepoint carrying a fifth of seaborne oil is 5; one small company's product launch is
  1. This is about REACH, not about direction or size of any price move.

expected_span_months — from the catalyst and the exit condition ONLY, how many months would you
  expect between this event opening and its exit condition being settled? An integer. Say 1 if it
  should settle within a month, 12 if it is open-ended.

why — one sentence, under 25 words, naming the weakest of the five."""


def _brief(e: dict) -> str:
    ents = e.get("entries") or []
    exp = [f"{c.get('ticker')}: {c.get('why')}" for c in (e.get("exposure") or []) if isinstance(c, dict)]
    ms, seen = [], set()
    for x in ents:
        for m in (x.get("milestones") or []):
            d = as_milestone(m)
            if d["what"] and d["what"].lower() not in seen:
                seen.add(d["what"].lower())
                ms.append(f"[{d['kind']}] {d['when'] or 'no date'} {d['what']}")
    last = ents[-1] if ents else {}
    return (f"CATALYST: {e.get('catalyst')}\n"
            f"PENDING NEXT: {e.get('pending_next')}\n"
            f"EXIT CONDITION: {last.get('exit_advice')}\n"
            f"VEHICLES: {', '.join(sorted(as_set(e.get('vehicles'))))}\n"
            f"VEHICLE THESES:\n  " + ("\n  ".join(exp) or "(none stated)") + "\n"
            f"MILESTONES:\n  " + ("\n  ".join(ms[:8]) or "(none)") + "\n"
            f"SCANS TRACKED: {len(ents)}\n"
            f"LAST ASSESSMENT: {last.get('assessment')}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="data/cbt_3yr_v31_exposure")
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N (a cheap pilot)")
    ap.add_argument("--only", default="", help="comma-separated event ids")
    ap.add_argument("--model", default="x-ai/grok-4.3")   # the resolved id; "grok4" is a profile alias
    a = ap.parse_args()
    load_dotenv()          # the key lives in .env, as every other entry point assumes
    run = ROOT / a.run
    ev = json.loads((run / "journal.json").read_text())["events"]
    keys = [k for k in ev if (ev[k].get("entries") or [])]
    if a.only:
        keys = [k for k in a.only.split(",") if k.strip() in ev]
    elif a.limit:
        keys = keys[:a.limit]
    cli = llm.make_client("openrouter", a.model)
    out_path = Path(a.out or (run / "event_quality.json"))
    out = json.loads(out_path.read_text()) if out_path.exists() else {}
    for i, k in enumerate(keys, 1):
        if k in out:
            continue
        try:
            txt = cli.complete(SYSTEM, _brief(ev[k]), use_web_search=False, stage="audit",
                               label=f"quality-{k}", json_schema=SCHEMA)
            out[k] = json.loads(txt) if isinstance(txt, str) else txt
        except Exception as exc:  # noqa: BLE001
            print(f"    {k}: {type(exc).__name__}: {exc}", file=sys.stderr)
        if i % 20 == 0:
            out_path.write_text(json.dumps(out, indent=1))
            print(f"    {i}/{len(keys)}", flush=True)
    out_path.write_text(json.dumps(out, indent=1))
    print(f"  wrote {out_path}: {len(out)} events scored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
