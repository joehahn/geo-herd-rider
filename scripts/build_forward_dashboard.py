#!/usr/bin/env python3
"""build_forward_dashboard.py — DRAFT forward dashboard (prototype).

Renders a single forward scan (from a --sandbox dir's scan-log + journal + archive) into a draft
HTML page under docs_preview/forward/. Panels mirror the prod gem dashboards: the decision + thesis,
an ILLUSTRATIVE value curve (the watchlist marked to market over the window from price history —
labelled illustrative, since a real forward curve needs >=2 weekly scans), the agent journal, the
firehose (pool + queries), and the model / cost. Superseded by the mini-series build (step b).

    python scripts/build_forward_dashboard.py --sandbox data/forward_proto
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import score  # noqa: E402

OUT = ROOT / "docs_preview" / "forward"

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Forward paper-trade — DRAFT ({week})</title>
<script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
<style>
 body{{font:14px/1.5 -apple-system,system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}}
 h1{{font-size:1.4rem}} h2{{font-size:1.05rem;border-bottom:1px solid #eee;padding-bottom:4px;margin-top:2rem}}
 .draft{{background:#fff6e5;border:1px solid #e0b34a;border-radius:6px;padding:8px 12px;font-size:13px}}
 .pick{{background:#f4faff;border:1px solid #cfe3f5;border-radius:6px;padding:10px 14px;margin:8px 0}}
 .pick b{{font-size:1.1rem}} table{{border-collapse:collapse;width:100%}} td,th{{text-align:left;padding:4px 8px;border-bottom:1px solid #eee;vertical-align:top}}
 .q{{color:#555;font-size:12px}} #curve{{height:340px}}
</style></head><body>
<h1>Forward paper-trade — <span style="color:#c0392b">DRAFT</span></h1>
<p class="draft"><b>Prototype.</b> Week ending {week}, curator <code>{model}</code>. The value curve below is
<b>illustrative</b> — the watchlist marked to market over the window from price history; a real forward
curve fills in as weekly scans accrue (needs ≥2). This page is a layout draft, not a result.</p>

<h2>This week's watchlist</h2>
{picks_html}

<h2>Value vs SPY <span class="q">(illustrative — retrospective mark over the {days}-day window)</span></h2>
<div id="curve"></div>

<h2>Agent journal</h2>
{journal_html}

<h2>Firehose <span class="q">({npool} in-window articles from {nraw} raw hits, {nq} searches)</span></h2>
<details><summary>search queries</summary><ul class="q">{queries_html}</ul></details>
<details><summary>pool (titles)</summary><table>{pool_html}</table></details>

<h2>Config &amp; cost</h2>
<table>{cfg_html}</table>
<script>
 Plotly.newPlot('curve', {curve_json}, {{margin:{{t:10,r:10}},yaxis:{{title:'$'}},legend:{{orientation:'h'}}}},
   {{displayModeBar:false,responsive:true}});
</script>
</body></html>"""


def _val_curve(tickers, weights, start, end, capital=50000.0):
    """Illustrative: hold `weights` over [start,end] from price history -> a $ curve per name + SPY."""
    tks = list(dict.fromkeys([*tickers, "SPY"]))
    panel = score.fetch_panel(tks, start, end, use_cache=False)
    traces = []
    # portfolio: weighted sum of the held names, normalized to `capital` at the start
    held = [t for t in tickers if t in panel and panel[t].dropna().shape[0] > 1]
    if held:
        import pandas as pd
        px = panel[held].dropna()
        w = pd.Series({t: weights.get(t, 1.0 / len(held)) for t in held})
        w = w / w.sum()
        port = (px / px.iloc[0] * w).sum(axis=1) * capital
        traces.append({"x": [d.strftime("%Y-%m-%d") for d in port.index], "y": [round(v, 0) for v in port],
                       "name": "watchlist", "line": {"color": "#c0392b", "width": 2}})
    spy = panel["SPY"].dropna()
    traces.append({"x": [d.strftime("%Y-%m-%d") for d in spy.index],
                   "y": [round(v, 0) for v in (spy / spy.iloc[0] * capital)],
                   "name": "SPY", "line": {"color": "#888", "dash": "dot"}})
    return traces


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandbox", required=True, help="forward sandbox dir (has firehose_scans.csv, journal.json, archive/)")
    ap.add_argument("--window-days", type=int, default=28)
    a = ap.parse_args(argv)
    sb = Path(a.sandbox)

    import pandas as pd
    log = pd.read_csv(sb / "firehose_scans.csv")
    week = str(log["week"].iloc[-1])
    picks = [r for _, r in log.iterrows() if str(r.get("ticker", "")).strip()]
    journal = json.loads((sb / "journal.json").read_text())
    arch = json.loads((sb / "archive" / f"{week}.json").read_text())
    cfg = arch.get("config", {})

    end = week
    start = (pd.Timestamp(week) - pd.Timedelta(days=a.window_days)).date().isoformat()
    tickers = [str(r["ticker"]).strip() for r in picks]
    curve = _val_curve(tickers, {}, start, end) if tickers else []

    picks_html = "".join(
        f'<div class="pick"><b>{r["ticker"]}</b> — conviction {r.get("conviction","?")} · '
        f'<i>{r.get("thesis","")}</i><br><span class="q">thesis_live={r.get("thesis_live")}</span></div>'
        for r in picks) or "<p>No live gems this week.</p>"

    jrows = []
    for eid, ev in journal.get("events", {}).items():
        for e in ev.get("entries", []):
            jrows.append(f"<tr><td>{e.get('date')}</td><td><b>{eid}</b> {','.join(sorted(ev['vehicles']))}</td>"
                         f"<td>live={e.get('thesis_live')} conv={e.get('conviction')}<br>{e.get('assessment','')}"
                         f"<br><span class='q'>exit: {e.get('exit_case','')}</span></td></tr>")
    journal_html = f"<table>{''.join(jrows)}</table>" if jrows else "<p>Empty journal (no live event).</p>"

    queries_html = "".join(f"<li>{q}</li>" for q in arch.get("queries", [])[:60])
    pool_html = "".join(f"<tr><td class='q'>{p.get('published_date')}</td><td>{p.get('title','')[:80]}</td></tr>"
                        for p in sorted(arch.get("pool", []), key=lambda x: x.get("published_date",""), reverse=True))
    cfg_html = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in cfg.items())

    html = PAGE.format(week=week, model=arch.get("model", "?"), days=a.window_days,
                       picks_html=picks_html, curve_json=json.dumps(curve), journal_html=journal_html,
                       npool=len(arch.get("pool", [])), nraw=len(arch.get("raw_results", [])),
                       nq=len(arch.get("queries", [])), queries_html=queries_html, pool_html=pool_html,
                       cfg_html=cfg_html)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(html)
    print(f"  wrote draft forward dashboard -> {OUT}/index.html  ({len(picks)} pick(s), curve={len(curve)} traces)")


if __name__ == "__main__":
    main()
