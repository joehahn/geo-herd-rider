"""forward.py — forward paper-trade of the FIREHOSE (the only clean test).

Every retrospective firehose number is doubly hindsight-contaminated and cannot be cleaned:
(1) no available search tool gives true point-in-time retrieval — both Anthropic's `before:` and
Tavily's `end_date` leak post-cutoff articles, and the early under-the-radar pieces don't rank into
a date-bounded pull (see search.py); and (2) the curator model is trained past the events. The
fixture backtest (firehose.py --fixture) only proves the MECHANICS assuming perfect retrieval.

So the firehose is provable only FORWARD: scan the live news firehose NOW for gems the press is
naming today (searching now for a just-happened event is look-ahead-correct by construction), log
the watchlist stamped with decision_ts=now, and mark the accumulated weekly portfolio to market as
prices arrive. Nothing about the outcome exists when a row is written.

Modes:
  --scan    Run the live firehose scan for the current week and APPEND its picks (decision_ts=now)
            to the forward scan log. Needs ANTHROPIC_API_KEY (tokens + web search). Re-running the
            same week is a no-op (dedup by week). Run this weekly as fresh coverage arrives.
  --report  Rebuild the weekly portfolio from the accumulated scans, mark it to market with current
            prices, and report the firehose portfolio vs SPY, the gems caught, and live holdings.

State : data/forward/firehose_scans.csv  (append-only: decision_ts, week, ticker, thesis,
        thesis_live, evidence_urls)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

import pandas as pd

import firehose
import forward_engine
import score
import trump_feed
import wayback
from optimizer import load_financial_model, resolve_curator_model
from util import load_dotenv, scan_anchors, news_domains

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANS_CSV = REPO_ROOT / "data" / "forward" / "firehose_scans.csv"
ARCHIVE_DIR = REPO_ROOT / "data" / "forward" / "archive"   # LOCAL-ONLY (gitignored): raw web-search
_FWD_PROFILE = REPO_ROOT / "investor_profile.forward.md"   #   inputs frozen at decision time (Option B)
# Forward/production reads the FROZEN forward profile (the live candidate under test); the backtest
# tools use investor_profile.md, which is free to keep evolving. Fall back if the forward file is absent.
PROFILE = _FWD_PROFILE if _FWD_PROFILE.exists() else REPO_ROOT / "investor_profile.md"
MODEL = "claude-opus-4-8"

COLS = ["decision_ts", "week", "ticker", "thesis", "thesis_live", "conviction", "evidence_urls"]


def _freeze_text(url: str, cutoff: str) -> tuple[str, str]:
    """Freeze `url`'s article text at decision time -> (text, source). Forward articles are FRESH,
    so a live fetch NOW is point-in-time-correct; Wayback (as-of-`cutoff`) is the backfill path."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (geo-herd-rider forward)"})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        lede = wayback._extract_lede(html)
        if lede:
            return lede, "live"
    except Exception:  # noqa: BLE001  (best-effort; a miss is fine, we still keep the metadata)
        pass
    try:
        l = wayback.lede(url, cutoff)
        if l:
            return l, "wayback-asof"
    except Exception:  # noqa: BLE001
        pass
    return "", "unavailable"


def _write_archive(week: str, decision_ts: str, model: str, capture: dict,
                   picks: list[dict], cutoff: str) -> Path:
    """Freeze every web-search result's text and write the immutable per-week archive (LOCAL-ONLY)."""
    results = []
    for r in capture.get("results", []):
        text, source = _freeze_text(r["url"], cutoff)
        results.append({**r, "frozen_text": text, "text_source": source, "fetched_at": _now().isoformat()})
    cfg = load_financial_model(str(PROFILE))               # stamp the frozen config that produced this week
    knobs = {k: cfg.get(k) for k in ("model", "concentration_cap", "risk_aversion", "min_trade_size",
             "lookback_period_days", "max_agents", "spy_agent_conviction", "defensive_agent_conviction",
             "defensive_ticker", "curator_memory_weeks", "rebalance_days")}
    rec = {"week": week, "decision_ts": decision_ts, "model": model,
           "profile": PROFILE.name, "config": knobs,
           "queries": capture.get("queries", []), "results": results,
           "picks": [{k: p.get(k) for k in ("ticker", "thesis", "thesis_live", "conviction", "evidence_urls")} for p in picks]}
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    out = ARCHIVE_DIR / f"{week}.json"
    out.write_text(json.dumps(rec, indent=2, default=str))
    got = sum(1 for r in results if r["text_source"] != "unavailable")
    print(f"  archived {len(results)} web-search results ({got} with frozen text) -> {out}")
    return out


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


def scan_and_log(model: str, rebalance_days: int, curator_memory_weeks: int = 8) -> pd.DataFrame:
    """Live EVENT-FIRST scan for the current week; append its picks (deduped by week). The engine
    (forward_engine.run_week) gathers the week's firehose, discovers/tracks events, and persists the
    LOCAL journal; here we log the decision + archive the raw inputs."""
    log = _read()
    anchor = _current_anchor(rebalance_days)
    wk_key = anchor.date().isoformat()
    if len(log) and (log["week"].astype(str) == wk_key).any():
        print(f"  period {wk_key}: already scanned, skipping (dedup).")
        return log
    print(f"  scanning week {wk_key} (event-first engine) via {model} ...", flush=True)
    capture: dict = {}
    decision_ts = _now().isoformat()
    picks = forward_engine.run_week(anchor, model, rebalance_days,
                                    curator_memory_weeks=curator_memory_weeks, capture=capture)
    # Freeze + archive the raw web-search inputs (LOCAL-ONLY) — regardless of whether any gem is live,
    # so a later variant-replay sees the FULL pool the scout saw this week, not just what it cited.
    _write_archive(wk_key, decision_ts, model, capture, picks, anchor.date().isoformat())
    if not picks:
        print(f"  week {wk_key}: no live gems this week (journal holds nothing).")
        picks = [{"ticker": "", "thesis": "", "thesis_live": "", "conviction": ""}]   # empty marker row
    rows = [{"decision_ts": decision_ts, "week": wk_key, "ticker": p.get("ticker", ""),
             "thesis": p.get("thesis", ""), "thesis_live": p.get("thesis_live", ""),
             "conviction": p.get("conviction", ""),
             "evidence_urls": ";".join(p.get("evidence_urls", []) or [])} for p in picks]
    out = pd.concat([log, pd.DataFrame(rows)], ignore_index=True)
    SCANS_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(SCANS_CSV, index=False)
    live = [f"{r['ticker']}(conv {r['conviction']})" for r in rows if r["ticker"]]
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
    cap = float(fm.get("initial_investment_usd", 50_000))
    scans = _scans_dict(log)
    bt = firehose.backtest(scans, fm, cap)
    print(f"weeks scanned: {len(weeks)}  ({weeks[0]} .. {weeks[-1]})")
    print(f"  firehose portfolio : ${cap:,.0f} -> ${bt['final']:,.0f} ({bt['final']/cap-1:+.1%})")
    print(f"  SPY           : ${cap:,.0f} -> ${bt['spy_final']:,.0f} ({bt['spy_final']/cap-1:+.1%})")
    held = {t for r in bt["log"] for t in r["watchlist"].split(";") if t}
    print(f"  gems caught   : {', '.join(sorted(held)) or '—'}")
    a = list(scans)[-1]
    print(f"  live holdings : {[p['ticker'] for p in scans[a] if p.get('thesis_live')] or '—'}")
    print("=" * 62)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Forward paper-trade of the firehose (the clean test).")
    ap.add_argument("--scan", action="store_true", help="live firehose scan for this week, append to log")
    ap.add_argument("--report", action="store_true", help="mark the accumulated portfolio to market vs SPY")
    ap.add_argument("--model", default=None,
                    help="curator model id override; default = investor_profile.md's model knob "
                         "(e.g. sonnet5 -> claude-sonnet-5). Must be an Anthropic model (web search).")
    args = ap.parse_args(argv)
    if not (args.scan or args.report):
        ap.error("choose at least one of --scan / --report")

    if args.scan:
        load_dotenv()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ERROR: ANTHROPIC_API_KEY not set (export it or put it in .env).", file=sys.stderr)
            return 2
        fm = load_financial_model(str(PROFILE))
        model_id, provider = resolve_curator_model(fm.get("model", "sonnet5"))
        if args.model:                          # explicit override wins
            model_id = args.model
        elif provider != "anthropic":           # web search is Anthropic-only; don't silently misfire
            print(f"ERROR: forward --scan needs an Anthropic curator (web search is Anthropic-only); "
                  f"investor_profile model '{fm.get('model')}' resolves to provider '{provider}'. "
                  f"Pass --model <anthropic-id>.", file=sys.stderr)
            return 2
        scan_and_log(model_id, int(fm.get("rebalance_days", 7)), int(fm.get("curator_memory_weeks", 8)))

    if args.report:
        report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
