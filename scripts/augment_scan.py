#!/usr/bin/env python3
"""augment_scan.py — THROWAWAY: merge the Anthropic gather into the Tavily weekly pools, then re-scan.

Tests whether adding Anthropic/Brave's niche coverage (which surfaced DRAM) to Tavily's dense-mainstream
pools yields sharper events. Anthropic only reaches Jun 8 -> Jul 3, so the May weeks are unchanged. Writes
to data/forward_augmented/ (throwaway); build_forward_dashboard renders it.
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
import llm  # noqa: E402
from util import load_dotenv, scan_anchors  # noqa: E402
from optimizer import load_financial_model, resolve_curator_model  # noqa: E402


def main():
    load_dotenv()
    TAV = ROOT / "data" / "forward_tavily"
    OUT = ROOT / "data" / "forward_augmented"
    (OUT / "archive").mkdir(parents=True, exist_ok=True)
    anth = [a for a in json.loads((ROOT / "data/forward_proto/archive/2026-07-03.json").read_text()).get("pool", [])
            if a.get("published_date") and a.get("url")]
    print(f"  Anthropic pool: {len(anth)} articles "
          f"({min(a['published_date'][:10] for a in anth)}..{max(a['published_date'][:10] for a in anth)})")

    fm = load_financial_model(str(ROOT / "investor_profile.forward.md"))
    model = resolve_curator_model(fm.get("model", "sonnet5"))[0]
    memw = int(fm.get("curator_memory_weeks", 8))
    cli = llm.make_client("anthropic", model)
    anchors = scan_anchors("2026-05-08", "2026-07-03", 7)[-8:]
    events, retired, nid = {}, {}, 0
    rows: list[dict] = []
    ts = datetime.now(timezone.utc).isoformat()
    anth_urls = {a["url"] for a in anth}
    for i, anch in enumerate(anchors):
        wk, lo = anch.date().isoformat(), (anch - pd.Timedelta(days=7)).date().isoformat()
        tf = TAV / "archive" / f"{wk}.json"
        tav = json.loads(tf.read_text()).get("pool", []) if tf.exists() else []
        anth_wk = [a for a in anth if lo < a["published_date"][:10] <= wk]
        merged: dict = {}
        for a in anth_wk + tav:                 # Anthropic FIRST so its niche pieces survive dedup + cap
            merged.setdefault(a["url"], a)
        pool = sorted(merged.values(), key=lambda x: x["published_date"], reverse=True)[:100]
        n_anth = sum(1 for a in pool if a["url"] in anth_urls)
        dram = any("dram" in (a.get("title", "") + a.get("url", "")).lower()
                   or "roundhill" in (a.get("title", "") + a.get("url", "")).lower() for a in pool)
        picks, nid = agent.process_week(cli, anch, pool, events, retired, nid, i, curator_memory_weeks=memw)
        live = [p for p in picks if p["thesis_live"]]
        print(f"  week {wk}: {len(pool):3} arts ({n_anth} anthropic, DRAM-in-pool={dram}) -> "
              f"{[(p['ticker'], p['conviction']) for p in live] or 'none'}")
        (OUT / "archive" / f"{wk}.json").write_text(json.dumps(
            {"week": wk, "model": model, "pool": pool, "queries": [], "raw_results": [],
             "config": {k: fm.get(k) for k in ("model", "concentration_cap", "risk_aversion", "lookback_period_days",
                        "max_agents", "spy_agent_conviction", "defensive_agent_conviction", "defensive_ticker",
                        "rebalance_days")}}, indent=2, default=str))
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
    print(f"  events: {[(k, v['status'], sorted(v['vehicles'])) for k, v in events.items()]}")


if __name__ == "__main__":
    main()
