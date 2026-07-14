#!/usr/bin/env python3
"""confirmation_sweep.py — sweep the breakout co-confirmations on top of the momentum gate (no LLM).

On the existing per-gem curator scans, with the +20%/1mo momentum gate ON, test adding:
  #1 RVOL volume confirmation (recent vol >= X x 20-day avg) — reject thin/fake breakouts, and
  #2 trailing-N-day-low exit (Turtle "cut losers, let winners run").
Deterministic + look-ahead-clean + free (cached prices/volume). Re-runs firehose.backtest per config.

    python scripts/confirmation_sweep.py MP HL BWET
"""
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
import pandas as pd  # noqa: E402
from util import load_dotenv; load_dotenv()  # noqa: E402
import firehose, score  # noqa: E402
import build_dashboard as bd  # noqa: E402
from optimizer import load_financial_model  # noqa: E402

SCANS = {"MP": "firehose_scans_mp.json", "HL": "firehose_scans_hl.json", "BWET": "firehose_scans_bwet.json"}
CONFIGS = [   # label -> knob overrides (momentum gate stays at profile 0.20)
    ("mom +20% only",          {}),
    ("+ RVOL 1.5",             {"rvol_gate": 1.5}),
    ("+ trailing 20d-low",     {"trailing_low_days": 20}),
    ("+ trailing 10d-low",     {"trailing_low_days": 10}),
    ("+ RVOL 1.5 + 20d-low",   {"rvol_gate": 1.5, "trailing_low_days": 20}),
]


def load_scans(name):
    raw = json.loads((ROOT / "data" / "windows" / name).read_text())
    return {pd.Timestamp(wk + " 16:30", tz="America/New_York"): p for wk, p in raw.items()}


def run(gem):
    scans = load_scans(SCANS[gem])
    cfg = bd.gem_config(gem)
    fm0 = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    cap = float(fm0.get("initial_investment_usd", 50_000))
    anchors = sorted(scans)
    tickers = sorted({p["ticker"] for ps in scans.values() for p in ps if p.get("ticker")} | {score.BENCHMARK, gem, "GLD"})
    start = (anchors[0] - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    end = (anchors[-1] + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
    panel = score.fetch_panel(tickers, start, end, use_cache=False)
    vpanel = score.fetch_volume_panel(tickers, start, end, use_cache=False)   # fetch once, inject
    print(f"\n==== {gem}: breakout co-confirmations (momentum gate = {fm0.get('momentum_gate_pct')}) ====")
    print(f"{'config':>22} {'final $':>10} {'maxDD':>8}")
    for label, ov in CONFIGS:
        fm = {**fm0, **ov}
        bt = firehose.backtest(scans, fm, cap, daily=True, overlay=gem, overlay_anchor=cfg["trigger"],
                               panel=panel, vol_panel=vpanel)
        d = bt["daily"]
        if d is None:
            print(f"{label:>22}   (no daily series)"); continue
        v = d["value"]; mdd = min((x - m) / m for x, m in zip(v, [max(v[:i + 1]) for i in range(len(v))]))
        print(f"{label:>22} ${v[-1]:>8,.0f} {100*mdd:>7.1f}%")


if __name__ == "__main__":
    for g in (sys.argv[1:] or ["MP", "HL", "BWET"]):
        run(g.upper())
