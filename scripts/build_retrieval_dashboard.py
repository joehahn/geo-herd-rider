"""build_retrieval_dashboard.py — render the bi-weekly Tavily retrieval backtest as a standalone HTML.

Reads data/retrieval_backtest.json (from scripts/retrieval_backtest.py) and writes a self-contained page
(inline SVG, no deps) spanning START..END (forward day-1), showing: the single-stock-vs-ETF-wrapper
retrievability finding, a per-gem detection timeline (by-name vs thesis vs peak), the detection table, the
un-planted candidate gems the sweep surfaced, and the run's cost block. Output -> docs_preview/ (gitignored
local preview). The retrieval backtest is an UPPER BOUND (look-ahead-leaky web search, known winners) — the
forward paper trade is the verdict; the page says so.
"""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "data" / "retrieval_backtest.json"
OUT = REPO / "docs_preview" / "retrieval_backtest.html"

FORM_COLOR = {"single stock": "#2f9e44", "ETF wrapper": "#f08c00", "foreign ADR": "#868e96"}
LANE_ORDER = ["MP", "DRAM", "BWET", "GDX", "RNMBY"]     # single stock, then ETF wrappers, then ADR


def _x(d: str, lo: date, span: int, x0: float, w: float) -> float:
    return x0 + (date.fromisoformat(d) - lo).days / span * w


def _timeline(res: dict) -> str:
    lo, hi = date.fromisoformat(res["span"][0]), date.fromisoformat(res["span"][1])
    span = (hi - lo).days
    W, x0, plot_w, lane_h = 1120, 96, 1000, 46
    lanes = [g for g in LANE_ORDER if g in res["detection"]]
    H = 44 + len(lanes) * lane_h + 34
    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px">']
    # month/quarter gridlines + labels
    y_top, y_bot = 30, 30 + len(lanes) * lane_h
    yq, mq = lo.year, ((lo.month - 1) // 3) * 3 + 1
    while date(yq, mq, 1) <= hi:
        gx = _x(date(yq, mq, 1).isoformat(), lo, span, x0, plot_w)
        s.append(f'<line x1="{gx:.0f}" y1="{y_top}" x2="{gx:.0f}" y2="{y_bot}" stroke="#e9ecef"/>')
        s.append(f'<text x="{gx:.0f}" y="{y_bot+16}" font-size="11" fill="#868e96" text-anchor="middle">'
                 f'{yq}-{mq:02d}</text>')
        mq += 3
        if mq > 12:
            mq -= 12; yq += 1
    for i, g in enumerate(lanes):
        d = res["detection"][g]
        cy = y_top + i * lane_h + lane_h / 2
        s.append(f'<line x1="{x0}" y1="{cy:.0f}" x2="{x0+plot_w}" y2="{cy:.0f}" stroke="#f1f3f5"/>')
        col = FORM_COLOR[d["form"]]
        s.append(f'<text x="{x0-10}" y="{cy-3:.0f}" font-size="13" font-weight="600" fill="#212529" '
                 f'text-anchor="end">{g}</text>')
        s.append(f'<text x="{x0-10}" y="{cy+11:.0f}" font-size="9" fill="{col}" text-anchor="end">'
                 f'{d["form"]}</text>')
        # thesis (hollow grey) then by-name (filled colored) so by-name sits on top
        for dt in res["hits"][g]["thesis_dates"]:
            s.append(f'<circle cx="{_x(dt,lo,span,x0,plot_w):.1f}" cy="{cy:.0f}" r="3.2" fill="none" '
                     f'stroke="#adb5bd" stroke-width="1"/>')
        for dt in res["hits"][g]["by_name_dates"]:
            s.append(f'<circle cx="{_x(dt,lo,span,x0,plot_w):.1f}" cy="{cy:.0f}" r="4.2" fill="{col}"/>')
        # peak marker
        px = _x(d["peak"], lo, span, x0, plot_w)
        s.append(f'<line x1="{px:.0f}" y1="{cy-15:.0f}" x2="{px:.0f}" y2="{cy+15:.0f}" stroke="#e03131" '
                 f'stroke-width="1.4" stroke-dasharray="3,2"/>')
        s.append(f'<text x="{px+4:.0f}" y="{cy-8:.0f}" font-size="9" fill="#e03131">peak</text>')
        # lead annotation
        if d["lead_days"] is not None:
            ld = d["lead_days"]
            lc = "#2f9e44" if ld > 7 else "#e8590c" if ld >= 0 else "#e03131"
            txt = f"+{ld}d early" if ld > 0 else (f"{ld}d (post-peak)" if ld < 0 else "at peak")
            ex = _x(d["earliest_by_name"], lo, span, x0, plot_w)
            s.append(f'<text x="{ex:.0f}" y="{cy-9:.0f}" font-size="10" fill="{lc}" '
                     f'text-anchor="middle" font-weight="600">{txt}</text>')
        else:
            s.append(f'<text x="{x0+plot_w:.0f}" y="{cy-9:.0f}" font-size="10" fill="#e03131" '
                     f'text-anchor="end">never named by ticker</text>')
    s.append('</svg>')
    return "".join(s)


def _volume(res: dict) -> str:
    wc = res["wcount"]
    ks = sorted(wc)
    mx = max(wc.values()) or 1
    W, bw, gap, h = len(ks) * 8, 6, 2, 40
    s = [f'<svg viewBox="0 0 {max(W,300)} {h+16}" width="100%" style="max-width:{max(W,300)}px">']
    for i, k in enumerate(ks):
        bh = wc[k] / mx * h
        c = "#4dabf7" if wc[k] else "#ffa8a8"
        s.append(f'<rect x="{i*(bw+gap)}" y="{h-bh:.0f}" width="{bw}" height="{bh:.0f}" fill="{c}"/>')
    s.append(f'<text x="0" y="{h+13}" font-size="10" fill="#868e96">{ks[0]}</text>')
    s.append(f'<text x="{W}" y="{h+13}" font-size="10" fill="#868e96" text-anchor="end">{ks[-1]}</text>')
    s.append('</svg>')
    return "".join(s)


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
    """
    dot = ('<span class="legend"><b>●</b> filled = named by ticker &nbsp; '
           '<span style="color:#adb5bd">○</span> hollow = thesis only (catalyst named, vehicle not) &nbsp; '
           '<span style="color:#e03131">┆</span> peak</span>')
    half = res["candidates"][:20]
    col1 = "".join(_cand_row(s, n, half[0][1]) for s, n in half[:10])
    col2 = "".join(_cand_row(s, n, half[0][1]) for s, n in half[10:20])
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Retrieval backtest</title>
<style>{css}</style></head><body>
<h1>Retrieval backtest — can the firehose catch the gems early?</h1>
<div class="sub">Bi-weekly Tavily two-pass sweep, {res['span'][0]} → {res['span'][1]} (forward day-1) ·
generic aligned beats, <b>no ticker queries</b> · {res['generated_from']}</div>

<div class="cost">
  <div><b>{c['pool_size']:,}</b><span>articles retrieved</span></div>
  <div><b>{c['windows']}</b><span>bi-weekly windows</span></div>
  <div><b>{c['tavily_credits']:,}</b><span>Tavily credits</span></div>
  <div><b>${c['llm_usd']:.2f}</b><span>LLM cost (no LLM)</span></div>
  <div><b>{c['wall_seconds']:.0f}s</b><span>wall time</span></div>
</div>

<div class="finding">
<b>Finding — retrievability splits on the gem's <i>form</i>.</b> A single-stock gem (<b>MP</b>) is named
by ticker early and often (<b>+132d</b> before peak) — cleanly retrievable off the generic beats. The
ETF-wrapper gems (<b>DRAM, BWET, GDX</b>) are named by ticker <i>late or never</i>, yet their underlying
<b>thesis and constituent stocks show up early</b> — to buy the ETF early you'd have to infer the vehicle
from the thesis. The foreign ADR (<b>RNMBY</b>) is named only in hindsight (and is the semi-synthetic
instrument; US-centric beats under-cover European names). So the firehose reliably catches early
<i>single-stock</i> gems by name; ETF wrappers need vehicle inference.
</div>

<h2>Detection timeline</h2>
{dot}
{_timeline(res)}

<h2>Detection summary</h2>
<table><tr><th>gem</th><th>form</th><th>by-name</th><th>thesis</th><th>earliest by-name</th>
<th>earliest thesis</th><th>peak</th><th>lead vs peak</th></tr>
{table}
</table>

<h2>Candidate gems the sweep surfaced (un-planted)</h2>
<div class="sub">Tickers the generic beats found on their own — your "there are probably others" hunch.
Gold-miner cluster + memory/AI-chip names dominate, matching the live theses.</div>
<div class="cols"><div>{col1}</div><div>{col2}</div></div>

<h2>Retrieval volume per window</h2>
<div class="sub">~80 articles/window, 0 blackouts (after the rate-limit fix). Confirms even coverage across the span.</div>
{_volume(res)}

<div class="note"><b>What this is / isn't.</b> This is a <b>backtest of known winners on look-ahead-leaky
web search</b> (Tavily re-surfaces past weeks with hindsight; CLAUDE.md #4/#6). A positive means the ticker
was <b>retrievable early by name</b> — NOT that the live firehose would pick it out of ~80 articles/window
of noise (that's the next rung: run the scout→matcher→event-agent curator over these pools). It is an
<b>upper bound</b>; the forward paper trade is the verdict. Backtest engine = Tavily (reaches history);
live forward engine = Anthropic web_search (recent weeks) — same aligned beats, so this proxies forward
retrieval (conservatively: Anthropic also spawns adaptive follow-ups this fixed sweep doesn't).</div>
</body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    return OUT


def _cand_row(sym: str, n: int, mx: int) -> str:
    return (f'<div class="cand"><span class="sym">{sym}</span>'
            f'<span class="bar" style="width:{n/mx*100:.0f}%"></span><span class="n">{n}</span></div>')


if __name__ == "__main__":
    p = build()
    print(f"wrote {p}")
