#!/usr/bin/env python3
"""proto_surge.py — a DETERMINISTIC, ZERO-LLM candidate picker, scored against the live curator.

THE HYPOTHESIS. The thing worth owning is a ticker the press has started naming REPEATEDLY, whose
price is already rising. Both halves are measurable, so neither needs an LLM:

  repeatedly named  -> a SURGE in mention count against that ticker's OWN baseline. Raw frequency is
                       useless: it just returns NVDA every week. A name going 0 -> 5 mentions is the
                       under-the-radar signal; NVDA going 30 -> 34 is not. Normalising against the
                       ticker's own trailing history is what separates the two, and it is the whole
                       "is this a gem?" judgement reduced to arithmetic.
  sustained rise    -> a price condition over the trailing window. Free.

If this matches or beats the live curator's picks, then the scout's per-week LLM judgement, the
event agents, and the six knobs around them are buying less than they cost in complexity -- and the
solution collapses to: count mentions, check the trend, hand the survivors to the optimizer.

WHAT IT IS NOT. This does not test the SCOUT's extraction (which tickers an article names) -- it
reuses that, via the ticker/company aliases the scout already produced. It tests the SELECTION step:
given the same candidate universe, does a mention-surge rule pick better than an LLM does?

    python scripts/proto_surge.py --run data/cbt_3yr_v10 --corpus data/backtest_3yr
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import re
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pandas as pd  # noqa: E402
import score  # noqa: E402

STOP = {"inc", "corp", "corporation", "ltd", "limited", "plc", "group", "holdings", "holding", "co",
        "company", "the", "and", "technologies", "technology", "energy", "mining", "resources",
        "international", "industries", "systems", "solutions", "pharmaceuticals", "pharma", "etf",
        "trust", "fund", "shares", "capital", "partners", "global", "american", "us", "usa"}


def _lit(x):
    return x if isinstance(x, (list, dict)) else ast.literal_eval(x)


def build_aliases(run: Path) -> dict[str, str]:
    """alias -> ticker, from the ticker/company pairs the scout already emitted.

    Deliberately CONSERVATIVE. A false alias silently inflates a ticker's mention count, which is the
    one number this whole experiment rests on, so anything ambiguous is dropped rather than guessed:
    single common words, anything under 4 characters, and any alias claimed by two tickers."""
    pairs = collections.defaultdict(set)
    for line in (run / "decisions.jsonl").open():
        for c in _lit(json.loads(line)["proposed"]):
            t = (c.get("ticker") or "").strip().upper()
            if t.isalpha() and 1 < len(t) <= 5:
                pairs[t].add((c.get("company") or "").strip())
    alias: dict[str, list[str]] = collections.defaultdict(list)
    for t, names in pairs.items():
        alias[t.lower()].append(t)                       # the symbol itself
        for nm in names:
            toks = [w for w in re.findall(r"[A-Za-z]+", nm.lower()) if w not in STOP and len(w) >= 4]
            if toks:
                alias[toks[0]].append(t)                 # "NVIDIA Corp" -> nvidia
            if len(toks) >= 2:
                alias[" ".join(toks[:2])].append(t)      # "lithium americas" -> LAC
    return {a: ts[0] for a, ts in alias.items() if len(set(ts)) == 1}


def count_mentions(arts: list[dict], alias: dict[str, str]) -> dict:
    """(ticker, date) -> mention count, over title + snippet."""
    uni = {a for a in alias if " " in a}
    cnt: dict = collections.defaultdict(int)
    for art in arts:
        txt = ((art.get("title") or "") + " " + (art.get("snippet") or "")).lower()
        toks = re.findall(r"[a-z]+", txt)
        hits = {alias[w] for w in toks if w in alias}
        for bg in uni:                                   # two-word aliases, checked as substrings
            if bg in txt:
                hits.add(alias[bg])
        d = (art.get("published_date") or "")[:10]
        for t in hits:
            cnt[(t, d)] += 1
    return cnt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="data/cbt_3yr_v10")
    ap.add_argument("--corpus", default="data/backtest_3yr")
    ap.add_argument("--window", type=int, default=30, help="mention window, days")
    ap.add_argument("--baseline", type=int, default=180, help="trailing baseline, days")
    ap.add_argument("--min-mentions", type=int, default=3)
    ap.add_argument("--trend-days", type=int, default=60, help="0 disables the price condition")
    ap.add_argument("--horizon", type=int, default=90, help="forward return horizon, days")
    a = ap.parse_args(argv)
    run, corp = ROOT / a.run, ROOT / a.corpus

    alias = build_aliases(run)
    print(f"  {len(alias)} unambiguous aliases -> {len(set(alias.values()))} tickers", flush=True)
    arts = json.loads((corp / "pool.json").read_text())["articles"]
    cnt = count_mentions(arts, alias)
    print(f"  {len(cnt):,} (ticker, day) mention cells over {len(arts):,} articles", flush=True)

    # the live curator's own decisions, as the benchmark
    rows = [json.loads(l) for l in (run / "decisions.jsonl").open()]
    anchors = [r["context"] for r in rows]
    actual = [(r["context"], t) for r in rows for t in _lit(r["admitted"])
              if t.isalpha() and 1 < len(t) <= 5]
    k_per_scan = {r["context"]: len(_lit(r["admitted"])) for r in rows}

    by_t: dict = collections.defaultdict(dict)
    for (t, d), n in cnt.items():
        by_t[t][d] = n

    def window_count(t, end, days):
        lo = (pd.Timestamp(end) - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
        return sum(n for d, n in by_t.get(t, {}).items() if lo < d <= end)

    picks = []
    for anch in anchors:
        scored = []
        for t in by_t:
            w = window_count(t, anch, a.window)
            if w < a.min_mentions:
                continue
            base = window_count(t, anch, a.baseline) - w
            rate = base / max(a.baseline - a.window, 1) * a.window      # expected, from its own history
            surge = w / max(rate, 0.5)                                  # 0.5 floor: never-mentioned names
            scored.append((surge, w, t))
        scored.sort(reverse=True)
        for _, _, t in scored[:k_per_scan.get(anch, 4)]:
            picks.append((anch, t))

    # ---- score both sets on forward return ------------------------------------------------------
    tk = sorted({t for _, t in actual + picks})
    panel = score.fetch_panel(tk, "2023-08-01", "2026-08-12", use_cache=True)

    def fwd(t, d, days):
        if t not in panel:
            return None
        s = panel[t].dropna()
        s = s[s.index >= pd.Timestamp(d, tz=s.index.tz)] if s.index.tz else s[s.index >= pd.Timestamp(d)]
        if len(s) < 5:
            return None
        return (s.iloc[min(days, len(s) - 1)] / s.iloc[0] - 1) * 100

    def trend_ok(t, d):
        if not a.trend_days or t not in panel:
            return True
        s = panel[t].dropna()
        s = s[s.index <= pd.Timestamp(d, tz=s.index.tz)] if s.index.tz else s[s.index <= pd.Timestamp(d)]
        return len(s) > 5 and s.iloc[-1] > s.iloc[-min(a.trend_days, len(s) - 1)]

    gated = [(d, t) for d, t in picks if trend_ok(t, d)]
    print()
    for lbl, S in [("CURATOR (LLM, as run)", actual),
                   ("SURGE (no LLM)", picks),
                   (f"SURGE + rising {a.trend_days}d", gated)]:
        X = [v for d, t in S if (v := fwd(t, d, a.horizon)) is not None and abs(v) < 2000]
        if not X:
            print(f"  {lbl:26s} no priced picks")
            continue
        print(f"  {lbl:26s} n={len(X):4d}  median {st.median(X):6.1f}%  mean {st.mean(X):6.1f}%  "
              f"win {100 * sum(1 for x in X if x > 0) / len(X):3.0f}%  "
              f"top-decile {sorted(X)[int(.9 * len(X))]:6.1f}%")
    ov = len(set(actual) & set(picks))
    print(f"\n  overlap with the curator's picks: {ov}/{len(set(actual))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
