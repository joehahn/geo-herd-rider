"""build_golden.py — freeze a deterministic BWET-era regression snapshot.

The problem: re-running the LLM curator is non-deterministic, and even a fixed scan log drifts
because backtest() fetches LIVE yfinance prices (the book wandered +421%->+393% over days with NO
code change). So neither is a clean regression target for CODE revisions.

This freezes the three deterministic inputs into data/golden/bwet/ — the committed event-first
scan log, the price panel it needs, and the financial-model knobs — plus the expected backtest
OUTPUT. `scripts/check_golden.py` then replays the frozen inputs and asserts the output is byte-
stable, isolating code changes from LLM noise and price drift.

    python scripts/build_golden.py            # (re)generate the snapshot from the committed scan log
    python scripts/build_golden.py --refresh-panel   # also re-pull prices from yfinance (rare)

Regenerate ONLY when you intend to change the baseline (an engine/sizing change you've vetted) —
then commit the new snapshot in the same change. Routine code revisions must leave it untouched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import firehose  # noqa: E402
import score  # noqa: E402
from optimizer import load_financial_model  # noqa: E402

SCANS_SRC = ROOT / "data" / "windows" / "firehose_scans.json"
GOLDEN = ROOT / "data" / "golden" / "bwet"
CAPITAL = 50_000.0


def load_scans(path: Path) -> dict:
    raw = json.loads(path.read_text())
    return {pd.Timestamp(k): v for k, v in raw.items()}


def expected_from_backtest(bt: dict) -> dict:
    """The regression surface: the structure that MUST stay stable under a code-only change —
    week count, the per-week book (watchlist, funded weights, return), and the headline totals.
    Daily-series floats are intentionally excluded (too brittle); the weekly log captures the logic."""
    return {
        "weeks": bt["weeks"],
        "final": round(bt["final"], 2),
        "spy_final": round(bt["spy_final"], 2),
        "log": bt["log"],  # [{week, watchlist, weights, week_return}] — funded weights per week
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh-panel", action="store_true",
                    help="re-pull the price panel from yfinance (else reuse the frozen one if present)")
    args = ap.parse_args(argv)

    GOLDEN.mkdir(parents=True, exist_ok=True)
    scans = load_scans(SCANS_SRC)
    fm = load_financial_model(str(ROOT / "investor_profile.md"))

    # 1. price panel — frozen so the replay is offline + deterministic
    panel_path = GOLDEN / "panel.csv"
    if args.refresh_panel or not panel_path.exists():
        watch = firehose._stateful_watch(scans)
        anchors = list(scans)
        lookback = int(fm["lookback_period_days"])
        tickers = {score.BENCHMARK, firehose.OVERLAY} | {t for w in watch.values() for t in w}
        start = (anchors[0] - pd.Timedelta(days=lookback + 14)).strftime("%Y-%m-%d")
        end = (anchors[-1] + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
        panel = score.fetch_panel(sorted(tickers), start, end, use_cache=False)
        panel.to_csv(panel_path)
        print(f"  froze price panel ({panel.shape[0]} days x {panel.shape[1]} tickers) -> {panel_path}")
    # ALWAYS recompute expected from the CSV-reloaded panel, not the in-memory one: with a tight
    # min_trade_size the optimizer is knife-edge, so CSV float round-trip can flip which name wins.
    # check_golden reads this same CSV, so expected must be derived from it for the replay to match.
    panel = pd.read_csv(panel_path, index_col=0, parse_dates=True)
    if not args.refresh_panel:
        print(f"  using frozen panel {panel_path} (use --refresh-panel to re-pull)")

    # 2. frozen inputs: scan log + the fm knobs that shaped the book
    (GOLDEN / "firehose_scans.json").write_text(SCANS_SRC.read_text())
    (GOLDEN / "fm.json").write_text(json.dumps(fm, indent=2, default=str) + "\n")

    # 3. expected output — replay against the FROZEN panel, not live prices
    bt = firehose.backtest(scans, fm, CAPITAL, daily=True, panel=panel)
    expected = expected_from_backtest(bt)
    (GOLDEN / "expected.json").write_text(json.dumps(expected, indent=2) + "\n")

    print(f"  wrote scan log, fm.json, expected.json -> {GOLDEN}")
    print(f"  golden book: ${CAPITAL:,.0f} -> ${expected['final']:,.0f} over {expected['weeks']} weeks "
          f"(SPY ${expected['spy_final']:,.0f})")
    print("Commit data/golden/bwet/ alongside any change that intentionally moves the baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
