#!/usr/bin/env python3
"""build_cbt_dashboard.py — the Curator Backtest (CBT) dashboard: docs/cbt.html

THE OTHER HALF OF THE FBT. Where the FBT judges the news gathering and stops before any LLM, this
page starts there: given that corpus, what did the curator DO with it? It deliberately does NOT lead
with an equity curve. The measure adopted 2026-08-07 is BREADTH and DIVERSITY -- does the curator
surface enough distinct, well-separated ticker-events for the optimizer to fund a spread of them --
because a backtest steered by returns on known history is how you overfit.

Reads a run dir from scripts/backtest_gdelt.py:
  curator_metrics.json  per-week funnel (articles -> events -> vehicles -> catalysts)
  decisions.jsonl       the scout's PROPOSED vs ADMITTED candidates (--decisions)
  journal.json          every event: catalyst, vehicles, status, weekly entries
  firehose_scans.csv    the picks themselves, with evidence urls
plus the corpus it read (data/backtest_1yr/pool.json) to join picks back to their source, lede
provenance and beat.

Borrowed from PWR's CBT (docs/backtest_gkg_3yr_kimi.html): the ATTRIBUTION idea -- gains and adds
traced back to news source, lede source, search keyword and author. GHR carries all four on every
article, so the join is free. Borrowed from GHR's own pre-GKG curator pages: the event Gantt
(proposed vs funded) and the cost panel.

Render-only: no LLM, no network.

    python scripts/build_cbt_dashboard.py --run data/cbt_1yr --corpus data/backtest_1yr
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from util import load_dotenv  # noqa: E402
load_dotenv()          # the picker needs ANTHROPIC_API_KEY; a render-only build otherwise has no env
import dash_nav  # noqa: E402
import score as _score  # noqa: E402
from build_fbt_dashboard import (CONFIG_URL, DARK, LIGHT, PLOTLY_CDN, PROFILE_URL,  # noqa: E402
                                 STATUS, _LINK, esc, panel, table_html, tile)


def load(run: Path, corpus: Path):
    m = json.loads((run / "curator_metrics.json").read_text())
    j = json.loads((run / "journal.json").read_text())
    dec = []
    df = run / "decisions.jsonl"
    if df.exists():
        dec = [json.loads(l) for l in df.read_text().splitlines() if l.strip()]
    import csv
    picks = []
    sf = run / "firehose_scans.csv"
    if sf.exists():
        picks = [r for r in csv.DictReader(sf.open()) if (r.get("ticker") or "").strip()]
    cd = json.loads((corpus / "pool.json").read_text())
    arts = cd.get("articles", cd) if isinstance(cd, dict) else cd
    return m, j, dec, picks, {a.get("url", ""): a for a in arts}, arts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="data/cbt_1yr")
    ap.add_argument("--corpus", default="data/backtest_1yr")
    ap.add_argument("--out", default="docs/cbt.html")
    a = ap.parse_args(argv)
    run, corpus = ROOT / a.run, ROOT / a.corpus
    M, J, DEC, PICKS, BYURL, ARTS = load(run, corpus)

    # ARTICLES PER BEAT over the whole corpus -- the denominator for beat efficiency (panel 6) and
    # the reason panel 5 can now list beats that produced NOTHING. `queries` is a stringified list.
    _beat_arts: dict = collections.Counter()
    for _a in ARTS:
        _q = _a.get("queries")
        if isinstance(_q, str):
            try:
                _q = ast.literal_eval(_q)
            except Exception:  # noqa: BLE001
                _q = []
        for _b in (_q or []):
            _beat_arts[_b] += 1
    _beat_arts = dict(_beat_arts)

    # ...and the same counts AFTER the discovery gate. This is the denominator that matters: the scout
    # is 91% of the LLM bill and reads ONLY gate-passed articles, so a beat's real cost is what it puts
    # in front of the scout, not what it pulled from GKG. The two diverge sharply -- the momentum beat
    # passes the gate at 31% against 3-8% for everything else, because its atoms (`stock surges`,
    # `shares soar`) ARE the gate's vocabulary. On the corpus denominator that beat is 17% of articles;
    # on this one it is 35% of everything the scout reads.
    _beat_gated: dict = collections.Counter()
    try:
        import agent as _ag
        _gset = {id(_x) for _x in _ag.superlative_pool(ARTS)}
        for _a in ARTS:
            if id(_a) not in _gset:
                continue
            _q = _a.get("queries")
            if isinstance(_q, str):
                try:
                    _q = ast.literal_eval(_q)
                except Exception:  # noqa: BLE001
                    _q = []
            for _b in (_q or []):
                _beat_gated[_b] += 1
    except Exception as _e:  # noqa: BLE001 - the gate is optional; fall back to corpus counts
        print(f"  gated beat counts unavailable ({type(_e).__name__}: {_e})", file=sys.stderr)
    _beat_gated = dict(_beat_gated)

    weeks = [r["week"] for r in M]
    ev = J.get("events", {})
    live_now = [e for e in ev.values() if e.get("status") == "live"]
    all_veh = {v for e in ev.values() for v in e.get("vehicles", [])}
    scout = [d for d in DEC if d.get("kind") == "scout"]
    proposed = sum(len(d.get("proposed", [])) for d in scout)
    admitted = sum(len(d.get("admitted", [])) for d in scout)
    capbound = sum(1 for d in scout if len(d.get("proposed", [])) > d.get("max_new_events", 99))

    # ---- attribution: join each pick's evidence urls back to the corpus ---------------------------
    src_c, lede_c, beat_c = collections.Counter(), collections.Counter(), collections.Counter()
    n_ev_urls = n_matched = 0
    for p in PICKS:
        for u in (p.get("evidence_urls") or "").split(";"):
            u = u.strip()
            if not u:
                continue
            n_ev_urls += 1
            art = BYURL.get(u)
            if not art:
                continue
            n_matched += 1
            src_c[art.get("source", "?")] += 1
            lede_c["archived" if art.get("lede") else
                   "live page" if art.get("lede_live") else "headline only"] += 1
            for b in (art.get("queries") or []):
                beat_c[b] += 1

    # ---- the deferred FBT chart, built here because it needs curator output -----------------------
    # coverage per ticker vs whether the curator ever picked it. A bare corpus histogram says which
    # tickers the press mentions; this says which of them the curator ACTED on -- and, more usefully,
    # which heavily-covered names it never touched.
    import re as _re
    EXCH = _re.compile(r"\((?:NYSE|NASDAQ|NYSEARCA|NYSEAMERICAN|AMEX|OTCMKTS)\s*:\s*([A-Z.]{1,6})\)", _re.I)
    BARE = _re.compile(r"\(([A-Z]{2,5})\)")
    CRYPTO = {"BTC", "ETH", "XRP", "SOL", "DOGE", "ADA", "BNB", "USDT", "USDC", "LTC", "TRX",
              "SHIB", "AVAX", "DOT", "LINK", "MATIC", "PEPE", "BCH", "XLM", "ETC"}
    cov = collections.Counter()
    for x in ARTS:
        hay = f"{x.get('title','')} {x.get('snippet','') or ''}"
        syms = {s.upper() for s in EXCH.findall(hay)}
        syms |= {s for s in BARE.findall(hay) if s not in CRYPTO}
        cov.update(syms)
    picked = {p["ticker"] for p in PICKS}
    top_cov = cov.most_common(40)

    from optimizer import load_financial_model as _lfm
    from util import resolve_cadence as _resolve_cadence
    _lfm0 = _lfm(str(ROOT / "investor_profile.backtest.md"))

    # ---- portfolio math: only for SKILL measures, never as the headline ---------------------------
    # This page deliberately does not lead with an equity curve (2026-08-07 redirect). But three of
    # the pre-GKG dashboard's panels are about curator SKILL rather than performance, and they need
    # the book to exist: what share of theses made money standalone (precision), which events and
    # tickers carried the result (attribution), and the week-by-week reasoning behind each event
    # (the storyboard). Precision in particular is a HIT RATE, not a return -- breadth without
    # precision is noise, precision without breadth is luck, and only the pair means anything.
    import pandas as _pd
    _wcap = 0
    _held_per_week, _cull_bind, _cull_med = [], 0, 0
    prec, per_agent, book = [], [], {}
    try:
        import firehose as _fh
        _s = _pd.read_csv(run / "firehose_scans.csv")
        _s = _s[_s["ticker"].notna() & (_s["ticker"].astype(str).str.strip() != "")]
        # THE EXIT SIGNAL. firehose._stateful_watch hard-exits on catalyst_resolved and exits after
        # EXIT_PATIENCE explicit thesis_live=False reads -- but only if it is SHOWN those reads. Runs
        # before 2026-08-09 wrote only the live picks to firehose_scans.csv, so the reconstruction had
        # no exit path except the 4-week MAX_STALE silence timeout and every position ran well past
        # the agent's own call to close it. Newer runs carry the flags in the CSV; for older ones we
        # recover them from journal.json, which always had them.
        _hasflag = "catalyst_resolved" in _s.columns
        _scans = collections.defaultdict(list)
        for _wk, _g in _s.groupby("week"):
            _ts = _pd.Timestamp(str(_wk) + " 16:30", tz="America/New_York")
            _scans[_ts] += [{"ticker": r.ticker, "thesis": ("" if _pd.isna(r.thesis) else str(r.thesis)),
                             "thesis_live": (bool(r.thesis_live) if _hasflag else True),
                             "catalyst_resolved": (bool(r.catalyst_resolved) if _hasflag else False),
                             "evidence_urls": []} for r in _g.itertuples()]
        if not _hasflag:
            for _e in ev.values():
                for _x in (_e.get("entries") or []):
                    if not (_x.get("catalyst_resolved") or not _x.get("thesis_live", True)):
                        continue
                    _ts = _pd.Timestamp(str(_x["date"]) + " 16:30", tz="America/New_York")
                    if _ts not in _scans:          # only mark weeks the backtest actually steps on
                        continue
                    for _v in _e.get("vehicles", []):
                        _scans[_ts].append({"ticker": _v, "thesis": _e.get("catalyst", ""),
                                            "thesis_live": False,
                                            "catalyst_resolved": bool(_x.get("catalyst_resolved")),
                                            "evidence_urls": []})
        _scans = dict(sorted(_scans.items()))
        # ONE capital figure for every curve on the value panel -- the curated book, the
        # buy-and-hold of starter_watchlist, and SPY all start at initial_investment_usd.
        _wcap = _fh.watchlist_cap(_lfm0)
        # resolve_cadence, NOT the raw key: rebalance_days is the retired numeric knob and
        # optimizer's defaults still inject 7 for it, so reading it directly returned 7 for a
        # run whose scans are 30 days apart -- the final watchlist span was drawn 4x too short.
        _cad0 = _resolve_cadence(_lfm0)
        _cap = float(_lfm0.get('initial_investment_usd', 50_000.0))
        # PICKER: without it the max_watchlist cull is keep-first-N over sorted(holding) -- i.e.
        # alphabetical. That was harmless while the prune kept live events under the cap, but live
        # events now run ~40 against 8 slots, so the cull fires every scan and was choosing by first
        # letter. src/picker.py ranks on the catalyst ARC (never conviction, never predicted return).
        _pick = None
        if _lfm0.get("picker_model"):
            try:
                import picker as _pk
                _pick, _ = _pk.make_picker(_lfm0)
            except Exception as _e:  # noqa: BLE001
                print(f"  picker unavailable ({type(_e).__name__}: {_e}); cull falls back to keep-first-N",
                      file=sys.stderr)
        _bt = _fh.backtest(_scans, _lfm0, capital=_cap, daily=True, picker=_pick)
        # Keep only PRICED theses. A ticker with no price history scores ret=None, and comparing
        # that to 0 raised TypeError once max_watchlist widened the book enough to admit one
        # (2026-08-12). Precision over unpriced theses is meaningless, so they are excluded rather
        # than silently counted as losses -- the "no priced theses" fallback already assumed this.
        prec = [x for x in _bt.get("agent_precision", []) if isinstance(x.get("ret"), (int, float))]
        # how hard the max_watchlist cull actually bites: live names vs what may hold capital
        _w = _fh._stateful_watch(_scans, seed=[x.upper() for x in (_lfm0.get("starter_watchlist") or [])])
        _live_n = [len(v) for v in _w.values()]
        _held_per_week = [min(n, _wcap) if _wcap else n for n in _live_n]
        _cull_bind = sum(1 for n in _live_n for _ in [0] if _wcap and n > _wcap)
        _cull_med = sorted(_live_n)[len(_live_n) // 2] if _live_n else 0
        book = {"final": _bt.get("final"), "spy": _bt.get("spy_final"), "weeks": _bt.get("weeks")}
        _d = _bt.get("daily") or {}
        # PWR's CBT plots 2-7, as GHR equivalents. PWR groups by WAVE BUCKET (its thesis unit);
        # GHR's unit is the EVENT, so the two "by wave" panels become "by event" -- same question,
        # different taxonomy. Kept OUT of the headline deliberately: these are attribution, and the
        # 2026-08-07 redirect says breadth/diversity leads, not the equity curve.
        _gain = _d.get("gain") or {}
        _gs = _d.get("gain_series") or {}
        _alloc = _d.get("alloc") or {}
        # ATTRIBUTE P&L TO THE EVENT THAT WAS ACTUALLY HOLDING, ON THE DAY IT HELD.
        # The old rule was `{v: eid for eid, e in ev.items() for v in e.get("vehicles", [])}` -- a plain
        # dict comprehension, so LAST WRITE WINS and a ticker claimed by several events handed its
        # ENTIRE lifetime P&L to whichever event happened to iterate last. That is not a rounding
        # error: 63 of 145 tickers are claimed by more than one event, carrying $311,951 of $411,591
        # gross gain, so ~76% of this panel was decided by dict ordering. Concretely, ev78 was charged
        # AMD's whole -$15,508 -- a ticker SIX events claim and ev78 never funded -- while GEV's
        # +$3,971, which ev78 did fund, went to ev79. The panel read -$17,473 for an event that made
        # about +$2,000.
        # Now: walk each ticker's daily gain increment and split it among the events that (a) list the
        # ticker and (b) were LIVE that day. Equal split when several qualify, because nothing in the
        # book says which of two live events "owns" a shared position -- and an equal split is at
        # least stable and sums to the true total, which the old rule did not.
        _elife = {}
        for _k, _e in ev.items():
            _en = _e.get("entries") or []
            _elife[_k] = (str(_en[0].get("date"))[:10] if _en and _en[0].get("date") else None,
                          None if _e.get("status") == "live" or not _en
                          else str(_en[-1].get("date"))[:10])
        _claim = collections.defaultdict(list)
        for _k, _e in ev.items():
            for _v in _e.get("vehicles", []):
                _claim[_v].append(_k)
        _dates_l = _d.get("dates") or []
        _evgain = collections.Counter()
        for _tk, _g in _gain.items():
            _ser = _gs.get(_tk)
            _cands = _claim.get(_tk, [])
            if not _ser or not _cands or not _dates_l:
                _evgain["(unassigned)"] += float(_g or 0)      # no series/claim -> cannot place it
                continue
            _prev = 0.0
            for _i, _day in enumerate(_dates_l):
                _cum = float(_ser[_i] or 0) if _i < len(_ser) else _prev
                _inc, _prev = _cum - _prev, _cum
                if not _inc:
                    continue
                # A start of None means the event has NO journal entries -- it was culled at birth and
                # never ran an agent, so it never tracked anything and cannot have owned a position.
                # Treating None as "live forever" (the first cut of this fix) let those events collect
                # a share of every ticker they listed, across the whole backtest: ev70 and ev79 were
                # silently splitting MU's and GEV's P&L with the events that actually held them.
                _live = [_k for _k in _cands
                         if _elife[_k][0] is not None and _elife[_k][0] <= _day
                         and (_elife[_k][1] is None or _day <= _elife[_k][1])]
                if not _live:
                    _evgain["(unassigned)"] += _inc
                else:
                    for _k in _live:
                        _evgain[_k] += _inc / len(_live)
        # BUY-AND-HOLD baseline, PWR's blue curve: the profile's `starter_watchlist`, equal-DOLLAR at
        # inception and never touched again. The honest control for "did curating add anything, or
        # would holding a boring opening basket have done as well?" firehose.backtest computes it off
        # the same price panel and the same initial_investment_usd, so both curves start at the same $.
        # WATCHLIST COMPOSITION (PWR's CBT plot 2, as a GHR equivalent). Two spans per ticker: when it
        # was WATCHLISTED (the curator held the thesis) and when it was actually FUNDED (the optimizer
        # gave it weight). The gap between them is the interesting part -- a name the curator kept and
        # the math never backed. Colour is by EVENT, GHR's unit of thesis, where PWR colours by wave.
        # Colour key: each ticker's DOMINANT BEAT, from the beats that surfaced its evidence articles.
        # Beats are the vocabulary the firehose actually searches on, so this says which part of the
        # news a name came from. 14 beats appear, past the ~8 a categorical palette keeps separable,
        # so the tail folds into "other" rather than cycling hues (which would imply false identity).
        _tb = collections.defaultdict(collections.Counter)
        for _p in PICKS:
            for _u in (_p.get("evidence_urls") or "").split(";"):
                _a = BYURL.get(_u.strip())
                if _a:
                    for _bq in (_a.get("queries") or []):
                        _tb[_p["ticker"]][_bq] += 1
        _dom = {tk: c.most_common(1)[0][0] for tk, c in _tb.items() if c}
        # KEEP EVERY BEAT THAT MOVED MONEY. A flat top-8-by-ticker-count cap silently misattributed the
        # book: measured 2026-08-14 on v15, RKLB's $36,886 -- the single largest gain in the run, and a
        # pure rocket-builder -- landed in "other" because "space stocks" funded few tickers, while the
        # per-beat chart still listed "space stocks" at $0 (that panel enumerates ALL corpus beats). The
        # reading was exactly backwards: the beat looked like a total failure while it was the top earner.
        # So a beat that is dominant for any ticker carrying P&L keeps its own label; only beats that
        # funded nothing but noise fold into "other" (still capped, so the hue order stays finite).
        _moved = {b for b in _dom.values()}
        _top = [b for b, _ in collections.Counter(_dom.values()).most_common(24) if b in _moved]
        def _beat_of(tk):
            b = _dom.get(tk)
            return b if b in _top else ("other" if b else "no beat")
        _wl = _bt.get("watch") or {}
        _wspans, _fspans = [], []
        _anch = sorted(_wl)
        _t2e = {v: eid for eid, e in ev.items() for v in e.get("vehicles", [])}
        for _i, _a in enumerate(_anch):
            _end = _anch[_i + 1] if _i + 1 < len(_anch) else _a + _pd.Timedelta(days=_cad0)
            for _tk in _wl[_a]:
                _wspans.append({"t": _tk, "s": str(_a.date()), "e": str(_end.date()),
                                "ev": _t2e.get(_tk, ""), "b": _beat_of(_tk)})
        _dts = _d.get("dates", [])
        for _tk, _ser in (_d.get("alloc") or {}).items():   # contiguous funded runs, from daily weights
            _run = None
            for _i, _w in enumerate(_ser):
                if _w > 0.01 and _run is None:
                    _run = _i
                elif _w <= 0.01 and _run is not None:
                    _fspans.append({"t": _tk, "s": _dts[_run], "e": _dts[_i], "ev": _t2e.get(_tk, ""), "b": _beat_of(_tk)})
                    _run = None
            if _run is not None:
                _fspans.append({"t": _tk, "s": _dts[_run], "e": _dts[-1], "ev": _t2e.get(_tk, ""), "b": _beat_of(_tk)})
        book.update({
            "wcomp": {"watch": _wspans, "funded": _fspans, "beats": _top + ["other", "no beat"]},
            "tickerbeat": {tk: _beat_of(tk) for tk in
                           ({p["ticker"] for p in PICKS} | set(_gain) | set(_dom))},
            # ARTICLES PER BEAT across the whole corpus -- the denominator for beat efficiency, and
            # the reason gainbeat can now list beats that produced NOTHING. Counting only funded beats
            # (the old behaviour) showed 9 of 46 and hid the expensive failures entirely.
            "beatarts": _beat_arts,
            "beatgated": _beat_gated,
            "gainbeat": {b: round(v, 2) for b, v in sorted(
                {bb: sum(g for tk, g in _gain.items() if _beat_of(tk) == bb)
                 for bb in ({_beat_of(tk) for tk in _gain} | set(_beat_arts))}.items(),
                key=lambda kv: kv[1])},
            "anchors": _fh.anchor_tickers(_lfm0),
            "rebal": [str(x.date()) for x in sorted(_scans)],
            "bh": [None if x is None else float(x) for x in (_d.get("bh") or [])],
            "bh_tickers": _d.get("bh_tickers") or [],
            "dates": _d.get("dates", []), "value": [float(x) for x in _d.get("value", [])],
            "spyser": [float(x) for x in _d.get("spy", [])],
            "gain": {k: float(v or 0) for k, v in sorted(_gain.items(), key=lambda kv: kv[1])},
            "evgain": dict(sorted(_evgain.items(), key=lambda kv: kv[1])),
            "alloc": {k: [float(x) for x in v] for k, v in _alloc.items()},
            "evseries": {eid: [sum(float(_gs.get(v, [0])[i] if i < len(_gs.get(v, [])) else 0)
                                   for v in e.get("vehicles", []) if v in _gs)
                               for i in range(len(_d.get("dates", [])))]
                         for eid, e in ev.items()
                         if any(v in _gs for v in e.get("vehicles", []))},
        })
    except Exception as _e:  # noqa: BLE001 -- prices are a live fetch; a failure must not kill the page
        print(f"  portfolio math skipped ({type(_e).__name__}: {_e})", file=sys.stderr)

    # EVENT-LEVEL beat + funded spans for the timeline. Colouring by live/exited made nearly every
    # bar grey (most events exit), which carried no information. Beat + funded is the useful pair:
    # the SPAN says proposed -> terminated, the SOLID overlay says when the optimizer backed it.
    _ev_beat, _ev_fund = {}, {}
    _al = (book.get("alloc") or {})
    _dts = book.get("dates") or []
    _tbmap = book.get("tickerbeat") or {}
    for _k, _e in ev.items():
        _vs = list(_e.get("vehicles") or [])
        _bs = collections.Counter(_tbmap.get(v) for v in _vs if _tbmap.get(v))
        _ev_beat[_k] = _bs.most_common(1)[0][0] if _bs else "no beat"
        # AN EVENT CANNOT BE FUNDED BEFORE IT EXISTED. `_held` marks any day ANY of the event's
        # vehicles was funded -- but the basket GROWS, so an event that later absorbs a widely-held
        # ticker (AMD, NVDA) inherits every day that ticker was ever funded, including years under a
        # DIFFERENT event. That drew ev69's funded bar starting 2024 when its agent first ran
        # 2026-05-27. Same root cause as the start-date bug fixed alongside this, and the one the
        # user actually saw: the pale proposed span and the solid funded span were BOTH wrong, and
        # fixing only the span left the chart looking unchanged.
        # So clip funding to the event's own lifetime. Two live events sharing a vehicle still both
        # show funded, which is honest -- both do claim it -- but neither reaches outside its life.
        # Derived inline, NOT from _opened: that dict is built ~150 lines further down, so reading it
        # here would NameError. Same rule -- the event is born when its agent first ran.
        _lo_i, _hi_i = 0, len(_dts) - 1
        _ents = _e.get("entries") or []
        _ostart = str(_ents[0].get("date"))[:10] if _ents and _ents[0].get("date") else None
        if _ostart:
            _lo_i = next((i for i, d in enumerate(_dts) if d >= _ostart), len(_dts))
        _oend = _ents[-1].get("date") if _ents else None
        if _oend and _e.get("status") != "live":
            _hi_i = next((i for i in range(len(_dts) - 1, -1, -1) if _dts[i] <= str(_oend)[:10]), _hi_i)
        _held = [(_lo_i <= i <= _hi_i)
                 and any((_al.get(v) or [0] * len(_dts))[i] > 0.01 for v in _vs)
                 for i in range(len(_dts))]
        _runs, _st = [], None
        for _i, _h in enumerate(_held):
            if _h and _st is None:
                _st = _i
            elif not _h and _st is not None:
                _runs.append([_dts[_st], _dts[_i]]); _st = None
        if _st is not None:
            _runs.append([_dts[_st], _dts[-1]])
        _ev_fund[_k] = _runs

    # PERFECT-FORESIGHT CEILING. For each watchlisted name, the best single buy->sell available
    # INSIDE the span the curator actually held it -- i.e. what was on the table given the picks it
    # made, with no credit for names it never found. Paired with the plain buy-and-hold return over
    # the same span, it separates two very different failures: a LOW ceiling means the pick itself
    # was a dud, while a high ceiling with a poor buy-and-hold means the pick was fine and the entry
    # or exit timing was not. It is an UPPER BOUND computed with hindsight and is not attainable.
    ceil_rows = []
    try:
        import numpy as _np
        _spans = collections.defaultdict(list)
        for _s in ((book.get("wcomp") or {}).get("watch") or []):
            _spans[_s["t"]].append((_s["s"], _s["e"]))
        _anc = set(book.get("anchors") or [])
        _cand = [tk for tk in sorted(_spans) if tk not in _anc]
        if _cand and book.get("dates"):
            _cp = _score.fetch_panel(_cand, book["dates"][0], book["dates"][-1], use_cache=False)
            if _cp.index.tz is not None:
                _cp.index = _cp.index.tz_localize(None)
            for _tk in _cand:
                if _tk not in _cp.columns:
                    continue
                _best, _bh, _days = 0.0, None, 0
                for _s0, _e0 in _spans[_tk]:
                    _ser = _cp[_tk].loc[_pd.Timestamp(_s0):_pd.Timestamp(_e0)].dropna()
                    if len(_ser) < 2:
                        continue
                    _days += len(_ser)
                    _lo = _np.minimum.accumulate(_ser.values)
                    _best = max(_best, float(_np.max(_ser.values / _lo - 1)))
                    if _bh is None:
                        _bh = float(_ser.values[-1] / _ser.values[0] - 1)
                if _days:
                    ceil_rows.append({"t": _tk, "ceil": round(100 * _best, 1),
                                      "bh": round(100 * (_bh or 0.0), 1), "d": _days,
                                      "b": _beat_of(_tk)})
        ceil_rows.sort(key=lambda r: -r["ceil"])
    except Exception as _e:  # noqa: BLE001
        print(f"  ceiling panel skipped ({type(_e).__name__}: {_e})", file=sys.stderr)

    # PRICE HISTORY for the click-through on plot 3 (PWR's CFT does the same). One SHARED date index
    # plus one array per ticker -- emitting {date, price} pairs per ticker would triple the page size.
    # The watchlisted and funded spans ride along so the modal can shade "the relevant part" rather
    # than dumping an undifferentiated 3-year line.
    px_hist: dict = {}
    try:
        _pt = sorted(set(book.get("gain") or {}))
        if _pt and book.get("dates"):
            _pp = _score.fetch_panel(_pt, book["dates"][0], book["dates"][-1], use_cache=False)
            if _pp.index.tz is not None:
                _pp.index = _pp.index.tz_localize(None)
            _pp = _pp.ffill()
            px_hist = {"d": [d.strftime("%Y-%m-%d") for d in _pp.index],
                       "p": {tk: [None if _pd.isna(v) else round(float(v), 4)
                                  for v in _pp[tk]] for tk in _pt if tk in _pp.columns}}
    except Exception as _e:  # noqa: BLE001
        print(f"  price-history payload skipped ({type(_e).__name__}: {_e})", file=sys.stderr)

    # ---- cost -------------------------------------------------------------------------------------
    cost = 0.0
    cf = ROOT / "data" / "llm_costs.csv"
    if cf.exists():
        import csv as _csv
        rows = list(_csv.DictReader(cf.open()))
        # the run's own window: everything logged while it was executing
        cost = sum(float(r.get("cost_usd") or 0) for r in rows[-max(len(scout) * 6, 200):])

    def st(v, good, warn):
        return "good" if v >= good else ("warning" if v >= warn else "critical")

    med_live = statistics.median([r["events_live"] for r in M]) if M else 0
    med_cat = statistics.median([r["distinct_catalysts"] for r in M]) if M else 0
    tiles = "".join(tile(v) for v in [
        dict(label="Weeks curated", value=f"{len(M)}", sub=f"{weeks[0]} → {weeks[-1]}" if weeks else "",
             status="good", why="Rebalances the curator ran across the backtest window."),
        dict(label="Events opened", value=f"{J.get('nid', 0)}", sub=f"{len(live_now)} still live at the end",
             status=st(J.get("nid", 0), 30, 12),
             why="Distinct catalysts the curator opened an event on over the whole run."),
        dict(label="Events live / week", value=f"{med_live:.0f}",
             sub=f"cap max_watchlist = {_wcap or 'uncapped'}",
             status=st(med_live, 4, 2),
             why="Typical number of events holding capital at once — the breadth the optimizer can spread across."),
        dict(label="Distinct catalysts", value=f"{med_cat:.0f}", sub="median per week",
             status=st(med_cat, 4, 2),
             why="Separate stories behind those events. Several events on one theme is not diversity."),
        dict(label="Vehicles named", value=f"{len(all_veh)}", sub=f"across {J.get('nid',0)} events",
             status=st(len(all_veh), 40, 15),
             why="Distinct tickers the curator named. Peer baskets mean one event can carry several."),
        dict(label="Scout inflow", value=f"{proposed}",
             sub=f"{admitted} admitted · {capbound}/{len(scout)} weeks hit the cap",
             status="critical" if capbound > len(scout) * 0.5 else "good",
             why="Candidates the scout proposed vs admitted. If the cap rarely binds, breadth is set "
                 "by the curator, not the knob."),
        dict(label="Evidence matched", value=f"{100*n_matched/max(n_ev_urls,1):.0f}%",
             sub=f"{n_matched:,} of {n_ev_urls:,} cited urls",
             status=st(100*n_matched/max(n_ev_urls, 1), 90, 70),
             why="Share of cited articles found back in the corpus. Low means picks rest on evidence "
                 "the backtest cannot audit."),
        dict(label="Agent precision",
             value=(f"{100*sum(1 for x in prec if (x['ret'] or 0) > 0)/len(prec):.0f}%" if prec else "—"),
             sub=(f"{sum(1 for x in prec if (x['ret'] or 0) > 0)} of {len(prec)} theses profitable"
                  if prec else "no priced theses"),
             status=("good" if prec and sum(1 for x in prec if (x["ret"] or 0) > 0)/len(prec) >= 0.5
                     else "warning" if prec and sum(1 for x in prec if (x["ret"] or 0) > 0)/len(prec) >= 0.35
                     else "critical"),
             why="Share of the curator's theses that made money standalone over their live span. A "
                 "HIT RATE, not a return — breadth without precision is noise."),
        dict(label="LLM cost", value=f"${cost:.2f}", sub=f"${cost/max(len(M),1):.3f} per week",
             status="good", why="Curator spend for the run: scout, matcher and event agents."),
    ])

    # OPEN DATE for every event, including those culled on the scan they opened. `entries[0].date`
    # exists only once an event-agent has run, and with max_events culling most events the SAME scan
    # they open, 103 of 175 had no entries -- so they carried start="" and were silently DROPPED from
    # the timeline. decisions.jsonl records what was ADMITTED per scan, which is when an event comes
    # into being, so it dates them all.
    _opened = {}
    try:
        import ast as _ast
        _first = {}
        for _l in (run / "decisions.jsonl").open():
            _r = json.loads(_l)
            _a = _r["admitted"] if isinstance(_r["admitted"], list) else _ast.literal_eval(_r["admitted"])
            for _t in _a:
                _first.setdefault(_t, _r["context"])
        for _k, _v in ev.items():
            # AN EVENT IS BORN WHEN ITS AGENT FIRST RAN, not when its earliest-ever ticker was first
            # seen. The old rule took min(first-admitted) across the event's vehicles -- but a basket
            # GROWS: the agent adds peer tickers over time, and if it absorbs a ticker that some
            # EARLIER event introduced, the event's start date jumps backwards to that old date.
            # Measured 2026-08-14 on v15: 18 of 61 events were back-dated, median 390 days, worst 870
            # (ev78 drawn at 2024-02-07 when its agent first ran 2026-06-26, dated by AMD/NVDA which it
            # inherited). That made the timeline unreadable -- events appeared to start years before
            # their catalyst existed, and the ordering no longer tracked when things actually happened.
            # So: the event's OWN first journal entry wins. min(first-admitted) survives only as the
            # fallback for events culled at birth (19 of 80 here), which never ran an agent and so have
            # no entry to date them -- and whose basket never grew, so the fallback is right for them.
            _ent = _v.get("entries") or []
            _own = (_ent[0].get("date") if _ent else None)
            if _own:
                _opened[_k] = str(_own)[:10]
                continue
            _d = [_first[t] for t in _v.get("vehicles", []) if t in _first]
            if _d:
                _opened[_k] = min(_d)
    except Exception:  # noqa: BLE001 - decisions.jsonl is opt-in via --decisions
        pass

    # per-scan gate counts: prefer what the run RECORDED; recompute only for pre-2026-08-14 runs
    if all("articles_gated" in r for r in M):
        _gated_total = sum(r["articles_gated"] for r in M)
    else:
        try:
            import agent as _ag2
            import pandas as _pd2
            import statistics as _st2
            # Cadence from THE RUN, never the profile. investor_profile.backtest.md still carries a
            # stale rebalance_days: 7 alongside rebalance_period: monthly, and this run's scans are
            # actually 30 days apart -- reading the profile gave a 7-day window and undercounted the
            # gate bar by 5x (1,087 vs the true 5,096). The run's own scan spacing cannot drift.
            _wk = sorted(_pd2.Timestamp(str(r["week"])[:10]) for r in M)
            _gaps = [(_wk[i + 1] - _wk[i]).days for i in range(len(_wk) - 1)]
            _cad = int(_st2.median(_gaps)) if _gaps else 30
            _gt = 0
            for _r in M:
                _hi = str(_r["week"])[:10]
                _lo = (_pd2.Timestamp(_hi) - _pd2.Timedelta(days=_cad)).date().isoformat()
                _w = [a for a in ARTS if _lo < (a.get("published_date") or "")[:10] <= _hi]
                _gt += len(_ag2.superlative_pool(_w))
            _gated_total = _gt
        except Exception as _e:  # noqa: BLE001 -- a missing gate bar beats a broken dashboard
            print(f"  gate bar skipped ({type(_e).__name__}: {_e})", file=sys.stderr)
            _gated_total = 0

    payload = {
        "funnel": {
            # THE DISCOVERY GATE BELONGS IN THIS FUNNEL. Without it the chart jumps 98,950 -> 187 in one
            # step and reads as "the scout rejected 99.8% of what it saw" -- but the scout never saw
            # 98,950. It saw ~5,100: the gate is a ~19x cut sitting between those two bars, and it was
            # the largest single reduction anywhere in the pipeline while appearing on no dashboard.
            # `articles_gated` is recorded per scan by backtest_gdelt.py from 2026-08-14; older runs
            # lack it, so it is recomputed here (free -- superlative_pool over the window, no LLM).
            "labels": ["articles read", "past the discovery gate", "candidates proposed",
                       "candidates admitted", "events opened", "vehicles named", "picks logged"],
            "values": [sum(r["articles_read"] for r in M), _gated_total, proposed, admitted,
                       J.get("nid", 0), len(all_veh), len(PICKS)]},
        "breadth": {"w": weeks, "cap": _wcap, "held": _held_per_week,
                    "events": [r["events_live"] for r in M],
                    "vehicles": [r["vehicles_live"] for r in M],
                    "catalysts": [r["distinct_catalysts"] for r in M]},
        "inflow": {"w": [d["context"] for d in scout],
                   "prop": [len(d.get("proposed", [])) for d in scout],
                   "adm": [len(d.get("admitted", [])) for d in scout],
                   "cap": [d.get("max_new_events", 0) for d in scout]},
        "ceiling": ceil_rows[:40],
        "px": px_hist,
        "cap_pct": float(_lfm0.get("concentration_cap", 0.25)),
        "gantt": [{"id": k, "cat": v.get("catalyst", "")[:70],
                   "veh": sorted(v.get("vehicles", []))[:6],
                   "start": ((v.get("entries") or [{}])[0].get("date", "") or _opened.get(k, "")),
                   "end": ((v.get("entries") or [{}])[-1].get("date", "") or _opened.get(k, "")),
                   "beat": _ev_beat.get(k, "no beat"), "fund": _ev_fund.get(k, []),
                   "status": v.get("status", "")}
                   # CULLED-AT-BIRTH events are EXCLUDED BY CHOICE (and counted in the lead):
                   # max_events retires most events on the scan they open, before any agent sees
                   # them, so they would draw 103 zero-length bars and bury the theses that ran.
                   # Stated, not silent -- silently dropping them (no entries -> no date) was this
                   # panel's original bug.
                   for k, v in ev.items() if (v.get("entries") or [])],
        "src": {"s": [s for s, _ in src_c.most_common(25)], "n": [n for _, n in src_c.most_common(25)]},
        "lede": {"k": list(lede_c), "n": list(lede_c.values())},
        "beat": {"b": [b for b, _ in beat_c.most_common(20)], "n": [n for _, n in beat_c.most_common(20)]},
        "cov": {"t": [t for t, _ in top_cov], "n": [n for _, n in top_cov],
                "picked": [t in picked for t, _ in top_cov]},
        "prec": {"t": [x["ticker"] for x in sorted(prec, key=lambda z: z["ret"])],
                 "r": [round(100 * x["ret"], 1) for x in sorted(prec, key=lambda z: z["ret"])],
                 "th": [str(x.get("thesis") or "")[:70] for x in sorted(prec, key=lambda z: z["ret"])],
                 "span": [f"{x['first']} → {x['last']}" for x in sorted(prec, key=lambda z: z["ret"])]},
        "book": book,
        "text": {"w": weeks, "clean": [r["lede_clean"] for r in M],
                 "live": [r["lede_live"] for r in M], "none": [r["lede_headline_only"] for r in M]},
    }

    # ---- parameter table: the exact knobs behind every number on this page ------------------------
    # Modelled on PWR's CBT. Not decoration: a curator result is meaningless without the config that
    # produced it, and these values are the ones a reader would otherwise have to go dig out of the
    # profile. Grouped by WHO reads them -- curator vs optimizer -- because they answer different
    # questions, and the caps are called out since they bound the breadth this page measures.
    from util import resolve_cadence as _rc
    _ws = sorted({r["week"] for r in M})
    _gaps = [( _pd.Timestamp(_ws[i+1]) - _pd.Timestamp(_ws[i]) ).days for i in range(len(_ws) - 1)]
    _cad = int(statistics.median(_gaps)) if _gaps else _rc(_lfm0)   # what the RUN did, not what the profile now says
    _cad_profile = _rc(_lfm0)
    fmp = _lfm0
    cfgp = json.loads((ROOT / "retrieval_config.json").read_text())
    arm_used = "fuller (archived lede, falling back to live page)"
    # A knob's value, named by its investor_profile key. NO commentary: the profile itself carries the
    # explanation, and repeating it here just makes the table unreadable. The ONE exception is a
    # SENTINEL -- a number whose plain reading is wrong (news_cap 0 does not mean "reads nothing", it
    # means uncapped) -- where the row states what the sentinel means INSTEAD of the bare value.
    SENTINEL = {
        "news_cap":                 {0: "0 = uncapped"},
        "event_news_cap":           {0: "0 = uncapped"},
        "max_new_events":           {0: "0 = uncapped"},
        "max_watchlist":            {0: "0 = uncapped"},
        "max_event_scans":          {0: "0 = off"},
        "drop_unfunded_weeks":      {0: "0 = off"},
        "unfunded_cooldown_weeks":  {0: "0 = never"},
        "curator_memory_weeks":     {0: "0 = off", -1: "-1 = whole history"},
        "relevance_keep":           {0: "0 = no ceiling"},
    }

    def _pv(key, note=""):
        v = fmp.get(key)
        if key in SENTINEL and isinstance(v, int) and not isinstance(v, bool) and v in SENTINEL[key]:
            return (key, SENTINEL[key][v])
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v) or "[]"
        elif isinstance(v, bool):
            v = "true" if v else "false"
        elif v is None or v == "":
            v = "(unset)"
        return (key, f"{v}")

    params = [
        ("— window (this run) —", ""),
        ("backtest window", f"{weeks[0]} → {weeks[-1]}" if weeks else "?"),
        ("curator calls", f"{len(M)} scans, every {_cad} days as run"
         + ("" if _cad == _cad_profile else f" — profile now says {_cad_profile}d; this run predates that")),
        ("corpus", f"{a.corpus} · {len(ARTS):,} articles · "
                   f"{len(cfgp['gem_beats'])}+{len(cfgp['coverage_beats'])} beats"),
        ("lede arm", arm_used),
        ("— investor_profile.backtest.md · cadence —", ""),
        _pv("rebalance_period"),
        ("— investor_profile.backtest.md · curator —", ""),
        _pv("model"),
        _pv("scout_model"),
        _pv("event_agent_model"),
        _pv("picker_model"),
        _pv("news_cap"),
        _pv("event_news_cap"),
        _pv("relevance_filter"),
        _pv("max_new_events"),
        _pv("curator_memory_weeks"),
        _pv("exit_patience_scans"),
        _pv("max_stale_scans"),
        _pv("max_event_scans"),
        ("— investor_profile.backtest.md · optimizer —", ""),
        _pv("initial_investment_usd"),
        _pv("starter_watchlist"),
        _pv("always_include"),
        _pv("max_watchlist"),
        _pv("concentration_cap"),
        _pv("risk_aversion"),
        _pv("min_trade_size"),
        _pv("lookback_period_days"),
        _pv("t_update_days"),
        _pv("risk_free_rate"),
        _pv("drop_unfunded_weeks"),
        _pv("unfunded_reentry_on_new_catalyst"),
        _pv("unfunded_cooldown_weeks"),
    ]
    prows = "".join(
        (f'<tr><td colspan="2" style="color:var(--text2);padding-top:10px;font-size:11.5px;'
         f'text-transform:uppercase;letter-spacing:.05em;border-bottom:none;">{esc(k.strip("— "))}</td></tr>'
         if not v else f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>")
        for k, v in params)
    ptable = (f'<section class="panel"><h2>Parameter settings</h2>'
              f'<p class="lead">The exact knobs behind every number on this page, read from '
              f'{_LINK(PROFILE_URL, "investor_profile.backtest.md")} and '
              f'{_LINK(CONFIG_URL, "retrieval_config.json")}. The two <b>CAP</b> rows bound the breadth '
              f'this dashboard measures, so read them alongside <i>Breadth over time</i> and <i>Watchlist composition</i>.</p>'
              f'<div class="scroll"><table class="params">{prows}</table></div></section>')

    # ---- curation log: every week that CHANGED something ------------------------------------------
    # PWR's CBT leads with this and it is the right instinct: the charts say how much, the log says
    # WHAT. GHR's analogue of adds/removes is events OPENED and EXITED, since an event -- not a
    # ticker -- is what the curator reasons about; its vehicles come along with it.
    # No-change weeks are hidden, exactly as PWR does, or the table is mostly blank rows.
    opened, exited = collections.defaultdict(list), collections.defaultdict(list)
    for eid, e in ev.items():
        ents = e.get("entries") or []
        if not ents:
            continue
        opened[ents[0].get("date", "")].append((eid, e))
        if e.get("status") != "live":
            exited[ents[-1].get("date", "")].append((eid, e))
    prop_by_wk = {d["context"]: (len(d.get("proposed", [])), len(d.get("admitted", [])))
                  for d in scout}
    log_rows = []
    for wk in weeks:
        op, ex = opened.get(wk, []), exited.get(wk, [])
        if not op and not ex:
            continue                                   # no-change week
        pr, ad = prop_by_wk.get(wk, ("", ""))
        log_rows.append([
            wk,
            " · ".join(f"{eid}: {e['catalyst'][:46]} [{', '.join(sorted(e['vehicles'])[:5])}]"
                       for eid, e in op) or "—",
            " · ".join(f"{eid} [{', '.join(sorted(e['vehicles'])[:5])}]" for eid, e in ex) or "—",
            f"{pr}\u2192{ad}" if pr != "" else "—",
        ])
    book["changed_weeks"] = [r[0] for r in log_rows]
    curation_log = table_html(["Week", "Events opened (catalyst -> vehicles)", "Events exited",
                               "Proposed\u2192admitted"], log_rows)
    log_panel = (
        f'<section class="panel"><h2>21. Curation log</h2>'
        f'<p class="lead">The {len(log_rows)} of {len(M)} weekly calls that CHANGED something — a week '
        f'where the curator opened or closed an event. No-change weeks are hidden. An <b>event</b> is '
        f'one catalyst and the basket of tickers expressing it, so opening an event is GHR\'s analogue '
        f'of an add. <b>Proposed&rarr;admitted</b> is what the scout put forward versus what survived '
        f'the inflow cap, so a week where those differ is a week the cap bound.</p>'
        # Collapsed by default. It is the longest table on the page and it is a REFERENCE, not a
          # finding -- you arrive with a particular week in mind rather than reading it through.
          f'<details class="tbl"><summary>show the {len(log_rows)}-week log</summary>'
          f'<div class="scroll">{curation_log}</div></details></section>')

    # one row per ticker needs ~16px of vertical space, or the axis has no room for the labels
    _wcomp_n = len({s["t"] for s in ((book.get("wcomp") or {}).get("watch") or [])}
                   | {s["t"] for s in ((book.get("wcomp") or {}).get("funded") or [])})
    _wcomp_h = max(720, 16 * _wcomp_n + 80)

    # ---- the value-panel verdict, COMPUTED not asserted --------------------------------------------
    # If a rebuild flips the ranking the prose flips with it, so the page cannot drift into
    # flattering itself with a sentence somebody typed when the numbers happened to look good.
    _bhv = [x for x in (book.get("bh") or []) if x is not None]
    _cv = (book.get("value") or [0])[-1]
    _sp = (book.get("spyser") or [0])[-1]
    _bhf = _bhv[-1] if _bhv else None
    bh_names = ", ".join(book.get("bh_tickers") or []) or "none"
    bh_verdict = ""
    if _bhf:
        _pairs = (("the starter basket", _bhf), ("SPY", _sp))
        _beat = [n for n, v in _pairs if _cv > v]
        _lost = [n for n, v in _pairs if _cv <= v]
        bh_verdict = (f"Over this window the curated book ended at ${_cv:,.0f}, the starter basket at "
                      f"${_bhf:,.0f}, SPY at ${_sp:,.0f} — curating "
                      + ("beat " + " and ".join(_beat) if _beat else "")
                      + (" but " if _beat and _lost else "")
                      + ("trailed " + " and ".join(_lost) if _lost else "") + ".")

    # ---- event storyboard: the journal, rendered ---------------------------------------------------
    _ret = {x["ticker"]: x["ret"] for x in prec}
    def _ev_ret(e):
        rs = [_ret[v] for v in e.get("vehicles", []) if v in _ret]
        return sum(rs) / len(rs) if rs else None
    _ordered = sorted(ev.items(), key=lambda kv: (_ev_ret(kv[1]) is None, -(_ev_ret(kv[1]) or 0)))
    _blocks = []
    for eid, e in _ordered:
        r = _ev_ret(e)
        badge = ("<span style='color:%s'>%+.0f%%</span>" % (STATUS["good"] if r > 0 else STATUS["critical"],
                                                            100 * r)) if r is not None else \
                "<span style='color:#999'>never funded</span>"
        ents = e.get("entries") or []
        li = "".join(
            f"<li style='margin:.25em 0'><code>{esc(en.get('date',''))}</code> "
            f"<b>{'live' if str(en.get('thesis_live')).lower()=='true' else 'EXIT'}</b> "
            f"{esc(str(en.get('assessment') or '')[:200])}"
            + (f"<br><span style='color:#999'>exit: {esc(str(en.get('exit_case'))[:180])}</span>"
               if str(en.get('catalyst_resolved')).lower() == 'true' and en.get('exit_case') else "")
            + "</li>" for en in ents)
        _blocks.append(
            f"<details style='margin:.5em 0;border:1px solid var(--line);border-radius:8px;padding:8px 12px'>"
            f"<summary style='cursor:pointer'><b>{esc(eid)}</b> {badge} &nbsp;{esc(e.get('catalyst','')[:80])} "
            f"<span style='color:#999'>[{esc(', '.join(sorted(e.get('vehicles', []))[:8]))}]</span> "
            f"&middot; {len(ents)} weeks</summary><ul style='font-size:12.5px;margin:.6em 0'>{li}</ul></details>")
    story_html = "".join(_blocks)

    # ~14px a row keeps every event legible; a fixed 700px gave 4px at 175 events.
    # ~18px a row: 48 beats in 460px gave 9.6px and Plotly silently SKIPPED every other label.
    _n_beats = len(book.get("gainbeat") or {})
    _n_beats_eff = sum(1 for _b, _n in (book.get("beatgated") or {}).items() if _n >= 20)
    _n_gantt = sum(1 for g in payload["gantt"] if g["start"])
    _n_lived = len(payload["gantt"])            # events with >=1 agent entry (the ones drawn)
    _n_culled = len(ev) - _n_lived              # retired on the scan they opened

    panels = ptable + "".join([
        panel(1, "Realized portfolio value",
              "Three books that all start at the same dollar: the curated one, a buy-and-hold of the "
              f"<code>starter_watchlist</code> ({bh_names}) bought equal-dollar on day 1 and never "
              "touched, and SPY. Squares mark the weekly rebalances — dark red where an event actually "
              "opened or closed, orange where the curator rebalanced but changed nothing. "
              + bh_verdict +
              " Kept OUT of the headline on purpose: a backtest steered by returns on known history is "
              "how you overfit, which is why this page leads with breadth and precision.",
              "c-value", 380),
        panel(2, "Watchlist composition over time",
              "One row per ticker. The pale bar is the span the curator kept it WATCHLISTED — it held "
              "the thesis; the solid bar is the span the optimizer actually FUNDED it. The gap between "
              "them is the honest part: a name the curator believed in and the math never backed. "
              "Colour is the ticker&rsquo;s dominant BEAT &mdash; which part of the firehose its evidence "
              "came from. Grey means no beat-attributable evidence.",
              "c-wcomp", _wcomp_h),
        panel(3, "Event timeline",
              f"The {_n_lived} events that LIVED. The pale bar spans proposed &rarr; terminated; the solid "
              "overlay is when the optimizer actually FUNDED it, coloured by beat. A bar with no solid "
              "section is a thesis the curator held and the math never backed. "
              f"<b>{_n_culled} further events are not shown</b> \u2014 <code>max_events</code> retired "
              "them on the scan they opened, before any agent assessed them, so they have no span to "
              "draw. That is deliberate: those events go on to return 12.6 points LESS over the next "
              "60 days than the ones kept.",
              "c-gantt", max(700, 14 * _n_gantt)),
        panel(4, "Cumulative $ gain per holding",
              "The 16 best and 8 worst funded names, with every other name rolled into one grey bar. "
              "A result resting on one or two names is a different thing from the same return spread "
              "across many — and the difference is not visible in the equity curve above. <b>Click any "
              "named bar</b> for that ticker&rsquo;s price history, with &#9650;/&#9660; marking the "
              "moments the optimizer funded and unfunded it.",
              "c-gainh", 560),
        panel(5, "Cumulative $ gain per beat",
              "The same dollars as the panel above, rolled up to the BEAT that surfaced each ticker\u2019s "
              "evidence \u2014 i.e. which part of the firehose paid. A beat that costs money is a "
              "retrieval-vocabulary problem, not a curator one.",
              "c-gainb", max(460, 18 * _n_beats)),
        panel(6, "Gain per article read, by beat",
              "The same dollars divided by how many of that beat's articles actually REACHED THE SCOUT "
              "&mdash; i.e. survived the discovery gate. That is the cost that binds: the scout is 91% of "
              "the LLM bill and reads only gate-passed articles. The corpus count and the pass rate are "
              "in the hover. They diverge sharply &mdash; the momentum beat passes at 31% against 3-8% "
              "elsewhere, because its atoms ARE the gate's vocabulary, so it is 17% of the corpus but 35% "
              "of everything the scout reads. "
              "Beats that retrieved articles but never produced a funded position sit at zero: they are "
              "pure cost. Read it against panel 5 &mdash; a tall bar there with a short bar here is a "
              "beat carried by volume rather than by quality.",
              "c-beateff", max(420, 18 * _n_beats_eff)),
        panel(7, "Cumulative $ gain per event",
              "The same gains grouped by the EVENT that motivated them. PWR groups this by wave "
              "bucket; GHR's unit of thesis is the event, so this is its analogue. It answers whether "
              "the curator's <i>ideas</i> paid, independently of which vehicle expressed them.",
              "c-gaine", 480),
        panel(8, "Portfolio value by event over time",
              "How the book was distributed across events as the year ran. Wide bands that persist "
              "mean concentrated conviction; a churn of thin bands means the optimizer kept rotating.",
              "c-evtime", 420),
        panel(9, "Allocation over time",
                "Dollars held per ticker, stacked — the top edge is the portfolio value. The "
                "<code>always_include</code> anchors (SPY, BIL) sit outside the watchlist cap and are "
                "where idle capital parks — which is why there is <b>no cash band</b>: the weights sum "
                "to 1 within 1e-4 on every one of 734 days, so the book is never actually in cash. A "
                "grey anchor stretch is the book parked in SPY/BIL, not a decision to hold cash.",
                "c-alloc", 580),
        panel(10, "Thesis concentration",
                "How much of the whole portfolio is riding on one event. Anchors are not a bet, so a "
                "day parked in SPY/BIL reads 0%; the dashed line is the per-ticker cap, for scale.",
                "c-evconc", 380),
        panel(11, "Curator funnel",
              "Everything the curator touched, from the articles it read down to the picks it logged. "
              "The direct analogue of the firehose funnel — but here the interesting collapse is at the "
              "top: a whole week of articles yields a handful of candidates. Log x-axis.",
              "c-funnel", 340),
        panel(12, "Breadth over time",
              "Events live, distinct tickers named, and how many separate catalysts those events "
              "represent — per rebalance. Several events on one theme is concentration wearing a "
              "diversity costume, which is why catalysts are drawn separately from events. The dashed "
              f"line is <code>max_watchlist</code> = {_wcap}. Since the unfunded prune was turned on "
              f"(2026-08-09) it binds in only <b>{_cull_bind} of {len(_held_per_week)}</b> weeks — the "
              "prune keeps the live set under the cap by itself, so the cap is a backstop rather than "
              "an active knob. What used to sit between the solid and dashed lines was inventory the "
              "optimizer was never going to fund.",
              "c-breadth", 380),
        panel(13, "Scout inflow vs the cap",
              "What the scout proposed each week against what it was allowed to admit "
              f"(<code>max_new_events</code>, in {_LINK(PROFILE_URL, 'investor_profile.backtest.md')}). "
              "If the proposal line sits below the cap, breadth is limited by the curator's judgement, "
              "not by the knob — and loosening the knob would change nothing.",
              "c-inflow", 340),
        panel(14, "Coverage vs picks, per ticker",
              "Article counts for the 40 most-covered tickers in the corpus. <b>Green</b> got "
              "watchlisted at some point; <b>grey</b> was named in the news but never watchlisted.",
              "c-cov", 720),
        panel(15, "Evidence by lede provenance",
              "For every article the curator cited as evidence, where its text came from. If picks "
              "cluster on <b>archived</b> text (Wayback, look-ahead-clean) the clean arm is earning its cost; if they cluster on "
              "<b>live page</b> text the corpus is leaning on look-ahead-biased material.",
              "c-lede", 300),
        panel(16, "Evidence by source",
              "Which outlets actually produced the articles behind the picks. Compare with the "
              "firehose dashboard's source panel: an outlet supplying much of the corpus but little of "
              "the evidence is volume without signal.",
              "c-src", 620),
        panel(17, "Evidence by beat",
              f"Which standing searches ({_LINK(CONFIG_URL, 'retrieval_config.json')}) produced the "
              "articles behind the picks. A beat that fills the corpus but never appears here is "
              "paying rent without earning it.",
              "c-beat", 560),
        panel(18, "Agent precision",
              "Every thesis the curator held, and what that ticker returned over its live span — the "
              "standalone result of the idea, before any position sizing. This is the closest thing "
              "on this page to a skill measure: it asks whether the curator's calls were RIGHT, not "
              "whether the optimizer weighted them well. Green is profitable, red is not. Read it "
              "beside <i>Breadth over time</i>: breadth without precision is noise, precision "
              "without breadth is luck.",
              "c-prec", 620,
              table_html(["ticker", "return %", "live span", "thesis"],
                         [[x["ticker"], f"{100*x['ret']:+.1f}%", f"{x['first']} → {x['last']}",
                           x["thesis"][:90]] for x in sorted(prec, key=lambda z: -z["ret"])])),
        panel(19, "Event storyboard",
              "Each event's week-by-week journal: what the agent concluded, and why it eventually "
              "exited. The qualitative counterpart to the curation log — the only place you can see "
              "whether the exit logic is REASONING about a catalyst resolving or just pattern-matching "
              "on a price move. Funded events first, then those that never held capital.",
              "c-story", 0, story_html),
        panel(20, "Text provenance of what the curator read",
              "Per week, how much of the pool reached the curator as <b>archived</b> text, <b>live-page</b> "
              "text, or a bare <b>headline</b>. This is the firehose's provenance panel restricted to the "
              "slices the curator actually read. <b>Archived = Wayback</b> (archive.org's snapshot as of "
              "the article's own date), which is the only look-ahead-CLEAN text here: a live page is "
              "fetched today and may have been edited, extended or corrected since publication, so it can "
              "carry knowledge the curator could not have had. A week leaning on live text is a week "
              "whose result is an upper bound.",
              "c-text", 340),
    ])
    panels += log_panel          # the log is a reference table, not a headline -- it reads last

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Curator Backtest (CBT)</title>
<script src="{PLOTLY_CDN}"></script>
<style>
:root {{ --surface:{LIGHT['surface']}; --card:#ffffff; --text:{LIGHT['text']}; --text2:{LIGHT['text2']};
  --grid:{LIGHT['grid']}; --line:#e6e5e1; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --surface:{DARK['surface']}; --card:#222220; --text:{DARK['text']}; --text2:{DARK['text2']};
  --grid:{DARK['grid']}; --line:#33322f; }} }}
:root[data-theme="dark"] {{ --surface:{DARK['surface']}; --card:#222220; --text:{DARK['text']};
  --text2:{DARK['text2']}; --grid:{DARK['grid']}; --line:#33322f; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--surface); color:var(--text);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }}
.wrap {{ max-width:1180px; margin:0 auto; padding:28px 20px 80px; }}
header h1 {{ font-size:26px; margin:0 0 4px; letter-spacing:-.01em; }}
.sub {{ color:var(--text2); font-size:14px; margin:0 0 6px; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(238px,1fr)); gap:12px; margin-bottom:30px; }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:13px 14px; }}
.tile-h {{ display:flex; align-items:center; gap:7px; margin-bottom:5px; }}
.dot {{ font-size:11px; }}
.tl {{ font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--text2); flex:1; }}
.badge {{ font-size:10px; text-transform:uppercase; letter-spacing:.05em; border:1px solid;
  border-radius:20px; padding:1px 7px; }}
.tv {{ font-size:27px; font-weight:600; letter-spacing:-.02em; }}
.ts {{ font-size:12.5px; color:var(--text2); margin-bottom:7px; }}
.tw {{ font-size:12.5px; color:var(--text2); line-height:1.5; }}
.panel {{ margin:0 0 34px; }}
.panel h2 {{ font-size:17px; margin:0 0 5px; font-weight:600; }}
.lead {{ color:var(--text2); font-size:13.5px; margin:0 0 12px; max-width:80ch; }}
.plot {{ background:var(--card); border:1px solid var(--line); border-radius:10px; }}
details.tbl {{ margin-top:9px; font-size:13px; color:var(--text2); }}
details.tbl summary {{ cursor:pointer; }}
table {{ border-collapse:collapse; margin-top:9px; width:100%; font-size:12.5px; }}
th,td {{ text-align:left; padding:5px 9px; border-bottom:1px solid var(--line); }}
th {{ color:var(--text2); font-weight:600; }}
.params {{ width:auto; }} .params td:first-child {{ color:var(--text2); }}
.scroll {{ overflow-x:auto; }}
#tkmodal {{position:fixed; inset:0; display:none; z-index:900;
           background:rgba(0,0,0,.45); backdrop-filter:blur(2px);}}
#tkcard  {{position:absolute; left:50%; top:50%; transform:translate(-50%,-50%);
           width:min(940px, 94vw); background:var(--surface); border:1px solid var(--line);
           border-radius:10px; padding:16px 18px 8px; box-shadow:0 12px 40px rgba(0,0,0,.3);}}
#tkclose {{position:absolute; right:12px; top:8px; border:none; background:none; cursor:pointer;
           font-size:22px; line-height:1; color:var(--text2);}}
</style></head><body>
<div id="tkmodal" onclick="if(event.target.id==='tkmodal')window._hideTk()">
  <div id="tkcard">
    <button id="tkclose" onclick="window._hideTk()" title="close">&times;</button>
    <h3 id="tktitle" style="margin:0 0 2px;font-size:16px;"></h3>
    <p id="tksub" class="lead" style="margin:0 0 8px;"></p>
    <div id="tkplot" style="height:420px;"></div>
  </div>
</div>
<div class="wrap">
{dash_nav.render('cbt.html')}
<header>
  <h1>Curator Backtest (CBT)</h1>
  <p class="sub">{esc(weeks[0] if weeks else '?')} &rarr; {esc(weeks[-1] if weeks else '?')}
     &middot; {len(M)} rebalances &middot; {J.get('nid', 0)} events &middot; {len(all_veh)} tickers</p>
</header>
<div class="tiles">{tiles}</div>
{panels}
</div>
<script>
const DATA = {json.dumps(payload, default=str)};
const L = {json.dumps(LIGHT)}, D = {json.dumps(DARK)}, ST = {json.dumps(STATUS)};
function pal() {{
  const dark = document.documentElement.dataset.theme === 'dark' ||
    (document.documentElement.dataset.theme !== 'light' &&
     window.matchMedia('(prefers-color-scheme: dark)').matches);
  return dark ? D : L;
}}
function base(p, extra) {{
  return Object.assign({{
    paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
    font:{{color:p.text2, size:12, family:'-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif'}},
    margin:{{l:60,r:24,t:14,b:44}},
    xaxis:{{gridcolor:p.grid, zerolinecolor:p.grid, linecolor:p.grid}},
    yaxis:{{gridcolor:p.grid, zerolinecolor:p.grid, linecolor:p.grid}},
    hoverlabel:{{bgcolor:p.surface, bordercolor:p.grid, font:{{color:p.text}}}},
    showlegend:false
  }}, extra || {{}});
}}
const CFG = {{displayModeBar:false, responsive:true}};
// Beat -> colour, shared by EVERY panel that colours by beat (composition, gain-per-holding,
// gain-per-beat, event timeline) so one colour means one thing across the page. Grey is RESERVED
// for "no beat-attributable evidence" and is deliberately absent from the categorical ramp.
const GREY = '#9aa4ae';
const _bts2 = (DATA.book && DATA.book.wcomp && DATA.book.wcomp.beats) || [];
const _bcol = b => (!b || b === 'no beat') ? GREY : PALS[Math.max(0, _bts2.indexOf(b)) % PALS.length];
const _dark = (hex, f) => {{
  if (!hex || hex[0] !== '#') return hex;
  const n = parseInt(hex.slice(1), 16);
  return `rgb(${{Math.round(((n>>16)&255)*f)}},${{Math.round(((n>>8)&255)*f)}},${{Math.round((n&255)*f)}})`;
}};

// PWR's softer stacked-area palette. A 15-series stack cannot be made CVD-safe by hue alone --
// no palette can at that count -- so identity is carried by the legend and hover, and these are
// chosen to be low-saturation enough that adjacent bands read as bands rather than as alarms.
const PALS = ['#7fb3d5','#f0b27a','#82c9a0','#c39bd3','#f5cba7','#a3c4dc','#d7bde2',
'#a2d9ce','#f9c6c9','#aed6f1','#f7dc6f','#d5a6bd','#a9cce3','#abebc6','#e8a598'];

function draw() {{
  const p = pal();
  const F = DATA.funnel;
  Plotly.react('c-funnel', [{{
    type:'bar', orientation:'h', x:F.values, y:F.labels,
    marker:{{color:p.ord.map ? p.ord[1] : p.s1, line:{{width:2,color:p.surface}}}},
    text:F.values.map(v=>v.toLocaleString()), textposition:'outside',
    textfont:{{color:p.text2, size:11}}, cliponaxis:false,
    hovertemplate:'%{{y}}<br>%{{x:,}}<extra></extra>'
  }}], base(p, {{margin:{{l:180,r:90,t:10,b:40}},
      yaxis:{{autorange:'reversed', gridcolor:'rgba(0,0,0,0)', automargin:true}},
      xaxis:{{type:'log', gridcolor:p.grid, title:{{text:'count (log scale)', font:{{size:11}}}}}}}}), CFG);

  const B = DATA.breadth;
  Plotly.react('c-breadth', [
    {{type:'scatter', mode:'lines+markers', name:'events live', x:B.w, y:B.events,
      line:{{color:p.s1,width:2}}, marker:{{size:6,color:p.s1,line:{{width:1.5,color:p.surface}}}}}},
    {{type:'scatter', mode:'lines+markers', name:'tickers named', x:B.w, y:B.vehicles,
      line:{{color:p.s2,width:2}}, marker:{{size:6,color:p.s2,line:{{width:1.5,color:p.surface}}}}}},
    {{type:'scatter', mode:'lines+markers', name:'distinct catalysts', x:B.w, y:B.catalysts,
      line:{{color:p.s3,width:2,dash:'dot'}}, marker:{{size:6,color:p.s3,line:{{width:1.5,color:p.surface}}}}}},
    {{type:'scatter', mode:'lines', name:'max_watchlist cap', x:B.w, y:B.w.map(()=>B.cap),
      line:{{color:ST.serious,width:2,dash:'dash'}}, hovertemplate:'cap %{{y}}<extra></extra>'}},
    {{type:'scatter', mode:'lines', name:'actually funded', x:B.w, y:B.held,
      line:{{color:ST.good,width:2}}, hovertemplate:'%{{x}}<br>%{{y}} funded<extra></extra>'}}
  ], base(p, {{showlegend:true, legend:{{orientation:'h', y:1.13, x:0, font:{{size:11.5}}}},
      margin:{{l:60,r:24,t:36,b:60}},
      yaxis:{{gridcolor:p.grid, rangemode:'tozero', title:{{text:'count', font:{{size:11}}}}}}}}), CFG);

  const I = DATA.inflow;
  Plotly.react('c-inflow', [
    {{type:'bar', name:'proposed', x:I.w, y:I.prop, marker:{{color:p.s1, line:{{width:2,color:p.surface}}}}}},
    {{type:'bar', name:'admitted', x:I.w, y:I.adm, marker:{{color:p.s3, line:{{width:2,color:p.surface}}}}}},
    {{type:'scatter', mode:'lines', name:'cap', x:I.w, y:I.cap,
      line:{{color:ST.critical, width:2, dash:'dash'}}}}
  ], base(p, {{barmode:'group', showlegend:true,
      legend:{{orientation:'h', y:1.15, x:0, font:{{size:11.5}}}}, margin:{{l:60,r:24,t:36,b:60}},
      // LOG y: `proposed` runs 2-72 while `admitted` is pinned at 2-4 by the cap, so on a linear axis
      // the admitted bars and the cap line are flattened into the baseline and the whole point of the
      // panel -- how far the scout's supply overshoots what is let through -- is invisible.
      // rangemode:'tozero' is dropped: it is meaningless on a log axis, which cannot reach 0.
      yaxis:{{type:'log', gridcolor:p.grid, title:{{text:'candidates (log)', font:{{size:11}}}}}}}}), CFG);

  const G = DATA.gantt.filter(g=>g.start);
  const _gseen = new Set();
  const _gspan = G.map(g=>({{                       // proposed -> terminated, pale
    type:'scatter', mode:'lines', x:[g.start, g.end || g.start], y:[g.id, g.id],
    line:{{color:_bcol(g.beat), width:11}}, opacity:0.28, showlegend:false, hoverinfo:'text',
    hovertext:`${{g.id}} &mdash; ${{g.cat}}<br>${{g.veh.join(', ')}}<br>${{g.beat}}<br>`
             + `proposed ${{g.start}} &rarr; ${{g.end || g.start}}${{g.fund.length ? '' : '<br><b>never funded</b>'}}`
  }}));
  const _gfund = [];                                // funded spans, solid, one legend entry per beat
  G.forEach(g => (g.fund || []).forEach(fr => {{
    const first = !_gseen.has(g.beat); _gseen.add(g.beat);
    _gfund.push({{
      type:'scatter', mode:'lines', x:[fr[0], fr[1]], y:[g.id, g.id],
      line:{{color:_dark(_bcol(g.beat), 0.72), width:7}},
      name:g.beat, legendgroup:g.beat, showlegend:first, hoverinfo:'text',
      hovertext:`${{g.id}} &mdash; ${{g.cat}}<br>${{g.beat}}<br>FUNDED ${{fr[0]}} &rarr; ${{fr[1]}}`
    }});
  }}));
  Plotly.react('c-gantt', [..._gspan, ..._gfund], base(p, {{margin:{{l:70,r:30,t:10,b:44}},
      yaxis:{{gridcolor:'rgba(0,0,0,0)', automargin:true, tickfont:{{size:10}}}},
      xaxis:{{gridcolor:p.grid}}}}), CFG);

  const C = DATA.cov;
  Plotly.react('c-cov', [{{
    type:'bar', orientation:'h', x:C.n.slice().reverse(), y:C.t.slice().reverse(),
    marker:{{color:C.picked.slice().reverse().map(b=>b?ST.good:p.grid), line:{{width:2,color:p.surface}}}},
    text:C.picked.slice().reverse().map((b,i)=>C.n.slice().reverse()[i].toLocaleString()+(b?' ✓ picked':'')),
    textposition:'outside', textfont:{{color:p.text2, size:10}}, cliponaxis:false,
    hovertemplate:'%{{y}}<br>%{{x:,}} articles<extra></extra>'
  }}], base(p, {{margin:{{l:90,r:130,t:10,b:44}},
      yaxis:{{gridcolor:'rgba(0,0,0,0)', automargin:true, tickfont:{{size:10}}}},
      xaxis:{{gridcolor:p.grid, title:{{text:'articles naming this ticker', font:{{size:11}}}}}}}}), CFG);

  const LD = DATA.lede;
  Plotly.react('c-lede', [{{
    type:'bar', x:LD.k, y:LD.n,
    marker:{{color:LD.k.map(k=>k==='archived'?ST.good:k==='live page'?p.s2:p.grid),
             line:{{width:2,color:p.surface}}}},
    text:LD.n.map(v=>v.toLocaleString()), textposition:'outside',
    textfont:{{color:p.text2, size:11}}, cliponaxis:false,
    hovertemplate:'%{{x}}<br>%{{y:,}} cited articles<extra></extra>'
  }}], base(p, {{yaxis:{{gridcolor:p.grid, title:{{text:'cited articles', font:{{size:11}}}}}}}}), CFG);

  const S = DATA.src;
  Plotly.react('c-src', [{{
    type:'bar', orientation:'h', x:S.n.slice().reverse(), y:S.s.slice().reverse(),
    marker:{{color:p.s1, line:{{width:2,color:p.surface}}}},
    text:S.n.slice().reverse().map(v=>v.toLocaleString()), textposition:'outside',
    textfont:{{color:p.text2, size:10}}, cliponaxis:false,
    hovertemplate:'%{{y}}<br>%{{x:,}} cited<extra></extra>'
  }}], base(p, {{margin:{{l:190,r:70,t:10,b:44}},
      yaxis:{{gridcolor:'rgba(0,0,0,0)', automargin:true, tickfont:{{size:10}}}},
      xaxis:{{gridcolor:p.grid, title:{{text:'articles cited as evidence', font:{{size:11}}}}}}}}), CFG);

  const BT = DATA.beat;
  Plotly.react('c-beat', [{{
    type:'bar', orientation:'h', x:BT.n.slice().reverse(), y:BT.b.slice().reverse(),
    marker:{{color:p.s3, line:{{width:2,color:p.surface}}}},
    text:BT.n.slice().reverse().map(v=>v.toLocaleString()), textposition:'outside',
    textfont:{{color:p.text2, size:10}}, cliponaxis:false,
    hovertemplate:'%{{y}}<br>%{{x:,}} cited<extra></extra>'
  }}], base(p, {{margin:{{l:300,r:70,t:10,b:44}},
      yaxis:{{gridcolor:'rgba(0,0,0,0)', automargin:true, tickfont:{{size:10}}}},
      xaxis:{{gridcolor:p.grid, title:{{text:'articles cited as evidence', font:{{size:11}}}}}}}}), CFG);

  const BK = DATA.book;
  if (BK.dates && BK.dates.length) {{
    // PWR's CBT equity-curve schema, deliberately matched so the two repos' pages read alike:
    // amber = the curated book, blue = buy-and-hold of the opening basket, green dashed = SPY.
    // Square markers sit on the curated curve at every rebalance, split by whether the curator
    // actually CHANGED anything that week -- a rebalance that altered nothing is a different event
    // from one that opened or closed a thesis, and the split is the cheapest way to see cadence.
    const _ch = new Set(BK.changed_weeks || []);
    const _ri = (BK.rebal || []).map(w => BK.dates.indexOf(w)).filter(i => i >= 0);
    const _mk = (want) => {{
      const xs = [], ys = [];
      (BK.rebal || []).forEach(w => {{
        const i = BK.dates.indexOf(w);
        if (i >= 0 && (_ch.has(w) === want)) {{ xs.push(BK.dates[i]); ys.push(BK.value[i]); }}
      }});
      return [xs, ys];
    }};
    const [cx, cy] = _mk(true), [nx, ny] = _mk(false);
    Plotly.react('c-value', [
      {{type:'scatter', mode:'lines', name:'Curator-driven', x:BK.dates, y:BK.value,
        line:{{color:'#d97706', width:2.5}}, hovertemplate:'%{{x}}<br>%{{y:$,.0f}}<extra>curator</extra>'}},
      {{type:'scatter', mode:'lines', name:'Buy-and-hold (starter_watchlist)', x:BK.dates, y:BK.bh,
        line:{{color:'#3b82f6', width:2}}, hovertemplate:'%{{x}}<br>%{{y:$,.0f}}<extra>buy &amp; hold</extra>'}},
      {{type:'scatter', mode:'lines', name:'SPY benchmark', x:BK.dates, y:BK.spyser,
        line:{{color:'#10b981', width:2, dash:'dash'}}, hovertemplate:'%{{x}}<br>%{{y:$,.0f}}<extra>SPY</extra>'}},
      {{type:'scatter', mode:'markers', name:'Rebalanced (no change)', x:nx, y:ny,
        marker:{{symbol:'square', size:7, color:'#ea580c', line:{{width:1.5, color:p.surface}}}},
        hovertemplate:'%{{x}}<br>rebalanced, watchlist unchanged<extra></extra>'}},
      {{type:'scatter', mode:'markers', name:'Watchlist changed', x:cx, y:cy,
        marker:{{symbol:'square', size:9, color:'#dc2626', line:{{width:1.5, color:p.surface}}}},
        hovertemplate:'%{{x}}<br>an event opened or closed<extra></extra>'}}
    ], base(p, {{showlegend:true, legend:{{orientation:'h', y:1.14, x:0, font:{{size:11}}}},
        margin:{{l:74,r:24,t:40,b:44}},
        // LOG y-axis: the book grows ~11x, so linearly the first two years flatten onto the baseline
          // and only the last leg is legible. On a log scale equal vertical distances are equal
          // PERCENTAGE moves, which is what makes the curator line comparable to SPY anywhere on it.
          yaxis:{{type:'log', gridcolor:p.grid, tickprefix:'$', title:{{text:'portfolio value (log)', font:{{size:11}}}}}}}}), CFG);

    // 2. watchlist composition -- horizontal spans, pale = watchlisted, solid = funded. Ticker rows are
  //    ordered by first appearance so the page reads chronologically down the axis.
  const WC = BK.wcomp || {{watch:[], funded:[], beats:[]}};
  const _seenT = [];
  WC.watch.forEach(s => {{ if (!_seenT.includes(s.t)) _seenT.push(s.t); }});
  WC.funded.forEach(s => {{ if (!_seenT.includes(s.t)) _seenT.push(s.t); }});
  const _ancSet = new Set(BK.anchors || []);
  const _order = [..._seenT.filter(x => !_ancSet.has(x)), ..._seenT.filter(x => _ancSet.has(x))];
  const _bts = WC.beats || [];
  const _seen = new Set();
  const _seg = (spans, opacity, width, name, shade) => spans.map(s => {{
    const first = Boolean(shade) && !_seen.has(s.b);   // ONE legend entry per beat, not per span
    if (shade) _seen.add(s.b);
    return {{
      type:'scatter', mode:'lines', x:[s.s, s.e], y:[s.t, s.t],
      line:{{color: shade ? _dark(_bcol(s.b), shade) : _bcol(s.b), width:width}}, opacity:opacity,
      name: s.b, legendgroup: s.b, showlegend: first, hoverinfo:'text',
      hovertext:`${{s.t}} &middot; ${{s.b}}<br>${{s.ev || 'no event'}}<br>${{name}}<br>${{s.s}} &rarr; ${{s.e}}`
    }};
  }});
  Plotly.react('c-wcomp',
    [..._seg(WC.watch, 0.32, 9, 'watchlisted'), ..._seg(WC.funded, 1.0, 6, 'funded', 0.72)],
    base(p, {{showlegend:true,
      legend:{{orientation:'v', x:1.01, xanchor:'left', y:1, font:{{size:10}},
               title:{{text:'dominant beat', font:{{size:10}}}}}},
      margin:{{l:78, r:260, t:16, b:44}},
      yaxis:{{type:'category', categoryorder:'array', categoryarray:_order.slice().reverse(),
              // tickmode linear + dtick 1 = one label PER ROW. Without it Plotly thins the labels
              // whenever rows outnumber the pixels it thinks it has, silently hiding half the tickers.
              tickmode:'linear', dtick:1,
              gridcolor:p.grid, tickfont:{{size:10}}, automargin:true}},
      xaxis:{{gridcolor:p.grid, type:'date'}}}}), CFG);

  const TB = BK.tickerbeat || {{}};
  const GB = Object.entries(BK.gainbeat || {{}}).sort((a,b)=>a[1]-b[1]);

  // 6. GAIN PER 1,000 ARTICLES. The denominator is what makes an expensive beat visible: the momentum
  // beat is the single largest source of articles in the corpus and returns NEGATIVE dollars, which
  // panel 5 cannot show because it plots totals. Zero-gain beats are kept in deliberately -- they
  // retrieved articles and produced nothing, which is the cheapest thing to prune.
  {{
    const BA = BK.beatarts || {{}}, BG = BK.beatgated || {{}};
    // denominator = GATE-PASSED articles, not corpus articles: that is what the scout actually reads.
    const eff = Object.keys(BA)
      .map(b => [b, BA[b], BG[b] || 0, (BK.gainbeat || {{}})[b] || 0])
      .filter(r => r[2] >= 20)                       // below ~20 gated articles the ratio is noise
      .map(r => [r[0], r[1], r[2], r[3], r[3] / r[2]])
      .sort((a, b) => a[4] - b[4]);
    Plotly.react('c-beateff', [{{
      type:'bar', orientation:'h', x:eff.map(r=>r[4]), y:eff.map(r=>r[0]),
      marker:{{color:eff.map(r=>r[4] >= 0 ? _bcol(r[0]) : ST.critical),
               line:{{width:2, color:p.surface}}}},
      customdata:eff.map(r=>[r[1], r[2], r[3], 100*r[2]/r[1]]),
      hovertemplate:'%{{y}}<br>%{{customdata[1]:,}} gate-passed of %{{customdata[0]:,}} in corpus (%{{customdata[3]:.1f}}%)<br>gain %{{customdata[2]:$,.0f}}'
                    +'<br><b>%{{x:$,.2f}} per gate-passed article</b><extra></extra>'
    }}], base(p, {{margin:{{l:250,r:24,t:16,b:46}},
        xaxis:{{gridcolor:p.grid, zeroline:true, zerolinecolor:p.text2, zerolinewidth:1.5,
                tickprefix:'$', title:{{text:'gain per GATE-PASSED article (what the scout reads)', font:{{size:11}}}}}},
        yaxis:{{gridcolor:'rgba(0,0,0,0)', automargin:true, tickfont:{{size:10}}, type:'category', tickmode:'linear', dtick:1}}}}), CFG);
  }}
  Plotly.react('c-gainb', [{{
    type:'bar', orientation:'h', x:GB.map(e=>e[1]), y:GB.map(e=>e[0]),
    marker:{{color:GB.map(e=>_bcol(e[0])), line:{{width:2,color:p.surface}}}},
    hovertemplate:'%{{y}}<br>%{{x:$,.0f}}<extra></extra>'
  }}], base(p, {{margin:{{l:250,r:100,t:10,b:44}},
      yaxis:{{gridcolor:'rgba(0,0,0,0)', automargin:true, tickfont:{{size:10}}, type:'category', tickmode:'linear', dtick:1}},
      xaxis:{{gridcolor:p.grid, zeroline:true, zerolinecolor:p.text2, zerolinewidth:1.5,
              tickprefix:'$', title:{{text:'cumulative gain', font:{{size:11}}}}}}}}), CFG);

  // CLICK-THROUGH on plot 3 -> that ticker's price history, with the span the curator held it
  // WATCHLISTED shaded and the FUNDED spans drawn heavier. The point is not the price line -- it is
  // seeing where the holding period sat inside the move.
  const PX = DATA.px || {{d:[], p:{{}}}};
  window._hideTk = () => {{ document.getElementById('tkmodal').style.display='none'; }};
  window._showTk = (tk) => {{
    const ser = (PX.p || {{}})[tk];
    if (!ser) return;
    const wl = (BK.wcomp.watch || []).filter(s => s.t === tk);
    const fd = (BK.wcomp.funded || []).filter(s => s.t === tk);
    const shapes = wl.map(s => ({{type:'rect', xref:'x', yref:'paper', x0:s.s, x1:s.e, y0:0, y1:1,
                                 fillcolor:_bcol(s.b), opacity:0.16, line:{{width:0}}, layer:'below'}}));
    const traces = [{{type:'scatter', mode:'lines', name:tk, x:PX.d, y:ser,
                      line:{{color:p.text2, width:1.6}}, hovertemplate:'%{{x}}<br>$%{{y:.2f}}<extra></extra>'}}];
    const _onX = [], _onY = [], _offX = [], _offY = [];
    fd.forEach(s => {{
      const i0 = PX.d.indexOf(s.s), i1 = PX.d.indexOf(s.e);
      if (i0 >= 0 && i1 > i0) {{
        traces.push({{
          type:'scatter', mode:'lines', name:'funded', showlegend:false,
          x:PX.d.slice(i0, i1+1), y:ser.slice(i0, i1+1),
          line:{{color:_dark(_bcol(wl.length?wl[0].b:''), 0.72), width:4}},
          hovertemplate:'FUNDED %{{x}}<br>$%{{y:.2f}}<extra></extra>'}});
        _onX.push(PX.d[i0]);  _onY.push(ser[i0]);      // the moment capital went IN
        _offX.push(PX.d[i1]); _offY.push(ser[i1]);     // ...and the moment it came OUT
      }}
    }});
    // The transitions are the point of the chart -- seeing WHERE on the move the optimizer bought
    // and sold. Segment edges alone are easy to miss on a 3-year line.
    if (_onX.length) traces.push({{
      type:'scatter', mode:'markers', name:'funded', showlegend:false, x:_onX, y:_onY,
      marker:{{symbol:'triangle-up', size:11, color:ST.good, line:{{width:1.5, color:p.surface}}}},
      hovertemplate:'FUNDED from %{{x}}<br>$%{{y:.2f}}<extra></extra>'}});
    if (_offX.length) traces.push({{
      type:'scatter', mode:'markers', name:'unfunded', showlegend:false, x:_offX, y:_offY,
      marker:{{symbol:'triangle-down', size:11, color:ST.critical, line:{{width:1.5, color:p.surface}}}},
      hovertemplate:'UNFUNDED at %{{x}}<br>$%{{y:.2f}}<extra></extra>'}});
    const g = BK.gain[tk];
    document.getElementById('tktitle').textContent = tk;
    document.getElementById('tksub').innerHTML =
      `shaded = watchlisted &middot; solid = funded &middot; &#9650; funded &#9660; unfunded &middot; realised `
      + `${{g>=0?'+':'&minus;'}}$${{Math.abs(g).toLocaleString(undefined,{{maximumFractionDigits:0}})}}`;
    document.getElementById('tkmodal').style.display='block';
    Plotly.react('tkplot', traces, base(p, {{showlegend:false, shapes:shapes,
        margin:{{l:64,r:20,t:10,b:40}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$'}}, xaxis:{{gridcolor:p.grid, type:'date'}}}}), CFG);
  }};
  // Bind the drill-down click ONCE PER GRAPH, and stop retrying once every graph is bound.
  // The retry exists because this runs before Plotly.react() has turned the div into a graph
  // (`g.on` does not exist yet), so the first pass always misses. But the terminating condition has
  // to be counted against the ACTUAL id list: an earlier `done < 2` against a one-id list could
  // never be satisfied, so bind() rescheduled itself every 150ms forever, appending ANOTHER
  // plotly_click handler to c-gainh each pass. Within a minute one click fired _showTk hundreds of
  // times, each doing a full Plotly.react on the modal -- the tab froze instead of opening the popup,
  // which reads exactly like "the popup doesn't work" (found 2026-08-14 on plot 4).
  const _CLICKABLE = ['c-gainh'];
  (function bind(){{
    const left = _CLICKABLE.filter(id => {{
      const g = document.getElementById(id);
      if (!g || !g.on || g._tkBound) return !(g && g._tkBound);   // not ready yet -> keep waiting
      g._tkBound = true;                                          // idempotent: never bind twice
      g.on('plotly_click', ev => {{
        const tk = ev.points[0].x;
        if (!String(tk).startsWith('other (')) window._showTk(tk);   // the rolled bar is not a name
      }});
      return false;
    }});
    if (left.length) setTimeout(bind, 150);
  }})();
  document.addEventListener('keydown', e => {{ if (e.key === 'Escape') window._hideTk(); }});


  const GH = Object.entries(BK.gain);
    // TOP 10 + BOTTOM 5 + one rolled-up bar for everything between. 85 funded names is unreadable as
    // 85 bars, and the middle of that distribution is the part that carries no information -- what
    // matters is which few names made the money, which few lost it, and whether the long tail nets
    // out to anything. Asymmetric on purpose: the winners are where the thesis either worked or
    // did not, so they get the deeper list. The rolled bar is grey because it is an aggregate.
    const _NTOP = 16, _NBOT = 8;
    const _gs = GH.slice().sort((a,b) => b[1] - a[1]);
    const _top = _gs.slice(0, _NTOP);
    const _bot = _gs.length > _NTOP + _NBOT ? _gs.slice(-_NBOT) : _gs.slice(_NTOP);
    const _midArr = _gs.slice(_NTOP, Math.max(_NTOP, _gs.length - _NBOT));
    const _mid = _midArr.reduce((s, e) => s + e[1], 0);
    const GHr = _midArr.length
      ? [..._top, [`other (${{_midArr.length}})`, _mid], ..._bot]
      : _gs;
    const _isRoll = lbl => String(lbl).startsWith('other (');
    Plotly.react('c-gainh', [{{
      type:'bar', x:GHr.map(e=>e[0]), y:GHr.map(e=>e[1]),
      marker:{{color:GHr.map(e=>_isRoll(e[0]) ? GREY : _bcol(TB[e[0]])),
               line:{{width:2,color:p.surface}}}},
      hovertemplate:'%{{x}}<br>%{{y:$,.0f}}<br>%{{customdata}}<extra></extra>',
      customdata:GHr.map(e=>_isRoll(e[0]) ? 'every other funded name, summed' : (TB[e[0]] || 'no beat'))
    }}], base(p, {{margin:{{l:72,r:24,t:26,b:80}},
        xaxis:{{gridcolor:'rgba(0,0,0,0)', tickangle:-90, automargin:true, tickfont:{{size:10}},
                tickmode:'linear', dtick:1}},
        yaxis:{{gridcolor:p.grid, zeroline:true, zerolinecolor:p.text2, zerolinewidth:1.5,
                tickprefix:'$', title:{{text:'cumulative gain', font:{{size:11}}}}}}}}), CFG);

    const _evbeat = Object.fromEntries((DATA.gantt || []).map(g=>[g.id, g.beat]));
    const GE = Object.entries(BK.evgain);
    Plotly.react('c-gaine', [{{
      type:'bar', orientation:'h', x:GE.map(e=>e[1]), y:GE.map(e=>e[0]),
      marker:{{color:GE.map(e=>_bcol(_evbeat[e[0]])), line:{{width:2,color:p.surface}}}},
      hovertemplate:'%{{y}}<br>%{{x:$,.0f}}<br>%{{customdata}}<extra></extra>',
      customdata:GE.map(e=>_evbeat[e[0]] || 'no beat')
    }}], base(p, {{margin:{{l:90,r:100,t:10,b:44}},
        yaxis:{{gridcolor:'rgba(0,0,0,0)', automargin:true, tickfont:{{size:11}}}},
        xaxis:{{gridcolor:p.grid, zeroline:true, zerolinecolor:p.text2, zerolinewidth:1.5,
                tickprefix:'$', title:{{text:'cumulative gain', font:{{size:11}}}}}}}}), CFG);

    // BK.alloc is a WEIGHT per ticker per day (sums to 1, or to 0 on a fully-cash day) -- NOT dollars.
    // Both stacks below label their axis '$', so convert here once: dollars = weight x that day's
    // portfolio value. Without this the y-axis read "$1" at every point in a book that grew 8x.
    const _DOL = {{}};
    Object.entries(BK.alloc).forEach(([t, a]) => {{
      _DOL[t] = a.map((w, i) => w * BK.value[i]);
    }});
    // DOLLARS HELD per event, not cumulative gain -- so the top edge of the stack IS the portfolio
    // value, which is what the panel title claims. A ticker named by more than one live event has its
    // dollars SPLIT evenly between them rather than counted twice. The ANCHORS (always_include) belong
    // to no event, so they get their own band; without it the stack would stop short of the portfolio
    // value and the difference would read as missing data. The band was called "anchors + cash", which
    // implied some of it was a deliberate cash position -- it never is. Measured 2026-08-14: the daily
    // weights sum to 1 within 1e-4 on every one of 734 days, because always_include [SPY, BIL]
    // guarantees idle capital a home. The band is 100% anchors.
    const _evVeh = Object.fromEntries((DATA.gantt || []).map(g => [g.id, g.veh || []]));
    const _nd = BK.dates.length;
    const _owners = {{}};                       // ticker -> how many events claim it
    Object.values(_evVeh).forEach(vs => vs.forEach(t => {{ _owners[t] = (_owners[t] || 0) + 1; }}));
    const _evDollars = Object.entries(_evVeh).map(([id, vs]) => {{
      const y = new Array(_nd).fill(0);
      vs.forEach(t => {{
        const a = _DOL[t];
        if (!a) return;
        for (let i = 0; i < _nd; i++) y[i] += a[i] / _owners[t];
      }});
      return [id, y];
    }}).filter(e => e[1].some(v => v > 0));
    const _evSum = new Array(_nd).fill(0);
    _evDollars.forEach(e => {{ for (let i = 0; i < _nd; i++) _evSum[i] += e[1][i]; }});
    const _rest = BK.value.map((v, i) => Math.max(0, v - _evSum[i]));
    Plotly.react('c-evtime', [
      {{type:'scatter', mode:'lines', stackgroup:'one', name:'anchors', x:BK.dates, y:_rest,
        line:{{width:0.5, color:GREY}}, fillcolor:GREY,
        hovertemplate:'%{{x}}<br>anchors %{{y:$,.0f}}<extra></extra>'}},
      ..._evDollars.map((e,i)=>({{
        type:'scatter', mode:'lines', stackgroup:'one', name:e[0], x:BK.dates, y:e[1],
        line:{{width:0.5, color:PALS[i % PALS.length]}}, fillcolor:PALS[i % PALS.length],
        hovertemplate:'%{{x}}<br>'+e[0]+' %{{y:$,.0f}}<extra></extra>'}}))
    ], base(p, {{showlegend:true, legend:{{orientation:'h', y:1.1, x:0, font:{{size:10}}}},
        margin:{{l:70,r:24,t:40,b:44}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$', title:{{text:'portfolio value', font:{{size:11}}}}}}}}), CFG);

    // 8. THESIS CONCENTRATION. Share of the FUNDED book held by its largest single event, per day.
    // concentration_cap bounds a TICKER; nothing bounds an EVENT, so a thesis naming four vehicles can
    // hold the whole book with every position still under the cap. Anchors are excluded -- they are
    // where idle cash parks, not a thesis, and counting them would dilute the very number in question.
    // Vehicles claimed by two live events are split evenly, as in panel 7, so one dollar is counted once.
    {{
      const ANC2 = new Set(BK.anchors || []);
      // LARGEST EVENT AS A SHARE OF THE WHOLE PORTFOLIO -- the question actually worth asking:
      // how much of MY MONEY is riding on one thesis?
      // This used to divide the largest event by the EVENT-DRIVEN total, with anchors excluded from
      // BOTH sides. So a day holding one ticker at 22.7% with 77% parked in SPY/BIL read as 100%
      // concentration (2023-10-12, CRLBF) -- the opposite of risky. 137 days sat at ~100% while the
      // dominant event held a MEDIAN OF ONE ticker and 38% of the book. It also emitted a null, and
      // so a visible gap, whenever the funded slice fell under 5% -- precisely the "nothing is
      // funded" case that carries no concentration risk at all (146 days, 20% of the backtest).
      // Anchors stay OUT of the numerator (SPY/BIL is where idle capital parks, not a bet) and IN
      // the denominator, so an all-anchor day now reads 0%: no bet, no risk, no gap.
      const conc = [], capline = [];
      for (let i = 0; i < _nd; i++) {{
        const byEv = {{}};
        Object.entries(_evVeh).forEach(([id, vs]) => vs.forEach(t => {{
          if (ANC2.has(t)) return;
          const w = (BK.alloc[t] || [])[i] || 0;         // already a fraction of portfolio value
          if (w > 0.001) byEv[id] = (byEv[id] || 0) + w / _owners[t];
        }}));
        conc.push(100 * Math.max(...Object.values(byEv), 0));
        capline.push(100 * (DATA.cap_pct || 25));
      }}
      const above = conc.filter(x => x >= 80).length;
      const live  = conc.filter(x => x > 0).length;
      Plotly.react('c-evconc', [
        {{type:'scatter', mode:'lines', name:'largest event, % of portfolio', x:BK.dates, y:conc,
          line:{{color:ST.critical, width:2}}, connectgaps:false,
          hovertemplate:'%{{x}}<br>largest thesis = %{{y:.0f}}% of the PORTFOLIO<extra></extra>'}},
        {{type:'scatter', mode:'lines', name:'per-TICKER cap (for scale)', x:BK.dates, y:capline,
          line:{{color:p.text2, width:1.5, dash:'dash'}}, hoverinfo:'skip'}}
      ], base(p, {{showlegend:true, legend:{{orientation:'h', y:1.16, x:0, font:{{size:11}}}},
          margin:{{l:60,r:24,t:44,b:44}},
          yaxis:{{gridcolor:p.grid, range:[0,105], ticksuffix:'%',
                  title:{{text:'largest thesis, % of portfolio', font:{{size:11}}}}}},
          annotations:[{{xref:'paper', yref:'paper', x:0.01, y:0.06, showarrow:false,
            font:{{size:11.5, color:p.text2}},
            text:`one thesis held &ge;80% of the book on <b>${{above}}</b> of ${{live}} funded days`}}]}}), CFG);

    }}


    // The always_include anchors are not curator picks -- they are where idle capital parks. Giving
    // them ONE shared neutral colour stops three separate hues implying three separate theses, and
    // makes the anchor band read at a glance as "capital the curator did not deploy".
    const ANCH = new Set(BK.anchors || []);
    const _ANCHC = GREY;
    const _nonAnchor = Object.keys(BK.alloc).filter(k => !ANCH.has(k));
    const _allocSum = new Array(BK.dates.length).fill(0);
    Object.values(_DOL).forEach(a => a.forEach((v,i) => {{ _allocSum[i] += v; }}));
    // NO CASH SERIES. It used to be drawn as its own band "so an empty stretch reads as a decision,
    // not as missing data" -- but there is never an empty stretch: always_include [SPY, BIL] absorbs
    // idle capital, so the weights sum to 1 within 1e-4 on all 734 days and the band could only ever
    // draw a hairline of float rounding. Drawing it invited the reader to see a cash position that
    // does not exist. If a future config drops the anchors this must come back.
    Plotly.react('c-alloc', [
      ...Object.entries(_DOL).map((e,i)=>({{
      type:'scatter', mode:'lines', stackgroup:'one', name:e[0], x:BK.dates, y:e[1],
      legendgroup: ANCH.has(e[0]) ? 'anchors' : e[0],
      // Anchors share ONE grey; the palette index is taken over NON-anchors only, so pulling the
      // anchors out of the ramp doesn't leave holes or shift every other series' colour.
      line:{{width:0.5, color: ANCH.has(e[0]) ? _ANCHC : PALS[_nonAnchor.indexOf(e[0]) % PALS.length]}},
      fillcolor: ANCH.has(e[0]) ? _ANCHC : PALS[_nonAnchor.indexOf(e[0]) % PALS.length]
    }}))], base(p, {{showlegend:true,
        // ~47 series wrap to several legend rows; anchoring the legend's BOTTOM just above the plot
        // and reserving real top margin keeps it fully clear instead of spilling back over the area.
        legend:{{orientation:'h', yanchor:'bottom', y:1.02, xanchor:'left', x:0, font:{{size:10}}}},
        margin:{{l:70,r:24,t:150,b:44}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$', title:{{text:'dollars held', font:{{size:11}}}}}}}}), CFG);
  }}

  const PR = DATA.prec;
  Plotly.react('c-prec', [{{
    type:'bar', orientation:'h', x:PR.r, y:PR.t,
    marker:{{color:PR.r.map(v=>v>0?ST.good:ST.critical), line:{{width:2,color:p.surface}}}},
    text:PR.r.map(v=>(v>0?'+':'')+v.toFixed(0)+'%'), textposition:'outside',
    textfont:{{color:p.text2, size:10.5}}, cliponaxis:false,
    customdata:PR.t.map((_,i)=>[PR.th[i], PR.span[i]]),
    hovertemplate:'%{{y}} %{{x:.1f}}%<br>%{{customdata[0]}}<br>%{{customdata[1]}}<extra></extra>'
  }}], base(p, {{margin:{{l:80,r:80,t:10,b:44}},
      yaxis:{{gridcolor:'rgba(0,0,0,0)', automargin:true, tickfont:{{size:11}}}},
      xaxis:{{gridcolor:p.grid, zeroline:true, zerolinecolor:p.text2, zerolinewidth:1.5,
              ticksuffix:'%', title:{{text:'standalone return over the live span', font:{{size:11}}}}}}}}), CFG);

  const T = DATA.text;
  Plotly.react('c-text', [
    {{type:'bar', name:'archived', x:T.w, y:T.clean, marker:{{color:ST.good, line:{{width:2,color:p.surface}}}}}},
    {{type:'bar', name:'live page', x:T.w, y:T.live, marker:{{color:p.s2, line:{{width:2,color:p.surface}}}}}},
    {{type:'bar', name:'headline only', x:T.w, y:T.none, marker:{{color:p.grid, line:{{width:2,color:p.surface}}}}}}
  ], base(p, {{barmode:'stack', showlegend:true,
      legend:{{orientation:'h', y:1.15, x:0, font:{{size:11.5}}}}, margin:{{l:60,r:24,t:36,b:60}},
      yaxis:{{gridcolor:p.grid, title:{{text:'articles read', font:{{size:11}}}}}}}}), CFG);
}}
draw();
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
</script>
</body></html>"""
    out = ROOT / a.out if not Path(a.out).is_absolute() else Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc)
    print(f"wrote {out}  ({len(doc)/1024:.0f} KB, {len(M)} weeks, {J.get('nid',0)} events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
