#!/usr/bin/env python3
"""backfill_gdelt.py — SEEDLESS: pull GDELT+Wayback for the sandbox window, merge with the Tavily+Anthropic
augmented pools, run the curator, write a combined sandbox (data/forward_gdelt/).

Tests whether a richer, organic, date-indexed feed (GDELT reaches back + has high-impact events; Wayback
gives as-of-date ledes) makes the forward scout surface MORE / higher-impact events than Tavily+Anthropic
alone (which found 5). No seeds anywhere — GDELT is organic, seeds are a separate hand-added layer we omit.
"""
from __future__ import annotations

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


def main():
    load_dotenv()
    TAV = ROOT / "data" / "forward_augmented"
    OUT = ROOT / "data" / "forward_gdelt"
    (OUT / "archive").mkdir(parents=True, exist_ok=True)
    anchors = scan_anchors("2026-05-08", "2026-07-03", 7)[-8:]           # 8 weekly Fridays
    win_start = anchors[0] - pd.Timedelta(days=10)
    cache_f = ROOT / "data" / "windows" / "gdelt_pool_forward.json"
    stats = str(OUT / "retrieval_stats.json")                # GDELT+Wayback health, read by the dashboard
    enrich_cache = str(ROOT / "data" / "windows" / "wayback_forward.json")

    print("  pulling GDELT (general queries, weekly chunks, throttled) ...", flush=True)
    gpool = gd.pool(firehose.GDELT_QUERIES, win_start, anchors[-1], chunk_days=7, per=80,
                    cache_path=str(cache_f), stats_path=stats)
    print(f"  GDELT pool: {len(gpool)} articles", flush=True)

    fm = load_financial_model(str(ROOT / "investor_profile.forward.md"))
    model = resolve_curator_model(fm.get("model", "sonnet5"))[0]
    memw = int(fm.get("curator_memory_weeks", 8))
    cli = llm.make_client("anthropic", model)
    events, retired, nid = {}, {}, 0
    rows: list[dict] = []
    ts = datetime.now(timezone.utc).isoformat()
    for i, anch in enumerate(anchors):
        wk = anch.date().isoformat()
        gslice = sorted(firehose._window(gpool, anch, 7),
                        key=lambda x: x.get("published_date", ""), reverse=True)[:80]
        wayback.enrich(gslice, wk, cache_path=enrich_cache, fetch=True, stats_path=stats)   # as-of ledes
        for a in gslice:
            a["engine"] = "gdelt"
        tf = TAV / "archive" / f"{wk}.json"
        tav = json.loads(tf.read_text()).get("pool", []) if tf.exists() else []             # engine-tagged tavily/anthropic
        merged: dict = {}
        for a in tav + gslice:                                                              # dedup by url
            u = a.get("url")
            if u:
                merged.setdefault(u, a)
        pool = sorted(merged.values(), key=lambda x: (x.get("published_date") or ""), reverse=True)[:120]
        n_g = sum(1 for a in pool if a.get("engine") == "gdelt")
        picks, nid = agent.process_week(cli, anch, pool, events, retired, nid, i, curator_memory_weeks=memw)
        live = [p for p in picks if p["thesis_live"]]
        print(f"  week {wk}: {len(pool):3} arts ({n_g} gdelt) -> "
              f"{[(p['ticker'], p['conviction']) for p in live] or 'none'}", flush=True)
        (OUT / "archive" / f"{wk}.json").write_text(json.dumps(
            {"week": wk, "model": model, "pool": pool, "queries": [], "raw_results": [],
             "config": {k: fm.get(k) for k in ("model", "concentration_cap", "risk_aversion",
                        "lookback_period_days", "max_agents", "spy_agent_conviction",
                        "defensive_agent_conviction", "defensive_ticker", "rebalance_days")}},
            indent=2, default=str))
        if live:
            for p in live:
                rows.append({"decision_ts": ts, "week": wk, "ticker": p["ticker"], "thesis": p["thesis"],
                             "thesis_live": True, "conviction": p["conviction"],
                             "evidence_urls": ";".join(p.get("evidence_urls", []) or [])})
        else:
            rows.append({"decision_ts": ts, "week": wk, "ticker": "", "thesis": "", "thesis_live": "",
                         "conviction": "", "evidence_urls": ""})

    pd.DataFrame(rows).to_csv(OUT / "firehose_scans.csv", index=False)
    (OUT / "journal.json").write_text(json.dumps({
        "events": {k: {**v, "vehicles": sorted(v["vehicles"])} for k, v in events.items()},
        "retired": retired, "nid": nid, "week_seq": len(anchors)}, indent=2, default=str))
    print(f"  events: {[(k, v['status'], sorted(v['vehicles'])) for k, v in events.items()]}", flush=True)


if __name__ == "__main__":
    main()
