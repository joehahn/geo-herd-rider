#!/usr/bin/env python3
"""build_sbt_dashboard.py — the Sweep Backtest (SBT) dashboard: docs/sbt.html

Renders the FULL-FACTORIAL optimizer grid from scripts/sweep_optimizer.py. Zero LLM cost, zero
network: the curation is fixed and only the book math varies, so every number here is reproducible
by re-running the sweep.

Borrowed from PWR's sweep dashboard: the return-vs-drawdown frontier and a recommended-settings
table. The per-knob marginal panels and the two-knob heatmap were DROPPED 2026-08-21 along with the
max_events risk/cost pair and the judge audit -- the recommendation no longer comes from reading one
knob at a time, so panels showing knobs in isolation invited a way of choosing the page no longer
supports.

THE HEADLINE MEASURE IS THE REGION (panel 8): a config scored on its own one-knob neighbourhood
rather than on its single cell, over ten metrics turned into percentile ranks. Cancellation is one
of the ten, not the headline it used to be -- it is a RATIO and blind to magnitude, so it never
rewarded picking well, only not losing.

    python scripts/build_sbt_dashboard.py                     # canonical sweep -> docs/sbt.html
    python scripts/build_sbt_dashboard.py --sweep data/sweep_v7.json \
        --out docs_preview/sbt_v7.html                        # any other sweep: NOT to docs/
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import dash_nav  # noqa: E402
import provenance as _canon  # noqa: E402  canonical-inputs gate
from build_fbt_dashboard import (CSS, DARK, LIGHT, PLOTLY_CDN, PROFILE_URL, STATUS,  # noqa: E402
                                 _LINK, esc, panel, table_html, tile)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep", default=_canon.CANON_SWEEP)
    ap.add_argument("--out", default="docs/sbt.html")
    a = ap.parse_args(argv)
    S = json.loads((ROOT / a.sweep).read_text())
    # THE GATE, two parts. (1) The sweep records the curation it was computed on, so this checks the
    # BOOK rather than the filename. (2) The GRID must vary book knobs only. A sweep that varies a
    # CURATION knob is a different thing entirely -- it re-reads the news and produces one new
    # curation per cell -- and its cells are not comparable to the canonical book at all. Publishing
    # one here would put re-curated results on the page that claims to sweep the canonical curation.
    _p = []
    _iv = _canon.check_interpreter()
    if _iv:
        _p.append(_iv)
    _srun = S.get("run", "(unrecorded)")
    if _srun != _canon.CANON_RUN:
        _p.append(f"sweep was computed on {_srun}, canonical curation is {_canon.CANON_RUN}")
    _cur_knobs = sorted(set(S.get("grid") or {}) & _canon.CURATION_KNOBS)
    if _cur_knobs:
        _p.append(f"grid varies CURATION knobs {_cur_knobs}, which re-read the news -- every cell is "
                  f"its own curation, so these are not sweeps of {_canon.CANON_RUN}")
    _canon.require_publishable(a.out, "SBT", _p)
    cells = [c for c in S["cells"] if c.get("cancelled") is not None]
    keys = list(S["grid"])
    # `base` = where the LIVE profile sits in the grid (the star in panels 2-7, the "current" row).
    # Read it from the profile at BUILD time, not from S["base"], which froze when the sweep ran. The
    # cells never change when a knob moves -- only which one is "current" -- so a profile edit should
    # cost a 2-second rebuild, not a re-sweep. Falls back to the stored base for any key the profile
    # no longer carries (e.g. cull_rank, which now defaults rather than being listed).
    import optimizer as _opt
    _fm = _opt.load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    base = {k: (_fm.get(k) if _fm.get(k) is not None else S["base"].get(k)) for k in keys}

    def is_base(c):
        return all(c[k] == base[k] for k in keys)
    cur = next((c for c in cells if is_base(c)), None)
    by_canc = sorted(cells, key=lambda c: c["cancelled"])
    by_ret = sorted(cells, key=lambda c: -c["final"])

    # THE max_events SERIES (panel 10), if it has been collected. Optional on purpose: it is the one
    # thing on this page that is NOT free -- max_events is a CURATION knob, so each point cost a full
    # re-curation (~$3-4.50, ~45 min) rather than a replay of fixed book math. Absent -> panel omitted.
    _mb = ROOT / "data/sweep_min_bundle.json"
    mb = json.loads(_mb.read_text()) if _mb.exists() else None
    _me = ROOT / "data/sweep_max_events.json"
    me = json.loads(_me.read_text()) if _me.exists() else None
    # ORDER THE SERIES ONCE, HERE. max_events=0 means "uncapped", i.e. the LIMIT of the series, so it
    # belongs at the right-hand end -- sorting numerically puts it at the left where it reads as the
    # smallest cap, the exact opposite of what it is. Done at load so panel 1's table and panels 9-10
    # cannot disagree: they did, the table showing 4..20,uncapped and the plots uncapped,4..20.
    if me and me.get("rows"):
        me["rows"] = sorted(me["rows"], key=lambda r: (r["max_events"] == 0, r["max_events"]))

    payload = {"cells": cells, "keys": keys, "cur": cur, "me": me, "mb": mb}

    # What was swept, and what the profile currently says -- PWR's "Parameter settings" panel. The
    # `current` column is what makes it readable: without it the grid is a list of numbers with no
    # indication of where we actually stand in it.
    # DISPLAY the canonical knob name. The sweep grid is keyed on `lookback_period_days`, which is a
    # LEGACY ALIAS that load_financial_model keeps in sync with `optimizer_lookback_days`; showing the
    # alias made this table name a knob the profile no longer uses. Renaming the grid key itself would
    # invalidate every stored sweep, so the substitution is display-only.
    _CANON = {"lookback_period_days": "optimizer_lookback_days"}
    ps_rows = [[_CANON.get(k, k), ", ".join(str(v) for v in S["grid"][k]), str(base[k]),
                "free — book replay"] for k in keys]
    # max_events belongs in this table -- it IS swept on this page (panels 9-10) -- but it is swept
    # on completely different terms and listing it beside the six without saying so would be the
    # misleading part. The six are FREE: they re-weight a fixed curation, so 6,300 cells cost nothing.
    # max_events is a CURATION knob, so each value needed its own re-curation and its own LLM bill.
    # Hence the fourth column: it exists to keep that distinction on the page rather than in a commit
    # message. Only shown once the series has actually been collected.
    if me and me.get("rows"):
        _mer = me["rows"]                      # already in canonical order (see above)
        _cost = sum(r.get("cost_usd") or 0 for r in _mer)
        ps_rows.append([
            "max_events",
            ", ".join("uncapped" if r["max_events"] == 0 else str(r["max_events"]) for r in _mer),
            ("0 = uncapped" if not base.get("max_events") else str(base.get("max_events")))
            if "max_events" in base else
            ("0 = uncapped" if not _fm.get("max_events") else str(_fm.get("max_events"))),
            f"${_cost:.2f} — {len(_mer)} re-curations"])
    # min_bundle_articles, same treatment and for the same reason: a CURATION knob, one re-curation
    # per value, so it does not belong beside the six free ones without the cost column saying so.
    if mb and mb.get("rows"):
        _mbr = mb["rows"]
        ps_rows.append([
            "min_bundle_articles",
            ", ".join(str(r["min_bundle_articles"]) for r in _mbr),
            str(_fm.get("min_bundle_articles", 1)),
            f"$20.23 — {len(_mbr)} re-curations"])
    # THE MODEL SWEEP (panels 15-20). Listed here because a reader looking for "what was varied"
    # looks at this table, and the eight-arm bake-off is otherwise invisible until panel 12.
    #
    # IT WAS event_agent_model THAT MOVED, NOT scout_model. The scout was held FIXED at llama4 in all
    # eight arms -- every arm read the same 1,248 scout chunks off the same corpus -- and that is
    # exactly what makes the comparison controlled: any difference downstream is attributable to the
    # judgment stage. Both rows are shown so the held-fixed one is as visible as the varied one.
    _bof = ROOT / "data/bakeoff_summary.json"
    if _bof.exists():
        _bo = sorted(json.loads(_bof.read_text()), key=lambda r: r["cost"])
        ps_rows.append([
            "event_agent_model",
            ", ".join(r["disp"].replace("<br>", " ") for _bo_i, r in enumerate(_bo)),
            str(_fm.get("event_agent_model")),
            f"${sum(r['cost'] for r in _bo):.2f} — {len(_bo)} re-curations"])
        ps_rows.append([
            "scout_model",
            "not swept — held fixed in all 8 arms, which is what makes them comparable",
            str(_fm.get("scout_model")), "—"])
    param_tbl = table_html(["parameter", "values swept", "current (profile)", "cost to sweep"], ps_rows)

    # ---- PLATEAU: the anti-overfit rank, ported from PWR ------------------------------------------
    # A config's score is half its own cancellation and half the mean of its GRID NEIGHBOURS -- every
    # cell one step away on exactly one axis. A lone in-sample spike (great cell, poor surroundings)
    # sinks below a broad shallow region, which is the point: a robust neighbourhood is likelier to
    # hold FORWARD than a fragile peak, and with 5,760 cells on ONE 3-year path the best raw cell is
    # very likely noise. PWR plateaus over IR; we plateau over CANCELLATION because that is the
    # objective here -- the mechanism is what ports, not the metric.
    idx = {tuple(c[k] for k in keys): c for c in cells}
    pos = {k: {v: i for i, v in enumerate(S["grid"][k])} for k in keys}
    for c in cells:
        key = tuple(c[k] for k in keys)
        nb = []
        for ki, k in enumerate(keys):
            i0 = pos[k][c[k]]
            for step in (-1, 1):
                if 0 <= i0 + step < len(S["grid"][k]):
                    alt = list(key)
                    alt[ki] = S["grid"][k][i0 + step]
                    n = idx.get(tuple(alt))
                    if n:
                        nb.append(n["cancelled"])
        c["plateau"] = round(0.5 * c["cancelled"] + 0.5 * (sum(nb) / len(nb) if nb else c["cancelled"]), 1)

    # ---- ROBUST: the rank table 8 actually sorts on ------------------------------------------------
    # Mean of a config's CANCELLATION rank and its DRAWDOWN rank, both 0 (best) .. 1 (worst), taken
    # over every cell in the sweep. Ranks rather than raw values, because the two are on unrelated
    # scales (cancellation runs 4-269%, drawdown 0-100%) and averaging them directly would let
    # cancellation set the whole score.
    #
    # CHOSEN BY MEASUREMENT, 2026-08-17, not by argument. The only clean test available is the
    # noise-experiment pair -- data/sweep_me16.json and data/sweep_rep.json, the SAME settings curated
    # twice, differing only in LLM sampling. Rank all 6,300 configs on curation A, then look at where
    # that top 50 actually lands on curation B. Percentile of B's final value, and B's median final:
    #
    #     rank(canc)+rank(DD)   86th   $189,137   <- this
    #     + rank(sharpe)        85th   $160,259
    #     plateau(cancellation) 83rd   $156,393   <- what table 8 used before
    #     drawdown alone        67th   $ 94,531
    #     SHARPE                54th   $ 64,075   <- a coin flip
    #     slope_2h              53rd   $ 75,001
    #     grid median           50th   $ 62,997
    #     final                 43rd   $ 47,322   <- WORSE THAN RANDOM
    #     annualized return     41st   $ 44,803
    #     gain_pain             41st   $ 49,487
    #
    # Ranking by P&L -- final, annualized, gain-to-pain -- puts its winners BELOW the median on the
    # re-curation. That is non-negotiable #6 expressed as a number: those metrics select one
    # curation's luck. Sharpe carries essentially nothing across a re-run, which is why adding it to
    # this composite makes it slightly worse rather than better. Do not re-add it without a new
    # transfer test.
    #
    # PLATEAU SMOOTHING IS NOT WHAT WAS DOING THE WORK. Varying the self/neighbour weight on the
    # cancellation plateau moved the result barely at all (w=0.5 -> 83rd, w=0.3 -> 84th, w=0.0, i.e.
    # neighbours only -> 83rd). Cancellation is simply a REPRODUCIBLE metric while P&L is not.
    # `plateau` is therefore kept and still shown as a column, but it no longer sets the order.
    _rank = {}
    for fld in ("cancelled", "max_drawdown"):
        order = sorted(cells, key=lambda c, f=fld: (c.get(f) is None, c.get(f) or 0))
        n = max(len(order) - 1, 1)
        for i, c in enumerate(order):
            _rank.setdefault(id(c), []).append(i / n)
    for c in cells:
        r = _rank.get(id(c)) or [1.0, 1.0]
        c["robust"] = round(100 * sum(r) / len(r), 1)

    # 50% squeeze / 30% squeeze, by column name (position-independent, so a grid change cannot
    # silently point these at the wrong column).
    TIER_A = {"concentration_cap", "lookback_period_days", "drop_unfunded_weeks"}
    TIER_B = {"max_watchlist", "risk_aversion", "min_trade_size"}

    def _cls(i, headers):
        h = headers[i]
        if h in TIER_A:
            return ' class="k kA"'
        if h in TIER_B:
            return ' class="k kB"'
        return ""

    def _compact(v):
        """0.25 -> .25, 3.0 -> 3 : same number, fewer glyphs. A cell cannot render narrower than its
        own text, so this is what makes the 50% tier reachable at all."""
        t = str(v)
        if t.startswith("0.") and len(t) > 2:
            t = t[1:]
        if t.endswith(".0"):
            t = t[:-2]
        return t

    def _rot_table(headers, rows):
        """Same as table_html, but each header sits in a span the CSS can rotate.

        17 columns of 1-3 characters under headers like `concentration_cap` means the HEADER sets the
        column width and the table runs off the page. Rotating the labels ~30 degrees lets each column
        shrink to its DATA width, which is what fits the whole grid on one screen."""
        def _lab(x):
            # Break the long knob names on their underscores. Horizontal headers stacked over 2-3
            # short lines take LESS width than the rotated version did, and stay readable straight on.
            return ("<br>".join(esc(t) for t in x.split("_"))
                    if x in TIER_A | TIER_B else esc(x))
        h = "".join(f"<th{_cls(i, headers)}><span>{_lab(x)}</span></th>"
                    for i, x in enumerate(headers))
        b = "".join("<tr>" + "".join(
            f"<td{_cls(i, headers)}>{esc(_compact(c) if headers[i] in TIER_A | TIER_B else c)}</td>"
            for i, c in enumerate(r)) + "</tr>" for r in rows)
        return f'<table class="rot"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'

    def _f(x, s="", d=2):
        return "—" if x is None else (f"{x:.{d}f}{s}")

    # ---- SHORTLIST: read the gates straight off panels 2-7 ---------------------------------------
    # Panels 2-5 plot annualized return against drawdown, L1, L2 and cancellation. Rather than eyeball
    # a "good corner" in four separate clouds, cut all four at once and let what survives be the
    # candidate set. These are ABSOLUTE bars, deliberately: a percentile gate would always pass the
    # same fraction no matter how bad the book, which is how a weak sweep flatters itself.
    # NO CHURN GATE. Both L1<850 and L2<700 were excluding the best region of the grid: median Sharpe
    # rises monotonically with churn here (0.73 at L2<500 -> 1.17 at L2>900), so a churn ceiling was
    # selecting for not trading. Trading is free in an IRA, so churn is only ever a robustness PROXY --
    # and what it was proxying for is drawdown, which the upper bands really do carry (median 52-55%).
    # So gate the thing itself, and let churn fall where it may.
    # Re-cut 2026-08-13 for the v11 book. The old DD<40 / canc<45 pair left 17 of 6,300 -- and EXCLUDED
    # the grid's best cell (Sharpe 1.75, $1.35M) on a 51% drawdown. A shortlist that omits the top
    # config is a threshold set on a different book, not a filter. DD and cancellation do the selecting;
    # the Sharpe floor is a sanity check that cannot bind at the top of the list, since the table RANKS
    # by Sharpe -- it only trims a tail the 20-row display already hides.
    # Re-cut for the v14 book. Its cancellation spread is far wider than v13's (18-637% vs 20-309%),
    # so `cancelled` now does most of the selecting -- it alone keeps 586 of 6,300, against L2's 4,611.
    # The churn bars are back, but LOOSE: they exclude the runaway-turnover tail without excluding the
    # profitable high-churn region that the old L1<850 gate was silently cutting out.
    # RE-CUT 2026-08-17 (seventh set, on the v8 book): DD < 65%, L1 > 2000%/yr, L2 750-1350/yr,
    # Sharpe > 0.8, cancelled < 65%. 2,288 of 6,300 survive.
    #
    # READ `cancelled` AS A PERCENT. It is stored 4.1-268.6 (median 58.6), not 0-1, so the requested
    # "cancel < 0.65" is taken as < 65%. Read literally it would admit ZERO of the 6,300 cells, since
    # the lowest cancellation anywhere on the grid is 4.1%.
    #
    # SHARPE AT 0.8 barely separates -- 53.0% of the grid clears it, against 30.2% at the 1.0 bar. It
    # is still the largest single cut here (3,850 -> 2,372); DD < 65% is inert at 90.4%.
    #
    # A RETURN GATE WAS ADDED HERE ON 2026-08-17 AND REVERTED THE SAME DAY. `final > $200K` was
    # proposed to make the shortlist "scale with gains". It should not have been: bootstrapped over
    # 300 resampled config subsets, adding it to the gate set was worth a median +1.0 percentage point
    # in the A->B direction of the transfer test and EXACTLY 0.0 in the B->A direction (P(better) 94%
    # and 0% respectively). A one-way one-point effect is not a reason to change what the page
    # recommends.
    #
    # IT ALSO BROKE THE PAGE'S OWN LOGIC, which is the more important reason it is gone. With the
    # floor in, the cumulative gate counts ran 3,850 -> 706 -> 706 -> 706: the return threshold cut
    # everything and CANCELLATION THEN REMOVED NOTHING AT ALL. Cancellation is this module's stated
    # headline measure and the objective the sweep exists to serve, so a shortlist where it no longer
    # constrains anything -- and a P&L threshold fitted to THIS curation does all the selecting -- is
    # the failure non-negotiable #6 describes, not a refinement of it.
    #
    # THE UNDERLYING FINDING STILL STANDS and is what `robust` rests on: ranking by cancellation and
    # drawdown transfers across a re-curation (86th/68th percentile) while ranking by Sharpe (54th),
    # slope (53rd) or final value (43rd, worse than random) does not. That gap is large and shows in
    # both directions. The micro-differences BETWEEN good options -- gate vs no gate at +1/0, plateau
    # vs robust at +3/0 -- are not, and should not be treated as decisions.
    #
    # THE 'NEVER GAINED OR LOST' DEGENERATE CASE DOES NOT ARISE, which is why the score needs no
    # return term to defend against it. Both of robust's inputs are SCALE-FREE RATIOS: cancellation is
    # |losses| / gains, so a book making $1K and losing $200 scores the same as one making $500K and
    # losing $100K, and sitting still does not lower it (with no gains it is undefined and the cell is
    # dropped). Measured on the top 100 by robust with NO gates applied: median final $199,584 against
    # a grid median of $103,541, median annualized 83% against 43%, median L1 2,195 -- fully trading.
    # One cell of 100 finished under $100K, and NO cell anywhere in the 6,300 has L1 < 50%/yr.
    #
    # THE LIVE CONFIG [8, 0.25, 21, 0, 4.00, 0.10] PASSES ALL SIX: DD 31.1, L1 2230, L2 806,
    # Sharpe 1.80, cancellation 35.6, final $302,079.
    GATES = [("max DD", "max_drawdown", lambda v: v < 65, "&lt; 65%"),
             ("L1", "l1", lambda v: v > 2000, "&gt; 2000%/yr"),
             ("L2", "l2", lambda v: 750 < v < 1350, "750&ndash;1350/yr"),
             ("Sharpe", "sharpe", lambda v: v > 0.8, "&gt; 0.8"),
             ("cancelled", "cancelled", lambda v: v < 65, "&lt; 65%")]
    _pos = {id(c): i for i, c in enumerate(cells)}
    # ---- THE REGION: ONE CURATION, LOCAL NEIGHBOURHOODS ------------------------------------------
    # WHAT CHANGED AND WHY (2026-08-21). This used to pool 15 sweeps across as many curations, on the
    # argument that averaging over draws of the news beats trusting one. The premise did not survive
    # being checked. Those curations are not repeat draws of one setup: the text they read ranges
    # from 2.5% to 56.9% clean (wayback) lede, and one fed the curator 46.7% bare headlines. Four
    # read a different article pool entirely and one varied
    # max_events. Three of the "15" were the same curation swept twice, which inflated n and shrank
    # the standard error the region width is built from. Averaging over that is averaging over
    # RETRIEVAL REGIMES, not over curation noise.
    #
    # So the population is now ONE curation -- the canonical one, which read the best text of any of
    # them -- and the noise control moves inside the grid instead. A config's REGION is itself plus
    # every config differing in exactly ONE knob: 1 + 3+2+6+2+4+4 = 22 cells. The luckiest and
    # unluckiest member BY FINAL VALUE are dropped, and each metric is summarised as the median and
    # standard error of the mean over the surviving 20.
    #
    # This is a different defence against the same error, and it is the one that fits a single
    # history. Ranking cells picks the cell that best fits this history's accidents. Ranking
    # NEIGHBOURHOODS cannot: a knife-edge cell whose neighbours are bad scores badly, because its
    # neighbours are in its own score. A config only ranks well if the settings AROUND it also work,
    # which is the property you actually want when the number will be run forward on new news.
    #
    # RANKING is the mean of per-metric percentile ranks across all 6,300 regions, each metric
    # oriented so higher is better. Percentiles because the metrics have incomparable units; the mean
    # because no weighting is defensible without evidence for one. Ranking instead on each metric's
    # conservative (median - 1 SE) bound picks the SAME top config and shares 18 of its top 20, so the
    # simpler form is kept and the SE is shown rather than folded in.
    import math as _math
    _pos = {id(c): i for i, c in enumerate(cells)}
    _by = {tuple(c[k] for k in keys): c for c in cells}
    # metric -> +1 higher is better, -1 lower is better
    # SCORED SET, cut from ten to seven on 2026-08-21. `final` and `ann` are gone because they are the
    # SAME AXIS counted twice more: across the 6,300 config means annualized correlates +0.93 with
    # final value, so keeping both silently tripled the weight on return and let a lucky book carry a
    # region. `worst_behind` is gone as the weakest of the risk measures and the one most driven by a
    # single bad stretch. What remains is one return-shape measure (slope), two risk-adjusted ones
    # (sharpe, gain_pain), two picking measures (capital_hit, edge) and two give-back measures
    # (cancelled, max_drawdown). final and ann are still COLUMNS -- they are what a reader wants to
    # see -- they just no longer vote.
    _MET = {"sharpe": 1, "gain_pain": 1, "slope_2h": 1, "capital_hit": 1,
            "edge": 1, "safe_park": 1, "cancelled": -1, "max_drawdown": -1}
    # SHOWN BUT NOT SCORED. Summarised per region exactly like the scored ones so the columns and the
    # payload have them, but excluded from the percentile mean -- see the note above.
    _SHOW = ("final", "ann")

    def _neigh(t):
        yield t
        for i, k in enumerate(keys):
            for v in S["grid"][k]:
                if v != t[i]:
                    yield t[:i] + (v,) + t[i + 1:]

    _reg_stat, _reg_mem = {}, {}
    for _t in _by:
        _mem = [_by[n] for n in _neigh(_t) if n in _by]
        _mem = [c for c in _mem if c.get("final") is not None]
        if len(_mem) < 5:
            continue
        _reg_mem[_t] = _mem
        _keep = sorted(_mem, key=lambda c: c["final"])[1:-1]   # drop luckiest + unluckiest
        _st = {}
        for _m in (*_MET, *_SHOW):
            _v = [c[_m] for c in _keep if c.get(_m) is not None]
            if len(_v) > 1:
                _st[_m] = (statistics.median(_v),
                           statistics.stdev(_v) / _math.sqrt(len(_v)))
        _st["_n"] = len(_keep)
        _reg_stat[_t] = _st

    import bisect as _bis
    _score = {}
    for _m, _sgn in _MET.items():
        _sorted = sorted(r[_m][0] for r in _reg_stat.values() if _m in r)
        for _t, r in _reg_stat.items():
            if _m not in r:
                continue
            _p = 100 * _bis.bisect_left(_sorted, r[_m][0]) / len(_sorted)
            _score.setdefault(_t, []).append(_p if _sgn > 0 else 100 - _p)
    _score = {t: statistics.mean(v) for t, v in _score.items()}
    _rank = sorted(_reg_stat, key=lambda t: -_score[t])
    _best = _rank[0]
    _live = tuple(base[k] for k in keys)

    def _pm(t, m, fmt, scale=1.0):
        r = _reg_stat[t].get(m)
        if not r:
            return "\u2014"
        return f"{fmt.format(r[0] * scale)} \u00b1 {fmt.format(r[1] * scale)}"

    def _rrow(t, tag):
        return [tag + " · ".join(str(x) for x in t),
                f"{_score[t]:.1f}",
                _pm(t, "final", "{:,.0f}"),
                _pm(t, "ann", "{:.0f}") + "%",
                _pm(t, "sharpe", "{:.2f}"),
                _pm(t, "slope_2h", "{:,.0f}"),
                _pm(t, "cancelled", "{:.0f}") + "%",
                _pm(t, "max_drawdown", "{:.0f}") + "%",
                _pm(t, "gain_pain", "{:.2f}"),
                _pm(t, "capital_hit", "{:.0f}") + "%",
                _pm(t, "edge", "{:,.0f}"),
                _pm(t, "safe_park", "{:.0f}") + "%"]

    _TOPR = 12
    _shown = _rank[:_TOPR]
    _rows = [_rrow(t, "★ " if t == _live else "") for t in _shown]
    _cls = ["reg"] * len(_shown)
    if _live in _reg_stat and _live not in _shown:      # always show where the LIVE config lands
        _rows.append(_rrow(_live, "★ "))
        _cls.append("mid")

    def _ctable(headers, rows, cls):
        """table_html plus a per-row class, so the winning band and the live row are visible without
        a column that just repeats what the banding already says."""
        h = "".join(f"<th>{x}</th>" for x in headers)
        b = "".join(f'<tr class="{c}">' + "".join(f"<td>{esc(x)}</td>" for x in r) + "</tr>"
                    for r, c in zip(rows, cls))
        return f'<table class="cfg"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'

    reg_tbl = _ctable(
        ["config &mdash; watch &middot; cap &middot; lookback &middot; drop &middot; risk "
         "&middot; trade", "score", "final (median &plusmn; SE)", "annualized", "sharpe",
         "2nd-half slope $/yr", "cancelled", "max DD",
         "gain/pain", "capital hit-rate", "edge $/exposure", "safe-park %"], _rows, _cls)

    _live_rank = (_rank.index(_live) + 1) if _live in _reg_stat else None
    _live_str = " \u00b7 ".join(str(x) for x in _live)
    payload["region"] = {
        "best": [str(x) for x in _best], "best_score": round(_score[_best], 1),
        "n_members": len(_reg_mem[_best]), "n_kept": _reg_stat[_best]["_n"],
        "live": [str(x) for x in _live], "live_rank": _live_rank,
        "live_score": round(_score[_live], 1) if _live in _score else None,
        "n_regions": len(_reg_stat), "n_metrics": len(_MET),
        "best_final": round(_reg_stat[_best]["final"][0]),
        "best_final_se": round(_reg_stat[_best]["final"][1]),
        "live_final": round(_reg_stat[_live]["final"][0]) if _live in _reg_stat else None,
        "live_final_se": round(_reg_stat[_live]["final"][1]) if _live in _reg_stat else None}

    # THE SQUARES MARK THE LIVE CONFIG'S REGION, not the top-scoring one (changed 2026-08-21).
    # They used to mark row 1. But the two sweeps on hand disagree about row 1 -- mb2's winner ranks
    # 662nd on v9 and v9's ranks 2,003rd on mb2 -- so the profile is deliberately set to the config
    # that survives BOTH (best combined rank), which is not the in-sample peak. Highlighting the peak
    # while running something else would point every scatter at a config we looked at and declined.
    # All members are drawn, the two trimmed cells included: the trim keeps one lucky or unlucky
    # neighbour out of the MEDIANS, it does not make those cells non-members.
    _markset = _live if _live in _reg_mem else _best
    _bestset = {id(c) for c in _reg_mem[_markset]}
    payload["topn"] = [i for i, c in enumerate(cells) if id(c) in _bestset]

    # THE LLM BAKE-OFF (panels 11-15). Five full re-curations that differ ONLY in which model runs the
    # event-agent JUDGMENT stage, plus a Fable-5 audit of all 2,849 decisions they made. Optional: absent
    # -> the panels are simply omitted, exactly like the max_events series.
    _bo = ROOT / "data/bakeoff_summary.json"
    bo = json.loads(_bo.read_text()) if _bo.exists() else None
    if bo:
        bo = sorted(bo, key=lambda r: r["cost"])      # ORDER BY LLM SPEND -- the x-axis of every panel
        # DISPLAY NAMES carry the VERSION, because "deepseek4" or "luna" dates badly and means nothing
        # to an outside reader. The Kimi pair is labelled by REASONING EFFORT, not by size: they are
        # the SAME 2.8T model and "kimi-low" reads as a smaller variant, which is the opposite of what
        # that arm tests (does more thinking beat a bigger model?).
        _DISP = {"deepseek4": "DeepSeek<br>V4 Flash", "minimax": "MiniMax<br>M3",
                 "luna": "GPT-5.6<br>Luna", "kimi-high": "Kimi K3<br>HIGH reasoning",
                 "kimi-low": "Kimi K3<br>LOW reasoning",
                 "grok-high": "Grok 4.3<br>HIGH reasoning", "grok-low": "Grok 4.3<br>LOW reasoning",
                 "sonnet5": "Claude<br>Sonnet 5"}
        # FALL BACK TO THE SUMMARY'S OWN `disp`, not the raw slug. This line used to end
        # `.get(r["arm"], r["arm"])`, so the three arms added after the map was written rendered as
        # "grok-low" / "sonnet5" on every axis and in the table -- the version number, and the fact
        # that the Grok pair differ only in REASONING EFFORT, both silently lost. bakeoff_summary.json
        # already carries a correct `disp`; preferring it means a new arm is named in one place.
        for r in bo:
            r["disp"] = _DISP.get(r["arm"]) or r.get("disp") or r["arm"]
    payload["bo"] = bo


    panels = "".join([
        # table-only panels: no plot div, so the render check does not report a phantom blank chart
        ('<section class="panel"><h2>1. Parameter settings</h2><p class="lead">'
         f"The {len(keys)} FREE swept knobs &mdash; every combination is a cell, "
         f"{'&times;'.join(str(len(S['grid'][k])) for k in keys)} = {len(cells)} configs &mdash; and the "
         "values considered. These knobs only RE-WEIGHT a fixed set of curator picks, which is what "
         "makes the grid free: no LLM call is made and no event is discovered or closed differently. "
         "<b>The last two rows are the exceptions</b>, and the cost column says why. "
         "<code>max_events</code> (panels 9\u201310) and <code>min_bundle_articles</code> "
         "(panel 11) are CURATION knobs: the first decides which events stay live and so which "
         "tickers ever reach the optimizer, the second decides which bundles the scout is shown "
         "as a company\u2019s news. Neither can be replayed \u2014 every value needed its own full "
         "re-curation and its own LLM bill. Every other optimizer / curator parameter is held at its "
         f"{_LINK(PROFILE_URL, 'investor_profile.backtest.md')} value."
         f'</p><div class="scroll">{param_tbl}</div></section>'),
        panel(2, "Return vs drawdown",
              "The horizontal axis is max drawdown &mdash; the book's biggest peak-to-trough loss as a "
              "fraction of its running peak; further right = deeper loss. The vertical axis is "
              "annualized return, so <b>upper-left is best</b>. Each point is one config; colour is "
              "the share of the winners' gains handed back by the losers, so a pale point in the "
              "upper-left is the whole objective at once. The live config is the purple &#9733; star.",
              "s-dd", 470),
        panel(3, "Return vs Sharpe",
              "The same cloud with <b>Sharpe on the horizontal</b> &mdash; return per unit of "
              "volatility, one of the ten measures table 8 ranks on. This is the one panel here where "
              "<b>upper-RIGHT is best</b>, since higher Sharpe is better; every other risk axis on "
              "this page reads the other way. Colour is max drawdown, pinned to the same 20&ndash;120% "
              "band as panel 4 so the two are comparable. The cloud is a tight rising diagonal "
              "&mdash; return and Sharpe correlate <b>+0.92</b> across the grid, so for most configs "
              "they say the same thing and there is no return/risk trade to agonise over. <b>The "
              "divergence is all in the tail, which is exactly where a config gets picked.</b> On "
              "this book the grid's biggest final value ($1.52M, 225%/yr) ranks only <b>1,351st of "
              "6,300 by Sharpe</b> &mdash; 79th percentile &mdash; because it earns that return on a "
              "59% drawdown. The best-Sharpe cell (1.93) makes $401K on a 24% drawdown instead. Read "
              "the top-right corner, not the top edge: a point that is high but far left is return "
              "bought with volatility a live account has to actually sit through.",
              "s-sharpe", 470),
        panel(4, "Return vs cancellation",
              "The fourth view of the same points, and the one that matters most: the horizontal axis "
              "is the share of the winners' gains handed back by the losers, so <b>upper-left is "
              "best</b> &mdash; a book that earns and keeps it. Colour is max drawdown, so a pale "
              "upper-left point earns well, keeps it, and does so without a deep hole. The cloud's "
              "shape is itself the finding: if it were a tight rising diagonal these knobs would only "
              "be trading return against cancellation, and it is not one.",
              "s-canc", 470),
        panel(5, "Return vs second-half slope",
              "Return against <b>when</b> it arrived. The horizontal is the second-half slope &mdash; "
              "(final &minus; midpoint) &divide; 1.5 years, in dollars per year &mdash; so a point far "
              "right was still compounding in the back half of the run, and a point at or left of zero "
              "made its money early and then coasted or gave it back. <b>Upper-right is best.</b> "
              "Colour is max drawdown, on the same 20&ndash;120% band as panels 2 and 4.<br><br>"
              "The cloud is a tight rising diagonal &mdash; return and slope correlate <b>+0.95</b>, "
              "tighter even than return and Sharpe &mdash; so for almost every config the two say the "
              "same thing. <b>That tightness is the finding, and it is a warning about slope, not a "
              "recommendation of it:</b> a measure this correlated with return carries almost no "
              "information return does not, which is consistent with slope ranking configs at the 53rd "
              "percentile on the re-curation transfer test, i.e. no better than random. Only <b>4 of "
              "6,300</b> cells clear 50%/yr while finishing with a negative slope, so the "
              "made-it-early-then-coasted failure this panel was built to expose barely happens on "
              "this book. <b>863 cells (14%) do have a negative slope</b>, but they are the low-return "
              "cells you would drop anyway.<br><br>"
              "Two things to read carefully. The 95 cells above $500K/yr are <b>clipped</b> at the "
              "right edge rather than allowed to flatten the rest (the maximum is $2.0M/yr). And the "
              "two axes are computed off <b>different equity curves</b> &mdash; annualized return "
              "compounds rebalance-window to rebalance-window while slope comes from the daily series, "
              "which for the live config end at $302,079 and $460,556 respectively. The rank ordering "
              "is unaffected, but do not read a ratio off this panel. Blue squares are table 8\'s "
              "winning region &mdash; 21 of its 22 members have a positive slope, median "
              "<b>$121,181</b>/yr.",
              "s-slope", 470),
        panel(6, "Return vs capital hit-rate",
              "The share of allocated capital-days that sat in tickers which ended up profitable. "
              "Capital-WEIGHTED on purpose: ten winners at 1% and one loser at 40% is not good "
              "picking, and an unweighted count would say it was. Colour is <b>cancellation</b> "
              "\u2014 what the winners handed back \u2014 so the corner you want is right and pale: "
              "the money sat in the right names AND kept the gains.",
              "s-hit", 470),
        panel(7, "Return vs edge",
              "Dollars earned per unit of exposure: total gain divided by total capital-days. It "
              "separates a book that earns a lot by HOLDING a lot from one that earns a lot per "
              "dollar-day of risk. Colour is <b>capital hit-rate</b>, so a dark dot far right earns "
              "well per unit of exposure without that exposure being in winners \u2014 which is luck, "
              "not picking.",
              "s-edge", 470),
        ('<section class="panel"><h2>8. The best region of the grid</h2><p class="lead">'
         "A config\u2019s region is itself plus every config one setting away \u2014 "
         f"{payload['region']['n_members']} in all. I drop the luckiest and unluckiest member by "
         f"final value, then report the median and standard error of the remaining "
         f"{payload['region']['n_kept']}. Each region is scored on "
         f"{payload['region']['n_metrics']} metrics, each turned into a percentile rank and "
         "averaged. Ranking neighbourhoods instead of cells means a config only wins if the "
         "settings around it work too.<br><br>"
         "<b>MEASURED 2026-08-21, and it limits everything below.</b> Three curations now exist: two "
         "at IDENTICAL settings (mb2, mb2rep) and one differing (v9). The same-config pair agrees on "
         "its top 200 at <b>1.7\u00d7 chance</b> \u2014 and so does the different-config pair. Two runs "
         "of the SAME configuration disagree about the best region exactly as much as two runs of "
         "different ones, so this ranking is driven by which news the scout happened to read, not by "
         "the settings. Each sweep\u2019s winner ranks 662nd, 1,016th, 3,231st or 5,398th on the "
         "others; the config this profile currently runs ranks 112 / 78 / <b>3,973</b>. "
         "<b>Table 8 identifies the best region WITHIN one curation. It does not identify a config "
         "that will still be good on the next one</b>, and no metric set fixes that \u2014 the "
         "instability is upstream of the scoring.<br><br>"
         "<b>The eight, in full:</b> Sharpe, gain/pain, second-half slope, capital hit-rate, edge and "
         "safe-park (higher is better), against cancellation and max drawdown (lower is better). "
         "Every one is a column, so nothing votes invisibly.<br><br>"
         "<b>capital hit-rate and edge now EXCLUDE the anchors.</b> SPY and BIL were in both "
         "denominators and distorted them in opposite directions \u2014 parking inflated hit-rate "
         "(both anchors end profitable, so idle capital counted as a WIN) and deflated edge (anchors "
         "earn $25/capital-day against the picks\u2019 $176). A config\u2019s score partly reflected "
         "how much it parked rather than how well it picked, which is the opposite of what both "
         "measure.<br><br>"
         "<b>safe-park</b> is where the anchors went instead: of the capital that did NOT end in a "
         "winner, the share parked in anchors rather than sunk in a losing pick. Parking when nothing "
         "is worth funding is a correct decision; holding a loser is a mistake, and every other metric "
         "treats them alike. It measures RESTRAINT, not picking \u2014 and it earns its place "
         "empirically: it correlates <b>\u22120.02</b> with capital hit-rate across the 6,300 cells "
         "despite sharing a denominator, and no higher than \u22120.12 with anything except max "
         "drawdown (\u22120.56). A genuinely new axis, not a restatement.<br><br>"
         "<b>Final value and annualized return are shown but do NOT vote.</b> They are the same axis "
         "as each other (+0.93 across the grid) and largely as Sharpe and slope, so scoring on them "
         "counted return three or four times and let one lucky book carry a region. What is left is "
         "one return-shape measure, two risk-adjusted, two about whether the capital sat in the right "
         "names, and two about what the book gave back. The mean is still unweighted by choice, not "
         "by derivation.</p>"
         f"{reg_tbl}</section>"),

    ] + ([panel(9, "Portfolio value vs max_events",
              "The one knob on this page that is <b>not free to sweep</b>. Everything above replays a "
              "FIXED curation through different book math, so 6,300 cells cost nothing; "
              "<code>max_events</code> decides which events stay live and so which tickers ever reach "
              "the optimizer, meaning each point here is a full re-curation "
              f"(${sum(r.get('cost_usd') or 0 for r in (me or {{}}).get('rows', [])):.2f} and several "
              "hours for the series). Bars are final portfolio value and, beside it, the gain on the "
              "six no-brainer names from panel 8 &mdash; same axis, same unit. The line is the "
              "share of events "
              "<b>culled at birth</b> &mdash; opened and retired without a single agent read, i.e. work "
              "paid for and thrown away. Read the CULL LINE first: it is a structural count the cap "
              "moves directly, while final value is one lucky name away from noise, and each point is "
              "a single stochastic sample (the scout is an LLM; two runs at the same cap would differ). "
              "A monotone trend across the six is worth something; a one-point spike in dollars is not.",
              "s-me", 460),
        panel(10, "Risk-adjusted quality vs max_events",
              "Sharpe per cap, against the <b>&gt; 0.8 floor</b> the current gate set uses. Read it "
              "beside panel 9: a cap that wins on final value while dropping below the floor has not "
              "won anything the shortlist would admit. <b>Sharpe is NOT what table 8 ranks by</b> "
              "&mdash; that is <code>robust</code>, cancellation rank + drawdown rank &mdash; and "
              "Sharpe measured at the <b>54th percentile</b> on the re-curation transfer test, i.e. a "
              "coin flip. It is kept here as a gate and a sanity column, not as a ranking.",
              "s-me-sharpe", 380),
    ] if me and me.get("rows") else []) + ([
        panel(11, "Portfolio value vs min_bundle_articles",
              "<code>min_bundle_articles</code> is the fewest articles a company bundle needs before "
              "the scout is shown it as that company\u2019s news. Below the floor the bundle is not "
              "built and its article falls to the beat or unclustered path \u2014 nothing is dropped, "
              "only reframed.",
              "s-mb", 380),
    ] if mb and mb.get("rows") else []) + ([
        panel(12, "Portfolio value vs LLM spend",
              "Final portfolio value from eight complete 3-year curations that differ only in "
              "<b><code>event_agent_model</code></b>, ordered left to right by what that model "
              "cost, with wall-clock per curation on the right axis. The shaded band is the "
              "measured noise floor: the same settings curated twice finished 1.86&times; apart.",
              "s-bo-pnl", 430),
        panel(13, "What the money actually buys: decision quality vs spend",
              "Each arm made ~565 keep-or-exit calls over its 3-year curation, graded on three "
              "tests: was the catalyst datable, did the write-up claim more than its cited sources "
              "establish, did the live/exit call contradict its own stated exit condition. The bar "
              "is the share <b>Claude Fable-5</b> judged clean on all three, estimated from a "
              "stratified sample of each arm's calls. The judge saw no prices and no outcomes, and "
              "was blind to which model produced the decision.",
              "s-bo-quality", 430),
        panel(14, "Decision quality, ranked by what the model costs",
              "Panel 16's numbers, arranged to be read on their own: dearest model at the top, "
              "cheapest at the bottom, bar length is the share of calls judged clean, and the "
              "shade is price &mdash; dark is dear. Labels give each model's cost as a multiple "
              "of the cheapest arm; the colour bar carries the dollars. <b>If spending more "
              "bought better decisions, the dark bars would be the long ones.</b> Six models "
              "rather than eight: the two arms that re-ran a model at a different reasoning "
              "effort are kept in panels 12, 14 and 15 but dropped here, where they would cost a "
              "sentence of explanation without changing the picture.",
              "s-bo-rank", 560),
        panel(15, "Where each model actually fails",
              "Panel 16's three tests, split out, same estimate. <b><code>dated</code></b> is "
              "Fable-5's verdict on the catalyst the model chose to open an event on: a specific "
              "resolvable event such as a contract award, a ruling or an FDA decision, rather than "
              "an open-ended trend like \u201cAI demand grows\u201d. <b><code>supported</code></b> "
              "is whether the write-up stayed within what its own cited sources establish. "
              "<b><code>consistent</code></b> is whether that period's live-or-exit call cohered "
              "with the model's own stated exit condition &mdash; it applies to every call, not "
              "only the exits. A call counts as clean in panel 12 only if it passes all three, which "
              "is close to but not the product of these bars &mdash; the failures overlap, since a "
              "vague catalyst tends to come with thin sourcing.",
              "s-bo-perdollar", 430),
        ('<section class="panel"><h2>16. The bake-off, in full</h2><p class="lead">'
         "Every arm, every measure, ordered by LLM spend. <b>Cancellation, drawdown and Sharpe are "
         "book behaviour; dated / supported / clean are decision quality; final value is the number "
         "that cannot be trusted alone.</b> Read the last three columns against the first: the "
         "correlation between what an arm costs and what its book returns is the thing this table "
         "exists to let you check for yourself."
         '</p><div class="scroll">'
         + table_html(["arm", "LLM $", "final value", "cancelled", "max DD", "Sharpe",
                       "FOCUS $", "events", "examined", "decisions", "dated", "supported", "clean (screen)", "overturned", "wrongly passed", "CLEAN (2-sided)"],
                      [[r.get("disp", r["arm"]).replace("<br>", " "), f"${r['cost']:.2f}", f"${r['final']:,.0f}", f"{r['cancelled']:.1f}%",
                        f"{r['max_drawdown']:.1f}%", f"{r['sharpe']:.2f}", f"${r['focus_gain']:,.0f}",
                        str(r["events"]), str(r["examined"]), str(r["decisions"]),
                        f"{r['dated']:.0f}%", f"{r['supported']:.0f}%", f"{r['clean']:.0f}%",
                        f"{r['overturn']:.0f}%", f"{r['fn_rate']:.0f}%", f"{r['clean_2s']:.0f}%"]
                       for r in bo])
         + "</div></section>"),
    ] if bo else []))

    def _slim(pl):
        """Drop the analysis-only arrays before the payload is inlined into the HTML.

        `daily_r` (753 returns per cell) and `blocks` (16x5 per cell) exist for the CSCV/PBO harness
        in scripts/pbo*.py and are never read by this page. Left in, they took docs/sbt.html to 56 MB
        -- past GitHub's file-size warning and far past what a browser should be asked to parse. A
        dashboard nobody can load is worse than one missing a panel.

        Done HERE, at serialisation, rather than by rebinding `cells` earlier: the table rows, the
        shortlist and `_pos` all key off the ORIGINAL cell objects by identity, and copying them
        mid-function silently detached the `robust` and `plateau` values computed after that point.
        """
        drop = ("daily_r", "blocks")
        return {**pl, "cells": [{k: v for k, v in c.items() if k not in drop} for c in pl["cells"]]}

    nknob1 = 1 + len(keys)          # last knob column index, for the narrow-column CSS rule
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sweep Backtest (SBT)</title>
<script src="{PLOTLY_CDN}"></script>
<style>{CSS}
.plot{{width:100%}} .scroll{{overflow-x:auto}}
/* PANEL 8's config table. Three bands -- region members, rival corners, the grid's median config --
   distinguished by a left rule and weight instead of a "region: IN/out" column, which spent a whole
   column restating the row order. Numbers right-aligned so the error bars line up and the eye can
   compare magnitudes down a column. */
table.cfg{{border-collapse:collapse;width:100%;font-size:13px;margin-top:9px}}
table.cfg th{{text-align:right;padding:5px 9px;border-bottom:1px solid var(--line);
  color:var(--text2);font-weight:600;white-space:nowrap}}
table.cfg td{{text-align:right;padding:5px 9px;border-bottom:1px solid var(--line);white-space:nowrap}}
table.cfg th:first-child,table.cfg td:first-child{{text-align:left;font-variant-numeric:tabular-nums}}
table.cfg tr.reg td{{font-weight:600;border-left:3px solid {LIGHT['s1']}}}
table.cfg tr.riv td{{color:var(--text2);border-left:3px solid transparent}}
table.cfg tr.mid td{{color:var(--text2);border-left:3px solid transparent;font-style:italic;
  border-top:1px solid var(--line)}}
/* HORIZONTAL headers, with the long knob names broken onto their underscores. Stacking
   `concentration_cap` as concentration/cap over two short lines costs less width than rotating it did,
   and reads straight on instead of at an angle. */
table.rot{{border-collapse:collapse;width:auto}}
table.rot thead th{{vertical-align:bottom;padding:0 4px 5px;border-bottom:1px solid var(--line);
  background:none}}
table.rot thead th span{{display:block;font-size:12.5px;font-weight:600;color:var(--text2);
  line-height:1.15;text-align:center;white-space:normal}}
table.rot td{{padding:3px 6px;font-size:14.5px;white-space:nowrap;text-align:right;
  border-bottom:1px solid var(--line)}}
table.rot td:first-child{{text-align:left;font-weight:600}}
table.rot tr:last-child td{{font-weight:600}}
/* KNOB columns hold 1-5 characters -- 6/0.25/45/2/3.0/0.05 -- while their headers are up to 20, so
   upright they reserved room they never use. Two tiers: the narrowest data (tier A) is squeezed
   hardest. A cell cannot go below its own glyphs, so the leading zero is dropped from decimals
   (0.25 -> .25) -- a normal compaction for a numeric table, and the only way to actually reach 50%. */
table.rot td.k, table.rot thead th.k {{ text-align:center; }}
table.rot td.kA, table.rot thead th.kA {{ padding-left:1px; padding-right:1px; }}
table.rot td.kA {{ font-size:11.5px; }}
table.rot td.kB, table.rot thead th.kB {{ padding-left:2px; padding-right:2px; }}
table.rot td.kB {{ font-size:12.5px; }}
</style></head><body><div class="wrap">
{dash_nav.render('sbt.html')}
<h1>Sweep Backtest (SBT)</h1>
<p class="sub">{len(cells)} optimizer configurations over one FIXED curation ({esc(S['run'])}) &middot;
no LLM, no re-curation &middot; knobs from {_LINK(PROFILE_URL, 'investor_profile.backtest.md')}</p>
{panels}
</div>
<script>
const DATA = {json.dumps(_slim(payload))};
const LIGHT = {json.dumps(LIGHT)}, DARK = {json.dumps(DARK)}, ST = {json.dumps(STATUS)};
const CFG = {{displayModeBar:false, responsive:true}};
function base(p, o){{ return Object.assign({{
  paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
  font:{{color:p.text2, size:11.5}}, margin:{{l:60,r:20,t:20,b:44}}}}, o); }}
function draw(){{
  const dark = matchMedia('(prefers-color-scheme: dark)').matches
            && document.documentElement.getAttribute('data-theme') !== 'light';
  const p = dark ? DARK : LIGHT;
  const C = DATA.cells, K = DATA.keys;
  const PUR = dark ? '#c084fc' : '#7c3aed';   // star marking the live config

  // PWR panels 1-3: annualized return against risk, then against each churn norm. One builder,
  // three x-axes -- they differ only in what is on the horizontal.
  const curKey = DATA.cur ? K.map(k=>DATA.cur[k]).join('|') : null;
  function scat(div, xf, xlab, xsuf, cf, clab, xmax, cmin, cmax, xmin) {{
    const isCur = c => K.map(k=>c[k]).join('|') === curKey;
    const mk = sel => ({{
      type:'scatter', mode:'markers',
      x:C.filter(sel).map(xf), y:C.filter(sel).map(c=>c.ann),
      marker: sel === isCur
        ? {{size:20, color:PUR, symbol:'star',
           line:{{width:1.5, color:p.surface}}}}
        : {{size:7, color:C.filter(sel).map(cf), colorscale:'YlOrRd', reversescale:(['Sharpe','capital hit-rate %','edge $/exposure'].includes(clab)), showscale:true,
           cmin:(cmin!==undefined?cmin:Math.min(...C.map(cf))),
           cmax:(cmax!==undefined?cmax:Math.max(...C.map(cf))),
           colorbar:{{title:{{text:clab, font:{{size:10}}}}, thickness:10}},
           line:{{width:1, color:p.surface}}}},
      text:C.filter(sel).map(c=>(sel===isCur?'<b>CURRENT CONFIG</b><br>':'')+K.map(k=>k+'='+c[k]).join('<br>')),
      hovertemplate:'%{{text}}<br>ann %{{y:.0f}}%<br>'+xlab+' %{{x:,.0f}}'+xsuf+'<extra></extra>',
      showlegend:false}});
    const tr=[mk(c=>!isCur(c))];
    // REGION MEMBERS as light-blue squares: smaller than the star and drawn UNDER it, so the live
    // config still reads first. Layer order is the whole point -- cloud, then recommendations, then you.
    const TOP = new Set(DATA.topn || []);
    const top = C.filter((c,i) => TOP.has(i) && !isCur(c));
    if (top.length) tr.push({{
      type:'scatter', mode:'markers', x:top.map(xf), y:top.map(c=>c.ann),
      marker:{{size:11, symbol:'square', color:'#7dd3fc',
               line:{{width:1.5, color:p.surface}}}},
      text:top.map(c=>'<b>IN THE REGION</b><br>'+K.map(k=>k+'='+c[k]).join('<br>')),
      hovertemplate:'%{{text}}<br>ann %{{y:.0f}}%<extra></extra>', showlegend:false}});
    if (curKey && C.some(isCur)) tr.push(mk(isCur));
    Plotly.react(div, tr, base(p, {{margin:{{l:64,r:20,t:16,b:48}},
      xaxis:{{gridcolor:p.grid, ticksuffix:xsuf, range:(xmax ? [(xmin!==undefined?xmin:0), xmax] : undefined),
             title:{{text:xlab+(div==='s-sharpe'||div==='s-slope'||div==='s-hit'||div==='s-edge'?' (HIGHER is better)':(div==='s-dd'||div==='s-canc'?' (lower is better)':' (lower = steadier)')), font:{{size:11}}}}}},
      yaxis:{{gridcolor:p.grid, ticksuffix:'%',
             title:{{text:'annualized return', font:{{size:11}}}}}}}}), CFG);
  }}
  const CANC = c=>c.cancelled, DD = c=>c.max_drawdown;
  scat('s-dd',   DD,   'max drawdown',         '%', CANC, 'cancelled %');
  // L1/L2 get SHARPE, not cancellation: neither axis carries any risk, so colour is doing real work
  // here, and Sharpe answers the question churn actually poses -- is the extra trading buying
  // risk-adjusted quality or just noise? Both panels use the same channel so the two norms stay
  // comparable at a glance.
  const SH = c=>c.sharpe;
  // Capped at 125%. Uncapped, a handful of blown-up cells (max 309% on the v11 book) squash the
  // whole decision-relevant 0-100% region into the left margin. Anything past 125% has given back
  // more than it made and fails the shortlist gate regardless, so the clip hides nothing selectable.
  // Colour scale PINNED to 20-120% drawdown rather than auto-scaled. Auto-scaling re-normalises the
  // ramp to whatever book is loaded (v11 spans 29-99%), so the same colour meant different drawdowns
  // between rebuilds and the panel could not be compared across curations. A fixed band fixes that.
  // X-axis 0-250%. Set explicitly rather than auto-ranged so the panel means the same thing across
  // rebuilds -- the cloud's position shifts between curations, and an auto axis re-centres it every
  // time, which hides exactly the drift worth seeing.
  // Return vs SHARPE. The only panel whose x-axis is better HIGHER, so the axis label is
  // switched below rather than inheriting the shared 'lower is better' suffix. Drawdown
  // colour is pinned to 20-120 to match panel 6, so the two read as one picture.
  scat('s-sharpe', SH, 'Sharpe', '', DD, 'max DD %', undefined, 20, 120);
  scat('s-canc', CANC, 'gains cancelled',      '%', DD,   'max DD %', 250, 20, 120, 0);
  // slope in $/yr: clipped at 500K (95 of 6,300 cells run past it, to $2.0M) so the bulk stays
  // readable. xmin -160K keeps the single deeply-negative cell on the page.
  scat('s-slope', c=>c.slope_2h, 'second-half slope', '', DD, 'max DD %', 500000, 20, 120, -160000);
  // DID THE CAPITAL GO WHERE THE MONEY WAS? Both x-axes measure PICKING rather than give-back, which
  // is what cancellation and drawdown cannot see: a config that funds little and risks little scores
  // well on those without ever having held a rising ticker.
  // Coloured by the OTHER half of the pair on purpose -- hit-rate against what it gave back, edge
  // against whether the exposure was in winners -- so each panel carries two independent readings
  // instead of repeating the max-DD colour a fourth time.
  scat('s-hit',  c=>c.capital_hit, 'capital hit-rate', '%', CANC, 'cancelled %', undefined, 0, 150);
  scat('s-edge', c=>c.edge, 'edge $/exposure', '', c=>c.capital_hit, 'capital hit-rate %', 1200, 30, 81, -60);

  // 5. max_events: value (bars) against cull-at-birth (line). TWO y-axes is normally forbidden, and
  // is legitimate here only because the second series is a PERCENTAGE OF A DIFFERENT THING (events
  // culled), not a second measure of the same book on a rescaled money axis -- the trap that makes
  // dual axes lie. The line is the trustworthy series and is drawn on top.
  const ME = DATA.me;
  if (ME && ME.rows && ME.rows.length) {{
    const xs = ME.rows.map(r => r.max_events === 0 ? 'uncapped' : String(r.max_events));
    Plotly.react('s-me', [
      {{type:'bar', name:'final value', x:xs, y:ME.rows.map(r=>r.final),
        marker:{{color:p.s2, line:{{width:2, color:p.surface}}}},
        text:ME.rows.map(r=>'$'+Math.round(r.final).toLocaleString()), textposition:'outside',
        textfont:{{color:p.text2, size:10}}, cliponaxis:false,
        customdata:ME.rows.map(r=>[r.events, r.agent_reads, r.funded, r.cost_usd]),
        hovertemplate:'max_events %{{x}}<br>final $%{{y:,.0f}}<br>%{{customdata[0]}} events · '+
                      '%{{customdata[1]}} agent-reads<br>%{{customdata[2]}} tickers funded · '+
                      '$%{{customdata[3]}} to curate<extra></extra>'}},
      // Same axis as final value because it is the SAME UNIT -- dollars. Total return says whether a
      // cap paid; this says whether it paid on the seven theses the strategy exists to catch, which a
      // cap that culls discoveries should damage first.
      {{type:'bar', name:'gain on the 7 shortlist names', x:xs,
        y:ME.rows.map(r=>r.focus_gain || 0),
        marker:{{color:p.s1, line:{{width:2, color:p.surface}}}},
        customdata:ME.rows.map(r=>[r.focus_held || 0]),
        hovertemplate:'max_events %{{x}}<br>shortlist $%{{y:,.0f}}<br>'+
                      '%{{customdata[0]}} of 7 names held<extra></extra>'}},
      {{type:'scatter', mode:'lines+markers', name:'culled at birth', x:xs,
        y:ME.rows.map(r=>r.cull_pct), yaxis:'y2',
        line:{{width:2, color:ST.serious}}, marker:{{size:9}},
        hovertemplate:'%{{y:.1f}}% of events culled unread<extra></extra>'}}
    ], base(p, {{barmode:'group', margin:{{l:74,r:64,t:34,b:44}},
        legend:{{orientation:'h', y:1.12, x:0, font:{{size:11}}}},
        xaxis:{{title:{{text:'max_events (events allowed live at once)', font:{{size:11}}}},
               type:'category', categoryorder:'array', categoryarray:xs}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$', title:{{text:'final portfolio value', font:{{size:11}}}}}},
        yaxis2:{{overlaying:'y', side:'right', ticksuffix:'%', range:[0,100], showgrid:false,
                 title:{{text:'events culled at birth', font:{{size:11}}}}}},
        // SPY over the same window, as the bar every cap has to clear. Without it the panel invites
        // reading the tallest bar as "good" when the question is whether ANY cap beats buy-and-hold.
        shapes:[{{type:'line', xref:'paper', x0:0, x1:1, yref:'y',
                  y0:ME.rows[0].spy, y1:ME.rows[0].spy,
                  line:{{color:p.text2, width:1.5, dash:'dash'}}}}],
        annotations:[{{xref:'paper', x:0.99, xanchor:'right', yref:'y', y:ME.rows[0].spy,
                       yanchor:'bottom', showarrow:false, font:{{size:10.5, color:p.text2}},
                       text:'SPY $'+Math.round(ME.rows[0].spy).toLocaleString()}}]}}), CFG);

    // 11. Sharpe, against the shortlist floor
    Plotly.react('s-me-sharpe', [{{
      type:'bar', x:xs, y:ME.rows.map(r=>r.sharpe||0),
      marker:{{color:ME.rows.map(r=>(r.sharpe||0) >= 1.2 ? p.s1 : ST.critical),
               line:{{width:2, color:p.surface}}}},
      text:ME.rows.map(r=>(r.sharpe||0).toFixed(2)), textposition:'outside',
      textfont:{{color:p.text2, size:11}}, cliponaxis:false,
      hovertemplate:'max_events %{{x}}<br>Sharpe %{{y:.2f}}<extra></extra>', showlegend:false}}],
      base(p, {{margin:{{l:64,r:20,t:16,b:46}},
        xaxis:{{title:{{text:'max_events', font:{{size:11}}}},
               type:'category', categoryorder:'array', categoryarray:xs}},
        yaxis:{{gridcolor:p.grid, title:{{text:'Sharpe', font:{{size:11}}}}}},
        shapes:[{{type:'line', xref:'paper', x0:0, x1:1, yref:'y', y0:1.2, y1:1.2,
                  line:{{color:ST.warning, width:1.5, dash:'dash'}}}}],
        annotations:[{{xref:'paper', x:0.99, xanchor:'right', yref:'y', y:1.2, yanchor:'bottom',
                       showarrow:false, font:{{size:10.5, color:p.text2}},
                       text:'shortlist floor 1.2'}}]}}), CFG);

  }}
  // PANEL 11 -- min_bundle_articles. Bars are the book; the SHADED BAND is the measured same-config
  // noise floor (5.8x between two curations of identical settings), drawn so the eye cannot read the
  // monotone rise as a trend. Same reason panel 12 carries the bake-off's 1.86x band.
  const MB = DATA.mb;
  if (MB && MB.rows && MB.rows.length) {{
    const xs = MB.rows.map(r => String(r.min_bundle_articles));
    const ys = MB.rows.map(r => r.final);
    const lo = Math.min(...ys), band = lo * 5.8;
    Plotly.react('s-mb', [
      {{type:'bar', name:'final portfolio value', x:xs, y:ys,
        marker:{{color:p.s1, line:{{width:2, color:p.surface}}}},
        text:ys.map(v => '$' + Math.round(v).toLocaleString()), textposition:'outside',
        cliponaxis:false, textfont:{{size:11, color:p.fg}},
        customdata:MB.rows.map(r => [r.events, r.run]),
        hovertemplate:'min_bundle_articles=%{{x}}<br>$%{{y:,.0f}}<br>'+
                      '%{{customdata[0]}} events<br>%{{customdata[1]}}<extra></extra>'}},
      {{type:'scatter', name:'SPY', x:xs, y:MB.rows.map(r => r.spy), mode:'lines',
        line:{{color:ST.warning, width:1.5, dash:'dash'}},
        hovertemplate:'SPY $%{{y:,.0f}}<extra></extra>'}}
    ], base(p, {{margin:{{l:74,r:20,t:34,b:52}}, showlegend:true,
        legend:{{orientation:'h', y:1.16, x:0, font:{{size:11}}}},
        shapes:[{{type:'rect', xref:'paper', x0:0, x1:1, yref:'y', y0:lo, y1:band,
                  fillcolor:(dark ? '#64748b' : '#cbd5e1'), opacity:0.22, line:{{width:0}},
                  layer:'below'}}],
        // AXIS PINNED $50K-$250K. $50,000 is the stake, so the floor is "did it make money at all";
        // the ceiling keeps the three bars legible. The noise band starts at the lowest book and runs
        // to 5.8x it ($574K), i.e. clean OFF THE TOP of this axis -- which is the point, and the
        // annotation says so rather than letting a clipped rectangle read as a bounded range.
        annotations:[{{xref:'paper', x:0.99, xanchor:'right', yref:'paper', y:0.97, yanchor:'top',
                       showarrow:false, font:{{size:10.5, color:p.text2}},
                       text:'shaded = same-config noise floor, 5.8\u00d7 the lowest book '+
                            '($' + Math.round(band).toLocaleString() + ') \u2014 it runs off the top'}}],
        xaxis:{{title:{{text:'min_bundle_articles', font:{{size:11}}}}, type:'category'}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$', range:[50000, 250000],
               title:{{text:'final portfolio value', font:{{size:11}}}}}}}}), CFG);
  }}
  // ---- LLM BAKE-OFF (panels 11-15) --------------------------------------------------------------
  // Already sorted by LLM spend python-side, so every panel shares one left-to-right ordering: the
  // cheapest judgment model first. Labels carry the dollar figure because the arm NAMES mean nothing
  // to a reader arriving from outside the project -- the money is the axis that travels.
  const BO = DATA.bo || [];
  if (BO.length) {{
    const nm = BO.map(r => (r.disp || r.arm) + '<br>$' + r.cost.toFixed(2));
    const cost = BO.map(r => r.cost);
    const fin = BO.map(r => r.final);
    // THE NOISE FLOOR, measured not assumed: two curations at IDENTICAL settings finished 1.86x apart.
    // Drawn as a band around the arms' midpoint so a reader sees at a glance which differences are
    // resolvable. Without it panel 12 invites exactly the over-reading it exists to prevent.
    const mid = fin.reduce((a, b) => a + b, 0) / fin.length;
    const lo = mid / Math.sqrt(1.86), hi = mid * Math.sqrt(1.86);
    // BARS, not a line. Five discrete `event_agent_model` choices are not a continuum, and the line
    // drawn here first implied an interpolation between (say) MiniMax and Luna that has no meaning.
    // Ordered by spend left-to-right, with the MODEL NAME on the mark so the panel reads standalone:
    // arm labels mean nothing to a reader arriving from outside the project, so the name and the
    // price both ride on the bar rather than hiding in a legend.
    // The noise band is a horizontal SHAPE, not a filled trace -- on a category axis a scatter-fill
    // has no continuous x to lie along and would silently vanish.
    Plotly.react('s-bo-pnl', [
      {{type:'bar', x:nm, y:fin, name:'final portfolio value',
        marker:{{color:'#7dd3fc', line:{{width:1.5, color:p.surface}}}},
        text:BO.map(r => '$' + Math.round(r.final / 1000) + 'K'), textposition:'outside',
        textfont:{{size:11, color:p.fg}}, cliponaxis:false,
        hovertext:BO.map(r => 'event_agent_model = ' + r.arm + '  (LLM $' + r.cost.toFixed(2) + ')'),
        hovertemplate:'%{{hovertext}}<br>final $%{{y:,.0f}}<extra></extra>'}},
      // WALL-CLOCK on a second axis. This is a dual-axis chart, which the house rule normally
      // forbids -- but the two series answer ONE question here ("what does this model cost me?") in
      // the two currencies that matter, dollars and hours, and splitting them would break the
      // comparison the panel exists to make. The line is deliberately thin and unfilled so the bars
      // stay the primary read.
      {{type:'scatter', mode:'lines+markers', x:nm, y:BO.map(r => r.minutes), yaxis:'y2',
        name:'wall-clock (min)', line:{{width:2, color:'#fbbf24'}},
        marker:{{size:9, color:'#fbbf24', line:{{width:1.5, color:p.surface}}}},
        hovertemplate:'%{{x}}<br>%{{y:.0f}} min to curate 3 years<extra></extra>'}}
    ], base(p, {{margin:{{l:70,r:62,t:40,b:82}}, showlegend:true,
        legend:{{orientation:'h', y:1.13, x:0, font:{{size:11}}}},
        shapes:[{{type:'rect', xref:'paper', x0:0, x1:1, yref:'y', y0:lo, y1:hi, layer:'below',
                 fillcolor:(dark ? 'rgba(148,163,184,.22)' : 'rgba(100,116,139,.16)'), line:{{width:0}}}}],
        annotations:[{{xref:'paper', x:0.995, xanchor:'right', yref:'y', y:hi, yanchor:'bottom',
                      text:'measured noise floor \u2014 the SAME settings re-run land 1.86x apart',
                      showarrow:false, font:{{size:10, color:p.fg}}}}],
        xaxis:{{type:'category',
               title:{{text:'event_agent_model', font:{{size:11}}}}}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$', range:[90000, 300000],
               title:{{text:'final portfolio value', font:{{size:11}}}}}},
        yaxis2:{{overlaying:'y', side:'right', showgrid:false, rangemode:'tozero',
                ticksuffix:' min', title:{{text:'wall-clock per curation', font:{{size:11}}}}}}}}), CFG);

    // GROUPED BARS on a category axis, matching panel 12. Two rates over five discrete models is
    // not a curve, and the earlier line invited reading a trend between points that do not connect.
    // ONE series, not two. The grey bar was the cheap screening judge's own rate, which measures
    // how wrong THAT judge was about each arm -- a property of the screen, not evidence about the
    // arm, not evidence about it (it lived in the judge-audit panel, dropped 2026-08-21). Two
    // readers in a row took grey for the swept model's
    // self-assessment and concluded the best arm was the one whose bars MATCHED, which inverts the
    // panel. Dropping it also removes the need to name the screening model here at all.
    Plotly.react('s-bo-quality', [
      {{type:'bar', x:nm, y:BO.map(r => r.clean_2s),
        marker:{{color:'#34d399', line:{{width:1.5, color:p.surface}}}},
        text:BO.map(r => r.clean_2s.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
        textfont:{{size:11, color:p.fg}},
        hovertext:BO.map(r => (r.disp || r.arm).replace('<br>', ' ') + ' - ' + r.decisions + ' calls'),
        hovertemplate:'%{{hovertext}}<br>clean %{{y:.1f}}%<extra></extra>'}}
    ], base(p, {{margin:{{l:64,r:20,t:20,b:86}}, showlegend:false,
        xaxis:{{type:'category', title:{{text:'event_agent_model', font:{{size:11}}}}, tickfont:{{size:10}}}},
        yaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[25, 75],
               title:{{text:'decisions clean on all 3 tests', font:{{size:11}}}}}}}}), CFG);

    // PANEL 18 -- the same numbers as 17, laid out for a feed rather than for the sweep. HORIZONTAL
    // bars in a tall frame: model names read left-to-right at full size instead of being rotated or
    // wrapped under a category axis, which is what makes this the shareable version.
    // COST IS ENCODED TWICE, deliberately -- position (most expensive on top) and shade (dark = dear).
    // One sequential hue, light to dark, because price is a MAGNITUDE; a categorical palette here
    // would imply the models are unordered, which is the one thing this chart is arguing they are not.
    // Reversed y so the dearest model sits at the TOP: the eye then travels down through falling price
    // and the bars get LONGER, which is the finding.
    {{
      // SIX BARS, NOT EIGHT. The two HIGH-reasoning arms are dropped from this view only -- panels 14,
      // 19 and 20 keep all eight. This is the chart meant to travel on its own, and "same model twice
      // at different reasoning effort" costs a sentence of explanation that a reader scrolling a feed
      // will not spend. With the pair gone the surviving arm needs no effort suffix either.
      const DROP = new Set(['kimi-high', 'grok-high']);
      const SHORT = {{'grok-low': 'Grok 4.3', 'kimi-low': 'Kimi K3'}};
      const byCost = BO.filter(r => !DROP.has(r.arm)).slice().sort((a, b) => a.cost - b.cost);
      const base$ = Math.min(...byCost.map(r => r.cost));          // CHEAPEST arm = the 1x anchor
      // MULTIPLES ONLY on the axis; the dollars live on the colourbar. A reader whose workload is a
      // different size can use 5.3x; they cannot use $31.66, and repeating both crowds the label.
      const lab = byCost.map(r => (SHORT[r.arm] || (r.disp || r.arm).replace('<br>', ' '))
                                  + '   ' + (r.cost / base$).toFixed(1) + 'x');
      // WARM ramp, not blue. This chart is built to be read in a LinkedIn feed, whose own chrome is
      // blue -- a blue chart there reads as part of the UI rather than as content. Amber to deep
      // orange contrasts with that surround, and hot = expensive needs no legend. Still ONE hue
      // family light-to-dark, because price is a magnitude.
      Plotly.react('s-bo-rank', [
        {{type:'bar', orientation:'h', x:byCost.map(r => r.clean_2s), y:lab,
          marker:{{color:byCost.map(r => r.cost),
                  colorscale:[[0,'#cfe3f2'],[0.28,'#8ab6da'],[0.55,'#c193ac'],[0.8,'#d05f5f'],[1,'#8f1d1d']],
                  cmin:0, cmax:Math.max(...byCost.map(r => r.cost)),
                  line:{{width:1.5, color:p.surface}},
                  colorbar:{{title:{{text:'LLM $ per<br>curation', font:{{size:10}}}}, thickness:10,
                            tickprefix:'$', len:0.55, y:0.5}}}},
          text:byCost.map(r => r.clean_2s.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
          textfont:{{size:13, color:p.fg}},
          hovertemplate:'%{{y}}<br>%{{x:.1f}}%% of calls clean<extra></extra>'}}
      ], base(p, {{margin:{{l:170, r:64, t:16, b:48}}, showlegend:false,
          xaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[0, 78],
                 title:{{text:'decisions clean on all 3 tests', font:{{size:11}}}}}},
          yaxis:{{automargin:true, tickfont:{{size:12}}}}}}), CFG);
    }}

    Plotly.react('s-bo-perdollar', [
      {{type:'bar', name:'dated', x:nm, y:BO.map(r => r.dated_adj), marker:{{color:'#fbbf24'}},
        text:BO.map(r => r.dated_adj.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
        textfont:{{size:10, color:p.fg}},
        hovertemplate:'%{{x}}<br>dated %{{y:.0f}}%<extra></extra>'}},
      {{type:'bar', name:'supported', x:nm, y:BO.map(r => r.supported_adj), marker:{{color:'#34d399'}},
        text:BO.map(r => r.supported_adj.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
        textfont:{{size:10, color:p.fg}},
        hovertemplate:'%{{x}}<br>supported %{{y:.0f}}%<extra></extra>'}},
      {{type:'bar', name:'consistent', x:nm, y:BO.map(r => r.consistent_adj), marker:{{color:'#60a5fa'}},
        text:BO.map(r => r.consistent_adj.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
        textfont:{{size:10, color:p.fg}},
        hovertemplate:'%{{x}}<br>consistent %{{y:.0f}}%<extra></extra>'}}
    ], base(p, {{barmode:'group', margin:{{l:60,r:20,t:38,b:86}},
        legend:{{orientation:'h', y:1.15, x:0, font:{{size:11}}}},
        xaxis:{{type:'category', title:{{text:'event_agent_model', font:{{size:11}}}}, tickfont:{{size:10}}}},
        yaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[35, 105],
               title:{{text:'process test pass rate (Fable-5 corrected)', font:{{size:11}}}}}}}}), CFG);

  }}
}}

draw();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
</script></body></html>"""
    out = ROOT / a.out
    out.write_text(html)
    print(f"wrote {out}  ({len(html)//1024} KB, {len(cells)} cells)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
