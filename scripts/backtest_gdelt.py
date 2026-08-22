#!/usr/bin/env python3
"""backtest_gdelt.py — SEEDLESS, look-ahead-clean continuous backtest over an arbitrary window.

Pulls GDELT (date-indexed) + Wayback (as-of-date ledes) — NO live search (no future-leak), NO seeds. Runs
the current curator week by week. Writes each week's archive PLUS an incremental scan-log/journal after every
week, so a partial dashboard can be built while the slow Wayback enrich is still running. Fully RESUMABLE:
GDELT/Wayback caches survive interruption, and on restart it reloads the journal state and skips scanned weeks.

    python scripts/backtest_gdelt.py --start 2024-04-23 --end 2026-07-02 --out data/backtest_gdelt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pandas as pd  # noqa: E402
import agent  # noqa: E402
import firehose  # noqa: E402
import relevance  # noqa: E402
import gdelt as gd  # noqa: E402
import lede  # noqa: E402  two-speed ledes (clean `lede` + fast `lede_live`)
import llm  # noqa: E402
from util import load_dotenv, scan_anchors, resolve_cadence  # noqa: E402
from optimizer import load_financial_model, resolve_stage_models  # noqa: E402

CFG = ("event_agent_model", "scout_model", "concentration_cap", "risk_aversion", "lookback_period_days",
       "max_agents", "max_watchlist", "always_include", "starter_watchlist",
       "spy_agent_conviction", "defensive_agent_conviction", "defensive_ticker", "rebalance_days", "rebalance_period", "event_news_cap", "relevance_keep", "relevance_filter", "max_event_scans",
       "retrieval_engine")   # provenance: which discovery engine produced this week's pool


# The old hand-rolled `live_enrich` lived here. It is superseded by src/lede.py, which does the same
# job correctly: a title-consistency gate (rejects URL recycles -- 14.5% of fetches, measured), a
# per-host throttle, retries, an on-disk cache, and -- the important part -- it writes to `lede_live`
# rather than stomping `snippet`, so the fast and clean ledes coexist and the choice between them is
# made at RENDER time by lede.apply(). The old version also shared the meta-regex bug that was
# emitting raw markup as ledes (see wayback._extract_lede).


def rebuild_dashboard(sandbox, out: str, wk: str) -> None:
    """Incremental: rebuild THIS week's as-of page + refresh the All-weeks index, so the weekly
    dashboards update as each week's GDELT+curator completes. Wrapped so a render error never aborts
    the pull (the archives are already flushed; the dashboard can be rebuilt later regardless)."""
    try:
        import build_forward_dashboard as bfd
        bfd.build(str(sandbox), out, wk, [])       # this week's frozen as-of page
        bfd.build(str(sandbox), out, None, [])     # refresh index/landing + latest-week page
        print(f"    dashboard updated -> {out} (through {wk})", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"    dashboard rebuild skipped ({wk}): {e}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-new-events", type=int, default=None, dest="max_new_events_cli",
                    help="override the profile's max_new_events (the per-scan quality gate); 0 = uncapped")
    ap.add_argument("--workers", type=int, default=24,
                    help="concurrent event-agent calls per scan. Live events plateau near 40, so the "
                         "old default of 8 meant 5-6 serial waves per scan; OpenRouter shows no 429s "
                         "at 24 and llm.py backs off if that changes.")
    ap.add_argument("--rebalance-days", type=int, default=None, dest="rebalance_days",
                    help="override the profile's cadence (rebalance_period/rebalance_days)")
    ap.add_argument("--relevance-filter", dest="relevance_filter", action="store_true", default=None,
                    help="ON: cheap-LLM relevance filter at pool assembly (the forward search-ranker "
                         "stand-in). No quota -- pool size floats with the week's news.")
    ap.add_argument("--relevance-keep", type=int, default=None, dest="relevance_keep",
                    help="SAFETY CEILING on the filtered pool; 0 = none (intended)")
    ap.add_argument("--event-news-cap", type=int, default=None, dest="event_news_cap",
                    help="articles handed to EACH event-agent per scan (the cost knob); "
                         "omit to use the profile's event_news_cap")
    ap.add_argument("--min-bundle-articles", type=int, default=None, dest="min_bundle_articles",
                    help="company bundles smaller than this are DEMOTED to the unclustered/beat path "
                         "(articles still shown). 1 = every bundle qualifies (default).")
    ap.add_argument("--news-cap", type=int, default=None, dest="news_cap",
                    help="per-week cap on articles the scout reads (most-recent kept); 0 = UNCAPPED. "
                         "Omit to use the profile's news_cap.")
    # BAKE-OFF OVERRIDES (2026-08-17). The event-agent stage is the one being swept, so its model and
    # reasoning effort have to be settable per RUN -- editing investor_profile.backtest.md between arms
    # would make the live config depend on whichever arm finished last. Both expose existing profile
    # knobs, exactly as --news-cap and --event-news-cap already do.
    ap.add_argument("--event-agent-model", default=None, dest="event_agent_model",
                    help="override the profile's event_agent_model (a CURATOR_MODELS short name)")
    ap.add_argument("--event-agent-effort", default=None, dest="event_agent_effort",
                    choices=["none", "low", "medium", "high"],
                    help="reasoning effort for the per-event JUDGMENT call; omit to use the profile's "
                         "event_agent_effort")
    # Exposes an EXISTING profile knob on the CLI, exactly as --news-cap and --event-news-cap already
    # do; it is not a new profile parameter. Added 2026-08-17 so the news window can be varied for a
    # side experiment without editing investor_profile.backtest.md, which the live config depends on.
    ap.add_argument("--news-lookback-days", type=int, default=None, dest="news_lookback_days",
                    help="trailing days of news each scan READS, decoupled from the trading cadence; "
                         "0 = track the cadence. Omit to use the profile's news_lookback_days.")
    ap.add_argument("--wayback-cap", type=int, default=0, help="enrich only the top-N/week (0 = all in news-cap)")
    ap.add_argument("--trace", nargs="?", const="__default__", default=None,
                    help="log every LLM prompt/response + search query to <out>/transcript.jsonl (or PATH)")
    ap.add_argument("--enrich", choices=("none", "live", "wayback", "both"), default="wayback",
                    help="which lede FETCH passes to run: none=headlines only; live=today's page "
                         "(fast, ~80x, look-ahead-BIASED -> lede_live); wayback=as-of archive "
                         "(slow, clean -> lede); both=live then wayback. Passes ACCUMULATE into "
                         "separate fields, so re-running adds an arm instead of overwriting one.")
    ap.add_argument("--arm", choices=lede.ARMS, default="fuller",
                    help="which text the curator actually READS, chosen at render time: "
                         "clean=lede only (the ONLY arm whose numbers are quotable); "
                         "fuller=lede then lede_live (default: ~98%% coverage, ~95%% of it clean); "
                         "fast=lede_live then lede; live-only=lede_live (the do-we-need-Wayback control)")
    ap.add_argument("--gentle", action="store_true",
                    help="run the wayback pass at the overnight pace (~1 req/s, 4 workers) so a long "
                         "unattended fill doesn't trip archive.org's throttle")
    ap.add_argument("--by-week", action="store_true",
                    help="pull ALL beats for week i BEFORE week i+1, processing each as it completes "
                         "(enables incremental dashboards during a long pull); default = whole-window up-front")
    ap.add_argument("--corpus", default=None,
                    help="consume a PRE-BUILT corpus dir from scripts/ingest.py (reads its pool.json) "
                         "instead of re-deriving discovery and re-fetching ledes. This is the intended "
                         "path: ingest builds the corpus once -- BigQuery rows, live ledes, archived "
                         "ledes -- and the curator reads it. Without it a run re-fetches every lede "
                         "under different cache filenames, discarding hours of archive.org work.")
    ap.add_argument("--no-pull", action="store_true",
                    help="skip GDELT fetching entirely; curate on the EXISTING <out>/gdelt_pool.json as-is "
                         "(prototype dashboards on a partial pool without disturbing a live pull)")
    ap.add_argument("--decisions", action="store_true",
                    help="record every cull decision to <out>/decisions.jsonl (src/picker_log.py): the "
                         "scout's PROPOSED vs ADMITTED candidates, and the picker's keep-list vs culled. "
                         "Without this a low breadth reading is ambiguous -- you cannot tell a narrow "
                         "curator from a tight max_new_events cap.")
    ap.add_argument("--dashboard", default=None,
                    help="after EACH week, rebuild the forward dashboard at this dir (weekly dbs update as "
                         "each week's GDELT+curator completes); pairs naturally with --by-week")
    a = ap.parse_args(argv)
    load_dotenv()
    OUT = Path(a.out)
    (OUT / "archive").mkdir(parents=True, exist_ok=True)
    if a.decisions:
        import picker_log
        picker_log.enable(OUT / "decisions.jsonl")
        print(f"  DECISION LOG ON -> {OUT / 'decisions.jsonl'}", flush=True)
    if a.trace is not None:
        import trace
        tp = str(OUT / "transcript.jsonl") if a.trace == "__default__" else a.trace
        trace.enable(tp)
        print(f"  TRACE ON -> {tp} (every LLM prompt/response + search query)", flush=True)
    # Cadence comes from the profile (rebalance_period / rebalance_days). This was HARDCODED to 7
    # until 2026-08-09, which quietly made the profile's cadence knob decorative for the backtest.
    fm = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    cadence = a.rebalance_days if a.rebalance_days else resolve_cadence(fm)
    # One knob for how much of ONE article the curator sees. Set on the module so every call
    # site (scout blocks, event-agent blocks, lede.apply) cuts at the same place.
    agent.MAX_ARTICLE_CHARS = int(fm.get('max_article_chars') or agent.MAX_ARTICLE_CHARS)
    agent.SCOUT_ARTICLES_PER_CALL = int(fm.get('scout_articles_per_call') or agent.SCOUT_ARTICLES_PER_CALL)
    if a.min_bundle_articles is not None:            # sweep arm: override the profile
        fm = {**fm, "min_bundle_articles": a.min_bundle_articles}
    agent.MIN_BUNDLE_ARTICLES = int(fm.get('min_bundle_articles') or 1)
    print(f"  min_bundle_articles={agent.MIN_BUNDLE_ARTICLES} · "
          f"max_article_chars={agent.MAX_ARTICLE_CHARS} · "
          f"scout_articles_per_call={agent.SCOUT_ARTICLES_PER_CALL} · "
          f"group_by_ticker={agent.GROUP_BY_TICKER}",
          flush=True)
    # The news window is decoupled from the trading cadence: `news_lookback_days` > cadence reads an
    # overlapping stretch so a late-indexed or boundary-straddling article is not lost to the gap.
    news_win = int(a.news_lookback_days if a.news_lookback_days is not None
                   else (fm.get("news_lookback_days") or 0)) or cadence
    anchors = scan_anchors(a.start, a.end, cadence)
    win_start = anchors[0] - pd.Timedelta(days=10)
    cache_f = str(OUT / "gdelt_pool.json")
    stats = str(OUT / "retrieval_stats.json")
    enrich_cache = str(OUT / "wayback.json")     # clean arm: url -> as-of lede | false
    live_cache = str(OUT / "lede_live.json")     # fast arm: url -> today's lede | false
    print(f"  {len(anchors)} scans every {cadence}d, reading {news_win}d of news"
          f"{' (OVERLAP '+str(news_win-cadence)+'d)' if news_win>cadence else ''}  "
          f"{anchors[0].date()} .. {anchors[-1].date()}", flush=True)

    gpool = None
    if a.corpus:
        cdir = Path(a.corpus)
        cd = json.loads((cdir / "pool.json").read_text())
        gpool = cd.get("articles", cd) if isinstance(cd, dict) else cd
        # the corpus already carries its ledes in `lede` / `lede_live`; re-fetching would be pure waste
        a.enrich = "none"
        cov = {"clean": sum(1 for x in gpool if x.get("lede")),
               "live": sum(1 for x in gpool if x.get("lede_live") and not x.get("lede")),
               "none": sum(1 for x in gpool if not (x.get("lede") or x.get("lede_live")))}
        print(f"  CORPUS {cdir}: {len(gpool):,} articles "
              f"({cov['clean']:,} archived · {cov['live']:,} live · {cov['none']:,} headline-only); "
              f"enrichment skipped", flush=True)
    elif a.no_pull:                                            # curate on whatever's already cached (partial-pool prototype)
        d = json.loads(Path(cache_f).read_text()) if Path(cache_f).exists() else {}
        gpool = list(d.get("articles", []))
        print(f"  NO-PULL: {len(gpool)} cached articles ({d.get('progress', '?')})", flush=True)
    elif not a.by_week:                                      # whole-window up-front (default)
        print("  pulling GDELT (whole-window, date-indexed, resumable) ...", flush=True)
        gpool = firehose.news_pool(firehose.GDELT_QUERIES, win_start, anchors[-1], chunk_days=7,
                                   per=80, cache_path=cache_f, stats_path=stats)
        print(f"  GDELT pool: {len(gpool)} articles", flush=True)
    else:                                                    # per-week: pull inside the loop (all beats/week, then process)
        print("  BY-WEEK pull: all beats per week, each processed before the next (incremental)", flush=True)

    if a.event_agent_model:                      # bake-off arm: swap only the JUDGMENT stage
        fm = {**fm, "event_agent_model": a.event_agent_model}
    ev_effort = a.event_agent_effort or str(fm.get("event_agent_effort") or "high")
    (scout_id, scout_prov), (event_id, event_prov) = resolve_stage_models(fm)
    memw = int(fm.get("curator_memory_weeks", 8))
    # The event-concurrency picker. Built once; None unless BOTH knobs are set, so an unset picker_model
    # cannot silently turn max_events into "keep the first N", which would be a mechanical cull wearing
    # the picker's name.
    _picker = None
    if int(fm.get("max_events") or 0) and fm.get("picker_model") and not os.environ.get("GHR_NO_PICKER"):
        import picker as _pk
        _picker, _pstats = _pk.make_picker(fm)
        print(f"  event-picker ON: cap {fm.get('max_events')} via {_pstats()[1]}", flush=True)
    news_cap = a.news_cap if a.news_cap is not None else int(fm.get("news_cap", 0))
    ev_cap = a.event_news_cap if a.event_news_cap is not None else int(fm.get("event_news_cap", 20))
    rel_keep = a.relevance_keep if a.relevance_keep is not None else int(fm.get("relevance_keep", 0))
    max_ev_scans = int(fm.get("max_event_scans", 0) or 0)
    # NEVER passed through until 2026-08-10: process_week fell back to CANDIDATE_CAP=3, so the
    # profile's max_new_events was decorative for the whole backtest.
    max_new = (a.max_new_events_cli if a.max_new_events_cli is not None
               else int(fm.get("max_new_events", agent.CANDIDATE_CAP)))
    rel_on = bool(a.relevance_filter if a.relevance_filter is not None else fm.get("relevance_filter", False))
    scout_cli = llm.make_client(scout_prov, scout_id)         # cheap extraction/routing (scout + matcher)
    event_cli = llm.make_client(event_prov, event_id)         # judgment (event agents)
    print(f"  scout={scout_id} ({scout_prov}) · event_agent={event_id} ({event_prov}) · news_cap={news_cap or 'uncapped'} · event_news_cap={ev_cap or 'uncapped'}", flush=True)
    print(f"  ARM: event_agent={event_id} @ effort={ev_effort}", flush=True)

    # RESUME: reload journal state + skip weeks already scanned
    events, retired, nid, rows, done = {}, {}, 0, [], set()
    metrics: list[dict] = []          # per-week curator funnel; see the writer below
    jf, sf = OUT / "journal.json", OUT / "firehose_scans.csv"
    if jf.exists():
        j = json.loads(jf.read_text())
        events = {k: {**v, "vehicles": set(v["vehicles"])} for k, v in j.get("events", {}).items()}
        retired, nid = j.get("retired", {}), int(j.get("nid", 0))
        mf = OUT / "curator_metrics.json"
        if mf.exists():                              # resume without truncating the metric series
            metrics = json.loads(mf.read_text())
        done = {p.stem for p in (OUT / "archive").glob("*.json")}
        if sf.exists():
            rows = pd.read_csv(sf).fillna("").to_dict("records")
        print(f"  RESUME: {len(done)} weeks done, {len(events)} events in state", flush=True)

    # STAMP THE RUN WITH THE INPUTS IT IS ABOUT TO BE CURATED UNDER, before any week is processed.
    # `fm` here is the EFFECTIVE config -- the profile after every CLI override above (--news-cap,
    # --event-agent-model, --relevance-filter, ...), which is how bake-off arms and any sweep that
    # re-reads the news are produced. Recording the effective values is the whole point: an arm's
    # fingerprint then differs from the profile's by construction, so the dashboards recognise it as
    # non-canonical and refuse to publish it. See src/provenance.py.
    import provenance as _prov
    _prov.stamp(OUT, fm, a.corpus or "(gdelt-live)", arm=a.arm, argv=sys.argv[1:])

    ts = datetime.now(timezone.utc).isoformat()

    def flush():                                             # incremental -> partial dashboards buildable anytime
        pd.DataFrame(rows).to_csv(sf, index=False)
        (OUT / "journal.json").write_text(json.dumps(
            {"events": {k: {**v, "vehicles": sorted(v["vehicles"])} for k, v in events.items()},
             "retired": retired, "nid": nid, "week_seq": len(anchors)}, indent=2, default=str))

    for i, anch in enumerate(anchors):
        wk = anch.date().isoformat()
        if wk in done:
            continue
        if a.by_week and not a.no_pull and not a.corpus:     # pull THIS week's beats now, then process it
            gpool = firehose.news_pool(firehose.GDELT_QUERIES, anch - pd.Timedelta(days=news_win), anch,
                                       chunk_days=7, per=80, cache_path=cache_f, stats_path=stats)
        _raw = sorted(firehose._window(gpool, anch, news_win),  # news window; >= cadence gives an overlap
                      key=lambda x: x.get("published_date", ""), reverse=True)
        if rel_on:                  # stand in for the forward's search-engine relevance ranking
            _before = len(_raw)
            _raw = relevance.rank_pool(scout_cli, _raw, rel_keep, anchor=anch, enabled=True,
                                       cache_path=str(OUT / "relevance_cache.json"),
                                       dropped_path=str(OUT / "relevance_dropped.jsonl"))
            if _before != len(_raw):
                print(f"    relevance: {_before} -> {len(_raw)} articles", flush=True)
        gslice = _raw[:news_cap] if news_cap else _raw          # news_cap=0 -> UNCAPPED (keep all)
        if news_cap and len(_raw) > news_cap:                    # surface silent drops, don't hide them
            print(f"    !! news-cap dropped {len(_raw) - news_cap} of {len(_raw)} articles "
                  f"(oldest-in-window) at {wk}", flush=True)
        enrich_slice = gslice[:a.wayback_cap] if a.wayback_cap else gslice
        # Two-speed ledes (src/lede.py): the passes ACCUMULATE into `lede` (clean) and `lede_live`
        # (fast, biased) rather than competing for `snippet`, so a week can be enriched by the fast
        # arm today and back-filled by the clean arm tonight without losing either.
        if a.enrich in ("live", "both"):
            lede.enrich_live(enrich_slice, cache_path=live_cache)
        if a.enrich in ("wayback", "both"):
            lede.enrich_wayback(enrich_slice, wk, cache_path=enrich_cache, gentle=a.gentle,
                                stats_path=stats)
        # a.enrich == "none": GDELT headlines only, no enrichment.
        # Render-time arm selection: this is what fills `snippet`, the field the curator reads.
        _arm = lede.apply(gslice, arm=a.arm)
        print(f"    ledes[{a.arm}]: {_arm['coverage_pct']}% covered "
              f"({_arm['wayback']} clean, {_arm['live']} live, {_arm['headline_only']} headline-only)",
              flush=True)
        for x in gslice:
            x["engine"] = "gdelt"
        picks, nid = agent.process_week(event_cli, anch, gslice, events, retired, nid, i,
                                        curator_memory_weeks=memw, scout_client=scout_cli,
                                        discovery_filter=bool(fm.get('discovery_filter')),
                                        max_events=int(fm.get('max_events') or 0), picker=_picker,
                                        event_news_cap=ev_cap, max_event_scans=max_ev_scans,
                                        max_new_events=max_new, workers=a.workers,
                                        event_agent_effort=ev_effort)
        live = [p for p in picks if p["thesis_live"]]
        print(f"  {wk} ({i + 1}/{len(anchors)}): {len(gslice):3} arts -> "
              f"{[p['ticker'] for p in live] or 'none'}", flush=True)
        (OUT / "archive" / f"{wk}.json").write_text(json.dumps(
            {"week": wk, "model": event_id, "pool": gslice, "queries": [], "raw_results": [],
             "config": {**{k: fm.get(k) for k in CFG}, "news_cap": news_cap}}, indent=2, default=str))
        # Write EVERY pick, live or not -- not just the live ones. The dead/resolved reads ARE the
        # exit signal: firehose._stateful_watch hard-exits the moment it sees catalyst_resolved, and
        # exits after EXIT_PATIENCE explicit thesis_live=False reads. Filtering them out (as this did
        # until 2026-08-09) left the backtest with no exit path except the MAX_STALE silence timeout,
        # so every position ran 4 weeks past the agent's own call to close it.
        if picks:
            for p in picks:
                rows.append({"decision_ts": ts, "week": wk, "ticker": p["ticker"], "thesis": p["thesis"],
                             "thesis_live": bool(p.get("thesis_live", True)),
                             "catalyst_resolved": bool(p.get("catalyst_resolved", False)),
                             "evidence_urls": ";".join(p.get("evidence_urls", []) or [])})
        else:
            rows.append({"decision_ts": ts, "week": wk, "ticker": "", "thesis": "", "thesis_live": "",
                         "catalyst_resolved": "", "evidence_urls": ""})
        # PER-WEEK CURATOR METRICS -- the curator's own funnel, the analogue of the FBT's plot 1.
        # Volume alone cannot say whether the curator is working; what matters is how much reaches
        # each stage. Written every week so a partial run is still readable.
        _liveev = [e for e in events.values() if e["status"] == "live"]
        _cats = {e["catalyst"] for e in _liveev}
        metrics.append({
            "week": wk,
            "articles_read": len(gslice),
            # TRACK THE GATE AT THE SOURCE. The discovery gate is the single largest reduction in the
            # whole pipeline (~19x: 2,659 -> 140 per scan on v15) and nothing recorded it, so every
            # dashboard that wanted it had to re-derive it from the corpus. Recording it here is free --
            # the scan computes the same set anyway -- and it means a later reader never has to guess
            # which gate vocabulary was in force at the time. Runs made before 2026-08-14 lack the key;
            # build_cbt_dashboard falls back to recomputing it rather than forcing a re-curation.
            "articles_gated": (len(agent.superlative_pool(gslice))
                               if fm.get("discovery_filter") else len(gslice)),
            "articles_with_text": sum(1 for x in gslice if x.get("snippet") != x.get("title")),
            "lede_clean": _arm["wayback"], "lede_live": _arm["live"],
            "lede_headline_only": _arm["headline_only"],
            "events_live": len(_liveev),
            "events_opened_total": nid,
            "events_exited": sum(1 for e in events.values() if e["status"] != "live"),
            "picks_live": len(live),
            "vehicles_live": len({v for e in _liveev for v in e["vehicles"]}),
            "distinct_catalysts": len(_cats),
            # the caps, recorded alongside so a reading can be attributed to curator or to cap
            "cap_max_new_events": int(fm.get("max_new_events", 0)),
            "cap_max_agents": int(fm.get("max_agents", 0)),
        })
        (OUT / "curator_metrics.json").write_text(json.dumps(metrics, indent=1, default=str))
        flush()
        if a.dashboard:                                      # weekly db updates as each week completes
            rebuild_dashboard(OUT, a.dashboard, wk)

    print(f"  DONE. events: {[(k, v['status'], sorted(v['vehicles'])) for k, v in events.items()]}", flush=True)


if __name__ == "__main__":
    main()
