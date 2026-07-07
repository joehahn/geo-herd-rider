#!/usr/bin/env python3
"""forward_replay.py — replay the event-first engine WEEKLY over a sandbox's pre-gathered pools.

Combines every archived pool in a --sandbox dir (from one or more big-window `forward.py --scan
--rebalance-days N` gathers) into one dated article pool, then runs `agent.process_week` for each
weekly anchor spanning the pool — so the curator does WEEKLY event-detection + tracking over news
gathered in cheap big chunks. This is Phase C (replay on frozen inputs) + the payoff of the
gather/scout split: the gather is the expensive step, the per-week scout/agents are cheap.

Look-ahead caveat: a big-window LIVE gather can't cleanly fetch old news, so older weeks have thinner /
degraded pools — a throwaway prototype for testing + dashboard-drafting, never a clean result.

    python scripts/forward_replay.py --sandbox data/forward_proto --rebalance-days 7
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
import llm  # noqa: E402
from util import load_dotenv, scan_anchors  # noqa: E402
from optimizer import load_financial_model, resolve_curator_model  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandbox", required=True, help="sandbox dir with archive/*.json (gathered pools)")
    ap.add_argument("--rebalance-days", type=int, default=7, dest="rebalance_days")
    a = ap.parse_args(argv)
    load_dotenv()
    sb = Path(a.sandbox)

    # combine every archived pool (dated frozen articles), dedup by url
    pool: dict[str, dict] = {}
    for f in sorted((sb / "archive").glob("*.json")):
        for art in json.load(f.open()).get("pool", []):
            if art.get("published_date") and art.get("url"):
                pool.setdefault(art["url"], art)
    articles = list(pool.values())
    if not articles:
        print("  no dated pool articles in the sandbox archive — run a --scan gather first.", file=sys.stderr)
        return 1
    dates = sorted(x["published_date"][:10] for x in articles)
    lo, hi = dates[0], dates[-1]
    print(f"  combined pool: {len(articles)} articles, {lo} .. {hi}")

    anchors = scan_anchors(lo, hi, a.rebalance_days)          # weekly (W-FRI) anchors across the span
    fm = load_financial_model(str(ROOT / "investor_profile.forward.md"))
    model_id = resolve_curator_model(fm.get("model", "sonnet5"))[0]
    memw = int(fm.get("curator_memory_weeks", 8))
    cli = llm.make_client("anthropic", model_id)

    events: dict = {}
    retired: dict = {}
    nid = 0
    rows: list[dict] = []
    ts = datetime.now(timezone.utc).isoformat()
    print(f"  replaying {len(anchors)} weekly scans over the pool (model {model_id}) ...")
    for i, anch in enumerate(anchors):
        loi = (anch - pd.Timedelta(days=a.rebalance_days)).date().isoformat()
        hii = anch.date().isoformat()
        wk = sorted([x for x in articles if loi < x["published_date"][:10] <= hii],
                    key=lambda x: x["published_date"], reverse=True)[:80]
        picks, nid = agent.process_week(cli, anch, wk, events, retired, nid, i, curator_memory_weeks=memw)
        live = [p for p in picks if p["thesis_live"]]
        print(f"  week {hii}: {len(wk):3} arts -> {[(p['ticker'], p['conviction']) for p in live] or 'none'}")
        if live:
            for p in live:
                rows.append({"decision_ts": ts, "week": hii, "ticker": p["ticker"], "thesis": p["thesis"],
                             "thesis_live": True, "conviction": p["conviction"],
                             "evidence_urls": ";".join(p.get("evidence_urls", []) or [])})
        else:
            rows.append({"decision_ts": ts, "week": hii, "ticker": "", "thesis": "", "thesis_live": "",
                         "conviction": "", "evidence_urls": ""})

    pd.DataFrame(rows).to_csv(sb / "firehose_scans.csv", index=False)
    (sb / "journal.json").write_text(json.dumps({
        "events": {k: {**v, "vehicles": sorted(v["vehicles"])} for k, v in events.items()},
        "retired": retired, "nid": nid, "week_seq": len(anchors)}, indent=2, default=str))
    print(f"  wrote {len(anchors)}-week series -> {sb}/firehose_scans.csv + journal.json")
    print(f"  final events: {[(k, v['status'], sorted(v['vehicles'])) for k, v in events.items()] or 'NONE'}")


if __name__ == "__main__":
    raise SystemExit(main())
