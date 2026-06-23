"""forward.py — forward paper-trade of the FIREHOSE (the only clean test).

Every retrospective firehose number is doubly hindsight-contaminated and cannot be cleaned:
(1) no available search tool gives true point-in-time retrieval — both Anthropic's `before:` and
Tavily's `end_date` leak post-cutoff articles, and the early under-the-radar pieces don't rank into
a date-bounded pull (see search.py); and (2) the curator model is trained past the events. The
fixture backtest (firehose.py --fixture) only proves the MECHANICS assuming perfect retrieval.

So the firehose is provable only FORWARD: scan the live news firehose NOW for gems the press is
naming today (searching now for a just-happened event is look-ahead-correct by construction), log
the watchlist stamped with decision_ts=now, and mark the accumulated weekly book to market as
prices arrive. Nothing about the outcome exists when a row is written.

Modes:
  --scan    Run the live firehose scan for the current week and APPEND its picks (decision_ts=now)
            to the forward scan log. Needs ANTHROPIC_API_KEY (tokens + web search). Re-running the
            same week is a no-op (dedup by week). Run this weekly as fresh coverage arrives.
  --report  Rebuild the weekly book from the accumulated scans, mark it to market with current
            prices, and report the firehose book vs SPY, the gems caught, and live holdings.

State : data/forward/firehose_scans.csv  (append-only: decision_ts, week, ticker, thesis,
        thesis_live, evidence_urls)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

import firehose
import score
import trump_feed
from optimizer import load_financial_model
from util import load_dotenv, scan_anchors, news_domains

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANS_CSV = REPO_ROOT / "data" / "forward" / "firehose_scans.csv"
PROFILE = REPO_ROOT / "investor_profile.md"
MODEL = "claude-opus-4-8"

COLS = ["decision_ts", "week", "ticker", "thesis", "thesis_live", "evidence_urls"]


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _current_anchor(rebalance_days: int = 7) -> pd.Timestamp:
    """Most recent cron anchor (16:30 ET) on/before now, at the rebalance cadence."""
    now = _now()
    anchors = scan_anchors((now - pd.Timedelta(days=3 * rebalance_days)).strftime("%Y-%m-%d"),
                           (now + pd.Timedelta(days=1)).strftime("%Y-%m-%d"), rebalance_days)
    past = [a for a in anchors if a.tz_convert("UTC") <= now]
    return past[-1] if past else anchors[-1]


def _read() -> pd.DataFrame:
    return pd.read_csv(SCANS_CSV) if SCANS_CSV.exists() else pd.DataFrame(columns=COLS)


def scan_and_log(model: str, rebalance_days: int, lookback_days: int | None = None) -> pd.DataFrame:
    """Live firehose scan for the current period; append its picks (deduped by period)."""
    import anthropic
    lookback_days = rebalance_days if lookback_days is None else lookback_days
    log = _read()
    anchor = _current_anchor(rebalance_days)
    wk_key = anchor.date().isoformat()
    if len(log) and (log["week"].astype(str) == wk_key).any():
        print(f"  period {wk_key}: already scanned, skipping (dedup).")
        return log
    lo = anchor - pd.Timedelta(days=lookback_days)
    posts = trump_feed.candidate_posts(lo.strftime("%Y-%m-%d"), anchor.strftime("%Y-%m-%d"))
    print(f"  scanning week {wk_key} ({len(posts)} posts in lookback) via {model} ...", flush=True)
    picks = firehose.scan(anthropic.Anthropic(), model, anchor, posts, news_domains())
    if not picks:
        print(f"  week {wk_key}: no gems named by the press this week.")
        # still record an empty marker row so the week is logged (and not re-scanned)
        picks = [{"ticker": "", "thesis": "", "thesis_live": ""}]
    rows = [{"decision_ts": _now().isoformat(), "week": wk_key, "ticker": p.get("ticker", ""),
             "thesis": p.get("thesis", ""), "thesis_live": p.get("thesis_live", ""),
             "evidence_urls": ";".join(p.get("evidence_urls", []) or [])} for p in picks]
    out = pd.concat([log, pd.DataFrame(rows)], ignore_index=True)
    SCANS_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(SCANS_CSV, index=False)
    live = [r["ticker"] + ("" if r["thesis_live"] in (True, "True", "true") else "(EXIT)")
            for r in rows if r["ticker"]]
    print(f"  week {wk_key}: logged {live or '—'} -> {SCANS_CSV}")
    return out


def _scans_dict(log: pd.DataFrame) -> dict:
    """Rebuild firehose's {anchor_ts: [picks]} from the flat scan log."""
    out: dict = {}
    for wk, grp in log.groupby("week"):
        anchor = pd.Timestamp(str(wk) + " 16:30", tz="America/New_York")
        picks = []
        for _, r in grp.iterrows():
            if not str(r.get("ticker", "")).strip():
                continue
            tl = r.get("thesis_live")
            picks.append({"ticker": str(r["ticker"]).strip().upper(),
                          "thesis": r.get("thesis", ""),
                          "thesis_live": str(tl) in ("True", "true", "1", "1.0", "True ")})
        out[anchor] = picks
    return dict(sorted(out.items()))


def report() -> None:
    log = _read()
    print("\n" + "=" * 62)
    print("geo-herd-rider — FORWARD firehose scoreboard (look-ahead-clean)")
    print("=" * 62)
    weeks = sorted(log["week"].astype(str).unique()) if len(log) else []
    if len(weeks) < 2:
        print(f"{len(weeks)} week(s) scanned. Need >=2 weekly scans to mark a return.")
        if weeks:
            latest = _scans_dict(log)
            a = list(latest)[-1]
            live = [p["ticker"] for p in latest[a] if p.get("thesis_live")]
            print(f"Latest scan {weeks[-1]}: live picks {live or '—'}")
        print("Run `forward.py --scan` weekly as coverage arrives.")
        return
    fm = load_financial_model(str(PROFILE))
    scans = _scans_dict(log)
    bt = firehose.backtest(scans, fm)
    print(f"weeks scanned: {len(weeks)}  ({weeks[0]} .. {weeks[-1]})")
    print(f"  firehose book : $50,000 -> ${bt['final']:,.0f} ({bt['final']/50000-1:+.1%})")
    print(f"  SPY           : $50,000 -> ${bt['spy_final']:,.0f} ({bt['spy_final']/50000-1:+.1%})")
    held = {t for r in bt["log"] for t in r["watchlist"].split(";") if t}
    print(f"  gems caught   : {', '.join(sorted(held)) or '—'}")
    a = list(scans)[-1]
    print(f"  live holdings : {[p['ticker'] for p in scans[a] if p.get('thesis_live')] or '—'}")
    print("=" * 62)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Forward paper-trade of the firehose (the clean test).")
    ap.add_argument("--scan", action="store_true", help="live firehose scan for this week, append to log")
    ap.add_argument("--report", action="store_true", help="mark the accumulated book to market vs SPY")
    ap.add_argument("--model", default=MODEL)
    args = ap.parse_args(argv)
    if not (args.scan or args.report):
        ap.error("choose at least one of --scan / --report")

    if args.scan:
        load_dotenv()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ERROR: ANTHROPIC_API_KEY not set (export it or put it in .env).", file=sys.stderr)
            return 2
        fm = load_financial_model(str(PROFILE))
        scan_and_log(args.model, int(fm.get("rebalance_days", 7)), fm.get("news_lookback_days"))

    if args.report:
        report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
