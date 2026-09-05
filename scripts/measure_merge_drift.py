#!/usr/bin/env python3
"""measure_merge_drift.py — when a candidate joins an existing event, is it the SAME catalyst?

THE DRIFT. ev192 entered on 2026-01-27 as "Corvex announces long-term GPU lease for AI development"
with vehicles MOVE and NVDA -- MOVE being the microcap that spiked 115% on that announcement. At the
next scan it held NVDA, AVGO and MRVL and had dropped MOVE, then spent seven scans reporting "No
Corvex lease news" while booking +$27,339 from MRVL riding the AI tape. The catalyst text never
changed; the event did.

THE ONLY PLACE THE EVIDENCE SURVIVES. `firehose_scans.csv`'s `thesis` column is the EVENT's catalyst,
written after the match, so every merge looks identical there by construction (80 of 87 additions,
which is what a post-match record looks like, not what agreement looks like). The scout's OWN wording
for a candidate exists only in `decisions.jsonl`, which is why this script needs `--decisions` runs.

WHAT IT MEASURES. For every ticker that becomes a NEW vehicle of an existing event, the similarity
between the scout's proposed thesis and that event's catalyst. On cbt_3yr_v25_vehgate, 62 of 64
recoverable merges join a candidate whose thesis DIFFERS from the catalyst it is merged into -- so
merging across theses was the norm rather than the exception, which is the PRE-2026-09-05
MATCH_SYSTEM working exactly as written ("DEFAULT TO MERGING ... When unsure, MERGE"). That prompt
has been rewritten around one occurrence per event, with the tie-break flipped to "new"; re-run this
against the next curation to see what it did.

WHY THERE IS NO THRESHOLD HERE, and why the script prints the list rather than a verdict: similarity
does NOT separate the good merges from the bad. "SEC approves a Bitcoin ETF" <- MARA "Bitcoin ETF
approval anticipation" is the same occurrence in different words and scores 0.37; ev192's "Corvex
announces long-term GPU lease" <- AVGO "Broadcom dominates custom AI chip market" is a different
occurrence entirely and scores 0.32. Any cut-off would take the wrong ones. The judgement is
semantic, which is why the lever is the matcher's INSTRUCTION and not a gate bolted in front of it.

Read the tail of the list before and after a MATCH_SYSTEM change: the target is fewer merges of the
"ev5 'US ramps up chip subsidy' <- INTC 'German budget passes'" kind, without losing the
"'SEC approves a Bitcoin ETF' <- 'Bitcoin ETF approval anticipation'" kind.

    scripts/measure_merge_drift.py [run_dir]
"""
from __future__ import annotations

import collections
import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import agent          # noqa: E402  _norm_catalyst, so this and _consolidate_events agree


def main() -> int:
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "cbt_3yr_v25_vehgate"
    dec = run / "decisions.jsonl"
    if not dec.exists():
        print(f"{run}/decisions.jsonl not found — the run must be curated with --decisions")
        return 1
    ev = json.loads((run / "journal.json").read_text())["events"]
    prop: dict = {}
    for ln in dec.read_text().splitlines():
        r = json.loads(ln)
        if r.get("kind") == "scout":
            for p in r.get("proposed", []):
                prop[(r.get("context"), str(p.get("ticker", "")).strip().upper())] = \
                    str(p.get("thesis") or "")
    rows, absorbed = [], collections.Counter()
    for k, e in ev.items():
        ents = e.get("entries") or []
        prev: set = set()
        for i, x in enumerate(ents):
            cur = {str(v).upper() for v in (x.get("vehicles") or [])}
            d = str(x.get("date", ""))[:10]
            for t in (cur - prev if i else set()):
                absorbed[k] += 1
                th = prop.get((d, t))
                if th:
                    rows.append((difflib.SequenceMatcher(
                        None, agent._norm_catalyst(e["catalyst"]),
                        agent._norm_catalyst(th)).ratio(), k, d, e["catalyst"], t, th))
            prev |= cur
    rows.sort()
    band = collections.Counter("same wording" if s > 0.95 else
                               ("close" if s >= 0.6 else "DIFFERENT") for s, *_ in rows)
    print(f"  {run.name}: {sum(absorbed.values())} vehicle additions after entry, "
          f"{len(rows)} with the scout's own thesis recoverable")
    for b, n in band.most_common():
        print(f"     {n:>3}  {b}")
    print("\n  biggest absorbers")
    for k, n in absorbed.most_common(5):
        print(f"     +{n:<3} {k:>6}  '{ev[k]['catalyst'][:60]}'")
    print("\n  merged into an event about something else, least similar first")
    for s, k, d, c, t, th in rows[:25]:
        print(f"     {s:.2f} {k:>6} @ {d}  '{c[:38]}'  <-  {t}: '{th[:44]}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
