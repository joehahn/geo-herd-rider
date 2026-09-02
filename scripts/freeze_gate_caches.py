#!/usr/bin/env python3
"""freeze_gate_caches.py — one SHARED, clean corp-actions cache for the two book risk gates.

THE BUG THIS FIXES, found 2026-09-01 mid-sweep. `firehose.backtest` freezes `corpactions.json` and
`volume.csv` beside a run's `panel.csv`, and fetches them LIVE when they are absent. In a sweep that
happens inside all 10 worker processes at once, so ten simultaneous yfinance fetches race, get
rate-limited, and each worker ends up with a DIFFERENT partial view. The death-spiral gate fails open
per missing ticker, so cells computed by different workers ran under different risk gates.

Measured error rates in the caches on disk when this was found (share of tickers the gate could not
evaluate, hence silently never excluded):

    mb2rep 257/257 · v21_evscans12 266/394 · v20_catalystkey 254/393 · v22_resolver 229/369
    v23_silence 147/281 · v24_wirelede 121/257 · bw21 1/437 · mb1 0/261 · v9 0/269 · v18 0/438
    v25_vehgate 1/229 · v19 MISSING · wk14 MISSING

So the 13 curations were about to be swept under six different gate regimes, decided by nothing but
when each cache happened to be fetched and how hard yfinance was throttling at that moment. A
cross-curation ranking built on that would partly be ranking the rate limiter.

THE FIX IS SHARING, not just retrying. One cache keyed by TICKER, fetched serially with backoff, then
written out as each run's per-run subset. 1,206 unique tickers cover 4,493 per-run slots, so it is
also 73% less fetching -- and, the point, every run's gate sees byte-identical data.

    scripts/freeze_gate_caches.py            # fill the shared cache, then write all 13 per-run files
    scripts/freeze_gate_caches.py --report   # just print the error table, fetch nothing
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "data" / "corpactions_shared.json"
RUNS = ("bw21 mb1 mb2rep v9 v18 v19 v20_catalystkey v21_evscans12 v22_resolver v23_silence "
        "v24_wirelede v25_vehgate wk14").split()


def run_dir(r: str) -> Path:
    return ROOT / "data" / f"cbt_3yr_{r}"


def panel_tickers(r: str) -> list[str]:
    return list(pd.read_csv(run_dir(r) / "panel.csv", index_col=0, nrows=1).columns)


def fetch_batch(ts: list[str], yf) -> dict:
    """{first listing date, reverse splits} for a BATCH, in one yfinance call.

    BATCHED, not per-ticker. The per-ticker form (yf.Ticker().history + .splits) measured ~6 s a name
    once yfinance's throttle engaged -- two hours for these 1,206. `download(..., actions=True)`
    returns the split column alongside the bars, so 40 names cost one request and the whole set takes
    minutes. Same two fields, same semantics: first bar date, and splits with ratio < 1.
    """
    out: dict = {}
    raw = None
    delay = 5.0
    for _ in range(4):
        try:
            raw = yf.download(ts, period="max", interval="1d", actions=True, group_by="ticker",
                              auto_adjust=True, progress=False, threads=True)
            break
        except Exception as e:  # noqa: BLE001
            if "RateLimit" not in type(e).__name__ and "Too Many" not in str(e):
                break
            time.sleep(delay)
            delay *= 2.5
    for t in ts:
        try:
            sub = (raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw).dropna(how="all")
            if not len(sub):
                raise ValueError("no bars")
            sp = sub["Stock Splits"] if "Stock Splits" in sub.columns else None
            out[t] = {"first": sub.index[0].date().isoformat(),
                      "rsplits": ([[i.date().isoformat(), float(v)] for i, v in sp.items()
                                   if v and float(v) < 1.0] if sp is not None else [])}
        except Exception:  # noqa: BLE001 -- FAIL OPEN, and the report counts it
            out[t] = {"first": None, "rsplits": [], "error": True}
    return out


def report() -> None:
    print(f"{'run':<18} {'tickers':>8} {'gate errors':>12}")
    for r in RUNS:
        cf = run_dir(r) / "corpactions.json"
        if not cf.exists():
            print(f"{r:<18} {'-':>8} {'MISSING':>12}")
            continue
        j = json.loads(cf.read_text())
        e = sum(1 for v in j.values() if v.get("error"))
        print(f"{r:<18} {len(j):>8} {e:>12}{'   <-- fails open' if e > len(j) * 0.02 else ''}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="print the error table and stop")
    a = ap.parse_args()
    if a.report:
        report()
        return 0

    import yfinance as yf

    want: set[str] = set()
    for r in RUNS:
        want |= set(panel_tickers(r))
    cache: dict = json.loads(SHARED.read_text()) if SHARED.exists() else {}
    todo = sorted(t for t in want if t not in cache or cache[t].get("error"))
    print(f"{len(want)} unique tickers across {len(RUNS)} runs; {len(cache)} cached, "
          f"{len(todo)} to fetch", flush=True)

    BATCH = 40
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        cache.update(fetch_batch(chunk, yf))
        SHARED.write_text(json.dumps(cache))         # checkpoint, so a kill costs at most one batch
        err = sum(1 for v in cache.values() if v.get("error"))
        print(f"  {min(i + BATCH, len(todo))}/{len(todo)}  cached={len(cache)}  unresolved={err}",
              flush=True)
        time.sleep(1.0)                              # pace, so ten-way racing never happens again

    for r in RUNS:
        ts = panel_tickers(r)
        (run_dir(r) / "corpactions.json").write_text(
            json.dumps({t: cache.get(t, {"first": None, "rsplits": [], "error": True}) for t in ts}))
    print("\nper-run files rewritten from the shared cache:\n", flush=True)
    report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
