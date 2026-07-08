#!/usr/bin/env python3
"""build_forward_sweeps.py — non-LLM parameter sweep over a forward sandbox (deterministic; no LLM).

The events/scans are fixed, so only the optimizer re-runs. Fetches the price panel ONCE, then: (1) a 1-D
sensitivity sweep of each SWEEPS knob (others at the profile base), and (2) a coarse grid over the 5 key
sizing knobs to find the best combination. Renders a self-contained sweeps dashboard.

    python scripts/build_forward_sweeps.py --sandbox data/forward_gdelt --out docs_preview/forward_sweeps
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import pandas as pd  # noqa: E402
import firehose  # noqa: E402
import score  # noqa: E402
import build_dashboard  # noqa: E402
import build_forward_dashboard as bfd  # noqa: E402
from optimizer import load_financial_model  # noqa: E402

CAP = 50_000.0


def _final(scans, fm, panel):
    return firehose.backtest(scans, fm, CAP, panel=panel)["final"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandbox", required=True)
    ap.add_argument("--out", default=str(ROOT / "docs_preview" / "forward_sweeps"))
    a = ap.parse_args(argv)
    sb = Path(a.sandbox)
    scans = bfd._enriched_scans(pd.read_csv(sb / "firehose_scans.csv"))     # conviction-enriched, fixed events
    fm = dict(load_financial_model(str(ROOT / "investor_profile.forward.md")))
    anchors = sorted(scans)
    tickers = {score.BENCHMARK, str(fm.get("defensive_ticker", "GLD")).upper()} | \
        {p["ticker"] for a in scans for p in scans[a] if p.get("ticker")}
    start = (anchors[0] - pd.Timedelta(days=200)).strftime("%Y-%m-%d")
    end = (anchors[-1] + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
    print(f"  fetching price panel for {len(tickers)} tickers ...", flush=True)
    panel = score.fetch_panel(sorted(tickers), start, end, use_cache=False)
    spy_final = firehose.backtest(scans, fm, CAP, panel=panel)["spy_final"]
    base_final = _final(scans, fm, panel)

    # 1-D sensitivity per knob (others at profile base)
    sens = []
    for sw in build_dashboard.SWEEPS:
        k, vals = sw["key"], sw["values"]
        ys = []
        for v in vals:
            b = dict(fm); b[k] = v
            ys.append(round(_final(scans, b, panel)))
        sens.append({"key": k, "values": vals, "finals": ys, "log": sw.get("log", False),
                     "base": fm.get(k)})
        print(f"  1-D {k}: best ${max(ys):,.0f} @ {vals[ys.index(max(ys))]}", flush=True)

    # coarse grid over the 5 key sizing knobs -> best combo
    gkeys = ["concentration_cap", "risk_aversion", "spy_agent_conviction", "defensive_agent_conviction", "max_agents"]
    gvals = [[0.3, 0.5, 0.7, 1.0], [0.1, 0.5, 0.67, 1.0, 1.5], [5, 7, 8], [5, 7, 8], [2, 3, 5, 7]]
    combos = []
    for c in itertools.product(*gvals):
        b = dict(fm)
        for k, v in zip(gkeys, c):
            b[k] = v
        combos.append((dict(zip(gkeys, c)), round(_final(scans, b, panel))))
    combos.sort(key=lambda x: -x[1])
    print(f"  grid: {len(combos)} combos, best ${combos[0][1]:,.0f}", flush=True)

    # ---- render ----
    plots = []
    for s in sens:
        plots.append({
            "div": f"sw_{s['key']}", "title": s["key"], "log": s["log"],
            "trace": [{"x": s["values"], "y": s["finals"], "mode": "lines+markers",
                       "line": {"color": "#c0392b"}, "name": "final $"}],
            "base": s["base"]})
    rows = "".join(
        f"<tr><td>{i+1}</td>" + "".join(f"<td>{c[0][k]}</td>" for k in gkeys) +
        f"<td>${c[1]:,.0f}</td><td>{c[1]/CAP-1:+.1%}</td></tr>"
        for i, c in enumerate(combos[:15]))
    divs = "".join(f'<h3>{p["title"]} <span class="q">(base={p["base"]})</span></h3>'
                   f'<div id="{p["div"]}" style="height:240px"></div>' for p in plots)
    scr = ""
    for p in plots:
        xtype = 'type:"log",' if p["log"] else ''
        scr += (f'Plotly.newPlot("{p["div"]}",{json.dumps(p["trace"])},'
                f'{{margin:{{t:6,r:10,b:30,l:60}},xaxis:{{{xtype}title:"{p["title"]}"}},'
                f'yaxis:{{tickprefix:"$"}},shapes:[{{type:"line",x0:0,x1:1,xref:"paper",y0:{spy_final},y1:{spy_final},'
                f'line:{{color:"#888",dash:"dot"}}}}]}},{{displayModeBar:false,responsive:true}});')
    best = combos[0]
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Forward sweeps</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
 body{{font:14px/1.5 -apple-system,system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}}
 h1{{font-size:1.35rem}} h3{{font-size:1rem;margin:20px 0 4px;border-bottom:1px solid #eee}}
 .q{{color:#888;font-size:12px}} .kpi{{background:#f4faff;border:1px solid #cfe3f5;border-radius:8px;padding:10px 14px}}
 table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}}
 td,th{{text-align:left;padding:4px 8px;border-bottom:1px solid #eee}} th{{color:#666}}
</style></head><body>
<h1>Forward parameter sweep <span class="q">— {sb.name}, deterministic (no LLM); fixed events</span></h1>
<p class="kpi">Profile base: <b>${base_final:,.0f}</b> ({base_final/CAP-1:+.1%}) &middot; SPY <b>${spy_final:,.0f}</b> ({spy_final/CAP-1:+.1%})
 &middot; <b>Best grid combo: ${best[1]:,.0f}</b> ({best[1]/CAP-1:+.1%}) @ {best[0]}</p>
<p class="q">Dotted gray line = SPY. 1-D sweeps vary one knob with the rest at the profile base.</p>
{divs}
<h3>Top 15 combinations <span class="q">(grid over 5 sizing knobs)</span></h3>
<table><tr><th>#</th>{''.join(f'<th>{k}</th>' for k in gkeys)}<th>final</th><th>ret</th></tr>{rows}</table>
<script>{scr}</script></body></html>"""
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html)
    print(f"  wrote {out}/index.html")


if __name__ == "__main__":
    main()
