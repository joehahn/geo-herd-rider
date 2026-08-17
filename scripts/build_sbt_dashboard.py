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
    # `base` = where the LIVE profile sits in the grid (the star in panels 2-5, the "current" row).
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

    # ---- SHORTLIST: read the gates straight off panels 2-5 ---------------------------------------
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
    # RE-CUT 2026-08-17 (fourth set, on the v8 book): cancelled < 40%, Sharpe > 0.9, DD < 60%,
    # L1 > 2050%/yr, L2 < 1250/yr, no-brainer gains > $50K. 332 of 6,300 survive.
    #
    # CANCELLATION IS THE BINDING GATE, and it changes what this shortlist selects for. At < 40% it
    # admits 12.1% of the grid on its own -- tighter than every other bar and tighter than the $50K
    # no-brainer floor (22.2%). An earlier set inverted this: its loose < 60% cancellation passed 53%
    # and let the FOCUS floor do the cutting. So these survivors are chosen first for LOW GIVE-BACK
    # (little of the gross gain handed back by losing positions) and only second for riding the focus
    # names. Sharpe, DD, L1 and L2 are near-inert here -- together they take 765 to 554.
    #
    # THE $50K FLOOR SURVIVED THE FOCUS RE-CUT UNCHANGED, which is luck rather than design. FOCUS was
    # cut from seven names to six the same day (see sweep_optimizer.FOCUS: DRUG and BE were never
    # named by the curator, MP returned +141% against +1392% alternatives). That re-scan moved every
    # focus_gain -- the live config went $56,312 -> $65,869 on 5 of 6 names held -- yet the share of
    # the grid clearing $50K barely moved, 22.3% -> 22.2%. The floor did NOT need recalibrating.
    #
    # THE LIVE CONFIG [8, 0.25, 21, 0, 4.00, 0.10] PASSES ALL SIX (cancellation 35.6, Sharpe 1.81,
    # DD 31.1, L1 2230, L2 806, FOCUS $65,869 on 5 of 6 names).
    GATES = [("cancelled", "cancelled", lambda v: v < 40, "&lt; 40%"),
             ("Sharpe", "sharpe", lambda v: v > 0.9, "&gt; 0.9"),
             ("max DD", "max_drawdown", lambda v: v < 60, "&lt; 60%"),
             ("L1", "l1", lambda v: v > 2050, "&gt; 2050%/yr"),
             ("L2", "l2", lambda v: v < 1250, "&lt; 1250/yr"),
             ("no-brainer $", "focus_gain", lambda v: v > 50_000, "&gt; $50K")]
    # Rows shown in table 8 AND marked as light-blue squares in panels 2-6. Raised 30 -> 50
    # 2026-08-16: with 319 survivors the top 30 was cutting off cells that lead on PLATEAU
    # rather than Sharpe -- the table ranks by Sharpe, so a robust cell can sit well down it
    # (the [8, 0.60, 21, 0, 3.0, 0.10] block is 3rd by plateau and 37th by Sharpe, i.e. it
    # was invisible in the table AND unmarked in the scatters).
    TOP_N = 50
    short = [c for c in cells
             if all(c.get(f) is not None and t(c[f]) for _, f, t, _ in GATES)]
    # RANKED BY PLATEAU (2026-08-16), not Sharpe. Every cell here has already cleared five quality
    # bars -- Sharpe > 1.2, DD < 40%, cancellation < 20%, both churn bands -- so the shortlist is a set
    # of configs that are all ACCEPTABLE. The question the ordering should answer is therefore not
    # "which is best in sample" but "which is likeliest to still work on the next curation", and that
    # is what plateau estimates: half a config's own cancellation, half its grid neighbours'.
    # Sharpe-ranking put [6, 0.40, 21, 0, 4.0, 0.30] at row 1 on a Sharpe of 1.93 whose immediate
    # neighbours average 1.29 -- a knife-edge cell at the top of the list that decides configs. This
    # repo has had THREE sweep winners fail to survive the next curation, so ranking on the in-sample
    # peak is ranking on the quantity that has misled it every time.
    # The Sharpe rank is kept as its own column: where the two disagree IS the overfit risk, and
    # hiding one of them would just move the blind spot.
    _sh_rank = {id(c): i + 1 for i, c in enumerate(
        sorted(short, key=lambda c: -(c["sharpe"] if c.get("sharpe") is not None else -9)))}
    short.sort(key=lambda c: c["plateau"])

    # A star per column-winner AMONG THE SURVIVORS, on the four measures worth optimising. Four stars
    # rarely land on one row -- where they scatter IS the trade-off, and reading that is the point.
    stars = {}
    if short:
        stars = {"plateau": min(short, key=lambda c: c["plateau"]),
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

    # The 20 rows table 6 shows, keyed the same way the JS keys a cell, so panels 2-5 can mark exactly
    # the configs the table recommends. Without this the table and the scatters are two separate
    # arguments about the same grid and you have to hold one in your head while reading the other.
    # INDICES into `cells`, not a formatted key. Building the key python-side gave "3.0" where JSON/JS
    # gives "3", so only 2 of 20 ever matched -- a silent near-miss that LOOKED like the feature working.
    _pos = {id(c): i for i, c in enumerate(cells)}
    payload["topn"] = [_pos[id(c)] for c in short[:TOP_N] if id(c) in _pos]
    # Table 8's top 5, called out separately for panel 7. The other scatters mark all TOP_N as
    # anonymous squares; on the shortlist-gain panel the question is narrower -- do the configs this
    # page RECOMMENDS actually get paid on the no-brainer names? -- so those five are labelled with
    # their rank rather than left for the reader to hunt in a hover.
    payload["top5"] = [_pos[id(c)] for c in short[:5] if id(c) in _pos]

    cols = ["#Sharpe", "plateau", "cancelled", "months to lead", "days behind",
            "slope (per year)", "ann", "Sharpe", "Gain/Pain", "max DD", "L1", "L2",
            "final"]

    def _row(c, label):
        return ([label] + [str(c[k]) for k in keys]
                + [f"{_sh_rank.get(id(c), 0)}",
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
        panel(7, "Gains on the no-brainer shortlist",
              "What each config made on seven names that were obvious in hindsight &mdash; big "
              "multi-year rises where the press named <b>dated</b> catalysts, not narrative: RKLB, "
              "DRUG, MU, BE, IREN, MP, QUBT, one per sector so nothing wins by loading a single "
              "theme. Vertical is dollars on those seven, horizontal is Sharpe, colour is "
              "cancellation &mdash; upper-right and pale earns well, keeps it, and gets paid for the "
              "obvious ones; below the zero line it <b>lost</b> money on names that rose "
              "120&ndash;3,590%. <b>No robotics</b>: our corpus carries 1 article on RCAT (+868%), 0 "
              "on UMAC (+762%) and 3 on ONDS (+699%) but 90 on PATH (&minus;1%), so that sector is a "
              "hole in our news feed, not a config failure. Blue squares are table 8's top five "
              "(rank on hover); the purple &#9733; is the live config.",
              "s-focus", 470),
        ('<section class="panel"><h2>8. Recommended settings</h2><p class="lead">'
         "The shortlist: every config clearing <b>all six gates</b> &mdash; "
         + " &middot; ".join(f"{n} {d}" for n, _, _, d in GATES) +
         f" &mdash; <b>{len(short)} of {len(cells):,}</b> survive. "
         "<b>Ranked by plateau</b> (&frac12; a config's own cancellation + &frac12; its grid "
         "neighbours'), <b>not Sharpe</b>: every row here already passed the bars, so the question is "
         "not which is best in sample but which still works on the NEXT curation &mdash; and "
         "Sharpe-ranking put a cell at row 1 whose own 1.93 falls to <b>1.29</b> one step away, in a "
         "project that has had three sweep winners fail to survive a re-curation. The <b>#Sharpe</b> "
         "column keeps both views, because where the two disagree is where the overfit risk is. "
         "Note the live config [8, 0.25, 14, 0, 4.0, 0.20] misses by 0.0 &mdash; cancellation 20.0 "
         f"against a &lt; 20% bar; a <b>&#9733;</b> marks the best survivor per column, top {TOP_N} "
         "shown, current config on the last row."
         f'</p>{rec}</section>'),
        panel(9, f"{heat['ky']} × {heat['kx']}",
              "Median cancellation at each combination of the two knobs whose marginals span the "
              "widest range. This is the panel a 1-D sweep cannot produce, and it is where the "
              "interactions hide — a value that looks harmless on average can be the worst choice "
              "in one corner of the grid.",
              "s-heat", 420),
        panel(10, "Each knob on its own",
              "For every value of every knob: the MEDIAN cancellation across all cells holding that "
              "value (the bar) and the BEST single cell (the dot). A wide gap between them means the "
              "knob only pays in combination with something else. The live setting is outlined.",
              "s-marg", 620),
    ] + ([panel(11, "Portfolio value vs max_events",
              "The one knob on this page that is <b>not free to sweep</b>. Everything above replays a "
              "FIXED curation through different book math, so 6,300 cells cost nothing; "
              "<code>max_events</code> decides which events stay live and so which tickers ever reach "
              "the optimizer, meaning each point here is a full re-curation "
              f"(${sum(r.get('cost_usd') or 0 for r in (me or {{}}).get('rows', [])):.2f} and several "
              "hours for the series). Bars are final portfolio value and, beside it, the gain on the "
              "six no-brainer names from panel 7 &mdash; same axis, same unit. The line is the "
              "share of events "
              "<b>culled at birth</b> &mdash; opened and retired without a single agent read, i.e. work "
              "paid for and thrown away. Read the CULL LINE first: it is a structural count the cap "
              "moves directly, while final value is one lucky name away from noise, and each point is "
              "a single stochastic sample (the scout is an LLM; two runs at the same cap would differ). "
              "A monotone trend across the six is worth something; a one-point spike in dollars is not.",
              "s-me", 460),
        panel(12, "Risk-adjusted quality vs max_events",
              "Sharpe per cap, with the shortlist's <b>&gt; 1.2 floor</b> drawn in. This is the panel "
              "to weigh against 10, because Sharpe is what table 8 actually ranks by and what the "
              "live config was chosen on &mdash; a cap that wins on final value while dropping below "
              "the floor has not won anything we would deploy. Bars under the line are configs the "
              "shortlist would refuse regardless of how much money they made.",
              "s-me-sharpe", 380),
        panel(13, "Where the money sits, and what it gives back",
              "Three percentages on ONE axis, all of them defects: <b>max drawdown</b> (the hole the "
              "book digs), <b>cancellation</b> (winners' gains handed back by losers) and <b>idle "
              "days</b> (days holding NO position at all &mdash; the cash band in CBT plot 9). Dashed "
              "lines are the shortlist bars, DD &lt; 35% and cancellation &lt; 25%. Idle days is the "
              "one that ties this knob to the optimizer: a tight cap starves the watchlist, so the "
              "book sits in cash not because the curator ran out of theses but because it was never "
              "allowed to open them. All three are better LOW, so a cap whose three bars are all "
              "short is the one to want.",
              "s-me-risk", 400),
        panel(14, "What the cap costs, and what it buys",
              "Left bars: the <b>LLM bill for that curation</b> &mdash; the only thing on this page "
              "that is not free, since each point is a full re-curation rather than a replay. Right "
              "bars: the same money divided by <b>tickers that actually got funded</b>, which is the "
              "efficiency question &mdash; a cap that opens hundreds of events the optimizer never "
              "funds is paying the event-agent bill for reading it may as well not have done. Only "
              "the agent half of the bill scales with the cap; the scout reads the same ticker-groups "
              "either way, which is why the left bars flatten while the cap keeps rising.",
              "s-me-cost", 380),
    ] if me and me.get("rows") else []))

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
    // The table-8 top N, as light-blue squares: smaller than the star and drawn UNDER it, so the live
    // config still reads first. Layer order is the whole point -- cloud, then recommendations, then you.
    const TOP = new Set(DATA.topn || []);
    const top = C.filter((c,i) => TOP.has(i) && !isCur(c));
    if (top.length) tr.push({{
      type:'scatter', mode:'markers', x:top.map(xf), y:top.map(c=>c.ann),
      marker:{{size:11, symbol:'square', color:'#7dd3fc',
               line:{{width:1.5, color:p.surface}}}},
      text:top.map(c=>'<b>table-8 top '+TOPN+'</b><br>'+K.map(k=>k+'='+c[k]).join('<br>')),
      hovertemplate:'%{{text}}<br>ann %{{y:.0f}}%<extra></extra>', showlegend:false}});
    if (curKey && C.some(isCur)) tr.push(mk(isCur));
    Plotly.react(div, tr, base(p, {{margin:{{l:64,r:20,t:16,b:48}},
      xaxis:{{gridcolor:p.grid, ticksuffix:xsuf, range:(xmax ? [(xmin!==undefined?xmin:0), xmax] : undefined),
             title:{{text:xlab+(div==='s-sharpe'?' (HIGHER is better)':(div==='s-dd'||div==='s-canc'?' (lower is better)':' (lower = steadier)')), font:{{size:11}}}}}},
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
      // table-8 top 5, labelled with their rank and drawn UNDER the star
      const T5 = (DATA.top5 || []).map(i => C[i]).filter(c => c && !isCur(c));
      if (T5.length) tr.push({{
        type:'scatter', mode:'markers', x:T5.map(c=>c.sharpe), y:T5.map(c=>c.focus_gain),
        marker:{{size:13, symbol:'square', color:'#7dd3fc', line:{{width:1.5, color:p.surface}}}},
        hovertext:T5.map((c,i)=>'<b>table-8 rank '+(i+1)+'</b><br>'+K.map(k=>k+'='+c[k]).join('<br>')),
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
