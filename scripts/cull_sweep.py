#!/usr/bin/env python3
"""cull_sweep.py — measure a conviction-floor cull WITHOUT re-running the curator (no LLM).

The cull is deterministic post-processing on an existing scan log's conviction history: an event
(thesis) is RETIRED once its conviction sits at/below `conviction_floor` for `cull_patience_weeks`
consecutive weeks; its agent stops running (and its picks stop) from the next week on. We then re-run
firehose.backtest() (cached prices, no LLM) on the culled scans to read the gains impact.

This lets us sweep (floor, patience) instantly to see the cost/returns tradeoff before wiring the
engine. KEY: while floor < spy_agent_conviction the culled events are unfunded, so gains should be
flat and only the AGENT-WEEK count (the cost proxy) drops; a floor >= spy starts culling funded names.

    python scripts/cull_sweep.py MP HL
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import pandas as pd  # noqa: E402
from util import load_dotenv  # noqa: E402
load_dotenv()
import firehose  # noqa: E402
import build_dashboard as bd  # noqa: E402
from optimizer import load_financial_model  # noqa: E402

FLOORS = [0, 1, 2, 3, 4, 5, 6]      # 0 = OFF (baseline)
PATIENCE = [1, 2, 3]


def load_scans(path: Path) -> dict:
    raw = json.loads(path.read_text())
    return {pd.Timestamp(str(wk) + " 16:30", tz="America/New_York"): picks for wk, picks in raw.items()}


def cull(scans: dict, floor: int, patience: int) -> dict:
    """Retire an event once conviction <= floor for `patience` consecutive weeks; drop its picks after."""
    if floor <= 0:
        return scans
    anchors = sorted(scans)
    streak: dict = {}
    culled_from: dict = {}                     # thesis -> anchor it was retired ON (dropped strictly after)
    for a in anchors:
        for p in scans[a]:
            th = p.get("thesis", "")
            if not th or th in culled_from:
                continue
            c = p.get("conviction", 5) or 5
            if c <= floor:
                streak[th] = streak.get(th, 0) + 1
                if streak[th] >= patience:
                    culled_from[th] = a
            else:
                streak[th] = 0
    out = {}
    for a in anchors:
        out[a] = [p for p in scans[a]
                  if not (culled_from.get(p.get("thesis", "")) is not None
                          and a > culled_from[p.get("thesis", "")])]
    return out


def agent_weeks(scans: dict) -> int:
    """Cost proxy: total (event, week) agent invocations = sum of distinct live theses per week."""
    return sum(len({p.get("thesis", "") for p in ps if p.get("thesis", "")}) for ps in scans.values())


def run(ticker: str):
    cfg = bd.gem_config(ticker)
    scans0 = load_scans(cfg["scans"])
    fm = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    cap = float(fm.get("initial_investment_usd", 50_000))
    spy = int(fm.get("spy_agent_conviction", 5))
    base_aw = agent_weeks(scans0)

    def final(scans):
        bt = firehose.backtest(scans, fm, cap, daily=True, overlay=ticker, overlay_anchor=cfg["trigger"])
        d = bt["daily"]
        return (d["value"][-1], min((v - m) / m for v, m in zip(d["value"], _cummax(d["value"]))))

    print(f"\n==== {ticker}: baseline agent-weeks={base_aw}, capital=${cap:,.0f}, spy_agent_conviction={spy} ====")
    print(f"{'floor':>5} {'patience':>8} {'agent-weeks':>12} {'cost cut':>9} {'final $':>10} {'vs base':>9} {'maxDD':>7}")
    base_val, _ = final(scans0)
    for floor in FLOORS:
        for pat in (PATIENCE if floor > 0 else [1]):
            culled = cull(scans0, floor, pat)
            aw = agent_weeks(culled)
            val, dd = final(culled)
            tag = "  <-- baseline" if floor == 0 else ("  culls FUNDED (floor>=spy)" if floor >= spy else "")
            print(f"{floor:>5} {pat:>8} {aw:>12} {100*(base_aw-aw)//base_aw:>7}% ${val:>8,.0f} "
                  f"{100*(val-base_val)/base_val:>+7.1f}% {100*dd:>6.1f}%{tag}")


def _cummax(xs):
    out, m = [], xs[0]
    for x in xs:
        m = max(m, x); out.append(m)
    return out


if __name__ == "__main__":
    for t in (sys.argv[1:] or ["MP", "HL"]):
        run(t.upper())
