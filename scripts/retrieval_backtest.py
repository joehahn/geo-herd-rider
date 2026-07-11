"""retrieval_backtest.py — bi-weekly Tavily retrieval backtest over a fixed span.

Answers the ONLY question a retrospective retrieval test can honestly answer: "running the SAME generic
beat sweep the live forward firehose runs (no ticker names), was each known backtest gem RETRIEVABLE, by
name, EARLY enough to act on?" Walks contiguous 14-day windows across [START, END], runs the aligned
two-pass gather (forward_gather_tavily — gem beats + specialty allowlist, coverage beats + mill blocklist)
at each anchor, accumulates one deduped pool, then runs gem_detect over it.

This is a BACKTEST OF KNOWN WINNERS on look-ahead-leaky web search (CLAUDE.md #4/#6): a positive tells you
the ticker was retrievable early, NOT that the live firehose would surface it from the noise. It's an upper
bound; the forward paper trade is the verdict. END defaults to 2026-07-07 (forward day-1) so this backtest
timeline butts directly against the live forward series.

Cost is tracked and written into the result (wall seconds, Tavily credits = search calls, LLM $=0 — the
Tavily gather uses no LLM) so a report/dashboard can show it. Checkpointed/resumable; paces itself under
Tavily's rate limiter via TAVILY_MIN_INTERVAL (search.py's global pacer).

Usage:  python scripts/retrieval_backtest.py [--start 2025-01-01] [--end 2026-07-07] [--lookback 14]
Output: data/retrieval_backtest.json   (feeds scripts/build_retrieval_dashboard.py)
"""
from __future__ import annotations
import argparse
import collections
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
from util import load_dotenv  # noqa: E402

load_dotenv()
os.environ.setdefault("TAVILY_MIN_INTERVAL", "0.25")   # ~4 req/s — stay under Tavily's rate limiter
import pandas as pd  # noqa: E402
import search  # noqa: E402
import trace  # noqa: E402
import gem_detect  # noqa: E402

OUT = REPO / "data" / "retrieval_backtest.json"


def _count_searches():
    """Wrap search.search to count Tavily API calls (= credits) before gather imports the name."""
    orig, cnt = search.search, {"n": 0}

    def wrapped(*a, **k):
        cnt["n"] += 1
        return orig(*a, **k)
    search.search = wrapped
    return cnt


def run(start: str, end: str, lookback: int, ckpt: Path) -> dict:
    cnt = _count_searches()
    trace.log = lambda *a, **k: None
    import forward_gather_tavily as t          # picks up the wrapped search.search

    anchors, d = [], pd.Timestamp(end)
    while d.date().isoformat() > start:
        anchors.append(d)
        d -= pd.Timedelta(days=lookback)
    anchors = list(reversed(anchors))          # last window ends exactly at END (forward day-1)

    pool, wcount = {}, {}
    if ckpt.exists():
        s = json.loads(ckpt.read_text())
        pool = {a["url"]: a for a in s["arts"]}
        wcount = s["wcount"]
        cnt["n"] = s["searches"]
    done = {k for k, v in wcount.items() if v > 0}   # retry empties (0 = a prior rate-limit blackout)

    t0 = time.time()
    for a in anchors:
        ak = a.date().isoformat()
        if ak in done:
            continue
        arts = t.gather(None, None, a, lookback, workers=4)
        for art in arts:
            ex = pool.get(art["url"])
            if ex is None:
                pool[art["url"]] = art
            else:
                for q in art.get("queries", []):
                    if q not in ex.setdefault("queries", []):
                        ex["queries"].append(q)
        wcount[ak] = len(arts)
        ckpt.write_text(json.dumps({"arts": list(pool.values()), "wcount": wcount, "searches": cnt["n"]}))
    elapsed = time.time() - t0
    return _summarize(list(pool.values()), wcount, cnt["n"], elapsed, len(anchors), start, end)


def _summarize(arts, wcount, credits, elapsed, nwin, start, end) -> dict:
    det = gem_detect.detect(arts)
    tick, pat = collections.Counter(), re.compile(r"\$([A-Z]{1,5})\b|\((?:NYSE|NASDAQ|NYSEARCA)[:\s]+([A-Z]{1,5})\)")
    for a in arts:
        for m in pat.findall(a.get("title", "") + " " + a.get("snippet", "")):
            s = m[0] or m[1]
            if s:
                tick[s] += 1
    detection, hits = {}, {}
    for g, r in det.items():
        bn, th = r["by_name"], r["thesis"]
        ebn = bn[0]["published_date"] if bn else None
        peak = gem_detect.PEAK[g]
        detection[g] = {"form": gem_detect.GEMS[g]["form"], "by_name": len(bn), "thesis": len(th),
                        "peak": peak, "earliest_by_name": ebn,
                        "earliest_thesis": th[0]["published_date"] if th else None,
                        "lead_days": (pd.Timestamp(peak) - pd.Timestamp(ebn)).days if ebn else None}
        hits[g] = {"by_name_dates": [a["published_date"] for a in bn],
                   "thesis_dates": [a["published_date"] for a in th]}
    det_arts = {g: {k: [{"d": a["published_date"], "src": a["source"], "title": a["title"], "url": a["url"]}
                        for a in det[g][k][:10]] for k in ("by_name", "thesis")} for g in det}
    return {"span": [start, end], "generated_from": "tavily two-pass aligned beat sweep (no ticker queries)",
            "cost": {"windows": nwin, "tavily_credits": credits, "wall_seconds": round(elapsed, 1),
                     "llm_usd": 0.0, "pool_size": len(arts)},
            "wcount": wcount, "detection": detection, "hits": hits, "det_arts": det_arts,
            "candidates": tick.most_common(25)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2026-07-07")      # forward day-1
    ap.add_argument("--lookback", type=int, default=14)  # bi-weekly contiguous windows
    ap.add_argument("--ckpt", default=str(REPO / "data" / "retrieval_backtest.ckpt.json"))
    args = ap.parse_args()
    res = run(args.start, args.end, args.lookback, Path(args.ckpt))
    OUT.write_text(json.dumps(res, indent=1))
    c = res["cost"]
    print(f"backtest {args.start}..{args.end}: pool={c['pool_size']} | {c['tavily_credits']} credits | "
          f"{c['wall_seconds']}s | $0 LLM -> {OUT}")
