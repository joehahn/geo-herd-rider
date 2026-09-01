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
import datetime as _dt
import json
import os
import sys
import urllib.request
from pathlib import Path

import pandas as pd

import firehose
import anthropic
import forward_engine
import gkg                       # for _spam_title only: the forward pull filters to the BACKTEST's standard
import forward_gather
import forward_gather_tavily
import llm
import score
import trace
import trump_feed
import wayback
from optimizer import load_financial_model, resolve_curator_model, resolve_gather_model, resolve_stage_models
from util import resolve_cadence, load_dotenv, scan_anchors, news_domains

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANS_CSV = REPO_ROOT / "data" / "forward" / "firehose_scans.csv"
ARCHIVE_DIR = REPO_ROOT / "data" / "forward" / "archive"   # LOCAL-ONLY (gitignored): raw web-search
_FWD_PROFILE = REPO_ROOT / "investor_profile.forward.md"   #   inputs frozen at decision time (Option B)
# Forward/production reads the FROZEN forward profile (the live candidate under test); the backtest
# tools use investor_profile.backtest.md, which is free to keep evolving. Fall back if the forward file is absent.
PROFILE = _FWD_PROFILE if _FWD_PROFILE.exists() else REPO_ROOT / "investor_profile.backtest.md"
MODEL = "claude-opus-4-8"

# `conviction` was dropped from this schema 2026-08-14 (measured ~random, read by nothing). Rows written
# before that date still carry the column; _read tolerates it, so the historical log stays loadable.
# catalyst_resolved ADDED 2026-09-01. agent.py has always REQUIRED the field (see its schema),
# and forward.py was discarding it every week -- so the entry gate in firehose.backtest() would
# have been INERT live while working in the backtest. Forward weeks before this date have no
# value recorded and cannot be re-evaluated with the gate.
COLS = ["decision_ts", "week", "ticker", "thesis", "thesis_live", "catalyst_resolved",
        "evidence_urls"]


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
    knobs = {k: cfg.get(k) for k in ("gather_model", "event_agent_model", "scout_model", "picker_model", "picker_effort",
             "concentration_cap", "risk_aversion", "min_trade_size", "lookback_period_days", "max_agents",
             "max_watchlist", "always_include", "starter_watchlist",
             "max_new_events", "defensive_ticker", "curator_memory_weeks", "rebalance_period")}
    pool = capture.get("arts", [])                         # frozen in-window pool: {title,url,published_date,source,snippet}
    rec = {"week": week, "decision_ts": decision_ts, "model": model,
           "profile": PROFILE.name, "config": knobs,
           "queries": capture.get("queries", []),
           "pool": pool,                                   # the FROZEN articles the scout actually read (replay corpus)
           "raw_results": capture.get("results", []),      # every gather hit (metadata + in_window flag), no re-fetch
           "picks": [{k: p.get(k) for k in ("ticker", "thesis", "thesis_live", "catalyst_resolved",
                                            "evidence_urls")} for p in picks]}
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
                 gather_engine: str = "both", scout_model: str | None = None,
                 scout_provider: str = "anthropic", gather_model: str | None = None,
                 event_provider: str = "anthropic", news_lookback_days: int = 0,
                 fm: dict | None = None) -> pd.DataFrame:
    """Live EVENT-FIRST scan for the current week; append its picks (deduped by week). The engine
    (forward_engine.run_week) gathers the week's firehose, discovers/tracks events, and persists the
    LOCAL journal; here we log the decision + archive the raw inputs.

    `model` is the event/judgment model (any provider); `gather_model` is the Anthropic web-search
    firehose model (defaults to `model`). Three-tier split — see optimizer.resolve_gather_model."""
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
        # NEWS WINDOW != TRADING CADENCE. news_lookback_days (0 = follow the cadence) decouples how far
        # back each scan READS from how often it TRADES. optimizer.py has documented this as LIVE
        # behaviour all along -- "set it LONGER than the cadence for a deliberate OVERLAP, so an article
        # indexed late, or published right on a scan boundary, still gets read on the next scan" -- but
        # only firehose.py (the BACKTEST) implemented it; the forward silently used the cadence, so
        # setting the knob here did nothing. That matters most at a short cadence: measured 2026-08-14,
        # a batch of articles dated 08-09..08-14 is visible to exactly ONE weekly scan and then ages out
        # for good, while a 30-day window keeps it readable across four. Backward-only, so #4 holds.
        _news_lb = news_lookback_days or rebalance_days
        lo = (anchor - pd.Timedelta(days=_news_lb)).date().isoformat()
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
                                    scout_model=scout_model, scout_provider=scout_provider,
                                    gather_model=gather_model, event_provider=event_provider, fm=fm)
    # Freeze + archive the raw web-search inputs (LOCAL-ONLY) — regardless of whether any gem is live,
    # so a later variant-replay sees the FULL pool the scout saw this week, not just what it cited.
    _write_archive(wk_key, decision_ts, model, capture, picks, anchor.date().isoformat())
    if not picks:
        print(f"  week {wk_key}: no live gems this week (journal holds nothing).")
        picks = [{"ticker": "", "thesis": "", "thesis_live": "", "catalyst_resolved": ""}]   # empty marker row
    rows = [{"decision_ts": decision_ts, "week": wk_key, "ticker": p.get("ticker", ""),
             "thesis": p.get("thesis", ""), "thesis_live": p.get("thesis_live", ""),
             "catalyst_resolved": p.get("catalyst_resolved", ""),
             "milestones": p.get("milestones", ""), "exit_advice": p.get("exit_advice", ""),  # picker evidence
             "evidence_urls": ";".join(p.get("evidence_urls", []) or [])} for p in picks]
    out = pd.concat([log, pd.DataFrame(rows)], ignore_index=True)
    SCANS_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(SCANS_CSV, index=False)
    live = [r['ticker'] for r in rows if r["ticker"]]
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
            _cell = lambda k: ("" if pd.isna(r.get(k, "")) else str(r.get(k, "")))   # NaN-safe (old logs lack these cols)
            picks.append({"ticker": str(r["ticker"]).strip().upper(),
                          "thesis": r.get("thesis", ""),
                          "milestones": _cell("milestones"), "exit_advice": _cell("exit_advice"),  # picker evidence
                          "thesis_live": str(tl) in ("True", "true", "1", "1.0", "True "),
                          "catalyst_resolved": str(r.get("catalyst_resolved")).strip()
                                               in ("True", "true", "1", "1.0")})
        out[anchor] = picks
    return dict(sorted(out.items()))


# ANTHROPIC NEEDS A WIDER RETRIEVAL WINDOW THAN TAVILY. Measured 2026-08-14 on a live 165-result gem
# sweep: at the 1-day window the daily pull used, **0 of 165** results survived the fail-closed date
# filter (3d -> 22, 7d -> 35, 14d -> 55). Anthropic's web_search has NO recency operator -- `before:DATE`
# bounds only the UPPER end -- so it returns articles spread over months and a 1-day window matches
# essentially nothing. That is the whole of the "anthropic 0 + tavily N" line every day for 32 days:
# not a crash, a spec mismatch, which is why it never raised. Tavily has a real date filter, so it keeps
# lookback=1 and stays the precise daily engine.
#
# Widening looks BACKWARD only (hi stays at the anchor), so it cannot leak future news -- non-negotiable
# #4 is intact. The cost is that consecutive days re-surface the same articles, so _drop_already_pulled
# removes any URL an earlier daily file already stored.
#
# DO NOT READ THE 3d/7d/14d LADDER ABOVE AS CURRENT. Re-measured 2026-08-25, same code, live gather,
# anchor 2026-08-25, lookback=14: 37 in-window, of which 34 were 0-7 days old and THREE were 8-14.
# Widening 7 -> 14 buys +8.8%, not the +57% the ladder implies. The ladder was one measurement on one
# sweep and it has not held; it stays recorded because it explains why lookback=1 was abandoned, which
# is still true, but it is not evidence for widening further.
#
# THE BINDING CONSTRAINT IS forward_gather's `freeze_cap`, NOT THIS WINDOW. Same run:
#   438 raw -> 434 triaged -> 160 FETCHED -> 37 in-window   (93 out-of-window, 30 undateable)
# 434 candidates were triaged and only 160 fetched (`survivors = triaged[:freeze_cap]`); the other
# ~274 are discarded before their date is ever read. At the 23% in-window yield of what IS fetched,
# that is roughly 60 more articles -- a ~2.6x lever on the higher-quality engine, against this
# window's 1.09x. Unlike a date filter it is not free: freeze_cap bounds one HTTP fetch per article.
# A further 30 (8% of fetched) died as UNDATEABLE, already paid for -- a cheaper lever still.
_ANTHROPIC_LOOKBACK = 7


def _drop_already_pulled(arts: list, daily_dir: Path, today_key: str, back: int = 21) -> list:
    """Drop articles whose URL an earlier daily file already captured.

    The Anthropic pass looks back `_ANTHROPIC_LOOKBACK` days, so without this the same article would be
    re-stored every day for a week and the weekly scan would read it as several independent mentions --
    which would corrupt evscore's coverage-velocity signal (it counts mentions per scan)."""
    seen: set = set()
    for f in sorted(daily_dir.glob("*.json"))[-back:]:
        if f.stem >= today_key:
            continue
        try:
            for a in (json.loads(f.read_text()).get("pool") or []):
                u = (a.get("url") or "").split("?")[0].rstrip("/").lower()
                if u:
                    seen.add(u)
        except Exception:  # noqa: BLE001 -- a corrupt old file must not block today's pull
            continue
    out = [a for a in arts
           if (a.get("url") or "").split("?")[0].rstrip("/").lower() not in seen]
    if len(out) != len(arts):
        print(f"    anthropic: {len(arts) - len(out)} of {len(arts)} already pulled on an earlier day")
    return out


def pull_day(model: str, gather_engine: str = "both", scheduled: bool = False) -> None:
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
    # MERGE, NEVER SKIP AND NEVER OVERWRITE. This used to return early when the day already had a
    # file, which meant a throwaway TEST pull permanently blocked that day's scheduled pull: on
    # 2026-08-24 a manual run captured 29 articles at 21:25 and the 06:30 cron would have skipped
    # the day, where re-pulling later actually got 51. Overwriting is no better in the other
    # direction -- measured the same day, re-querying a window Tavily had already served returned
    # FEWER and PARTLY DIFFERENT results (2 back, 1 of them one the first pull never had), so the
    # later pull is not a superset. The pull is unrepeatable, so BOTH skip and overwrite throw away
    # articles that cannot be re-fetched. Union by URL keeps whatever either run found, and strictly
    # dominates both. Same shape as PWR's forward corpus: unique articles + one row per sighting.
    _prior: list = []
    if out.exists():
        try:
            _prior = json.loads(out.read_text()).get("pool") or []
            print(f"  daily pull {dk}: {len(_prior)} article(s) already on file; MERGING, not replacing.")
        except Exception as _e:  # noqa: BLE001 -- a corrupt file must not block today's pull
            print(f"  daily pull {dk}: existing file unreadable ({_e}); starting fresh.", file=sys.stderr)
    print(f"  daily {gather_engine} pull for {dk} (past-24h window) ...", flush=True)
    cap: dict = {}
    if gather_engine == "tavily":
        arts = forward_gather_tavily.gather(None, model, day, 1, capture=cap, cap=0)
    elif gather_engine == "both":                           # UNION: Anthropic + Tavily, deduped by URL
        acap, tcap = {}, {}
        # PER-ENGINE ISOLATION. These are two independent retrievals unioned by URL, and a failure in
        # one says nothing about the other -- but an exception used to escape pull_day entirely and
        # lose BOTH. That is what happened on 2026-08-15: the Anthropic pass raised a 400
        # (`container_id is required ...`), the pull died with "daily pull failed", no file was
        # written, and Tavily's articles -- which had been fetched fine -- went with it. The pull is
        # unrepeatable, so the day is gone permanently. It read afterwards as a cron that never fired.
        # A dead engine is already reported loudly below; this makes the report TRUE for the crash
        # case, not just the empty case.
        def _try(engine_name, fn):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                print(f"  !! {engine_name} gather RAISED ({type(e).__name__}: {str(e)[:200]}). "
                      f"Continuing on the other engine so this day is not lost.", file=sys.stderr,
                      flush=True)
                return []
        a_arts = _try("anthropic", lambda: forward_gather.gather(
            anthropic.Anthropic(), model, day, _ANTHROPIC_LOOKBACK, capture=acap, cap=0))
        a_arts = _drop_already_pulled(a_arts, daily_dir, dk)
        t_arts = _try("tavily", lambda: forward_gather_tavily.gather(
            None, model, day, 1, capture=tcap, cap=0))
        arts = forward_gather.merge_pools(a_arts, t_arts)
        cap["arts"] = arts
        cap["queries"] = (acap.get("queries") or []) + (tcap.get("queries") or [])
        print(f"    union: anthropic {len(a_arts)} + tavily {len(t_arts)} -> {len(arts)} deduped")
        # FAIL LOUD ON A DEAD ENGINE. The union masks a broken half: "anthropic 0 + tavily 139" reads
        # like a normal line, and that is exactly how the Anthropic pass stayed dead for 32 days while
        # the corpus quietly lost the Cloudflare-walled specialty desks Tavily cannot reach. The two
        # engines are COMPLEMENTARY, not redundant -- measured over 34 overlapping days, GKG and the
        # web-search corpus shared 2.3% of URLs -- so a silent zero is a real hole, not a rounding error.
        for _eng, _n in (("anthropic", len(a_arts)), ("tavily", len(t_arts))):
            if _n == 0:
                print(f"  !! WARNING: the {_eng} gather returned ZERO articles for {dk}. The pull is "
                      f"running on one engine and this day's corpus has a coverage hole.", file=sys.stderr)
    else:
        arts = forward_gather.gather(anthropic.Anthropic(), model, day, 1, capture=cap, cap=0)  # uncapped daily

    # SAME TITLE-SPAM FILTER THE BACKTEST APPLIES (gkg._spam_title, from retrieval_config.json).
    # Until 2026-08-14 the forward pull filtered by DOMAIN only (specialty_allow / mill_block), while the
    # backtest additionally dropped listicle/price-target titles -- so the two corpora were filtered to
    # different standards and were not comparable. Measured over the 1,875 articles accumulated by then it
    # removes ~1.1% ("9 Best Stocks To Buy Now For August 2026"), i.e. mill_block already catches most of
    # it; this closes the rest so the bootstrap can splice backtest and forward news without a filtering
    # seam. Applied at WRITE time (not in the gather) so it covers every engine branch above equally.
    _pool = cap.get("arts", arts)
    _kept = [a for a in _pool if not gkg._spam_title(a.get("title") or "")]
    if len(_kept) != len(_pool):
        print(f"    spam-title filter: dropped {len(_pool) - len(_kept)} of {len(_pool)}")
    _merged = forward_gather.merge_pools(_prior, _kept) if _prior else _kept
    _added = len(_merged) - len(_prior)
    cap["arts"] = _merged
    out.write_text(json.dumps({"date": dk, "model": model, "pool": _merged,
                               "queries": cap.get("queries", [])}, indent=2, default=str))
    # PER-PULL MANIFEST, so a day's provenance is queryable rather than inferred. PWR keeps one row
    # per pull for exactly this reason ("GAPS are first-class ... a missed/empty pull is recorded,
    # not silent"), and this repo learned the same lesson the hard way: 2026-08-15 read all week as
    # a cron that never fired when in fact the pull crashed. `scheduled` distinguishes the cron from
    # a hand-run so a test pull's contribution stays visible in the record instead of being
    # indistinguishable from production.
    try:
        with (daily_dir.parent / "pulls.jsonl").open("a") as _f:
            _f.write(json.dumps({
                "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(), "anchor": dk,
                "scheduled": bool(scheduled), "engine": gather_engine, "model": model,
                "prior": len(_prior), "found": len(_kept), "added": _added, "total": len(_merged),
            }) + "\n")
    except Exception as _e:  # noqa: BLE001 -- the manifest is provenance, never a gate
        print(f"    pulls manifest not written ({_e})", file=sys.stderr)
    if _prior:
        print(f"  pulled {len(_kept)} articles, {_added} new -> {len(_merged)} total in {out}")
    else:
        print(f"  pulled {len(_merged)} articles -> {out}")


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
    pk = None
    if fm.get("picker_model"):                        # the weekly max_agents cull = the LLM agent-picker
        import picker                                  # noqa: PLC0415
        pk, pstats = picker.make_picker(fm)
    bt = firehose.backtest(scans, fm, cap, picker=pk)
    if pk:
        print(f"  agent-picker: {pstats()[1]}, {pstats()[0]} LLM calls (rest cached)")
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
    # the audit runs on Anthropic (it's a web-search-free critique); resolve the Anthropic gather model,
    # never the (possibly non-Anthropic) event model, else make_client("anthropic", <llama-id>) would break.
    model_id = resolve_curator_model(_cfg.get("gather_model") or _cfg.get("event_agent_model")
                                     or _cfg.get("model") or "sonnet5")[0]
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
    ap.add_argument("--log-picker", nargs="?", const="__default__", default=None, dest="log_picker",
                    help="AUDIT the scout + agent pickers: log every cull's full inputs+outputs to "
                         "data/forward/picker_decisions.jsonl (or PATH). OFF by default.")
    ap.add_argument("--model", default=None,
                    help="curator model id override; default = investor_profile.forward.md's model knob "
                         "(e.g. sonnet5 -> claude-sonnet-5). Must be an Anthropic model (web search).")
    ap.add_argument("--explain", nargs="?", const="", default=None, metavar="WEEK",
                    help="audit why the scout kept few/no gems for a week (default: latest archive); no web search")
    ap.add_argument("--scheduled", action="store_true",
                    help="mark this pull as the CRON's, not a hand-run, in data/forward/pulls.jsonl. "
                         "Provenance only -- it changes nothing about what is fetched or kept. Use "
                         "--sandbox DIR for throwaway TEST pulls so they never touch the live series.")
    ap.add_argument("--sandbox", default=None, metavar="DIR",
                    help="THROWAWAY run: redirect journal/scan-log/archive under DIR (isolates from the live series)")
    ap.add_argument("--lookback-days", type=int, default=None, dest="lookback_days",
                    help="trailing days of news each scan READS (0/unset = follow the cadence). "
                         "Mirrors firehose.py's --lookback-days so backtest and forward take the "
                         "same override; the profile knob is news_lookback_days.")
    ap.add_argument("--rebalance-days", type=int, default=None, dest="rebalance_days",
                    help="override the gather window in days (e.g. 28 for a 4-week prototype); default from profile")
    ap.add_argument("--anchor", default=None, metavar="YYYY-MM-DD",
                    help="explicit week-ending anchor (e.g. a recent Friday); default = most recent cron anchor")
    ap.add_argument("--pull", action="store_true",
                    help="daily 1-day Anthropic news pull; accumulates for the weekly --scan (no LLM scout)")
    ap.add_argument("--gather", choices=["anthropic", "tavily"], default=None,
                    help="gather engine override: anthropic | tavily | both (default = both — union of the two)")
    args = ap.parse_args(argv)
    if args.trace is not None:
        tp = str(SCANS_CSV.parent / "transcript.jsonl") if args.trace == "__default__" else args.trace
        trace.enable(tp)
        print(f"  TRACE ON -> {tp}", flush=True)
    if args.log_picker is not None:
        import picker_log  # noqa: PLC0415
        lp = str(SCANS_CSV.parent / "picker_decisions.jsonl") if args.log_picker == "__default__" else args.log_picker
        picker_log.enable(lp)
        print(f"  PICKER LOG ON -> {lp}", flush=True)
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
        (scout_id, scout_prov), (event_id, event_prov) = resolve_stage_models(fm)
        gather_id, gather_prov = resolve_gather_model(fm)
        if args.model:                          # explicit override wins — sets the Anthropic GATHER model
            gather_id, gather_prov = args.model, "anthropic"
        if gather_prov != "anthropic":          # ONLY the gather does web search (Anthropic-only); event may be any provider
            print(f"ERROR: forward --scan needs an Anthropic gather_model (the web-search firehose is "
                  f"Anthropic-only); '{fm.get('gather_model') or fm.get('event_agent_model') or fm.get('model')}' "
                  f"resolves to provider '{gather_prov}'. Pass --model <anthropic-id>. "
                  f"(event_agent_model and scout_model may be any provider.)", file=sys.stderr)
            return 2
        # SET THE READ CAPS FROM THE PROFILE, as backtest_gdelt does. forward.py never did, so it ran
        # on agent.py's module defaults and the profile knobs were silently inert on the LIVE path --
        # they happen to agree today (both 800), which is exactly why nobody noticed. A knob the
        # backtest honours and the forward ignores makes the backtest a proxy for something the
        # forward is not running.
        import agent as _ag                     # noqa: PLC0415
        _ag.MAX_ARTICLE_CHARS = int(fm.get("max_article_chars") or _ag.MAX_ARTICLE_CHARS)
        _ag.SCOUT_ARTICLES_PER_CALL = int(fm.get("scout_articles_per_call")
                                          or _ag.SCOUT_ARTICLES_PER_CALL)
        _ag.MIN_BUNDLE_ARTICLES = int(fm.get("min_bundle_articles") or _ag.MIN_BUNDLE_ARTICLES)
        rebal = args.rebalance_days or resolve_cadence(fm)
        anch = pd.Timestamp(args.anchor, tz="America/New_York") if args.anchor else None
        scan_and_log(event_id, rebal, int(fm.get("curator_memory_weeks", 8)), anchor=anch,
                     news_cap=int(fm.get("news_cap", 0)),
                     gather_engine=(args.gather or str(fm.get("gather_engine", "both"))),
                     scout_model=scout_id, scout_provider=scout_prov,
                     gather_model=gather_id, event_provider=event_prov,
                     news_lookback_days=int(args.lookback_days if args.lookback_days is not None
                                            else (fm.get("news_lookback_days") or 0)),
                     fm=fm)

    if args.pull:
        load_dotenv()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
            return 2
        fm = load_financial_model(str(PROFILE))
        gather_id, _gp = resolve_gather_model(fm)                   # daily pull is gather-ONLY (Anthropic web search)
        pull_day(args.model or gather_id, gather_engine=(args.gather or str(fm.get("gather_engine", "both"))),
                 scheduled=args.scheduled)

    if args.report:
        report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
