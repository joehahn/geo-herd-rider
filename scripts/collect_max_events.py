#!/usr/bin/env python3
"""collect_max_events.py — turn the max_events curations into one comparable series.

WHY THIS IS NOT PART OF sweep_optimizer.py. That sweep is free: the curation is FIXED and only the
book math varies, so 6,300 cells replay off one journal. `max_events` is a CURATION knob -- it decides
which events stay live and therefore which tickers ever reach the optimizer -- so every value needs its
own re-curation and its own LLM bill (~$3-4.50 and ~45 min each, measured). Six points cost what the
entire optimizer grid does not.

WHAT IT MEASURES, and in what order of trust. Each run is ONE stochastic sample: the scout is an LLM,
so two runs at the SAME cap would not produce the same book. This repo has already been burnt by that
(the catalyst-gate experiment, where a single-run A/B showed lift that did not survive a re-scan). So:

  TRUST FIRST   events, cull-at-birth %, agent-reads -- structural counts, driven by the cap directly.
  TRUST LESS    funded events, tickers -- one optimizer step removed from the knob.
  TRUST LEAST   final value -- one lucky name moves it more than the knob does.

A monotone trend across six points is worth something; a single-point spike in dollars is not.

    python scripts/collect_max_events.py --out data/sweep_max_events.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import firehose as fh  # noqa: E402
import optimizer  # noqa: E402
from sweep_optimizer import load_scans, metrics  # noqa: E402

# Scans a COMPLETE 3-year monthly curation produces, over 2023-08-11..2026-08-09. Hard-coded, so if
# the backtest window ever changes this must change with it -- a stale value here makes every run look
# permanently "incomplete" and the series silently empty, which is at least loud rather than wrong.
EXPECT_SCANS = 37

# (max_events value, run directory). 0 is the nominal run, already curated as data/cbt_3yr_v4.
RUNS = [(0, "data/cbt_3yr_v4"), (4, "data/cbt_3yr_me4"), (8, "data/cbt_3yr_me8"),
        (12, "data/cbt_3yr_me12"), (16, "data/cbt_3yr_me16"), (20, "data/cbt_3yr_me20")]


def _mech(run: Path) -> dict:
    """Structural counts straight off the journal -- the numbers the cap moves directly."""
    j = json.loads((run / "journal.json").read_text())
    ev = j.get("events") or {}
    items = list(ev.items())
    born = [k for k, e in items if not (e.get("entries") or [])]
    return {"events": len(items),
            "culled_at_birth": len(born),
            "cull_pct": round(100 * len(born) / len(items), 1) if items else 0.0,
            "agent_reads": sum(len(e.get("entries") or []) for _, e in items),
            "vehicles": len({v for _, e in items for v in (e.get("vehicles") or [])})}


def _costs_by_window(runs: list) -> dict:
    """LLM spend per curation, attributed from the GLOBAL ledger by each run's OWN time window.

    There is no per-run cost file -- every call lands in data/llm_costs.csv -- so a first version
    looked for one, found nothing, and reported $0.00 for all six runs.
    The second version chained the windows (each run owns rows since the previous run's end). That
    broke on the very first row: the uncapped run was curated a day earlier, so it sorted first, its
    window opened at the dawn of the ledger, and it was billed $1,066.67 for a $4.51 curation.
    A window is now bounded on BOTH sides by the run's own directory: birth time to journal mtime.
    Independent per run, so the order the runs were curated in cannot corrupt the attribution."""
    import csv
    import datetime as dt
    rows = []
    f = ROOT / "data/llm_costs.csv"
    if f.exists():
        for r in csv.DictReader(f.open()):
            try:
                rows.append((dt.datetime.fromisoformat(r["ts"]), float(r.get("cost_usd") or 0)))
            except Exception:  # noqa: BLE001 -- a malformed ledger row must not sink the report
                continue
    out = {}
    for cap, rel in runs:
        d = ROOT / rel
        j = d / "journal.json"
        if not j.exists():
            continue
        st = d.stat()
        t0 = dt.datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_ctime), dt.timezone.utc)
        t1 = dt.datetime.fromtimestamp(j.stat().st_mtime, dt.timezone.utc)
        out[cap] = round(sum(c for t, c in rows if t0 <= t <= t1), 2)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/sweep_max_events.json")
    a = ap.parse_args(argv)

    # ONE financial model for every point, read once. The whole experiment is invalid if the optimizer
    # config drifts between runs, so it is pinned here rather than re-read per run.
    fm = optimizer.load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    fm = dict(fm)
    fm.pop("max_events", None)          # the swept knob must not also reach the book math

    costs = _costs_by_window(RUNS)
    rows = []
    for cap, rel in RUNS:
        run = ROOT / rel
        if not (run / "journal.json").exists():
            print(f"  max_events={cap:<3} {rel}: NOT PRESENT, skipped", file=sys.stderr)
            continue
        # COMPLETENESS GUARD. journal.json is written INCREMENTALLY, so a run still in progress reads
        # as a finished one with fewer events -- and every metric here (events, cull %, final value)
        # then reports the partial book as if it were the answer. Caught live: the max_events=4 run
        # gave "44 events / $75,906" at scan 6 and "59 / $112,779" at scan 10, neither being the
        # result. A point is only admitted once its scan count matches the longest run present.
        n_scans = sum(1 for ln in (run / "decisions.jsonl").open()
                      if json.loads(ln).get("kind") == "scout")
        if n_scans < EXPECT_SCANS:
            print(f"  max_events={cap:<3} {rel}: INCOMPLETE ({n_scans}/{EXPECT_SCANS} scans), skipped",
                  file=sys.stderr)
            continue
        scans = load_scans(run)
        bt = fh.backtest(scans, fm, capital=float(fm.get("initial_investment_usd") or 50_000),
                         daily=True)
        # Sharpe / drawdown / cancellation are NOT returned by backtest() -- they are derived in
        # sweep_optimizer.metrics(). Reading them off `bt` returned None for every run and the first
        # collected table showed Sharpe 0.00 and drawdown 0.0% across the board, which is a silent
        # zero, the worst kind. Reuse the sweep's own function so this series and the 6,300-cell grid
        # are computed by the SAME code and stay comparable.
        anchors = set(fh.anchor_tickers(fm))
        m = metrics(bt, anchors, fm)
        d = bt.get("daily") or {}
        alloc = d.get("alloc") or {}
        n = len(d.get("value") or [])
        # days the book holds nothing -- the defect the cash band in CBT plot 9 exposed
        idle = sum(1 for i in range(n)
                   if 1.0 - sum(s[i] for s in alloc.values() if i < len(s)) > 0.005)
        row = {"max_events": cap, "run": rel,
               "final": round(bt.get("final") or 0, 2),
               "spy": round(bt.get("spy_final") or 0, 2),
               "sharpe": m.get("sharpe"), "max_drawdown": m.get("max_drawdown"),
               "cancelled": m.get("cancelled"), "ann": m.get("ann"),
               # what each cap made on the no-brainer names (sweep_optimizer.FOCUS). Total
               # return says whether a cap paid; this says whether it paid ON THE THESES THE STRATEGY
               # EXISTS TO CATCH -- and a cap that culls discoveries should show it here first.
               "focus_gain": m.get("focus_gain"), "focus_held": m.get("focus_held"),
               "lead_months": m.get("lead_months"), "slope_2h": m.get("slope_2h"),
               "funded": len([k for k, v in alloc.items() if any(x > 0.005 for x in v)]),
               "idle_pct": round(100 * idle / n, 1) if n else 0.0,
               "cost_usd": costs.get(cap, 0.0)}
        row.update(_mech(run))
        rows.append(row)
        print(f"  max_events={cap:<3} events {row['events']:4}  cull {row['cull_pct']:5.1f}%  "
              f"reads {row['agent_reads']:5}  funded {row['funded']:3}  ${row['final']:>10,.0f}")

    out = ROOT / a.out
    out.write_text(json.dumps({"rows": rows, "config": {k: fm.get(k) for k in (
        "max_watchlist", "concentration_cap", "optimizer_lookback_days",
        "drop_unfunded_weeks", "risk_aversion", "min_trade_size")}}, indent=1))
    print(f"  wrote {out}  ({len(rows)} points)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
