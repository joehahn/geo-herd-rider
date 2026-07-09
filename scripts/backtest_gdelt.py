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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--window-cap", type=int, default=80)
    ap.add_argument("--wayback-cap", type=int, default=0, help="enrich only the top-N/week (0 = all in window-cap)")
    ap.add_argument("--trace", nargs="?", const="__default__", default=None,
                    help="log every LLM prompt/response + search query to <out>/transcript.jsonl (or PATH)")
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

    print("  pulling GDELT (date-indexed, resumable) ...", flush=True)
    gpool = gd.pool(firehose.GDELT_QUERIES, win_start, anchors[-1], chunk_days=7, per=80,
                    cache_path=cache_f, stats_path=stats)
    print(f"  GDELT pool: {len(gpool)} articles", flush=True)

    fm = load_financial_model(str(ROOT / "investor_profile.forward.md"))
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
        gslice = sorted(firehose._window(gpool, anch, 7),
                        key=lambda x: x.get("published_date", ""), reverse=True)[:a.window_cap]
        enrich_slice = gslice[:a.wayback_cap] if a.wayback_cap else gslice
        wayback.enrich(enrich_slice, wk, cache_path=enrich_cache, fetch=True, stats_path=stats)
        for x in gslice:
            x["engine"] = "gdelt"
        picks, nid = agent.process_week(cli, anch, gslice, events, retired, nid, i, curator_memory_weeks=memw)
        live = [p for p in picks if p["thesis_live"]]
        print(f"  {wk} ({i + 1}/{len(anchors)}): {len(gslice):3} arts -> "
              f"{[(p['ticker'], p['conviction']) for p in live] or 'none'}", flush=True)
        (OUT / "archive" / f"{wk}.json").write_text(json.dumps(
            {"week": wk, "model": model, "pool": gslice, "queries": [], "raw_results": [],
             "config": {k: fm.get(k) for k in CFG}}, indent=2, default=str))
        if live:
            for p in live:
                rows.append({"decision_ts": ts, "week": wk, "ticker": p["ticker"], "thesis": p["thesis"],
                             "thesis_live": True, "conviction": p["conviction"],
                             "evidence_urls": ";".join(p.get("evidence_urls", []) or [])})
        else:
            rows.append({"decision_ts": ts, "week": wk, "ticker": "", "thesis": "", "thesis_live": "",
                         "conviction": "", "evidence_urls": ""})
        flush()

    print(f"  DONE. events: {[(k, v['status'], sorted(v['vehicles'])) for k, v in events.items()]}", flush=True)


if __name__ == "__main__":
    main()
