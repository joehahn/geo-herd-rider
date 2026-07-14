#!/usr/bin/env python3
"""aging_sweep.py — POST-HOC test of the aging-retirement rule (no LLM, cached prices).

On a gem's existing scans, SIMULATE the engine's aging rule (agent.process_week: retire a live event once
its conviction has been <= aging_floor for aging_patience consecutive weeks -> it stops appearing) and
report BOTH effects side by side:
  * concurrent-agent count  (peak + mean live agents/week)  <- does it clear the ~14-19 pileup?
  * returns                 (final $, maxDD)                <- does clearing the pileup hurt the book?

This is the cheap gate before paying for a curator-level A/B: if aging cuts concurrency with flat/positive
returns here, it's worth the paid re-run; if it clips returns (a revived winner got retired), it isn't.

    python scripts/aging_sweep.py MP BWET
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
import pandas as pd  # noqa: E402
from util import load_dotenv; load_dotenv()  # noqa: E402
import firehose  # noqa: E402
import build_dashboard as bd  # noqa: E402
from optimizer import load_financial_model  # noqa: E402

GRID = [(1, 3), (1, 2), (2, 3), (2, 2)]   # (aging_floor, aging_patience)


def load_scans(gem: str) -> dict:
    p = ROOT / "data" / "windows" / f"firehose_scans_{gem.lower()}.json"
    raw = json.loads(p.read_text())
    return {pd.Timestamp(wk + " 16:30", tz="America/New_York"): picks for wk, picks in raw.items()}


def retire(scans: dict, floor: int, patience: int) -> dict:
    """Simulate: an event (thesis) whose conviction is <= floor for `patience` consecutive weeks is retired;
    drop its picks from the week AFTER the trigger on (mirrors the engine setting status='aged')."""
    anchors = sorted(scans)
    streak, retired_at = {}, {}
    for a in anchors:
        for p in scans[a]:
            th = p.get("thesis", "")
            if not th or th in retired_at:
                continue
            c = int(p.get("conviction", 5) or 5)
            if c <= floor:
                streak[th] = streak.get(th, 0) + 1
                if streak[th] >= patience:
                    retired_at[th] = a
            else:
                streak[th] = 0
    return {a: [p for p in scans[a]
                if not (retired_at.get(p.get("thesis", "")) is not None and a > retired_at[p.get("thesis", "")])]
            for a in anchors}


def concurrency(scans: dict) -> tuple[int, float]:
    per = [len({p.get("thesis", "") for p in scans[a] if p.get("thesis") and p.get("thesis_live", True)})
           for a in sorted(scans)]
    return (max(per) if per else 0), (sum(per) / len(per) if per else 0.0)


def run(gem: str):
    scans0 = load_scans(gem)
    cfg = bd.gem_config(gem)
    fm = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    cap = float(fm.get("initial_investment_usd", 50_000))

    def final(scans):
        bt = firehose.backtest(scans, fm, cap, daily=True, overlay=gem, overlay_anchor=cfg["trigger"])
        d = bt["daily"]
        v = d["value"]
        mdd = min((x - m) / m for x, m in zip(v, [max(v[:i + 1]) for i in range(len(v))]))
        return v[-1], mdd

    pk0, mn0 = concurrency(scans0)
    v0, dd0 = final(scans0)
    print(f"\n==== {gem}: aging-retirement post-hoc (baseline: peak {pk0} / mean {mn0:.1f} agents, "
          f"${v0:,.0f}, maxDD {100*dd0:.1f}%) ====")
    print(f"{'floor':>5} {'patience':>8} {'peak':>5} {'mean':>5} {'final $':>10} {'vs base':>9} {'maxDD':>7}")
    for floor, pat in GRID:
        r = retire(scans0, floor, pat)
        pk, mn = concurrency(r)
        v, dd = final(r)
        print(f"{floor:>5} {pat:>8} {pk:>5} {mn:>5.1f} ${v:>8,.0f} {100*(v-v0)/v0:>+7.1f}% {100*dd:>6.1f}%")


if __name__ == "__main__":
    for g in (sys.argv[1:] or ["MP", "BWET"]):
        run(g.upper())
