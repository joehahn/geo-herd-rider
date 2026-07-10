#!/usr/bin/env python3
"""backfill_tavily.py — THROWAWAY: Tavily date-bounded N-week backfill + weekly process_week.

For each weekly anchor, gathers that week via Tavily (date-honoring, reaches OLD weeks unlike
Anthropic/Brave), runs `agent.process_week` (scout -> matcher -> event agents), accumulates the
journal, and writes the scan-log + journal + per-week archives into a sandbox. Then
`build_forward_dashboard.py` renders the real multi-week series.

Forward stays Anthropic/Brave; this is a throwaway backfill to prove the pipeline + build the
dashboard, using a live (date-honoring) search that is NOT GDELT.

    python scripts/backfill_tavily.py --sandbox data/forward_tavily --anchor 2026-07-03 --weeks 8
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
import forward_gather_tavily  # noqa: E402
import llm  # noqa: E402
from util import load_dotenv, scan_anchors  # noqa: E402
from optimizer import load_financial_model, resolve_curator_model  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandbox", required=True)
    ap.add_argument("--anchor", required=True, help="most recent week-ending Friday")
    ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--rebalance-days", type=int, default=7, dest="rebalance_days")
    a = ap.parse_args(argv)
    load_dotenv()
    fm = load_financial_model(str(ROOT / "investor_profile.forward.md"))
    model = resolve_curator_model(fm.get("event_agent_model") or fm.get("model") or "sonnet5")[0]
    memw = int(fm.get("curator_memory_weeks", 8))
    cli = llm.make_client("anthropic", model)              # scout/agents (web search OFF; pool is pre-gathered)
    sb = Path(a.sandbox)
    (sb / "archive").mkdir(parents=True, exist_ok=True)

    end = pd.Timestamp(a.anchor, tz="America/New_York")
    anchors = scan_anchors((end - pd.Timedelta(days=a.rebalance_days * a.weeks)).date().isoformat(),
                           end.date().isoformat(), a.rebalance_days)[-a.weeks:]
    print(f"  Tavily backfill: {len(anchors)} weeks, curator {model}")
    events, retired, nid = {}, {}, 0
    rows: list[dict] = []
    ts = datetime.now(timezone.utc).isoformat()
    for i, anch in enumerate(anchors):
        cap: dict = {}
        arts = forward_gather_tavily.gather(None, model, anch, a.rebalance_days, capture=cap,
                                            cap=int(fm.get("news_cap", 0)))
        picks, nid = agent.process_week(cli, anch, arts, events, retired, nid, i, curator_memory_weeks=memw)
        wk = anch.date().isoformat()
        live = [p for p in picks if p["thesis_live"]]
        print(f"  week {wk}: {len(arts):3} arts -> {[(p['ticker'], p['conviction']) for p in live] or 'none'}")
        (sb / "archive" / f"{wk}.json").write_text(json.dumps(
            {"week": wk, "model": model, "pool": arts, "queries": cap.get("queries", []),
             "raw_results": cap.get("results", []), "config": {k: fm.get(k) for k in
             ("model", "concentration_cap", "risk_aversion", "lookback_period_days", "max_agents",
              "spy_agent_conviction", "defensive_agent_conviction", "defensive_ticker", "rebalance_days")}},
            indent=2, default=str))
        if live:
            for p in live:
                rows.append({"decision_ts": ts, "week": wk, "ticker": p["ticker"], "thesis": p["thesis"],
                             "thesis_live": True, "conviction": p["conviction"],
                             "evidence_urls": ";".join(p.get("evidence_urls", []) or [])})
        else:
            rows.append({"decision_ts": ts, "week": wk, "ticker": "", "thesis": "", "thesis_live": "",
                         "conviction": "", "evidence_urls": ""})

    pd.DataFrame(rows).to_csv(sb / "firehose_scans.csv", index=False)
    (sb / "journal.json").write_text(json.dumps({
        "events": {k: {**v, "vehicles": sorted(v["vehicles"])} for k, v in events.items()},
        "retired": retired, "nid": nid, "week_seq": len(anchors)}, indent=2, default=str))
    print(f"  wrote {len(anchors)}-week series -> {sb}/  |  events: "
          f"{[(k, v['status'], sorted(v['vehicles'])) for k, v in events.items()] or 'NONE'}")


if __name__ == "__main__":
    main()
