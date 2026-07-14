#!/usr/bin/env python3
"""backtest_retrieval_curator.py — feed the RETRIEVAL-backtest Tavily pool into the CURATOR.

The retrieval backtest (scripts/retrieval_backtest.py) measures ONLY whether the generic-beat
retriever surfaces a gem's articles. This harness closes the loop: it runs the SAME two-tier curator
(scout -> matcher -> per-event agents, agent.process_week) week-by-week over that retrieved pool, so we
see what the curator actually PICKS from real retrieved coverage — then renders a bwet_v2-style gem
dashboard (portfolio vs SPY vs the gem, agent storyline) via build_dashboard.build_gem.

This is STILL an upper bound (CLAUDE.md #4/#6): the Tavily pool leaks future-dated articles that
search.py re-bounds client-side, and retrieval isn't the clean forward. It answers "given this
retrieved firehose, does the curator name the gem, when, and does sizing pay?" — not the live verdict.

    # one-gem POC (cheap event model set in investor_profile.backtest.md):
    python scripts/backtest_retrieval_curator.py --gem MP
    python scripts/backtest_retrieval_curator.py --gem MP --start 2025-01-01 --end 2025-12-31

Pool source: data/retrieval_backtest.ckpt.json ({"arts": [{url,title,published_date,source,snippet}]}).
Output: data/windows/firehose_scans_<gem>.json ({date: [picks]}) -> then build_gem(<gem>).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import pandas as pd  # noqa: E402
import agent  # noqa: E402
import firehose  # noqa: E402
import llm  # noqa: E402
from util import load_dotenv, scan_anchors  # noqa: E402
from optimizer import load_financial_model, resolve_stage_models  # noqa: E402

CKPT = ROOT / "data" / "retrieval_backtest.ckpt.json"
# per-gem default era (aligned to the Plot-1 window in the retrieval dashboard). Override via --start/--end.
GEM_ERA = {
    "MP":   ("2025-01-01", "2025-12-31"),   # rare-earth run-up to the Oct-2025 peak + early decay
    "TSM":  ("2025-01-01", "2026-07-07"),
    "INTC": ("2026-01-01", "2026-07-07"),
    "NEM":  ("2025-01-01", "2026-07-07"),
    "CIFR": ("2025-04-01", "2026-07-07"),
    "HL":   ("2025-09-01", "2026-07-11"),   # widened: the curator's silver catch is 2026-06, after HL's price run
    "MU":   ("2026-01-01", "2026-07-07"),
    "BWET": ("2025-02-01", "2026-07-11"),   # widened: the big tanker catches launched 2025-02, before BWET's price era
    "GDX":  ("2025-01-01", "2026-07-11"),
    "RNMBY": ("2025-01-01", "2026-02-28"),
}


def load_pool() -> list[dict]:
    """The retrieved article pool (Tavily two-pass sweep), normalized to the curator's expected keys."""
    if not CKPT.exists():
        sys.exit(f"ERROR: {CKPT} not found — run scripts/retrieval_backtest.py first.")
    d = json.loads(CKPT.read_text())
    arts = d["arts"] if isinstance(d, dict) else d
    out = []
    for a in arts:
        pd_ = str(a.get("published_date", ""))[:10]
        if not pd_:
            continue
        out.append({"published_date": pd_, "source": a.get("source", ""), "title": a.get("title", ""),
                    "snippet": a.get("snippet", ""), "url": a.get("url", ""), "engine": "tavily"})
    return out


FULL_WINDOW = ("2025-01-01", "2026-07-07")   # the whole retrieval pool span (forward day-1)


# Each gem db is a THEMATIC sub-book: it shows only the agents on ITS theme (declutters the ~30-agent
# whole-era book). Events on a theme with NO gem (uranium/steel/lithium/quantum/space/biotech/one-offs)
# go to the "OTHER" catch-all db, so no agent is orphaned. Theme keywords = the gems' own thesis vocab.
GEM_THEME = {"MP": "rare-earth", "NEM": "metals", "GDX": "metals", "HL": "metals", "TSM": "chip", "INTC": "chip",
             "MU": "memory", "CIFR": "crypto", "BWET": "tanker", "RNMBY": "defense"}
_THEME_KW = {
    "rare-earth": ["rare earth", "rare-earth", "neodymium", "magnet", "gallium", "critical mineral", "ndfeb"],
    "metals": ["gold miner", "gold mining", "gold price", "bullion", "gold rally", "gold surge", "gold stock", "silver"],
    "chip": ["semiconductor", "foundry", "chipmaker", "tsmc", "intel", "14a", "advanced node", "ai chip", "wafer", "chip factory", "terafab"],
    "memory": ["memory chip", "dram", "hbm", "high-bandwidth", "micron", "memory shortage", "ai memory"],
    "crypto": ["bitcoin", "crypto", "hashrate", "stablecoin"],
    "tanker": ["tanker", "vlcc", "freight", "hormuz", "supertanker", "shipping"],
    "defense": ["defense", "defence", "rearmament", "military", "fighter jet", "missile", "ngad", "rheinmetall"],
}


# theme TICKERS — the robust signal (catalysts are worded too freely to keyword-match reliably). An event
# is assigned to a theme if its vehicle basket overlaps that theme's tickers, else the catalyst keywords.
# gold + silver are ONE "metals" theme: the curator's only precious-metals catch is a single silver event
# (AG/HL/PAAS/SLV/WPM), so splitting them just empties the gold dbs — NEM/GDX/HL all benchmark the metals trade.
_THEME_TICKERS = {
    "rare-earth": {"MP", "USAR", "CRML", "AREC", "TMRC", "UUUU", "REEMF"},
    "metals": {"NEM", "GDX", "GOLD", "AEM", "KGC", "HMY", "AU", "GFI", "WPM", "FNV", "IAU", "GLD", "AAAU", "AGI",
               "BTG", "HL", "AG", "PAAS", "CDE", "MAG", "SIL", "SLV", "FSM", "SVM", "USAS"},
    "chip": {"TSM", "INTC", "NVDA", "AMD", "AVGO", "ASML", "LRCX", "AMAT", "QCOM", "SMH", "SOXX", "SMCI", "MRVL", "TXN"},
    "memory": {"MU", "WDC", "STX", "SNDK", "PSTG"},
    "crypto": {"CIFR", "RIOT", "MARA", "COIN", "BITF", "HUT", "CLSK", "WULF", "BTBT", "CRCL", "BITM", "IREN"},
    "tanker": {"FRO", "DHT", "INSW", "TNK", "STNG", "NAT", "EURN", "TRMD", "BWET"},
    "defense": {"RNMBY", "LMT", "RTX", "NOC", "GD", "BA", "LHX", "AVAV", "KTOS", "ITA", "RCAT"},
}


def classify_theme(catalyst: str, vehicles) -> str:
    """Map an event (catalyst + basket tickers) to a gem theme, or 'other'. Tickers win (robust);
    catalyst keywords are the fallback. Ticker overlap is scored by MAJORITY, not first-match: a silver
    event holding {AG,HL,PAAS,SLV} + one gold-streamer WPM must file under silver (4 hits) not gold (1) —
    first-match let 'gold' (checked first) steal it, emptying the silver db."""
    veh = {str(v).strip().upper() for v in vehicles}
    best, best_n = None, 0
    for theme, tks in _THEME_TICKERS.items():
        n = len(veh & tks)
        if n > best_n:
            best, best_n = theme, n
    if best:
        return best
    hay = (catalyst + " " + " ".join(vehicles)).lower()
    for theme, kws in _THEME_KW.items():
        if any(k in hay for k in kws):
            return theme
    return "other"


def slice_full(full_scans: dict) -> None:
    """Slice ONE full-window curator run into per-THEME gem dbs + one 'OTHER' catch-all. Each gem db =
    events (a) LAUNCHED within the gem's era AND (b) on the gem's theme -> a focused thematic sub-book,
    capital invested at the era start. Every event not placed in a gem db lands in firehose_scans_other.json
    so nothing is orphaned. Cheap + reusable — the curator LLM run happens once."""
    first_seen, veh = {}, {}
    for wk in sorted(full_scans):
        for p in full_scans[wk]:
            th = p.get("thesis", "")
            if th:
                first_seen.setdefault(th, wk)
                veh.setdefault(th, set()).add(p.get("ticker", ""))
    theme_of = {th: classify_theme(th, veh[th]) for th in first_seen}
    assigned = set()                                         # theses placed in >=1 gem db
    for gem, (s, e) in GEM_ERA.items():
        keep = {th for th in first_seen if theme_of[th] == GEM_THEME[gem] and s <= first_seen[th] <= e}
        assigned |= keep
        sub = {wk: [p for p in picks if p.get("thesis", "") in keep]
               for wk, picks in full_scans.items() if s <= wk <= e}
        out = ROOT / "data" / "windows" / f"firehose_scans_{gem.lower()}.json"
        out.write_text(json.dumps(sub, indent=1, default=str))
        print(f"  sliced {gem:6} ({GEM_THEME[gem]}): {len(keep):2} agents, {len(sub)} weeks -> {out.name}", flush=True)
    orphan = {th for th in first_seen if th not in assigned}   # everything on a theme with no gem
    sub = {wk: [p for p in picks if p.get("thesis", "") in orphan] for wk, picks in full_scans.items()}
    (ROOT / "data" / "windows" / "firehose_scans_other.json").write_text(json.dumps(sub, indent=1, default=str))
    print(f"  sliced OTHER  (catch-all): {len(orphan):2} agents, {len(sub)} weeks -> firehose_scans_other.json", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--gem", default=None, help="gem ticker (e.g. MP) — overlay + scans-file name")
    ap.add_argument("--full", action="store_true",
                    help="run the curator ONCE over the whole 2025-01-01..2026-07-07 pool -> firehose_scans_full.json, "
                         "then SLICE into per-gem era files. The economical all-gems build (one LLM run, reusable).")
    ap.add_argument("--start", default=None, help="ISO date; default = the gem's Plot-1 era")
    ap.add_argument("--end", default=None, help="ISO date; default = the gem's Plot-1 era")
    ap.add_argument("--news-cap", type=int, default=None, dest="news_cap",
                    help="per-week cap on articles the scout reads (most-recent kept); 0 = uncapped. "
                         "Omit to use the profile's news_cap.")
    ap.add_argument("--no-render", action="store_true", help="write scans only; skip build_gem")
    a = ap.parse_args(argv)
    load_dotenv()
    if a.full:
        gem = "FULL"
        start, end = a.start or FULL_WINDOW[0], a.end or FULL_WINDOW[1]
    else:
        if not a.gem:
            sys.exit("pass --gem <TICKER> or --full")
        gem = a.gem.upper()
        era = GEM_ERA.get(gem)
        start = a.start or (era[0] if era else None)
        end = a.end or (era[1] if era else None)
        if not (start and end):
            sys.exit(f"no default era for {gem}; pass --start/--end")

    fm = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    (scout_id, scout_prov), (event_id, event_prov) = resolve_stage_models(fm)
    reb = int(fm.get("rebalance_days", 7))
    memw = int(fm.get("curator_memory_weeks", 8))
    aging_floor = int(fm.get("aging_floor", 1))
    aging_patience = int(fm.get("aging_patience", 0))        # 0 = OFF (aging retirement disabled)
    news_cap = a.news_cap if a.news_cap is not None else int(fm.get("news_cap", 0))
    scout_cli = llm.make_client(scout_prov, scout_id)
    event_cli = llm.make_client(event_prov, event_id)

    pool = load_pool()
    anchors = scan_anchors(start, end, reb)
    print(f"Curator-on-retrieval backtest: gem={gem} {start}..{end} ({len(anchors)} weeks, every {reb}d)",
          flush=True)
    print(f"  pool={len(pool)} retrieved arts · scout={scout_id} ({scout_prov}) · "
          f"event_agent={event_id} ({event_prov}) · news_cap={news_cap or 'uncapped'}", flush=True)

    events, retired, nid = {}, {}, 0
    scans: dict[str, list] = {}
    scans_path = ROOT / "data" / "windows" / f"firehose_scans_{gem.lower()}.json"
    journal_path = scans_path.with_name(scans_path.stem + ".journal.json")   # event-state for RESUME
    scans_path.parent.mkdir(parents=True, exist_ok=True)

    if scans_path.exists() and journal_path.exists():        # RESUME: reload scans + event state, skip done weeks
        scans = json.loads(scans_path.read_text())
        j = json.loads(journal_path.read_text())
        events = {k: {**v, "vehicles": set(v["vehicles"])} for k, v in j["events"].items()}
        retired, nid = j["retired"], int(j["nid"])
        print(f"  RESUME: {len(scans)} weeks already done, {len(events)} events in state", flush=True)

    def flush():                                             # persist scans + event journal after each week
        scans_path.write_text(json.dumps(scans, indent=1, default=str))
        journal_path.write_text(json.dumps(
            {"events": {k: {**v, "vehicles": sorted(v["vehicles"])} for k, v in events.items()},
             "retired": retired, "nid": nid}, indent=1, default=str))

    for i, anch in enumerate(anchors):
        wk = anch.date().isoformat()
        if wk in scans:                                      # already scanned (resume) -> skip
            continue
        raw = sorted(firehose._window(pool, anch, reb),
                     key=lambda x: x.get("published_date", ""), reverse=True)
        wkpool = raw[:news_cap] if news_cap else raw
        if news_cap and len(raw) > news_cap:
            print(f"    !! news-cap dropped {len(raw) - news_cap} of {len(raw)} arts (oldest-in-window) at {wk}",
                  flush=True)
        picks, nid = agent.process_week(event_cli, anch, wkpool, events, retired, nid, i,
                                        curator_memory_weeks=memw, scout_client=scout_cli,
                                        aging_floor=aging_floor, aging_patience=aging_patience)
        live = [p for p in picks if p["thesis_live"]]
        print(f"  {wk} ({i + 1}/{len(anchors)}): {len(wkpool):3} arts -> "
              f"{[(p['ticker'], p['conviction']) for p in live] or 'none'}", flush=True)
        scans[wk] = picks
        flush()                                              # incremental + resumable (event state persisted)

    named = {p["ticker"] for ps in scans.values() for p in ps}
    print(f"\nDONE. wrote {scans_path} ({len(scans)} weeks, {len(events)} events).", flush=True)

    if a.full:                                   # slice the one full run into per-gem era scan files
        print("SLICING into per-gem era files ...", flush=True)
        slice_full(scans)
        if not a.no_render:
            import build_dashboard as bd
            for g in GEM_ERA:
                try:
                    bd.build_gem(g)
                    print(f"  dashboard -> docs/{g.lower()}/", flush=True)
                except SystemExit as e:          # a gem with no priced weeks -> skip, keep going
                    print(f"  {g}: skipped ({e})", flush=True)
    else:
        print(f"  {gem} named by curator: {gem in named}", flush=True)
        if not a.no_render:
            import build_dashboard as bd
            bd.build_gem(gem)
            print(f"  dashboard -> docs/{gem.lower()}/index.html", flush=True)


if __name__ == "__main__":
    main()
