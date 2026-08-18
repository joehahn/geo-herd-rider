#!/usr/bin/env python3
"""judge_bakeoff.py — grade the event-agent bake-off arms on DECISION QUALITY, not P&L.

WHY A JUDGE AND NOT THE SCOREBOARD. Comparing arms on portfolio value cannot work here and that is
measured, not assumed: two curations at IDENTICAL settings differ 1.86x in median final value, and
across the 6,300-cell optimizer grid one of them "wins" 77-84% of cells purely on LLM sampling. So a
per-arm P&L number, or even a per-arm win-rate over the whole grid, sits inside the noise. A decision
judge escapes that by changing the unit of analysis: 2,849 individually-judgeable calls instead of
five aggregate numbers.

WHAT IT SCORES, AND WHAT IT IS FORBIDDEN TO SCORE. Process only:
  dated        was the cited catalyst specific, dated and resolvable -- not an open-ended trend?
  supported    does the assessment follow from the evidence the agent ITSELF cites, or assert past it?
  consistent   is thesis_live / exit_case coherent with catalyst_resolved and the stated exit_advice?
The judge never sees a price, a return, or an outcome, and is never asked whether the pick "was good".
That is deliberate: a judge scoring expected return would be an LLM forecasting magnitude, which is
CLAUDE.md non-negotiable #1. It would also re-introduce exactly the look-ahead the backtest fights.

BLIND. The packet never names the arm or model. The judge cannot reward a family it recognises.

TWO TIERS, because a cheap model is fine at triage and untrustworthy at the verdict that matters:
  tier 1  cheap screen over every decision (~$1)
  tier 2  Fable-5 re-reads only what tier 1 called flawed -- false accusations are the expensive
          error and they are rare, so the strong model is spent exactly where accuracy decides the
          result. Reported per arm as a FLAW RATE with a tier-2-confirmed subset.

    python scripts/judge_bakeoff.py --tier 1
    python scripts/judge_bakeoff.py --tier 2
"""
from __future__ import annotations
import argparse, json, random, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from util import load_dotenv          # noqa: E402
import llm                            # noqa: E402
from optimizer import resolve_curator_model  # noqa: E402

ARMS = {"deepseek4": "bakeoff_deepseek4", "minimax": "bakeoff_minimax", "luna": "bakeoff_luna",
        "kimi-high": "bakeoff_kimi_high", "kimi-low": "bakeoff_kimi_low"}
OUT = ROOT / "data/judge_bakeoff.json"

SYS = """You audit the PROCESS of an analyst's written call on a live news-driven thesis. You are NOT
asked whether the trade made money, and you are given no prices or outcomes — judging on outcome would
be hindsight, and is out of scope.

You see: the thesis's catalyst, the milestones and news claims the analyst cited, the sources, the
analyst's own exit condition, and this period's written assessment plus its live/exit decision.

Score three things, each true or false:
  dated       — the CATALYST is a specific, datable, resolvable event (a contract award, a ruling, an
                FDA decision, a signed act, a chokepoint closing). An open-ended trend ("AI demand
                grows", "rates may fall") is NOT dated, however plausible.
  supported   — the assessment follows from the evidence actually cited. If it asserts facts or a
                degree of certainty the cited claims/sources do not carry, this is false.
  consistent  — the live/exit decision coheres with catalyst_resolved and with the analyst's OWN
                stated exit condition. Calling a thesis live while its own exit condition has
                plainly triggered is inconsistent.

Output JSON only: {"dated":bool,"supported":bool,"consistent":bool,"why":"<=25 words"}"""

SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"dated": {"type": "boolean"}, "supported": {"type": "boolean"},
                         "consistent": {"type": "boolean"}, "why": {"type": "string"}},
          "required": ["dated", "supported", "consistent", "why"]}


def packets() -> list[dict]:
    """One judgeable packet per event-agent decision, arm label kept OUT of the text."""
    out = []
    for arm, d in ARMS.items():
        ev = json.loads((ROOT / "data" / d / "journal.json").read_text()).get("events") or {}
        for eid, e in ev.items():
            for i, en in enumerate(e.get("entries") or []):
                out.append({"arm": arm, "eid": eid, "i": i, "date": en.get("date"),
                            "text": json.dumps({
                                "catalyst": e.get("catalyst"),
                                "period": en.get("date"),
                                "periods_held": i + 1,
                                "milestones": (en.get("milestones") or [])[:6],
                                "news_claims": str(en.get("news_claims") or "")[:400],
                                "sources": (en.get("sources") or [])[:4],
                                "analyst_exit_condition": str(en.get("exit_advice") or "")[:300],
                                "catalyst_resolved": en.get("catalyst_resolved"),
                                "assessment": str(en.get("assessment") or "")[:600],
                                "DECISION_thesis_live": en.get("thesis_live"),
                                "DECISION_exit_case": en.get("exit_case")}, indent=1)})
    return out


def run(pk: list[dict], model_short: str, tag: str, workers: int = 12) -> list[dict]:
    mid, prov = resolve_curator_model(model_short)
    cli = llm.make_client(prov, mid)

    def _parse(txt: str) -> dict:
        """json.loads, then the first {...} block. The schema is REQUESTED but not always honoured:
        the pilot lost 7 of 40 decisions (17.5%) to bare parse errors -- prose before the object, or a
        stray leading token. Dropping those is not neutral, it silently biases the flaw rate toward
        whichever arm happens to elicit cleaner formatting, which is not what is being measured."""
        try:
            return json.loads(txt)
        except Exception:                                        # noqa: BLE001
            i, j = txt.find("{"), txt.rfind("}")
            if i >= 0 and j > i:
                return json.loads(txt[i:j + 1])
            raise

    def one(p):
        last = ""
        for attempt in range(3):                                 # transient + format retries
            try:
                txt = cli.complete(SYS, p["text"], use_web_search=False, stage="agent",
                                   label=f"judge{tag}-{p['arm']}-{p['eid']}-{p['i']}",
                                   json_schema=SCHEMA, effort="low" if tag == "1" else "high")
                v = _parse(txt)
                if all(isinstance(v.get(k), bool) for k in ("dated", "supported", "consistent")):
                    return {**{k: p[k] for k in ("arm", "eid", "i", "date")},
                            **{k: v.get(k) for k in ("dated", "supported", "consistent", "why")}}
                last = "non-boolean verdict"
            except Exception as e:                               # noqa: BLE001
                last = str(e)[:80]
        return {**{k: p[k] for k in ("arm", "eid", "i", "date")}, "error": last}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, pk))


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="0 = all (sampled evenly across arms)")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit2", type=int, default=0, help="tier 2: stratified sample size (0 = all flagged)")
    a = ap.parse_args(argv)
    pk = packets()
    print(f"  {len(pk):,} decisions across {len(ARMS)} arms", flush=True)
    if a.limit:
        random.seed(11)
        pk = random.sample(pk, min(a.limit, len(pk)))
        print(f"  sampled {len(pk)}", flush=True)
    if a.tier == 1:
        res = run(pk, "deepseek4", "1", a.workers)
        OUT.write_text(json.dumps({"tier1": res}, indent=1))
        ok = [r for r in res if "error" not in r]
        print(f"  tier 1: {len(ok):,} judged, {len(res)-len(ok)} failed -> {OUT}")
    else:
        d = json.loads(OUT.read_text())
        flagged = [r for r in d["tier1"]
                   if "error" not in r and not (r["dated"] and r["supported"] and r["consistent"])]
        byk = {(r["arm"], r["eid"], r["i"]): r for r in flagged}
        sel = [p for p in pk if (p["arm"], p["eid"], p["i"]) in byk]
        if a.limit2:            # STRATIFIED: equal n per arm, so a big arm cannot dominate the verdict
            random.seed(23); per = a.limit2 // len(ARMS); out = []
            for arm in ARMS:
                s2 = [p for p in sel if p["arm"] == arm]; out += random.sample(s2, min(per, len(s2)))
            sel = out
        print(f"  tier 2: Fable-5 re-reads {len(sel):,} tier-1-flagged decisions", flush=True)
        res = run(sel, "fable", "2", max(4, a.workers // 2))
        d["tier2"] = res
        OUT.write_text(json.dumps(d, indent=1))
        ok = [r for r in res if "error" not in r]
        print(f"  tier 2: {len(ok):,} judged, {len(res)-len(ok)} failed -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
