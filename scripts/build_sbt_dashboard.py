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
    base = S["base"]

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

    def _f(x, s="", d=2):
        return "—" if x is None else (f"{x:.{d}f}{s}")

    by_plat = sorted(cells, key=lambda c: c["plateau"])
    cols = ["plateau", "cancelled", "ann", "Sharpe", "Gain/Pain", "max DD", "L1", "final"]

    def _row(c, label):
        return ([label] + [str(c[k]) for k in keys]
                + [f"{c['plateau']:.0f}%", f"{c['cancelled']:.0f}%", _f(c.get("ann"), "%", 0),
                   _f(c.get("sharpe")), _f(c.get("gain_pain")), f"{c['max_drawdown']:.0f}%",
                   f"{c['l1']:,.0f}%", f"${c['final']:,.0f}"])
    rec_rows = [_row(c, f"#{i+1}") for i, c in enumerate(by_plat[:12])]
    if cur:
        rec_rows.append(_row(cur, "current"))
    rec = table_html(["rank"] + keys + cols, rec_rows)

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
              "churn = less noise-chasing and less overfit, making upper-left the sweet spot.",
              "s-l1", 470),
        panel(4, "Return vs L2 course correction",
              "Same idea, but the path length the book was dragged through weight-space "
              "(&radic;&Sigma;&Delta;weight&sup2; per rebalance, summed and annualized). Against L1 it "
              "weights concentrated single-name rotations more heavily. The useful read is whether the "
              "two norms rank configs alike &mdash; if they do, the churn ordering is robust rather "
              "than an artefact of which norm was picked.",
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
         "Ranked by <b>plateau</b> = &frac12;&middot;the config's own cancellation + "
         "&frac12;&middot;the mean of its grid neighbours (every cell one step away on one axis). "
         "A lone in-sample spike with weak surroundings sinks below a broad, shallow region &mdash; "
         "the anti-overfit rank, and the right one here because the best raw cell out of "
         f"{len(cells):,} on a single 3-year path is very likely noise. <b>Cancellation</b> (the share "
         "of the winners' gains the losers hand back) is the objective; <b>Sharpe</b> and "
         "<b>Gain/Pain</b> (Schwager: total return &divide; the sum of every down-move) are there to "
         "catch a config that cancels little only because it barely traded. Current live config on "
         "the bottom row."
         f'</p><div class="scroll">{rec}</div></section>'),
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

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sweep Backtest (SBT)</title>
<script src="{PLOTLY_CDN}"></script>
<style>{CSS}
.plot{{width:100%}} .scroll{{overflow-x:auto}}
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
  function scat(div, xf, xlab, xsuf, cf, clab) {{
    const isCur = c => K.map(k=>c[k]).join('|') === curKey;
    const mk = sel => ({{
      type:'scatter', mode:'markers',
      x:C.filter(sel).map(xf), y:C.filter(sel).map(c=>c.ann),
      marker: sel === isCur
        ? {{size:20, color:PUR, symbol:'star',
           line:{{width:1.5, color:p.surface}}}}
        : {{size:7, color:C.filter(sel).map(cf), colorscale:'YlOrRd', showscale:true,
           cmin:Math.min(...C.map(cf)), cmax:Math.max(...C.map(cf)),
           colorbar:{{title:{{text:clab, font:{{size:10}}}}, thickness:10}},
           line:{{width:1, color:p.surface}}}},
      text:C.filter(sel).map(c=>(sel===isCur?'<b>CURRENT CONFIG</b><br>':'')+K.map(k=>k+'='+c[k]).join('<br>')),
      hovertemplate:'%{{text}}<br>ann %{{y:.0f}}%<br>'+xlab+' %{{x:,.0f}}'+xsuf+'<extra></extra>',
      showlegend:false}});
    const tr=[mk(c=>!isCur(c))];
    if (curKey && C.some(isCur)) tr.push(mk(isCur));
    Plotly.react(div, tr, base(p, {{margin:{{l:64,r:20,t:16,b:48}},
      xaxis:{{gridcolor:p.grid, ticksuffix:xsuf,
             title:{{text:xlab+(div==='s-dd'||div==='s-canc'?' (lower is better)':' (lower = steadier)'), font:{{size:11}}}}}},
      yaxis:{{gridcolor:p.grid, ticksuffix:'%',
             title:{{text:'annualized return', font:{{size:11}}}}}}}}), CFG);
  }}
  const CANC = c=>c.cancelled, DD = c=>c.max_drawdown;
  scat('s-dd',   DD,   'max drawdown',         '%', CANC, 'cancelled %');
  scat('s-l1',   c=>c.l1, 'L1 churn',          '%', CANC, 'cancelled %');
  scat('s-l2',   c=>c.l2, 'L2 course correction', '', CANC, 'cancelled %');
  scat('s-canc', CANC, 'gains cancelled',      '%', DD,   'max DD %');

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
