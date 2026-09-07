#!/usr/bin/env python3
"""measure_cull_rank.py — does the CURATOR'S OWN JUDGMENT beat price momentum for the watchlist slots?

THE QUESTION. The stack is three filters: firehose -> curator -> optimizer. But the curator's whole
apparatus (events, theses, exits, silence caps) only decides MEMBERSHIP of a ~126-name candidate
pool. Which 6 of those get capital is then decided by `_ranked_cull` on trailing risk-adjusted
return -- a momentum sort that reads none of the curator's reasoning. So most of the curator's
sophistication is discarded at the last gate.

This swaps the rank key for the curator's own coverage score (`entry["coverage"]`, stamped by
evscore since v29: source breadth, superlative count, coverage velocity, author breadth -- all
OBSERVATIONS of what the press is doing, never a forecast, so it stays inside non-negotiable #1).

FREE. `cull_rank` acts at replay time over a fixed journal, so this is a BOOK-knob experiment: no
re-curation, no LLM, no network.

JUDGED AGAINST A RANDOM NULL, which is this repo's established method for a ranker and the only
honest one -- CLAUDE.md #6 says a single curation's P&L cannot adjudicate, and `_ranked_cull`'s own
docstring reports its result that way (alphabetical 67th percentile, trailing-return 83rd,
oldest-first 53rd against a 60-seed null). A ranker that cannot beat coin-flipping is not a ranker.

THE FRESHNESS RESERVE IS HELD IDENTICAL in every arm. Without it this would test two changes at
once, and the reserve exists because a trailing statistic cannot see a catalyst younger than its own
window (non-negotiable #2).

    python scripts/measure_cull_rank.py [--seeds N] [--run PATH]
"""
from __future__ import annotations

import argparse
import bisect
import json
import random
import statistics as st
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import firehose as fh          # noqa: E402
import optimizer               # noqa: E402
from measure_orphan_rule import funded, load_scans   # noqa: E402


def coverage_by_date(run: Path):
    """{scan date: {TICKER: best coverage score of any live event naming it}}."""
    ev = json.loads((run / "journal.json").read_text())["events"]
    out: dict[str, dict[str, float]] = {}
    for e in ev.values():
        for x in (e.get("entries") or []):
            c = x.get("coverage") or {}
            if not c or not x.get("thesis_live"):
                continue
            d = out.setdefault(x["date"], {})
            for t in (x.get("vehicles") or e.get("vehicles") or []):
                t = str(t).strip().upper()
                d[t] = max(d.get(t, float("-inf")), float(c.get("score") or 0.0))
    return out


def make_cull(mode: str, cov: dict, seed: int = 0):
    """Same two tiers as fh._ranked_cull; only the SECOND tier's sort key changes."""
    dates = sorted(cov)
    rng = random.Random(seed)

    def cull(ev, keep, panel, asof, lookback, first_k, k, fresh_slots, fresh_scans):
        if not keep or len(ev) <= keep:
            return ev
        fresh = [t for t in ev if t in first_k and (k - first_k[t]) < fresh_scans]
        fresh = sorted(fresh, key=lambda t: first_k[t], reverse=True)[:max(0, fresh_slots)]
        rest = [t for t in ev if t not in set(fresh)]
        if mode == "coverage":
            # the scan on/before this rebalance -- coverage is stamped per SCAN, the cull runs per
            # rebalance day, and they are not the same calendar dates.
            key = str(pd.Timestamp(asof).date())
            j = bisect.bisect_right(dates, key) - 1
            sc = cov.get(dates[j], {}) if j >= 0 else {}
            rest = sorted(rest, key=lambda t: (-sc.get(t, float("-inf")), t))
        else:
            rest = rest[:]
            rng.shuffle(rest)
        return (fresh + rest)[:keep]
    return cull


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="data/cbt_3yr_v29_silence")
    ap.add_argument("--seeds", type=int, default=24)
    a = ap.parse_args()
    run = ROOT / a.run
    fm = optimizer.load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    scans = load_scans(run)
    panel = pd.read_csv(run / "panel.csv", index_col=0, parse_dates=True)
    cov = coverage_by_date(run)
    if not cov:
        print(f"  {run.name} has no stamped `coverage` -- this needs a v29-or-later curation.")
        return 1
    print(f"  {run.name}: coverage on {len(cov)} scans, "
          f"median {st.median(len(v) for v in cov.values()):.0f} tickers/scan · "
          f"max_watchlist {fh.watchlist_cap(fm)}\n")

    def replay(patch):
        orig = fh._ranked_cull
        if patch is not None:
            fh._ranked_cull = patch
        try:
            bt = fh.backtest(scans, fm, capital=50000, panel=panel,
                             freeze_panel=str(run / "panel.csv"))
        finally:
            fh._ranked_cull = orig
        f = funded(bt)
        ds = sorted(f)
        churn = [len(set(f[d]) - set(f[ds[i - 1]])) for i, d in enumerate(ds) if i]
        return float(bt.get("final") or 0), (st.mean(churn) if churn else 0.0)

    base, base_turn = replay(None)
    covf, cov_turn = replay(make_cull("coverage", cov))
    nulls = []
    for s in range(a.seeds):
        v, _ = replay(make_cull("random", cov, seed=s))
        nulls.append(v)
        print(f"    null seed {s:2d}: ${v:,.0f}", flush=True)
    nulls.sort()

    def pct(v):
        return 100.0 * sum(1 for x in nulls if x < v) / len(nulls)

    print(f"\n  {'arm':<26}{'final':>14}{'vs random null':>18}{'turnover':>11}")
    print(f"    {'TREND (current)':<24}{base:>14,.0f}{pct(base):>15.0f}th{base_turn:>11.2f}")
    print(f"    {'COVERAGE (curator)':<24}{covf:>14,.0f}{pct(covf):>15.0f}th{cov_turn:>11.2f}")
    print(f"    {'random null':<24}{st.median(nulls):>14,.0f}{'50th (by defn)':>18}")
    print(f"      null spread: ${nulls[0]:,.0f} .. ${nulls[-1]:,.0f} "
          f"(median ${st.median(nulls):,.0f}, n={len(nulls)})")
    print("\n  READ IT AS A PERCENTILE, NOT A DOLLAR DIFFERENCE (CLAUDE.md #6): these are paired\n"
          "  replays of ONE curation, so a lucky book lifts every arm at once.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
