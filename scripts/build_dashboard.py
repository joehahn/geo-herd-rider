"""build_dashboard.py — the portfolio dashboard: $50K through this solution's trades.

Backtests the curator end-to-end over a calendar window (default 2025-11-01 -> present)
as a single, faithfully-traded book: start in cash, deploy when a trigger fires, hold for
the event's horizon, rotate back to cash. Renders an interactive HTML dashboard comparable
to portfolio-wave-rider's — a portfolio-value timeseries vs SPY, plus an allocation-over-
time stacked area (cash -> SBLK/FRO/... -> cash) — and a linked child page (tree.html) that
lays the curator's decision ladders out on a timeline.

Two books are shown side by side, because the contrast IS the thesis:
  - CURATED (middle band)  — what the solution actually recommends: chain_depth >= 2 and
    NOT a megaphone call. Deliberately sparse (the filter is brutal).
  - ALL SIGNALS            — every mapped trigger, including the loud/obvious oil calls the
    thesis says are already grazed. Shown for contrast/context.

Capital policy (locked with the user): FULLY-INVESTED book — split equity equally across
every currently-active event, equal capital per event; 100% cash when none active. Idle
capital earns 0% (no bond yield assumed). Within a long event, mean-variance weights on a
look-ahead-safe trailing lookback (reused verbatim from curator.py); shorts equal-weighted
(the production optimizer is long-only — flagged, not silently dropped).

HONESTY (repo discipline #4/#6): this is a RETROSPECTIVE backtest over a single loud-regime
window (the 2026 Iran war), and the curator LLM may have seen hindsight via web search, so
every number here is an UPPER BOUND. The clean test is the forward eval (src/forward.py).

    python scripts/build_dashboard.py            # window 2025-11-01 -> today
    python scripts/build_dashboard.py --start 2025-11-01 --capital 50000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import score  # noqa: E402
import curator  # noqa: E402
from optimizer import load_financial_model  # noqa: E402

OUT_DIR = ROOT / "docs"  # GitHub Pages serves this folder (Settings -> Pages -> main /docs)
TRIGGERS_CSV = ROOT / "data" / "windows" / "iran.csv"
MAPPED_CSV = ROOT / "data" / "windows" / "iran_mapped.csv"
LOOKBACK = curator.BACKTEST_LOOKBACK_DAYS  # 547d trailing optimizer fit

# A stable, distinct color per ticker (cash is grey). Falls back to a palette cycle.
PALETTE = ["#c0392b", "#2980b9", "#27ae60", "#8e44ad", "#e67e22", "#16a085",
           "#d35400", "#2c3e50", "#c0a000", "#7f8c8d", "#1abc9c", "#9b59b6",
           "#e74c3c", "#3498db", "#f39c12", "#34495e"]


def load_events() -> pd.DataFrame:
    """Triggers joined to their curator mappings, with the middle-band keep flag."""
    trig = pd.read_csv(TRIGGERS_CSV)
    mapped = pd.read_csv(MAPPED_CSV)
    df = trig.merge(mapped.drop(columns=[c for c in ("source", "telegraph_text", "telegraph_ts")
                                         if c in mapped.columns]), on="event_id")
    df["keep"] = curator.middle_band_mask(df)
    return df.sort_values("telegraph_ts").reset_index(drop=True)


def build_trades(events: pd.DataFrame, panel: pd.DataFrame, fm: dict) -> list[dict]:
    """One trade per event: entry/exit sessions, signed direction, per-leg weights.
    Long legs use the look-ahead-safe mean-variance fit; shorts equal-weight (long-only
    optimizer can't size them). Trades whose window runs past available prices are kept
    but flagged truncated so the dashboard can show the open position honestly."""
    spy = panel[score.BENCHMARK].dropna()
    days = spy.index
    trades = []
    for _, ev in events.iterrows():
        tickers = [t.strip().upper() for t in str(ev["mapped_tickers"]).split(";") if t.strip()]
        ei = score.entry_index(days, ev["telegraph_ts"])
        if ei is None:
            continue
        entry_d = days[ei]
        xi = score.exit_index(days, ei, ev["horizon_days"])
        truncated = xi is None
        exit_d = days[xi] if xi is not None else days[-1]
        is_long = str(ev["direction"]).lower() == "long"

        weights = None
        if is_long:
            weights = curator._optimized_weights(tickers, panel, entry_d, fm, LOOKBACK)
        if not weights:  # short, or optimizer abstained -> equal-weight what has prices at entry
            usable = [t for t in tickers if t in panel.columns
                      and panel.loc[:entry_d, t].notna().any()]
            if not usable:
                continue
            weights = {t: 1.0 / len(usable) for t in usable}

        thin = any(t in score.THIN_TICKERS for t in weights)
        trades.append({
            "event_id": ev["event_id"], "entry": entry_d, "exit": exit_d,
            "sign": 1.0 if is_long else -1.0, "weights": weights, "keep": bool(ev["keep"]),
            "haircut": score.HAIRCUT_THIN if thin else score.HAIRCUT_DEFAULT,
            "truncated": truncated, "direction": "long" if is_long else "short",
        })
    return trades


def simulate(trades: list[dict], panel: pd.DataFrame, start: str, capital: float) -> dict:
    """Fully-invested book: each session, equal capital across active events; cash if none.
    Returns the daily value series and the per-ticker allocation matrix (capital committed,
    sign-agnostic; cash fills the remainder)."""
    days = panel.index[panel.index >= pd.Timestamp(start)]
    daily_ret = panel.pct_change()

    legs = pd.DataFrame(index=days)            # signed daily return per active event
    alloc = pd.DataFrame(0.0, index=days, columns=sorted(
        {t for tr in trades for t in tr["weights"]}))
    active_count = pd.Series(0.0, index=days)

    for tr in trades:
        held = list(tr["weights"])
        w = pd.Series(tr["weights"])
        win = days[(days >= tr["entry"]) & (days <= tr["exit"])]
        if len(win) == 0:
            continue
        leg = (daily_ret.loc[win[0]:win[-1], held] * w).sum(axis=1) * tr["sign"]
        leg.iloc[0] = -tr["haircut"]           # round-trip cost charged on entry day
        legs[tr["event_id"]] = leg.reindex(days)
        active_count.loc[win] += 1.0
        for t in held:                         # capital committed (magnitude), per event share
            alloc.loc[win, t] += w[t]

    strat_daily = legs.mean(axis=1).fillna(0.0)  # equal capital across active legs; cash if idle
    value = capital * (1.0 + strat_daily).cumprod()

    # Normalize allocation by number of active events (equal capital per event), cash = remainder.
    share = active_count.replace(0.0, pd.NA)
    alloc = alloc.div(share, axis=0).fillna(0.0)
    cash = (1.0 - alloc.sum(axis=1)).clip(lower=0.0)
    alloc = alloc.loc[:, (alloc.abs().sum() > 1e-9)]  # drop never-held tickers

    return {"dates": [d.strftime("%Y-%m-%d") for d in days],
            "value": [round(v, 2) for v in value],
            "alloc": {t: [round(x, 4) for x in alloc[t]] for t in alloc.columns},
            "cash": [round(x, 4) for x in cash]}


def metrics(value: list[float], spy_value: list[float], capital: float) -> dict:
    v = pd.Series(value)
    peak = v.cummax()
    mdd = float(((v - peak) / peak).min())
    return {"final": round(value[-1], 0), "total_ret": round(value[-1] / capital - 1, 4),
            "spy_ret": round(spy_value[-1] / capital - 1, 4), "max_dd": round(mdd, 4)}


def color_map(tickers: list[str]) -> dict:
    return {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(sorted(tickers))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2025-11-01", help="backtest window start (default 2025-11-01)")
    ap.add_argument("--capital", type=float, default=50_000.0, help="starting capital (default 50000)")
    args = ap.parse_args(argv)

    events = load_events()
    fm = load_financial_model(str(ROOT / "investor_profile.md"))

    tickers = {score.BENCHMARK}
    for cell in events["mapped_tickers"]:
        tickers.update(t.strip().upper() for t in str(cell).split(";") if t.strip())
    panel_start = (pd.Timestamp(args.start) - pd.Timedelta(days=LOOKBACK + 14)).strftime("%Y-%m-%d")
    panel_end = (pd.Timestamp.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Fetching {len(tickers)} tickers, {panel_start} .. {panel_end} ...")
    panel = score.fetch_panel(sorted(tickers), panel_start, panel_end, use_cache=False)

    trades = build_trades(events, panel, fm)
    curated = [t for t in trades if t["keep"]]
    print(f"Trades: {len(trades)} all-signals, {len(curated)} curated (middle band).")

    books = {"all": simulate(trades, panel, args.start, args.capital),
             "curated": simulate(curated, panel, args.start, args.capital)}

    spy = panel[score.BENCHMARK].reindex(pd.to_datetime(books["all"]["dates"])).ffill()
    spy_value = [round(args.capital * v, 2) for v in (spy / spy.iloc[0]).tolist()]

    payload = {
        "start": args.start, "capital": args.capital,
        "dates": books["all"]["dates"], "spy_value": spy_value,
        "books": {k: {"value": b["value"], "alloc": b["alloc"], "cash": b["cash"]}
                  for k, b in books.items()},
        "metrics": {k: metrics(b["value"], spy_value, args.capital) for k, b in books.items()},
        "colors": color_map([t for b in books.values() for t in b["alloc"]]),
        "events": [{
            "id": e["event_id"], "date": e["telegraph_ts"][:10], "source": e["source"],
            "text": e["telegraph_text"], "mechanism": e["mechanism"],
            "tickers": [t.strip().upper() for t in str(e["mapped_tickers"]).split(";") if t.strip()],
            "direction": e["direction"], "depth": int(float(e["chain_depth"])),
            "audience": e["audience_breadth"], "horizon": int(float(e["horizon_days"])),
            "keep": bool(e["keep"]),
        } for _, e in events.iterrows()],
    }

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "data.json").write_text(json.dumps(payload, indent=2))
    (OUT_DIR / "index.html").write_text(INDEX_HTML)
    (OUT_DIR / "tree.html").write_text(TREE_HTML)
    m = payload["metrics"]
    print(f"\nCurated  ${args.capital:,.0f} -> ${m['curated']['final']:,.0f} "
          f"({m['curated']['total_ret']:+.1%}), maxDD {m['curated']['max_dd']:.1%}")
    print(f"AllSig   ${args.capital:,.0f} -> ${m['all']['final']:,.0f} "
          f"({m['all']['total_ret']:+.1%}), maxDD {m['all']['max_dd']:.1%}")
    print(f"SPY      ${args.capital:,.0f} -> ${spy_value[-1]:,.0f} ({m['all']['spy_ret']:+.1%})")
    print(f"\nWrote {OUT_DIR/'index.html'} + tree.html + data.json")
    print("Open: python -m http.server -d docs  (then visit localhost:8000)")
    return 0


INDEX_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>geo-herd-rider — $50K portfolio backtest</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
 :root{--ink:#1a1a1a;--mut:#666;--line:#e3e3e3;--bg:#fafafa}
 body{font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);
   margin:0;background:var(--bg)}
 .wrap{max-width:960px;margin:0 auto;padding:28px 20px 60px}
 h1{font-size:25px;margin:0 0 4px} h2{font-size:18px;margin:34px 0 6px}
 .sub{color:var(--mut);margin:0 0 18px}
 .warn{background:#fff8e1;border:1px solid #f0d98c;border-radius:8px;padding:10px 14px;
   font-size:13px;color:#5a4a00;margin:14px 0 22px}
 .cards{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0 6px}
 .card{flex:1;min-width:150px;background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px}
 .card .k{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.03em}
 .card .v{font-size:22px;font-weight:600;margin-top:3px}
 .pos{color:#1e7d34} .neg{color:#c0392b}
 .toggle{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;margin:6px 0}
 .toggle button{border:0;background:#fff;padding:7px 14px;font-size:13px;cursor:pointer;color:var(--mut)}
 .toggle button.on{background:#2c3e50;color:#fff}
 a{color:#2980b9} .foot{color:var(--mut);font-size:12px;margin-top:30px;border-top:1px solid var(--line);padding-top:12px}
 #chart,#alloc{width:100%;height:420px}
</style></head>
<body><div class="wrap">
 <h1>Through the herd, on $50K</h1>
 <p class="sub" id="sub"></p>
 <div class="warn"><b>Retrospective, upper-bound.</b> One loud-regime window (the 2026 Iran war) —
   the only window with curated triggers. The curator LLM may have absorbed hindsight via web
   search, so these returns are an <b>upper bound</b>, not a forward result. Clean test = the
   forward eval (<code>src/forward.py</code>). See <a href="tree.html">the decision-tree timeline →</a></p>

 <div class="cards" id="cards"></div>

 <h2>Portfolio value</h2>
 <p class="sub">Three books from the same $50K: the <b>curated</b> middle-band book (what the
   solution recommends), the <b>all-signals</b> book (every trigger, incl. obvious oil calls),
   and <b>SPY</b> buy-and-hold.</p>
 <div id="chart"></div>

 <h2>Allocation over time</h2>
 <div class="toggle" id="btoggle">
   <button data-b="curated" class="on">Curated book</button>
   <button data-b="all">All-signals book</button>
 </div>
 <p class="sub">Capital committed per ticker (cash fills the rest). Fully-invested policy:
   equity splits equally across active events; back to cash when none are live.</p>
 <div id="alloc"></div>

 <p class="foot">geo-herd-rider · generated by <code>scripts/build_dashboard.py</code> ·
   sizing = mean-variance (long legs), equal-weight (shorts) · idle = cash @ 0%.</p>
</div>
<script>
fetch("data.json").then(r=>r.json()).then(D=>{
  const fmt=x=>"$"+Math.round(x).toLocaleString();
  const pct=x=>(x>=0?"+":"")+(x*100).toFixed(1)+"%";
  document.getElementById("sub").textContent =
    `Backtest ${D.start} → present · $${D.capital.toLocaleString()} start · faithfully traded`;

  const m=D.metrics.curated, ma=D.metrics.all;
  const cls=x=>x>=0?"pos":"neg";
  document.getElementById("cards").innerHTML = [
    ["Curated final", fmt(m.final), pct(m.total_ret), cls(m.total_ret)],
    ["All-signals final", fmt(ma.final), pct(ma.total_ret), cls(ma.total_ret)],
    ["SPY buy & hold", fmt(D.spy_value[D.spy_value.length-1]), pct(ma.spy_ret), cls(ma.spy_ret)],
    ["Curated max drawdown", pct(m.max_dd), "", cls(m.max_dd)],
  ].map(([k,v,s,c])=>`<div class="card"><div class="k">${k}</div><div class="v ${c}">${v}</div>
     <div class="sub" style="margin:0;font-size:12px">${s}</div></div>`).join("");

  Plotly.newPlot("chart",[
    {x:D.dates,y:D.books.curated.value,name:"Curated (middle band)",line:{color:"#c0392b",width:2.4}},
    {x:D.dates,y:D.books.all.value,name:"All signals",line:{color:"#2980b9",width:1.8}},
    {x:D.dates,y:D.spy_value,name:"SPY",line:{color:"#9aa0a6",width:1.6,dash:"dot"}},
  ],{margin:{l:60,r:16,t:10,b:36},legend:{orientation:"h",y:1.12},
     yaxis:{tickprefix:"$",separatethousands:true},hovermode:"x unified"},{displayModeBar:false,responsive:true});

  function drawAlloc(book){
    const b=D.books[book], traces=[];
    for(const t in b.alloc) traces.push({x:D.dates,y:b.alloc[t].map(v=>v*100),name:t,
      stackgroup:"a",line:{width:0},fillcolor:D.colors[t]||"#bbb",hovertemplate:"%{y:.0f}%"});
    traces.push({x:D.dates,y:b.cash.map(v=>v*100),name:"cash",stackgroup:"a",
      line:{width:0},fillcolor:"#dfe3e6",hovertemplate:"%{y:.0f}%"});
    Plotly.newPlot("alloc",traces,{margin:{l:48,r:16,t:10,b:36},
      yaxis:{ticksuffix:"%",range:[0,100]},legend:{orientation:"h",y:1.14},
      hovermode:"x unified"},{displayModeBar:false,responsive:true});
  }
  drawAlloc("curated");
  document.querySelectorAll("#btoggle button").forEach(btn=>btn.onclick=()=>{
    document.querySelectorAll("#btoggle button").forEach(b=>b.classList.remove("on"));
    btn.classList.add("on"); drawAlloc(btn.dataset.b);
  });
});
</script></body></html>
"""


TREE_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>geo-herd-rider — decision-tree timeline</title>
<style>
 :root{--ink:#1a1a1a;--mut:#666;--line:#e3e3e3;--bg:#fafafa}
 body{font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);margin:0;background:var(--bg)}
 .wrap{max-width:860px;margin:0 auto;padding:28px 20px 60px}
 h1{font-size:24px;margin:0 0 4px} .sub{color:var(--mut);margin:0 0 20px} a{color:#2980b9}
 .ev{background:#fff;border:1px solid var(--line);border-left:5px solid #9aa0a6;border-radius:10px;
   padding:13px 16px;margin:0 0 14px;position:relative}
 .ev.keep{border-left-color:#c0392b}
 .ev .top{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:baseline}
 .date{font-weight:600} .src{color:var(--mut);font-size:13px}
 .badges{margin:7px 0} .b{display:inline-block;font-size:11px;padding:2px 8px;border-radius:20px;
   margin-right:6px;background:#eef1f3;color:#445}
 .b.depth{background:#e8f0fe;color:#1a56c4} .b.keep{background:#fae3e0;color:#c0392b;font-weight:600}
 .b.drop{background:#eef1f3;color:#888} .b.short{background:#fdebd0;color:#9c5700}
 .text{font-size:13.5px;color:#333;margin:6px 0} .mech{font-size:13.5px;margin:8px 0 6px}
 .mech b{color:var(--mut);font-weight:600}
 .chip{display:inline-block;font-size:12px;font-family:ui-monospace,Menlo,monospace;
   padding:2px 8px;border-radius:6px;background:#2c3e50;color:#fff;margin:2px 5px 2px 0}
 .legend{font-size:13px;color:var(--mut);margin:0 0 18px}
</style></head>
<body><div class="wrap">
 <h1>Decision-tree timeline</h1>
 <p class="sub"><a href="index.html">← back to the portfolio dashboard</a></p>
 <p class="legend">Each trigger and the causal ladder the curator pruned from it. A
   <b style="color:#c0392b">red</b> left-edge = kept by the middle-band filter (the bet);
   grey = dropped (megaphone / too shallow). <code>depth</code> = hops from the trigger.</p>
 <div id="tl"></div>
</div>
<script>
fetch("data.json").then(r=>r.json()).then(D=>{
  document.getElementById("tl").innerHTML = D.events.map(e=>`
    <div class="ev ${e.keep?'keep':''}">
      <div class="top"><span class="date">${e.date} · ${e.id}</span><span class="src">${e.source}</span></div>
      <div class="text">“${e.text}”</div>
      <div class="badges">
        <span class="b depth">depth ${e.depth} (${e.depth===1?'hop-1 obvious':e.depth>=4?'deep/speculative':'middle band'})</span>
        <span class="b">${e.audience}</span>
        ${e.direction==='short'?'<span class="b short">SHORT</span>':''}
        <span class="b">${e.horizon}d horizon</span>
        ${e.keep?'<span class="b keep">KEPT — the bet</span>':'<span class="b drop">dropped</span>'}
      </div>
      <div class="mech"><b>ladder:</b> ${e.mechanism}</div>
      <div>${e.tickers.map(t=>`<span class="chip">${t}</span>`).join("")}</div>
    </div>`).join("");
});
</script></body></html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
