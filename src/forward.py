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
import anthropic
import forward_engine
import forward_gather
import forward_gather_tavily
import llm
import score
import trace
import trump_feed
import wayback
from optimizer import load_financial_model, resolve_curator_model, resolve_stage_models
from util import load_dotenv, scan_anchors, news_domains

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANS_CSV = REPO_ROOT / "data" / "forward" / "firehose_scans.csv"
ARCHIVE_DIR = REPO_ROOT / "data" / "forward" / "archive"   # LOCAL-ONLY (gitignored): raw web-search
_FWD_PROFILE = REPO_ROOT / "investor_profile.forward.md"   #   inputs frozen at decision time (Option B)
# Forward/production reads the FROZEN forward profile (the live candidate under test); the backtest
# tools use investor_profile.backtest.md, which is free to keep evolving. Fall back if the forward file is absent.
PROFILE = _FWD_PROFILE if _FWD_PROFILE.exists() else REPO_ROOT / "investor_profile.backtest.md"
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
    """Write the immutable per-week archive (LOCAL-ONLY). REUSES the gather's already-frozen in-window
    pool (`capture['arts']` — the actual scout input, no re-fetching) + the full raw-result metadata."""
    cfg = load_financial_model(str(PROFILE))               # stamp the frozen config that produced this week
    knobs = {k: cfg.get(k) for k in ("event_agent_model", "scout_model", "concentration_cap", "risk_aversion",
             "min_trade_size", "lookback_period_days", "max_agents", "spy_agent_conviction",
             "defensive_agent_conviction", "defensive_ticker", "curator_memory_weeks", "rebalance_days")}
    pool = capture.get("arts", [])                         # frozen in-window pool: {title,url,published_date,source,snippet}
    rec = {"week": week, "decision_ts": decision_ts, "model": model,
           "profile": PROFILE.name, "config": knobs,
           "queries": capture.get("queries", []),
           "pool": pool,                                   # the FROZEN articles the scout actually read (replay corpus)
           "raw_results": capture.get("results", []),      # every gather hit (metadata + in_window flag), no re-fetch
           "picks": [{k: p.get(k) for k in ("ticker", "thesis", "thesis_live", "conviction", "evidence_urls")} for p in picks]}
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    out = ARCHIVE_DIR / f"{week}.json"
    out.write_text(json.dumps(rec, indent=2, default=str))
    print(f"  archived pool={len(pool)} frozen articles + {len(rec['raw_results'])} raw hits -> {out}")
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


def _use_sandbox(dir_path: str) -> None:
    """Redirect ALL forward state (scan-log, archive, journal) under DIR — for THROWAWAY experiments
    that must NOT touch the live series (data/forward/) or its cron. Everything else is unchanged."""
    global SCANS_CSV, ARCHIVE_DIR
    d = Path(dir_path)
    d.mkdir(parents=True, exist_ok=True)
    SCANS_CSV = d / "firehose_scans.csv"
    ARCHIVE_DIR = d / "archive"
    forward_engine.STATE_F = d / "journal.json"
    print(f"  SANDBOX: forward state -> {d}/ (live series untouched)", file=sys.stderr)


def scan_and_log(model: str, rebalance_days: int, curator_memory_weeks: int = 8,
                 anchor: pd.Timestamp | None = None, news_cap: int = 0,
                 gather_engine: str = "anthropic", scout_model: str | None = None,
                 scout_provider: str = "anthropic") -> pd.DataFrame:
    """Live EVENT-FIRST scan for the current week; append its picks (deduped by week). The engine
    (forward_engine.run_week) gathers the week's firehose, discovers/tracks events, and persists the
    LOCAL journal; here we log the decision + archive the raw inputs."""
    log = _read()
    anchor = anchor if anchor is not None else _current_anchor(rebalance_days)
    wk_key = anchor.date().isoformat()
    if len(log) and (log["week"].astype(str) == wk_key).any():
        print(f"  period {wk_key}: already scanned, skipping (dedup).")
        return log
    print(f"  scanning week {wk_key} (event-first engine) via {model} ...", flush=True)
    capture: dict = {}
    decision_ts = _now().isoformat()
    daily_dir = SCANS_CSV.parent / "daily"                 # weekly scan CONSUMES the week's accumulated daily pulls
    acc: dict = {}
    if daily_dir.exists():
        lo = (anchor - pd.Timedelta(days=rebalance_days)).date().isoformat()
        for f in sorted(daily_dir.glob("*.json")):
            for a in json.loads(f.read_text()).get("pool", []):
                d = (a.get("published_date") or "")[:10]
                if a.get("url") and d and lo < d <= wk_key:
                    acc[a["url"]] = a
    pool = None
    if acc:
        _raw = sorted(acc.values(), key=lambda x: x["published_date"], reverse=True)
        pool = _raw[:news_cap] if news_cap else _raw           # news_cap=0 -> UNCAPPED (keep all)
        if news_cap and len(_raw) > news_cap:                  # surface silent drops in forward operation
            print(f"  !! news-cap dropped {len(_raw) - news_cap} of {len(_raw)} articles "
                  f"(oldest-in-window)", file=sys.stderr, flush=True)
    if pool:
        print(f"  using {len(pool)} accumulated daily-pull articles (no separate weekly gather).", flush=True)
    picks = forward_engine.run_week(anchor, model, rebalance_days,
                                    curator_memory_weeks=curator_memory_weeks, capture=capture, news_cap=news_cap,
                                    gather_engine=gather_engine, pool=pool,
                                    scout_model=scout_model, scout_provider=scout_provider)
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


def pull_day(model: str, gather_engine: str = "anthropic") -> None:
    """DAILY past-24h news pull -> accumulate into <forward>/daily/<date>.json (dedup by date).
    The weekly --scan reads the week's accumulated daily pulls as its pool (no separate weekly gather).

    `gather_engine`: "anthropic" (default), "tavily", or "both" (UNION of the two — Anthropic reaches
    Cloudflare-walled etf.com, Tavily reaches the Dow Jones sites that block Anthropic's crawler).

    Fetches UNCAPPED: the daily pull must keep every day's news so the week accumulates in full; the
    single news_cap (a per-WEEK scout budget) is applied only when --scan reads that week's pool. (An
    earlier version passed the same cap here per-DAY *and* per-week — double-capping the pool.)"""
    day = _current_anchor(1)                                # most recent daily 16:30-ET point on/before now
    dk = day.date().isoformat()
    daily_dir = SCANS_CSV.parent / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    out = daily_dir / f"{dk}.json"
    if out.exists():
        print(f"  daily pull {dk}: already pulled, skipping (dedup).")
        return
    print(f"  daily {gather_engine} pull for {dk} (past-24h window) ...", flush=True)
    cap: dict = {}
    if gather_engine == "tavily":
        arts = forward_gather_tavily.gather(None, model, day, 1, capture=cap, cap=0)
    elif gather_engine == "both":                           # UNION: Anthropic + Tavily, deduped by URL
        acap, tcap = {}, {}
        a_arts = forward_gather.gather(anthropic.Anthropic(), model, day, 1, capture=acap, cap=0)
        t_arts = forward_gather_tavily.gather(None, model, day, 1, capture=tcap, cap=0)
        arts = forward_gather.merge_pools(a_arts, t_arts)
        cap["arts"] = arts
        cap["queries"] = (acap.get("queries") or []) + (tcap.get("queries") or [])
        print(f"    union: anthropic {len(a_arts)} + tavily {len(t_arts)} -> {len(arts)} deduped")
    else:
        arts = forward_gather.gather(anthropic.Anthropic(), model, day, 1, capture=cap, cap=0)  # uncapped daily
    out.write_text(json.dumps({"date": dk, "model": model, "pool": cap.get("arts", arts),
                               "queries": cap.get("queries", [])}, indent=2, default=str))
    print(f"  pulled {len(cap.get('arts', arts))} articles -> {out}")


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


def explain(week: str | None = None) -> None:
    """Diagnostic: audit WHY the scout kept few/no gems for a week — walk the pool's named movers with a
    one-line KEEP/REJECT verdict each. Reads the LOCAL archive; one cheap LLM call, no web search."""
    load_dotenv()
    files = sorted(ARCHIVE_DIR.glob("*.json"))
    if not files:
        print("  no forward archive yet — run --scan first.", file=sys.stderr)
        return
    f = (ARCHIVE_DIR / f"{week}.json") if week else files[-1]
    if not f.exists():
        print(f"  no archive for week {week}.", file=sys.stderr)
        return
    rec = json.loads(f.read_text())
    pool = rec.get("pool", [])
    block = "\n".join(f"[{a.get('published_date')} | {a.get('source')}] {a.get('title')} "
                      f"— {a.get('snippet', '')[:180]}" for a in pool)
    _cfg = rec.get("config", {})
    model_id = resolve_curator_model(_cfg.get("event_agent_model") or _cfg.get("model") or "sonnet5")[0]
    sys_p = ("You audit a hidden-gem scout. It keeps ONLY a still-EARLY / under-the-radar US-listed ticker "
             "tied to a SPECIFIC, DATABLE, RESOLVABLE catalyst (a war/chokepoint, export ban/tariff, named "
             "bill, regulatory/agency action, supply shock, deal, OR a dated future event it is rising in "
             "anticipation of). It REJECTS already-run/mainstream names, vague themes/momentum, and "
             "untradeable/foreign names, AND brand-new IPOs or just-merged SPACs lacking a few weeks of trading history (the mechanical optimizer can't size a name with no price history). For the week's articles below, list each NAMED-MOVER candidate with "
             "a one-line verdict — KEEP or REJECT + the reason (already-run / no clean catalyst / not "
             "US-tradeable / too new to size / just a theme / etc.). Finish with the SINGLE closest call and whether it "
             "should have been kept.")
    user = f"Week ending {rec['week']}. Articles the scout read ({len(pool)}):\n\n{block}\n\nAudit them."
    txt = llm.make_client("anthropic", model_id).complete(sys_p, user, use_web_search=False,
                                                           stage="agent", label=f"explain-{rec['week']}")
    print(f"\n=== scout audit — week {rec['week']} ({len(pool)} articles, {len(rec.get('picks', []))} picks) ===\n")
    print(txt)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Forward paper-trade of the firehose (the clean test).")
    ap.add_argument("--scan", action="store_true", help="live firehose scan for this week, append to log")
    ap.add_argument("--report", action="store_true", help="mark the accumulated portfolio to market vs SPY")
    ap.add_argument("--trace", nargs="?", const="__default__", default=None,
                    help="log every LLM prompt/response + search query to data/forward/transcript.jsonl (or PATH)")
    ap.add_argument("--model", default=None,
                    help="curator model id override; default = investor_profile.forward.md's model knob "
                         "(e.g. sonnet5 -> claude-sonnet-5). Must be an Anthropic model (web search).")
    ap.add_argument("--explain", nargs="?", const="", default=None, metavar="WEEK",
                    help="audit why the scout kept few/no gems for a week (default: latest archive); no web search")
    ap.add_argument("--sandbox", default=None, metavar="DIR",
                    help="THROWAWAY run: redirect journal/scan-log/archive under DIR (isolates from the live series)")
    ap.add_argument("--rebalance-days", type=int, default=None, dest="rebalance_days",
                    help="override the gather window in days (e.g. 28 for a 4-week prototype); default from profile")
    ap.add_argument("--anchor", default=None, metavar="YYYY-MM-DD",
                    help="explicit week-ending anchor (e.g. a recent Friday); default = most recent cron anchor")
    ap.add_argument("--pull", action="store_true",
                    help="daily 1-day Anthropic news pull; accumulates for the weekly --scan (no LLM scout)")
    ap.add_argument("--gather", choices=["anthropic", "tavily"], default=None,
                    help="gather engine override; default = profile gather_engine or anthropic")
    args = ap.parse_args(argv)
    if args.trace is not None:
        tp = str(SCANS_CSV.parent / "transcript.jsonl") if args.trace == "__default__" else args.trace
        trace.enable(tp)
        print(f"  TRACE ON -> {tp}", flush=True)
    if args.sandbox:
        _use_sandbox(args.sandbox)
    if not (args.scan or args.report or args.explain is not None or args.pull):
        ap.error("choose at least one of --scan / --report / --explain / --pull")

    if args.explain is not None:
        explain(args.explain or None)

    if args.scan:
        load_dotenv()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ERROR: ANTHROPIC_API_KEY not set (export it or put it in .env).", file=sys.stderr)
            return 2
        fm = load_financial_model(str(PROFILE))
        (scout_id, scout_prov), (event_id, provider) = resolve_stage_models(fm)
        if args.model:                          # explicit override wins (event/gather model)
            event_id = args.model
        elif provider != "anthropic":           # the gather + event model does web search (Anthropic-only)
            print(f"ERROR: forward --scan needs an Anthropic event_agent_model (gather web search is "
                  f"Anthropic-only); '{fm.get('event_agent_model') or fm.get('model')}' resolves to "
                  f"provider '{provider}'. Pass --model <anthropic-id>. (scout_model may be any provider.)",
                  file=sys.stderr)
            return 2
        rebal = args.rebalance_days or int(fm.get("rebalance_days", 7))
        anch = pd.Timestamp(args.anchor, tz="America/New_York") if args.anchor else None
        scan_and_log(event_id, rebal, int(fm.get("curator_memory_weeks", 8)), anchor=anch,
                     news_cap=int(fm.get("news_cap", 0)),
                     gather_engine=(args.gather or str(fm.get("gather_engine", "anthropic"))),
                     scout_model=scout_id, scout_provider=scout_prov)

    if args.pull:
        load_dotenv()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
            return 2
        fm = load_financial_model(str(PROFILE))
        (_si, _sp), (event_id, _prov) = resolve_stage_models(fm)   # daily gather = Anthropic event model
        pull_day(args.model or event_id, gather_engine=(args.gather or str(fm.get("gather_engine", "anthropic"))))

    if args.report:
        report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
