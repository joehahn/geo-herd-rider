"""build_dashboard.py — the portfolio dashboard: $50K through the firehose.

Renders the firehose portfolio — a weekly-rebalanced portfolio of the gems the financial press
named (entered while the driving thesis is live, dropped when it decays) — against SPY, with
the motivating BWET "hidden gem" overlaid. Reads the weekly scan log produced by
`firehose.py --fixture` (so a dashboard rebuild costs no LLM tokens) and reuses
firehose.backtest for the numbers; a linked child page (firehose.html) lays the weekly
press-named gems out on a timeline.

HONESTY (repo discipline #4/#6): the on-screen portfolio is the FIXTURE backtest — it assumes
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


def load_scans(path: Path = SCANS_JSON) -> dict:
    """Rebuild firehose's {anchor_ts: [picks]} from the saved weekly scan log."""
    if not path.exists():
        sys.exit(f"ERROR: {path} not found. Run first:\n"
                 f"  python src/firehose.py --fixture data/fixtures/firehose_bwet.json")
    raw = json.loads(path.read_text())
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
    """Cost to produce THIS portfolio: LLM rows (firehose/agent stages) whose label is dated within the
    portfolio's window, last cost per label. Works for either engine (fixture firehose or the agent)."""
    if not costs.LEDGER.exists() or not dates:
        return 0.0
    import re
    led = pd.read_csv(costs.LEDGER)
    led = led[led["stage"].isin(["firehose", "agent"])].copy()
    led["d"] = led["label"].astype(str).map(
        lambda s: (m.group(0) if (m := re.search(r"\d{4}-\d{2}-\d{2}", s)) else None))
    led = led[led["d"].notna() & (led["d"] >= dates[0]) & (led["d"] <= dates[-1])]
    return round(float(led.groupby("label")["cost_usd"].last().sum()) if len(led) else 0.0, 2)


GEMS_JSON = ROOT / "data" / "fixtures" / "gems.json"


def gem_config(ticker: str) -> dict:
    """Resolve a gem's name/trigger (from gems.json) + its scan/stats files + output subdir.
    BWET keeps the canonical files; other gems use the firehose_scans_<gem>.json convention."""
    g = next(x for x in json.loads(GEMS_JSON.read_text())["gems"] if x["ticker"] == ticker)
    low = ticker.lower()
    scans = "firehose_scans.json" if ticker == "BWET" else f"firehose_scans_{low}.json"
    stats = f"retrieval_stats_{low}.json"
    return {"ticker": ticker, "name": g["name"], "trigger": g["trigger_date"],
            "scans": ROOT / "data" / "windows" / scans,
            "stats": ROOT / "data" / "windows" / stats, "out": OUT_DIR / low}


def build_gem(ticker: str, capital_override: float | None = None) -> dict:
    """Build one gem's dashboard into docs/<gem>/ (data.json + index.html + firehose.html).
    The gem's own price is the overlay, anchored at its trigger date. Returns the payload."""
    import retstats
    cfg = gem_config(ticker)
    scans = load_scans(cfg["scans"])
    fm = load_financial_model(str(ROOT / "investor_profile.md"))
    capital = capital_override if capital_override is not None else float(fm.get("initial_investment_usd", 50_000))
    bt = firehose.backtest(scans, fm, capital, daily=True, overlay=ticker, overlay_anchor=cfg["trigger"])
    d = bt["daily"]
    if d is None:
        sys.exit(f"{ticker}: no daily series — need >=1 week with prices.")
    gems = []
    for a, picks in scans.items():
        for p in picks:
            if str(p.get("ticker", "")).strip():
                gems.append({"week": a.date().isoformat(), "ticker": p["ticker"],
                             "thesis": p.get("thesis", ""), "thesis_live": bool(p.get("thesis_live", True)),
                             "urls": [u for u in (p.get("evidence_urls", []) or []) if u]})
    caught = ticker in {str(p.get("ticker", "")).strip().upper() for picks in scans.values()
                        for p in picks if str(p.get("ticker", "")).strip()}
    tickers = sorted(d["alloc"].keys())
    watch = firehose._stateful_watch(scans)
    funded_by_week = {lg["week"]: [s.split(":")[0] for s in lg["weights"].split(";") if s] for lg in bt["log"]}
    watchlist = [{"week": a.date().isoformat(), "names": watch[a],
                  "funded": funded_by_week.get(a.date().isoformat(), [])} for a in scans]

    # daily watchlist membership per ticker (for the Gantt's "proposed vs funded" layers): each daily
    # date inherits the watchlist of the most recent anchor on/before it.
    import bisect
    anchors = list(scans)
    adates = [a.date() for a in anchors]
    all_wt = sorted({t for names in watch.values() for t in names})
    watch_daily = {t: [0] * len(d["dates"]) for t in all_wt}
    for i, ds in enumerate(d["dates"]):
        j = bisect.bisect_right(adates, pd.Timestamp(ds).date()) - 1
        if j >= 0:
            for t in watch[anchors[j]]:
                watch_daily[t][i] = 1
    payload = {
        "gem": ticker, "overlay_label": f"{ticker} trigger", "caught": caught,
        "capital": capital, "dates": d["dates"], "value": d["value"], "spy": d["spy"],
        "gain": d.get("gain", {}), "overlay": d["overlay"], "overlay_ticker": d["overlay_ticker"],
        "overlay_anchor": d["overlay_anchor"], "alloc": d["alloc"], "cash": d["cash"],
        "colors": {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(tickers)},
        "metrics": metrics(d["value"], d["spy"], capital),
        "cost_usd": book_cost(d["dates"]), "weeks": bt["weeks"], "gems": gems,
        "watchlist": watchlist, "watch_daily": watch_daily,
        "retrieval": retstats.load(str(cfg["stats"])), "params": fm,
    }
    out = cfg["out"]; out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(payload, indent=2))
    (out / "index.html").write_text(INDEX_HTML)
    (out / "firehose.html").write_text(FIREHOSE_HTML)
    m = payload["metrics"]
    print(f"  {ticker}: ${capital:,.0f} -> ${m['final']:,.0f} ({m['total_ret']:+.1%}), "
          f"maxDD {m['max_dd']:.1%}  caught={caught}  -> {out}/")
    return payload


def build_landing() -> None:
    """Landing page at docs/index.html: one card per built gem (scans docs/<gem>/data.json)."""
    rows = []
    for sub in sorted(OUT_DIR.glob("*/data.json")):
        d = json.loads(sub.read_text())
        if "metrics" not in d or "gem" not in d:
            continue                      # skip non-gem subdirs (e.g. docs/sweeps/)
        m = d["metrics"]
        rows.append({"gem": d.get("gem", sub.parent.name.upper()), "url": f"{sub.parent.name}/index.html",
                     "ret": m["total_ret"], "spy": m["spy_ret"], "maxdd": m["max_dd"], "caught": d.get("caught"),
                     "window": f'{d["dates"][0]} → {d["dates"][-1]}',
                     "join": (d.get("retrieval") or {}).get("wayback", {}).get("join_rate_pct")})
    rows.sort(key=lambda r: r["window"], reverse=True)

    def card(r):
        cls = "pos" if r["ret"] >= 0 else "neg"
        cc = "pos" if r["caught"] else "neg"
        caught = "✓ caught" if r["caught"] else "✗ missed"
        jn = f"{r['join']}%" if r["join"] is not None else "—"
        return (f'<a class="gcard" href="{r["url"]}"><div class="gt">{r["gem"]}</div>'
                f'<div class="gv {cls}">{r["ret"]*100:+.0f}%</div>'
                f'<div class="gs">vs SPY {r["spy"]*100:+.0f}% · maxDD {r["maxdd"]*100:.0f}%</div>'
                f'<div class="gs"><span class="{cc}">{caught}</span> · Wayback join {jn}</div>'
                f'<div class="gs">{r["window"]}</div></a>')
    cards = "".join(card(r) for r in rows) or '<p class="sub">No gem dashboards built yet.</p>'
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "index.html").write_text(LANDING_HTML.replace("{{CARDS}}", cards))
    print(f"  landing: {len(rows)} gem(s) -> {OUT_DIR}/index.html")


# Parameter sweeps. Each entry re-scores every gem's book across `values` of `key` (an fm knob)
# and the sweeps dashboard plots SUM-across-gems of final curated value vs the parameter. Extensible:
# add risk_aversion / min_trade_size here later (left commented so they're not run yet).
SWEEPS = [
    {"key": "concentration_cap", "label": "concentration_cap",
     "values": [0.25, 0.33, 0.4, 0.5, 0.55, 0.67, 0.75, 0.85, 1.0]},
    {"key": "min_trade_size", "label": "min_trade_size",
     "values": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]},
    {"key": "lookback_period_days", "label": "lookback_period_days",
     "values": [21, 30, 45, 60, 75, 90, 120, 150, 180, 215, 252, 330]},   # ~3wk -> ~1.3yr μ/Σ fit
    # {"key": "risk_aversion",    "label": "risk_aversion",    "values": [0.5, 1.0, 2.0, 4.0]},
]


def build_sweeps() -> None:
    """Sweep dashboard at docs/sweeps/: for each parameter, re-score every gem's book across its
    values (ONE fixed price panel per gem, so the cap comparison is clean) and write the SUM across
    gems of Final Curated Portfolio value + Sum Final SPY (flat benchmark). Extensible via SWEEPS."""
    import score
    fm0 = load_financial_model(str(ROOT / "investor_profile.md"))
    capital = float(fm0.get("initial_investment_usd", 50_000))
    gem_tickers = [g["ticker"] for g in json.loads(GEMS_JSON.read_text())["gems"]
                   if gem_config(g["ticker"])["scans"].exists()]
    if not gem_tickers:
        print("  sweeps: no gem scan logs yet — skipped"); return
    # enough pre-window history to cover the LONGEST lookback being swept (else early-week μ/Σ fits
    # would run short); +30d buffer, floor 70d.
    pre = max([70] + [max(sw["values"]) + 30 for sw in SWEEPS if sw["key"] == "lookback_period_days"])
    # load each gem's scans + fetch ONE panel, reused across every param/value (deterministic compare)
    gem_data = {}
    for t in gem_tickers:
        cfg = gem_config(t)
        scans = load_scans(cfg["scans"])
        ana = list(scans)
        tix = {score.BENCHMARK, t} | {p["ticker"] for v in scans.values() for p in v
                                      if str(p.get("ticker", "")).strip()}
        start = (ana[0] - pd.Timedelta(days=pre)).strftime("%Y-%m-%d")
        end = (ana[-1] + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
        gem_data[t] = (scans, score.fetch_panel(sorted(tix), start, end, use_cache=False), cfg["trigger"])
    out = {"gems": gem_tickers, "capital_per_gem": capital, "params": {}}
    for sw in SWEEPS:
        key, vals = sw["key"], sw["values"]
        sum_cur, sum_spy, per_gem = [], [], {t: [] for t in gem_tickers}
        for val in vals:
            tc = ts = 0.0
            for t in gem_tickers:
                scans, panel, anchor = gem_data[t]
                bt = firehose.backtest(scans, {**fm0, key: val}, capital, panel=panel,
                                       overlay=t, overlay_anchor=anchor)
                tc += bt["final"]; ts += bt["spy_final"]; per_gem[t].append(round(bt["final"]))
            sum_cur.append(round(tc)); sum_spy.append(round(ts))
        out["params"][key] = {"label": sw["label"], "values": vals,
                              "sum_curated": sum_cur, "sum_spy": sum_spy, "per_gem": per_gem}
        print(f"  sweep {key}: " + " ".join(f"{v}->${c:,.0f}" for v, c in zip(vals, sum_cur)))
    sd = OUT_DIR / "sweeps"; sd.mkdir(parents=True, exist_ok=True)
    (sd / "data.json").write_text(json.dumps(out, indent=2))
    (sd / "index.html").write_text(SWEEPS_HTML)
    print(f"  sweeps -> {sd}/index.html ({len(gem_tickers)} gems: {', '.join(gem_tickers)})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gem", help="build one gem's dashboard (e.g. BWET, MP, SMR) -> docs/<gem>/")
    ap.add_argument("--all", action="store_true", help="build every gem + landing + sweeps")
    ap.add_argument("--sweeps", action="store_true", help="build only the parameter-sweep dashboard")
    ap.add_argument("--capital", type=float, default=None,
                    help="override; default = initial_investment_usd from investor_profile.md")
    args = ap.parse_args(argv)
    if args.all:
        built = [g["ticker"] for g in json.loads(GEMS_JSON.read_text())["gems"]
                 if gem_config(g["ticker"])["scans"].exists()]
        for t in built:
            build_gem(t, args.capital)
        build_landing()
        build_sweeps()
        print(f"\nBuilt {len(built)} gem dashboard(s): {', '.join(built)} + landing + sweeps")
    elif args.sweeps:
        build_sweeps()
    elif args.gem:
        build_gem(args.gem, args.capital)
        build_landing()
    else:
        ap.error("choose --gem <TICKER>, --all, or --sweeps")
    print("Open: python -m http.server -d docs  (then visit localhost:8000)")
    return 0


INDEX_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>geo-herd-rider — gem scan</title>
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
 <nav class="nav"><a href="../index.html">↑ All gems</a>
   <a href="index.html" class="active">Dashboard</a>
   <a href="firehose.html">Firehose log</a>
   <a href="../sweeps/index.html">Sweeps</a>
   <a href="https://github.com/joehahn/geo-herd-rider/blob/main/README.md">README</a></nav>
 <h1 id="gemtitle">Gem scan</h1>
 <p class="sub" id="sub"></p>
 <div class="warn"><b>Hindsight upper bound, not forward lift.</b> This is the
   <b>event-first agent</b> finding the gem in a realistic, noisy GDELT news firehose — with its
   early under-the-radar article <b>seeded</b> (real search misses those niche pieces). It still
   leans on that seed and on a model trained past the events, so it does <i>not</i> prove the
   firehose finds gems in time. The clean verdict is the forward eval
   (<code>src/forward.py</code>).</div>
 <div class="cards" id="cards"></div>
 <p class="sub">The <b>curated portfolio</b>: each week, the gems the financial press names as
   thesis-driven movers go on the watchlist; a position is held while its driving thesis is
   <b>live</b> and dropped when it decays. A plain mean-variance optimizer sizes it — the LLM
   never sets a weight. Below, the portfolio vs <b>SPY</b>, with
   <b id="gemname">the gem</b> (dashed) scaled to the portfolio at its trigger — does the portfolio
   ride the same move?</p>

 <h2>Scan parameters</h2>
 <table id="params" style="border-collapse:collapse;font-size:13px;max-width:560px"></table>

 <h2>Plot 1 — Portfolio value</h2>
 <div id="chart"></div>

 <h2>Plot 2 — Allocation over time</h2>
 <p class="sub">Capital committed per ticker (cash fills the rest). Fully invested while the
   watchlist is non-empty; to cash when the press names nothing live.</p>
 <div id="alloc"></div>
 <p class="sub" id="allocnote" style="margin-top:4px"></p>

 <h2>Plot 3 — Holdings timeline (proposed vs funded)</h2>
 <p class="sub">One row per ticker the curator <b>named</b>. <span style="color:#aab">Thin gray, small
   dots</span> = <b>proposed</b> (on the live watchlist); <b>thick colored, large dots</b> = <b>funded</b>
   (the optimizer actually bought it).</p>
 <div id="gantt"></div>

 <h2>Plot 4 — Dollars held per ticker</h2>
 <p class="sub">Capital in <b>dollars</b> per ticker over time (cash fills to the portfolio total, so the
   stack's top edge is the portfolio value). Plot 2 shows the same split as percentages.</p>
 <div id="dollars"></div>

 <h2>Plot 5 — Cumulative $ gain per holding</h2>
 <p class="sub" style="margin:0 0 6px">Total dollar P&amp;L each holding contributed over the window
   (Σ daily position-value × daily return). Green = winner, red = loser; the bars sum to the
   portfolio's total gain.</p>
 <div id="gain"></div>

 <h2>Plot 6 — Watchlist by date</h2>
 <p class="sub" style="margin:0 0 0">Each row is a date the live watchlist (or its funding) changed —
   the names the press kept thesis-live that week. <b>Bold + colored</b> = actually funded by the
   optimizer; <span style="color:#aaa">gray</span> = on the watchlist but pruned by the sizing floor.</p>
 <table class="atab" id="watchtable"></table>

 <h2>What it cost</h2>
 <div id="costs"></div>

 <h2>Retrieval health (GDELT + Wayback)</h2>
 <p class="sub" style="margin:0 0 6px">Health of the news-retrieval for the run that built this book.
   The Wayback miss-split distinguishes a real archive gap (confirmed) from a rate-limit/transient
   failure (deferred — recoverable on re-run).</p>
 <div id="retr"></div>
</div>
<script>
fetch("data.json").then(r=>r.json()).then(D=>{
  const fmt=x=>"$"+Math.round(x).toLocaleString();
  const pct=x=>(x>=0?"+":"")+(x*100).toFixed(1)+"%";
  const last=D.dates.length-1, m=D.metrics, cls=x=>x>=0?"pos":"neg";
  document.title = `Scan of the ${D.gem} gem — geo-herd-rider`;
  document.getElementById("gemtitle").textContent = `Scan of the ${D.gem} gem`;
  const gn=document.getElementById("gemname"); if(gn) gn.textContent = D.gem;
  document.getElementById("sub").textContent =
    `${D.weeks} weekly scans · ${D.dates[0]} → ${D.dates[last]} · $${D.capital.toLocaleString()} start · weekly-rebalanced`;

  document.getElementById("cards").innerHTML=[
    ["Final Curated Portfolio", fmt(m.final), pct(m.total_ret), cls(m.total_ret)],
    ["Final SPY", fmt(D.spy[last]), pct(m.spy_ret), cls(m.spy_ret)],
    ["Excess vs SPY", pct(m.total_ret-m.spy_ret), "", cls(m.total_ret-m.spy_ret)],
    ["Max drawdown", pct(m.max_dd), "", cls(m.max_dd)],
  ].map(([k,v,s,c])=>`<div class="card"><div class="k">${k}</div><div class="v ${c}">${v}</div>
     <div class="sub" style="margin:0;font-size:12px">${s}</div></div>`).join("");

  // Scan parameters table (mean-variance / optimizer knobs from investor_profile.md)
  const P=D.params||{};
  const order=["initial_investment_usd","concentration_cap","min_trade_size","risk_aversion",
    "max_tickers_per_event","lookback_period_days","t_update_days","rebalance_days","risk_free_rate"];
  const pk=order.filter(k=>k in P);   // only the curated LIVE knobs (hides vestigial/optional keys)
  const prow=(k,v)=>`<tr><td style="padding:3px 16px 3px 0;border-bottom:1px solid #eee"><code>${k}</code></td>`
    +`<td style="padding:3px 0;border-bottom:1px solid #eee;text-align:right">${v}</td></tr>`;
  document.getElementById("params").innerHTML=
    prow("window", `${D.dates[0]} → ${D.dates[D.dates.length-1]}`) + prow("weekly_scans", D.weeks)
    + pk.map(k=>prow(k, P[k])).join("");

  // PWR (tab10) palette: portfolio = red, SPY = gray, the gem overlay = its own allocation color.
  const BOOK="#d62728", SPYC="#7f7f7f";
  const OVC=(D.colors&&D.colors[D.overlay_ticker])||"#1f77b4";
  const endlab=(arr,col,ys)=>({x:D.dates[last],y:arr[last],xanchor:"left",xshift:6,yshift:ys,
    showarrow:false,text:fmt(arr[last])+" ("+pct(arr[last]/D.capital-1)+")",font:{color:col,size:11}});
  const vtraces=[
    {x:D.dates,y:D.value,name:"Curated portfolio",line:{color:BOOK,width:2.4}},
    {x:D.dates,y:D.spy,name:"SPY",line:{color:SPYC,width:1.6,dash:"dot"}},
  ];
  const vann=[endlab(D.value,BOOK,10),endlab(D.spy,SPYC,-10)], vshapes=[];
  if(D.overlay){
    vtraces.push({x:D.dates,y:D.overlay,name:D.overlay_ticker+" (scaled)",
      line:{color:OVC,width:1.8,dash:"dash"},connectgaps:true});
    vshapes.push({type:"line",x0:D.overlay_anchor,x1:D.overlay_anchor,yref:"paper",y0:0,y1:1,
      line:{color:OVC,width:1,dash:"dot"}});
    vann.push({x:D.overlay_anchor,y:1,yref:"paper",yanchor:"bottom",showarrow:false,
      text:D.overlay_label||(D.overlay_ticker+" trigger"),font:{color:OVC,size:10}});
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

  // Plot 3 — holdings Gantt: every curator-named ticker. Thin gray + small markers = PROPOSED
  // (watchlisted/thesis-live); thick colored + large markers = FUNDED (optimizer bought it).
  const WD=D.watch_daily||{};
  // contiguous spans where series[i] passes thresh
  const spansOf=(arr,th)=>{const s=[];let st=null;for(let i=0;i<D.dates.length;i++){
    const on=(arr&&arr[i]||0)>th; if(on&&st===null)st=i;
    if(st!==null&&(!on||i===D.dates.length-1)){s.push([st,on?i:i-1]);st=null;}}return s;};
  const propFirst=t=>{const a=WD[t]||[];for(let i=0;i<a.length;i++)if(a[i])return i;return 1e9;};
  const gord=Array.from(new Set([...Object.keys(WD),...Object.keys(D.alloc||{})]))
                .filter(t=>propFirst(t)<1e9||(D.alloc[t]||[]).some(w=>w>0.0001))
                .sort((a,b)=>propFirst(a)-propFirst(b));
  const gtraces=[];
  gord.forEach((t,yi)=>{
    const col=D.colors[t]||"#888";
    spansOf(WD[t],0).forEach(sp=>gtraces.push({x:[D.dates[sp[0]],D.dates[sp[1]]],y:[yi,yi],
      mode:"lines+markers",line:{color:"#ccd2d8",width:5},marker:{color:"#ccd2d8",size:4},
      legendgroup:t,showlegend:false,hovertemplate:`<b>${t}</b> · proposed<br>%{x|%Y-%m-%d}<extra></extra>`}));
    spansOf(D.alloc[t],0.0001).forEach(sp=>gtraces.push({x:[D.dates[sp[0]],D.dates[sp[1]]],y:[yi,yi],
      mode:"lines+markers",line:{color:col,width:13},marker:{color:col,size:9},
      legendgroup:t,showlegend:false,hovertemplate:`<b>${t}</b> · funded<br>%{x|%Y-%m-%d}<extra></extra>`}));
  });
  Plotly.newPlot("gantt",gtraces,{margin:{l:80,r:140,t:18,b:36},
    height:Math.max(180,34*gord.length+80),
    yaxis:{tickmode:"array",tickvals:gord.map((_,i)=>i),ticktext:gord,autorange:"reversed"},
    xaxis:{type:"date"},hovermode:"closest"},
    {displayModeBar:false,responsive:true});

  // Plot 4 — dollars held per ticker over time (stacked area; top edge = portfolio value).
  // FUNDED tickers only (gord above includes proposed-never-funded names that have no alloc series).
  const ord=Object.keys(D.alloc).filter(t=>D.alloc[t].some(w=>w>0.0001))
              .sort((a,b)=>D.alloc[a].findIndex(w=>w>0.0001)-D.alloc[b].findIndex(w=>w>0.0001));
  const dtraces=[];
  for(const t of ord) dtraces.push({x:D.dates,y:D.alloc[t].map((w,i)=>w*D.value[i]),name:t,
    stackgroup:"d",line:{width:0},fillcolor:D.colors[t]||"#bbb",hovertemplate:"$%{y:,.0f}"});
  dtraces.push({x:D.dates,y:D.cash.map((c,i)=>c*D.value[i]),name:"cash",stackgroup:"d",
    line:{width:0},fillcolor:"#dfe3e6",hovertemplate:"$%{y:,.0f}"});
  Plotly.newPlot("dollars",dtraces,{margin:{l:70,r:140,t:40,b:36},
    yaxis:{tickprefix:"$",separatethousands:true},legend:{orientation:"h",y:1.22},
    hovermode:"x unified"},{displayModeBar:false,responsive:true});

  // Plot 5 — cumulative $ gain per holding (sorted bar; green win / red loss; sums to total gain).
  const G=Object.entries(D.gain||{}).sort((a,b)=>b[1]-a[1]);
  Plotly.newPlot("gain",[{type:"bar",x:G.map(e=>e[0]),y:G.map(e=>e[1]),
    marker:{color:G.map(e=>e[1]>=0?"#2ca02c":"#d62728")},
    hovertemplate:"%{x}<br>$%{y:,.0f}<extra></extra>"}],
    {margin:{l:72,r:30,t:18,b:50},xaxis:{tickangle:-30},
     yaxis:{tickprefix:"$",separatethousands:true,zeroline:true,zerolinecolor:"#888"}},
    {displayModeBar:false,responsive:true});

  // Plot 6 — watchlist by date: rows where the live watchlist or its funding changed.
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
    `<div class="card" style="max-width:430px"><div class="k">cost to produce this portfolio</div>`
    + `<div class="v">$${(D.cost_usd||0).toFixed(2)}</div>`
    + `<div class="sub" style="margin:6px 0 0;font-size:12px">The event-first agent's scout + journal `
    + `calls across ${D.weeks} weekly scans (dev model). No causal ladder, no magnitude forecasts.</div></div>`;

  // Retrieval health panel
  const R=D.retrieval||{}, g=R.gdelt, w=R.wayback;
  const card=(k,v,sub,cls)=>`<div class="card"><div class="k">${k}</div><div class="v ${cls||''}">${v}</div>`
    + `<div class="sub" style="margin:3px 0 0;font-size:12px">${sub||''}</div></div>`;
  const num=x=>(x==null?'—':x);
  if(!g && !w){
    document.getElementById("retr").innerHTML=`<div class="sub">No retrieval stats recorded for this `
      +`book (run the harness with the instrumented code to populate).</div>`;
  } else {
    let h="";
    if(g) h+=card("GDELT pool", num(g.items), "deduped GDELT items");
    if(g) h+=card("non-English", `${g.non_english_pct??0}%`, "of GDELT pool");
    if(w) h+=card("GDELT-Wayback join rate", (w.join_rate_pct??0)+"%",
      `${w.lede} of ${w.looked_up} GDELT headlines got a lede`, w.join_rate_pct>=60?"pos":"neg");
    if(w) h+=card("Wayback misses", `${w.confirmed_no_snapshot} + ${w.transient_deferred}`,
      `${w.confirmed_no_snapshot} not archived (real gap) · ${w.transient_deferred} rate-limited (retry)`,
      w.transient_deferred>w.confirmed_no_snapshot?"neg":"");
    // throughput/error cards only when a LIVE instrumented run recorded them (post-hoc backfill = null)
    const errs=o=>{const p=[]; if(o.http_429)p.push(o.http_429+" rate-limited"); if(o.http_5xx)p.push(o.http_5xx+" server-err"); if(o.timeout)p.push(o.timeout+" timeout"); return p.length?p.join("<br>")+" (all retried)":"no errors";};
    const rate=o=>`${(o.requests||0).toLocaleString()} req<br>${o.items_per_min!=null?o.items_per_min+"/min":"—"}`;
    let hf="";   // fetch cards go on their own row below (GDELT left of Wayback)
    if(g && g.requests!=null)
      hf+=card("GDELT fetch", g.from_cache?"from cache":rate(g), errs(g), (g.http_429||g.http_5xx||g.timeout)?"neg":"");
    if(w && w.requests!=null)
      hf+=card("Wayback fetch", rate(w), errs(w), (w.http_5xx||w.timeout)?"neg":"");
    document.getElementById("retr").innerHTML=
      `<div class="cards">${h}</div>`+(hf?`<div class="cards" style="margin-top:10px">${hf}</div>`:"");
  }
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
 .th{font-size:13.5px;color:#333;margin:6px 0 4px} .u{font-size:12px} .u a{color:#2980b9;margin-right:10px}
</style></head>
<body><div class="wrap">
 <nav class="nav"><a href="../index.html">↑ All gems</a>
   <a href="index.html">Dashboard</a>
   <a href="firehose.html" class="active">Firehose log</a>
   <a href="https://github.com/joehahn/geo-herd-rider/blob/main/README.md">README</a></nav>
 <h1>Firehose log — the press-named gems, week by week</h1>
 <div class="how"><b>How the simplified solution works:</b>
   <ol>
     <li><b>📰 Read the firehose.</b> Each week, scan the news for tickers the press explicitly
       NAMES as thesis-driven movers — the journalists do the gem-discovery (CNBC/ETF.com named
       BWET weeks before it tripled).</li>
     <li><b>🟢 Enter / hold while LIVE.</b> A name stays on the watchlist while its driving thesis
       is live (war on, chokepoint shut).</li>
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
    return `<div class="row ${g.thesis_live?'':'exit'}">
      <div class="hd"><span class="date">${esc(g.week)} &nbsp;<span class="tk">${esc(g.ticker)}</span>
        <span class="b ${g.thesis_live?'live':'exit'}">${g.thesis_live?'HELD — thesis live':'EXIT — thesis decayed'}</span>
        </span></div>
      <div class="th">${esc(g.thesis)}</div><div class="u">${urls}</div></div>`;
  }).join("")).join("") || '<p class="sub">No gems logged yet.</p>';
});
</script></body></html>
"""


REDIRECT_HTML = r"""<!doctype html><meta charset="utf-8">
<meta http-equiv="refresh" content="0; url=firehose.html">
<p>The decision-tree page was retired. Redirecting to the <a href="firehose.html">firehose log</a>…</p>
"""


LANDING_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>geo-herd-rider — gem scans</title>
<style>
 :root{--ink:#1a1a1a;--mut:#666;--line:#e3e3e3;--bg:#fafafa}
 body{font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);margin:0;background:var(--bg)}
 .wrap{max-width:960px;margin:0 auto;padding:28px 20px 60px}
 h1{font-size:25px;margin:0 0 4px} .sub{color:var(--mut);margin:0 0 22px} a{color:#2980b9}
 .grid{display:flex;gap:14px;flex-wrap:wrap}
 .gcard{flex:1;min-width:210px;max-width:300px;background:#fff;border:1px solid var(--line);border-radius:12px;
   padding:16px 18px;text-decoration:none;color:var(--ink);transition:box-shadow .15s}
 .gcard:hover{box-shadow:0 3px 14px rgba(0,0,0,.08)}
 .gt{font-size:20px;font-weight:700;font-family:ui-monospace,Menlo,monospace}
 .gv{font-size:28px;font-weight:700;margin:2px 0}
 .gs{color:var(--mut);font-size:13px;margin:2px 0}
 .pos{color:#1e7d34} .neg{color:#c0392b}
 .foot{color:var(--mut);font-size:12px;margin-top:34px;border-top:1px solid var(--line);padding-top:12px}
</style></head>
<body><div class="wrap">
 <h1>geo-herd-rider — gem scans</h1>
 <p class="sub">Each card is one hidden-gem event scanned through the LLM news-firehose + a mean-variance
   optimizer. Return is the book vs SPY over the gem's window; <b>caught</b> = the firehose named the
   gem itself. Every number is a hindsight <b>upper bound</b> — the clean test is the forward eval.
   &nbsp;<a href="sweeps/index.html"><b>Parameter sweeps →</b></a> &middot;
   <a href="https://github.com/joehahn/geo-herd-rider/blob/main/README.md">README</a></p>
 <div class="grid">{{CARDS}}</div>
 <p class="foot">geo-herd-rider · generated by <code>scripts/build_dashboard.py --all</code></p>
</div></body></html>
"""


SWEEPS_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>geo-herd-rider — parameter sweeps</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
 :root{--ink:#1a1a1a;--mut:#666;--line:#e3e3e3;--bg:#fafafa}
 body{font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);margin:0;background:var(--bg)}
 .wrap{max-width:960px;margin:0 auto;padding:28px 20px 60px}
 h1{font-size:25px;margin:0 0 4px} h2{font-size:18px;margin:30px 0 4px} .sub{color:var(--mut);margin:0 0 16px}
 a{color:#2980b9}
 .nav{display:flex;gap:20px;padding:0 0 16px;margin:0 0 18px;border-bottom:1px solid var(--line);font-size:14px}
 .nav a{color:var(--mut);text-decoration:none;font-weight:500} .nav a:hover{color:var(--ink)}
 .chart{width:100%;height:420px}
 .foot{color:var(--mut);font-size:12px;margin-top:30px;border-top:1px solid var(--line);padding-top:12px}
</style></head>
<body><div class="wrap">
 <nav class="nav"><a href="../index.html">↑ All gems</a>
   <a href="https://github.com/joehahn/geo-herd-rider/blob/main/README.md">README</a></nav>
 <h1>Parameter sweeps</h1>
 <p class="sub" id="sub"></p>
 <div id="charts"></div>
 <p class="foot">geo-herd-rider · generated by <code>scripts/build_dashboard.py --all</code></p>
</div>
<script>
fetch("data.json").then(r=>r.json()).then(D=>{
  const gems=D.gems||[], n=gems.length;
  document.getElementById("sub").textContent =
    `Sum across ${n} gem book(s) (${gems.join(", ")}) · $${(D.capital_per_gem*n).toLocaleString()} total start. `
    +`Each book re-scored at every value on one fixed price panel per gem (a clean, deterministic comparison).`;
  const host=document.getElementById("charts"), P=D.params||{};
  const pal=["#1f77b4","#2ca02c","#9467bd","#ff7f0e","#17becf"];
  Object.keys(P).forEach((k,i)=>{
    const p=P[k];
    const h2=document.createElement("h2"); h2.textContent=`Plot ${i+1} — Sum Final Curated Portfolio vs ${p.label}`; host.appendChild(h2);
    const div=document.createElement("div"); div.className="chart"; div.id="c_"+k; host.appendChild(div);
    const traces=[
      {x:p.values,y:p.sum_curated,name:"Sum Final Curated",mode:"lines+markers",line:{color:"#d62728",width:2.6},marker:{size:8}},
      {x:p.values,y:p.sum_spy,name:"Sum Final SPY",mode:"lines+markers",line:{color:"#7f7f7f",width:2},marker:{size:6}},
    ];
    gems.forEach((g,gi)=>{ if(p.per_gem&&p.per_gem[g]) traces.push(
      {x:p.values,y:p.per_gem[g],name:g+" (contribution)",mode:"lines+markers",
       line:{color:pal[gi%pal.length],width:2.2,dash:"dash"},marker:{size:6}}); });
    Plotly.newPlot(div.id,traces,{margin:{l:72,r:30,t:14,b:46},
      xaxis:{title:p.label,tickvals:p.values},yaxis:{tickprefix:"$",separatethousands:true},
      legend:{orientation:"h",y:1.16},hovermode:"x unified"},{displayModeBar:false,responsive:true});
  });
  if(!Object.keys(P).length) host.innerHTML='<p class="sub">No sweeps recorded yet.</p>';
});
</script></body></html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
