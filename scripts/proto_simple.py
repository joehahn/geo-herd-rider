#!/usr/bin/env python3
"""proto_simple.py — PROTOTYPE of the stripped-down curator (2026-07-14 design):
NO thesis / catalyst / conviction / exit / milestones. Just:
  1. harvest tickers the press names WITH A SUPERLATIVE (mechanical, from the existing retrieval pool),
  2. (variant A) confirm those up >=20% over the trailing month, or (variant B) skip the confirm,
  3. feed the watchlist to the SAME mean-variance optimizer, and
  4. drop a ticker unfunded for >=4 consecutive weeks.
All look-ahead-clean (superlatives from as-of articles; prices/returns <= the rebalance day). FREE — reuses
the saved pool + cached prices, no LLM. Stage 1 here just checks the harvest SURFACES the gems.

    python scripts/proto_simple.py            # stage-1 coverage report (no prices)
"""
from __future__ import annotations
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))

POOL = json.loads((ROOT / "data" / "retrieval_backtest.ckpt.json").read_text())
ARTS = POOL["arts"] if isinstance(POOL, dict) else POOL

# strong price-superlatives (a MOVE word, not just any adjective) — the "it's already ripping" signal
_SUPER = re.compile(r"\b(soar|surg|skyrocket|rocket|spike|explod|"
                    r"record high|all[- ]time high|52[- ]week high|"
                    r"best[- ]perform|top[- ]perform|breakout|break out|"
                    r"doubl|tripl|quadrupl|moonshot|parabolic|"
                    r"up \d{2,}%|gain\w* \d{2,}%|jump\w* \d{2,}%|\d{2,}% (rally|gain|jump|surge|pop))",
                    re.I)
# high-precision ticker patterns: exchange-prefixed, cashtag, or parenthesized (filtered by a stoplist)
_EXCH = re.compile(r"\((?:NYSE|NASDAQ|NYSEARCA|AMEX|OTC(?:MKTS)?|CBOE)[:\s]+([A-Z]{1,5})\)")
_CASH = re.compile(r"\$([A-Z]{2,5})\b")
_PAREN = re.compile(r"\(([A-Z]{2,5})\)")
_STOP = {"ETF", "CEO", "CFO", "USA", "GDP", "IPO", "AI", "EV", "USD", "SEC", "FDA", "NYSE", "NASDAQ",
         "AMEX", "OTC", "CEOS", "ESG", "EPS", "AGM", "Q1", "Q2", "Q3", "Q4", "US", "UK", "EU", "PDF",
         "NEWS", "WWW", "COM", "LLC", "INC", "CORP", "REIT", "SPAC", "GAAP", "YOY", "TAM", "OTCMKTS"}


def tickers_in(text: str) -> set:
    t = set(_EXCH.findall(text)) | set(_CASH.findall(text))
    for m in _PAREN.findall(text):                       # parenthesized: keep only if not a stopword
        if m not in _STOP:
            t.add(m)
    return {x for x in t if x not in _STOP}


def harvest() -> dict:
    """week (ISO Monday) -> {ticker -> hit count} for superlative-tagged mentions."""
    import pandas as pd
    wk_tk: dict = defaultdict(lambda: defaultdict(int))
    for a in ARTS:
        pd_ = str(a.get("published_date", ""))[:10]
        if not pd_:
            continue
        text = f"{a.get('title', '')} {a.get('snippet', '')}"
        if not _SUPER.search(text):
            continue
        try:
            wk = (pd.Timestamp(pd_) - pd.Timedelta(days=pd.Timestamp(pd_).weekday())).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            continue
        for tk in tickers_in(text):
            wk_tk[wk][tk] += 1
    return wk_tk


def _entry(days, a):
    import pandas as pd  # noqa: PLC0415
    for i, d in enumerate(days):
        if d >= a:
            return i
    return None


def _tret(panel, t, asof, win):
    """trailing win-day return as of `asof`, look-ahead-clean. None if unavailable."""
    import pandas as pd  # noqa: PLC0415
    if t not in panel.columns:
        return None
    s = panel[t].dropna(); s = s[s.index <= asof]
    if len(s) < 2:
        return None
    past = s[s.index <= asof - pd.Timedelta(days=win)]
    base = past.iloc[-1] if len(past) else s.iloc[0]
    return (s.iloc[-1] / base - 1) if base > 0 else None


def run(variant="A", capital=50_000.0, reb_days=7, confirm_pct=0.20, confirm_win=30,
        drop_after=4, news_win=7, park_spy=True):
    """Week-by-week: harvest superlative candidates -> (A: confirm >=20%/mo) -> optimizer -> drop-unfunded-4wk."""
    import pandas as pd, score, curator  # noqa: PLC0415
    from optimizer import load_financial_model  # noqa: PLC0415
    fm = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    lookback = int(fm.get("lookback_period_days", 14))
    wk_tk = harvest(); weeks = sorted(wk_tk)
    wkcount: dict = defaultdict(int)                     # weeks each ticker appears with a superlative
    for w in weeks:
        for t in wk_tk[w]:
            wkcount[t] += 1
    universe = sorted({t for t, n in wkcount.items() if n >= 2})   # >=2 weeks: drops one-off listicle noise + trims price fetch
    anchors = list(pd.date_range(weeks[0], weeks[-1], freq=f"{reb_days}D"))
    tk_all = sorted(set(universe) | {score.BENCHMARK})
    start = (anchors[0] - pd.Timedelta(days=lookback + 40)).strftime("%Y-%m-%d")
    end = (anchors[-1] + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
    panel = score.fetch_panel(tk_all, start, end, use_cache=True)
    days = panel[score.BENCHMARK].dropna().index
    valid = {t for t in universe if t in panel.columns and panel[t].notna().any()}
    streak: dict = {}                       # ticker -> consecutive unfunded weeks (drop at drop_after)
    week_w: dict = {}; reb: list = []; funded_wk: dict = defaultdict(int); held_wk: dict = defaultdict(int)
    for k, a in enumerate(anchors):
        cands = set()                       # superlative candidates in the trailing news window
        for w in weeks:
            if a - pd.Timedelta(days=news_win) <= pd.Timestamp(w) <= a:
                cands |= {t for t in wk_tk[w] if t in valid and t != score.BENCHMARK}
        for t in cands:
            streak.setdefault(t, 0)         # (re)enter the watchlist
        i = _entry(days, a); reb.append(i)
        if i is None:
            continue
        di = days[i]
        wl = list(streak)
        if variant == "A":                  # confirm: only names already up >=confirm_pct over the month
            wl = [t for t in wl if (_tret(panel, t, di, confirm_win) or -9) >= confirm_pct]
        uni = list(dict.fromkeys(wl + ([score.BENCHMARK] if park_spy else [])))   # dedup, preserve order
        w = (curator._optimized_weights(uni, panel, di, fm, lookback) or {}) if uni else {}
        week_w[k] = w
        funded = {t for t, x in w.items() if x > 0.01}
        for t in list(streak):              # drop-unfunded-4wk (funding resets the streak)
            streak[t] = 0 if t in funded else streak[t] + 1
            if streak[t] >= drop_after:
                del streak[t]
        for t in funded:
            if t != score.BENCHMARK:
                funded_wk[t] += 1
    # weekly value + per-gem held-weeks
    val = capital; vseries = [capital]
    for k in range(len(anchors) - 1):
        i, j, w = reb[k], reb[k + 1], week_w.get(k, {})
        if i is None or j is None or j <= i:
            vseries.append(round(val, 2)); continue
        d0, d1 = days[i], days[j]
        ret = sum(w.get(t, 0) * (panel.loc[d1, t] / panel.loc[d0, t] - 1)
                  for t in w if pd.notna(panel.loc[d0, t]) and pd.notna(panel.loc[d1, t]))
        val *= (1 + ret); vseries.append(round(val, 2))
        for t in w:
            if w[t] > 0.01 and t != score.BENCHMARK:
                held_wk[t] += 1
    spy = panel[score.BENCHMARK].reindex(days).ffill()
    i0 = _entry(days, anchors[0]); spy_ret = float(spy.iloc[-1] / spy.iloc[i0] - 1)
    return {"final": val, "ret": val / capital - 1, "spy_ret": spy_ret, "peak": max(vseries),
            "funded_wk": dict(funded_wk), "held_wk": dict(held_wk), "n_funded": len(funded_wk)}


if __name__ == "__main__":
    import backtest_retrieval_curator as brc
    wk_tk = harvest()
    allw = sorted(wk_tk)
    universe = {t for w in wk_tk.values() for t in w}
    print(f"pool={len(ARTS)} arts · superlative weeks={len(allw)} ({allw[0]}..{allw[-1]}) · "
          f"distinct superlative-tickers={len(universe)}")
    per = [len(wk_tk[w]) for w in allw]
    print(f"candidates/week: median={sorted(per)[len(per)//2]}  max={max(per)}  min={min(per)}")
    print()
    print("GEM COVERAGE — does the superlative harvest surface each gem, and when?")
    print(f"{'gem':<6}{'weeks named w/ superlative':<28}{'first':<12}{'total hits'}")
    for g in ["MP", "MU", "HL", "NEM", "GDX", "TSM", "INTC", "CIFR", "FRO", "BA", "DHT", "STNG"]:
        wks = [w for w in allw if g in wk_tk[w]]
        hits = sum(wk_tk[w].get(g, 0) for w in allw)
        print(f"{g:<6}{len(wks):<28}{(wks[0] if wks else '—'):<12}{hits}")

    print("\n\n=== STAGE 2: backtest (one portfolio over the whole superlative universe) ===")
    GEMS = ["MP", "MU", "HL", "NEM", "TSM", "INTC", "CIFR"]
    for variant, desc in [("A", "+20%/mo confirm"), ("B", "no confirm (optimizer decides)")]:
        r = run(variant)
        print(f"\n--- variant {variant}: {desc} ---")
        print(f"portfolio {r['ret']*100:+.0f}%  (peak {r['peak']/50000*100-100:+.0f}%)  vs SPY {r['spy_ret']*100:+.0f}%  "
              f"· {r['n_funded']} distinct names ever funded")
        print(f"{'gem':<6}{'funded wks':<12}{'held wks'}")
        for g in GEMS:
            print(f"{g:<6}{r['funded_wk'].get(g, 0):<12}{r['held_wk'].get(g, 0)}")
