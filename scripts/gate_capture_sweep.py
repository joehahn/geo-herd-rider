#!/usr/bin/env python3
"""gate_capture_sweep.py — sweep the momentum gate and measure BOTH return and RISE-CAPTURE (no LLM).

For each gem, on its era whole-book (aging applied, RVOL from the profile), vary momentum_gate_pct and
report final $ AND rise-capture = portfolio final return / the gem's PEAK move. A LOWER gate enters
earlier -> should capture MORE of the rise. Answers "did the gate make us miss too much of the run?".

    python scripts/gate_capture_sweep.py MP HL BWET TSM NEM MU
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
import backtest_retrieval_curator as brc  # noqa: E402
import build_dashboard as bd  # noqa: E402
from optimizer import load_financial_model  # noqa: E402

GATES = [0.0, 0.10, 0.15, 0.20, 0.25]
FULL = json.loads((ROOT / "data" / "windows" / "firehose_scans_full.json").read_text())
fm0 = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
CAP = float(fm0.get("initial_investment_usd", 50_000))
AF, AP = int(fm0.get("aging_floor", 1)), int(fm0.get("aging_patience", 3))


def era_slice_aged(gem: str) -> dict:
    s, e = brc.GEM_ERA[gem]
    first_seen = {}
    for wk in sorted(FULL):
        for p in FULL[wk]:
            th = p.get("thesis", "")
            if th:
                first_seen.setdefault(th, wk)
    sub = {wk: [p for p in FULL[wk] if s <= first_seen.get(p.get("thesis", ""), wk) <= e]   # whole-book, era-launched
           for wk in sorted(FULL) if s <= wk <= e}
    streak, ret = {}, {}                                     # apply aging (floor=1, patience=3)
    for wk in sorted(sub):
        for p in sub[wk]:
            th = p.get("thesis", "")
            if not th or th in ret:
                continue
            c = int(p.get("conviction", 5) or 5)
            if c <= AF:
                streak[th] = streak.get(th, 0) + 1
                if streak[th] >= AP:
                    ret[th] = wk
            else:
                streak[th] = 0
    aged = {wk: [p for p in sub[wk] if not (ret.get(p.get("thesis", "")) is not None and wk > ret[p.get("thesis", "")])]
            for wk in sorted(sub)}
    return {pd.Timestamp(wk + " 16:30", tz="America/New_York"): picks for wk, picks in aged.items()}


for gem in (sys.argv[1:] or ["MP", "HL", "BWET", "TSM", "NEM", "MU"]):
    scans = era_slice_aged(gem)
    cfg = bd.gem_config(gem)
    print(f"\n==== {gem}: momentum-gate vs rise-capture (RVOL {fm0.get('rvol_gate')}, aging on) ====")
    print(f"{'gate':>6} {'final $':>10} {'port ret':>9} {'gem peak':>9} {'CAPTURE':>8}")
    for g in GATES:
        fm = {**fm0, "momentum_gate_pct": g}
        bt = firehose.backtest(scans, fm, CAP, daily=True, overlay=gem, overlay_anchor=cfg["trigger"])
        d = bt["daily"]
        pr = d["value"][-1] / CAP - 1
        ov = [x for x in (d.get("overlay") or []) if x is not None]
        gpeak = (max(ov) / CAP - 1) if ov else None
        cap_pct = f"{100 * pr / gpeak:.0f}%" if (gpeak and gpeak > 0) else "n/a"
        gp = f"{100 * gpeak:+.0f}%" if gpeak is not None else "n/a"
        print(f"{('none' if g == 0 else f'{int(g*100)}%'):>6} ${d['value'][-1]:>8,.0f} {100*pr:>+7.0f}% {gp:>9} {cap_pct:>8}")
