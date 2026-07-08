#!/usr/bin/env python3
"""build_forward_dashboard.py — forward dashboard (prototype).

Renders a forward sandbox (scan-log + journal + archive) into docs_preview/forward/. The TOP plot is
the RECOMMENDED-PORTFOLIO equity curve vs SPY — `firehose.backtest` on the accumulated scan log, which
always includes the two always-on agents (SPY + the defensive floor), so there's a real portfolio every
week even with zero scout gems. Below: the weekly recommendations (optimizer weights), the active
agents, the agent journal, the firehose (pool + queries), and config/cost.

    python scripts/build_forward_dashboard.py --sandbox data/forward_proto
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pandas as pd  # noqa: E402
import firehose  # noqa: E402
import forward  # noqa: E402
from optimizer import load_financial_model  # noqa: E402

OUT = ROOT / "docs_preview" / "forward"

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Forward paper-trade ({week})</title>
<script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
<style>
 body{{font:14px/1.5 -apple-system,system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}}
 h1{{font-size:1.4rem}} h2{{font-size:1.05rem;border-bottom:1px solid #eee;padding-bottom:4px;margin-top:2rem}}
 .draft{{background:#fff6e5;border:1px solid #e0b34a;border-radius:6px;padding:8px 12px;font-size:13px}}
 .pick{{background:#f4faff;border:1px solid #cfe3f5;border-radius:6px;padding:10px 14px;margin:8px 0}}
 table{{border-collapse:collapse;width:100%}} td,th{{text-align:left;padding:4px 8px;border-bottom:1px solid #eee;vertical-align:top}}
 .q{{color:#555;font-size:12px}} #curve{{height:360px}} .kpi{{font-size:1.1rem}}
</style></head><body>
<h1>Forward paper-trade — week ending {week}</h1>
<p class="draft"><b>Prototype</b> on {nweeks} weeks of sandboxed news (curator <code>{model}</code>). Paper only;
the recommended portfolio auto-follows the optimizer (test mode A).</p>

<h2>Recommended-portfolio value vs SPY</h2>
<p class="kpi">${final:,.0f} <span class="q">from ${cap:,.0f} ({ret:+.1%}) · SPY {spyret:+.1%}</span></p>
<div id="curve"></div>

<h2>Published-date distribution <span class="q">(articles/day across all weeks' pools — spikes on the week-ending Fridays = end-of-week clustering)</span></h2>
<div id="datehist"></div>

<h2>Weekly recommendations <span class="q">(optimizer weights — SPY + defensive floor always on)</span></h2>
<table><tr><th>week</th><th>recommended portfolio (weights)</th><th>week return</th></tr>{recs_html}</table>

<h2>Active agents</h2>
{agents_html}

<h2>Agent journal</h2>
{journal_html}

<h2>Firehose <span class="q">(latest week: {npool} in-window articles, {nq} searches)</span></h2>
<details><summary>search queries</summary><ul class="q">{queries_html}</ul></details>
<details><summary>pool (titles)</summary><table>{pool_html}</table></details>

<h2>Config &amp; cost</h2>
<table>{cfg_html}</table>
<script>
 Plotly.newPlot('curve', {curve_json}, {{margin:{{t:10,r:10}},yaxis:{{title:'$'}},legend:{{orientation:'h'}}}},
   {{displayModeBar:false,responsive:true}});
 Plotly.newPlot('datehist', {hist_json}, {{margin:{{t:10,r:10}},yaxis:{{title:'articles'}},bargap:0.05}},
   {{displayModeBar:false,responsive:true}});
</script>
</body></html>"""


def _write_landing(out: Path, latest: str, final: float, cap: float, spy_final: float) -> None:
    """Landing page: link every preserved weekly snapshot (newest first) + the latest headline."""
    weeks = sorted((f.stem for f in out.glob("*.html") if f.stem != "index"), reverse=True)
    items = "\n".join(f'<li><a href="{w}.html">week ending {w}</a></li>' for w in weeks)
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Forward paper-trade</title>
<style>body{{{{font:14px/1.6 -apple-system,system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem}}}}
h1{{{{font-size:1.4rem}}}} .kpi{{{{font-size:1.15rem;background:#f4faff;border:1px solid #cfe3f5;border-radius:6px;padding:10px 14px}}}}
a{{{{color:#c0392b}}}}</style></head><body>
<h1>Forward paper-trade — weekly dashboards</h1>
<p class="kpi">Latest ({latest}): <b>${final:,.0f}</b> from ${cap:,.0f} ({final/cap-1:+.1%}) &middot; SPY {spy_final/cap-1:+.1%}</p>
<p>Every weekly snapshot is preserved (newest first):</p>
<ul>{items}</ul>
<p style="color:#888;font-size:12px">Paper trade; the recommended portfolio auto-follows the optimizer.</p>
</body></html>"""
    (out / "index.html").write_text(html)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandbox", required=True, help="forward sandbox dir (firehose_scans.csv, journal.json, archive/)")
    ap.add_argument("--out", default=str(OUT), help="output dir (default docs_preview/forward; prod = docs/forward)")
    a = ap.parse_args(argv)
    sb = Path(a.sandbox)

    log = pd.read_csv(sb / "firehose_scans.csv")
    weeks = sorted(log["week"].astype(str).unique())
    week = weeks[-1]
    fm = load_financial_model(str(ROOT / "investor_profile.forward.md"))
    cap = float(fm.get("initial_investment_usd", 50_000))
    scans = forward._scans_dict(log)
    bt = firehose.backtest(scans, fm, cap)                      # rows = weekly equity curve incl. floors

    rows = bt["rows"]
    curve = [
        {"x": [r["date"] for r in rows], "y": [r["value"] for r in rows],
         "name": "recommended", "line": {"color": "#c0392b", "width": 2}},
        {"x": [r["date"] for r in rows], "y": [r["spy"] for r in rows],
         "name": "SPY", "line": {"color": "#888", "dash": "dot"}},
    ]

    recs_html = "".join(f"<tr><td>{r['week']}</td><td>{r['weights'] or '—'}</td>"
                        f"<td>{r['week_return']:+.2%}</td></tr>" for r in bt["log"]) or \
        "<tr><td colspan=3 class='q'>need ≥2 weeks to mark a return</td></tr>"

    # active agents = every ticker that carried weight in any week (floors + gems), with its latest weight
    latest_w = {}
    for r in bt["log"]:
        for chunk in r["weights"].split(";"):
            if ":" in chunk:
                t, wv = chunk.split(":"); latest_w[t] = float(wv)
    always_on = {forward.score.BENCHMARK: "SPY floor (always-on)",
                 str(fm.get("defensive_ticker", "GLD")).upper(): "defensive floor (always-on)"}
    agents_html = "<table><tr><th>agent</th><th>role</th><th>latest weight</th></tr>" + "".join(
        f"<tr><td><b>{t}</b></td><td>{always_on.get(t, 'gem (scout)')}</td><td>{w:.0%}</td></tr>"
        for t, w in sorted(latest_w.items(), key=lambda kv: -kv[1])) + "</table>" \
        if latest_w else "<p>No agents held.</p>"

    journal = json.loads((sb / "journal.json").read_text())
    jrows = []
    for eid, ev in journal.get("events", {}).items():
        for e in ev.get("entries", []):
            jrows.append(f"<tr><td>{e.get('date')}</td><td><b>{eid}</b> {','.join(sorted(ev['vehicles']))}</td>"
                         f"<td>live={e.get('thesis_live')} conv={e.get('conviction')}<br>{e.get('assessment','')}</td></tr>")
    journal_html = f"<table>{''.join(jrows)}</table>" if jrows else \
        "<p class='q'>No scout events this run — the portfolio is the two always-on floors only.</p>"

    from collections import Counter
    datehist: Counter = Counter()
    for f in sorted((sb / "archive").glob("*.json")):
        for art in json.loads(f.read_text()).get("pool", []):
            d = (art.get("published_date") or "")[:10]
            if d:
                datehist[d] += 1
    hx = sorted(datehist)
    hist_trace = [{"x": hx, "y": [datehist[d] for d in hx], "type": "bar", "marker": {"color": "#4a90d9"}}]

    arch_f = sb / "archive" / f"{week}.json"
    arch = json.loads(arch_f.read_text()) if arch_f.exists() else {}
    queries_html = "".join(f"<li>{q}</li>" for q in arch.get("queries", [])[:60])
    pool_html = "".join(f"<tr><td class='q'>{p.get('published_date')}</td><td>{p.get('title','')[:80]}</td></tr>"
                        for p in sorted(arch.get("pool", []), key=lambda x: x.get("published_date", ""), reverse=True))
    cfg_html = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in arch.get("config", {}).items())

    html = PAGE.format(week=week, nweeks=len(weeks), model=arch.get("model", fm.get("model", "?")),
                       final=bt["final"], cap=cap, ret=bt["final"] / cap - 1,
                       spyret=bt["spy_final"] / cap - 1, curve_json=json.dumps(curve), hist_json=json.dumps(hist_trace),
                       recs_html=recs_html, agents_html=agents_html, journal_html=journal_html,
                       npool=len(arch.get("pool", [])), nq=len(arch.get("queries", [])),
                       queries_html=queries_html, pool_html=pool_html, cfg_html=cfg_html)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{week}.html").write_text(html)                 # dated snapshot — NEVER overwritten week-to-week
    _write_landing(out, week, bt["final"], cap, bt["spy_final"])
    print(f"  wrote {out}/{week}.html + index.html landing  ({len(weeks)} weeks, "
          f"portfolio ${bt['final']:,.0f} vs SPY ${bt['spy_final']:,.0f})")


if __name__ == "__main__":
    main()
