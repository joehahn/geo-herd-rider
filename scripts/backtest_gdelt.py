#!/usr/bin/env python3
"""backtest_gdelt.py — SEEDLESS, look-ahead-clean continuous backtest over an arbitrary window.

Pulls GDELT (date-indexed) + Wayback (as-of-date ledes) — NO live search (no future-leak), NO seeds. Runs
the current curator week by week. Writes each week's archive PLUS an incremental scan-log/journal after every
week, so a partial dashboard can be built while the slow Wayback enrich is still running. Fully RESUMABLE:
GDELT/Wayback caches survive interruption, and on restart it reloads the journal state and skips scanned weeks.

    python scripts/backtest_gdelt.py --start 2024-04-23 --end 2026-07-02 --out data/backtest_gdelt
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pandas as pd  # noqa: E402
import agent  # noqa: E402
import firehose  # noqa: E402
import gdelt as gd  # noqa: E402
import llm  # noqa: E402
import wayback  # noqa: E402
from util import load_dotenv, scan_anchors  # noqa: E402
from optimizer import load_financial_model, resolve_curator_model  # noqa: E402

CFG = ("model", "concentration_cap", "risk_aversion", "lookback_period_days", "max_agents",
       "spy_agent_conviction", "defensive_agent_conviction", "defensive_ticker", "rebalance_days")


def live_enrich(articles, workers: int = 12) -> list:
    """FAST but LOOK-AHEAD-RISKY: fetch each article NOW and use its <meta description> as the lede.
    The live page may have been edited since publication (esp. developing-event stories) — that bias
    is what the 3-stage ablation measures against the Wayback (as-of) baseline. Dead/paywalled URLs
    just stay headline-only."""
    import concurrent.futures as cf
    import re as _re
    import urllib.request as _u
    def _one(a):
        try:
            req = _u.Request(a["url"], headers={"User-Agent": "Mozilla/5.0 (geo-herd-rider live-enrich)"})
            html = _u.urlopen(req, timeout=15).read(200000).decode("utf-8", "ignore")
            m = _re.search(r'<meta[^>]+(?:name|property)=["\']' r'(?:og:)?description["\'][^>]+content=["\']([^"\']{40,500})', html, _re.I)
            if m:
                a["snippet"] = _re.sub(r"\s+", " ", m.group(1)).strip()[:280]
        except Exception:
            pass
        return a
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_one, articles))
    return articles


def rebuild_dashboard(sandbox, out: str, wk: str) -> None:
    """Incremental: rebuild THIS week's as-of page + refresh the All-weeks index, so the weekly
    dashboards update as each week's GDELT+curator completes. Wrapped so a render error never aborts
    the pull (the archives are already flushed; the dashboard can be rebuilt later regardless)."""
    try:
        import build_forward_dashboard as bfd
        bfd.build(str(sandbox), out, wk, [])       # this week's frozen as-of page
        bfd.build(str(sandbox), out, None, [])     # refresh index/landing + latest-week page
        print(f"    dashboard updated -> {out} (through {wk})", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"    dashboard rebuild skipped ({wk}): {e}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--window-cap", type=int, default=80,
                    help="max articles/week the scout reads (most-recent kept); 0 = UNCAPPED (keep all)")
    ap.add_argument("--wayback-cap", type=int, default=0, help="enrich only the top-N/week (0 = all in window-cap)")
    ap.add_argument("--trace", nargs="?", const="__default__", default=None,
                    help="log every LLM prompt/response + search query to <out>/transcript.jsonl (or PATH)")
    ap.add_argument("--enrich", choices=("none", "live", "wayback"), default="wayback",
                    help="none=GDELT headlines only (fast, clean); live=fetch article NOW (fast, edit-bias risk); wayback=as-of ledes (slow, clean)")
    ap.add_argument("--by-week", action="store_true",
                    help="pull ALL beats for week i BEFORE week i+1, processing each as it completes "
                         "(enables incremental dashboards during a long pull); default = whole-window up-front")
    ap.add_argument("--no-pull", action="store_true",
                    help="skip GDELT fetching entirely; curate on the EXISTING <out>/gdelt_pool.json as-is "
                         "(prototype dashboards on a partial pool without disturbing a live pull)")
    ap.add_argument("--dashboard", default=None,
                    help="after EACH week, rebuild the forward dashboard at this dir (weekly dbs update as "
                         "each week's GDELT+curator completes); pairs naturally with --by-week")
    a = ap.parse_args(argv)
    load_dotenv()
    OUT = Path(a.out)
    (OUT / "archive").mkdir(parents=True, exist_ok=True)
    if a.trace is not None:
        import trace
        tp = str(OUT / "transcript.jsonl") if a.trace == "__default__" else a.trace
        trace.enable(tp)
        print(f"  TRACE ON -> {tp} (every LLM prompt/response + search query)", flush=True)
    anchors = scan_anchors(a.start, a.end, 7)
    win_start = anchors[0] - pd.Timedelta(days=10)
    cache_f = str(OUT / "gdelt_pool.json")
    stats = str(OUT / "retrieval_stats.json")
    enrich_cache = str(OUT / "wayback.json")
    print(f"  {len(anchors)} weeks {anchors[0].date()} .. {anchors[-1].date()}", flush=True)

    gpool = None
    if a.no_pull:                                            # curate on whatever's already cached (partial-pool prototype)
        d = json.loads(Path(cache_f).read_text()) if Path(cache_f).exists() else {}
        gpool = list(d.get("articles", []))
        print(f"  NO-PULL: {len(gpool)} cached articles ({d.get('progress', '?')})", flush=True)
    elif not a.by_week:                                      # whole-window up-front (default)
        print("  pulling GDELT (whole-window, date-indexed, resumable) ...", flush=True)
        gpool = gd.pool(firehose.GDELT_QUERIES, win_start, anchors[-1], chunk_days=7, per=80,
                        cache_path=cache_f, stats_path=stats)
        print(f"  GDELT pool: {len(gpool)} articles", flush=True)
    else:                                                    # per-week: pull inside the loop (all beats/week, then process)
        print("  BY-WEEK pull: all beats per week, each processed before the next (incremental)", flush=True)

    fm = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    model = resolve_curator_model(fm.get("model", "sonnet5"))[0]
    memw = int(fm.get("curator_memory_weeks", 8))
    cli = llm.make_client("anthropic", model)

    # RESUME: reload journal state + skip weeks already scanned
    events, retired, nid, rows, done = {}, {}, 0, [], set()
    jf, sf = OUT / "journal.json", OUT / "firehose_scans.csv"
    if jf.exists():
        j = json.loads(jf.read_text())
        events = {k: {**v, "vehicles": set(v["vehicles"])} for k, v in j.get("events", {}).items()}
        retired, nid = j.get("retired", {}), int(j.get("nid", 0))
        done = {p.stem for p in (OUT / "archive").glob("*.json")}
        if sf.exists():
            rows = pd.read_csv(sf).fillna("").to_dict("records")
        print(f"  RESUME: {len(done)} weeks done, {len(events)} events in state", flush=True)

    ts = datetime.now(timezone.utc).isoformat()

    def flush():                                             # incremental -> partial dashboards buildable anytime
        pd.DataFrame(rows).to_csv(sf, index=False)
        (OUT / "journal.json").write_text(json.dumps(
            {"events": {k: {**v, "vehicles": sorted(v["vehicles"])} for k, v in events.items()},
             "retired": retired, "nid": nid, "week_seq": len(anchors)}, indent=2, default=str))

    for i, anch in enumerate(anchors):
        wk = anch.date().isoformat()
        if wk in done:
            continue
        if a.by_week and not a.no_pull:                      # pull THIS week's beats now, then process it
            gpool = gd.pool(firehose.GDELT_QUERIES, anch - pd.Timedelta(days=7), anch, chunk_days=7,
                            per=80, cache_path=cache_f, stats_path=stats)   # exactly the week -> 1 chunk, no overlap waste
        _raw = sorted(firehose._window(gpool, anch, 7),
                      key=lambda x: x.get("published_date", ""), reverse=True)
        gslice = _raw[:a.window_cap] if a.window_cap else _raw   # window_cap=0 -> UNCAPPED (keep all)
        if a.window_cap and len(_raw) > a.window_cap:            # surface silent drops, don't hide them
            print(f"    !! window-cap dropped {len(_raw) - a.window_cap} of {len(_raw)} articles "
                  f"(oldest-in-window) at {wk}", flush=True)
        enrich_slice = gslice[:a.wayback_cap] if a.wayback_cap else gslice
        if a.enrich == "wayback":
            wayback.enrich(enrich_slice, wk, cache_path=enrich_cache, fetch=True, stats_path=stats)
        elif a.enrich == "live":
            live_enrich(enrich_slice)
        # a.enrich == "none": GDELT headlines only, no enrichment
        for x in gslice:
            x["engine"] = "gdelt"
        picks, nid = agent.process_week(cli, anch, gslice, events, retired, nid, i, curator_memory_weeks=memw)
        live = [p for p in picks if p["thesis_live"]]
        print(f"  {wk} ({i + 1}/{len(anchors)}): {len(gslice):3} arts -> "
              f"{[(p['ticker'], p['conviction']) for p in live] or 'none'}", flush=True)
        (OUT / "archive" / f"{wk}.json").write_text(json.dumps(
            {"week": wk, "model": model, "pool": gslice, "queries": [], "raw_results": [],
             "config": {**{k: fm.get(k) for k in CFG}, "window_cap": a.window_cap}}, indent=2, default=str))
        if live:
            for p in live:
                rows.append({"decision_ts": ts, "week": wk, "ticker": p["ticker"], "thesis": p["thesis"],
                             "thesis_live": True, "conviction": p["conviction"],
                             "evidence_urls": ";".join(p.get("evidence_urls", []) or [])})
        else:
            rows.append({"decision_ts": ts, "week": wk, "ticker": "", "thesis": "", "thesis_live": "",
                         "conviction": "", "evidence_urls": ""})
        flush()
        if a.dashboard:                                      # weekly db updates as each week completes
            rebuild_dashboard(OUT, a.dashboard, wk)

    print(f"  DONE. events: {[(k, v['status'], sorted(v['vehicles'])) for k, v in events.items()]}", flush=True)


if __name__ == "__main__":
    main()
