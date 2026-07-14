#!/usr/bin/env python3
"""momentum_gate_sweep.py — test a PRICE-CONFIRMATION gate WITHOUT re-running the curator (no LLM).

Hypothesis (Joe, 2026-07-13): the solution wins on events where the ticker has ALREADY grown >~X% over
the past ~month — a realized-momentum confirmation — rather than on the LLM's (empirically weak) conviction.
This gates FUNDING on the ticker's own trailing return: a pick is kept only if its realized N-day return as
of the decision week clears the threshold. Deterministic + look-ahead-clean (trailing) + free (cached prices).

Sweeps threshold on the existing per-gem curator scans and re-runs firehose.backtest. If a positive gate
lifts returns across gems, it validates building the candidate->live promotion gate (idea 2) in the engine.

    python scripts/momentum_gate_sweep.py MP HL BWET
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

THRESHOLDS = [None, 0.0, 0.10, 0.20, 0.30]   # None = no gate (baseline)
WINDOW_DAYS = 30

SCANS = {"MP": "firehose_scans_mp.json", "HL": "firehose_scans_hl.json", "BWET": "firehose_scans_bwet.json"}


def load_scans(name):
    raw = json.loads((ROOT / "data" / "windows" / name).read_text())
    return {pd.Timestamp(wk + " 16:30", tz="America/New_York"): p for wk, p in raw.items()}


def tret(panel, tk, wk, days):
    """Realized trailing `days`-calendar return of tk as of week `wk` (look-ahead-clean)."""
    if tk not in panel.columns:
        return None
    wkd = wk.tz_localize(None)
    s = panel[tk].dropna()
    s = s[s.index <= wkd]
    if len(s) < 2:
        return None
    past = s[s.index <= wkd - pd.Timedelta(days=days)]
    if not len(past):
        return None
    return s.iloc[-1] / past.iloc[-1] - 1.0


def run(gem):
    scans0 = load_scans(SCANS[gem])
    cfg = bd.gem_config(gem)
    fm = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    cap = float(fm.get("initial_investment_usd", 50_000))
    tickers = sorted({p["ticker"] for ps in scans0.values() for p in ps if p.get("ticker")}) + [score.BENCHMARK, gem]
    anchors = sorted(scans0)
    start = (anchors[0] - pd.Timedelta(days=WINDOW_DAYS + 40)).strftime("%Y-%m-%d")
    end = (anchors[-1] + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
    panel = score.fetch_panel(sorted(set(tickers)), start, end, use_cache=False)

    print(f"\n==== {gem}: momentum-confirmation gate ({WINDOW_DAYS}-day trailing return) ====")
    print(f"{'gate':>10} {'final $':>10} {'vs base':>9} {'maxDD':>7} {'picks kept':>11}")
    base_val = None
    for th in THRESHOLDS:
        if th is None:
            filt = scans0
        else:
            filt = {wk: [p for p in ps if (tret(panel, p["ticker"], wk, WINDOW_DAYS) or -9) >= th] for wk, ps in scans0.items()}
        kept = sum(len(ps) for ps in filt.values())
        bt = firehose.backtest(filt, fm, cap, daily=True, overlay=gem, overlay_anchor=cfg["trigger"], panel=panel)
        d = bt["daily"]
        if d is None:
            print(f"{'none' if th is None else f'+{int(th*100)}%':>10}  (no daily series)"); continue
        v = d["value"]; mdd = min((x - m) / m for x, m in zip(v, [max(v[:i + 1]) for i in range(len(v))]))
        if th is None:
            base_val = v[-1]
        tag = "  <- baseline" if th is None else ""
        vs = "" if base_val is None or th is None else f"{100*(v[-1]-base_val)/base_val:+.1f}%"
        print(f"{'none' if th is None else f'>=+{int(th*100)}%':>10} ${v[-1]:>8,.0f} {vs:>9} {100*mdd:>6.1f}% {kept:>11}{tag}")


if __name__ == "__main__":
    for g in (sys.argv[1:] or ["MP", "HL", "BWET"]):
        run(g.upper())
