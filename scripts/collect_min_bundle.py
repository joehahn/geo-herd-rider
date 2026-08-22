#!/usr/bin/env python3
"""collect_min_bundle.py — the min_bundle_articles series for SBT, one row per arm.

Mirrors collect_max_events.py: min_bundle_articles is a CURATION knob, so each value cost a full
re-curation (2026-08-21, $20.23 for three) rather than a replay. Books are computed from each run's
OWN frozen panel.csv so the series is reproducible and cannot drift with live prices.

    .venv/bin/python scripts/collect_min_bundle.py
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
from util import load_dotenv  # noqa: E402
load_dotenv()
import pandas as pd  # noqa: E402
import firehose as fh, optimizer as optimizer  # noqa: E402
from sweep_optimizer import load_scans  # noqa: E402

RUNS = [(1, "data/cbt_3yr_mb1"), (2, "data/cbt_3yr_mb2"), (3, "data/cbt_3yr_mb3")]


def main() -> int:
    fm = optimizer.load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    cap = float(fm.get("initial_investment_usd", 50_000))
    rows = []
    for val, run in RUNS:
        d = ROOT / run
        if not (d / "journal.json").exists():
            print(f"  {run}: missing, skipped", file=sys.stderr); continue
        j = json.loads((d / "journal.json").read_text())
        items = list((j.get("events") or {}).items())
        bt = fh.backtest(load_scans(d), fm, capital=cap, daily=True,
                         panel=pd.read_csv(d / "panel.csv", index_col=0, parse_dates=True))
        rows.append({"min_bundle_articles": val, "run": run,
                     "final": round(bt.get("final") or 0, 2),
                     "spy": round(bt.get("spy_final") or 0, 2),
                     "events": len(items),
                     "culled_at_birth": sum(1 for _, e in items if not (e.get("entries") or [])),
                     "agent_reads": sum(len(e.get("entries") or []) for _, e in items)})
        print(f"  min_bundle_articles={val}  ${rows[-1]['final']:,.0f}  {rows[-1]['events']} events",
              flush=True)
    out = ROOT / "data/sweep_min_bundle.json"
    out.write_text(json.dumps({"rows": rows}, indent=1))
    print(f"wrote {out} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
