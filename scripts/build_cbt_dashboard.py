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
import datetime as _dt
import json
import re
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
import lede as _lede
import orgs as _orgs  # noqa: E402  SIZE_BUCKETS -- one bucket definition, shared with FBS
import provenance as _canon  # noqa: E402  canonical-inputs gate
import optimizer as _opt_gate  # noqa: E402  profile read by the gate, before the body
import gkg as _gkg  # noqa: E402  canon_beat: reconcile corpus tags with renamed beats
from build_fbt_dashboard import (CONFIG_URL, DARK, LIGHT, PLOTLY_CDN, PROFILE_URL,  # noqa: E402
                                 STATUS, _LINK, esc, panel_rec, render_panels,
                                 table_html, tile)


def load(run: Path, corpus: Path, bootstrap: bool = False):
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
    if bootstrap:
        # ASSEMBLED, not read from disk: the bootstrap corpus is deliberately never materialised
        # ("a copy of two sources is a third thing that can drift from both"), and load() already
        # applies the article contract, so the curator-facing shape is identical to a pool.json.
        import bootstrap_corpus as _bs
        _arts, _bm = _bs.load(org_tagger=_bs.profile_org_tagger())
        cd = {"articles": _arts, **{k: v for k, v in _bm.items() if k != "ingest"}}
    else:
        cd = json.loads((corpus / "pool.json").read_text())
    arts = cd.get("articles", cd) if isinstance(cd, dict) else cd
    return m, j, dec, picks, {a.get("url", ""): a for a in arts}, arts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # The CANONICAL curation, kept in step with whatever the published page shows. Was
    # data/cbt_1yr until 2026-08-21, ~50 commits after the page had moved to the 3yr grok
    # run -- so a bare `python scripts/build_cbt_dashboard.py` quietly rebuilt docs/cbt.html
    # off a 52-week/26-event curation. It is rendered into the params table now, so the
    # next time this drifts the page says so instead of looking merely disappointing.
    ap.add_argument("--run", default=_canon.CANON_RUN)
    # DEFAULTED TO THE 1-YEAR POOL UNTIL 2026-08-19, while every curation since v5 read the 3-year
    # one. Built without --corpus, this page therefore reported 38,896 articles from
    # data/backtest_1yr when the curator had actually read 99,117 from data/backtest_3yr_v5 -- so
    # the beat-bundle, coverage and article-count panels were computed off a pool the curator
    # never saw. The row below now names the path explicitly so the mismatch is visible.
    ap.add_argument("--corpus", default=_canon.CANON_CORPUS)
    ap.add_argument("--out", default=None)
    ap.add_argument("--bootstrap", action="store_true",
                    help="render CBS (docs/cbs.html) -- the curation of the BOOTSTRAP corpus "
                         "(src/bootstrap_corpus) under investor_profile.forward.md. Reads the "
                         "assembled corpus instead of a --corpus pool.json.")
    a = ap.parse_args(argv)
    # CBS defaults: its own run dir, its own page, its own profile. CBT's defaults are canonical
    # constants, so --bootstrap has to redirect all three or it would render the backtest curation
    # under a bootstrap label.
    if a.bootstrap:
        if a.run == _canon.CANON_RUN:
            # cbs_v4. The history, because each version fixed a way of MISREPRESENTING the handover:
            #   v1  no --decisions -> decisions.jsonl absent and four panels drew ZEROS rather than
            #       empty (funnel proposed/admitted, scout inflow, bundle-vs-scout, gains-per-bundle).
            #       An absent input drawn as zero asserts something false ("bigger bundles never made
            #       the scout act"), which is worse than an empty chart.
            #   v2  seeded the WATCHLIST only, so the backtest's live theses were dropped at the
            #       handover and their capital sat in one undifferentiated "held, no live event"
            #       band -- 69.1% of the book.
            #   v3  seeded the journal, but on the wrong test: "the last entry on or before the
            #       handover says thesis_live". That is not "still being carried" -- an event the
            #       curator STOPPED re-judging keeps its final entry forever. It carried 85 where the
            #       run held 29; 56 were already out of span, median 300 days stale, max 660.
            #   v4  carries an event iff the run re-judged it AT its last scan on or before the
            #       handover and that judgement was live. Reproduces the run's own events_live (29).
            #   v5  the re-curation at MONTHLY cadence (the profile's, matching the backtest),
            #       with the warm-up month so scan 1 reads a full window (2,809 articles, not 101),
            #       the wall-clock age offset on seeded events, and -- the big one -- search
            #       snippets preserved: scan 5 reads 2,290 search snippets where v4 read 2,171
            #       headlines, because lede.apply used to overwrite every websearch snippet with
            #       the title before the curator saw it.
            #   v6  re-curated 2026-08-27 to pick up five changes at once: the wall-clock age
            #       offset on seeded events, search-snippet preservation (the last scan reads 2,358
            #       search snippets where v5 read 2,171 HEADLINES), lede.apply's cap 280 -> 800,
            #       min_bundle_articles reaching the live path, and concentration_cap 0.25 -> 0.6.
            #       Five reasons for any v5/v6 difference, so read the MECHANISM counts (panel 24's
            #       provenance bands, the seeded-event retirements), never the book value.
            a.run = _canon.CANON_BOOTSTRAP_RUN
        a.out = a.out or "docs/cbs.html"
    a.out = a.out or "docs/cbt.html"
    run, corpus = ROOT / a.run, ROOT / a.corpus
    # THE GATE. CBT's claim is "this is the book the current profile produces on the canonical
    # curation", so all three inputs are checked before anything is rendered: the corpus, the
    # curation's recorded curation-knobs, and that every profile knob is classified. BOOK knobs are
    # deliberately NOT checked -- max_watchlist 8 -> 6 is a rebuild, not an invalidation.
    _problems = []
    _interp = _canon.check_interpreter()
    if _interp:
        _problems.append(_interp)          # NEVER exempt: orthogonal to which corpus is rendered
    # THE ARM'S PROFILE, used by EVERY profile read on this page. It was hard-coded to
    # .backtest.md in four places, so CBS -- which is CURATED under .forward.md -- displayed the
    # backtest's parameters AND replayed its book with the backtest's BOOK knobs. That is not a
    # label bug: max_stale_scans is 8 in .backtest and 32 in .forward, so the CBS book was being
    # replayed under a different holding rule than the curation ran with. Caught by reading the
    # rendered page against the profile: it showed news_lookback_days=0 where .forward says 30.
    _gate_profile = "investor_profile.forward.md" if a.bootstrap else "investor_profile.backtest.md"
    _bs_handoff, _bs_start, _bs_stamp = None, None, {}
    if a.bootstrap:
        try:
            _bs_stamp = json.loads((ROOT / a.run / "provenance.json").read_text())
        except Exception:  # noqa: BLE001 -- an unstamped run still renders, it just says so
            _bs_stamp = {}
        try:
            import bootstrap_corpus as _bsh
            _bs_handoff = _bsh.HANDOFF
            _bs_start = _bsh.day_zero()
        except Exception:  # noqa: BLE001
            pass
    PROFILE_FILE = _gate_profile
    _lfm0_probe = _opt_gate.load_financial_model(str(ROOT / _gate_profile))
    # CBS SKIPS THE CANONICAL-CURATION CHECKS, exactly as FBS does and for the same reason: it
    # renders a DIFFERENT corpus under a DIFFERENT profile by design, not a drifted one. The
    # interpreter check above sits OUTSIDE this guard on purpose -- exempting the corpus check must
    # never silently exempt the numerical stack, which is how a whole session of FBS builds once ran
    # on the system python with nothing warning.
    _vfy = {}
    # CBS IS EXEMPT FROM THE CORPUS CHECK, NOT FROM THE KNOB CHECK. The exemption below was reasoned
    # about the CORPUS -- the bootstrap assembles a different one on purpose. It silently exempted the
    # PROFILE too, and those are not the same thing: cbs_v4 was curated under whatever
    # investor_profile.forward.md said at the time, so once a CURATION knob there moves, this page
    # reports settings the curation could never have run under. Live case: rebalance_period went
    # weekly -> monthly on 2026-08-26 and a rebuild would have printed "monthly" over 17 WEEKLY scans
    # with nothing objecting. Warn, do not hard-stop: an off-profile bootstrap build is a legitimate
    # thing to want, it just must never be silent.
    if a.bootstrap and _bs_stamp:
        _gk = _bs_stamp.get("knobs") or {}
        # A knob the profile no longer carries AT ALL is RELOCATED, not drifted: `specialty_allow`
        # and `mill_block` moved to retrieval_config.json this month, so the stamp remembers values
        # the profile has legitimately stopped owning. Reported separately -- calling that "drift"
        # buries the four real ones under two false positives, and a warning nobody can trust gets
        # ignored, which is the whole failure this guard exists to prevent.
        _cmp = [(k, _gk[k], _lfm0_probe.get(k)) for k in sorted(_canon.CURATION_KNOBS) if k in _gk]
        _moved = [k for k, _g, _w in _cmp if _w is None and _g is not None]
        _drift = [(k, g, _canon._norm(w)) for k, g, w in _cmp
                  if w is not None and _canon._norm(g) != _canon._norm(w)]
        if _moved:
            print(f"  note: {len(_moved)} knob(s) recorded by {a.run} are no longer profile knobs "
                  f"({', '.join(_moved)}) -- relocated, not drifted.", file=sys.stderr)
        if _drift:
            print(f"  !! {a.run} was CURATED under different settings than {_gate_profile} now has.",
                  file=sys.stderr)
            for _k, _got, _want in _drift:
                print(f"       {_k}: curated under {_got!r}, profile now says {_want!r}", file=sys.stderr)
            print("     These are CURATION knobs: the page's parameter table will describe a curation "
                  "that could not have produced this journal. RE-CURATE to make them agree.",
                  file=sys.stderr)
    if not a.bootstrap:
        _unclassified = _canon.check_partition_covers_profile()
        if _unclassified:
            _problems.append(f"profile knobs nobody has classified as curation-or-book: {_unclassified}. "
                             f"Add them to CURATION_KNOBS or BOOK_KNOBS in src/provenance.py.")
        if a.corpus != _canon.CANON_CORPUS:
            _problems.append(f"corpus is {a.corpus}, canonical is {_canon.CANON_CORPUS}")
        _lfm_gate = _opt_gate.load_financial_model(str(ROOT / _gate_profile))
        _vfy = _canon.verify(a.run, _lfm_gate, a.corpus)
        if not _vfy["ok"]:
            if _vfy["reason"] == "unstamped":
                _problems.append(_vfy["detail"] + "  Stamp it: python scripts/stamp_legacy_run.py " + a.run)
            for _k, _got, _want in _vfy["diffs"]:
                _problems.append(f"{_k}: curation ran under {_got!r}, profile now says {_want!r} "
                                 f"-> this curation would have to be RE-RUN, not just rebuilt")
    _canon.require_publishable(a.out, "CBS" if a.bootstrap else "CBT", _problems)
    if _vfy.get("unverifiable"):
        print(f"  note: {len(_vfy['unverifiable'])} curation knobs were never recorded by {a.run} "
              f"and cannot be checked ({', '.join(_vfy['unverifiable'][:4])}, ...)", file=sys.stderr)
    M, J, DEC, PICKS, BYURL, ARTS = load(run, corpus, bootstrap=a.bootstrap)
    try:
        from sweep_optimizer import FOCUS as _FOCUS_TICKERS
    except Exception:  # noqa: BLE001 -- emphasis is a nicety, never a build blocker
        _FOCUS_TICKERS = ()

    # ARTICLES PER BEAT over the whole corpus -- the denominator for beat efficiency (panel 7) and
    # the reason panel 5 can now list beats that produced NOTHING. `queries` is a stringified list.
    _beat_arts: dict = collections.Counter()
    for _a in ARTS:
        _q = _a.get("queries")
        if isinstance(_q, str):
            try:
                _q = ast.literal_eval(_q)
            except Exception:  # noqa: BLE001
                _q = []
        for _b in {_gkg.canon_beat(_x) for _x in (_q or [])}:   # canonical + de-duped per article
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
            for _b in {_gkg.canon_beat(_x) for _x in (_q or [])}:
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
    # DISTINCT tickers, vs `admitted` which counts ticker-scans: 45 of v15's 176 admissions are the
    # same ticker re-admitted later (TSM 11x). Without this the funnel's next bar looks like a cull.
    _distinct_admitted = len({t for d in scout for t in d.get("admitted", [])})
    # 0 = UNCAPPED. Without that guard every scan proposing anything counted as "hit the cap" and the
    # tile read "35/37 weeks hit the cap" in CRITICAL red for a knob that is switched off -- the same
    # `0 means uncapped, not zero` bug that made the Anthropic gather return nothing for 32 days.
    capbound = sum(1 for d in scout
                   if d.get("max_new_events") and len(d.get("proposed", [])) > d["max_new_events"])

    # ---- BUNDLE PAYOFF: does a bigger ticker-bundle actually make the scout act? -------------------
    # The design's central claim is that pairing a ticker's move-signal with its DRIVERS in one bundle
    # is what lets the scout open an event -- a move with no cause is correctly refused. That claim is
    # testable: bucket every bundle the scout was shown by size, and ask what fraction produced a
    # proposal. Rebuilt with the SAME orgs/agent code the curation ran, and joined to proposals via
    # each proposal's `company` field, which normalises to an org key ~100% of the time.
    bundle_buckets = {"labels": [], "groups": [], "hits": []}
    try:
        import orgs as _o
        import agent as _ag
        import gkg as _gk
        import pandas as _pdb
        _cl = [a for a in ARTS if not _gk._spam_title(a.get("title") or "")]
        _cn = _o.build_canon(_cl)
        # resolve the cadence HERE: _cad0 is not defined until ~140 lines below this point
        # Read the profile HERE rather than reuse _lfm0/_cad0: both are defined ~100 lines BELOW this
        # point, and depending on definition order in a long function is how this file has broken
        # before. Two extra file reads cost nothing.
        from optimizer import load_financial_model as _lfmb
        from util import resolve_cadence as _rc
        _win_days = int(_rc(_lfmb(str(ROOT / PROFILE_FILE))) or 30)
        _prop_by = collections.defaultdict(set)
        _tick_by: dict = {}          # bundle key -> tickers proposed from it, for gains-per-bundle
        for _d in DEC:
            if _d.get("kind") != "scout":
                continue
            for _p in (_d.get("proposed") or []):
                _k = _o.normalise(_p.get("company", ""))
                if _k:
                    _kk = _cn.get(_k, _k)
                    _prop_by[_d["context"]].add(_kk)
                    if _p.get("ticker"):
                        _tick_by.setdefault(_kk, set()).add(_p["ticker"])
        _BK = _orgs.SIZE_BUCKETS      # the ONE definition; see orgs.SIZE_BUCKETS
        _tot = collections.Counter(); _hit = collections.Counter()
        _bucket_ticks: dict = collections.defaultdict(set)
        _store: dict = {}
        for _r in M:
            _hi = str(_r["week"])[:10]
            _lo = (_pdb.Timestamp(_hi) - _pdb.Timedelta(days=_win_days)).date().isoformat()
            _w = [a for a in _cl if _lo < (a.get("published_date") or "")[:10] <= _hi]
            if not _w:
                continue
            _o.learn_ticker_evidence(_w, _cn, _store)          # chronological, as the run did
            _tm = _o.ticker_map_from(_store)
            _g = _ag.superlative_pool(_w)
            _P = _prop_by.get(_hi, set())
            for _b in _ag._scout_groups(_g, _w, _cn, 30, _tm):
                for _k, _v in _b:
                    if _k == _ag.UNGROUPED:
                        continue
                    _lab = next(l for lo, hi, l in _BK if lo <= len(_v) <= hi)
                    _tot[_lab] += 1
                    if _k in _P:
                        _hit[_lab] += 1
                        _bucket_ticks[_lab] |= _tick_by.get(_k, set())
        bundle_buckets = {"labels": [l for _, _, l in _BK],
                          "groups": [_tot[l] for _, _, l in _BK],
                          "hits": [_hit[l] for _, _, l in _BK],
                          # tickers proposed from each size class. Gains are attached below, once the
                          # book exists -- a ticker proposed from more than one size is counted in
                          # each, so these columns answer "what did proposals of this size earn",
                          # not "how does the book decompose". Stated because they will not sum to
                          # the book total.
                          "ticks": {l: sorted(_bucket_ticks[l]) for _, _, l in _BK},
                          # bundle NAME -> tickers proposed from it, for the per-name panel
                          "byname": {k: sorted(v) for k, v in _tick_by.items()}}
    except Exception as _e:  # noqa: BLE001 -- a diagnostic panel must never take the dashboard down
        print(f"  bundle-payoff panel unavailable ({type(_e).__name__}: {_e})", file=sys.stderr)

    # ---- attribution: join each pick's evidence urls back to the corpus ---------------------------
    src_c, lede_c, beat_c = collections.Counter(), collections.Counter(), collections.Counter()
    # Per-TICKER provenance mix, for the gain-by-provenance panel. Counting evidence articles says
    # where the curator's reading came from; it does not say where the MONEY came from, and those can
    # differ sharply if the archived arm supplies most of the reading but the picks that paid were
    # argued off live pages (or vice versa).
    tick_lede: dict = collections.defaultdict(collections.Counter)
    # Default here, not inside the portfolio-math block: that block is wrapped in a try and HAS
    # skipped before ("portfolio math skipped (NameError...)"), which would leave this undefined and
    # take the whole build down at payload time.
    _prov_gain: dict = {}
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
            # ONE definition, shared with lede.apply -- this copy used to know only lede/lede_live,
            # so every websearch article (neither field; its text is in `snippet`) was reported as
            # "headline only". 67 of 110 evidence articles on cbs_v5, with their P&L credited to a
            # bucket that means "the curator saw nothing but a headline".
            _prov = _lede.provenance(art)
            lede_c[_prov] += 1
            if p.get("ticker"):
                tick_lede[p["ticker"]][_prov] += 1
            for b in {_gkg.canon_beat(x) for x in (art.get("queries") or [])}:
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
    _lfm0 = _lfm(str(ROOT / PROFILE_FILE))
    # THE CADENCE WORD, derived ONCE from rebalance_period. Every "per week" on this page was
    # hardcoded, and BOTH arms have run MONTHLY for as long as the knob has existed -- so the tiles
    # were dividing by SCANS and calling them weeks on CBT too, not only on the re-based CBS.
    # Deriving it means the label cannot drift from the knob again, the same reason panel numbers
    # became positional. Defined at function level, never inside a try: an undefined name swallowed
    # by a broad except is how `book_seed` failed 600 lines downstream of its real cause.
    _PERIOD_WORD = {"weekly": "week", "biweekly": "fortnight",
                    "monthly": "month", "quarterly": "quarter"}
    _per = _PERIOD_WORD.get(str(_lfm0.get("rebalance_period", "weekly")).lower(), "scan")
    _pers = _per + ("s" if not _per.endswith("s") else "")

    # ---- portfolio math: only for SKILL measures, never as the headline ---------------------------
    # This page deliberately does not lead with an equity curve (2026-08-07 redirect). But three of
    # the pre-GKG dashboard's panels are about curator SKILL rather than performance, and they need
    # the book to exist: what share of theses made money standalone (precision), which events and
    # tickers carried the result (attribution), and the week-by-week reasoning behind each event
    # (the storyboard). Precision in particular is a HIT RATE, not a return -- breadth without
    # precision is noise, precision without breadth is luck, and only the pair means anything.
    import pandas as _pd
    _wcap = 0
    _held_per_week, _cull_bind, _cull_med, _held_med = [], 0, 0, 0
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
        # CBS CONTINUES CBT'S BOOK. The bootstrap exists to bridge the backtest to the forward, so
        # its portfolio starts where the backtest's recommendation left off rather than from cash:
        # the curve begins on the corpus's FIRST day holding what CBT recommended at its last scan
        # on or before it, and the curator then updates that one portfolio weekly through the
        # handoff to today. PWR does the same ("Day-0 portfolio = the CBT RECOMMENDED weights on the
        # nearest rebalance <= SINCE"), and without it the two pages describe unrelated books that
        # happen to share an axis.
        #
        # The CURATION is untouched -- all 17 scans, both eras, still drive every other panel. Only
        # the BOOK is re-based, which is a replay-time concern and costs no re-curation.
        # ONE definition on every path. `book_seed` was only ever ASSIGNED inside this branch, so a
        # later read of it on the CBT path raises NameError -- and the read sits inside a broad
        # `except`, so it surfaced as "portfolio math skipped" and then an UnboundLocalError three
        # hundred lines downstream rather than as the missing initialiser it was.
        book_seed = None
        if a.bootstrap and _bs_start:
            _seed_tk, _seed_at = [], None
            try:
                import csv as _csvs
                _cbt_rows = list(_csvs.DictReader((ROOT / _canon.CANON_RUN / "firehose_scans.csv").open()))
                _cbt_wks = sorted({r["week"] for r in _cbt_rows if (r.get("ticker") or "").strip()})
                _before = [w for w in _cbt_wks if w <= _bs_start]
                if _before:
                    _seed_at = _before[-1]
                    _seed_tk = sorted({r["ticker"].strip().upper() for r in _cbt_rows
                                       if r["week"] == _seed_at and (r.get("ticker") or "").strip()})
            except Exception as _e:  # noqa: BLE001 -- no CBT log -> fall back to starting from cash
                print(f"  CBS seed unavailable ({type(_e).__name__}: {_e})", file=sys.stderr)
            # SEED AT DAY ZERO, NOT AT THE HANDOFF. The bootstrap corpus deliberately begins ~3
            # months BEFORE the handoff so the curator is exercised on backtest-era news and
            # websearch news in one continuous run. Seeding at the handoff threw the first 13 scans
            # away from the book and left a 4-point curve; the point is to watch ONE portfolio be
            # updated weekly straight through the seam.
            _post = dict(_scans)
            if _seed_tk and _post:
                _first = min(_post)
                _have = {p["ticker"] for p in _post[_first]}
                for _t in _seed_tk:
                    if _t not in _have:
                        _post[_first].append({"ticker": _t, "thesis": f"carried from CBT {_seed_at}",
                                              "thesis_live": True, "catalyst_resolved": False,
                                              "evidence_urls": []})
                print(f"  CBS book seeded from CBT {_seed_at}: {len(_seed_tk)} tickers carried into "
                      f"{str(_first.date())}", flush=True)
                _scans = dict(sorted(_post.items()))
                book_seed = {"from": _seed_at, "n": len(_seed_tk), "tickers": _seed_tk}
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
        # THE FROZEN PRICE PANEL, shared with the sweep. Without it this page re-downloaded prices
        # on every build and was NOT reproducible: the same curation, the same code and the same
        # corpus rendered $272,336 on 2026-08-19 and $112,435 on 2026-08-21, because live adjusted
        # closes drift and small drifts cascade through the covariance and the min_trade_size
        # threshold. sweep_optimizer.py hit exactly this (919 of 6,300 cells disagreed, one by 36x)
        # and fixed it by freezing one panel per run; CBT was left fetching live, so CBT and SBT
        # could describe the same book with different numbers. Same file, so they cannot any more.
        # Re-fetching is an explicit choice -- delete data/<run>/panel.csv -- not the default.
        _panel = None
        _pf = run / "panel.csv"
        if _pf.exists():
            import pandas as _pdp
            _panel = _pdp.read_csv(_pf, index_col=0, parse_dates=True)
            print(f"  panel: reusing frozen {_pf} ({_panel.shape[1]} tickers)")
        else:
            print(f"  panel: no frozen {_pf}; fetching live -- this build is NOT reproducible",
                  file=sys.stderr)
        # THE WEIGHTS, NOT JUST THE TICKERS. Seeding the WATCHLIST let CBS's optimizer re-choose from
        # the inherited candidate set and it picked a different book: CBT held SOXX/SMH/MRVL at a
        # third each on 2026-05-01, CBS opened STM/SOXX/QTUM/AAPL -- one name in common, and AAPL
        # arrived from starter_watchlist, which a continuation book should not have at all. Handing
        # the opening ALLOCATION over makes "the bootstrap portfolio matches the backtest's on day
        # zero" true rather than approximate.
        # RECOMPUTED FROM THE RUN, NEVER PARSED OUT OF docs/cbt.html (changed 2026-09-02).
        # This used to read the opening weights out of the RENDERED CBT PAGE, which made the
        # bootstrap's opening book a function of WHEN CBT was last built. Build CBS before rebuilding
        # CBT after a sizing change and it silently opens on the old config's allocation, with nothing
        # anywhere saying so -- the same "a page describing inputs it was not built from" failure this
        # file's provenance module exists to kill, and unreachable by any fingerprint we could add,
        # because the number would be internally consistent and simply wrong.
        #
        # NOT FROZEN AT CURATION TIME EITHER, which is the other obvious fix and is worse: the opening
        # allocation is CBT's weights on the seed date, so it is a function of max_watchlist,
        # concentration_cap, optimizer_lookback_days and risk_aversion -- BOOK knobs. Stamping it into
        # the run would make every sizing tweak demand a re-curation of the bootstrap, which is
        # precisely the misfiling provenance.py warns against.
        #
        # So: replay CANON_RUN under the BACKTEST profile (the one CBT's own page is built under) and
        # read the allocation at the seed date. Measured 0.7s off the frozen panel, and it reproduces
        # the page's numbers exactly. No ordering, no artifact, nothing to go stale.
        _seed_w = None
        if a.bootstrap and _bs_start:
            try:
                import bisect as _bis
                _cbt_fm = _lfm(str(ROOT / "investor_profile.backtest.md"))
                _cbt_scans: dict = collections.defaultdict(list)
                for _r in _cbt_rows:
                    _tk = (_r.get("ticker") or "").strip().upper()
                    if not _tk:
                        continue
                    _ts = _pd.Timestamp(str(_r["week"]) + " 16:30", tz="America/New_York")
                    _cbt_scans[_ts].append({"ticker": _tk, "thesis": (_r.get("thesis") or ""),
                                            "thesis_live": str(_r.get("thesis_live")).lower() == "true",
                                            "catalyst_resolved":
                                                str(_r.get("catalyst_resolved")).lower() == "true",
                                            "evidence_urls": []})
                _cbt_pf = ROOT / _canon.CANON_RUN / "panel.csv"
                _cbt_bt = _fh.backtest(dict(sorted(_cbt_scans.items())), _cbt_fm,
                                       capital=float(_cbt_fm.get("initial_investment_usd", 50_000)),
                                       daily=True,
                                       panel=(_pd.read_csv(_cbt_pf, index_col=0, parse_dates=True)
                                              if _cbt_pf.exists() else None),
                                       freeze_panel=str(_cbt_pf))
                _cdates = (_cbt_bt.get("daily") or {}).get("dates") or []
                _calloc = (_cbt_bt.get("daily") or {}).get("alloc") or {}
                if _cdates:
                    _ix = min(_bis.bisect_left(_cdates, _bs_start), len(_cdates) - 1)
                    _seed_w = {t: float(wv[_ix]) for t, wv in _calloc.items()
                               if _ix < len(wv) and wv[_ix] > 1e-6}
                if _seed_w:
                    print(f"  CBS opens with CBT's allocation at {_cdates[_ix]} "
                          f"(replayed from {_canon.CANON_RUN}): "
                          + ", ".join(f"{t} {100*v:.1f}%" for t, v in sorted(_seed_w.items(),
                                                                            key=lambda kv: -kv[1])),
                          flush=True)
            except Exception as _e:  # noqa: BLE001 -- no replay -> fall back to ticker-only seeding
                print(f"  CBS weight seed unavailable ({type(_e).__name__}: {_e})", file=sys.stderr)
                _seed_w = None
        # SAY WHAT IT OPENED ON, on the page. The seed used to be implicit -- the curve simply started
        # somewhere and the reader had to trust it. Recording the run, the date and the weights makes
        # the page self-describing, which is the part of "fingerprint it" worth keeping once the
        # staleness itself is designed out.
        if book_seed is not None:
            book_seed["run"] = _canon.CANON_RUN
            book_seed["weights"] = {t: round(float(v), 6) for t, v in
                                    sorted((_seed_w or {}).items(), key=lambda kv: -kv[1])}
        _bt = _fh.backtest(_scans, _lfm0, capital=_cap, daily=True, picker=_pick, panel=_panel,
                           seed_holdings=_seed_w, freeze_panel=_pf)
        # Keep only PRICED theses. A ticker with no price history scores ret=None, and comparing
        # that to 0 raised TypeError once max_watchlist widened the book enough to admit one
        # (2026-08-12). Precision over unpriced theses is meaningless, so they are excluded rather
        # than silently counted as losses -- the "no priced theses" fallback already assumed this.
        prec = [x for x in _bt.get("agent_precision", []) if isinstance(x.get("ret"), (int, float))]
        # how hard the max_watchlist cull actually bites: live names vs what may hold capital
        _bseed0 = sorted((book_seed or {}).get("tickers") or [])
        # fm= IS REQUIRED. Without it _watch_clocks falls back to the MODULE defaults (2, 4) while
        # the book ran on the profile's (2, 8), so every live-watchlist count on this page described
        # a shorter-memory portfolio than the one being plotted: median 99 against the book's 126,
        # a 27% understatement, in panel 6's counts, the cull-bind figure and panel 7's funnel.
        # firehose.backtest has always passed it; this call was the odd one out.
        _w = _fh._stateful_watch(_scans, seed=(_bseed0 if (a.bootstrap and _bseed0) else
                                               [x.upper() for x in (_lfm0.get("starter_watchlist") or [])]),
                                 fm=_lfm0)
        # SNAPSHOT IT HERE, under a name nothing else uses. `_w` is rebound twice further down this
        # 2,800-line function (line ~1122, to a list of articles), and because the later binding can
        # be an EMPTY list, `(_w or {})` degraded to {} with no exception -- so the curation log's
        # already-live filter silently did nothing and counted 152 rejections instead of 109.
        _live_by_wk = {str(k.date()): set(v) for k, v in _w.items()}
        _live_n = [len(v) for v in _w.values()]
        # WHAT ACTUALLY HELD CAPITAL, read off the allocation -- not min(live, cap), which is what
        # this was until 2026-08-28 and which is not a measurement at all. The cap binds in nearly
        # every period, so min(live, cap) equalled the cap almost everywhere and the green line was
        # simply redrawing the dashed cap line under a label that claimed it was funding. Measured
        # on the canonical book the two disagreed in 36 of 36 periods: the old line said 6 every
        # week, the real count has a median of 2. The panel's whole point is the gap between what
        # the curator may fund and what the optimizer does fund, and the bug hid exactly that gap.
        # Anchors are excluded because they sit OUTSIDE the cap (always_include), and >1% is the
        # same funded threshold sweep_optimizer and panel 5 use, so the three agree.
        _anch0 = set(_fh.anchor_tickers(_lfm0))
        _dd0 = _bt.get("daily") or {}
        _adates, _aal = _dd0.get("dates") or [], _dd0.get("alloc") or {}
        # None, NOT 0, for a scan that predates the book's first day -- the first rebalance does on
        # both arms. There is no allocation to count there, and drawing absent data as a zero is the
        # same false assertion the decisions.jsonl note above objects to. The JS maps null to a gap.
        def _funded_on(_wk):
            _i = next((i for i in range(len(_adates) - 1, -1, -1) if _adates[i] <= _wk), -1)
            if _i < 0:
                return None
            return sum(1 for _t, _sv in _aal.items()
                       if _t not in _anch0 and _i < len(_sv) and _sv[_i] > 0.01)
        _held_per_week = [_funded_on(_wk) for _wk in weeks]
        _cull_bind = sum(1 for n in _live_n for _ in [0] if _wcap and n > _wcap)
        _cull_med = sorted(_live_n)[len(_live_n) // 2] if _live_n else 0
        _hpw0 = [n for n in _held_per_week if n is not None]
        _held_med = sorted(_hpw0)[len(_hpw0) // 2] if _hpw0 else 0

        # ---- THE CULL FUNNEL -------------------------------------------------------------------
        # Panel 14's funnel stops at the journal: it ends with "picks logged" and says nothing about
        # what happens to those picks afterwards. Everything that decides where capital actually
        # goes is DOWNSTREAM of that -- the sticky watch, the max_watchlist cull, and the optimizer's
        # own floor -- and none of it was drawn anywhere on this page.
        # The freshness split duplicates the tier rule in firehose._ranked_cull. Kept in step by
        # reading the SAME two knobs off the profile and applying the same expression; if that rule
        # gains a tier, this needs the matching arm. The alternative (returning the split from
        # backtest) would thread a chart's needs through the engine, which is worse.
        _fslots = int(_lfm0.get("cull_fresh_slots", 3) or 0)
        _fscans = int(_lfm0.get("cull_fresh_scans", 2) or 0)
        _fk: dict = {}
        _fresh_n, _kept_n = [], []
        for _ki, _ai in enumerate(sorted(_scans)):
            for _p in _scans[_ai]:
                _fk.setdefault(_p["ticker"], _ki)
            _evi = _w.get(_ai) or []
            _kept_n.append(min(len(_evi), _wcap) if _wcap else len(_evi))
            if _wcap and len(_evi) > _wcap:
                _fc = [t for t in _evi if t in _fk and (_ki - _fk[t]) < _fscans]
                _fresh_n.append(min(len(_fc), _fslots, _wcap))
            else:
                _fresh_n.append(0)

        def _med(xs):
            xs = [x for x in xs if x is not None]
            return sorted(xs)[len(xs) // 2] if xs else 0
        _cf_live, _cf_kept = _med(_live_n), _med(_kept_n)
        _cf_fresh = _med(_fresh_n)
        _cf_trend = max(0, _cf_kept - _cf_fresh)
        _cf_fund = _med(_held_per_week)
        # HOW MUCH OF THE BOOK those funded names actually hold. Without this the funnel's last drop
        # reads as idle capital, which is the opposite of the truth: the floor forbids dust and the
        # cap allows 60% in one name, so a FULLY INVESTED book is two or three names.
        _cf_dep = []
        for _wk in weeks:
            _i = next((i for i in range(len(_adates) - 1, -1, -1) if _adates[i] <= _wk), -1)
            if _i < 0:
                continue
            _cf_dep.append(100.0 * sum(_sv[_i] for _t, _sv in _aal.items()
                                       if _t not in _anch0 and _i < len(_sv) and _sv[_i] > 0.01))
        _cf_dep_med = _med([round(x) for x in _cf_dep])
        # Span of the breadth panel's series, for its lead and to justify its log axis.
        _bser = ([r['events_live'] for r in M] + [r['vehicles_live'] for r in M]
                 + [r['distinct_catalysts'] for r in M])
        _bmin, _bmax = (min(_bser), max(_bser)) if _bser else (0, 0)
        book = {"final": _bt.get("final"), "spy": _bt.get("spy_final"), "weeks": _bt.get("weeks")}
        _pg: dict = collections.Counter()
        for _tk, _mix in tick_lede.items():
            _g = float((_d0 := (_bt.get("daily") or {}).get("gain") or {}).get(_tk) or 0)
            _tot = sum(_mix.values())
            if not _g or not _tot:
                continue
            for _p, _n in _mix.items():
                _pg[_p] += _g * _n / _tot
        _prov_gain = dict(_pg)
        # GAINS PER BUNDLE SIZE. The PANEL was deleted 2026-08-23 -- it attributed only -1% of the
        # book because the bundle->proposal join is reconstructed post-hoc by replaying
        # _scout_groups and matching on company name, and that match failed for 93% of realised
        # P&L (36 funded tickers, $227,734). The key is still computed because it is cheap and
        # because the panel can return correctly once agent.py logs each proposal's BUNDLE SIZE
        # at scout time -- see TODO.md. Attach the book's per-ticker P&L to the size class that proposed
        # each ticker. Done here because it needs the book, which does not exist where the buckets
        # are built.
        _gg = (_bt.get("daily") or {}).get("gain") or {}
        bundle_buckets["gain"] = [round(sum(float(_gg.get(t) or 0)
                                            for t in bundle_buckets.get("ticks", {}).get(l, [])), 2)
                                  for l in bundle_buckets.get("labels", [])]
        # PER BUNDLE NAME. Same attribution as the size buckets -- a bundle is credited with the P&L
        # of every ticker proposed out of it -- but named, so it answers WHICH bundles paid rather
        # than which sizes. Beat bundles carry the \x00beat: sentinel; strip it and mark them.
        _bn = []
        for _k, _ts in (bundle_buckets.get("byname") or {}).items():
            _v = sum(float(_gg.get(t) or 0) for t in _ts)
            if _v:
                _bn.append((_k, round(_v, 2), sorted(_ts)[:8]))
        _bn.sort(key=lambda t: -abs(t[1]))
        _bn = sorted(_bn[:30], key=lambda t: t[1])
        # the unlisted tail, stated rather than drawn -- rolled bars here would be $86k and -$90k
        # against a largest named bar of $50k, i.e. the same axis-squashing that was just removed
        # from plot 5.
        _shown = {t[0] for t in _bn}
        _restv = []
        for _k2, _ts2 in (bundle_buckets.get("byname") or {}).items():
            if _k2 in _shown:
                continue
            _v2 = sum(float(_gg.get(t) or 0) for t in _ts2)
            if _v2:
                _restv.append(_v2)
        bundle_buckets["rest_win_n"] = sum(1 for v in _restv if v > 0)
        bundle_buckets["rest_win"] = round(sum(v for v in _restv if v > 0), 2)
        bundle_buckets["rest_los_n"] = sum(1 for v in _restv if v <= 0)
        bundle_buckets["rest_los"] = round(abs(sum(v for v in _restv if v <= 0)), 2)
        bundle_buckets["names"] = [t[0] for t in _bn]
        bundle_buckets["ngain"] = [t[1] for t in _bn]
        bundle_buckets["nticks"] = [", ".join(t[2]) for t in _bn]
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
        # inception and never touched again. THE SAME BASKET ON BOTH ARMS: it is the control, not
        # the inception holding, so it does not follow seed_holdings -- CBT and CBS are only
        # comparable if they are measured against the same boring basket. The honest control for "did curating add anything, or
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
                        # canonicalise: corpus tags carry whatever the beat was CALLED when the
                        # article was retrieved, so after a rename this page labelled the same beat
                        # differently from FBT -- two published pages disagreeing about one corpus.
                        _tb[_p["ticker"]][_gkg.canon_beat(_bq)] += 1
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
        # --- rebalance moves (see the "moves" note in the payload below) ---
        _mv_px = None
        if _panel is not None:
            _mv_px = _panel.copy()
            _mv_px.index = _mv_px.index.strftime("%Y-%m-%d")   # dates[] are strings
        _mv_dates = _d.get("dates", [])
        _mv_val = [float(x) for x in _d.get("value", [])]
        _mv_T = sorted(_alloc)
        def _vec(i):
            return {t: _alloc[t][i] for t in _mv_T if i < len(_alloc[t]) and _alloc[t][i] > 1e-9}
        def _vecd(i):                     # same, in DOLLARS
            return {t: _alloc[t][i] * _mv_val[i] for t in _mv_T
                    if i < len(_alloc[t]) and _alloc[t][i] > 1e-9}
        # --- sankey flows (see the "flow" note in the payload) ---
        _fl_nodes, _fl_links, _fl_idx = [], [], {}
        def _node(_col, _tk, _usd, _date, _ret=None, _pool=False):
            _k = ("~pool", _col) if _pool else (_col, _tk)
            if _k not in _fl_idx:
                _fl_idx[_k] = len(_fl_nodes)
                _fl_nodes.append({"t": _tk, "c": _col, "d": _date, "usd": round(_usd),
                                  "p": 1 if _pool else 0,
                                  # fractional return over the period this position was HELD --
                                  # what colours the band. None for the final column, which has no
                                  # following rebalance to measure against.
                                  "r": (None if _ret is None else round(100 * _ret, 1))})
            elif _ret is not None and _fl_nodes[_fl_idx[_k]].get("r") is None:
                _fl_nodes[_fl_idx[_k]]["r"] = round(100 * _ret, 1)
            return _fl_idx[_k]
        _rbf = [0]
        for _i in range(1, len(_mv_dates)):
            _a, _c = _vecd(_i - 1), _vecd(_i)
            if set(_a) != set(_c) or sum(abs(_c.get(t, 0) - _a.get(t, 0))
                                         for t in set(_a) | set(_c)) / max(_mv_val[_i], 1) > 0.10:
                _rbf.append(_i)
        for _k in range(len(_rbf) - 1):
            _i, _j = _rbf[_k], _rbf[_k + 1]
            _held, _endv, _nxt = _vecd(_i), _vecd(_j - 1), _vecd(_j)
            _pret = {t: (_endv[t] / _held[t] - 1.0) for t in _held
                     if _held.get(t) and t in _endv}      # this period's return, per position
            _src = {t: _endv.get(t, 0.0) for t in _held}
            _dst = dict(_nxt)
            if sum(_src.values()) <= 0 or sum(_dst.values()) <= 0:
                continue
            for t in list(_src):                       # 1. carry continuations straight through
                if t in _dst:
                    _m = min(_src[t], _dst[t])
                    if _m > 0:
                        _fl_links.append({"s": _node(_k, t, _held.get(t, 0), _mv_dates[_i], _pret.get(t)),
                                          "t": _node(_k + 1, t, _nxt.get(t, 0), _mv_dates[_j]),
                                          "v": round(_m)})
                        _src[t] -= _m
                        _dst[t] -= _m
            # 2. THE RESIDUAL GOES THROUGH A POOL, not straight from an exiting name to an
            # entering one. NOTHING IN THE DATA SAYS WHOSE DOLLARS BECAME WHOSE: at a rebalance the
            # book is liquidated into one pot and the optimizer allocates out of it. The proportional
            # split this replaces INVENTED that pairing -- 1,163 of 1,228 links (95%) and 76% of the
            # flow were an assumption drawn as fact, and at 6-8 names a side it fabricated up to 64
            # lines per rebalance, which is the unreadable braid in the late columns.
            # exits -> pool -> entries is ~16 lines and claims only what is known. CONTINUATIONS
            # (step 1) bypass the pool entirely, because a name held through a rebalance really did
            # continue -- that is the one attribution the data supports.
            # It also retires the orphan-repair hack: every new position now has an inbound link by
            # construction, so no node can be stranded with none and land in Plotly's layer 0.
            _out = {t_: v for t_, v in _src.items() if v > 0}
            _in = {t_: v for t_, v in _dst.items() if v > 0}
            if _out and _in:
                _pk = _node(_k, "", sum(_out.values()), _mv_dates[_j], None, _pool=True)
                for st, sv in _out.items():
                    _fl_links.append({"s": _node(_k, st, _held.get(st, 0), _mv_dates[_i], _pret.get(st)),
                                      "t": _pk, "v": max(1, round(sv))})
                for dt, dv in _in.items():
                    _fl_links.append({"s": _pk,
                                      "t": _node(_k + 1, dt, _nxt.get(dt, 0), _mv_dates[_j]),
                                      "v": max(1, round(dv))})
        _flow = {"nodes": _fl_nodes, "links": _fl_links, "cols": len(_rbf)}
        book.update({
            "wcomp": {"watch": _wspans, "funded": _fspans, "beats": _top + ["other", "no beat"]},
            # BOOTSTRAP ONLY: the date the news source changes under the curator's feet. Absent on
            # CBT, whose corpus has no seam, so the chart simply draws no line there.
            "handoff": (_bs_handoff if a.bootstrap else None),
            # sorted(): iterating a SET makes the emitted key order depend on string hashing, so
            # two builds of the same page produced byte-different JSON for identical data. Harmless
            # to the charts, but it defeats "did anything actually change?" on a rebuild -- which is
            # the check that just caught the live-yfinance drift above.
            "tickerbeat": {tk: _beat_of(tk) for tk in
                           sorted({p["ticker"] for p in PICKS} | set(_gain) | set(_dom))},
            # ARTICLES PER BEAT across the whole corpus -- the denominator for beat efficiency, and
            # the reason gainbeat can now list beats that produced NOTHING. Counting only funded beats
            # (the old behaviour) showed 9 of 46 and hid the expensive failures entirely.
            "beatarts": _beat_arts,
            "beatgated": _beat_gated,
            "gainbeat": {b: round(v, 2) for b, v in sorted(
                {bb: sum(g for tk, g in _gain.items() if _beat_of(tk) == bb)
                 for bb in sorted({_beat_of(tk) for tk in _gain} | set(_beat_arts))}.items(),
                key=lambda kv: kv[1])},   # sorted() input: ties broke arbitrarily, see above
            "cullfunnel": {"labels": ["live watchlist", "survive the cull",
                                      "\u21b3 freshness tier", "\u21b3 trend tier",
                                      # plain ">", not &gt;: these are Plotly tick labels, not HTML
                                      "funded (weight > 1%)"],
                           "values": [_cf_live, _cf_kept, _cf_fresh, _cf_trend, _cf_fund],
                           "tier": [0, 0, 1, 1, 0],
                           "cap": _wcap, "slots": _fslots, "scans": _fscans,
                           "floor": _lfm0.get("min_trade_size")},
            "anchors": _fh.anchor_tickers(_lfm0),
            # THE STANDING RECOMMENDATION (panels 28-29). Everything else on this page is history;
            # this is the one forward-looking object -- the weights the optimizer set at the LAST
            # curation, which is what someone acting on the book would hold until the next one.
            # `unfunded` is the rest of that scan's watchlist: names the curator judged worth
            # watching that the sizing math declined to fund. Showing them as zero bars is the
            # point -- the gap between "the curator likes it" and "the book owns it" is the single
            # most misread thing on this page, and panel 6 only shows it as a count.
            # NOT a trade list and NOT a broker instruction: no holdings file is read here (a paper
            # book assumes its own recommendation was followed), so there is nothing to diff against.
            "rec": (lambda _lat: {
                "date": _lat.get("date"),
                "cadence_days": _cad0,
                "next_due": (str(_dt.date.fromisoformat(_lat["date"]) +
                                 _dt.timedelta(days=_cad0)) if _lat.get("date") else None),
                "funded": [{"t": t, "w": w, "b": _beat_of(t)}
                           for t, w in (_lat.get("weights") or {}).items() if w > 0.0005],
                "unfunded": [{"t": t, "b": _beat_of(t)} for t in sorted(
                    set(_lat.get("watchlist") or [])
                    - {t for t, w in (_lat.get("weights") or {}).items() if w > 0.0005}
                    - set(_fh.anchor_tickers(_lfm0)))],
            })(_bt.get("latest") or {}),
            "rebal": [str(x.date()) for x in sorted(_scans)],
            "bh": [None if x is None else float(x) for x in (_d.get("bh") or [])],
            "bh_tickers": _d.get("bh_tickers") or [],
            "dates": _d.get("dates", []), "value": [float(x) for x in _d.get("value", [])],
            "spyser": [float(x) for x in _d.get("spy", [])],
            "gain": {k: float(v or 0) for k, v in sorted(_gain.items(), key=lambda kv: kv[1])},
            "evgain": dict(sorted(_evgain.items(), key=lambda kv: kv[1])),
            "alloc": {k: [float(x) for x in v] for k, v in _alloc.items()},
            # ---- SANKEY: dollars handed from ticker to ticker at every rebalance ----
            # One column per rebalance, one node per position held, ribbon = dollars.
            # A ribbon leaves a column FATTER than it entered when the ticker rose -- the width
            # change IS the position's return, which is why no separate gain/loss node is drawn.
            # CONTINUATIONS ARE MATCHED FIRST (min of held and wanted), then the residual is split
            # proportionally. Without that, a position that simply persists renders as a self-link
            # plus a web of crossings, and the diagram reads as churn that never happened.
            # THE TICKER-TO-TICKER ATTRIBUTION IS A MODEL, NOT A TRACE: the book sells into a pool
            # and buys out of it, and nothing records which dollar went where. Proportional split is
            # the usual convention; the caption says so.
            "flow": _flow,
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
        # PREFER THE FROZEN PANEL FOR THE MODAL TOO. Both price lookups below called
        # fetch_panel(use_cache=False), which hits yfinance LIVE on every build -- so two builds of the
        # same page disagreed in the last decimal, and every build needed the network. That is the drift
        # the frozen panel exists to prevent (CLAUDE.md: "Re-fetching is an explicit choice ... never the
        # default"; live drift once made two sweeps of one journal disagree on 919 of 6,300 cells).
        # The book itself was never affected -- it has always priced off the frozen panel -- but the
        # popup history was, and CBT's frozen panel covers its ENTIRE modal range (2023-03-14 against a
        # need of 2023-08-15). Only CBS reaches back past its own corpus, because its two-year lookback
        # predates a four-month book, so only that case still fetches.
        # ALL-OR-NOTHING on purpose: stitching a frozen slice to a fetched one would join two different
        # adjusted-close vintages into one series, which is a subtler version of the bug being fixed.
        _frozen_px = locals().get("_panel")

        def _px_panel(tks, start, end):
            tks = list(tks)
            if _frozen_px is not None and len(_frozen_px.index) and tks:
                _i0 = _frozen_px.index[0]
                if getattr(_i0, "tzinfo", None) is not None:
                    _i0 = _i0.tz_localize(None)
                if _pd.Timestamp(start) >= _i0 and all(t in _frozen_px.columns for t in tks):
                    print(f"  px: frozen panel covers {len(tks)} tickers from {start} (no live fetch)")
                    return _frozen_px[tks].loc[_pd.Timestamp(start):_pd.Timestamp(end)].copy()
            print(f"  px: LIVE fetch, {len(tks)} tickers from {start} -- the frozen panel does not "
                  f"cover it, so this part of the page is not bit-reproducible", file=sys.stderr)
            return _score.fetch_panel(tks, start, end, use_cache=False)

        _spans = collections.defaultdict(list)
        for _s in ((book.get("wcomp") or {}).get("watch") or []):
            _spans[_s["t"]].append((_s["s"], _s["e"]))
        _anc = set(book.get("anchors") or [])
        _cand = [tk for tk in sorted(_spans) if tk not in _anc]
        if _cand and book.get("dates"):
            _cp = _px_panel(_cand, book["dates"][0], book["dates"][-1])
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
        # PLUS the standing recommendation. `gain` is names that have HELD capital; a ticker
        # first funded at the last curation has no realised P&L yet, so it was absent here and
        # panel 28 would have drawn a bar with no price behind it and no row in the calculator.
        _rec0 = (book.get("rec") or {})
        _pt = sorted(set(book.get("gain") or {})
                     | {r["t"] for r in (_rec0.get("funded") or [])}
                     | {r["t"] for r in (_rec0.get("unfunded") or [])})
        if _pt and book.get("dates"):
            # TWO YEARS OF CONTEXT, not just the book's own window. On CBS the book is four months
            # long, so a ticker's popup showed four months of price and no way to see whether the
            # move we captured was a break from its own history or the middle of a trend already
            # running. min() so CBT, whose book already spans three years, is unchanged.
            _end = book["dates"][-1]
            _2y = (_dt.date.fromisoformat(str(_end)[:10]) - _dt.timedelta(days=730)).isoformat()
            _start = min(str(book["dates"][0])[:10], _2y)
            _pp = _px_panel(_pt, _start, _end)
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
    _cost_note = ""
    cf = ROOT / "data" / "llm_costs.csv"
    if cf.exists():
        import csv as _csv, datetime as _dtc
        rows = list(_csv.DictReader(cf.open()))
        # THE RUN'S OWN WINDOW, from the run's own files. This used to be `rows[-max(len(scout)*6,
        # 200):]` -- the last N rows of the GLOBAL ledger as a proxy for "logged while it ran". That
        # silently truncates any run that made more calls than the window: CBS made 1,427 and the
        # tile summed 200 of them, reporting $0.51 for a run that cost $16.99, a 33x understatement
        # on a published page. The archive files are written one per scan as the run executes, so
        # their mtimes ARE the window -- no schema change, no tagging, and it cannot silently
        # truncate.
        _arch = sorted((run / "archive").glob("*.json"), key=lambda f: f.stat().st_mtime)
        if _arch:
            _t0 = _dtc.datetime.fromtimestamp(_arch[0].stat().st_mtime, _dtc.timezone.utc)
            _t1 = _dtc.datetime.fromtimestamp(_arch[-1].stat().st_mtime, _dtc.timezone.utc)
            _lo = (_t0 - _dtc.timedelta(minutes=10)).isoformat()   # the first scan's calls precede its file
            _hi = (_t1 + _dtc.timedelta(minutes=5)).isoformat()
            _in = [r for r in rows if _lo <= (r.get("ts") or "") <= _hi]
            cost = sum(float(r.get("cost_usd") or 0) for r in _in)
            _cost_note = f" · {len(_in):,} calls in the run window"
        else:
            cost = sum(float(r.get("cost_usd") or 0) for r in rows[-max(len(scout) * 6, 200):])
            _cost_note = " · window unknown (no archive files); last-N-rows estimate"

    def st(v, good, warn):
        return "good" if v >= good else ("warning" if v >= warn else "critical")

    med_live = statistics.median([r["events_live"] for r in M]) if M else 0
    med_cat = statistics.median([r["distinct_catalysts"] for r in M]) if M else 0
    # READ ABOVE THE TILES, not beside the parameters table where it used to live: the Beats card
    # needs it and Python has no forward references inside a function body.
    cfgp = json.loads((ROOT / "retrieval_config.json").read_text())
    tiles = "".join(tile(v) for v in [
        # CORPUS SIZE AND BEAT COUNT AS CARDS. They used to ride along inside the parameters
        # table's corpus row ("path · N articles · X+Y beats"), where the path -- the one thing
        # that identifies WHICH pool this page was built from -- was the least visible part of
        # the string. The row is now the path alone; the magnitudes belong up here.
        dict(label="Corpus articles", value=f"{len(ARTS):,}",
             sub=("bootstrap (gkg+wayback | websearch)" if a.bootstrap else f"{a.corpus}"),
             status="good",
             why="Articles in the pool the curator read. The sub-line is the local path it "
                 "came from -- this page derives every corpus metric from that one directory."),
        dict(label="Beats", value=f"{len(cfgp['gem_beats'])}+{len(cfgp['coverage_beats'])}",
             sub="early-framing + sector-coverage", status="good",
             why="Search beats behind the corpus: gem beats hunt early framing, coverage beats "
                 "sweep a sector broadly. Defined in retrieval_config.json."),
        dict(label=f"{_pers.capitalize()} curated", value=f"{len(M)}", sub=f"{weeks[0]} → {weeks[-1]}" if weeks else "",
             status="good", why="Rebalances the curator ran across the backtest window."),
        dict(label="Events opened", value=f"{J.get('nid', 0)}", sub=f"{len(live_now)} still live at the end",
             status=st(J.get("nid", 0), 30, 12),
             why="Distinct catalysts the curator opened an event on over the whole run."),
        dict(label=f"Events live / {_per}", value=f"{med_live:.0f}",
             sub=f"cap max_watchlist = {_wcap or 'uncapped'}",
             status=st(med_live, 4, 2),
             why="Typical number of events holding capital at once — the breadth the optimizer can spread across."),
        dict(label="Distinct catalysts", value=f"{med_cat:.0f}", sub=f"median per {_per}",
             status=st(med_cat, 4, 2),
             why="Separate stories behind those events. Several events on one theme is not diversity."),
        dict(label="Vehicles named", value=f"{len(all_veh)}", sub=f"across {J.get('nid',0)} events",
             status=st(len(all_veh), 40, 15),
             why="Distinct tickers the curator named. Peer baskets mean one event can carry several."),
        dict(label="Scout inflow", value=f"{proposed}",
             sub=(f"{admitted} admitted · {capbound}/{len(scout)} curations hit the admission cap"
                  if any(d.get("max_new_events") for d in scout)
                  else f"{admitted} admitted · admission cap OFF (max_new_events=0)"),
             status="critical" if capbound > len(scout) * 0.5 else "good",
             why="Candidates the scout proposed vs admitted. The admission cap is OFF, so the gap is the "
                   "already-resolved block and the ticker guard — breadth is set by the curator."),
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
        dict(label="LLM cost", value=f"${cost:.2f}",
             sub=f"${cost/max(len(M),1):.3f} per {_per}{_cost_note}",
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
            # UNITS CHANGE MID-FUNNEL, so say so. "admitted" counts ticker-admissions ACROSS SCANS
            # (176 on v15) while "events opened" counts events (80) -- drawn adjacent that looked like a
            # 2.2x cull, but 45 of the 176 are the SAME ticker re-admitted in a later scan (TSM 11x,
            # NVDA 6x). Inserting the distinct-ticker count makes the unit change visible instead of
            # reading as a rejection that never happened.
            "labels": ["articles read", "past the discovery gate", "candidates proposed",
                       "admissions (ticker-curations)", "distinct tickers admitted", "events opened",
                       "vehicles named", "picks logged"],
            "values": [sum(r["articles_read"] for r in M), _gated_total, proposed, admitted,
                       _distinct_admitted, J.get("nid", 0), len(all_veh), len(PICKS)]},
        "breadth": {"w": weeks, "cap": _wcap, "held": _held_per_week,
                    "events": [r["events_live"] for r in M],
                    "vehicles": [r["vehicles_live"] for r in M],
                    "catalysts": [r["distinct_catalysts"] for r in M],
                    # DISTINCT TICKERS CARRYING A LIVE THESIS per scan. NOT `picks_live`, which is
                    # len(live) -- the per-EVENT pick rows, so a ticker named by three events counts
                    # three times (measured 1.13x inflation; ATYR, RAPP, MU and ALB each appear 3x on
                    # the last scan). And NOT "the watchlist" in the sizing sense: `max_watchlist`
                    # caps tickers that may HOLD CAPITAL, which is 3-4 here. Four different numbers
                    # get called the watchlist on this page, so this one says exactly what it counts.
                    "picks": [len({(r.get("ticker") or "").strip().upper() for r in PICKS
                                   if r.get("week") == w
                                   and str(r.get("thesis_live", "")).lower() in ("true", "1")})
                              for w in weeks],
                    # Events that opened and terminated on the SAME scan, by scan. Dropped from the
                    # timeline (they draw as hairlines) so they are counted here instead -- the point
                    # of panel 4 is what the cap is doing, and a one-scan life is the cap's signature.
                    # LIVE, DERIVED FROM THE JOURNAL, for panel 4 only. `events` above comes from the
                    # run's own events_live, which counts an event live only while it is being CARRIED
                    # and so EXCLUDES the scan it terminates on. The unfunded series below is derived
                    # from journal spans, which include that closing scan -- mixing the two put
                    # `unfunded` ABOVE `events` on 26 of 37 scans and implied a NEGATIVE funded count.
                    # Panel 4 therefore takes both curves from this one definition; `events` stays as
                    # it was for the breadth panel, which compares it against other run-derived series.
                    "live_j": [sum(1 for _k, _v in ev.items()
                                   if (_v.get("entries") or [])
                                   and _v["entries"][0].get("date", "") <= r["week"]
                                   <= _v["entries"][-1].get("date", ""))
                               for r in M],
                    # LIVE BUT NEVER YET FUNDED, per scan. A subset of `events` on the same axis, so
                    # the GAP between the two curves is what the optimizer actually backed. This is the
                    # allocation bottleneck as a time series: the curator can be tracking a dozen live
                    # theses while the book holds four, and nothing else on the page shows the two
                    # side by side. Counted from event birth up to that scan -- an event that gets
                    # funded later stops being counted from the scan it is first funded, so the curve
                    # falls when capital finally arrives rather than staying flat.
                    "unfunded": [sum(1 for _k, _v in ev.items()
                                     if (_v.get("entries") or [])
                                     and _v["entries"][0].get("date", "") <= r["week"]
                                     <= _v["entries"][-1].get("date", "")
                                     and not any(str(_a)[:10] <= r["week"]
                                                 for _a, _b in (_ev_fund.get(_k) or [])))
                                 for r in M],
                    "zerospan": [sum(1 for _k, _v in ev.items()
                                     if (_v.get("entries") or [])
                                     and _v["entries"][0].get("date") == _v["entries"][-1].get("date")
                                     == r["week"])
                                 for r in M]},
        "inflow": {"w": [d["context"] for d in scout],
                   "prop": [len(d.get("proposed", [])) for d in scout],
                   "adm": [len(d.get("admitted", [])) for d in scout],
                   "cap": [d.get("max_new_events", 0) for d in scout]},
        "ceiling": ceil_rows[:40],
        "px": px_hist,
        # THE SEAM, for the time panels to mark. Bootstrap only: CBT is one retrieval path end to
        # end and a line on it would mark nothing. Read from bootstrap_corpus so the page and the
        # corpus assembly cannot drift to two different dates.
        "handoff": (__import__("bootstrap_corpus").HANDOFF if a.bootstrap else None),
        "cap_pct": float(_lfm0.get("concentration_cap", 0.25)),
        "max_events": _lfm0.get("max_events"),
        "gantt": [{"id": k, "cat": v.get("catalyst", "")[:70],
                   # `veh` is TRUNCATED for the storyboard label -- 33 vehicles will not fit on a
                   # gantt row. `vehall` is the real list, and panel 9 must use it: that panel
                   # attributes DOLLARS to events off this same record, so with `veh` alone every
                   # ticker past the 6th alphabetically was invisible to the attribution and its
                   # money fell into the "held, no live event" residual. It put SMMT -- 21.9% of the
                   # opening book and named by a seeded event -- in the orphan band, and inflated
                   # that band to 41.4% of book-days. A display cap must never reach a measurement.
                   "veh": sorted(v.get("vehicles", []))[:6],
                   "vehall": sorted(v.get("vehicles", [])),
                   "start": ((v.get("entries") or [{}])[0].get("date", "") or _opened.get(k, "")),
                   "end": ((v.get("entries") or [{}])[-1].get("date", "") or _opened.get(k, "")),
                   "beat": _ev_beat.get(k, "no beat"), "fund": _ev_fund.get(k, []),
                   "status": v.get("status", "")}
                   # TWO CLASSES ARE EXCLUDED, both counted in the lead and both plotted in panel 4:
                   #   1. CULLED AT BIRTH -- no agent entry at all, so no date and no span.
                   #   2. ZERO-SPAN -- opened and terminated on the SAME scan. One agent read, then
                   #      gone. 42 of 117 on the me16 book, and they draw as hairlines that are
                   #      visually indistinguishable from the axis while consuming a third of the
                   #      rows, burying the theses that actually ran.
                   # Excluding them is stated, never silent: silently dropping a class (no entries ->
                   # no date) was this panel's original bug, and the counts are drawn in panel 4 so
                   # what is missing is visible rather than merely described.
                   for k, v in ev.items()
                   if (v.get("entries") or [])
                   and (v["entries"][0].get("date") != v["entries"][-1].get("date"))],
        "src": {"s": [s for s, _ in src_c.most_common(25)], "n": [n for _, n in src_c.most_common(25)]},
        "lede": {"k": list(lede_c), "n": list(lede_c.values())},
        "bundle": bundle_buckets,
        "focus": list(_FOCUS_TICKERS),   # the no-brainer tickers, bolded in the gain-per-holding panel
        # GAIN by provenance. Each ticker's P&L is split across the provenance classes of ITS OWN
        # evidence, in proportion to how many of its cited articles came from each. A proportional
        # split, not an assignment: a pick argued off six archived articles and two live pages cannot
        # be said to have been "caused" by either, so it contributes 75%/25%. Tickers with no resolved
        # evidence are dropped rather than parked in an "unknown" bar -- they would dominate it
        # without saying anything about provenance.
        "ledegain": {"k": list(_prov_gain), "n": [round(v, 2) for v in _prov_gain.values()]},
        "beat": {"b": [b for b, _ in beat_c.most_common(20)], "n": [n for _, n in beat_c.most_common(20)]},
        "cov": {"t": [t for t, _ in top_cov], "n": [n for _, n in top_cov],
                "picked": [t in picked for t, _ in top_cov]},
        # GAIN PER ARTICLE, per ticker -- the per-ticker twin of the by-beat panel. Only tickers that
        # are BOTH in the top-40 by coverage AND carry a book gain qualify, so this is a subset of the
        # bars above; the panel says so rather than letting the shorter list read as missing data.
        "covgain": sorted(
            [{"t": t, "n": n, "g": round(float(_gain[t]), 2), "per": round(float(_gain[t]) / n, 2)}
             for t, n in top_cov if t in picked and t in _gain and n],
            key=lambda r: r["per"]),
        "book": book,
        # Runs curated before 2026-08-26 have no `lede_search` key: those genuinely had zero, since
        # the class did not exist and their websearch snippets were being overwritten by the title.
        # .get(...,0) is therefore the TRUE value for them, not a fallback that hides anything.
        "text": {"w": weeks, "clean": [r["lede_clean"] for r in M],
                 "live": [r["lede_live"] for r in M],
                 "search": [r.get("lede_search", 0) for r in M],
                 "none": [r["lede_headline_only"] for r in M]},
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
    # Raw profile TEXT, for the self-audit below: load_financial_model returns defaults
    # merged in, so it cannot tell a knob the profile DECLARES from one it merely defaults.
    PROFILE_TEXT = (ROOT / PROFILE_FILE).read_text()
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
        "max_events":               {0: "0 = uncapped"},
        "news_lookback_days":       {0: "0 = track rebalance_period"},
        "scout_articles_per_call":  {0: "0 = one call for everything"},
        "max_article_chars":        {0: "0 = untruncated"},
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
        # "every N days as run" dropped 2026-08-19: rebalance_period states the cadence two rows
        # down. The MISMATCH note stays -- surfacing that is the only reason the clause existed.
        ("curator calls", f"{len(M)} curations"
         + ("" if _cad == _cad_profile else f" — run at {_cad}d; profile now says {_cad_profile}d")),
        # BOTH INPUT PATHS ARE NAMED HERE ON PURPOSE. The page has been built off the wrong
        # curation twice: once when a stray `cp -R` left a stale journal inside cbt_3yr_v9
        # (ca5aee6), and once when the --run DEFAULT went stale and a bare run silently
        # rendered a 26-event throwaway. Neither was visible on the page.
        ("curation (local path)", f"{a.run}"),
        # The page attests to its OWN inputs. A reader months from now should not have to
        # reconstruct which corpus and which curation-knobs produced the numbers below --
        # that reconstruction is what cost a full investigation on 2026-08-21.
        # STATE THE RUN'S OWN STAMP, and the expected one only when they DIFFER. Printing just the
        # expected hash was misleading off the canonical path: a --out docs_preview build of some
        # other curation still printed the hash the PROFILE implies, i.e. a fingerprint belonging to
        # no run on disk. On a published page the two are necessarily equal (the gate blocks
        # otherwise), so this changes nothing there and stops the preview builds from lying.
        # CBS reads its own stamp rather than the CBT verifier's: `verify()` compares a curation
        # against the CANONICAL profile+corpus, which CBS is not and does not claim to be, so it is
        # skipped for this arm (see the gate). Reporting "(unstamped)" for a run that IS stamped was
        # the visible cost of that skip.
        ("curation fingerprint",
         ((_bs_stamp.get("hash") or "(unstamped)") + " \u00b7 bootstrap (not the canonical book)")
         if a.bootstrap else
         ((_vfy.get("hash_run") or "(unstamped)")
          + (" \u2713 canonical" if not _problems else
             f" \u2717 NOT canonical \u2014 profile+corpus imply {_vfy.get('hash_want')}")
          + (f" ({len(_vfy['unverifiable'])} knobs unrecorded)" if _vfy.get("unverifiable") else ""))),
        ("corpus (local path)",
         (f"{(_bs_stamp.get('corpus') or {}).get('path', 'bootstrap')} \u00b7 "
          f"{(_bs_stamp.get('corpus') or {}).get('span', '')}") if a.bootstrap else f"{a.corpus}"),
        ("lede arm", arm_used),
        (f"— {PROFILE_FILE} · cadence —", ""),
        _pv("rebalance_period"),
        (f"— {PROFILE_FILE} · curator —", ""),
        _pv("model"),
        _pv("scout_model"),
        _pv("event_agent_model"),
        _pv("picker_model"),
        _pv("retrieval_engine"),
        _pv("discovery_filter"),
        _pv("news_cap"),
        _pv("news_lookback_days"),
        _pv("event_news_cap"),
        _pv("relevance_filter"),
        # The three knobs that define the GROUPED scout (added 2026-08-15). Their absence made a
        # grouped run indistinguishable from a flat one on this page, which is the single biggest
        # design change the curation has had.
        _pv("scout_articles_per_call"),
        _pv("max_article_chars"),
        _pv("max_events"),
        _pv("max_new_events"),
        _pv("curator_memory_weeks"),
        _pv("exit_patience_scans"),
        _pv("max_stale_scans"),
        _pv("max_event_scans"),
        (f"— {PROFILE_FILE} · optimizer —", ""),
        _pv("initial_investment_usd"),
        _pv("starter_watchlist"),
        _pv("always_include"),
        _pv("max_watchlist"),
        _pv("cull_fresh_slots"),
        _pv("cull_fresh_scans"),
        _pv("concentration_cap"),
        _pv("risk_aversion"),
        _pv("min_trade_size"),
        # CANONICAL NAME. lookback_period_days is a LEGACY ALIAS that load_financial_model keeps in
        # sync; showing the alias meant the table named a knob the profile no longer uses.
        _pv("optimizer_lookback_days"),
        _pv("t_update_days"),
        _pv("risk_free_rate"),
        _pv("drop_unfunded_weeks"),
        _pv("unfunded_reentry_on_new_catalyst"),
        _pv("unfunded_cooldown_weeks"),
        # INGEST PARAMS, from retrieval_config.json rather than the profile (moved 2026-08-25). The
        # self-audit below scans the PROFILE TEXT, so once these left that file they stopped being
        # "declared" and silently dropped off this table -- the audit working correctly, but the page
        # losing a real setting. Shown explicitly, and attributed to the file that now owns them.
        ("— retrieval_config.json · ingest —", ""),
        ("specialty_allow", f"{len(_gkg._specialty())} entries"),
        ("mill_block", f"{len(_gkg._mill_block())} entries"),
    ]
    # SELF-AUDIT. This table is hand-maintained, so every knob added to the profile has to be added
    # here too -- and on 2026-08-15 eleven were not, including max_events and the two knobs that
    # define the grouped scout. A run's own settings silently going missing from the page that exists
    # to record them is the worst kind of drift, because the page still looks complete.
    # Anything declared in the profile and not placed above is appended rather than dropped.
    _shown = {k for k, _ in params}
    _declared = [m.group(1) for m in re.finditer(r"^([a-z_][a-z0-9_]*):", PROFILE_TEXT, re.M)]
    _alias = {"lookback_period_days"}          # legacy aliases, deliberately shown under the new name
    _missing = [k for k in dict.fromkeys(_declared) if k not in _shown and k not in _alias]
    if _missing:
        params.append(("— declared in the profile, not placed above —", ""))
        for k in _missing:
            v = fmp.get(k)
            # the source lists are long; a count is the readable form and the profile is one click away
            params.append((k, f"{len(v)} entries" if isinstance(v, list) and len(v) > 6 else _pv(k)[1]))
    prows = "".join(
        (f'<tr><td colspan="2" style="color:var(--text2);padding-top:10px;font-size:11.5px;'
         f'text-transform:uppercase;letter-spacing:.05em;border-bottom:none;">{esc(k.strip("— "))}</td></tr>'
         if not v else f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>")
        for k, v in params)
    ptable = (f'<section class="panel"><h2>Parameter settings</h2>'
              f'<p class="lead">The exact knobs behind every number on this page, read from '
              f'{_LINK(PROFILE_URL, PROFILE_FILE)} and '
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
    # WHERE THE BOOK'S P&L CAME FROM. On the bootstrap arm the book is SEEDED with the backtest's
    # recommendation, so a headline "curated $X vs SPY $Y" reads as curator skill when a share of it
    # is the inherited positions drifting. Measured on the first CBS run: of +$7,302 realised,
    # +$7,792 came from tickers inherited from CBT and never re-picked, against +$1,942 from the
    # curator's own names -- i.e. the majority was carried in, not earned here. Splitting it is the
    # difference between reporting a result and reporting a fact.
    try:
        _bseed = set((book_seed or {}).get("tickers") or [])
    except NameError:
        _bseed = set()
    if _bseed:
        # INHERITED WINS TIES, and the reason matters. The first cut used a "both" bucket for tickers
        # that were seeded AND later re-proposed -- which was fine while only the WATCHLIST was
        # seeded, but once the EVENTS are migrated the agents re-propose the inherited names every
        # scan, so "both" swallowed nearly everything and `inherited` read $0. The question the split
        # answers is "did this capital arrive with the backtest's book, or did this curator find it?"
        # -- and a ticker that arrived seeded arrived seeded, whatever happened afterwards. So the
        # test is ORIGIN, not membership: seeded tickers are inherited, full stop, and only names the
        # curator introduced count as its own.
        _own = {str(p_["ticker"]).strip().upper() for p_ in PICKS if (p_.get("ticker") or "").strip()}
        _g = book.get("gain") or {}
        _anch = set(book.get("anchors") or [])
        _split = {"inherited": 0.0, "curator": 0.0, "anchor": 0.0}
        _n = {"inherited": 0, "curator": 0, "anchor": 0}
        for _t, _v in _g.items():
            _k = ("anchor" if _t in _anch else "inherited" if _t in _bseed
                  else "curator" if _t in _own else "anchor")
            _split[_k] += float(_v or 0)
            _n[_k] += 1
        book["seed"] = {}          # replaced below; keeps the shape explicit
        book["seed"] = {**(book_seed or {}), "tickers": sorted(_bseed),
                        "split": {k: round(v, 2) for k, v in _split.items()}, "n_split": _n}
    # A COMPACT TICKER-LEVEL LOG, above the event-level one. GHR's native unit is the EVENT (one
    # catalyst, a basket of tickers), and the log below reads that way -- but the question "what did
    # the curator ADD and REMOVE this week" is answered in tickers, and PWR's CBT leads with exactly
    # that table (Date | Adds | Removes | Rejections | Retries). Same rows, the other projection: an
    # event opening contributes its vehicles as adds, an event exiting contributes its vehicles as
    # removes, so nothing here is new data -- it is the event log read as a portfolio diff.
    # WHAT "REJECTED" ACTUALLY MEANS, counted properly (rewritten 2026-08-29). This column used to
    # print len(proposed) - len(admitted), which was wrong three ways:
    #   1. It named "the cap". max_new_events has been 0 (uncapped) in every scan since aead17e
    #      retired it in favour of max_events; nothing is rejected by a cap.
    #   2. It subtracted DIFFERENT UNITS between NON-NESTED sets -- `proposed` is (ticker, company,
    #      thesis) records, `admitted` is bare tickers, and 20 admissions across the canonical run
    #      were never proposed at all (peers joining an event). The printed total was 137 against
    #      157 actual; a peer-heavy week would have printed a NEGATIVE rejection count.
    #   3. 43 of those 157 were tickers ALREADY LIVE on the watchlist -- the scout re-proposing a
    #      name the book already holds is not a rejection, and counting it overstated the guard by
    #      ~27%.
    # Now: DISTINCT tickers proposed and not admitted, already-live excluded, split by reason. The
    # reason matters because one bucket is a bug and the others are not -- a name-shaped reject is
    # the scout emitting "SANDISK" instead of SNDK, and those were real gems being dropped.
    _live_by_wk = locals().get("_live_by_wk") or {}   # set above; {} if that block bailed

    def _rej_cell(wk: str) -> str:
        d = _scout_by_wk.get(wk)
        if not d:
            return "\u2014"
        adm = {str(t).strip().upper() for t in (d.get("admitted") or [])}
        live = _live_by_wk.get(wk, set())
        prop = {str(p.get("ticker", "")).strip().upper() for p in (d.get("proposed") or [])}
        rej = sorted(prop - adm - live - {""})
        if not rej:
            return "0"
        # THE SCOUT LOGS ITS OWN REASONS -- use them rather than guessing from the string's shape.
        # `dropped_resolved` and `restated_resolved` are recorded per scan, and together they
        # explain 78 of the canonical run's 109: the guard refusing to re-chase a catalyst that has
        # already resolved. Filing those under "other" (as this did for an afternoon) hid the single
        # largest and most deliberate category behind the vaguest possible label.
        done = ({str(t).strip().upper() for t in (d.get("dropped_resolved") or [])}
                | {str(t).strip().upper() for t in (d.get("restated_resolved") or [])})
        # ORDER MATTERS on the shape tests: check the DOT before the length, or DIR.UN.TO and
        # TATASTEEL.NS are longer than five characters and get filed as names, not foreign listings.
        res, name, forn, other = [], [], [], []
        for t in rej:
            if t in done:                                  res.append(t)
            elif " " in t:                                 name.append(t)
            elif "." in t:                                 forn.append(t)
            elif len(t) > 5 or not t.isalnum():            name.append(t)
            else:                                          other.append(t)
        # ONE REASON PER LINE. A newline, not a <br>: table_html escapes its cells, so markup here
        # would render as literal text. The column's CSS carries white-space:pre-line to turn these
        # into breaks. The leading total is dropped -- the lines already add up, and the split is
        # the part worth reading.
        return "\n".join(f"{len(lst)} {lbl}"
                          for lst, lbl in ((res, "resolved catalyst"), (name, "unresolved name"),
                                           (forn, "foreign"), (other, "other")) if lst)

    _scout_by_wk = {d["context"]: d for d in scout}
    _tick_rows = []
    for wk in weeks:
        _op, _ex = opened.get(wk, []), exited.get(wk, [])
        if not _op and not _ex:
            continue
        _adds = sorted({v for _, e in _op for v in (e.get("vehicles") or [])})
        _rems = sorted({v for _, e in _ex for v in (e.get("vehicles") or [])})
        _tick_rows.append([
            wk,
            f"{len(_op)} opened / {len(_ex)} exited",
            ", ".join(_adds) or "\u2014",
            ", ".join(_rems) or "\u2014",
            _rej_cell(wk),
        ])
    # Column order is load-bearing for the CSS below, which colours by nth-child: adds is 3rd,
    # removes is 4th. Reorder these and the colours follow the position, not the meaning.
    tick_log = ('<div class="curation-log">'
                + table_html(["Week", "Events", "Adds (tickers)", "Removes (tickers)",
                              "Proposed, not admitted"], _tick_rows)
                + '</div>')
    tick_panel = (
        f'<section class="panel"><h2>@@N1@@. Curation log &mdash; tickers</h2>'
        f'<p class="lead">The same {len(_tick_rows)} changed weeks as the event log below, projected '
        f'onto TICKERS: an event opening contributes its vehicles as adds, an event exiting '
        f'contributes them as removes. Nothing here is new data &mdash; it is the event log read as a '
        f'portfolio diff, which is the form PWR\'s curator log takes and the form the question "what '
        f'changed this week" is usually asked in. <b>Proposed, not admitted</b> counts the DISTINCT '
        f'tickers the scout put forward that did not enter, excluding names already on the '
        f'watchlist &mdash; re-proposing a name the book holds is not a rejection. No cap is '
        f'involved: <code>max_new_events</code> has been uncapped since the coverage-rank cull '
        f'replaced it. <b>Read the split.</b> <i>foreign</i> is correctly outside a US book, and '
        f'<i>resolved catalyst</i> is the scout guard refusing to re-chase a catalyst that has '
        f'already played out, which is the largest category and entirely deliberate &mdash; but <i>unresolved '
        f'name</i> is the scout emitting a company name where a symbol belongs, and those are real '
        f'gems being dropped: on this curation SANDISK, SYMBOTIC, SPACEMOBILE, NuScale Power and '
        f'Intuitive Machines were all discarded rather than read as SNDK, SYM, ASTS, SMR and LUNR. '
        f'The resolver that should have caught them was silently inert (fixed 2026-08-29, but this '
        f'curation predates the fix and would have to be re-run to benefit).</p>'
        # Collapsed by default, matching the event log below: both are REFERENCE tables you open
        # with a week in mind, not findings you read top to bottom.
        f'<details class="tbl"><summary>show the {len(_tick_rows)}-week ticker log</summary>'
        f'<div class="scroll">{tick_log}</div></details></section>')
    curation_log = table_html(["Week", "Events opened (catalyst -> vehicles)", "Events exited",
                               "Proposed\u2192admitted"], log_rows)
    log_panel = (
        f'<section class="panel"><h2>@@N2@@. Curation log &mdash; events</h2>'
        f'<p class="lead">The {len(log_rows)} of {len(M)} weekly calls that CHANGED something — a week '
        f'where the curator opened or closed an event. No-change weeks are hidden. An <b>event</b> is '
        f'one catalyst and the basket of tickers expressing it, so opening an event is GHR\'s analogue '
        f'of an add. <b>Proposed&rarr;admitted</b> is what the scout put forward versus what survived '
        f'the scout\'s own filters. NOT a cap: <code>max_new_events</code> has been uncapped since '
        f'the coverage-rank cull replaced it, so a week where those differ is a week the scout '
        f'refused a candidate &mdash; usually a catalyst it had already seen resolve. The table '
        f'above splits the reasons.</p>'
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
        # ...BUT SAY WHERE IT CAME FROM. On the bootstrap arm the book is seeded with the backtest's
        # recommendation, so that sentence reads as curator skill when part of the move is inherited
        # positions drifting. The split is the honest qualifier and it belongs beside the claim, not
        # in a panel further down that a reader may never reach.
        _sp_ = (book.get("seed") or {}).get("split") or {}
        if _sp_:
            _inh = _sp_.get("inherited", 0)
            _own_ = _sp_.get("curator", 0)
            _nn = (book.get("seed") or {}).get("n_split") or {}
            bh_verdict += (
                f" <b>Read that with the split:</b> of the ${sum(_sp_.values()):+,.0f} realised, "
                f"${_inh:+,.0f} came from {_nn.get('inherited', 0)} tickers INHERITED from the "
                f"backtest at {(book.get('seed') or {}).get('from', '?')} and ${_own_:+,.0f} from "
                f"{_nn.get('curator', 0)} names this curator introduced itself (origin decides: a "
                f"seeded ticker stays inherited even after the curator re-proposes it). The book starts as the "
                f"backtest's recommendation, so a rising curve is not by itself evidence the "
                f"bootstrap curator added anything.")

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
            f"&middot; {len(ents)} {_per if len(ents) == 1 else _pers}</summary><ul style='font-size:12.5px;margin:.6em 0'>{li}</ul></details>")
    story_html = "".join(_blocks)

    # ~14px a row keeps every event legible; a fixed 700px gave 4px at 175 events.
    # ~18px a row: 48 beats in 460px gave 9.6px and Plotly silently SKIPPED every other label.
    _n_beats = len(book.get("gainbeat") or {})
    _n_beats_eff = sum(1 for _b, _n in (book.get("beatgated") or {}).items() if _n >= 20)
    _n_gantt = sum(1 for g in payload["gantt"] if g["start"])
    _n_lived = len(payload["gantt"])            # events that RAN for more than one scan (drawn)
    _n_zero = sum(1 for v in ev.values()         # opened and terminated on the same scan
                  if (v.get("entries") or [])
                  and v["entries"][0].get("date") == v["entries"][-1].get("date"))
    _n_culled = len(ev) - _n_lived - _n_zero    # no agent entry at all
    # Events that had an agent read and NEVER received capital, over the whole run. Stated as a number
    # rather than drawn as a fourth curve: it is a cumulative FLOW against three stocks, so on this
    # axis it would only ever rise and would flatten the bands that carry the panel's meaning.
    # The rolled middle of plot 5, stated in WORDS rather than drawn. Two tall grey bars compressed
    # the y-axis so the named tickers -- the point of the panel -- lost most of their range.
    _g5 = sorted((book.get("gain") or {}).items(), key=lambda kv: -kv[1])
    _g5mid = _g5[16:max(16, len(_g5) - 8)]
    _roll_w = sum(v for _, v in _g5mid if v > 0)
    _roll_l = sum(v for _, v in _g5mid if v <= 0)
    _roll_nw = sum(1 for _, v in _g5mid if v > 0)
    _roll_nl = sum(1 for _, v in _g5mid if v <= 0)
    _n_unfunded_ever = sum(1 for k, v in ev.items()
                           if (v.get("entries") or []) and not (_ev_fund.get(k) or []))

    # UNINVESTED DAYS, measured not assumed. The old caption asserted the book is "never actually in
    # cash" -- a claim that was true of one curation and silently false for the next (53% of days on
    # the v4 book). Anything the caption states about the data is now computed from the data.
    # `alloc` holds WEIGHTS (0..1), not dollars -- the JS multiplies by book value at render time.
    # Comparing the weight sum against the dollar value made every day read as uninvested.
    # PER-CURATION RETURN STATS, computed here so the caption, the median line and any future
    # reader of book["curstat"] cannot disagree. SE of the MEAN is the coherent error bar; the
    # median is reported beside it as the robust twin, not with the mean's error attached to it.
    # Same clamp as the chart: a scan date need not be a trading day.
    # SPY OVER THE SAME PERIODS, on the same clamped indices. Without it a median per-curation
    # return is a number with no scale: +5% a month is excellent or mediocre depending entirely on
    # what the market did in those same months, and this panel had no way to say which.
    _cd, _cv = book.get("dates") or [], book.get("value") or []
    _sv = book.get("spyser") or []
    _cur_r, _spy_r, _prev, _sprev = [], [], None, None
    for _w in (book.get("rebal") or []):
        _i = next((i for i in range(len(_cd) - 1, -1, -1) if _cd[i] <= _w), -1)
        if _i < 0:
            continue
        if _prev is not None and _prev > 0:
            _cur_r.append(100.0 * (_cv[_i] / _prev - 1.0))
            if _sprev and _i < len(_sv) and _sv[_i]:
                _spy_r.append(100.0 * (_sv[_i] / _sprev - 1.0))
        _prev = _cv[_i]
        _sprev = _sv[_i] if _i < len(_sv) else None
    _cur_n = len(_cur_r)
    if _cur_n > 1:
        _cur_med = statistics.median(_cur_r)
        _cur_mean = statistics.fmean(_cur_r)
        _cur_sd = statistics.stdev(_cur_r)
        _cur_se = _cur_sd / (_cur_n ** 0.5)
        _spy_med = statistics.median(_spy_r) if len(_spy_r) > 1 else None
        # PAIRED, period by period: how often did the book beat SPY in the SAME month? That is the
        # question two medians cannot answer, because they can both be right and still be won by
        # different periods.
        _beat = sum(1 for a, b in zip(_cur_r, _spy_r) if a > b) if _spy_r else 0
        book["curstat"] = {"n": _cur_n, "med": round(_cur_med, 4), "mean": round(_cur_mean, 4),
                           "sd": round(_cur_sd, 4), "se": round(_cur_se, 4),
                           "spy_med": None if _spy_med is None else round(_spy_med, 4),
                           "beat": _beat, "n_spy": len(_spy_r)}
        _cur_note = (f" Across the {_cur_n} periods the book's median is {_cur_med:+.1f}% and its mean "
                     f"{_cur_mean:+.1f}% &plusmn; {_cur_se:.1f}% (standard error)."
                     + (f" <b>SPY over the same periods medians {_spy_med:+.1f}%</b>, and the book "
                        f"beat it in <b>{_beat} of {len(_spy_r)}</b> of them &mdash; a median gap is "
                        f"not the same as winning the months, so both are given."
                        if _spy_med is not None else "")
                     + " The dotted rule is the book's median.")
    else:
        book["curstat"] = None
        _cur_note = (" Too few periods to summarise &mdash; a median and a standard error need "
                     "more than two.")

    _bv = book.get("value") or []
    _bal = book.get("alloc") or {}
    _cash_days = sum(1 for i in range(len(_bv))
                     if 1.0 - sum(s[i] for s in _bal.values() if i < len(s)) > 0.005)
    _cash_note = (
        f"<b>{_cash_days} of {len(_bv)} days ({100 * _cash_days / len(_bv):.0f}%) the book holds no "
        f"position at all</b> and sits in cash — drawn as its own band, not left blank. That is the "
        f"optimizer cancelling trades, not the curator running out of ideas: at "
        f"<code>min_trade_size {_lfm0.get('min_trade_size')}</code> with "
        f"<code>max_watchlist {_lfm0.get('max_watchlist')}</code>, positions sized below the floor are "
        f"dropped rather than shrunk."
        if _cash_days and _bv else
        "The weights sum to 1 on every day, so the book is never actually in cash.")
    # Panels 14/16/17 and three funnel bars are fed by decisions.jsonl, which backtest_gdelt writes
    # ONLY under --decisions. Rather than render an empty plot -- which reads as "measured nothing" --
    # each says so in its own lead. The counts are not recoverable after the fact: proposed-but-culled
    # candidates are persisted nowhere else.
    _NODEC = ("" if DEC else
              "<br><br><b>Not available for this curation.</b> This panel is built from "
              "<code>decisions.jsonl</code>, which <code>backtest_gdelt.py</code> writes only when run "
              "with <code>--decisions</code>. " + esc(str(a.run)) + " was curated without it, so the "
              "proposed-and-culled candidates were never recorded \u2014 and they are not recoverable "
              "afterwards, since the archive keeps only the articles and the scan log keeps only what "
              "was ADMITTED. The plot is empty because the data is MISSING, not because the counts "
              "are zero.")
    # NUMBERED BY POSITION, cross-referenced by DIV ID (@@c-breadth@@). Hard-coded numbers were
    # fine until a panel MOVED: the numbers renumber and every prose reference silently points
    # somewhere else. FBT hit this by dropping panels and solved it the same way.
    # THE STANDING RECOMMENDATION, as a table beside the bars. Weights are the optimizer's, not a
    # rounded display of them: someone sizing real money off this page should see what the book
    # actually holds, and 0.4% vs "0%" is the difference between a position and none.
    _rc = book.get("rec") or {}
    _rc_rows = ([(r["t"], r["w"], r["b"], "funded") for r in (_rc.get("funded") or [])]
                + [(r["t"], 0.0, r["b"], "unfunded") for r in (_rc.get("unfunded") or [])])
    _rec_tbl = ""
    if _rc_rows:
        _rec_tbl = (
            '<table><thead><tr><th>Ticker</th><th>Weight</th><th>Beat</th><th>Status</th></tr></thead>'
            '<tbody>'
            + "".join(f'<tr><td><b>{esc(t)}</b></td><td>{w:.2%}</td><td>{esc(b or "no beat")}</td>'
                      f'<td>{st}</td></tr>' for t, w, b, st in _rc_rows)
            + f'<tr><td><b>total</b></td><td><b>{sum(r[1] for r in _rc_rows):.2%}</b></td>'
              f'<td colspan="2">the remainder, if any, is uninvested cash</td></tr>'
            '</tbody></table>')
    _P = [
        panel_rec("Realized portfolio value",
              "Three books that all start at the same dollar: the curated one, a buy-and-hold of the "
              f"<code>starter_watchlist</code> ({bh_names}) bought equal-dollar on this book's FIRST "
              "day and never touched, and SPY. The control is the SAME basket on every page &mdash; "
              "it is what the curated book is measured against, not what the book starts holding, so "
              "it does not follow the seed. "
              "Squares mark the rebalances — dark red where an event actually "
              "opened or closed, orange where the curator rebalanced but changed nothing. "
              + bh_verdict +
              " Kept OUT of the headline on purpose: a backtest steered by returns on known history is "
              "how you overfit, which is why this page leads with breadth and precision.",
              "c-value", 380),
        panel_rec("Fractional value change per curation",
              "The book&rsquo;s percent change from one curation to the next. Markers are green "
              "when the period made money, red when it lost. The panel above is cumulative and on "
              "a log axis; this is the same book read one period at a time." + _cur_note +
              " The first curation has no predecessor so it is not plotted.",
              "c-curdelta", 380),
        panel_rec("Watchlist composition over time",
              "One row per ticker. The pale bar is the span the curator kept it WATCHLISTED — it held "
              "the thesis; the solid bar is the span the optimizer actually FUNDED it. The gap between "
              "them is the honest part: a name the curator believed in and the math never backed. "
              "Colour is the ticker&rsquo;s dominant BEAT &mdash; which part of the firehose its evidence "
              "came from. Grey means no beat-attributable evidence.",
              "c-wcomp", _wcomp_h),
        panel_rec("Event timeline",
              f"The {_n_lived} events that RAN. The pale bar spans proposed &rarr; terminated; the "
              "solid overlay is when the optimizer actually FUNDED it, coloured by beat. A bar with "
              "no solid section is a thesis the curator held and the math never backed. "
              f"<b>Two classes are not drawn, and both are counted in panel @@c-evcount@@:</b> {_n_culled} events "
              "with no agent read at all, and "
              f"{_n_zero} that opened and terminated on the SAME curation &mdash; those draw as hairlines "
              "indistinguishable from the axis and would bury the theses that ran."
              + (" <b>The axis spans this book, not the theses.</b> An inherited event carries its "
                 "history from before the handover &mdash; the oldest reaches back eleven months "
                 "&mdash; so its bar runs off the left edge and is marked there with a caret. "
                 "The caret means the thesis was already running when this book opened."
                 if a.bootstrap else ""),
              "c-gantt", max(700, 14 * _n_gantt)),
        panel_rec("Live events vs the cap",
              "Theses the curator tracked at once, split by whether the optimizer put capital behind "
              "them, against the <code>max_events</code> ceiling (dashed). The stack\u2019s top edge is "
              "the live count &mdash; where it touches the dashed line the cap is binding and the "
              "lowest coverage-ranked event was retired to make room, so the knob rather than the news "
              "set what got tracked. <b>The amber band is the allocation bottleneck</b>: live theses "
              "the math never backed, and across the run "
              f"<b>{_n_unfunded_ever} of {_n_lived + _n_zero}</b> events with an agent read died never "
              "funded. The dotted line is a flow, not part of the stack &mdash; events opened and "
              "closed in a single curation, dropped from panel @@c-gantt@@ as hairlines. "
              "Ticker counts live in the panel directly below &mdash; this panel counts THESES.",
              "c-evcount", 380),
        panel_rec("Breadth over time",
              "The COUNTS behind the panel above, per rebalance: events live, tickers with a live "
              "thesis, every ticker named by a live event, and the distinct catalysts. "
              "per rebalance. Catalysts are drawn separately because several events on one theme is "
              "concentration wearing a diversity costume. The dashed line is "
              f"<code>max_watchlist</code> = {_wcap}, which binds in <b>{_cull_bind} of "
              f"{len(_held_per_week)}</b> {_pers}. The green line is what actually held capital, "
              f"read off the allocation: a median of <b>{_held_med}</b> of the {_wcap} slots, so the "
              f"binding constraint is the optimizer rather than the cap. "
              f"The axis is log because the series span {_bmin} to {_bmax}." + _NODEC,
              "c-breadth", 380),
        panel_rec("The cull funnel",
              "What happens to the curator&rsquo;s names AFTER the journal is written, per rebalance. "
              "Panel @@c-funnel@@ funnels the curation and stops at the picks; this one starts there and "
              "ends at the money. The two indented bars are the two tiers of the cull and sum to the "
              "bar above them &mdash; the freshness tier is awarded by RECENCY alone, the trend tier "
              "by trailing return over noise. "
              "<b>Bars are per-rebalance medians, not run totals</b>, so they do not add up down the "
              "page the way panel @@c-funnel@@&rsquo;s do. Log axis. "
              f"The last drop is NOT idle capital. The cull leaves {_wcap} slots and the optimizer "
              f"funds {_cf_fund}, but those hold a median <b>{_cf_dep_med}%</b> of the book: "
              f"<code>min_trade_size {_lfm0.get('min_trade_size')}</code> forbids dust and "
              f"<code>concentration_cap {_lfm0.get('concentration_cap')}</code> allows one name most "
              f"of the book, so a fully-invested book IS two or three names. What bounds the COUNT is "
              f"the floor, not <code>max_watchlist</code> &mdash; and the unfunded slots still matter, "
              f"because they are the pool the optimizer chooses from.",
              "c-cullfunnel", 340),
        panel_rec("Cumulative $ gain per holding",
              "The 16 best and 8 worst funded names. Every other funded name is left OFF the "
              f"chart and stated here instead: <b>{_roll_nw} more winners worth "
              f"${_roll_w:,.0f}</b> and <b>{_roll_nl} more losers worth ${abs(_roll_l):,.0f}</b>. "
              "They were drawn as two grey bars and are not any more \u2014 both were taller "
              "than most named tickers, so they set the y-range and squashed the comparison "
              "this panel is for. "
              "<b>Bold tickers are the seven shortlist names</b> \u2014 the big multi-year "
              "risers whose press named dated catalysts, i.e. the ones this strategy most "
              "wants to be holding. "
              "A result resting on one or two names is a different thing from the same return spread "
              "across many — and the difference is not visible in the equity curve above. <b>Click any "
              "named bar</b> for that ticker&rsquo;s price history, with &#9650;/&#9660; marking the "
              "moments the optimizer funded and unfunded it.",
              "c-gainh", 560),
        panel_rec("Cumulative $ gain per beat",
              "The same dollars as the panel above, rolled up to the BEAT that surfaced each ticker\u2019s "
              "evidence \u2014 i.e. which part of the firehose paid. A beat that costs money is a "
              "retrieval-vocabulary problem, not a curator one.",
              "c-gainb", max(460, 18 * _n_beats)),
        panel_rec("Gain per article read, by beat",
              "The same dollars divided by how many of that beat's articles actually REACHED THE SCOUT "
              "&mdash; i.e. survived the discovery gate. That is the cost that binds: the scout is 91% of "
              "the LLM bill and reads only gate-passed articles. The corpus count and the pass rate are "
              "in the hover. They diverge sharply &mdash; the momentum beat passes at 31% against 3-8% "
              "elsewhere, because its atoms ARE the gate's vocabulary, so it is 17% of the corpus but 35% "
              "of everything the scout reads. "
              "Beats that retrieved articles but never produced a funded position sit at zero: they are "
              "pure cost. Read it against panel @@c-gainb@@ &mdash; a tall bar there with a short bar here is a "
              "beat carried by volume rather than by quality.",
              "c-beateff", max(420, 18 * _n_beats_eff)),
        panel_rec("Cumulative $ gain per event",
              "The same gains grouped by the EVENT that motivated them. PWR groups this by wave "
              "bucket; GHR's unit of thesis is the event, so this is its analogue. It answers whether "
              "the curator's <i>ideas</i> paid, independently of which vehicle expressed them.",
              "c-gaine", 480),
        panel_rec("Portfolio value by event over time",
              "How the book was distributed across events as the year ran. Wide bands that persist "
              "mean concentrated conviction; a churn of thin bands means the optimizer kept rotating."
              "<br><br><b>Two non-event bands.</b> Grey is the real <code>always_include</code> anchors "
              "(SPY, BIL). Amber is money still funded after every event naming that ticker had "
              "terminated \u2014 the same dollars panel @@c-gaine@@ buckets as <i>(unassigned)</i>. Until "
              "2026-08-22 the two were drawn as one band labelled \u201canchors\u201d. The 17.3%-vs-8.1% "
              "disagreement once reported here was not a disagreement at all: this panel attributed "
              "dollars off a vehicle list TRUNCATED TO 6 for the timeline\u2019s row labels, so every "
              "ticker past the sixth fell into the amber band. Measured on the full list, amber is "
              "<b>0.0%</b> of the backtest book and 2.9% of the bootstrap\u2019s."
              "<br><br>Only the <b>8 largest events by peak holding</b> are named in the legend \u2014 "
              "about 70 hold dollars at some point, and that many swatches cannot be matched to bands "
              "by eye. <b>Hover any band</b> to identify it; every event is hoverable, labelled or not.",
              "c-evtime", 420),
        panel_rec("Allocation over time",
                "Dollars held per ticker, stacked — the top edge is the portfolio value. The "
                "<code>always_include</code> anchors (SPY, BIL) sit outside the watchlist cap and are "
                "where idle capital parks; a grey anchor stretch is the book in SPY/BIL, not a "
                "decision to hold cash. " + _cash_note,
                "c-alloc", 580),
        panel_rec("Where the money goes at each rebalance",
                "Every rebalance, left to right &mdash; oldest first. <b>Scrolls sideways.</b> Each band is a position, its thickness the dollars "
                "it carries; ribbons show which ticker handed money to which. A band that leaves a "
                "column fatter than it entered is a ticker that rose. <b>Colour is that position's "
                "fractional gain or loss over the period it was held</b> &mdash; green up, red down, grey flat or unmeasured. The ticker-to-ticker attribution is a proportional model, not a trace: the "
                "book sells into a pool and buys out of it, so nothing records which dollar went "
                "where. Hover any band for the name, date and amount.",
                "c-flow", 560),
        panel_rec("Thesis concentration",
                "How much of the whole portfolio is riding on one event. Anchors are not a bet, so a "
                "day parked in SPY/BIL reads 0%; the dashed line is the per-ticker cap, for scale.",
                "c-evconc", 380),
        panel_rec("Curator funnel",
              "Everything the curator touched, from the articles it read down to the picks it logged. "
              "<b>Log x-axis.</b> Two things to watch: the <b>discovery gate</b> is the largest cut in "
              "the whole pipeline (~19&times;), and it is <b>scout-only</b> — event agents still read "
              "the full window, so tracking an event is never starved."
              "<br><br>Units change mid-funnel: <i>admissions</i> counts ticker-curations, so a ticker "
              "re-admitted in a later curation is counted again; <i>distinct tickers</i> and <i>events</i> "
              "are the de-duplicated views. NOT shown: the ticker guard, which resolves names to "
              "symbols and drops unresolvable ones before these counters see them." + _NODEC,
              "c-funnel", 340),
        panel_rec("Scout inflow vs admissions",
                f"Candidate <b>tickers</b> the scout proposed each <code>rebalance_period</code> "
                f"({_cad0} days here), against what was admitted. Each candidate is a (ticker, thesis) "
                f"pair, not an event — several can collapse into one event later (176 admissions became "
                f"{J.get('nid', 0)} events)." + _NODEC,
                "c-inflow", 340),
        panel_rec("Does a bigger bundle make the scout act?",
              "The design\u2019s central claim, tested. Articles are bundled by company so a ticker\u2019s "
              "move-signal and its DRIVER arrive together \u2014 a move with no cause is correctly "
              "refused, which is why RKLB produced nothing for so long. If that is right, bigger "
              "bundles should propose more often. Bars are how many bundles the scout was shown at "
              "each size; the line is the share that produced a proposal. <b>Watch the 1-article "
              "bar</b>: a bundle of one cannot corroborate anything, so it is the control.",
              "c-bundle", 380),
        panel_rec("Gains per bundle",
              "The same dollars as the panel above, by bundle NAME rather than by size \u2014 which "
              "bundles actually paid. A bundle is credited with the realised P&amp;L of every ticker "
              "proposed out of it, so a name here earned its money by putting a ticker in front of "
              "the scout at the right moment. The <b>30 largest by absolute value</b> are drawn, "
              "winners and losers both; hover for the tickers each produced. Everything else is "
              f"stated rather than drawn: <b>{bundle_buckets.get('rest_win_n', 0)} more bundles worth "
              f"${bundle_buckets.get('rest_win', 0):,.0f}</b> and "
              f"<b>{bundle_buckets.get('rest_los_n', 0)} more worth "
              f"\u2212${bundle_buckets.get('rest_los', 0):,.0f}</b>. Rolled into bars they would be "
              "taller than the largest named bundle and would flatten everything here, the same way "
              "they did in panel @@c-gainh@@. <b>A ticker proposed from two bundles is credited to both</b>, so the bars total ~107% of the book \u2014 measured 12.3% double-counted, of which COPX alone is $21,445; over DISTINCT tickers the panel covers 95.1% of realised P&amp;L." + _NODEC,
              "c-bundlename", 620),
        panel_rec("Coverage vs picks, per ticker",
              "Article counts for the 40 most-covered tickers in the corpus. <b>Green</b> got "
              "watchlisted at some point; <b>grey</b> was named in the news but never watchlisted.",
              "c-cov", 720),
        panel_rec("Gain per article, per ticker",
              "For the picked tickers above: dollars earned per article the press wrote about them. "
              "The per-ticker twin of panel @@c-beateff@@. A name that paid a lot on little coverage sits far right; "
              "a heavily-covered loser sits far left. Only tickers that were both in the top-40 by "
              "coverage AND funded appear, so this is a subset of the bars above.",
              "c-covgain", 420),
        panel_rec("Gain per lede provenance",
              "Dollars earned, split by where the text behind each pick came from. The money twin of "
              "the panel below: that one counts ARTICLES the curator cited, this one counts what those "
              "picks actually paid. Each ticker\u2019s P&amp;L is divided across the provenance of its "
              "own evidence in proportion &mdash; a pick argued off six archived articles and two live "
              "pages contributes 75%/25%, since neither can be said to have caused it. Read against "
              "the panel below: if <b>archived</b> supplies most of the reading but <b>live page</b> "
              "most of the money, the quotable arm is not the one earning.",
              "c-ledegain", 300),
        panel_rec("Evidence by lede provenance",
              "For every article the curator cited as evidence, where its text came from. If picks "
              "cluster on <b>archived</b> text (Wayback, look-ahead-clean) the clean arm is earning its cost; if they cluster on "
              "<b>live page</b> text the corpus is leaning on look-ahead-biased material.",
              "c-lede", 300),
        panel_rec("Evidence by source",
              "Which outlets actually produced the articles behind the picks. Compare with the "
              "firehose dashboard's source panel: an outlet supplying much of the corpus but little of "
              "the evidence is volume without signal.",
              "c-src", 620),
        panel_rec("Evidence by beat",
              f"Which standing searches ({_LINK(CONFIG_URL, 'retrieval_config.json')}) produced the "
              "articles behind the picks. A beat that fills the corpus but never appears here is "
              "paying rent without earning it.",
              "c-beat", 560),
        panel_rec("Event storyboard",
              "Each event's week-by-week journal: what the agent concluded, and why it eventually "
              "exited. The qualitative counterpart to the curation log — the only place you can see "
              "whether the exit logic is REASONING about a catalyst resolving or just pattern-matching "
              "on a price move. Funded events first, then those that never held capital.",
              "c-story", 0, story_html),
        panel_rec("Text provenance of what the curator read",
              "Per week, how much of the pool reached the curator as <b>archived</b> text, <b>live-page</b> "
              "text, a <b>search snippet</b>, or a bare <b>headline</b>. This is the firehose's provenance panel restricted to the "
              "slices the curator actually read. <b>Archived = Wayback</b> (archive.org's snapshot as of "
              "the article's own date), which is the only look-ahead-CLEAN text here: a live page is "
              "fetched today and may have been edited, extended or corrected since publication, so it can "
              "carry knowledge the curator could not have had. A week leaning on live text is a week "
              "whose result is an upper bound. <b>Search snippet</b> is the text the retrieval engine "
              "itself returned &mdash; the only text a websearch article ever has &mdash; captured at "
              "PULL time, which for the daily forward pull is within ~24h of publication, so it is "
              "cleaner than a live fetch without being an archive. Until 2026-08-26 this class did not "
              "exist and those snippets were OVERWRITTEN BY THE TITLE before the curator read them, "
              "which is why every pre-2026-08-26 curation shows the post-handoff era as headline-only: "
              "the chart was right, the pipeline was throwing the text away.",
              "c-text", 340),
    ]
    # FORWARD-LOOKING, AND THEREFORE BOOTSTRAP-ONLY. CBT's last curation is the end of a historical
    # window, so "what this strategy would hold right now" is simply false there -- the recommendation
    # is months stale and the next-curation clock counts toward a date already past. CBS is the live
    # paper book, where the last curation IS the standing one. Same reasoning PWR uses to show its
    # curation clock only on the arms that have a next curation.
    if a.bootstrap:
        _P += [
            # ---- the forward-looking pair. Everything above is history; these two answer "so what do I
            # hold?", and they exist so the page is usable on the day real money follows it.
            panel_rec("Latest recommended portfolio %",
                  "The optimizer&#39;s target weights at the most recent curation "
                  f"({esc(str(_rc.get('date') or '?'))}) &mdash; <b>what this strategy would hold right "
                  "now</b>. Bars at zero are watchlisted tickers the optimizer declined to fund: the "
                  "curator judged the thesis live, the sizing math did not buy it. Bar colour is the "
                  "ticker&#39;s dominant beat, the same key panel @@c-wcomp@@ uses; the dotted line is "
                  "<code>concentration_cap</code>. The always-include anchors appear here like any other "
                  "holding &mdash; that is where idle capital parks, so a book that is mostly SPY/BIL is "
                  "the optimizer declining to bet, not a bug. Click a bar for that ticker&#39;s price "
                  "history."
                  + (f" <b>Next curation due {esc(str(_rc.get('next_due')))}</b>, on the profile&#39;s "
                     f"{_rc.get('cadence_days')}-day cadence; between curations the watchlist is fixed "
                     "and only prices move." if _rc.get("next_due") else "")
                  + " <b>This is a recommendation, not a trade instruction.</b> It is computed from a "
                    "backtested book on a corpus that ends at the last curation, and non-negotiable #7 "
                    "still applies: the forward scoreboard, not this page, is the verdict.",
                  "c-rec", 400, _rec_tbl),
            panel_rec("Position sizes",
                  "Enter what you have to invest and the table gives the dollars and share count each "
                  "funded weight implies. Prices are the last close in this page&#39;s frozen price "
                  "panel &mdash; the same prices the book itself is valued at, so the arithmetic is "
                  "consistent with every other number here, and NOT a live quote. Fractional shares "
                  "assumed. Nothing here places a trade.",
                  "c-possize", 0),
        ]
    panels = ptable + render_panels(_P)
    # the two logs are reference tables, not headlines -- they read last, and their numbers continue
    # the panel sequence rather than being hard-coded.
    panels += (tick_panel.replace("@@N1@@", str(len(_P) + 1))
               + log_panel.replace("@@N2@@", str(len(_P) + 2)))

    # ONE IDENTITY FOR THE ARM. Title, header and the nav's current-page marker all derive from it;
    # hard-coding "CBT" in three places meant the CBS page announced itself as the backtest AND told
    # dash_nav that CBT was the page you were on -- so the nav greyed out CBT and offered CBS as a
    # link while you were already looking at CBS.
    _name = "Curator Bootstrap (CBS)" if a.bootstrap else "Curator Backtest (CBT)"
    _page = "cbs.html" if a.bootstrap else "cbt.html"
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_name}</title>
<script src="{PLOTLY_CDN}"></script>
<style>
:root {{ --surface:{LIGHT['surface']}; --card:#ffffff; --text:{LIGHT['text']}; --text2:{LIGHT['text2']};
  --grid:{LIGHT['grid']}; --line:#e6e5e1;
  /* Status colours are FIXED, not themed -- their meaning must not shift with the theme, and both
     read on either surface. Only ever used ALONGSIDE a word, never as the sole carrier. */
  --good:{STATUS['good']}; --critical:{STATUS['critical']}; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --surface:{DARK['surface']}; --card:#222220; --text:{DARK['text']}; --text2:{DARK['text2']};
  --grid:{DARK['grid']}; --line:#33322f; }} }}
:root[data-theme="dark"] {{ --surface:{DARK['surface']}; --card:#222220; --text:{DARK['text']};
  --text2:{DARK['text2']}; --grid:{DARK['grid']}; --line:#33322f; }}
/* CURATION LOG: adds green, removes red. Coloured per COLUMN rather than per cell, because
   table_html escapes its cells -- markup pushed through it renders as literal text. Both columns
   keep their word headers, so colour is redundant reinforcement and never the only signal. */
.curation-log td:nth-child(3) {{ color:var(--good); }}
.curation-log td:nth-child(4) {{ color:var(--critical); }}
/* the reject column is one reason per LINE -- pre-line turns the cell's newlines into breaks
   without putting raw markup through table_html's escaping. */
.curation-log td:nth-child(5) {{ white-space:pre-line; line-height:1.45; }}
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
{dash_nav.render(_page)}
<header>
  <h1>{_name}</h1>
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
  // THE HANDOFF SEAM. Every panel with a date axis gets the same dashed line at the same date, so
  // "did this change at the seam?" is answered by looking straight down the page rather than by
  // remembering a date between panels. Bootstrap only -- CBT is GKG end to end and has no seam.
  const HOFF = DATA.handoff || null;
  const _hoff = (lbl) => HOFF ? [{{type:'line', xref:'x', x0:HOFF, x1:HOFF, yref:'y domain',
    y0:0, y1:1, line:{{color:p.text2, width:1.5, dash:'dash'}}, layer:'above'}}] : [];
  const _hoffAnn = () => HOFF ? [{{x:HOFF, xref:'x', xanchor:'left', yref:'y domain', y:1,
    yanchor:'top', showarrow:false, font:{{size:10.5, color:p.text2}},
    text:' handoff ' + HOFF}}] : [];

  // Panel 4: the live-event stack against the max_events ceiling. STACKED rather than three
  // separate lines because the two bands are PARTS OF ONE WHOLE -- unfunded + funded = live -- so the
  // stack's top edge IS the live count and can be read straight against the cap, while the split
  // shows the allocation bottleneck without a second axis or a subtraction done by eye.
  // Unfunded sits BELOW: it is the base the optimizer has not acted on, and putting it underneath
  // keeps the funded band adjacent to the ceiling it is competing for.
  {{
    const ME = DATA.max_events;
    const LIVE = B.live_j || B.events;
    const UNF = B.unfunded || LIVE.map(() => 0);
    const FUND = LIVE.map((v, i) => Math.max(0, v - (UNF[i] || 0)));
    const tr = [
      {{type:'scatter', mode:'lines', stackgroup:'ev', name:'live, not yet funded',
        x:B.w, y:UNF, line:{{width:0.5, color:ST.warning}}, fillcolor:ST.warning,
        hovertemplate:'%{{x}}<br>%{{y}} live, no capital yet<extra></extra>'}},
      {{type:'scatter', mode:'lines', stackgroup:'ev', name:'live and funded',
        x:B.w, y:FUND, line:{{width:0.5, color:p.s1}}, fillcolor:p.s1,
        hovertemplate:'%{{x}}<br>%{{y}} live and funded<extra></extra>'}},
    ];
    // Same-curation events are a FLOW, not part of the live stock, so they stay a line on top of the
    // stack rather than a third band -- adding them to the stack would double-count.
    if (B.zerospan) tr.push({{
      type:'scatter', mode:'lines+markers', name:'opened & closed same curation',
      x:B.w, y:B.zerospan, line:{{width:2, color:ST.critical, dash:'dot'}}, marker:{{size:5}},
      hovertemplate:'%{{x}}<br>%{{y}} opened and terminated the same curation<extra></extra>'}});
    Plotly.react('c-evcount', tr, base(p, {{showlegend:true,
      legend:{{orientation:'h', y:1.16, x:0, font:{{size:11}}}},
      margin:{{l:60,r:24,t:16,b:52}},
      // type:'date' -- x is scan dates, which Plotly would otherwise treat as CATEGORIES, and
      // the handoff falls BETWEEN two scans (a Tuesday; scans are Fridays) so on a category
      // axis it matches nothing and the line silently does not draw.
      xaxis:{{gridcolor:p.grid, tickangle:-40, automargin:true, type:'date'}},
      yaxis:{{gridcolor:p.grid, rangemode:'tozero',
             title:{{text:'events live at once', font:{{size:11}}}}}},
      // CONCAT onto these, never a second `shapes:`/`annotations:` key earlier in the literal --
      // a duplicate key in an object literal silently wins, so the earlier one is simply discarded.
      // That is how the handoff line went missing here twice.
      shapes: ((ME && ME > 0) ? [{{type:'line', xref:'paper', x0:0, x1:1, yref:'y', y0:ME, y1:ME,
                line:{{color:ST.critical, width:1.8, dash:'dash'}}}}] : []).concat(_hoff()),
      annotations: ((ME && ME > 0)
        ? [{{xref:'paper', x:0.99, xanchor:'right', yref:'y', y:ME, yanchor:'bottom',
             showarrow:false, font:{{size:11, color:p.text2}}, text:'max_events '+ME}}]
        : [{{xref:'paper', x:0.99, xanchor:'right', yref:'paper', y:0.02, yanchor:'bottom',
             showarrow:false, font:{{size:11, color:p.text2}},
             text:'max_events 0 = uncapped, no ceiling to draw'}}]).concat(_hoffAnn())}}), CFG);
  }}

  // LOG y (2026-08-24). The three curator series span 1 to ~194 on this curation, so on a linear
  // axis `distinct catalysts` and the funded line are pinned to the baseline and the panel reads as
  // one curve plus some noise at the bottom -- which is the opposite of its point, since the whole
  // panel is about the GAP between events and the catalysts behind them.
  // ZEROS. A zero has no position on a log axis (the same reason panel 14's all-zero cap trace was
  // dropped outright rather than drawn at 0), so every series is mapped 0 -> null and GAPS there
  // instead. Gapping rather than substituting 0.5 or clamping: a fabricated positive value would
  // draw a week with no live events as if it had one. Nothing gaps on the current curation -- the
  // series minima are 1, 1, 1 and 4 -- so this is a guard against a future one, not a repair.
  const _lg = a => a.map(v => (v > 0 ? v : null));
  // COLOUR CONVENTION (2026-08-24, pilot panel). Green means GAIN and red means LOSS everywhere on
  // this page, so neither is available to an ordinary series. This panel had TWO green counts --
  // `distinct catalysts` on s3 and `actually funded` on ST.good -- either of which read as a P&L
  // series at a glance. Now: the three curator-breadth counts take categorical hues s1/s2/s4
  // (blue / orange / purple, validated together against both surfaces), and the two lines that are
  // NOT curator breadth take INK rather than a hue -- `actually funded` is the outcome everything
  // else is compared against, and the cap is a reference line, not a series. Spending a categorical
  // hue on a horizontal constant would have been a hue wasted on a threshold.
  Plotly.react('c-breadth', [
    {{type:'scatter', mode:'lines+markers', name:'events live', x:B.w, y:_lg(B.events),
      line:{{color:p.s1,width:2}}, marker:{{size:6,color:p.s1,line:{{width:1.5,color:p.surface}}}}}},
    {{type:'scatter', mode:'lines+markers', name:'tickers named', x:B.w, y:_lg(B.vehicles),
      line:{{color:p.s2,width:2}}, marker:{{size:6,color:p.s2,line:{{width:1.5,color:p.surface}}}}}},
    // DISTINCT TICKERS WITH A LIVE THESIS. Not the same as 'tickers named' above: that counts every
    // vehicle any live event names, this counts the tickers actually emitted as live picks. The gap
    // between them is vehicles the curator lists on a thesis but does not currently pick.
    ...(B.picks && B.picks.some(v=>v>0) ? [{{type:'scatter', mode:'lines+markers',
      name:'tickers with a live thesis', x:B.w, y:_lg(B.picks),
      line:{{color:p.s4,width:2}}, marker:{{size:6,color:p.s4,line:{{width:1.5,color:p.surface}}}}}}] : []),
    {{type:'scatter', mode:'lines+markers', name:'distinct catalysts', x:B.w, y:_lg(B.catalysts),
      line:{{color:p.s4,width:2,dash:'dot'}}, marker:{{size:6,color:p.s4,line:{{width:1.5,color:p.surface}}}}}},
    ...(B.cap > 0 ? [{{type:'scatter', mode:'lines', name:'max_watchlist cap',
      x:B.w, y:B.w.map(()=>B.cap),
      line:{{color:p.text2,width:1.8,dash:'dash'}}, hovertemplate:'cap %{{y}}<extra></extra>'}}] : []),
    {{type:'scatter', mode:'lines', name:'actually funded', x:B.w, y:_lg(B.held),
      line:{{color:p.s3,width:2.5}}, hovertemplate:'%{{x}}<br>%{{y}} funded<extra></extra>'}}
  ], base(p, {{xaxis:{{gridcolor:p.grid, type:'date'}}, shapes:_hoff(), annotations:_hoffAnn(), showlegend:true, legend:{{orientation:'h', y:1.13, x:0, font:{{size:11.5}}}},
      margin:{{l:60,r:24,t:36,b:60}},
      yaxis:{{gridcolor:p.grid, type:'log',
              title:{{text:'count (log)', font:{{size:11}}}}}}}}), CFG);

  const I = DATA.inflow;
  // LINEAR y, and no cap line. The log axis was justified when max_new_events was SET: proposed ran
  // 2-72 while admitted sat pinned at 2-4, so linear flattened the admitted bars into the baseline.
  // With max_new_events: 0 (uncapped, superseded by the max_events CONCURRENCY cap) the two series
  // share a range -- 0-11 vs 0-11 -- and log now only makes small differences look large. The `cap`
  // trace is dropped outright: it was all zeros, and a dashed line at 0 is meaningless and cannot be
  // drawn on a log axis at all.
  Plotly.react('c-inflow', [
    {{type:'bar', name:'proposed', x:I.w, y:I.prop, marker:{{color:p.s1, line:{{width:2,color:p.surface}}}},
      hovertemplate:'%{{x}}<br>proposed %{{y}}<extra></extra>'}},
    {{type:'bar', name:'admitted', x:I.w, y:I.adm, marker:{{color:p.s3, line:{{width:2,color:p.surface}}}},
      hovertemplate:'%{{x}}<br>admitted %{{y}}<extra></extra>'}}
  ], base(p, {{xaxis:{{gridcolor:p.grid, type:'date'}}, shapes:_hoff(), annotations:_hoffAnn(), barmode:'group', showlegend:true,
      legend:{{orientation:'h', y:1.15, x:0, font:{{size:11.5}}}}, margin:{{l:60,r:24,t:36,b:60}},
      yaxis:{{gridcolor:p.grid, rangemode:'tozero',
              title:{{text:'candidate tickers per rebalance_period', font:{{size:11}}}}}}}}), CFG);

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
  // CLAMP THE AXIS TO THE BOOK. A SEEDED event carries its entries from BEFORE the handover, so on
  // the bootstrap its `start` is the date the BACKTEST first proposed it -- the 29 inherited theses
  // reach back to 2025-06-01, eleven months before this book opens. Autoscaling to that squeezed the
  // four months CBS actually covers into the right quarter of the panel. The axis now spans the book,
  // same as every other time panel on the page, so the three of them can be read against each other.
  // The clipped bars are MARKED, not silently truncated: a caret at the left edge says "this thesis
  // was already running when the book opened", which is exactly what inheriting it means.
  // DATA.book, not BK: `const BK` is declared further down this function, and reading a const
  // before its declaration throws (temporal dead zone) rather than giving undefined -- which
  // killed this panel and every panel drawn after it.
  const _gd = DATA.book.dates, _g0 = _gd[0], _g1 = _gd[_gd.length - 1];
  const _gpre = G.filter(g => g.start < _g0);
  const _gcar = _gpre.length ? [{{
    type:'scatter', mode:'markers', x:_gpre.map(()=>_g0), y:_gpre.map(g=>g.id),
    marker:{{symbol:'triangle-left', size:9, color:p.text2,
             line:{{width:1.5, color:p.surface}}}},
    showlegend:false, hoverinfo:'text',
    hovertext:_gpre.map(g=>`${{g.id}} &mdash; inherited<br>running since ${{g.start}}, `
                          + `before this book opened`)
  }}] : [];
  Plotly.react('c-gantt', [..._gspan, ..._gfund, ..._gcar], base(p, {{margin:{{l:70,r:30,t:10,b:44}},
      shapes:_hoff(), annotations:_hoffAnn(),
      yaxis:{{gridcolor:'rgba(0,0,0,0)', automargin:true, tickfont:{{size:10}}}},
      xaxis:{{gridcolor:p.grid, type:'date', range:[_g0, _g1]}}}}), CFG);

  const C = DATA.cov;
  Plotly.react('c-cov', [{{
    type:'bar', orientation:'h', x:C.n.slice().reverse(), y:C.t.slice().reverse(),
    marker:{{color:C.picked.slice().reverse().map(b=>b?p.s1:p.grid), line:{{width:2,color:p.surface}}}},
    text:C.n.slice().reverse().map(v=>v.toLocaleString()),   // colour already carries picked/not
    textposition:'outside', textfont:{{color:p.text2, size:10}}, cliponaxis:false,
    hovertemplate:'%{{y}}<br>%{{x:,}} articles<extra></extra>'
  }}], base(p, {{margin:{{l:90,r:130,t:10,b:44}},
      yaxis:{{gridcolor:'rgba(0,0,0,0)', automargin:true, tickfont:{{size:10}}}},
      xaxis:{{gridcolor:p.grid, title:{{text:'articles naming this ticker', font:{{size:11}}}}}}}}), CFG);

  // 15. GAIN PER ARTICLE, per ticker. Diverging: losers left of zero, winners right, so "was this
  //     name's coverage worth reading?" is answered by which side of the line it sits on.
  (function(){{
    const CG = DATA.covgain || [];
    if (!CG.length) return;
    Plotly.react('c-covgain', [{{
      type:'bar', orientation:'h', x:CG.map(r=>r.per), y:CG.map(r=>r.t),
      marker:{{color:CG.map(r=>r.per >= 0 ? ST.good : ST.critical),
               line:{{width:2, color:p.surface}}}},
      text:CG.map(r=>(r.per>=0?'+$':'-$')+Math.abs(r.per).toFixed(0)),
      textposition:'outside', textfont:{{color:p.text2, size:10.5}}, cliponaxis:false,
      customdata:CG.map(r=>[r.n, r.g]),
      hovertemplate:'%{{y}}<br>$%{{x:,.1f}} per article'
                  + '<br>%{{customdata[0]:,}} articles &middot; $%{{customdata[1]:,.0f}} total<extra></extra>'
    }}], base(p, {{margin:{{l:90,r:96,t:10,b:46}},
        yaxis:{{gridcolor:'rgba(0,0,0,0)', automargin:true, tickfont:{{size:10.5}}}},
        xaxis:{{gridcolor:p.grid, zerolinecolor:p.text2, zerolinewidth:1.5,
                title:{{text:'$ gain per article naming this ticker', font:{{size:11}}}}}}
    }}), CFG);
  }})();

  const LD = DATA.lede;
  // BUNDLE PAYOFF: count of bundles shown (bars) against the share that produced a proposal (line).
  // Dual axis is legitimate here for the same reason as CBT panel 4: the second series is a RATE over
  // a different population, not the same quantity rescaled.
  const BU = DATA.bundle;
  if (BU && BU.labels && BU.groups.some(v=>v)) {{
    const rate = BU.groups.map((g,i)=> g ? 100*BU.hits[i]/g : 0);
    Plotly.react('c-bundle', [
      {{type:'bar', name:'bundles shown', x:BU.labels, y:BU.groups,
        marker:{{color:p.s2, line:{{width:2,color:p.surface}}}},
        text:BU.groups.map(v=>v.toLocaleString()), textposition:'outside',
        textfont:{{color:p.text2, size:10}}, cliponaxis:false,
        customdata:BU.hits.map((h,i)=>[h]),
        hovertemplate:'%{{x}} article(s) per bundle<br>%{{y:,}} bundles<br>'+
                      '%{{customdata[0]}} produced a proposal<extra></extra>'}},
      {{type:'scatter', mode:'lines+markers', name:'produced a proposal', x:BU.labels, y:rate,
        yaxis:'y2', line:{{width:2, color:ST.good}}, marker:{{size:9}},
        hovertemplate:'%{{y:.1f}}% of bundles this size produced a proposal<extra></extra>'}}
    ], base(p, {{showlegend:true, margin:{{l:66,r:62,t:34,b:48}},
        legend:{{orientation:'h', y:1.16, x:0, font:{{size:11}}}},
        xaxis:{{title:{{text:'articles in the bundle', font:{{size:11}}}}}},
        yaxis:{{gridcolor:p.grid, type:'log', title:{{text:'bundles shown (log)', font:{{size:11}}}}}},
        yaxis2:{{overlaying:'y', side:'right', ticksuffix:'%', rangemode:'tozero', showgrid:false,
                 title:{{text:'produced a proposal', font:{{size:11}}}}}}}}), CFG);
  }}

  // GAINS PER BUNDLE NAME -- horizontal, like the per-event panel, because the labels are names.
  // Beat bundles carry a sentinel prefix; strip it for display and mark them so the two kinds of
  // bundle stay distinguishable at a glance.
  if (BU && BU.names && BU.names.length) {{
    const _BP = '\u0000beat:';
    const _nm = BU.names.map(k => k.startsWith(_BP) ? 'theme: ' + k.slice(_BP.length) : k);
    const _isBeat = BU.names.map(k => k.startsWith(_BP));
    Plotly.react('c-bundlename', [{{
      type:'bar', orientation:'h', x:BU.ngain, y:_nm,
      marker:{{color:BU.ngain.map(v => v < 0 ? ST.critical : ST.good),
               line:{{width:2,color:p.surface}}}},
      customdata:BU.nticks,
      hovertemplate:'%{{y}}<br>$%{{x:,.0f}}<br>proposed: %{{customdata}}<extra></extra>'
    }}], base(p, {{margin:{{l:230,r:70,t:16,b:44}},
        yaxis:{{gridcolor:'rgba(0,0,0,0)', tickfont:{{size:10.5}}, automargin:true}},
        xaxis:{{gridcolor:p.grid, tickprefix:'$', zeroline:true, zerolinecolor:p.text2,
               zerolinewidth:1.5, title:{{text:'realised gain', font:{{size:11}}}}}}}}), CFG);
  }}

  // GAIN by provenance -- same three categories as c-lede, same colours, so the two panels read as
  // a pair. Bars can be NEGATIVE here (a provenance whose picks lost money), which the article-count
  // twin can never show, so the axis is not forced to zero.
  const LG = DATA.ledegain;
  if (LG && LG.k && LG.k.length) {{
    Plotly.react('c-ledegain', [{{
      type:'bar', x:LG.k, y:LG.n,
      marker:{{color:LG.k.map(k=>k==='archived'?ST.good:k==='live page'?p.s2:p.grid),
               line:{{width:2,color:p.surface}}}},
      text:LG.n.map(v=>'$'+Math.round(v).toLocaleString()), textposition:'outside',
      textfont:{{color:p.text2, size:11}}, cliponaxis:false,
      hovertemplate:'%{{x}}<br>$%{{y:,.0f}} of realised gain<extra></extra>'
    }}], base(p, {{margin:{{l:74,r:24,t:16,b:44}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$', zeroline:true, zerolinecolor:p.text2,
               title:{{text:'realised gain', font:{{size:11}}}}}}}}), CFG);
  }}

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
    marker:{{color:p.s1, line:{{width:2,color:p.surface}}}},
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
    // CLAMP to the last book day <= the scan date, never indexOf. A scan date is a BOOK date and
    // need not be a trading day: exact lookup returns -1 and the marker is dropped in silence, so
    // the caption promises a square at every rebalance and a third of them are absent. Measured on
    // the pages of 2026-08-28: CBT drew 25 of 36 squares and CBS 3 of 4, and all 12 missing dates
    // were WEEKENDS. Same bug class the price-modal comment below documents, and the same fix.
    // The square sits on the clamped day so it lands ON the curve; the hover names the real scan
    // date, which is the one the curation log and the journal use.
    const _bi = (t) => {{ for (let i = BK.dates.length - 1; i >= 0; i--) if (BK.dates[i] <= t) return i;
                         return -1; }};
    const _mk = (want) => {{
      const xs = [], ys = [], ds = [];
      (BK.rebal || []).forEach(w => {{
        const i = _bi(w);
        if (i >= 0 && (_ch.has(w) === want)) {{ xs.push(BK.dates[i]); ys.push(BK.value[i]); ds.push(w); }}
      }});
      return [xs, ys, ds];
    }};
    const [cx, cy, cd] = _mk(true), [nx, ny, nd] = _mk(false);
    // The scan date is what the curation log and the journal call this rebalance, so the hover
    // leads with it; the priced day is named only when the clamp actually moved (a weekend scan),
    // which is also the honest way to say the square sits a session away from the decision.
    const _lab = (ds, xs, what) => ds.map((w, i) =>
      w + (w === xs[i] ? '' : ' \u2192 priced ' + xs[i]) + '<br>' + what);
    Plotly.react('c-value', [
      {{type:'scatter', mode:'lines', name:'Curator-driven', x:BK.dates, y:BK.value,
        line:{{color:'#d97706', width:2.5}}, hovertemplate:'%{{x}}<br>%{{y:$,.0f}}<extra>curator</extra>'}},
      {{type:'scatter', mode:'lines', name:'Buy-and-hold (starter_watchlist)', x:BK.dates, y:BK.bh,
        line:{{color:'#3b82f6', width:2}}, hovertemplate:'%{{x}}<br>%{{y:$,.0f}}<extra>buy &amp; hold</extra>'}},
      {{type:'scatter', mode:'lines', name:'SPY benchmark', x:BK.dates, y:BK.spyser,
        line:{{color:'#10b981', width:2, dash:'dash'}}, hovertemplate:'%{{x}}<br>%{{y:$,.0f}}<extra>SPY</extra>'}},
      {{type:'scatter', mode:'markers', name:'Rebalanced (no change)', x:nx, y:ny,
        marker:{{symbol:'square', size:7, color:'#ea580c', line:{{width:1.5, color:p.surface}}}},
        text:_lab(nd, nx, 'rebalanced, watchlist unchanged'), hoverinfo:'text'}},
      {{type:'scatter', mode:'markers', name:'Watchlist changed', x:cx, y:cy,
        marker:{{symbol:'square', size:9, color:'#dc2626', line:{{width:1.5, color:p.surface}}}},
        text:_lab(cd, cx, 'an event opened or closed'), hoverinfo:'text'}}
    ], base(p, {{showlegend:true, legend:{{orientation:'h', y:1.14, x:0, font:{{size:11}}}},
        margin:{{l:74,r:24,t:40,b:44}},
        // THE HANDOFF, on the bootstrap arm only (BK.handoff is emitted just for CBS). Left of it
        // the curator reads GKG+wayback, right of it the daily websearch pull -- so the book to the
        // LEFT is what the backtest's own news produces, and any divergence to the RIGHT is the
        // news source changing rather than the method. Without the line the two halves of this
        // curve look like one continuous experiment, which is exactly the reading to avoid.
        shapes: BK.handoff ? [{{type:'line', xref:'x', x0:BK.handoff, x1:BK.handoff,
          yref:'paper', y0:0, y1:1, line:{{color:p.text2, width:1.5, dash:'dash'}}}}] : [],
        annotations: BK.handoff ? [{{x:BK.handoff, xref:'x', xanchor:'left', yref:'paper', y:1,
          yanchor:'top', showarrow:false, font:{{size:10.5, color:p.text2}},
          text:' handoff ' + BK.handoff + ' \u2014 websearch from here'}}] : [],
        // LOG y-axis: the book grows ~11x, so linearly the first two years flatten onto the baseline
          // and only the last leg is legible. On a log scale equal vertical distances are equal
          // PERCENTAGE moves, which is what makes the curator line comparable to SPY anywhere on it.
          yaxis:{{type:'log', gridcolor:p.grid, tickprefix:'$', title:{{text:'portfolio value (log)', font:{{size:11}}}}}}}}), CFG);

    // PER-CURATION RETURN. The curve above is cumulative and log-scaled, which is what makes it
    // comparable to SPY -- but it also means a bad period late in the run looks like a wiggle. This
    // is the same book differenced curation-to-curation, where every period is on the same footing.
    // CLAMP to the last book day <= the scan date, never indexOf: a scan that falls on a non-trading
    // day (or on today, before yfinance has posted a bar) misses exactly, and the period would be
    // dropped in silence -- the same bug class the price-modal comment below documents.
    {{
      const _bi = t => {{ for (let i = BK.dates.length - 1; i >= 0; i--) if (BK.dates[i] <= t) return i;
                         return -1; }};
      const CS = BK.curstat || null;
      const dx = [], dy = [], sy = [];
      let _prev = null, _sprev = null;
      (BK.rebal || []).forEach(w => {{
        const i = _bi(w);
        if (i < 0) return;
        const v = BK.value[i], s = (BK.spyser || [])[i];
        if (_prev !== null && _prev > 0) {{
          dx.push(w); dy.push(100 * (v / _prev - 1));
          sy.push(_sprev ? 100 * (s / _sprev - 1) : null);
        }}
        _prev = v; _sprev = s;
      }});
      if (dx.length) Plotly.react('c-curdelta', [{{
        // SPY FIRST so the book's markers sit on top of it. Same green dashed convention as panel 1,
        // so the two panels read as the same benchmark seen two ways -- cumulative there, per period
        // here.
        type:'scatter', mode:'lines', name:'SPY, same periods', x:dx, y:sy,
        line:{{color:'#10b981', width:2, dash:'dash'}},
        hovertemplate:'%{{x}}<br>SPY %{{y:+.2f}}%<extra></extra>'
      }}, {{
        type:'scatter', mode:'lines+markers', name:'the book', x:dx, y:dy,
        line:{{color:'#d97706', width:2}},
        marker:{{size:6, color:dy.map(v => v < 0 ? ST.critical : ST.good),
                 line:{{width:1, color:p.surface}}}},
        hovertemplate:'%{{x}}<br>%{{y:+.2f}}% since the previous curation<extra></extra>'
      }}], base(p, {{margin:{{l:70,r:24,t:40,b:44}}, showlegend:true,
          legend:{{orientation:'h', y:1.16, x:0, font:{{size:11}}}},
          // The median as a dashed rule. Sourced from book["curstat"], which is what the caption
          // quotes -- computing it a second time here is how the two would drift apart.
          shapes:_hoff().concat(CS ? [{{type:'line', xref:'paper', x0:0, x1:1, yref:'y',
            y0:CS.med, y1:CS.med, line:{{color:p.text2, width:1.2, dash:'dot'}}}}] : []),
          annotations:_hoffAnn().concat(CS ? [{{xref:'paper', x:1, xanchor:'right', yref:'y',
            y:CS.med, yanchor:'bottom', showarrow:false, font:{{size:10.5, color:p.text2}},
            text:'median ' + (CS.med >= 0 ? '+' : '') + CS.med.toFixed(1) + '%'}}] : []),
          xaxis:{{gridcolor:p.grid, type:'date'}},
          yaxis:{{gridcolor:p.grid, ticksuffix:'%', zeroline:true, zerolinecolor:p.text2,
                 zerolinewidth:1.5,
                 title:{{text:'change since previous curation', font:{{size:11}}}}}}}}), CFG);
    }}

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
      margin:{{l:78, r:260, t:16, b:44}}, shapes:_hoff(), annotations:_hoffAnn(),
      yaxis:{{type:'category', categoryorder:'array', categoryarray:_order.slice().reverse(),
              // tickmode linear + dtick 1 = one label PER ROW. Without it Plotly thins the labels
              // whenever rows outnumber the pixels it thinks it has, silently hiding half the tickers.
              tickmode:'linear', dtick:1,
              gridcolor:p.grid, tickfont:{{size:10}}, automargin:true}},
      xaxis:{{gridcolor:p.grid, type:'date'}}}}), CFG);

  // THE CULL FUNNEL. Two colours, not five: the two indented bars are a BREAKDOWN of the bar above
  // them, so they must not read as further stages of the same descent. Log x for the same reason
  // panel 14 uses one -- the series spans 99 down to 2 and the tail is the whole point.
  {{
    const CFN = BK.cullfunnel;
    if (CFN && CFN.values && CFN.values.length) {{
      Plotly.react('c-cullfunnel', [{{
        type:'bar', orientation:'h',
        x:CFN.values.slice().reverse(), y:CFN.labels.slice().reverse(),
        marker:{{color:CFN.tier.slice().reverse().map(t => t ? p.s4 : p.s1),
                 line:{{width:2, color:p.surface}}}},
        text:CFN.values.slice().reverse().map(v => v.toLocaleString()),
        textposition:'outside', textfont:{{color:p.text2, size:11}}, cliponaxis:false,
        hovertemplate:'%{{y}}<br>%{{x}} per rebalance (median)<extra></extra>'
      }}], base(p, {{margin:{{l:150,r:60,t:16,b:46}},
          yaxis:{{gridcolor:'rgba(0,0,0,0)', automargin:true, tickfont:{{size:11}}}},
          xaxis:{{gridcolor:p.grid, type:'log',
                 title:{{text:'names per rebalance (median, log)', font:{{size:11}}}}}}}}), CFG);
    }}
  }}

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
    // CLAMP, never indexOf. A funded span's endpoints are BOOK dates and need not be trading days:
    // exact lookup returns -1 and the whole funded overlay is dropped in silence, leaving a modal
    // that shows the watchlisted shading only -- i.e. it says "watched, never funded" about a
    // position that WAS funded. Measured on cbs_v4: 4 of 45 spans, and all four were spans ending on
    // the last book day, so the names it misreported were precisely the ones still held TODAY
    // (AEM, +$1,886, was the one that surfaced it). i1 >= i0 rather than > so a span that opened on
    // the final day still draws its entry marker.
    // BOTH ENDS CLAMP INTO THE SERIES. The 2026-08-26 fix handled a span whose END fell past the
    // price data; a span that STARTS past it still vanished. That happens on the newest positions:
    // the book runs to today, yfinance has no bar for today yet, so a name funded at the last
    // rebalance has span [today, today] and no trading day is >= it. Falling back to the last index
    // draws the entry marker on the final priced day -- off by one session, and truthful -- instead
    // of silently reporting a funded position as never funded, which is the whole bug class.
    const _fst = t => {{ for (let i = 0; i < PX.d.length; i++) if (PX.d[i] >= t) return i;
                        return PX.d.length - 1; }};
    const _lst = t => {{ for (let i = PX.d.length - 1; i >= 0; i--) if (PX.d[i] <= t) return i;
                        return 0; }};
    fd.forEach(s => {{
      const i0 = _fst(s.s), i1 = _lst(s.e);
      if (i0 >= 0 && i1 >= i0) {{
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
  // ---- THE STANDING RECOMMENDATION (panels @@c-rec@@ / @@c-possize@@). The only forward-looking
  // objects on the page: the weights the optimizer set at the LAST curation, and what they cost.
  // WHY THE UNFUNDED BARS ARE HERE AND DRAWN AT ZERO rather than omitted: the gap between what the
  // curator has live and what the optimizer funds is the single most misread thing on this page --
  // panel @@c-breadth@@ shows it only as two counts. A zero bar with the ticker still on the axis
  // says "the thesis is live and the math declined it", which is a different statement from absence.
  const REC = BK.rec || {{}};
  const _RF = (REC.funded || []), _RU = (REC.unfunded || []);
  // last non-null close in the frozen panel, with the date it came from. NOT a live quote: the whole
  // page is priced off the frozen panel, and a live fetch here would make the calculator disagree
  // with every other dollar figure on the page (and stop the build being reproducible).
  const _lastPx = tk => {{
    const ser = (PX.p || {{}})[tk];
    if (!ser) return null;
    for (let i = ser.length - 1; i >= 0; i--) if (ser[i] != null) return {{px: ser[i], d: PX.d[i]}};
    return null;
  }};
  // GUARDED ON THE DIV, not on the data: the payload is emitted for both arms but only
  // CBS renders the panels, and Plotly.react() on a missing id throws and takes the rest
  // of this script down with it.
  if ((_RF.length || _RU.length) && document.getElementById('c-rec')) {{
    const _xs = _RF.map(r => r.t).concat(_RU.map(r => r.t));
    const _ys = _RF.map(r => r.w).concat(_RU.map(() => 0));
    const _bs = _RF.map(r => r.b).concat(_RU.map(r => r.b));
    const _cap = DATA.cap_pct;
    Plotly.react('c-rec', [{{
      type:'bar', x:_xs, y:_ys, customdata:_bs, marker:{{color:_bs.map(_bcol)}},
      text:_ys.map(v => v > 0.0005 ? (100 * v).toFixed(0) + '%' : 'unfunded'),
      textposition:'outside', textfont:{{size:11, color:p.text2}}, cliponaxis:false,
      hovertemplate:'%{{x}} (%{{customdata}})<br>%{{y:.1%}} of the book<extra></extra>'
    }}], base(p, {{
      showlegend:false, margin:{{l:66, r:24, t:26, b:70}},
      yaxis:{{gridcolor:p.grid, tickformat:'.0%', rangemode:'tozero',
              title:{{text:'% of portfolio', font:{{size:11}}}}}},
      xaxis:{{gridcolor:'rgba(0,0,0,0)', tickangle:-35, tickfont:{{size:11}}, automargin:true}},
      shapes:[{{type:'line', xref:'paper', x0:0, x1:1, yref:'y', y0:_cap, y1:_cap,
                line:{{color:ST.critical, width:1.5, dash:'dot'}}}}],
      annotations:[{{xref:'paper', x:1, xanchor:'right', yref:'y', y:_cap, yanchor:'bottom',
        showarrow:false, font:{{size:10.5, color:ST.critical}},
        text:'concentration_cap ' + (100 * _cap).toFixed(0) + '%'}}]
    }}), CFG);

    // ---- position sizes. Client-side arithmetic on values embedded at render, so the page stays a
    // single static file. Funded rows only: a zero weight buys nothing and a row of zeros is noise.
    const _PR = _RF.map(r => {{ const q = _lastPx(r.t); return q ? [r.t, r.w, q.px, q.d] : null; }})
                   .filter(Boolean);
    const _missing = _RF.length - _PR.length;
    const _host = document.getElementById('c-possize');
    if (_host && _PR.length) {{
      _host.style.height = 'auto';
      const _asof = _PR.map(r => r[3]).sort().slice(-1)[0];
      const _dflt = Math.max(1000, Math.round((BK.final || 50000) / 1000) * 1000);
      _host.innerHTML =
        '<div style="padding:12px 14px;border:1px solid var(--line);border-radius:8px;'
        + 'background:var(--card);max-width:860px;">'
        + '<label style="font-size:14px;color:var(--text);">Portfolio to invest: $'
        + '<input id="pfcalc" type="number" min="0" step="1000" value="' + _dflt + '" '
        + 'style="width:140px;padding:3px 6px;margin-left:4px;font-size:14px;background:var(--card);'
        + 'color:var(--text);border:1px solid var(--line);border-radius:4px;"></label>'
        + '<span style="color:var(--text2);font-size:13px;margin-left:10px;">shares at the '
        + _asof + ' close of the frozen panel; fractional shares assumed'
        + (_missing ? ' &middot; ' + _missing + ' funded ticker(s) omitted: no price in the panel' : '')
        + '</span>'
        + '<table style="border-collapse:collapse;width:100%;font-size:14px;margin-top:10px;">'
        + '<thead><tr style="border-bottom:2px solid var(--line);text-align:left;">'
        + '<th style="padding:5px;">Ticker</th><th style="padding:5px;">Weight</th>'
        + '<th style="padding:5px;">Price</th><th style="padding:5px;">Invest $</th>'
        + '<th style="padding:5px;">Shares</th></tr></thead>'
        + '<tbody id="pfcalcbody"></tbody></table></div>';
      const _fmt = v => v.toLocaleString(undefined, {{maximumFractionDigits:0}});
      const _draw = () => {{
        const v = parseFloat(document.getElementById('pfcalc').value) || 0;
        let h = '', tot = 0;
        _PR.forEach(r => {{
          const d = v * r[1]; tot += d;
          h += '<tr style="border-bottom:1px solid var(--line);">'
             + '<td style="padding:5px;"><b>' + r[0] + '</b></td>'
             + '<td style="padding:5px;">' + (100 * r[1]).toFixed(1) + '%</td>'
             + '<td style="padding:5px;">$' + r[2].toFixed(2) + '</td>'
             + '<td style="padding:5px;">$' + _fmt(d) + '</td>'
             + '<td style="padding:5px;">' + (d / r[2]).toFixed(4) + '</td></tr>';
        }});
        // The remainder is CASH, and it is named. Weights need not sum to 1 -- the optimizer can
        // decline to deploy -- and a total row that quietly showed 62% with no fourth row would
        // read as an arithmetic error rather than as the book's actual position.
        const cash = Math.max(0, v - tot);
        h += '<tr style="border-top:2px solid var(--line);"><td style="padding:5px;"><b>uninvested cash</b></td>'
           + '<td style="padding:5px;">' + (100 * (v ? cash / v : 0)).toFixed(1) + '%</td>'
           + '<td style="padding:5px;"></td><td style="padding:5px;">$' + _fmt(cash) + '</td>'
           + '<td style="padding:5px;"></td></tr>'
           + '<tr><td style="padding:5px;"><b>total</b></td><td style="padding:5px;">100.0%</td>'
           + '<td style="padding:5px;"></td><td style="padding:5px;"><b>$' + _fmt(v) + '</b></td>'
           + '<td style="padding:5px;"></td></tr>';
        document.getElementById('pfcalcbody').innerHTML = h;
      }};
      document.getElementById('pfcalc').addEventListener('input', _draw);
      _draw();
    }} else if (_host) {{
      _host.style.height = 'auto';
      _host.innerHTML = '<p class="lead">No funded position at the last curation, so there is '
        + 'nothing to size. The optimizer left the book in cash or in the anchors.</p>';
    }}
  }}

  // Bind the drill-down click ONCE PER GRAPH, and stop retrying once every graph is bound.
  // The retry exists because this runs before Plotly.react() has turned the div into a graph
  // (`g.on` does not exist yet), so the first pass always misses. But the terminating condition has
  // to be counted against the ACTUAL id list: an earlier `done < 2` against a one-id list could
  // never be satisfied, so bind() rescheduled itself every 150ms forever, appending ANOTHER
  // plotly_click handler to c-gainh each pass. Within a minute one click fired _showTk hundreds of
  // times, each doing a full Plotly.react on the modal -- the tab froze instead of opening the popup,
  // which reads exactly like "the popup doesn't work" (found 2026-08-14 on plot 4).
  const _CLICKABLE = ['c-gainh', 'c-rec'];
  (function bind(){{
    const left = _CLICKABLE.filter(id => {{
      const g = document.getElementById(id);
      if (!g || !g.on || g._tkBound) return !(g && g._tkBound);   // not ready yet -> keep waiting
      g._tkBound = true;                                          // idempotent: never bind twice
      g.on('plotly_click', ev => {{
        // STRIP THE TICK MARKUP. Plotly renders HTML in tick labels, which is how the FOCUS
        // shortlist gets bolded -- so a bolded bar's x is "<b>MU</b>", not "MU", and PX.p has no
        // such key. _showTk returns silently on a miss, so the click did nothing and only for the
        // emphasised names: the six the emphasis exists to draw attention to, MU (the largest
        // gainer, and therefore the FIRST bar) among them. Found 2026-08-21.
        const tk = String(ev.points[0].x).replace(/<[^>]*>/g, '');
        if (!tk.startsWith('other (')) window._showTk(tk);           // the rolled bar is not a name
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
    // NO ROLLED BAR. The unnamed middle used to be drawn -- first netted into one grey bar, then
    // split into winners and losers. Both versions were TALLER than most named tickers (+$222k and
    // -$73k against a best single name of ~$50k), so they set the y-range and squashed the 24 bars
    // the panel exists to compare. The totals are stated in the caption instead, where they cost no
    // vertical range at all.
    const GHr = [..._top, ..._bot];
    const _isRoll = () => false;
    // BOLD the shortlist names. Plotly renders HTML in tick labels, so the emphasis rides on the
    // label itself -- no second series and no colour channel spent, and it survives the sort.
    const _FOCUS = new Set(DATA.focus || []);
    const _lbl = t => _FOCUS.has(t) ? '<b>' + t + '</b>' : t;
    Plotly.react('c-gainh', [{{
      type:'bar', x:GHr.map(e=>_lbl(e[0])), y:GHr.map(e=>e[1]),
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
    const _evVeh = Object.fromEntries((DATA.gantt || []).map(g => [g.id, g.vehall || g.veh || []]));
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
    // SPLIT THE RESIDUAL. This band was labelled 'anchors' and was NOT: it is value minus everything
    // an event claims, which is anchors PLUS every dollar held while no event naming that ticker was
    // live. Two different stories, so two bands.
    // MOST OF WHAT THE RESIDUAL USED TO SHOW WAS AN ARTEFACT, not a holding. Until 2026-08-25 the
    // attribution above read `g.veh`, the vehicle list TRUNCATED TO 6 for the gantt row label, so
    // every ticker past the 6th alphabetically in its event was unattributable and its dollars
    // landed here. Re-measured on `vehall`: CBT's residual 12.6% -> 0.0%, CBS's 41.4% -> 2.9%.
    // So the band is now what it claims -- and on the canonical book it is EMPTY, because
    // always_include gives idle capital a home and the curator does not hold past an exit.
    // The earlier "17.3% vs a real 8.1% SPY/BIL holding" reading was this bug, not a disagreement
    // between panels 9 and 10. A display cap must never reach a measurement.
    const _ANC = new Set(BK.anchors || []);
    const _ancD = BK.value.map((v, i) =>
      Object.keys(_DOL).reduce((s, t) => s + (_ANC.has(t) ? (_DOL[t][i] || 0) : 0), 0));
    const _rest = BK.value.map((v, i) => Math.max(0, v - _evSum[i] - _ancD[i]));
    // LEGEND: ONLY THE LARGEST EVENTS GET AN ENTRY. ~72 events hold dollars at some point, and a
    // horizontal legend of 72 names wraps into a dozen rows that run down over the plot -- no value
    // of `y` fixes that, there is simply not room. 72 colour swatches are unreadable anyway: nobody
    // matches a band to a swatch at that cardinality. The 8 biggest by peak holding are labelled and
    // everything else stays hoverable, which is how the band is identified in practice.
    const _peak = _evDollars.map(e => Math.max(...e[1]));
    const _cut = [..._peak].sort((a,b)=>b-a)[Math.min(7, _peak.length-1)] ?? 0;
    Plotly.react('c-evtime', [
      {{type:'scatter', mode:'lines', stackgroup:'one', name:'anchors (SPY, BIL)', x:BK.dates, y:_ancD,
        line:{{width:0.5, color:GREY}}, fillcolor:GREY,
        hovertemplate:'%{{x}}<br>anchors %{{y:$,.0f}}<extra></extra>'}},
      {{type:'scatter', mode:'lines', stackgroup:'one', name:'held, no live event', x:BK.dates, y:_rest,
        line:{{width:0.5, color:ST.warning}}, fillcolor:ST.warning, opacity:0.55,
        hovertemplate:'%{{x}}<br>held past event exit %{{y:$,.0f}}<extra></extra>'}},
      ..._evDollars.map((e,i)=>({{
        type:'scatter', mode:'lines', stackgroup:'one', name:e[0], x:BK.dates, y:e[1],
        line:{{width:0.5, color:PALS[i % PALS.length]}}, fillcolor:PALS[i % PALS.length],
        showlegend: _peak[i] >= _cut,
        hovertemplate:'%{{x}}<br>'+e[0]+' %{{y:$,.0f}}<extra></extra>'}}))
    ], base(p, {{xaxis:{{gridcolor:p.grid, type:'date'}}, shapes:_hoff(), annotations:_hoffAnn(), showlegend:true,
        legend:{{orientation:'h', y:1.14, yanchor:'bottom', x:0, font:{{size:10}}}},
        margin:{{l:70,r:24,t:62,b:44}},
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
        // NO NUMERIC FALLBACK. This read `DATA.cap_pct || 25`, where cap_pct is a FRACTION --
        // so the fallback would have drawn the cap at 2,500%, off the 0-105 axis and invisible,
        // rather than failing loudly. It also duplicated a config value in JS, which is how a
        // page ends up disagreeing with the profile it claims to describe. The builder always
        // emits cap_pct (it defaults from the profile), so absence is a bug, not a case to cover.
        capline.push(100 * DATA.cap_pct);
      }}
      const above = conc.filter(x => x >= 80).length;
      const live  = conc.filter(x => x > 0).length;
      Plotly.react('c-evconc', [
        {{type:'scatter', mode:'lines', name:'largest event, % of portfolio', x:BK.dates, y:conc,
          line:{{color:ST.critical, width:2}}, connectgaps:false,
          hovertemplate:'%{{x}}<br>largest thesis = %{{y:.0f}}% of the PORTFOLIO<extra></extra>'}},
        {{type:'scatter', mode:'lines', name:'per-TICKER cap (for scale)', x:BK.dates, y:capline,
          line:{{color:p.text2, width:1.5, dash:'dash'}}, hoverinfo:'skip'}}
      ], base(p, {{xaxis:{{gridcolor:p.grid, type:'date'}}, shapes:_hoff(), showlegend:true, legend:{{orientation:'h', y:1.16, x:0, font:{{size:11}}}},
          margin:{{l:60,r:24,t:44,b:44}},
          yaxis:{{gridcolor:p.grid, range:[0,105], ticksuffix:'%',
                  title:{{text:'largest thesis, % of portfolio', font:{{size:11}}}}}},
          annotations:[{{xref:'paper', yref:'paper', x:0.01, y:0.06, showarrow:false,
            font:{{size:11.5, color:p.text2}},
            text:`one thesis held &ge;80% of the book on <b>${{above}}</b> of ${{live}} funded days`}}]
            // CONCAT, never assign -- this panel already owns an annotation and assigning would
            // silently replace it.
            .concat(_hoffAnn())}}), CFG);

    }}


    // The always_include anchors are not curator picks -- they are where idle capital parks. Giving
    // them ONE shared neutral colour stops three separate hues implying three separate theses, and
    // makes the anchor band read at a glance as "capital the curator did not deploy".
    const ANCH = new Set(BK.anchors || []);
    const _ANCHC = GREY;
    const _nonAnchor = Object.keys(BK.alloc).filter(k => !ANCH.has(k));
    const _allocSum = new Array(BK.dates.length).fill(0);
    Object.values(_DOL).forEach(a => a.forEach((v,i) => {{ _allocSum[i] += v; }}));
    // THE CASH SERIES IS BACK (2026-08-15). It was deleted on the reasoning that "there is never an
    // empty stretch -- always_include [SPY, BIL] absorbs idle capital, so the weights sum to 1 on all
    // 734 days". That was true of the curation it was written for and is FALSE of this one: on the
    // v4 book 399 of 753 days (53%) carry NO position at all, in stretches up to 81 days, because at
    // max_watchlist 6 / min_trade_size 0.20 the optimizer cancels 32% of its trades and the book sits
    // in cash. Without this band those days rendered as blank white canvas, which reads as a chart
    // that failed to draw rather than as a book that is not invested -- the single most important
    // thing this panel can say. An assumption about the DATA had been hard-coded into the CHART.
    // Drawn only when it is real (>0.5% of the book on some day) so a fully-invested run is unchanged.
    const _cash = BK.value.map((v,i) => Math.max(0, v - _allocSum[i]));
    const _cashReal = _cash.some((c,i) => BK.value[i] > 0 && c / BK.value[i] > 0.005);
    // ---- sankey: dollars handed from ticker to ticker at every rebalance ----
    try {{ if (BK.flow && BK.flow.nodes && BK.flow.nodes.length) {{
      const F = BK.flow;
      // one hue per TICKER (not per node), so a name keeps its colour as it recurs across columns
      // COLOUR = the position's FRACTIONAL return over the period it was held; thickness is the
      // dollars, which a sankey derives from the link values automatically. Diverging red -> grey ->
      // green, clamped at +/-25% so one extreme move does not flatten every other band.
      // NOT `dark`: that is declared INSIDE pal(), so referencing it here is a ReferenceError --
      // which killed the whole script and blanked every panel after this one.
      const hue = r => {{
        if (r === null || r === undefined) return 'rgba(150,150,150,0.55)';
        const x = Math.max(-1, Math.min(1, r / 25));
        return x >= 0 ? 'rgba(' + Math.round(220 - 198*x) + ',' + Math.round(220 - 57*x) + ','
                                + Math.round(220 - 208*x) + ',0.85)'
                      : 'rgba(' + Math.round(220) + ',' + Math.round(220 + 182*x) + ','
                                + Math.round(220 + 182*x) + ',0.85)';
      }};
      // ROTATED BACK TO HORIZONTAL, but kept LONG: the plot is drawn 5200px wide inside a panel
      // that scrolls horizontally, so the 56 rebalances get ~90px each instead of being crushed
      // into the page width. responsive:false is required -- with it on, Plotly re-fits the trace
      // to the container on every resize and the explicit width is discarded.
      const _fel = document.getElementById('c-flow');
      if (_fel) {{
        _fel.style.width = '5200px';
        if (_fel.parentElement) {{
          _fel.parentElement.style.overflowX = 'auto';
          _fel.parentElement.style.overflowY = 'hidden';
        }}
      }}
      Plotly.react('c-flow', [{{
        type:'sankey', orientation:'h',
        arrangement:'snap',
        node:{{ pad:6, thickness:9, label:F.nodes.map(n => n.t),
               // NO PINNED x/y ON PURPOSE. Plotly derives the layer from the LINK TOPOLOGY, and
               // every link here spans exactly one rebalance, so columns come out right on their
               // own -- PROVIDED no node is orphaned (a node with no inbound link is assigned
               // layer 0, which is what put mid-book entries beside the starter_watchlist; the
               // orphan repair in the payload fixes that at the source).
               // Letting Plotly size the vertical axis is the POINT: each column's stack is scaled
               // by the value flowing through it, so the book starts short at the left and fills
               // the height as it grows. Pinning y to evenly-spaced ranks destroyed that.
               color:F.nodes.map(n => hue(n.r)),
               line:{{width:0}},
               customdata:F.nodes.map(n => [n.d, n.usd, n.p ? 'redeployed at this rebalance'
                                            : (n.r === null || n.r === undefined ? 'n/a'
                                               : (n.r > 0 ? '+' : '') + n.r + '%')]),
               hovertemplate:'<b>%{{label}}</b> %{{customdata[0]}}<br>$%{{customdata[1]:,.0f}} held'
                             + '<br>return this period %{{customdata[2]}}<extra></extra>' }},
        // pool nodes carry p:1, no label, and a hover saying what they represent
        
        link:{{ source:F.links.map(l => l.s), target:F.links.map(l => l.t),
               value:F.links.map(l => l.v),
               color:F.links.map(l => 'rgba(150,150,150,0.22)'),
               hovertemplate:'%{{source.label}} &rarr; %{{target.label}}<br>$%{{value:,.0f}}<extra></extra>' }}
      }}], base(p, {{margin:{{l:8, r:8, t:24, b:26}}, width:5200, height:540}}),
         {{displayModeBar:false, responsive:false}});
    }} }} catch (e) {{ console.error('c-flow panel failed:', e); }}

    Plotly.react('c-alloc', [
      ...(_cashReal ? [{{
        type:'scatter', mode:'lines', stackgroup:'one', name:'uninvested (cash)',
        x:BK.dates, y:_cash, legendgroup:'cash',
        // A DISTINCT tone from the anchor grey: "parked in SPY/BIL" and "not in the market at all"
        // are different states and must not share a colour.
        line:{{width:0.5, color:'#c2b8a3'}}, fillcolor:'#c2b8a3'
      }}] : []),
      ...Object.entries(_DOL).map((e,i)=>({{
      type:'scatter', mode:'lines', stackgroup:'one', name:e[0], x:BK.dates, y:e[1],
      legendgroup: ANCH.has(e[0]) ? 'anchors' : e[0],
      // Anchors share ONE grey; the palette index is taken over NON-anchors only, so pulling the
      // anchors out of the ramp doesn't leave holes or shift every other series' colour.
      line:{{width:0.5, color: ANCH.has(e[0]) ? _ANCHC : PALS[_nonAnchor.indexOf(e[0]) % PALS.length]}},
      fillcolor: ANCH.has(e[0]) ? _ANCHC : PALS[_nonAnchor.indexOf(e[0]) % PALS.length]
    }}))], base(p, {{xaxis:{{gridcolor:p.grid, type:'date'}}, shapes:_hoff(), annotations:_hoffAnn(), showlegend:true,
        // ~47 series wrap to several legend rows; anchoring the legend's BOTTOM just above the plot
        // and reserving real top margin keeps it fully clear instead of spilling back over the area.
        legend:{{orientation:'h', yanchor:'bottom', y:1.02, xanchor:'left', x:0, font:{{size:10}}}},
        margin:{{l:70,r:24,t:150,b:44}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$', title:{{text:'dollars held', font:{{size:11}}}}}}}}), CFG);
  }}


  const T = DATA.text;
  Plotly.react('c-text', [
    {{type:'bar', name:'archived', x:T.w, y:T.clean, marker:{{color:ST.good, line:{{width:2,color:p.surface}}}}}},
    {{type:'bar', name:'live page', x:T.w, y:T.live, marker:{{color:p.s2, line:{{width:2,color:p.surface}}}}}},
    // SEARCH SNIPPET -- its own band, never folded into 'archived'. Different provenance: archived is
    // archive.org as of the article's own date; a search snippet is what the engine returned at PULL
    // time, which for the daily forward pull is within ~24h of publication.
    ...(T.search && T.search.some(v=>v>0) ? [{{type:'bar', name:'search snippet', x:T.w, y:T.search,
      marker:{{color:p.s4, line:{{width:2,color:p.surface}}}}}}] : []),
    {{type:'bar', name:'headline only', x:T.w, y:T.none, marker:{{color:p.grid, line:{{width:2,color:p.surface}}}}}}
  ], base(p, {{xaxis:{{gridcolor:p.grid, type:'date'}}, shapes:_hoff(), annotations:_hoffAnn(), barmode:'stack', showlegend:true,
      legend:{{orientation:'h', y:1.15, x:0, font:{{size:11.5}}}}, margin:{{l:60,r:24,t:36,b:60}},
      yaxis:{{gridcolor:p.grid, title:{{text:'articles read', font:{{size:11}}}}}}}}), CFG);
}}
draw();
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
</script>
</body></html>"""
    out = ROOT / a.out if not Path(a.out).is_absolute() else Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dash_nav.stamp(doc))
    print(f"wrote {out}  ({len(doc)/1024:.0f} KB, {len(M)} weeks, {J.get('nid',0)} events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
