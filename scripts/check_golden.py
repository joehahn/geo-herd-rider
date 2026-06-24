"""check_golden.py — deterministic regression check for code revisions.

Replays the FROZEN BWET-era snapshot (data/golden/bwet/: scan log + price panel + fm knobs) through
firehose.backtest() and asserts the output still matches the committed expected.json. Because every
input is frozen, any difference is your CODE change — not LLM non-determinism and not yfinance price
drift. Fast (no network, no LLM), free, and reproducible.

    python scripts/check_golden.py        # exit 0 = stable, 1 = drift (prints a per-week diff)

Use it as the routine regression gate for behavior-affecting edits (sizing, sticky-hold, entry
timing, the backtest loop). If a diff is INTENTIONAL (a vetted engine/sizing change), regenerate the
baseline with `python scripts/build_golden.py` and commit the new snapshot. Build the snapshot first
with build_golden.py if data/golden/bwet/ is missing.

NOTE: this checks the deterministic backtest/sizing layer. LLM-layer changes (scout/matcher/agent
prompts) are NOT covered here — smoke-test those with `firehose.py --fixture`, and validate on the
multi-gem harness (the scoreboard is the verdict).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import firehose  # noqa: E402

GOLDEN = ROOT / "data" / "golden" / "bwet"
CAPITAL = 50_000.0


def _by_week(log: list[dict]) -> dict:
    return {row["week"]: row for row in log}


def diff(expected: dict, actual: dict) -> list[str]:
    """Human-readable list of mismatches; empty = identical."""
    out: list[str] = []
    for k in ("weeks", "final", "spy_final"):
        if expected.get(k) != actual.get(k):
            out.append(f"  {k}: expected {expected.get(k)} -> got {actual.get(k)}")
    exp_w, act_w = _by_week(expected["log"]), _by_week(actual["log"])
    for wk in sorted(set(exp_w) | set(act_w)):
        e, a = exp_w.get(wk), act_w.get(wk)
        if e is None:
            out.append(f"  {wk}: NEW week in output {a}")
        elif a is None:
            out.append(f"  {wk}: MISSING week (expected {e})")
        elif e != a:
            for f in ("watchlist", "weights", "week_return"):
                if e.get(f) != a.get(f):
                    out.append(f"  {wk} {f}: expected {e.get(f)!r} -> got {a.get(f)!r}")
    return out


def main() -> int:
    if not (GOLDEN / "expected.json").exists():
        print(f"No golden snapshot at {GOLDEN}. Run: python scripts/build_golden.py", file=sys.stderr)
        return 2

    raw = json.loads((GOLDEN / "firehose_scans.json").read_text())
    scans = {pd.Timestamp(k): v for k, v in raw.items()}
    fm = json.loads((GOLDEN / "fm.json").read_text())
    panel = pd.read_csv(GOLDEN / "panel.csv", index_col=0, parse_dates=True)
    expected = json.loads((GOLDEN / "expected.json").read_text())

    bt = firehose.backtest(scans, fm, CAPITAL, daily=True, panel=panel)
    actual = {"weeks": bt["weeks"], "final": round(bt["final"], 2),
              "spy_final": round(bt["spy_final"], 2), "log": bt["log"]}

    mismatches = diff(expected, actual)
    if mismatches:
        print("GOLDEN DRIFT — the frozen BWET replay no longer matches expected.json:")
        print("\n".join(mismatches))
        print("\nIf this change is INTENTIONAL, rebuild: python scripts/build_golden.py")
        return 1

    print(f"GOLDEN OK — BWET replay stable: ${CAPITAL:,.0f} -> ${expected['final']:,.0f} "
          f"over {expected['weeks']} weeks (SPY ${expected['spy_final']:,.0f}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
