#!/usr/bin/env python3
"""refresh_gem_dashboards.py — LIVE-rebuild the per-gem dashboards while the full-pool curator run is
in progress. Every pass: slice the incrementally-written firehose_scans_full.json per gem (only events
LAUNCHED within each gem's era; capital invested at the era start) and rebuild each gem's dashboard,
skipping gems whose era hasn't accumulated any completed weeks yet.

A SEPARATE process from the curator (reads its scans file, never touches it). Throttled (default 240s)
so it doesn't hammer yfinance; loops until the curator process exits, then does one final pass.

    python scripts/refresh_gem_dashboards.py            # loop until curator done
    python scripts/refresh_gem_dashboards.py --once     # single pass (manual refresh)
"""
from __future__ import annotations
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
from util import load_dotenv  # noqa: E402
load_dotenv()
import backtest_retrieval_curator as brc  # noqa: E402  (fresh import -> the FIXED slice_full)
import build_dashboard as bd  # noqa: E402

FULL = ROOT / "data" / "windows" / "firehose_scans_full.json"
INTERVAL = 240   # seconds between passes
_prev_built: set = set()   # gems that existed last pass -> only re-render the retrieval navbar when this grows


def one_pass() -> None:
    global _prev_built
    if not FULL.exists():
        print("  (no full scans yet)", flush=True); return
    full = json.loads(FULL.read_text())
    brc.slice_full(full)                                     # per-gem era files, era-launched events only
    built = []
    for g in brc.GEM_ERA:
        try:
            if g == "BWET":                                 # BWET's gem_config points at the fixture -> override
                bd.build_gem("BWET", scans_override="firehose_scans_bwet.json", out_override="bwet_curator")
            else:
                bd.build_gem(g)
            built.append(g)
        except SystemExit:                                  # no in-era priced weeks completed yet -> skip
            pass
        except Exception as e:  # noqa: BLE001
            print(f"    {g}: skip ({e})", flush=True)
    print(f"REFRESHED {len(built)}/{len(brc.GEM_ERA)} gem dashboards from {len(full)} weeks: {built}", flush=True)
    if set(built) != _prev_built:                           # a gem appeared/disappeared -> refresh retrieval navbar
        try:
            import build_retrieval_dashboard as brd  # noqa: PLC0415
            brd.build()
            print(f"  -> retrieval db navbar refreshed with {len(built)} gem links: {built}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  retrieval-db refresh skip ({e})", flush=True)
        _prev_built = set(built)


def curator_running() -> bool:
    return subprocess.run(["pgrep", "-f", "backtest_retrieval_curator.py --full"],
                          capture_output=True).returncode == 0


if __name__ == "__main__":
    if "--once" in sys.argv:
        one_pass()
    else:
        while True:
            one_pass()
            if not curator_running():
                print("curator finished -> final refresh done.", flush=True)
                one_pass()
                break
            time.sleep(INTERVAL)
