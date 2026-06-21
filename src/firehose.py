"""firehose.py — the simplified solution: monitor the news firehose for called-out gems.

The pivot away from the causal decision-tree. We are not screening all tickers to discover gems;
the financial press already does that and prints the ticker by name (CNBC/ETF.com/24-7 named BWET
weeks before it tripled). So: each weekly run, read the firehose (news search + Trump posts,
look-ahead-safe), keep the tickers the press explicitly calls out as thesis-driven movers that are
still EARLY / under-the-radar (room to run), hand them to the optimizer as the watchlist, hold
while the thesis stays live, and drop before the crest when it goes consensus/decaying.

Entry, sizing, exit:
  - ENTRY: a ticker the press names as an early thesis-driven mover (stage 'early'/'building').
  - SIZING: the reused mean-variance optimizer + investor_profile knobs.
  - EXIT: it falls out of the weekly watchlist — the press stops calling it a live/early buy, or
    flags it 'crested'. (The "when do we drop BWET?" question, answered by the firehose itself.)

Reuses: trump_feed, the Anthropic web_search, curator._optimized_weights (sizing), score (prices,
entry timing, T_UPDATE_DAYS), costs, and the investor_profile knobs. No causal ladder.

    python src/firehose.py --start 2026-02-13 --end 2026-06-18 --model claude-opus-4-8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
import trump_feed  # noqa: E402
import costs  # noqa: E402
import score  # noqa: E402
import curator  # noqa: E402
from optimizer import load_financial_model  # noqa: E402
from util import load_dotenv as _load_dotenv, news_domains, weekly_anchors as _weekly_anchors, MAX_TEXT  # noqa: E402

MODEL = "claude-opus-4-8"
WORKERS = 8

SCAN_SYSTEM = """You are a markets desk reading the week's news firehose to find HIDDEN GEMS the
financial press is already calling out — tickers a journalist explicitly names as a thesis-driven
mover, ideally while it is still EARLY / under-the-radar (room to run), not yet consensus.

You read: (1) this week's Donald Trump Truth Social posts (given), and (2) the news you SEARCH.
SEARCH the week's market coverage for stories that NAME a specific US-listed ticker or fund as a
standout trade on a live thesis (geopolitics, energy/shipping, tariffs, Fed, a sector catalyst).
Append 'before:<cron date>' to every query and DISCARD anything dated after it (no look-ahead).

KEEP a ticker only if the PRESS explicitly names it as a thesis-driven mover (do not infer your
own picks). For each, classify the stage of how far the move/coverage has progressed:
  early    — just being noticed; "under the radar", "no one's talking about it", first write-ups
  building — gaining coverage, thesis intact, more room
  consensus— widely covered now; most of the move likely done
  crested  — thesis decaying/ending (the catalyst is reversing/resolving) -> EXIT signal

You forecast NOTHING — no magnitude, target, weight, or probability. Only: which tickers is the
press naming, on what thesis, at what stage.

Output ONLY JSON: {"picks":[{"ticker":"BWET","thesis":"<=12 words","stage":"early|building|
consensus|crested","evidence_urls":["news URLs"]}]}. Empty is fine: {"picks":[]}."""


def _extract_json(text: str) -> dict:
    t = text.strip()
    if "```" in t:
        for chunk in reversed(t.split("```")):
            c = chunk.strip()
            if c.startswith("json"):
                c = c[4:].strip()
            if c.startswith("{"):
                return json.loads(c)
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e > s:
        return json.loads(t[s:e + 1])
    raise ValueError("no JSON object in model output")


def scan(client, model: str, anchor: pd.Timestamp, posts: pd.DataFrame,
         domains: list[str]) -> list[dict]:
    """Firehose scan as of `anchor` (look-ahead-safe). Returns the press-named gems."""
    lines = [f"[{r.created_at.tz_convert('America/New_York').date()}] {r.text[:MAX_TEXT]}"
             for r in posts.itertuples()]
    prefer = ", ".join(domains) if domains else "major financial news outlets"
    user = (f"Week ending {anchor.date()} (use before:{anchor.date()} on every search).\n"
            f"This week's high-reach posts:\n\n" + "\n".join(lines or ["(none)"])
            + f"\n\nSearch the week's market news (prefer: {prefer}). Which tickers is the press "
            "naming as thesis-driven movers, and at what stage? Output the JSON.")
    tools = [{"type": "web_search_20260209", "name": "web_search"}]
    messages = [{"role": "user", "content": user}]
    kw = {"model": model, "max_tokens": 3000, "system": SCAN_SYSTEM, "tools": tools, "messages": messages}
    tally = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "web_searches": 0}
    text = ""
    for _ in range(6):
        resp = client.messages.create(**kw)
        u = costs.extract(resp.usage)
        for k in tally:
            tally[k] += u.get(k, 0)
        text = "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    costs.record("firehose", model, f"scan-{anchor.date()}", tally)
    try:
        picks = _extract_json(text).get("picks", [])
    except Exception:  # noqa: BLE001
        return []
    for p in picks:
        p["ticker"] = str(p.get("ticker", "")).strip().upper()
        p["anchor"] = anchor
    return [p for p in picks if p["ticker"]]


FIXTURE_SYSTEM = """You are a markets desk reading the financial press to find HIDDEN GEMS — a
ticker a journalist explicitly NAMES as a thesis-driven mover. Below is the press coverage
available as of this week (and nothing later). For each US-listed ticker the press names, decide:

  thesis        — the driving catalyst, <=12 words (e.g. "Iran war spikes tanker freight rates").
  thesis_live   — TRUE while that catalyst is still ACTIVE / UNRESOLVED as of this week (war
                  ongoing, chokepoint disrupted); FALSE only once the press says it is
                  RESOLVING or RESOLVED (ceasefire signed, Hormuz reopened, rates rolling over).
                  This is the HOLD/EXIT switch — hold while live, drop when it goes false.
  crowding      — how crowded the TRADE is, INFO only (does not drive hold/exit):
                  early (under-the-radar/little-known/small AUM) | building | consensus
                  (mainstream, "everyone piling in") | crested (rolling over).

Do NOT equate a big % gain with "late": a fund can be up huge and still be early/under-owned. Judge
thesis_live on the CATALYST's status, not the size of the move. You forecast NOTHING — no
magnitude, target, weight, or probability. Output ONLY JSON: {"picks":[{"ticker":"BWET","thesis":
"<=12 words","thesis_live":true,"crowding":"early|building|consensus|crested","evidence_urls":
["..."]}]}."""


def _fixture_articles(path: str) -> list[dict]:
    return json.loads(Path(path).read_text()).get("articles", [])


def scan_fixture(client, model: str, anchor: pd.Timestamp, articles: list[dict]) -> list[dict]:
    """Look-ahead-clean scan against a fixed article set (perfect-retrieval simulation).
    Only articles published on/before the anchor are visible."""
    cut = anchor.date().isoformat()
    seen = [a for a in articles if str(a.get("published_date", ""))[:10] <= cut]
    if not seen:
        return []
    block = "\n".join(f"[{a['published_date']} | {a.get('source','')}] {a.get('title','')} — "
                      f"{a.get('snippet','')} ({a.get('url','') or 'no url'})" for a in seen)
    user = (f"Week ending {cut}. Press coverage available as of this week:\n\n{block}\n\n"
            "Which tickers is the press naming, on what thesis, at what stage? Output the JSON.")
    resp = client.messages.create(model=model, max_tokens=1500, system=FIXTURE_SYSTEM,
                                  messages=[{"role": "user", "content": user}])
    costs.record("firehose", model, f"fixture-{cut}", costs.extract(resp.usage))
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        picks = _extract_json(text).get("picks", [])
    except Exception:  # noqa: BLE001
        return []
    for p in picks:
        p["ticker"] = str(p.get("ticker", "")).strip().upper()
        p["anchor"] = anchor
    return [p for p in picks if p["ticker"]]


def run_scans(start, end, lookback_days, model, workers, fixture=None) -> dict[pd.Timestamp, list[dict]]:
    import anthropic
    client = anthropic.Anthropic()
    anchors = _weekly_anchors(start, end)
    if fixture:
        articles = _fixture_articles(fixture)
        print(f"Firehose: FIXTURE scan of {len(anchors)} weeks vs {len(articles)} articles "
              f"({model}); retrieval assumed perfect, mechanics only.", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            pairs = list(zip(anchors, ex.map(lambda a: scan_fixture(client, model, a, articles), anchors)))
        return dict(sorted(pairs))
    posts = trump_feed.candidate_posts(start, end)
    domains = news_domains()
    print(f"Firehose: scanning {len(anchors)} weeks via {model} ...", file=sys.stderr)

    def one(a):
        lo = a - pd.Timedelta(days=lookback_days)
        wk = posts[(posts["created_at"] > lo) & (posts["created_at"] <= a)]
        return a, scan(client, model, a, wk, domains)

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for a, picks in ex.map(one, anchors):
            out[a] = picks
    return dict(sorted(out.items()))


def _held(p: dict) -> bool:
    """Hold rule = the user's thesis-decay exit: in the book while the catalyst is live."""
    return bool(p.get("thesis_live", True))


OVERLAY, OVERLAY_ANCHOR = "BWET", "2026-02-20"  # the motivating gem + carrier->W.Med transit


def backtest(scans: dict, fm: dict, capital: float = 50_000.0, daily: bool = False) -> dict:
    """Weekly-rebalanced portfolio from the firehose watchlist vs SPY. With daily=True, also
    returns a daily value/allocation series (weekly weights held across days) for the dashboard."""
    lookback = int(fm.get("lookback_period_days", curator.BACKTEST_LOOKBACK_DAYS))
    anchors = list(scans)
    watch = {a: sorted({p["ticker"] for p in scans[a] if _held(p)}) for a in anchors}
    tickers = {score.BENCHMARK, OVERLAY} | {t for w in watch.values() for t in w}
    start = (anchors[0] - pd.Timedelta(days=lookback + 14)).strftime("%Y-%m-%d")
    end = (anchors[-1] + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
    panel = score.fetch_panel(sorted(tickers), start, end, use_cache=False)
    days = panel[score.BENCHMARK].dropna().index

    # rebalance trading day for each anchor (anchor close + T_UPDATE_DAYS), and that week's weights
    reb, week_w = [], {}
    for k, a in enumerate(anchors):
        i = score.entry_index(days, a.strftime("%Y-%m-%dT%H:%M:%S%z"), fm.get("t_update_days"))
        reb.append(None if i is None else i)
        if i is not None:
            wl = watch[a]
            week_w[k] = (curator._optimized_weights(wl, panel, days[i], fm, lookback) or {}) if wl else {}

    value, spyval, log = capital, capital, []
    rows = [{"date": str(days[reb[0]].date()) if reb[0] else str(anchors[0].date()),
             "value": capital, "spy": capital, "held": ""}]
    for k in range(len(anchors) - 1):
        i, j = reb[k], reb[k + 1]
        if i is None or j is None or j <= i:
            continue
        d0, d1, w = days[i], days[j], week_w.get(k, {})
        ret = sum(w.get(t, 0) * (panel.loc[d1, t] / panel.loc[d0, t] - 1)
                  for t in w if pd.notna(panel.loc[d0, t]) and pd.notna(panel.loc[d1, t]))
        value *= (1 + ret)
        spyval *= panel.loc[d1, score.BENCHMARK] / panel.loc[d0, score.BENCHMARK]
        held = ";".join(f"{t}:{w[t]:.2f}" for t in sorted(w, key=lambda x: -w[x]))
        rows.append({"date": str(d1.date()), "value": round(value, 2), "spy": round(spyval, 2),
                     "held": held})
        log.append({"week": str(anchors[k].date()), "watchlist": ";".join(watch[anchors[k]]),
                    "weights": held, "week_return": round(ret, 4)})
    out = {"final": value, "spy_final": spyval, "rows": rows, "log": log, "weeks": len(anchors)}
    if daily:
        out["daily"] = _daily_series(panel, days, reb, week_w, capital)
    return out


def _daily_series(panel, days, reb, week_w, capital) -> dict | None:
    """Daily value/alloc: hold each week's weights from its rebalance day until the next."""
    starts = [r for r in reb if r is not None]
    if not starts:
        return None
    d_idx = days[starts[0]:]
    seg = {reb[k]: week_w.get(k, {}) for k in week_w if reb[k] is not None}  # pos -> weights
    daily_ret = panel.pct_change()
    all_t = sorted({t for w in seg.values() for t in w})
    alloc = pd.DataFrame(0.0, index=d_idx, columns=all_t)
    cur, val, values = {}, capital, []
    for n, d in enumerate(d_idx):
        pos = days.get_loc(d)
        if pos in seg:
            cur = seg[pos]
        if n > 0:
            val *= 1 + sum(cur.get(t, 0) * daily_ret.loc[d, t] for t in cur
                           if pd.notna(daily_ret.loc[d, t]))
        values.append(round(val, 2))
        for t in cur:
            alloc.loc[d, t] = cur[t]
    spy = panel[score.BENCHMARK].reindex(d_idx).ffill()
    spy_val = [round(capital * v, 2) for v in (spy / spy.iloc[0]).tolist()]
    overlay = None
    if OVERLAY in panel.columns:
        ov = panel[OVERLAY].reindex(d_idx).ffill()
        ai = next((i for i, d in enumerate(d_idx) if d >= pd.Timestamp(OVERLAY_ANCHOR)), None)
        if ai is not None and pd.notna(ov.iloc[ai]) and ov.iloc[ai] > 0:
            scale = values[ai] / float(ov.iloc[ai])
            overlay = [None if pd.isna(v) else round(float(v) * scale, 2) for v in ov.tolist()]
    alloc = alloc.loc[:, (alloc.abs().sum() > 1e-9)]
    cash = [max(0.0, round(1 - float(alloc.loc[d].sum()), 4)) for d in d_idx]
    return {"dates": [d.strftime("%Y-%m-%d") for d in d_idx], "value": values, "spy": spy_val,
            "overlay": overlay, "overlay_ticker": OVERLAY, "overlay_anchor": OVERLAY_ANCHOR,
            "alloc": {t: [round(x, 4) for x in alloc[t]] for t in alloc.columns}, "cash": cash}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2026-02-13")
    ap.add_argument("--end", default="2026-06-18")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--capital", type=float, default=50_000.0)
    ap.add_argument("--scan-only", action="store_true", help="print the weekly scans, skip backtest")
    ap.add_argument("--fixture", default=None,
                    help="path to a fixed article set (perfect-retrieval mechanics test, no live search)")
    ap.add_argument("--out", default=str(REPO_ROOT / "data" / "windows" / "firehose_scans.json"))
    args = ap.parse_args(argv)

    _load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        return 2
    fm = load_financial_model(str(REPO_ROOT / "investor_profile.md"))
    lookback = int(fm.get("news_lookback_days", 7))

    scans = run_scans(args.start, args.end, lookback, args.model, args.workers, fixture=args.fixture)
    serial = {str(a.date()): scans[a] for a in scans}
    for v in serial.values():
        for p in v:
            p.pop("anchor", None)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(serial, indent=2, default=str))

    print("\n=== weekly firehose picks (press-named gems) ===")
    for a in scans:
        live = [f"{p['ticker']}[{'LIVE' if p.get('thesis_live', True) else 'EXIT'},"
                f"{p.get('crowding','?')}]" for p in scans[a]]
        print(f"  {a.date()}: {', '.join(live) if live else '—'}")
    if args.scan_only:
        return 0

    bt = backtest(scans, fm, args.capital)
    print(f"\n=== weekly-rebalanced firehose book vs SPY ({bt['weeks']} weeks) ===")
    print(f"  firehose: ${args.capital:,.0f} -> ${bt['final']:,.0f} "
          f"({bt['final']/args.capital-1:+.1%})")
    print(f"  SPY:      ${args.capital:,.0f} -> ${bt['spy_final']:,.0f} "
          f"({bt['spy_final']/args.capital-1:+.1%})")
    # when did BWET enter / exit?
    bwet = [r["week"] for r in bt["log"] if "BWET" in r["watchlist"]]
    if bwet:
        print(f"  BWET held weeks: {bwet[0]} .. {bwet[-1]} ({len(bwet)} weeks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
