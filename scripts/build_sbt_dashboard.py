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

    payload = {"cells": cells, "keys": keys, "marg": marg, "heat": heat,
               "spy": cells[0]["spy"] if cells else 0,
               "cur": cur}

    # What was swept, and what the profile currently says -- PWR's "Parameter settings" panel. The
    # `current` column is what makes it readable: without it the grid is a list of numbers with no
    # indication of where we actually stand in it.
    ps_rows = [[k, ", ".join(str(v) for v in S["grid"][k]), str(base[k])] for k in keys]
    param_tbl = table_html(["parameter", "values swept", "current (profile)"], ps_rows)

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
    GATES = [("max DD", "max_drawdown", lambda v: v < 65, "< 65%"),
             ("L1", "l1", lambda v: v < 1800, "< 1800%/yr"),
             ("L2", "l2", lambda v: v < 1100, "< 1100/yr"),
             ("cancelled", "cancelled", lambda v: v < 60, "< 60%")]
    TOP_N = 30                      # rows shown in table 6 AND marked as squares in panels 2-5
    short = [c for c in cells
             if all(c.get(f) is not None and t(c[f]) for _, f, t, _ in GATES)]
    # Ranked by SHARPE, not plateau. Plateau ranks on CANCELLATION, and cancellation alone selects
    # for a book that barely trades -- the lowest-cancellation cell is dominated on return, Sharpe AND
    # drawdown by cells further up the grid. Plateau stays as a column, so a lone spike is still visible.
    short.sort(key=lambda c: -(c["sharpe"] if c.get("sharpe") is not None else -9))

    # A star per column-winner AMONG THE SURVIVORS, on the four measures worth optimising. Four stars
    # rarely land on one row -- where they scatter IS the trade-off, and reading that is the point.
    stars = {}
    if short:
        stars = {"plateau": min(short, key=lambda c: c["plateau"]),
                 "cancelled": min(short, key=lambda c: c["cancelled"]),
                 "ann": max(short, key=lambda c: c["ann"]),
                 "Sharpe": max(short, key=lambda c: (c.get("sharpe") is not None, c.get("sharpe")))}
    starred = {id(v) for v in stars.values()}

    def _st(c, col):
        return " ★" if stars.get(col) is c else ""

    # The 20 rows table 6 shows, keyed the same way the JS keys a cell, so panels 2-5 can mark exactly
    # the configs the table recommends. Without this the table and the scatters are two separate
    # arguments about the same grid and you have to hold one in your head while reading the other.
    # INDICES into `cells`, not a formatted key. Building the key python-side gave "3.0" where JSON/JS
    # gives "3", so only 2 of 20 ever matched -- a silent near-miss that LOOKED like the feature working.
    _pos = {id(c): i for i, c in enumerate(cells)}
    payload["top20"] = [_pos[id(c)] for c in short[:TOP_N] if id(c) in _pos]

    cols = ["plateau", "cancelled", "ann", "Sharpe", "Gain/Pain", "max DD", "L1", "L2", "final"]

    def _row(c, label):
        return ([label] + [str(c[k]) for k in keys]
                + [f"{c['plateau']:.0f}%" + _st(c, "plateau"),
                   f"{c['cancelled']:.0f}%" + _st(c, "cancelled"),
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
                + _rot_table(hdr, [_row(c, ("★ " if id(c) in starred else "") + f"{i+21}")
                                   for i, c in enumerate(short[20:])])
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
         "Every other optimizer / curator parameter is held at its "
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
        panel(5, "Return vs cancellation",
              "The fourth view of the same points, and the one that matters most: the horizontal axis "
              "is the share of the winners' gains handed back by the losers, so <b>upper-left is "
              "best</b> &mdash; a book that earns and keeps it. Colour is max drawdown, so a pale "
              "upper-left point earns well, keeps it, and does so without a deep hole. The cloud's "
              "shape is itself the finding: if it were a tight rising diagonal these knobs would only "
              "be trading return against cancellation, and it is not one.",
              "s-canc", 470),
        ('<section class="panel"><h2>6. Recommended settings</h2><p class="lead">'
         "The shortlist: every config that clears <b>all five gates read off panels 2&ndash;5</b> "
         + " &middot; ".join(f"{n} {d}" for n, _, _, d in GATES) +
         f" &mdash; <b>{len(short)} of {len(cells):,}</b> survive, <b>ranked by Sharpe</b>. There is "
         "deliberately NO churn gate: median Sharpe RISES monotonically with churn on this book "
         "(0.73 below L2 500, 1.17 above 900), so an L1/L2 ceiling was selecting for a book that "
         "barely trades. Trading is free in an IRA, so churn was only ever a proxy for drawdown "
         "&mdash; which is gated directly instead. <b>plateau</b> = "
         "&frac12;&middot;the config's own cancellation + &frac12;&middot;the mean of its grid "
         "neighbours, so a lone in-sample spike with weak surroundings still shows up. "
         "A <b>&#9733;</b> marks the survivor that is best on that column (plateau, cancellation, "
         "annualized return, Sharpe). Those four stars rarely land on one row &mdash; where they "
         "scatter IS the trade-off, and a config carrying two of them is the honest compromise. "
         f"Top {TOP_N} shown; the rest expand below. Current live config on the last row."
         f'</p>{rec}</section>'),
        panel(7, f"{heat['ky']} × {heat['kx']}",
              "Median cancellation at each combination of the two knobs whose marginals span the "
              "widest range. This is the panel a 1-D sweep cannot produce, and it is where the "
              "interactions hide — a value that looks harmless on average can be the worst choice "
              "in one corner of the grid.",
              "s-heat", 420),
        panel(8, "Each knob on its own",
              "For every value of every knob: the MEDIAN cancellation across all cells holding that "
              "value (the bar) and the BEST single cell (the dot). A wide gap between them means the "
              "knob only pays in combination with something else. The live setting is outlined.",
              "s-marg", 620),
    ])

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
    // The table-6 top 20, as light-blue squares: smaller than the star and drawn UNDER it, so the live
    // config still reads first. Layer order is the whole point -- cloud, then recommendations, then you.
    const TOP = new Set(DATA.top20 || []);
    const top = C.filter((c,i) => TOP.has(i) && !isCur(c));
    if (top.length) tr.push({{
      type:'scatter', mode:'markers', x:top.map(xf), y:top.map(c=>c.ann),
      marker:{{size:11, symbol:'square', color:'#7dd3fc',
               line:{{width:1.5, color:p.surface}}}},
      text:top.map(c=>'<b>table-6 top 20</b><br>'+K.map(k=>k+'='+c[k]).join('<br>')),
      hovertemplate:'%{{text}}<br>ann %{{y:.0f}}%<extra></extra>', showlegend:false}});
    if (curKey && C.some(isCur)) tr.push(mk(isCur));
    Plotly.react(div, tr, base(p, {{margin:{{l:64,r:20,t:16,b:48}},
      xaxis:{{gridcolor:p.grid, ticksuffix:xsuf, range:(xmax ? [(xmin!==undefined?xmin:0), xmax] : undefined),
             title:{{text:xlab+(div==='s-dd'||div==='s-canc'?' (lower is better)':' (lower = steadier)'), font:{{size:11}}}}}},
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
