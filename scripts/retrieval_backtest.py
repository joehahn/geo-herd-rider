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
        # cap=0 (keep ALL — no recency truncation) + max_results=20 (deeper per beat); free on credits,
        # and this is a retrieval-only backtest (no LLM scout reads the pool). Forward keeps its defaults.
        arts = t.gather(None, None, a, lookback, workers=4, cap=0, max_results=20)
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
    return _summarize(list(pool.values()), wcount, cnt["n"], elapsed, len(anchors), start, end, lookback)


TAVILY_CREDIT_USD = 0.008   # pay-as-you-go rate (Jul-2026 receipt: $41.72 / 5216 credits), pre-tax
WINDOW_OVERRIDE = {"RNMBY": ["2025-01-01", "2026-07-11"],   # full 2025-26 era (its rise+fall), per request
                   "AREC": ["2025-01-01", "2026-05-01"]}
# strong superlative markers — for the candidate shortlist's "gem-buzz" count (how many of a ticker's
# mentions sit in a skyrocketing/soaring/record-high article), and shared with build_ground_truth intent
SUPERLATIVES = ("skyrocket", "soar", "surg", "best performing", "best-performing", "record high",
                "all-time high", "little-known", "under the radar", "outperform", "rocket", "explod", "breakout",
                "unprecedented", "never seen", "never experienced", "record order", "record backlog", "highest ever",
                "fastest", "most traded", "most-traded", "best perf")


def _actual_peaks(panel) -> dict:
    """The ACTUAL price maximum per gem (argmax of adjusted close) — the visible top of the line, not
    an approximate catalyst date. Falls back to gem_detect.PEAK if a ticker has no price."""
    peaks = {}
    for g in gem_detect.GEMS:
        s = panel[g].dropna() if g in panel.columns else pd.Series(dtype=float)
        peaks[g] = s.idxmax().date().isoformat() if len(s) else gem_detect.PEAK[g]
    return peaks


def _norm_url(u: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(u)
    return (p.netloc.replace("www.", "") + p.path).rstrip("/").lower()


def _chart_data(det: dict, panel, peaks: dict, pool_urls: set | None = None, gt: dict | None = None) -> dict:
    """Per gem: adjusted-close series over its era + a dot per retrieved article (kind name/thesis,
    price as-of its date, title + lede). Peak marker = the ACTUAL price max (peaks[g]). Window =
    [min(peak-150d, earliest dot-21d) .. peak+60d], clamped to data; RNMBY overridden to the full span.
    Dots outside the window are dropped to keep the chart on the era; pre-inception dots (DRAM thesis
    before its 2026-04 launch) stay at baseline with price=null."""
    charts = {}
    reach_path = REPO / "data" / "gt_forward_reachable.json"   # missed GT the FORWARD (Anthropic) can still reach
    reach = json.loads(reach_path.read_text()) if reach_path.exists() else {}
    spy = panel["SPY"].dropna() if "SPY" in panel.columns else pd.Series(dtype=float)
    for g in gem_detect.GEMS:
        rows = [(a, "name") for a in det[g]["by_name"]]     # by-name dots only (thesis grep isn't the scout)
        s = panel[g].dropna() if g in panel.columns else pd.Series(dtype=float)
        peak = pd.Timestamp(peaks[g])
        dates = [pd.Timestamp(a["published_date"]) for a, _ in rows]
        ov = WINDOW_OVERRIDE.get(g)
        if ov:
            wstart, wend = pd.Timestamp(ov[0]), pd.Timestamp(ov[1])
        else:
            wstart = peak - pd.Timedelta(days=150)
            if dates:
                wstart = min(wstart, min(dates) - pd.Timedelta(days=21))
            wstart = max(wstart, pd.Timestamp("2025-01-01"))
            wend = peak + pd.Timedelta(days=60)
        if len(s):
            wend = min(wend, s.index.max())
        sw = s[(s.index >= wstart) & (s.index <= wend)] if len(s) else s
        series = [[d.date().isoformat(), round(float(v), 2)] for d, v in sw.items()] if len(sw) else []
        spw = spy[(spy.index >= wstart) & (spy.index <= wend)] if len(spy) else spy
        spy_series = [[d.date().isoformat(), round(float(v), 2)] for d, v in spw.items()] if len(spw) else []
        dots = []
        for a, kind in rows:
            d = pd.Timestamp(a["published_date"])
            if d < wstart or d > wend:               # keep the chart focused on the era
                continue
            sub = s[s.index <= d] if len(s) else s
            price = round(float(sub.iloc[-1]), 2) if len(sub) else None   # as-of close (nearest prior)
            dots.append({"d": a["published_date"], "kind": kind, "price": price,
                         "title": (a.get("title", "") or "")[:160], "lede": (a.get("snippet", "") or "")[:240],
                         "src": a.get("source", ""), "url": a.get("url", "")})
        # ground-truth overlay: target superlative articles, marked detected (in pool) or missed
        from urllib.parse import urlparse
        gt_over = []
        for a in (gt or {}).get(g, []):
            gd = pd.Timestamp(a["date"])
            if gd < wstart or gd > wend:
                continue
            sub2 = s[s.index <= gd] if len(s) else s
            gt_over.append({"d": a["date"], "price": round(float(sub2.iloc[-1]), 2) if len(sub2) else None,
                            "url": a["url"], "title": a["title"], "src": urlparse(a["url"]).netloc.replace("www.", ""),
                            "detected": (pool_urls is not None) and _norm_url(a["url"]) in pool_urls,
                            "forward_reachable": bool(reach.get(a["url"], False))})
        charts[g] = {"ticker": g, "window": [wstart.date().isoformat(), wend.date().isoformat()],
                     "series": series, "spy_series": spy_series, "dots": sorted(dots, key=lambda x: x["d"]),
                     "ground_truth": sorted(gt_over, key=lambda x: x["d"])}
    return charts


def _summarize(arts, wcount, credits, elapsed, nwin, start, end, lookback=14) -> dict:
    det = gem_detect.detect(arts)
    import score
    import forward_gather as fg
    GEMB, COVB = set(fg.GEM_BEATS), set(fg.COVERAGE_BEATS)
    SPEC, MILL = set(fg._SPECIALTY_ALLOW), set(fg._MILL_BLOCK)
    panel = score.fetch_panel(list(gem_detect.GEMS) + ["SPY"], "2025-01-01", "2026-07-11", use_cache=True)
    peaks = _actual_peaks(panel)                     # peak line + lead measured against the real price top
    gt_path = REPO / "data" / "gem_ground_truth.json"
    gt = json.loads(gt_path.read_text()) if gt_path.exists() else {}
    pool_urls = {_norm_url(a["url"]) for a in arts if a.get("url")}
    gt_recall = {g: [sum(1 for a in gt.get(g, []) if _norm_url(a["url"]) in pool_urls), len(gt.get(g, []))]
                 for g in gem_detect.GEMS}
    tick, tick_super = collections.Counter(), collections.Counter()
    pat = re.compile(r"\$([A-Z]{1,5})\b|\((?:NYSE|NASDAQ|NYSEARCA)[:\s]+([A-Z]{1,5})\)")
    for a in arts:
        blob = a.get("title", "") + " " + a.get("snippet", "")
        is_super = any(w in blob.lower() for w in SUPERLATIVES)
        for m in pat.findall(blob):
            s = m[0] or m[1]
            if s:
                tick[s] += 1
                if is_super:
                    tick_super[s] += 1
    detection, hits = {}, {}
    for g, r in det.items():
        bn, th = r["by_name"], r["thesis"]
        ebn = bn[0]["published_date"] if bn else None
        peak = peaks[g]
        detection[g] = {"form": gem_detect.GEMS[g]["form"], "by_name": len(bn), "thesis": len(th),
                        "peak": peak, "earliest_by_name": ebn,
                        "earliest_thesis": th[0]["published_date"] if th else None,
                        "lead_days": (pd.Timestamp(peak) - pd.Timestamp(ebn)).days if ebn else None}
        hits[g] = {"by_name_dates": [a["published_date"] for a in bn],
                   "thesis_dates": [a["published_date"] for a in th]}
    det_arts = {g: {k: [{"d": a["published_date"], "src": a["source"], "title": a["title"], "url": a["url"]}
                        for a in det[g][k][:10]] for k in ("by_name", "thesis")} for g in det}
    # retrieval-diagnostic aggregations over the whole pool
    byname_urls = {a["url"] for g in det for a in det[g]["by_name"]}   # articles that name ANY gem
    daily, monthly, quarterly = collections.Counter(), collections.Counter(), collections.Counter()
    dow = [0] * 7                                        # Mon..Sun, by publication weekday
    beats, beats_uniq, beats_byname = collections.Counter(), collections.Counter(), collections.Counter()
    domains = collections.Counter()
    gem_pass = cov_pass = both_pass = 0
    for a in arts:
        d = a.get("published_date")
        if d:
            daily[d] += 1
            monthly[d[:7]] += 1
            q = (int(d[5:7]) - 1) // 3 + 1
            quarterly[f"{d[:4]}-Q{q}"] += 1
            try:
                dow[pd.Timestamp(d).dayofweek] += 1
            except Exception:  # noqa: BLE001
                pass
        qs = a.get("queries", [])
        for beat in qs:                                 # each beat that surfaced this article
            beats[beat] += 1
            if a["url"] in byname_urls:
                beats_byname[beat] += 1
        if len(qs) == 1:
            beats_uniq[qs[0]] += 1
        g, cov = any(b in GEMB for b in qs), any(b in COVB for b in qs)
        gem_pass += g and not cov
        cov_pass += cov and not g
        both_pass += g and cov
        if a.get("source"):
            domains[a["source"]] += 1
    # within-window recency (cap=80 sorted-desc truncation bias): days before each window's end
    anchors, dd = [], pd.Timestamp(end)
    while dd.date().isoformat() > start:
        anchors.append(dd); dd -= pd.Timedelta(days=lookback)
    anchors = sorted(anchors)
    recency = [0] * lookback
    for a in arts:
        try:
            t = pd.Timestamp(a["published_date"])
            we = next((x for x in anchors if x >= t), anchors[-1])
            off = (we - t).days
            if 0 <= off < lookback:
                recency[off] += 1
        except Exception:  # noqa: BLE001
            pass

    def _klass(dom):
        n = dom.replace("www.", "").replace("m.uk.", "").replace("m.", "")
        if any(s in n or n in s for s in SPEC):
            return "specialty"
        if any(m in n for m in MILL):
            return "mill"
        return "other"
    domain_counts = [[d, n, _klass(d)] for d, n in domains.most_common(30)]
    return {"span": [start, end], "generated_from": "tavily two-pass aligned beat sweep (no ticker queries)",
            "cost": {"windows": nwin, "tavily_credits": credits, "wall_seconds": round(elapsed, 1),
                     "tavily_usd": round(credits * TAVILY_CREDIT_USD, 2), "llm_usd": 0.0, "pool_size": len(arts)},
            "wcount": wcount, "detection": detection, "hits": hits, "det_arts": det_arts,
            "charts": _chart_data(det, panel, peaks, pool_urls, gt), "gt_recall": gt_recall,
            "candidates": [[s, n, tick_super.get(s, 0)] for s, n in tick.most_common()
                           if s not in gem_detect.GEMS][:25],   # exclude already-promoted gems from the shortlist
            "daily": sorted(daily.items()), "monthly": sorted(monthly.items()),
            "quarterly": sorted(quarterly.items()), "dow": dow, "recency": recency,
            "beat_counts": beats.most_common(),
            "beat_unique": {b: beats_uniq.get(b, 0) for b, _ in beats.most_common()},
            "beat_byname": {b: beats_byname.get(b, 0) for b, _ in beats.most_common()},
            "pass_split": {"gem_only": gem_pass, "coverage_only": cov_pass, "both": both_pass},
            "domain_counts": domain_counts}


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
