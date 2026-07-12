"""build_retrieval_dashboard.py — render the bi-weekly Tavily retrieval backtest as a standalone HTML.

Reads data/retrieval_backtest.json (from scripts/retrieval_backtest.py) and writes a self-contained page
(inline SVG, no deps) spanning START..END (forward day-1), showing: the single-stock-vs-ETF-wrapper
retrievability finding, a per-gem detection timeline (by-name vs thesis vs peak), the detection table, the
un-planted candidate gems the sweep surfaced, and the run's cost block. Output -> docs_preview/ (gitignored
local preview). The retrieval backtest is an UPPER BOUND (look-ahead-leaky web search, known winners) — the
forward paper trade is the verdict; the page says so.
"""
from __future__ import annotations
import html
import json
from datetime import date, datetime, timedelta
from pathlib import Path

README_URL = "https://github.com/joehahn/geo-herd-rider/blob/main/README.md"

PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "data" / "retrieval_backtest.json"
OUT = REPO / "docs_preview" / "retrieval_backtest.html"

FORM_COLOR = {"single stock": "#2f9e44", "ETF wrapper": "#f08c00", "foreign ADR": "#868e96"}
DISPLAY_FORM = {"single stock": "stock", "ETF wrapper": "ETF", "foreign ADR": "ADR"}   # badge label
LANE_ORDER = ["MP", "AREC", "TSM", "KGC", "HL", "DRAM", "BWET", "GDX", "RNMBY"]   # single stocks, then ETFs, then ADR
CAPTION = {  # per-gem storyline (why it moved) + the retrieval timing vs the actual price peak
    "MP": "The only US rare-earth producer — rallied as the US moved to break its dependence on Chinese "
          "rare earths (China's Apr-2025 export controls, then a July DoD equity stake). Named by ticker "
          "at the base of the run-up, <b>~7½ months before its Oct-2025 peak</b>.",
    "AREC": "American Resources / ReElement — a tiny critical-minerals small-cap riding the <b>same 2025 "
            "rare-earth / China-decoupling thesis as MP</b> (US rare-earth supply chain, NdFeB magnets, "
            "gallium for defense). Higher-beta MP sibling: <b>16.9× to Oct-2025, then −74%</b>.",
    "TSM": "Taiwan Semiconductor (TSMC) — the AI/datacenter <b>chip-foundry</b> thesis (advanced-node demand "
           "from the AI boom). Mega-cap, the #1 candidate ticker the retriever named on its own: <b>~3.4× off "
           "the Apr-2025 tariff bottom</b>, then plateaued near the high (a secular winner more than a decayed gem).",
    "KGC": "Kinross Gold — a <b>gold miner</b> riding the 2025-26 gold bull run (a GDX constituent). "
           "<b>~4× to Jan-2026</b>, then pulled back with the metal.",
    "HL": "Hecla Mining — the largest US <b>silver miner</b>; rode the silver / precious-metals rally. "
          "<b>~7× to Jan-2026, then −56%</b> — a classic run-and-decay gem.",
    "DRAM": "Roundhill Memory ETF (launched 2026-04-02) — plays the DRAM/HBM memory shortage driven by the "
            "AI-datacenter boom. ETF named <b>~6 weeks before peak</b>; the memory thesis (hollow) was "
            "visible months earlier, <b>before the fund even existed</b>.",
    "BWET": "Breakwave Tanker Shipping ETF — crude-tanker (VLCC) freight rates spiking on the 2026 "
            "Strait-of-Hormuz / Iran crisis. Named by ticker only <b>after a ~600% run</b> (still ~2 months "
            "before the top); the tanker thesis (hollow) showed earlier.",
    "GDX": "VanEck Gold Miners ETF — the 2025-26 gold bull run (safe-haven + rate-cut demand). "
           "<b>Never named by ticker</b>; only the gold-miner thesis + individual miners (HMY/HL/KGC) surfaced.",
    "RNMBY": "Rheinmetall (German defense ADR) — European rearmament after Germany's Mar-2025 debt-brake "
             "defense-spending exemption + the Ukraine war. Named only <b>in hindsight</b>; the whole "
             "rise-and-fall went uncovered by the US-centric beats.",
}


def _x(d: str, lo: date, span: int, x0: float, w: float) -> float:
    return x0 + (date.fromisoformat(d) - lo).days / span * w


def _price_chart(g: str, ch: dict, det: dict) -> str:
    """Ticker vs SPY over the gem's era, BOTH normalized to 1.0 at the window start (so the y-axis is a
    growth multiple and out/under-performance is visible), with a blue dot for every by-name article.
    Each dot carries data-* (raw $ price, date, lede) for the hover popup."""
    W, x0, y0, plotw, ploth = 1080, 52, 14, 1004, 150
    ws, we = date.fromisoformat(ch["window"][0]), date.fromisoformat(ch["window"][1])
    span = (we - ws).days or 1
    series, spy = ch["series"], ch.get("spy_series", [])
    if not series:
        return '<svg viewBox="0 0 100 20" width="100%"><text x="4" y="14" font-size="11" fill="#adb5bd">no price data</text></svg>'
    t0 = series[0][1]                                   # normalization base (first close in window)
    tnorm = [(d, v / t0) for d, v in series]
    snorm = [(d, v / spy[0][1]) for d, v in spy] if spy else []
    allv = [v for _, v in tnorm] + [v for _, v in snorm]
    vmin, vmax = min(allv), max(allv)
    pad = (vmax - vmin) * 0.06 or 0.1
    vmin, vmax = vmin - pad, vmax + pad

    def X(dstr):
        return x0 + (date.fromisoformat(dstr) - ws).days / span * plotw

    def Y(v):
        return y0 + ploth - (v - vmin) / (vmax - vmin) * ploth

    s = [f'<svg viewBox="0 0 {W} {y0+ploth+26}" width="100%" style="max-width:{W}px">']
    for v in sorted({round(vmin + pad, 1), 1.0, round(vmax - pad, 1)}):   # y gridlines as growth multiples
        if not (vmin <= v <= vmax):
            continue
        y = Y(v)
        s.append(f'<line x1="{x0}" y1="{y:.0f}" x2="{x0+plotw}" y2="{y:.0f}" '
                 f'stroke="{"#dee2e6" if v == 1.0 else "#f1f3f5"}"/>')
        s.append(f'<text x="{x0-6}" y="{y+3:.0f}" font-size="9" fill="#adb5bd" text-anchor="end">{v:g}×</text>')
    yq, mq = ws.year, ws.month                          # monthly x ticks
    while date(yq, mq, 1) <= we:
        if date(yq, mq, 1) >= ws:
            gx = X(date(yq, mq, 1).isoformat())
            s.append(f'<line x1="{gx:.0f}" y1="{y0}" x2="{gx:.0f}" y2="{y0+ploth}" stroke="#f8f9fa"/>')
            s.append(f'<text x="{gx:.0f}" y="{y0+ploth+15}" font-size="8.5" fill="#adb5bd" '
                     f'text-anchor="middle">{yq}-{mq:02d}</text>')
        mq += 1
        if mq > 12:
            mq = 1; yq += 1
    if snorm:                                            # SPY benchmark (muted, dashed) under the ticker
        pts = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in snorm)
        s.append(f'<polyline points="{pts}" fill="none" stroke="#adb5bd" stroke-width="1.2" stroke-dasharray="4,3"/>')
        s.append(f'<text x="{X(snorm[-1][0])-2:.0f}" y="{Y(snorm[-1][1])-3:.0f}" font-size="9" '
                 f'fill="#868e96" text-anchor="end">SPY {snorm[-1][1]:.1f}×</text>')
    pts = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in tnorm)
    s.append(f'<polyline points="{pts}" fill="none" stroke="#495057" stroke-width="1.7"/>')
    s.append(f'<text x="{X(tnorm[-1][0])-2:.0f}" y="{Y(tnorm[-1][1])-3:.0f}" font-size="9.5" '
             f'fill="#343a40" font-weight="600" text-anchor="end">{g} {tnorm[-1][1]:.1f}×</text>')
    pk = det["peak"]
    if ws <= date.fromisoformat(pk) <= we:
        px = X(pk)
        s.append(f'<line x1="{px:.0f}" y1="{y0}" x2="{px:.0f}" y2="{y0+ploth}" stroke="#e03131" '
                 f'stroke-width="1.3" stroke-dasharray="3,2"/>')
        s.append(f'<text x="{px+4:.0f}" y="{y0+10}" font-size="9" fill="#e03131">peak</text>')
    base_y = y0 + ploth - 4
    for d in ch["dots"]:
        dx = X(d["d"])
        dy = base_y if d["price"] is None else Y(d["price"] / t0)
        pr = f"${d['price']:,.2f}" if d["price"] is not None else "pre-inception (no price)"
        s.append(
            f'<circle class="dot" cx="{dx:.1f}" cy="{dy:.1f}" r="4.6" fill="#1c7ed6" stroke="#1c7ed6" '
            f'stroke-width="1.5" data-date="{d["d"]}" data-price="{html.escape(pr)}" data-kind="by-name" '
            f'data-src="{html.escape(d["src"])}" data-title="{html.escape(d["title"])}" '
            f'data-lede="{html.escape(d["lede"])}" data-url="{html.escape(d["url"])}"/>')
    # ground-truth overlay: target superlative articles — big blue dot = DETECTED, orange square = MISSED
    for a in ch.get("ground_truth", []):
        dx = X(a["d"])
        dy = base_y if a["price"] is None else Y(a["price"] / t0)
        pr = f"${a['price']:,.2f}" if a["price"] is not None else "pre-inception"
        attrs = (f'data-date="{a["d"]}" data-price="{html.escape(pr)}" '
                 f'data-src="{html.escape(a["src"])}" data-title="{html.escape(a["title"])}" '
                 f'data-lede="" data-url="{html.escape(a["url"])}"')
        if a["detected"]:
            s.append(f'<circle class="dot" cx="{dx:.1f}" cy="{dy:.1f}" r="7.5" fill="#1c7ed6" stroke="#0b3d91" '
                     f'stroke-width="2" data-kind="TARGET ✓ detected (Tavily)" {attrs}/>')
        elif a.get("forward_reachable"):     # Tavily missed it, but the forward (Anthropic) can reach it
            s.append(f'<rect class="dot" x="{dx-6:.1f}" y="{dy-6:.1f}" width="12" height="12" fill="none" '
                     f'stroke="#2f9e44" stroke-width="2.4" data-kind="TARGET ✗ missed by Tavily, ✓ forward-reachable (Anthropic)" {attrs}/>')
        else:                                # missed by both engines (Anthropic-blocked or not indexed)
            s.append(f'<rect class="dot" x="{dx-6:.1f}" y="{dy-6:.1f}" width="12" height="12" fill="none" '
                     f'stroke="#f76707" stroke-width="2.2" data-kind="TARGET ✗ MISSED by both engines" {attrs}/>')
    s.append("</svg>")
    return "".join(s)


def _diag(res: dict) -> str:
    """Plotly JS for every retrieval-diagnostic plot. Divs must already exist in the page."""
    J = json.dumps
    BLUE, GREEN, GREY, RED = "'#4dabf7'", "'#2f9e44'", "'#adb5bd'", "'#e03131'"
    daily = res["daily"]
    monthly, quarterly = res["monthly"], res["quarterly"]
    beats = res["beat_counts"][::-1]                     # ascending -> largest on top (horizontal bars)
    bnames = [b for b, _ in beats]
    uniq = [res["beat_unique"].get(b, 0) for b in bnames]
    bytk = sorted(res["beat_byname"].items(), key=lambda kv: kv[1])
    doms = res["domain_counts"][:25][::-1]
    dklass = {"specialty": GREEN, "mill": RED, "other": GREY}
    dcolors = "[" + ",".join(dklass[k] for _, _, k in doms) + "]"
    ps = res["pass_split"]

    def bar(div, x, y, extra="", color=BLUE, layout=""):
        return ("Plotly.newPlot('" + div + "',[{x:" + J(x) + ",y:" + J(y) + ",type:'bar',marker:{color:"
                + color + "}" + extra + "}],{margin:{t:10,r:10,b:40},bargap:0.08," + layout
                + "},{displayModeBar:false,responsive:true});")
    return (
        bar("newshist", [d for d, _ in daily], [c for _, c in daily], layout="yaxis:{title:'articles / day'}")
        + bar("monthhist", [d for d, _ in monthly], [c for _, c in monthly], layout="yaxis:{title:'articles / month'}")
        + bar("quarterhist", [d for d, _ in quarterly], [c for _, c in quarterly], layout="yaxis:{title:'articles / quarter'}")
        + bar("weekhist", [d for d, _ in _weekly(daily)], [c for _, c in _weekly(daily)], layout="yaxis:{title:'articles / week'}")
        + bar("dowhist", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], res["dow"], layout="yaxis:{title:'articles'}")
        # per-beat: total (blue) + unique-only (dark) grouped
        + "Plotly.newPlot('beathist',[{type:'bar',orientation:'h',name:'total',y:" + J(bnames) + ",x:"
        + J([c for _, c in beats]) + ",marker:{color:" + BLUE + "}},{type:'bar',orientation:'h',name:'unique',y:"
        + J(bnames) + ",x:" + J(uniq) + ",marker:{color:'#1c7ed6'}}],{barmode:'group',margin:{l:255,r:20,t:10,b:34},"
        "xaxis:{title:'articles surfaced (total vs only-this-beat)'},yaxis:{automargin:true,tickfont:{size:10}},"
        "legend:{orientation:'h'}},{displayModeBar:false,responsive:true});"
        # by-name yield per beat
        + "Plotly.newPlot('bynamehist',[{type:'bar',orientation:'h',y:" + J([b for b, _ in bytk]) + ",x:"
        + J([n for _, n in bytk]) + ",marker:{color:" + GREEN + "},hovertemplate:'%{y}<br>%{x} gem-naming"
        " articles<extra></extra>'}],{margin:{l:255,r:20,t:10,b:34},xaxis:{title:'articles that name a gem ticker'},"
        "yaxis:{automargin:true,tickfont:{size:10}}},{displayModeBar:false,responsive:true});"
        # per-domain, colored by allow/block/other
        + "Plotly.newPlot('domhist',[{type:'bar',orientation:'h',y:" + J([d for d, _, _ in doms]) + ",x:"
        + J([n for _, n, _ in doms]) + ",marker:{color:" + dcolors + "},hovertemplate:'%{y}<br>%{x} articles"
        "<extra></extra>'}],{margin:{l:175,r:20,t:10,b:34},xaxis:{title:'articles (green=allowlist, grey=other,"
        " red=blocklist)'},yaxis:{automargin:true,tickfont:{size:10}}},{displayModeBar:false,responsive:true});"
        # gem-pass vs coverage-pass split
        + "Plotly.newPlot('passhist',[{type:'bar',x:" + J(["allowlist pass only (specialty_allow)", "blocklist pass only (all − mill_block)", "both passes"])
        + ",y:" + J([ps["gem_only"], ps["coverage_only"], ps["both"]]) + ",marker:{color:[" + GREEN + "," + GREY
        + "," + BLUE + "]}}],{margin:{t:10,r:10,b:40,l:45},yaxis:{title:'articles'},bargap:0.4},"
        "{displayModeBar:false,responsive:true});")


def _weekly(daily):
    wk = {}
    for d, c in daily:
        dd = date.fromisoformat(d)
        wk[(dd - timedelta(days=dd.weekday())).isoformat()] = wk.get((dd - timedelta(days=dd.weekday())).isoformat(), 0) + c
    return sorted(wk.items())


def build() -> Path:
    res = json.loads(SRC.read_text())
    c = res["cost"]
    rows = []
    for g in LANE_ORDER:
        d = res["detection"][g]
        lead = ("—" if d["lead_days"] is None
                else f'<b style="color:#2f9e44">+{d["lead_days"]}d early</b>' if d["lead_days"] > 7
                else f'<b style="color:#e8590c">at peak</b>' if d["lead_days"] >= 0
                else f'<b style="color:#e03131">{d["lead_days"]}d (post-peak)</b>')
        eb = d["earliest_by_name"] or "<span style='color:#e03131'>never</span>"
        rows.append(
            f'<tr><td><b>{g}</b></td><td><span class="badge" style="background:{FORM_COLOR[d["form"]]}">'
            f'{d["form"]}</span></td><td>{d["by_name"]}</td><td>{d["thesis"]}</td><td>{eb}</td>'
            f'<td>{d["earliest_thesis"] or "—"}</td><td>{d["peak"]}</td><td>{lead}</td></tr>')
    table = "\n".join(rows)
    css = """
    body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#212529;max-width:1180px;margin:24px auto;padding:0 18px}
    h1{font-size:23px;margin:0 0 2px} h2{font-size:16px;margin:30px 0 8px;border-bottom:2px solid #f1f3f5;padding-bottom:4px}
    .sub{color:#868e96;font-size:13px;margin-bottom:14px}
    .cost{display:flex;gap:22px;flex-wrap:wrap;background:#f8f9fa;border:1px solid #e9ecef;border-radius:8px;padding:12px 16px;margin:10px 0}
    .cost b{font-size:19px;display:block;color:#0b7285} .cost span{font-size:11px;color:#868e96}
    .finding{background:#fff9db;border:1px solid #ffe066;border-radius:8px;padding:14px 18px;margin:14px 0}
    .finding b{color:#e8590c}
    table{border-collapse:collapse;width:100%;font-size:13px} th,td{padding:6px 10px;text-align:left;border-bottom:1px solid #f1f3f5}
    th{color:#868e96;font-weight:600;font-size:11px;text-transform:uppercase}
    .badge{color:#fff;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:600}
    .legend{font-size:12px;color:#495057;margin:6px 0 0} .legend b{color:#2f9e44}
    .cand{display:flex;align-items:center;gap:8px;margin:2px 0;font-size:12px}
    .cand .sym{width:52px;font-weight:600;font-family:monospace} .cand .bar{height:11px;background:#4dabf7;border-radius:2px}
    .cand .n{color:#868e96} .cols{display:grid;grid-template-columns:1fr 1fr;gap:8px 30px}
    .note{font-size:12px;color:#868e96;background:#f8f9fa;border-radius:8px;padding:12px 16px;margin-top:8px}
    .dot{cursor:pointer} .dot:hover{stroke-width:2.8}
    .pcwrap{margin:6px 0 18px} .pchead{font-size:13px;color:#495057;margin:16px 0 0}
    .pchead .tk{font-weight:700;font-size:15px;color:#212529} .pchead .bd{color:#fff;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;margin:0 7px}
    #tt{position:fixed;display:none;max-width:360px;background:#212529;color:#f1f3f5;padding:9px 11px;border-radius:6px;font-size:12px;line-height:1.45;box-shadow:0 4px 16px rgba(0,0,0,.28);pointer-events:none;z-index:20}
    #tt b{color:#fff} #tt .lede{color:#ced4da;margin-top:4px;font-style:italic}
    .nav{display:flex;align-items:center;gap:4px;background:#212529;padding:8px 16px;margin:-24px -18px 18px;border-radius:0 0 8px 8px}
    .nav a{color:#ced4da;text-decoration:none;font-size:13px;padding:5px 12px;border-radius:5px}
    .nav a.active{background:#1c7ed6;color:#fff;font-weight:600}
    .nav a:not(.soon):hover{background:#343a40;color:#fff}
    .nav a.soon{color:#6c757d;cursor:default;border:1px dashed #495057}
    .nav .gen{margin-left:auto;color:#868e96;font-size:11px}
    """
    nav = (f'<nav class="nav"><a href="retrieval_backtest.html" class="active">Retrieval backtest</a>'
           f'<a class="soon">+ next db (soon)</a>'
           f'<a href="{README_URL}">README</a>'
           f'<span class="gen">generated {datetime.now().strftime("%Y-%m-%d %H:%M")}</span></nav>')
    half = res["candidates"][:20]
    mx = half[0][1] if half else 1
    col1 = "".join(_cand_row(*r, mx) for r in half[:10])
    col2 = "".join(_cand_row(*r, mx) for r in half[10:20])
    def _recall_badge(g):
        h, t = res.get("gt_recall", {}).get(g, [0, 0])
        pct = f"{100*h//t}%" if t else "—"
        col = "#2f9e44" if t and h / t >= 0.4 else "#e8590c" if t and h / t >= 0.15 else "#e03131"
        return (f'<span style="float:right;font-size:12px;color:{col};font-weight:600">'
                f'target recall {h}/{t} ({pct})</span>')
    charts_html = "".join(
        f'<div class="pcwrap"><div class="pchead"><span class="tk">{g}</span>'
        f'<span class="bd" style="background:{FORM_COLOR[res["detection"][g]["form"]]}">'
        f'{DISPLAY_FORM[res["detection"][g]["form"]]}</span>{_recall_badge(g)}{CAPTION[g]}</div>'
        f'{_price_chart(g, res["charts"][g], res["detection"][g])}</div>'
        for g in LANE_ORDER)
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Retrieval backtest</title>
<script src="{PLOTLY_CDN}"></script>
<style>{css}</style></head><body>
{nav}
<h1>Retrieval backtest — can the firehose catch the gems early?</h1>
<div class="sub">Bi-weekly Tavily two-pass sweep, {res['span'][0]} → {res['span'][1]} (forward day-1) ·
generic aligned beats, <b>no ticker queries</b> · {res['generated_from']}</div>

<div class="cost">
  <div><b>{c['pool_size']:,}</b><span>articles retrieved</span></div>
  <div><b>{c['windows']}</b><span>bi-weekly windows</span></div>
  <div><b>{c['tavily_credits']:,}</b><span>Tavily credits</span></div>
  <div><b>${c.get('tavily_usd', 0):,.2f}</b><span>Tavily cost (@ $0.008/credit)</span></div>
  <div><b>${c['llm_usd']:.2f}</b><span>LLM cost (no LLM)</span></div>
  <div><b>{c['wall_seconds']:.0f}s</b><span>wall time</span></div>
</div>

<div class="finding">
<b>Finding — retrievability splits on the gem's <i>form</i>.</b> A single-stock gem (<b>MP</b>) is named
by ticker early and often (<b>~7½ months before its price peak</b>) — cleanly retrievable off the generic beats. The
ETF-wrapper gems (<b>DRAM, BWET, GDX</b>) are named by ticker <i>late or never</i>, yet their underlying
<b>thesis and constituent stocks show up early</b> — to buy the ETF early you'd have to infer the vehicle
from the thesis. The foreign ADR (<b>RNMBY</b>) is named only in hindsight (and is the semi-synthetic
instrument; US-centric beats under-cover European names). So the firehose reliably catches early
<i>single-stock</i> gems by name; ETF wrappers need vehicle inference.
</div>

<h2>Plot 1 · Price &amp; retrieval timing</h2>
<div class="sub">Ticker (solid) vs <b>SPY</b> (dashed), both <b>normalized to 1× at the window start</b> so the growth
multiple &amp; out-performance are visible. Each <b style="color:#1c7ed6">●</b> is an article that <b>names the
ticker</b> (by-name), placed at its date &amp; price. <b>Hover</b> for date · price · lede; <b>click</b> to open
the article.<br><b>Squares = the "target set"</b> — an article that BOTH names the ticker AND carries a
superlative (the news we want caught). <b>Every target square was MISSED by the Tavily backtest</b>; the color
says what the live forward (Anthropic) engine can do about that miss: <b style="color:#2f9e44">🟩 green =
forward-<i>reachable</i></b> (Anthropic can reach it — e.g. Cloudflare-walled etf.com; "reachable" ≠ "would
surface it") · <b style="color:#f76707">🟧 orange = missed by BOTH</b> engines (Anthropic-blocked like
WSJ/MarketWatch/Investors.com, or unindexed). A target the Tavily backtest <i>did</i> catch is a
<b style="color:#1c7ed6">big blue ●</b>. Small blue dots = other by-name articles (not in the target set).</div>
{charts_html}

<h2>Detection summary</h2>
<table><tr><th>gem</th><th>form</th><th>by-name</th><th>thesis</th><th>earliest by-name</th>
<th>earliest thesis</th><th>peak</th><th>lead vs peak</th></tr>
{table}
</table>
<div class="sub" style="margin-top:6px"><b>by-name</b> = articles that name the ticker (title, or a $TK/(TK)
tag anywhere — a bare company name in the scraped snippet alone is rejected as page-chrome). <b>thesis</b> =
a keyword count of theme-only articles (e.g. "rare earth" without naming MP); shown for context only — it's a
grep, <i>not</i> the scout's judgment, and it's not plotted. <b>peak</b> = actual price maximum.</div>

<h2>Plot 2 · Candidate tickers named by the retriever</h2>
<div class="sub"><b>How they landed on this shortlist:</b> the tickers the generic beats surfaced most often,
counted by explicit <b>$TICKER / (EXCH:TICKER)</b> mentions across the retrieved pool — the retriever named
them unprompted (no ticker queries). The <b style="color:#e8590c">▲N</b> = how many of those mentions sit in a
<b>superlative</b> article (skyrocketing / soaring / record-high / best-performing) — the gem-buzz signal.
High mention count <i>and</i> high ▲ = a candidate worth promoting (e.g. AREC, now a gem in Plot 1).</div>
<div class="cols"><div>{col1}</div><div>{col2}</div></div>

<h2>Plot 3 · News-count histogram (per day)</h2>
<div class="sub">Retrieved articles per publication day. Each bi-weekly window caps at 80, so this is the
<i>retrieved &amp; kept</i> pool, not raw availability.</div>
<div id="newshist" style="width:100%;height:300px"></div>

<h2>Plot 4 · Weekly article counts</h2>
<div class="sub">Retrieved articles per calendar week (Monday-anchored).</div>
<div id="weekhist" style="width:100%;height:280px"></div>

<h2>Plot 5 · Monthly article counts</h2>
<div class="sub">Retrieved articles per calendar month — coarse temporal coverage.</div>
<div id="monthhist" style="width:100%;height:280px"></div>

<h2>Plot 6 · Quarterly article counts</h2>
<div class="sub">Retrieved articles per calendar quarter.</div>
<div id="quarterhist" style="width:100%;height:260px"></div>

<h2>Plot 7 · Articles by day of week</h2>
<div class="sub">Publication weekday of the retrieved pool.</div>
<div id="dowhist" style="width:100%;height:260px"></div>

<h2>Plot 8 · Articles per search term (total vs unique)</h2>
<div class="sub">Distinct pool articles each aligned beat surfaced (11 gem + 21 coverage beats). <b>total</b> vs
<b>unique</b> (surfaced by that beat ONLY) — a low unique share = a redundant beat. Hover for the beat.</div>
<div id="beathist" style="width:100%;height:760px"></div>

<h2>Plot 9 · By-name yield per search term</h2>
<div class="sub">How many <b>gem-ticker-naming</b> articles each beat surfaced — which beats are productive for
the actual goal, not just volume.</div>
<div id="bynamehist" style="width:100%;height:760px"></div>

<h2>Plot 10 · Articles per source domain</h2>
<div class="sub">Top publishers, colored by list: <b style="color:#2f9e44">allowlist</b> (specialty desks),
<b style="color:#adb5bd">other</b>, <b style="color:#e03131">blocklist</b> (should be absent).</div>
<div id="domhist" style="width:100%;height:520px"></div>

<h2>Plot 11 · Two-pass split — allowlist vs blocklist source filter</h2>
<div class="sub">The pool is gathered in two passes that differ by their <b>source filter</b>: the
<b>allowlist pass</b> runs the gem-hunting beats restricted to the <code>specialty_allow</code> desks
(narrow — specialty sources only); the <b>blocklist pass</b> runs the broad sector beats over <i>all</i>
sources <i>except</i> the <code>mill_block</code> mills (broad query + mill filter). Bars = articles surfaced
by only one pass vs by both.</div>
<div id="passhist" style="width:100%;height:280px"></div>

<div class="note"><b>What this is / isn't.</b> This is a <b>backtest of known winners on look-ahead-leaky
web search</b> (Tavily re-surfaces past weeks with hindsight; CLAUDE.md #4/#6). A positive means the ticker
was <b>retrievable early by name</b> — NOT that the live firehose would pick it out of ~80 articles/window
of noise (that's the next rung: run the scout→matcher→event-agent curator over these pools). It is an
<b>upper bound</b>; the forward paper trade is the verdict. Backtest engine = Tavily (reaches history);
live forward engine = Anthropic web_search (recent weeks) — same aligned beats, so this proxies forward
retrieval (conservatively: Anthropic also spawns adaptive follow-ups this fixed sweep doesn't).</div>
<div id="tt"></div>
<script>
const tt=document.getElementById('tt');
document.querySelectorAll('.dot').forEach(c=>{{
 c.addEventListener('mousemove',e=>{{
  const d=c.dataset;
  tt.innerHTML='<b>'+d.date+'</b> &middot; '+d.src+' &middot; '+d.price+' &middot; '+d.kind
    +'<br><b>'+d.title+'</b><div class="lede">'+d.lede+'</div>';
  tt.style.display='block';
  let L=e.clientX+15; if(L>window.innerWidth-370)L=e.clientX-375;
  tt.style.left=L+'px'; tt.style.top=(e.clientY+15)+'px';
 }});
 c.addEventListener('mouseleave',()=>tt.style.display='none');
 c.addEventListener('click',()=>{{if(c.dataset.url)window.open(c.dataset.url,'_blank');}});
}});
</script>
<script>{_diag(res)}</script>
</body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    return OUT


def _cand_row(sym: str, n: int, sup: int, mx: int) -> str:
    return (f'<div class="cand"><span class="sym">{sym}</span>'
            f'<span class="bar" style="width:{n/mx*100:.0f}%"></span><span class="n">{n}</span>'
            f'<span style="color:#e8590c;font-size:11px" title="mentions inside a superlative article">'
            f'▲{sup}</span></div>')


if __name__ == "__main__":
    p = build()
    print(f"wrote {p}")
