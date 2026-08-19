#!/usr/bin/env python3
"""build_sbt_dashboard.py — the Sweep Backtest (SBT) dashboard: docs/sbt.html

Renders the FULL-FACTORIAL optimizer grid from scripts/sweep_optimizer.py. Zero LLM cost, zero
network: the curation is fixed and only the book math varies, so every number here is reproducible
by re-running the sweep.

Borrowed from PWR's sweep dashboard: the return-vs-drawdown frontier, the per-knob marginal panels
with the live setting called out, and a recommended-settings table. Added because a GRID makes it
possible and 1-D sweeps cannot show it: a HEATMAP over the two dominant knobs, which is where the
interactions live (concentration_cap 0.60 is harmless at max_watchlist 6 and catastrophic at 10 --
a one-at-a-time sweep reports whichever slice it happened to hold fixed).

THE HEADLINE MEASURE IS CANCELLATION, not return: the share of the winners' gains handed back by the
losers. That is the defect this sweep exists to fix, and unlike final value it is not dominated by
one lucky name.

    python scripts/build_sbt_dashboard.py --sweep data/sweep_v7.json
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
from build_fbt_dashboard import (CSS, DARK, LIGHT, PLOTLY_CDN, PROFILE_URL, STATUS,  # noqa: E402
                                 _LINK, esc, panel, table_html, tile)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep", default="data/sweep_v7.json")
    ap.add_argument("--out", default="docs/sbt.html")
    a = ap.parse_args(argv)
    S = json.loads((ROOT / a.sweep).read_text())
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

    # ---- marginals: for each knob, the distribution of cancellation at each of its values --------
    marg = {}
    for k in keys:
        d = collections.defaultdict(list)
        for c in cells:
            d[c[k]].append(c["cancelled"])
        marg[k] = {"vals": [str(v) for v in S["grid"][k]],
                   "med": [round(statistics.median(d[v]), 1) for v in S["grid"][k]],
                   "best": [round(min(d[v]), 1) for v in S["grid"][k]],
                   "base": str(base[k])}

    # ---- heatmap over the two knobs the marginals show dominating ---------------------------------
    spread = {k: max(marg[k]["med"]) - min(marg[k]["med"]) for k in keys}
    kx, ky = sorted(spread, key=lambda k: -spread[k])[:2]
    cellmap = collections.defaultdict(list)
    for c in cells:
        cellmap[(str(c[kx]), str(c[ky]))].append(c["cancelled"])
    heat = {"x": [str(v) for v in S["grid"][kx]], "y": [str(v) for v in S["grid"][ky]],
            "kx": kx, "ky": ky,
            "z": [[round(statistics.median(cellmap[(str(xv), str(yv))]), 1)
                   if cellmap[(str(xv), str(yv))] else None
                   for xv in S["grid"][kx]] for yv in S["grid"][ky]]}

    # THE max_events SERIES (panel 10), if it has been collected. Optional on purpose: it is the one
    # thing on this page that is NOT free -- max_events is a CURATION knob, so each point cost a full
    # re-curation (~$3-4.50, ~45 min) rather than a replay of fixed book math. Absent -> panel omitted.
    _me = ROOT / "data/sweep_max_events.json"
    me = json.loads(_me.read_text()) if _me.exists() else None
    # ORDER THE SERIES ONCE, HERE. max_events=0 means "uncapped", i.e. the LIMIT of the series, so it
    # belongs at the right-hand end -- sorting numerically puts it at the left where it reads as the
    # smallest cap, the exact opposite of what it is. Done at load so panel 1's table and panels 10-13
    # cannot disagree: they did, the table showing 4..20,uncapped and the plots uncapped,4..20.
    if me and me.get("rows"):
        me["rows"] = sorted(me["rows"], key=lambda r: (r["max_events"] == 0, r["max_events"]))

    payload = {"cells": cells, "keys": keys, "marg": marg, "heat": heat,
               "spy": cells[0]["spy"] if cells else 0,
               "cur": cur, "me": me}

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
    # max_events belongs in this table -- it IS swept on this page (panels 10-13) -- but it is swept
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

    # ---- ROBUST: the rank table 9 actually sorts on ------------------------------------------------
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
    #     plateau(cancellation) 83rd   $156,393   <- what table 9 used before
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
    # Rows shown in table 9 AND marked as light-blue squares in panels 2-7. Raised 30 -> 50
    # 2026-08-16: with 319 survivors the top 30 was cutting off cells that lead on PLATEAU
    # rather than Sharpe -- the table ranks by Sharpe, so a robust cell can sit well down it
    # (the [8, 0.60, 21, 0, 3.0, 0.10] block is 3rd by plateau and 37th by Sharpe, i.e. it
    # was invisible in the table AND unmarked in the scatters).
    # Raised 50 -> 100 on 2026-08-17, following the gate loosening the same day: the sixth set
    # (DD/L1/L2/cancellation, no Sharpe bar) leaves 1,787 survivors where earlier cuts left 332-599,
    # so 50 squares had shrunk to the top 2.8% of the shortlist and the scatters no longer showed
    # where the ACCEPTABLE region sits -- only its tip. 100 restores a readable band without marking
    # so much of the cloud that the highlight stops meaning anything.
    #
    # THIS CONSTANT DRIVES THREE THINGS AT ONCE and they must not be split: table 9's visible rows,
    # the `topn` payload behind the blue squares in panels 2-7, and the "show the remaining N" fold.
    # Panel 8 is deliberately NOT on it -- it marks `top5` only, so its squares mean something
    # narrower than the same-coloured squares elsewhere on the page.
    TOP_N = 100
    short = [c for c in cells
             if all(c.get(f) is not None and t(c[f]) for _, f, t, _ in GATES)]
    # RANKED BY `robust` (2026-08-17; was plateau 2026-08-16, Sharpe before that). Every cell here has
    # already cleared the gates, so the shortlist is a set of configs that are all ACCEPTABLE. The
    # question the ordering should answer is therefore not "which is best in sample" but "which is
    # likeliest to still work on the NEXT curation" -- and that question now has a measured answer
    # rather than an argued one. See the `robust` block above for the transfer test: ranking by
    # cancellation+drawdown rank puts its top 50 at the 86th percentile of the re-curation, plateau at
    # the 83rd, Sharpe at the 54th (a coin flip), and final value at the 43rd (worse than random).
    # This repo has had THREE sweep winners fail to survive the next curation, so ranking on the
    # in-sample peak is ranking on the quantity that has misled it every time.
    # The Sharpe rank is kept as its own column: where it disagrees with the order IS the overfit
    # risk, and hiding one of them would just move the blind spot.
    _sh_rank = {id(c): i + 1 for i, c in enumerate(
        sorted(short, key=lambda c: -(c["sharpe"] if c.get("sharpe") is not None else -9)))}
    short.sort(key=lambda c: c["robust"])

    # A star per column-winner AMONG THE SURVIVORS, on the four measures worth optimising. Four stars
    # rarely land on one row -- where they scatter IS the trade-off, and reading that is the point.
    stars = {}
    if short:
        stars = {"robust": min(short, key=lambda c: c["robust"]),
                 "plateau": min(short, key=lambda c: c["plateau"]),
                 "cancelled": min(short, key=lambda c: c["cancelled"]),
                 "ann": max(short, key=lambda c: c["ann"]),
                 "Sharpe": max(short, key=lambda c: (c.get("sharpe") is not None, c.get("sharpe"))),
                 # fastest to a lead it never gives back -- the only timing measure on the page
                 "months to lead": min(short, key=lambda c: (c.get("lead_months") is None,
                                                      c.get("lead_months") or 1e9)),
                 # still compounding in the back half, vs made its money early and coasted
                 "slope (per year)": max(short, key=lambda c: (c.get("slope_2h") is not None,
                                                       c.get("slope_2h") or -1e18))}
    starred = {id(v) for v in stars.values()}

    def _st(c, col):
        return " ★" if stars.get(col) is c else ""

    # The rows table 9 shows, keyed the same way the JS keys a cell, so panels 2-7 can mark exactly
    # the configs the table recommends. Without this the table and the scatters are two separate
    # arguments about the same grid and you have to hold one in your head while reading the other.
    # INDICES into `cells`, not a formatted key. Building the key python-side gave "3.0" where JSON/JS
    # gives "3", so only 2 of 20 ever matched -- a silent near-miss that LOOKED like the feature working.
    _pos = {id(c): i for i, c in enumerate(cells)}
    payload["topn"] = [_pos[id(c)] for c in short[:TOP_N] if id(c) in _pos]
    # Table 9's top 5, called out separately for panel 8. The other scatters mark all TOP_N as
    # anonymous squares; on the shortlist-gain panel the question is narrower -- do the configs this
    # page RECOMMENDS actually get paid on the no-brainer names? -- so those five are labelled with
    # their rank rather than left for the reader to hunt in a hover.
    payload["top5"] = [_pos[id(c)] for c in short[:5] if id(c) in _pos]
    # THE LLM BAKE-OFF (panels 16-19). Five full re-curations that differ ONLY in which model runs the
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
                 "kimi-low": "Kimi K3<br>LOW reasoning"}
        for r in bo:
            r["disp"] = _DISP.get(r["arm"], r["arm"])
    payload["bo"] = bo
    _ja = ROOT / "data/judge_audit.json"
    payload["ja"] = json.loads(_ja.read_text()) if _ja.exists() else None

    cols = ["#Sharpe", "robust", "plateau", "cancelled", "months to lead", "days behind",
            "slope (per year)", "ann", "Sharpe", "Gain/Pain", "max DD", "L1", "L2",
            "final"]

    def _row(c, label):
        return ([label] + [str(c[k]) for k in keys]
                + [f"{_sh_rank.get(id(c), 0)}",
                   f"{c['robust']:.0f}" + _st(c, "robust"),
                   f"{c['plateau']:.0f}%" + _st(c, "plateau"),
                   f"{c['cancelled']:.0f}%" + _st(c, "cancelled"),
                   (f"{c['lead_months']:.0f}" if c.get("lead_months") is not None else "never")
                   + _st(c, "months to lead"),
                   f"{c.get('worst_behind', 0):.0f}",
                   (f"${c['slope_2h']:,.0f}" if c.get("slope_2h") is not None else "-")
                   + _st(c, "slope (per year)"),
                   _f(c.get("ann"), "%", 0) + _st(c, "ann"),
                   _f(c.get("sharpe")) + _st(c, "Sharpe"),
                   _f(c.get("gain_pain")), f"{c['max_drawdown']:.0f}%",
                   f"{c['l1']:,.0f}%", f"{c['l2']:,.0f}", f"${c['final']:,.0f}"])
    hdr = ["rank"] + keys + cols
    top = _rot_table(hdr, [_row(c, ("★ " if id(c) in starred else "") + f"{i+1}")
                           for i, c in enumerate(short[:TOP_N])])
    rest = ""
    if len(short) > TOP_N:
        rest = (f'<details><summary>show the remaining {len(short) - TOP_N} surviving configs</summary>'
                f'<div class="scroll">'
                + _rot_table(hdr, [_row(c, ("★ " if id(c) in starred else "") + f"{i+TOP_N+1}")
                                   for i, c in enumerate(short[TOP_N:])])
                + '</div></details>')
    cur_tbl = (f'<div class="scroll">{_rot_table(hdr, [_row(cur, "current")])}</div>'
               if cur else "")
    rec = f'<div class="scroll">{top}</div>{rest}{cur_tbl}'

    panels = "".join([
        # table-only panels: no plot div, so the render check does not report a phantom blank chart
        ('<section class="panel"><h2>1. Parameter settings</h2><p class="lead">'
         f"The {len(keys)} FREE swept knobs &mdash; every combination is a cell, "
         f"{'&times;'.join(str(len(S['grid'][k])) for k in keys)} = {len(cells)} configs &mdash; and the "
         "values considered. These knobs only RE-WEIGHT a fixed set of curator picks, which is what "
         "makes the grid free: no LLM call is made and no event is discovered or closed differently. "
         "<b>max_events is the exception</b> and is listed last: it is a CURATION knob, deciding which "
         "events stay live and therefore which tickers ever reach the optimizer, so each of its values "
         "needed a full re-curation rather than a replay &mdash; see the cost column, and panels "
         "10&ndash;13. Every other optimizer / curator parameter is held at its "
         f"{_LINK(PROFILE_URL, 'investor_profile.backtest.md')} value."
         f'</p><div class="scroll">{param_tbl}</div></section>'),
        panel(2, "Return vs drawdown",
              "The horizontal axis is max drawdown &mdash; the book's biggest peak-to-trough loss as a "
              "fraction of its running peak; further right = deeper loss. The vertical axis is "
              "annualized return, so <b>upper-left is best</b>. Each point is one config; colour is "
              "the share of the winners' gains handed back by the losers, so a pale point in the "
              "upper-left is the whole objective at once. The live config is the purple &#9733; star.",
              "s-dd", 470),
        panel(3, "Return vs L1 churn",
              "Same points, risk axis replaced by <b>L1 churn</b> = annualized one-way turnover "
              "(&Sigma;|&Delta;weight| across rebalances, &times;100/yr): how much of the book is "
              "traded per year. Weights are held flat between rebalances, so drift never counts as a "
              "trade. Trading is free in an IRA, so read this as <b>stability, not cost</b>: lower "
              "churn = less noise-chasing and less overfit, making upper-left the sweet spot. Colour is "
              "<b>Sharpe</b>: a dark point at high churn is trading that actually pays for itself.",
              "s-l1", 470),
        panel(4, "Return vs L2 course correction",
              "Same idea, but the path length the book was dragged through weight-space "
              "(&radic;&Sigma;&Delta;weight&sup2; per rebalance, summed and annualized). Against L1 it "
              "weights concentrated single-name rotations more heavily. The useful read is whether the "
              "two norms rank configs alike &mdash; if they do, the churn ordering is robust rather "
              "than an artefact of which norm was picked. Coloured by <b>Sharpe</b>, as panel 3.",
              "s-l2", 470),
        panel(5, "Return vs Sharpe",
              "The same cloud with <b>Sharpe on the horizontal</b> &mdash; return per unit of "
              "volatility, the measure table 6 actually ranks by. This is the one panel here where "
              "<b>upper-RIGHT is best</b>, since higher Sharpe is better; every other risk axis on "
              "this page reads the other way. Colour is max drawdown, pinned to the same 20&ndash;120% "
              "band as panel 6 so the two are comparable. The cloud is a tight rising diagonal "
              "&mdash; return and Sharpe correlate <b>+0.92</b> across the grid, so for most configs "
              "they say the same thing and there is no return/risk trade to agonise over. <b>The "
              "divergence is all in the tail, which is exactly where a config gets picked.</b> On "
              "this book the grid's biggest final value ($1.52M, 225%/yr) ranks only <b>1,351st of "
              "6,300 by Sharpe</b> &mdash; 79th percentile &mdash; because it earns that return on a "
              "59% drawdown. The best-Sharpe cell (1.93) makes $401K on a 24% drawdown instead. Read "
              "the top-right corner, not the top edge: a point that is high but far left is return "
              "bought with volatility a live account has to actually sit through.",
              "s-sharpe", 470),
        panel(6, "Return vs cancellation",
              "The fourth view of the same points, and the one that matters most: the horizontal axis "
              "is the share of the winners' gains handed back by the losers, so <b>upper-left is "
              "best</b> &mdash; a book that earns and keeps it. Colour is max drawdown, so a pale "
              "upper-left point earns well, keeps it, and does so without a deep hole. The cloud's "
              "shape is itself the finding: if it were a tight rising diagonal these knobs would only "
              "be trading return against cancellation, and it is not one.",
              "s-canc", 470),
        panel(7, "Return vs second-half slope",
              "Return against <b>when</b> it arrived. The horizontal is the second-half slope &mdash; "
              "(final &minus; midpoint) &divide; 1.5 years, in dollars per year &mdash; so a point far "
              "right was still compounding in the back half of the run, and a point at or left of zero "
              "made its money early and then coasted or gave it back. <b>Upper-right is best.</b> "
              "Colour is max drawdown, on the same 20&ndash;120% band as panels 5 and 6.<br><br>"
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
              "is unaffected, but do not read a ratio off this panel. Blue squares are table 9\'s top "
              "100 &mdash; all 100 have a positive slope, median <b>$151,140</b>/yr.",
              "s-slope", 470),
        panel(8, "Gains on the no-brainer shortlist",
              "What each config made on seven names that were obvious in hindsight &mdash; big "
              "multi-year rises where the press named <b>dated</b> catalysts, not narrative: RKLB, "
              "DRUG, MU, BE, IREN, MP, QUBT, one per sector so nothing wins by loading a single "
              "theme. Vertical is dollars on those seven, horizontal is Sharpe, colour is "
              "cancellation &mdash; upper-right and pale earns well, keeps it, and gets paid for the "
              "obvious ones; below the zero line it <b>lost</b> money on names that rose "
              "120&ndash;3,590%. <b>No robotics</b>: our corpus carries 1 article on RCAT (+868%), 0 "
              "on UMAC (+762%) and 3 on ONDS (+699%) but 90 on PATH (&minus;1%), so that sector is a "
              "hole in our news feed, not a config failure. Blue squares are table 9's top five "
              "(rank on hover); the purple &#9733; is the live config.",
              "s-focus", 470),
        ('<section class="panel"><h2>9. Recommended settings</h2><p class="lead">'
         "The shortlist: every config clearing <b>all five gates</b> &mdash; "
         + " &middot; ".join(f"{n} {d}" for n, _, _, d in GATES) +
         f" &mdash; <b>{len(short)} of {len(cells):,}</b> survive. "
         "<b>Ranked by <code>robust</code></b> &mdash; the mean of a config's cancellation rank and "
         "its drawdown rank across the whole sweep, 0 = best. This ordering was <b>chosen by "
         "measurement, not argument</b>: the only clean test available is the noise-experiment pair "
         "(<code>me16</code> and <code>rep</code>, the same settings curated twice, differing only in "
         "LLM sampling). Ranking all 6,300 configs on one and scoring that top 50 on the other puts "
         "<b>cancellation+drawdown at the 86th percentile</b> of the re-curation, plateau at the 83rd, "
         "<b>Sharpe at the 54th &mdash; a coin flip</b> &mdash; and <b>final value at the 43rd, worse "
         "than random</b>. Ranking a shortlist by P&amp;L selects one curation's luck. The "
         "<code>plateau</code> and <code>#Sharpe</code> columns are kept beside it: where they "
         "disagree with the order is exactly where the overfit risk lives."
         "neighbours'), <b>not Sharpe</b>: every row here already passed the bars, so the question is "
         "not which is best in sample but which still works on the NEXT curation &mdash; and "
         "Sharpe-ranking put a cell at row 1 whose own 1.93 falls to <b>1.29</b> one step away, in a "
         "project that has had three sweep winners fail to survive a re-curation. The <b>#Sharpe</b> "
         "column keeps both views, because where the two disagree is where the overfit risk is. "
         "Note the live config [8, 0.25, 14, 0, 4.0, 0.20] misses by 0.0 &mdash; cancellation 20.0 "
         f"against a &lt; 20% bar; a <b>&#9733;</b> marks the best survivor per column, top {TOP_N} "
         "shown, current config on the last row."
         f'</p>{rec}</section>'),
        panel(10, f"{heat['ky']} × {heat['kx']}",
              "Median cancellation at each combination of the two knobs whose marginals span the "
              "widest range. This is the panel a 1-D sweep cannot produce, and it is where the "
              "interactions hide — a value that looks harmless on average can be the worst choice "
              "in one corner of the grid.",
              "s-heat", 420),
        panel(11, "Each knob on its own",
              "For every value of every knob: the MEDIAN cancellation across all cells holding that "
              "value (the bar) and the BEST single cell (the dot). A wide gap between them means the "
              "knob only pays in combination with something else. The live setting is outlined.",
              "s-marg", 620),
    ] + ([panel(12, "Portfolio value vs max_events",
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
        panel(13, "Risk-adjusted quality vs max_events",
              "Sharpe per cap, against the <b>&gt; 0.8 floor</b> the current gate set uses. Read it "
              "beside panel 12: a cap that wins on final value while dropping below the floor has not "
              "won anything the shortlist would admit. <b>Sharpe is NOT what table 9 ranks by</b> "
              "&mdash; that is <code>robust</code>, cancellation rank + drawdown rank &mdash; and "
              "Sharpe measured at the <b>54th percentile</b> on the re-curation transfer test, i.e. a "
              "coin flip. It is kept here as a gate and a sanity column, not as a ranking.",
              "s-me-sharpe", 380),
        panel(14, "Where the money sits, and what it gives back",
              "Three percentages on ONE axis, all of them defects: <b>max drawdown</b> (the hole the "
              "book digs), <b>cancellation</b> (winners' gains handed back by losers) and <b>idle "
              "days</b> (days holding NO position at all &mdash; the cash band in CBT plot 9). Dashed "
              "lines are the shortlist bars, DD &lt; 35% and cancellation &lt; 25%. Idle days is the "
              "one that ties this knob to the optimizer: a tight cap starves the watchlist, so the "
              "book sits in cash not because the curator ran out of theses but because it was never "
              "allowed to open them. All three are better LOW, so a cap whose three bars are all "
              "short is the one to want.",
              "s-me-risk", 400),
        panel(15, "What the cap costs, and what it buys",
              "Left bars: the <b>LLM bill for that curation</b> &mdash; the only thing on this page "
              "that is not free, since each point is a full re-curation rather than a replay. Right "
              "bars: the same money divided by <b>tickers that actually got funded</b>, which is the "
              "efficiency question &mdash; a cap that opens hundreds of events the optimizer never "
              "funds is paying the event-agent bill for reading it may as well not have done. Only "
              "the agent half of the bill scales with the cap; the scout reads the same ticker-groups "
              "either way, which is why the left bars flatten while the cap keeps rising.",
              "s-me-cost", 380),
    ] if me and me.get("rows") else []) + ([
        panel(16, "Portfolio value vs LLM spend",
              "Final portfolio value from eight complete 3-year curations that differ only in "
              "<b><code>event_agent_model</code></b>, ordered left to right by what that model "
              "cost, with wall-clock per curation on the right axis. The shaded band is the "
              "measured noise floor: the same settings curated twice finished 1.86&times; apart.",
              "s-bo-pnl", 430),
        panel(17, "What the money actually buys: decision quality vs spend",
              "Each arm made ~565 keep-or-exit calls over the 3-year curation. Every call was "
              "graded on three tests &mdash; was the catalyst datable, did the write-up claim more "
              "than its cited sources establish, did the live/exit call contradict its own stated exit "
              "condition &mdash; and a call is <b>clean</b> only if it passes all three. "
              "<b>Grey</b> is the share rated clean by a cheap screening model that read every "
              "call; <b>green</b> is that share re-estimated after <b>Claude Fable-5</b> re-read "
              "samples of the calls the screen flagged and the calls it passed. Neither judge "
              "saw a price or an outcome.",
              "s-bo-quality", 430),
        panel(18, "Where each model actually fails",
              "Panel 17's three tests, split out. <b><code>dated</code></b> asks whether the catalyst "
              "is a specific resolvable event &mdash; a contract award, a ruling, an FDA decision "
              "&mdash; rather than an open-ended trend like \u201cAI demand grows\u201d; every model "
              "is weakest here (46&ndash;66%), and since that holds across eight runs and six vendors "
              "it is my prompt at fault, not them. <b><code>supported</code></b> asks whether the "
              "write-up follows from the evidence it actually cited or claims more certainty than "
              "those sources carry &mdash; the only axis that really separates models, GPT-5.6 Luna "
              "at 97% against DeepSeek's 75%. <b><code>consistent</code></b> asks whether the "
              "live/exit call contradicts the analyst's own stated exit condition; nobody does, so at "
              "94&ndash;100% it separates nothing and can be retired.",
              "s-bo-perdollar", 430),
        ('<section class="panel"><h2>19. The bake-off, in full</h2><p class="lead">'
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
        panel(20, "Is the judge any good?",
              "The obvious objection to panels 17&ndash;19 is that an LLM graded the LLMs, so here is "
              "the grader's own audit. <b>Left bars: how often the cheap screen and Fable-5 reached "
              "the same verdict</b>, per process test. They agree 95% of the time on "
              "<code>consistent</code> (did the call contradict its own stated exit condition? &mdash; "
              "nearly mechanical), 84% on <code>dated</code>, and only <b>65% on "
              "<code>supported</code></b> &mdash; deciding whether a write-up asserts more than its "
              "citations carry is exactly the judgment a cheap model gets wrong, which is the thesis "
              "of this whole exercise showing up inside its own measurement.<br><br>"
              "<b>Right bars are the load-bearing number: how much of each arm's flagged pile Fable-5 "
              "threw out.</b> The tier-1 ranking only survives correction because those rates are "
              "SIMILAR across arms (24&ndash;33%). Had one arm been overturned at 60% and another at "
              "10%, the ordering in panel 17 would be an artefact of the cheap screen's biases rather "
              "than a real difference between models.<br><br>"
              "<b>Gold bars are why the audit had to be two-sided.</b> Re-reading only what the screen "
              "FLAGGED corrects false accusations and is blind to false clearances, so every rate would "
              "have been an upper bound. Fable-5 also read 250 decisions the screen PASSED and condemned "
              "<b>15%</b> of them &mdash; but that rate runs <b>8% to 28% by arm</b>, and correcting it "
              "moved the cheapest model from 4th place to LAST. A one-sided audit would have published "
              "the wrong ranking. <b>Caveat, stated not buried:</b> 500 of 1,673 flags and 250 of 1,164 "
              "passes were re-read, stratified per arm &mdash; enough to estimate these rates, but "
              "samples. Reading every flagged decision alone would have cost $59 against $25 budgeted.",
              "s-judge-audit", 420),
    ] if bo else []))

    nknob1 = 1 + len(keys)          # last knob column index, for the narrow-column CSS rule
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sweep Backtest (SBT)</title>
<script src="{PLOTLY_CDN}"></script>
<style>{CSS}
.plot{{width:100%}} .scroll{{overflow-x:auto}}
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
const DATA = {json.dumps(payload)};
const LIGHT = {json.dumps(LIGHT)}, DARK = {json.dumps(DARK)}, ST = {json.dumps(STATUS)};
const TOPN = {TOP_N};
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
        : {{size:7, color:C.filter(sel).map(cf), colorscale:'YlOrRd', reversescale:(clab==='Sharpe'), showscale:true,
           cmin:(cmin!==undefined?cmin:Math.min(...C.map(cf))),
           cmax:(cmax!==undefined?cmax:Math.max(...C.map(cf))),
           colorbar:{{title:{{text:clab, font:{{size:10}}}}, thickness:10}},
           line:{{width:1, color:p.surface}}}},
      text:C.filter(sel).map(c=>(sel===isCur?'<b>CURRENT CONFIG</b><br>':'')+K.map(k=>k+'='+c[k]).join('<br>')),
      hovertemplate:'%{{text}}<br>ann %{{y:.0f}}%<br>'+xlab+' %{{x:,.0f}}'+xsuf+'<extra></extra>',
      showlegend:false}});
    const tr=[mk(c=>!isCur(c))];
    // The table-9 top N, as light-blue squares: smaller than the star and drawn UNDER it, so the live
    // config still reads first. Layer order is the whole point -- cloud, then recommendations, then you.
    const TOP = new Set(DATA.topn || []);
    const top = C.filter((c,i) => TOP.has(i) && !isCur(c));
    if (top.length) tr.push({{
      type:'scatter', mode:'markers', x:top.map(xf), y:top.map(c=>c.ann),
      marker:{{size:11, symbol:'square', color:'#7dd3fc',
               line:{{width:1.5, color:p.surface}}}},
      text:top.map(c=>'<b>table-9 top '+TOPN+'</b><br>'+K.map(k=>k+'='+c[k]).join('<br>')),
      hovertemplate:'%{{text}}<br>ann %{{y:.0f}}%<extra></extra>', showlegend:false}});
    if (curKey && C.some(isCur)) tr.push(mk(isCur));
    Plotly.react(div, tr, base(p, {{margin:{{l:64,r:20,t:16,b:48}},
      xaxis:{{gridcolor:p.grid, ticksuffix:xsuf, range:(xmax ? [(xmin!==undefined?xmin:0), xmax] : undefined),
             title:{{text:xlab+(div==='s-sharpe'||div==='s-slope'?' (HIGHER is better)':(div==='s-dd'||div==='s-canc'?' (lower is better)':' (lower = steadier)')), font:{{size:11}}}}}},
      yaxis:{{gridcolor:p.grid, ticksuffix:'%',
             title:{{text:'annualized return', font:{{size:11}}}}}}}}), CFG);
  }}
  const CANC = c=>c.cancelled, DD = c=>c.max_drawdown;
  // 7. shortlist gain vs Sharpe. NOT built on scat(): that helper fixes the y-axis to annualized
  // return, and the whole point here is a different y.
  {{
    const F = C.filter(c => c.focus_gain !== undefined && c.focus_gain !== null);
    if (F.length) {{
      const isCur = c => K.map(k=>c[k]).join('|') === curKey;
      const body = F.filter(c=>!isCur(c)), me = F.filter(isCur);
      const tr = [{{
        type:'scatter', mode:'markers', x:body.map(c=>c.sharpe), y:body.map(c=>c.focus_gain),
        marker:{{size:7, color:body.map(CANC), colorscale:'YlOrRd', showscale:true, cmin:0, cmax:120,
                 colorbar:{{title:{{text:'cancelled %', font:{{size:10}}}}, thickness:10}},
                 line:{{width:1, color:p.surface}}}},
        text:body.map(c=>K.map(k=>k+'='+c[k]).join('<br>')),
        hovertemplate:'%{{text}}<br>shortlist $%{{y:,.0f}}<br>Sharpe %{{x:.2f}}<extra></extra>',
        showlegend:false}}];
      // table-9 top 5, labelled with their rank and drawn UNDER the star
      const T5 = (DATA.top5 || []).map(i => C[i]).filter(c => c && !isCur(c));
      if (T5.length) tr.push({{
        type:'scatter', mode:'markers', x:T5.map(c=>c.sharpe), y:T5.map(c=>c.focus_gain),
        marker:{{size:13, symbol:'square', color:'#7dd3fc', line:{{width:1.5, color:p.surface}}}},
        hovertext:T5.map((c,i)=>'<b>table-9 rank '+(i+1)+'</b><br>'+K.map(k=>k+'='+c[k]).join('<br>')),
        hovertemplate:'%{{hovertext}}<br>shortlist $%{{y:,.0f}}<br>Sharpe %{{x:.2f}}<extra></extra>',
        showlegend:false}});
      if (me.length) tr.push({{
        type:'scatter', mode:'markers', x:me.map(c=>c.sharpe), y:me.map(c=>c.focus_gain),
        marker:{{size:20, color:PUR, symbol:'star', line:{{width:1.5, color:p.surface}}}},
        text:me.map(c=>'<b>CURRENT CONFIG</b><br>'+K.map(k=>k+'='+c[k]).join('<br>')),
        hovertemplate:'%{{text}}<br>shortlist $%{{y:,.0f}}<extra></extra>', showlegend:false}});
      Plotly.react('s-focus', tr, base(p, {{margin:{{l:78,r:20,t:16,b:48}},
        xaxis:{{gridcolor:p.grid, title:{{text:'Sharpe (higher is better)', font:{{size:11}}}}}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$',
               title:{{text:'$ made on the 7 shortlist names', font:{{size:11}}}}}},
        // zero line: below it a config LOST money on names that rose 120-3,590%
        shapes:[{{type:'line', xref:'paper', x0:0, x1:1, yref:'y', y0:0, y1:0,
                  line:{{color:ST.critical, width:1.5, dash:'dash'}}}}]}}), CFG);
    }}
  }}
  scat('s-dd',   DD,   'max drawdown',         '%', CANC, 'cancelled %');
  // L1/L2 get SHARPE, not cancellation: neither axis carries any risk, so colour is doing real work
  // here, and Sharpe answers the question churn actually poses -- is the extra trading buying
  // risk-adjusted quality or just noise? Both panels use the same channel so the two norms stay
  // comparable at a glance.
  const SH = c=>c.sharpe;
  scat('s-l1',   c=>c.l1, 'L1 churn',          '%', SH, 'Sharpe');
  scat('s-l2',   c=>c.l2, 'L2 course correction', '', SH, 'Sharpe');
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

  // 3. the interaction the 1-D sweeps cannot show
  const H = DATA.heat;
  Plotly.react('s-heat', [{{
    type:'heatmap', x:H.x, y:H.y, z:H.z, colorscale:'YlOrRd', reversescale:false,
    colorbar:{{title:{{text:'cancelled %', font:{{size:10}}}}, thickness:10}},
    hovertemplate:H.kx+'=%{{x}}<br>'+H.ky+'=%{{y}}<br>%{{z:.0f}}% cancelled<extra></extra>'
  }}], base(p, {{margin:{{l:80,r:20,t:16,b:52}},
      xaxis:{{title:{{text:H.kx, font:{{size:11}}}}, type:'category'}},
      yaxis:{{title:{{text:H.ky, font:{{size:11}}}}, type:'category'}}}}), CFG);

  // 4. per-knob marginals, live setting outlined
  const M = DATA.marg, tr = [];
  let row = 0;
  const nk = K.length;
  K.forEach((k, i) => {{
    const m = M[k], ax = i === 0 ? '' : (i + 1);
    tr.push({{type:'bar', x:m.vals, y:m.med, name:k, xaxis:'x'+ax, yaxis:'y'+ax,
      marker:{{color:m.vals.map(v => v === m.base ? ST.warning : p.s1),
               line:{{width:2, color:p.surface}}}},
      hovertemplate:k+'=%{{x}}<br>median %{{y:.0f}}%<extra></extra>', showlegend:false}});
    tr.push({{type:'scatter', mode:'markers', x:m.vals, y:m.best, xaxis:'x'+ax, yaxis:'y'+ax,
      marker:{{size:9, color:ST.good, line:{{width:1.5, color:p.surface}}}},
      hovertemplate:k+'=%{{x}}<br>best cell %{{y:.0f}}%<extra></extra>', showlegend:false}});
  }});
  const lay = {{grid:{{rows:Math.ceil(nk/2), columns:2, pattern:'independent'}},
                margin:{{l:52,r:16,t:26,b:34}}, annotations:[]}};
  K.forEach((k,i)=>{{
    lay['xaxis'+(i===0?'':(i+1))] = {{type:'category', gridcolor:'rgba(0,0,0,0)', tickfont:{{size:10}}}};
    lay['yaxis'+(i===0?'':(i+1))] = {{gridcolor:p.grid, ticksuffix:'%', tickfont:{{size:10}}}};
  }});
  Plotly.react('s-marg', tr, base(p, lay), CFG);

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

    // 12. three DEFECT percentages, one axis (they share a unit, so no second scale is needed)
    const PCT = [['max drawdown','max_drawdown',ST.critical],
                 ['cancellation','cancelled',ST.warning],
                 ['idle days','idle_pct',p.s2]];
    Plotly.react('s-me-risk', PCT.map(([nm,f,col])=>({{
      type:'bar', name:nm, x:xs, y:ME.rows.map(r=>r[f]||0),
      marker:{{color:col, line:{{width:2, color:p.surface}}}},
      hovertemplate:nm+' %{{y:.1f}}%<extra></extra>'}})),
      base(p, {{barmode:'group', margin:{{l:64,r:20,t:34,b:46}},
        legend:{{orientation:'h', y:1.14, x:0, font:{{size:11}}}},
        xaxis:{{title:{{text:'max_events', font:{{size:11}}}},
               type:'category', categoryorder:'array', categoryarray:xs}},
        yaxis:{{gridcolor:p.grid, ticksuffix:'%', title:{{text:'percent (all better LOW)', font:{{size:11}}}}}},
        shapes:[{{type:'line', xref:'paper', x0:0, x1:1, yref:'y', y0:35, y1:35,
                  line:{{color:ST.critical, width:1.2, dash:'dash'}}}},
                {{type:'line', xref:'paper', x0:0, x1:1, yref:'y', y0:25, y1:25,
                  line:{{color:ST.warning, width:1.2, dash:'dot'}}}}]}}), CFG);

    // 13. bill, and bill per funded ticker -- both DOLLARS, so one axis is honest
    Plotly.react('s-me-cost', [
      {{type:'bar', name:'curation cost', x:xs, y:ME.rows.map(r=>r.cost_usd||0),
        marker:{{color:p.s1, line:{{width:2, color:p.surface}}}},
        hovertemplate:'$%{{y:.2f}} to curate<extra></extra>'}},
      {{type:'bar', name:'cost per funded ticker', x:xs,
        y:ME.rows.map(r=>r.funded ? (r.cost_usd||0)/r.funded : 0),
        marker:{{color:p.s2, line:{{width:2, color:p.surface}}}},
        hovertemplate:'$%{{y:.2f}} per funded ticker<extra></extra>'}}
    ], base(p, {{barmode:'group', margin:{{l:64,r:20,t:34,b:46}},
        legend:{{orientation:'h', y:1.14, x:0, font:{{size:11}}}},
        xaxis:{{title:{{text:'max_events', font:{{size:11}}}},
               type:'category', categoryorder:'array', categoryarray:xs}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$', title:{{text:'USD', font:{{size:11}}}}}}}}), CFG);
  }}
  // ---- LLM BAKE-OFF (panels 16-19) --------------------------------------------------------------
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
    // resolvable. Without it panel 16 invites exactly the over-reading it exists to prevent.
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

    // GROUPED BARS on a category axis, matching panel 16. Two rates over five discrete models is
    // not a curve, and the earlier line invited reading a trend between points that do not connect.
    Plotly.react('s-bo-quality', [
      {{type:'bar', name:'cheap screen alone', x:nm, y:BO.map(r => r.clean),
        marker:{{color:'#94a3b8', line:{{width:1.5, color:p.surface}}}},
        text:BO.map(r => r.clean.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
        textfont:{{size:10, color:p.fg}},
        hovertemplate:'%{{x}}<br>screen alone %{{y:.1f}}%<extra></extra>'}},
      {{type:'bar', name:'after Fable-5 (two-sided)', x:nm, y:BO.map(r => r.clean_2s),
        marker:{{color:'#34d399', line:{{width:1.5, color:p.surface}}}},
        text:BO.map(r => r.clean_2s.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
        textfont:{{size:11, color:p.fg}},
        hovertemplate:'%{{x}}<br>two-sided %{{y:.1f}}%<extra></extra>'}}
    ], base(p, {{barmode:'group', margin:{{l:64,r:20,t:38,b:86}},
        legend:{{orientation:'h', y:1.15, x:0, font:{{size:11}}}},
        xaxis:{{type:'category', title:{{text:'event_agent_model', font:{{size:11}}}}, tickfont:{{size:10}}}},
        yaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[25, 75],
               title:{{text:'decisions clean on all 3 process tests', font:{{size:11}}}}}}}}), CFG);

    // Five discrete choices, not a continuum -- bars on a category axis, since a line would imply an
    // interpolation between models that does not exist.
    // Three CORRECTED axes per model. The per-dollar bar is gone: cost spans 4.6x and quality 1.4x,
    // so quality/cost was essentially 1/cost -- it ranked by cheapness and crowned the model with the
    // WORST decisions, quietly contradicting panels 16-17 instead of extending them.
    // These rubric rates are stratified estimates on the same footing as panel 17's green bars:
    // P(screen passed) x Fable-5's rate in the passed sample + P(flagged) x its rate in the flagged
    // sample. Previously they were RAW screen rates sitting beside a corrected bar, mixing two
    // different measurements in one frame.
    Plotly.react('s-bo-perdollar', [
      {{type:'bar', name:'dated', x:nm, y:BO.map(r => r.dated_adj), marker:{{color:'#fbbf24'}},
        text:BO.map(r => r.dated_adj.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
        textfont:{{size:10, color:p.fg}},
        hovertemplate:'%{{x}}<br>dated %{{y:.0f}}%<extra></extra>'}},
      {{type:'bar', name:'supported', x:nm, y:BO.map(r => r.supported_adj), marker:{{color:'#f472b6'}},
        text:BO.map(r => r.supported_adj.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
        textfont:{{size:10, color:p.fg}},
        hovertemplate:'%{{x}}<br>supported %{{y:.0f}}%<extra></extra>'}},
      {{type:'bar', name:'consistent', x:nm, y:BO.map(r => r.consistent_adj), marker:{{color:'#34d399'}},
        text:BO.map(r => r.consistent_adj.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
        textfont:{{size:10, color:p.fg}},
        hovertemplate:'%{{x}}<br>consistent %{{y:.0f}}%<extra></extra>'}}
    ], base(p, {{barmode:'group', margin:{{l:60,r:20,t:38,b:86}},
        legend:{{orientation:'h', y:1.15, x:0, font:{{size:11}}}},
        xaxis:{{type:'category', title:{{text:'event_agent_model', font:{{size:11}}}}, tickfont:{{size:10}}}},
        yaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[35, 105],
               title:{{text:'process test pass rate (Fable-5 corrected)', font:{{size:11}}}}}}}}), CFG);

    // PANEL 20 -- the judge auditing itself. Two grouped series on ONE percentage axis (both are
    // rates, so no second scale is needed): agreement per rubric test, and per-arm overturn.
    const JA = DATA.ja;
    if (JA) {{
      const axk = ['consistent', 'dated', 'supported'];
      Plotly.react('s-judge-audit', [
        {{type:'bar', name:'cheap screen agrees with Fable-5', x:axk.map(k => 'test:<br>' + k),
          y:axk.map(k => JA.agree[k]), marker:{{color:'#34d399'}},
          text:axk.map(k => JA.agree[k].toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
          textfont:{{size:11, color:p.fg}},
          hovertemplate:'%{{x}}<br>agreement %{{y:.1f}}%<extra></extra>'}},
        {{type:'bar', name:'screen FLAGGED it, Fable-5 disagreed', marker:{{color:'#f472b6'}},
          x:JA.per_arm.map(r => (r.disp || r.arm).replace('<br>', ' ')),
          y:JA.per_arm.map(r => r.overturn),
          text:JA.per_arm.map(r => r.overturn.toFixed(0) + '%'), textposition:'outside',
          cliponaxis:false, textfont:{{size:11, color:p.fg}},
          hovertemplate:'%{{x}}<br>%{{y:.0f}}%% of its flags overturned<extra></extra>'}},
        {{type:'bar', name:'screen PASSED it, Fable-5 condemned it', marker:{{color:'#fbbf24'}},
          x:JA.per_arm.map(r => (r.disp || r.arm).replace('<br>', ' ')),
          y:JA.per_arm.map(r => r.fn_rate),
          text:JA.per_arm.map(r => r.fn_rate.toFixed(0) + '%'), textposition:'outside',
          cliponaxis:false, textfont:{{size:11, color:p.fg}},
          hovertemplate:'%{{x}}<br>%{{y:.0f}}%% of its passes condemned<extra></extra>'}}
      ], base(p, {{barmode:'group', margin:{{l:60,r:20,t:38,b:86}},
          legend:{{orientation:'h', y:1.15, x:0, font:{{size:11}}}},
          xaxis:{{type:'category', tickfont:{{size:10}}}},
          yaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[0, 105],
                 title:{{text:'percent', font:{{size:11}}}}}}}}), CFG);
    }}
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
