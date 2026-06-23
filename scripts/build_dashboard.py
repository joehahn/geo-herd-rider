"""build_dashboard.py — the portfolio dashboard: $50K through the firehose.

Renders the firehose book — a weekly-rebalanced portfolio of the gems the financial press
named (entered while the driving thesis is live, dropped when it decays) — against SPY, with
the motivating BWET "hidden gem" overlaid. Reads the weekly scan log produced by
`firehose.py --fixture` (so a dashboard rebuild costs no LLM tokens) and reuses
firehose.backtest for the numbers; a linked child page (firehose.html) lays the weekly
press-named gems out on a timeline.

HONESTY (repo discipline #4/#6): the on-screen book is the FIXTURE backtest — it assumes
PERFECT point-in-time retrieval of the early articles, which no available search tool delivers
(both Anthropic `before:` and Tavily `end_date` leak future dates; the early under-the-radar
pieces don't rank into a date-bounded pull). So this proves the MECHANICS, not forward lift.
Every number here is an UPPER BOUND. The clean test is the forward eval (src/forward.py).

    python scripts/build_dashboard.py            # reads data/windows/firehose_scans.json
    python scripts/build_dashboard.py --capital 50000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import firehose  # noqa: E402
import costs  # noqa: E402
from optimizer import load_financial_model  # noqa: E402

OUT_DIR = ROOT / "docs"  # GitHub Pages serves this folder (Settings -> Pages -> main /docs)
SCANS_JSON = ROOT / "data" / "windows" / "firehose_scans.json"

# PWR (tab10) categorical palette — matches the portfolio-wave-rider dashboard color schema.
# Every plot draws ticker series and headline lines from this list (no seaborn/Flat-UI muted tones).
PALETTE = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#eab308",
           "#17becf", "#e377c2", "#7f7f7f", "#0d9488", "#8c564b", "#bcbd22"]


def load_scans() -> dict:
    """Rebuild firehose's {anchor_ts: [picks]} from the saved weekly scan log."""
    if not SCANS_JSON.exists():
        sys.exit(f"ERROR: {SCANS_JSON} not found. Run first:\n"
                 f"  python src/firehose.py --fixture data/fixtures/firehose_bwet.json")
    raw = json.loads(SCANS_JSON.read_text())
    out = {}
    for wk, picks in raw.items():
        out[pd.Timestamp(str(wk) + " 16:30", tz="America/New_York")] = picks
    return dict(sorted(out.items()))


def metrics(value: list[float], spy: list[float], capital: float) -> dict:
    v = pd.Series(value)
    mdd = float(((v - v.cummax()) / v.cummax()).min())
    return {"final": round(value[-1], 0), "total_ret": round(value[-1] / capital - 1, 4),
            "spy_ret": round(spy[-1] / capital - 1, 4), "max_dd": round(mdd, 4)}


def book_cost(dates: list[str]) -> float:
    """Cost to produce THIS book: LLM rows (firehose/agent stages) whose label is dated within the
    book's window, last cost per label. Works for either engine (fixture firehose or the agent)."""
    if not costs.LEDGER.exists() or not dates:
        return 0.0
    import re
    led = pd.read_csv(costs.LEDGER)
    led = led[led["stage"].isin(["firehose", "agent"])].copy()
    led["d"] = led["label"].astype(str).map(
        lambda s: (m.group(0) if (m := re.search(r"\d{4}-\d{2}-\d{2}", s)) else None))
    led = led[led["d"].notna() & (led["d"] >= dates[0]) & (led["d"] <= dates[-1])]
    return round(float(led.groupby("label")["cost_usd"].last().sum()) if len(led) else 0.0, 2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capital", type=float, default=50_000.0)
    args = ap.parse_args(argv)

    scans = load_scans()
    fm = load_financial_model(str(ROOT / "investor_profile.md"))
    print(f"Backtesting firehose book over {len(scans)} weekly scans ...")
    bt = firehose.backtest(scans, fm, args.capital, daily=True)
    d = bt["daily"]
    if d is None:
        sys.exit("No daily series — need >=1 week with prices.")

    # weekly press-named gems for the firehose-log page (flatten the scans)
    gems = []
    for a, picks in scans.items():
        for p in picks:
            if not str(p.get("ticker", "")).strip():
                continue
            gems.append({"week": a.date().isoformat(), "ticker": p["ticker"],
                         "thesis": p.get("thesis", ""), "thesis_live": bool(p.get("thesis_live", True)),
                         "crowding": p.get("crowding", ""),
                         "urls": [u for u in (p.get("evidence_urls", []) or []) if u]})

    tickers = sorted(d["alloc"].keys())

    # Plot 5 — the sticky live watchlist per week (what the curator kept thesis-live), with the
    # subset the optimizer actually funded that week (watchlisted-but-pruned shows as not-funded).
    watch = firehose._stateful_watch(scans)
    funded_by_week = {lg["week"]: [s.split(":")[0] for s in lg["weights"].split(";") if s]
                      for lg in bt["log"]}
    watchlist = [{"week": a.date().isoformat(), "names": watch[a],
                  "funded": funded_by_week.get(a.date().isoformat(), [])} for a in scans]

    payload = {
        "capital": args.capital, "dates": d["dates"], "value": d["value"], "spy": d["spy"],
        "overlay": d["overlay"], "overlay_ticker": d["overlay_ticker"],
        "overlay_anchor": d["overlay_anchor"],
        "alloc": d["alloc"], "cash": d["cash"],
        "colors": {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(tickers)},
        "metrics": metrics(d["value"], d["spy"], args.capital),
        "cost_usd": book_cost(d["dates"]), "weeks": bt["weeks"], "gems": gems,
        "watchlist": watchlist,
    }

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "data.json").write_text(json.dumps(payload, indent=2))
    (OUT_DIR / "index.html").write_text(INDEX_HTML)
    (OUT_DIR / "firehose.html").write_text(FIREHOSE_HTML)
    # legacy decision-tree page retired; leave a redirect so old links don't 404
    (OUT_DIR / "tree.html").write_text(REDIRECT_HTML)

    m = payload["metrics"]
    print(f"\nFirehose ${args.capital:,.0f} -> ${m['final']:,.0f} ({m['total_ret']:+.1%}), "
          f"maxDD {m['max_dd']:.1%}")
    print(f"SPY      ${args.capital:,.0f} -> ${payload['spy'][-1]:,.0f} ({m['spy_ret']:+.1%})")
    print(f"Cost to produce this book: ${payload['cost_usd']:.2f}")
    print(f"\nWrote {OUT_DIR/'index.html'} + firehose.html + data.json")
    print("Open: python -m http.server -d docs  (then visit localhost:8000)")
    return 0


INDEX_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>geo-herd-rider — $50K firehose backtest</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
 :root{--ink:#1a1a1a;--mut:#666;--line:#e3e3e3;--bg:#fafafa}
 body{font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);margin:0;background:var(--bg)}
 .wrap{max-width:960px;margin:0 auto;padding:28px 20px 60px}
 h1{font-size:25px;margin:0 0 4px} h2{font-size:18px;margin:34px 0 6px}
 .sub{color:var(--mut);margin:0 0 18px}
 .warn{background:#fff8e1;border:1px solid #f0d98c;border-radius:8px;padding:10px 14px;font-size:13px;color:#5a4a00;margin:14px 0 22px}
 .cards{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0 6px}
 .card{flex:1;min-width:150px;background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px}
 .card .k{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.03em}
 .card .v{font-size:22px;font-weight:600;margin-top:3px}
 .pos{color:#1e7d34} .neg{color:#c0392b}
 a{color:#2980b9} .foot{color:var(--mut);font-size:12px;margin-top:30px;border-top:1px solid var(--line);padding-top:12px}
 .nav{display:flex;gap:20px;padding:0 0 16px;margin:0 0 18px;border-bottom:1px solid var(--line);font-size:14px}
 .nav a{color:var(--mut);text-decoration:none;font-weight:500} .nav a:hover{color:var(--ink)} .nav a.active{color:var(--ink);font-weight:600}
 #chart,#alloc,#dollars{width:100%;height:420px}
 #gantt{width:100%}
 .atab{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}
 .atab th,.atab td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
 .atab th{color:var(--mut);font-weight:600}
 .atab td:first-child{white-space:nowrap;color:var(--mut);font-variant-numeric:tabular-nums}
</style></head>
<body><div class="wrap">
 <nav class="nav"><a href="index.html" class="active">Dashboard</a>
   <a href="firehose.html">Firehose log</a>
   <a href="https://github.com/joehahn/geo-herd-rider/blob/main/README.md">README</a></nav>
 <h1>Through the herd, on $50K</h1>
 <p class="sub" id="sub"></p>
 <div class="warn"><b>Hindsight upper bound, not forward lift.</b> This is the
   <b>event-first agent</b> finding BWET in a realistic, noisy GDELT news firehose — with BWET's
   early under-the-radar article <b>seeded</b> (real search misses those niche pieces). It still
   leans on that seed and on a model trained past the events, so it does <i>not</i> prove the
   firehose finds gems in time. The clean verdict is the forward eval
   (<code>src/forward.py</code>).</div>
 <div class="cards" id="cards"></div>
 <p class="sub">The <b>firehose book</b>: each week, the gems the financial press names as
   thesis-driven movers go on the watchlist; a position is held while its driving thesis is
   <b>live</b> and dropped when it decays. A plain mean-variance optimizer sizes it — the LLM
   never sets a weight. Below, the same $50K through the firehose vs <b>SPY</b>, with
   <b>BWET</b> (the motivating hidden gem, dashed) scaled to the book at the carrier→W-Med
   transit — does the book ride the same move?</p>

 <h2>Plot 1 — Portfolio value</h2>
 <div id="chart"></div>

 <h2>Plot 2 — Allocation over time</h2>
 <p class="sub">Capital committed per ticker (cash fills the rest). Fully invested while the
   watchlist is non-empty; to cash when the press names nothing live.</p>
 <div id="alloc"></div>
 <p class="sub" id="allocnote" style="margin-top:4px"></p>

 <h2>Plot 3 — Holdings timeline</h2>
 <p class="sub">One row per ticker; each bar spans the dates that name was held (color matches the
   allocation plot). First-held at the top — the same data as Plot 2, as a Gantt.</p>
 <div id="gantt"></div>

 <h2>Plot 4 — Dollars held per ticker</h2>
 <p class="sub">Capital in <b>dollars</b> per ticker over time (cash fills to the book total, so the
   stack's top edge is the portfolio value). Plot 2 shows the same split as percentages.</p>
 <div id="dollars"></div>

 <h2>Plot 5 — Watchlist by date</h2>
 <p class="sub" style="margin:0 0 0">Each row is a date the live watchlist (or its funding) changed —
   the names the press kept thesis-live that week. <b>Bold + colored</b> = actually funded by the
   optimizer; <span style="color:#aaa">gray</span> = on the watchlist but pruned by the sizing floor.</p>
 <table class="atab" id="watchtable"></table>

 <h2>What it cost</h2>
 <div id="costs"></div>

 <p class="foot">geo-herd-rider, generated by <code>scripts/build_dashboard.py</code> ·
   gems sourced from the news firehose (no hand-picking) · sizing = mean-variance · idle = cash @ 0%
   · event-first agent over a GDELT firehose (BWET seeded) — hindsight upper bound; clean test is the forward eval.</p>
</div>
<script>
fetch("data.json").then(r=>r.json()).then(D=>{
  const fmt=x=>"$"+Math.round(x).toLocaleString();
  const pct=x=>(x>=0?"+":"")+(x*100).toFixed(1)+"%";
  const last=D.dates.length-1, m=D.metrics, cls=x=>x>=0?"pos":"neg";
  document.getElementById("sub").textContent =
    `${D.weeks} weekly scans · ${D.dates[0]} → ${D.dates[last]} · $${D.capital.toLocaleString()} start · weekly-rebalanced`;

  document.getElementById("cards").innerHTML=[
    ["Firehose book", fmt(m.final), pct(m.total_ret), cls(m.total_ret)],
    ["SPY buy & hold", fmt(D.spy[last]), pct(m.spy_ret), cls(m.spy_ret)],
    ["Excess vs SPY", pct(m.total_ret-m.spy_ret), "", cls(m.total_ret-m.spy_ret)],
    ["Max drawdown", pct(m.max_dd), "", cls(m.max_dd)],
  ].map(([k,v,s,c])=>`<div class="card"><div class="k">${k}</div><div class="v ${c}">${v}</div>
     <div class="sub" style="margin:0;font-size:12px">${s}</div></div>`).join("");

  // PWR (tab10) palette: book = red, SPY = gray, the gem overlay = its own allocation color.
  const BOOK="#d62728", SPYC="#7f7f7f";
  const OVC=(D.colors&&D.colors[D.overlay_ticker])||"#1f77b4";
  const endlab=(arr,col,ys)=>({x:D.dates[last],y:arr[last],xanchor:"left",xshift:6,yshift:ys,
    showarrow:false,text:fmt(arr[last])+" ("+pct(arr[last]/D.capital-1)+")",font:{color:col,size:11}});
  const vtraces=[
    {x:D.dates,y:D.value,name:"Firehose book",line:{color:BOOK,width:2.4}},
    {x:D.dates,y:D.spy,name:"SPY",line:{color:SPYC,width:1.6,dash:"dot"}},
  ];
  const vann=[endlab(D.value,BOOK,10),endlab(D.spy,SPYC,-10)], vshapes=[];
  if(D.overlay){
    vtraces.push({x:D.dates,y:D.overlay,name:D.overlay_ticker+" (scaled)",
      line:{color:OVC,width:1.8,dash:"dash"},connectgaps:true});
    vshapes.push({type:"line",x0:D.overlay_anchor,x1:D.overlay_anchor,yref:"paper",y0:0,y1:1,
      line:{color:OVC,width:1,dash:"dot"}});
    vann.push({x:D.overlay_anchor,y:1,yref:"paper",yanchor:"bottom",showarrow:false,
      text:"carriers → W. Med",font:{color:OVC,size:10}});
  }
  Plotly.newPlot("chart",vtraces,
    {margin:{l:60,r:140,t:24,b:36},legend:{orientation:"h",y:1.14},annotations:vann,shapes:vshapes,
     yaxis:{tickprefix:"$",separatethousands:true},hovermode:"x unified"},
    {displayModeBar:false,responsive:true});

  const traces=[];
  for(const t in D.alloc) traces.push({x:D.dates,y:D.alloc[t].map(v=>v*100),name:t,
    stackgroup:"a",line:{width:0},fillcolor:D.colors[t]||"#bbb",hovertemplate:"%{y:.0f}%"});
  traces.push({x:D.dates,y:D.cash.map(v=>v*100),name:"cash",stackgroup:"a",
    line:{width:0},fillcolor:"#dfe3e6",hovertemplate:"%{y:.0f}%"});
  Plotly.newPlot("alloc",traces,{margin:{l:60,r:140,t:40,b:36},
    yaxis:{ticksuffix:"%",range:[0,100]},legend:{orientation:"h",y:1.22},hovermode:"x unified"},
    {displayModeBar:false,responsive:true});
  const dep=D.cash.filter(v=>v<0.999).length, n=D.cash.length;
  const peak={}; for(const t in D.alloc) peak[t]=Math.max(...D.alloc[t])*100;
  const top=Object.entries(peak).sort((a,b)=>b[1]-a[1]).slice(0,4).map(([t,v])=>`${t} ${v.toFixed(0)}%`).join(" · ");
  document.getElementById("allocnote").innerHTML=
    `Deployed <b>${(dep/n*100).toFixed(0)}%</b> of trading days (cash ${((n-dep)/n*100).toFixed(0)}%). Peak weights — ${top}.`;

  // Plot 3 — holdings Gantt: one row per ticker, bars = contiguous held spans.
  const firstIdx=t=>D.alloc[t].findIndex(w=>w>0.0001);
  const ord=Object.keys(D.alloc).filter(t=>firstIdx(t)>=0).sort((a,b)=>firstIdx(a)-firstIdx(b));
  const spans=t=>{const s=[];let st=null;for(let i=0;i<D.dates.length;i++){
    const on=D.alloc[t][i]>0.0001;
    if(on&&st===null)st=i;
    if(st!==null&&(!on||i===D.dates.length-1)){s.push([st,on?i:i-1]);st=null;}}return s;};
  const gtraces=[];
  ord.forEach((t,yi)=>spans(t).forEach((sp,k)=>gtraces.push({
    x:[D.dates[sp[0]],D.dates[sp[1]]],y:[yi,yi],mode:"lines+markers",
    line:{color:D.colors[t]||"#888",width:13},marker:{color:D.colors[t]||"#888",size:5},
    name:t,legendgroup:t,showlegend:false,
    hovertemplate:`<b>${t}</b><br>%{x|%Y-%m-%d}<extra></extra>`})));
  Plotly.newPlot("gantt",gtraces,{margin:{l:70,r:140,t:18,b:36},
    height:Math.max(180,38*ord.length+80),
    yaxis:{tickmode:"array",tickvals:ord.map((_,i)=>i),ticktext:ord,autorange:"reversed"},
    xaxis:{type:"date"},hovermode:"closest"},
    {displayModeBar:false,responsive:true});

  // Plot 4 — dollars held per ticker over time (stacked area; top edge = book value).
  const dtraces=[];
  for(const t of ord) dtraces.push({x:D.dates,y:D.alloc[t].map((w,i)=>w*D.value[i]),name:t,
    stackgroup:"d",line:{width:0},fillcolor:D.colors[t]||"#bbb",hovertemplate:"$%{y:,.0f}"});
  dtraces.push({x:D.dates,y:D.cash.map((c,i)=>c*D.value[i]),name:"cash",stackgroup:"d",
    line:{width:0},fillcolor:"#dfe3e6",hovertemplate:"$%{y:,.0f}"});
  Plotly.newPlot("dollars",dtraces,{margin:{l:70,r:140,t:40,b:36},
    yaxis:{tickprefix:"$",separatethousands:true},legend:{orientation:"h",y:1.22},
    hovermode:"x unified"},{displayModeBar:false,responsive:true});

  // Plot 5 — watchlist by date: rows where the live watchlist or its funding changed.
  let pw=null; const wrows=[];
  for(const w of (D.watchlist||[])){
    const fset=new Set(w.funded||[]);
    const sig=w.names.join(",")+"|"+(w.funded||[]).join(",");
    if(sig===pw) continue;
    pw=sig;
    if(!w.names.length){ wrows.push(`<tr><td>${w.week}</td><td style="color:#aaa">— empty (cash) —</td></tr>`); continue; }
    const cells=w.names.map(t=>{
      const c=(D.colors&&D.colors[t])||"#444";
      return fset.has(t) ? `<b style="color:${c}">${t}</b>` : `<span style="color:#aaa">${t}</span>`;
    }).join(" · ");
    wrows.push(`<tr><td>${w.week}</td><td>${cells}</td></tr>`);
  }
  document.getElementById("watchtable").innerHTML=
    `<thead><tr><th>Date</th><th>Live watchlist (bold = funded · gray = pruned)</th></tr></thead>`+
    `<tbody>${wrows.join("")||'<tr><td colspan=2 style="color:#aaa">never populated</td></tr>'}</tbody>`;

  document.getElementById("costs").innerHTML =
    `<div class="card" style="max-width:430px"><div class="k">cost to produce this book</div>`
    + `<div class="v">$${(D.cost_usd||0).toFixed(2)}</div>`
    + `<div class="sub" style="margin:6px 0 0;font-size:12px">The event-first agent's scout + journal `
    + `calls across ${D.weeks} weekly scans (dev model). No causal ladder, no magnitude forecasts.</div></div>`;
});
</script></body></html>
"""


FIREHOSE_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>geo-herd-rider — firehose log</title>
<style>
 :root{--ink:#1a1a1a;--mut:#666;--line:#e3e3e3;--bg:#fafafa}
 body{font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);margin:0;background:var(--bg)}
 .wrap{max-width:860px;margin:0 auto;padding:28px 20px 60px}
 h1{font-size:24px;margin:0 0 4px} .sub{color:var(--mut);margin:0 0 20px} a{color:#2980b9}
 .nav{display:flex;gap:20px;padding:0 0 16px;margin:0 0 18px;border-bottom:1px solid var(--line);font-size:14px}
 .nav a{color:var(--mut);text-decoration:none;font-weight:500} .nav a:hover{color:var(--ink)} .nav a.active{color:var(--ink);font-weight:600}
 .how{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:0 0 18px;font-size:13px}
 .how ol{margin:6px 0 0;padding-left:20px} .how li{margin:3px 0}
 .row{background:#fff;border:1px solid var(--line);border-left:5px solid #c0392b;border-radius:10px;padding:11px 15px;margin:0 0 11px}
 .row.exit{border-left-color:#9aa0a6;opacity:.7}
 .hd{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:baseline}
 .date{font-weight:600} .tk{font-family:ui-monospace,Menlo,monospace;font-weight:600;background:#2c3e50;color:#fff;border-radius:6px;padding:1px 7px;font-size:13px}
 .b{display:inline-block;font-size:11px;padding:1px 8px;border-radius:20px;margin-left:6px;background:#eef1f3;color:#445}
 .b.live{background:#e3f3e6;color:#1e7d34;font-weight:600} .b.exit{background:#eef1f3;color:#888;font-weight:600}
 .b.early{background:#e8f0fe;color:#1a56c4} .b.consensus{background:#fdebd0;color:#9c5700}
 .th{font-size:13.5px;color:#333;margin:6px 0 4px} .u{font-size:12px} .u a{color:#2980b9;margin-right:10px}
</style></head>
<body><div class="wrap">
 <nav class="nav"><a href="index.html">Dashboard</a>
   <a href="firehose.html" class="active">Firehose log</a>
   <a href="https://github.com/joehahn/geo-herd-rider/blob/main/README.md">README</a></nav>
 <h1>Firehose log — the press-named gems, week by week</h1>
 <div class="how"><b>How the simplified solution works:</b>
   <ol>
     <li><b>📰 Read the firehose.</b> Each week, scan the news for tickers the press explicitly
       NAMES as thesis-driven movers — the journalists do the gem-discovery (CNBC/ETF.com named
       BWET weeks before it tripled).</li>
     <li><b>🟢 Enter / hold while LIVE.</b> A name stays on the watchlist while its driving thesis
       is live (war on, chokepoint shut). <b>Crowding</b> (early→consensus) is shown for context
       only — it does not drive the trade.</li>
     <li><b>⚪ Exit on decay.</b> Drop it when the thesis resolves (ceasefire, Hormuz reopens).</li>
     <li><b>Mechanical sizing</b> downstream (mean-variance optimizer; the LLM never sets weights).</li>
   </ol>
   <span style="color:var(--mut)">No causal decision-tree — just the firehose, an entry/exit
   switch, and the optimizer. This is the event-first agent over a realistic GDELT firehose (BWET
   seeded); the live forward log is built by <code>src/forward.py</code>.</span></div>
 <div id="log"></div>
</div>
<script>
fetch("data.json").then(r=>r.json()).then(D=>{
  const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  const byWeek={};
  (D.gems||[]).forEach(g=>{(byWeek[g.week]=byWeek[g.week]||[]).push(g);});
  const weeks=Object.keys(byWeek).sort();
  document.getElementById("log").innerHTML = weeks.map(wk=>byWeek[wk].map(g=>{
    const urls=(g.urls||[]).map(u=>`<a href="${esc(u)}" target="_blank">source ↗</a>`).join("");
    const cw=g.crowding?`<span class="b ${esc(g.crowding)}">${esc(g.crowding)}</span>`:"";
    return `<div class="row ${g.thesis_live?'':'exit'}">
      <div class="hd"><span class="date">${esc(g.week)} &nbsp;<span class="tk">${esc(g.ticker)}</span>
        <span class="b ${g.thesis_live?'live':'exit'}">${g.thesis_live?'HELD — thesis live':'EXIT — thesis decayed'}</span>
        ${cw}</span></div>
      <div class="th">${esc(g.thesis)}</div><div class="u">${urls}</div></div>`;
  }).join("")).join("") || '<p class="sub">No gems logged yet.</p>';
});
</script></body></html>
"""


REDIRECT_HTML = r"""<!doctype html><meta charset="utf-8">
<meta http-equiv="refresh" content="0; url=firehose.html">
<p>The decision-tree page was retired. Redirecting to the <a href="firehose.html">firehose log</a>…</p>
"""


if __name__ == "__main__":
    raise SystemExit(main())
