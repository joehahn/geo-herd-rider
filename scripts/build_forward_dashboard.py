#!/usr/bin/env python3
"""build_forward_dashboard.py — the RICH forward dashboard.

Renders the forward paper-trade book (scan-log + journal + per-week archive) into the SAME dashboard
the backtest gems use (docs/bwet/ styling: PWR palette, timestamp bar, Plots 1-12, retrieval + cost
panels). It does this by REUSING `build_dashboard`'s two self-contained templates verbatim
(`INDEX_HTML`, `FIREHOSE_HTML`, `_write_page`, `PALETTE`, `metrics`) and building the same-shaped
`payload` dict from forward data — so all CSS/colors/plots are inherited, not re-implemented.

There is no single "gem"/overlay here: the top plot is the RECOMMENDED-PORTFOLIO equity curve vs SPY
(`firehose.backtest` on the accumulated scan log, which always carries the two always-on floors), and
the agent plots resolve each catalyst (thesis) into one event-agent with its basket of vehicle tickers.

    python scripts/build_forward_dashboard.py --sandbox data/forward_tavily --as-of 2026-07-03
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402
import firehose  # noqa: E402
import forward  # noqa: E402
import retstats  # noqa: E402
import score  # noqa: E402
import build_dashboard  # noqa: E402  (import-safe: real work guarded by if __name__=="__main__")
from optimizer import load_financial_model  # noqa: E402

OUT = ROOT / "docs_preview" / "forward"


def _enriched_scans(log: pd.DataFrame) -> dict:
    """firehose's {anchor_ts: [picks]} from the flat forward scan log, carrying the fields the agent
    blocks need: ticker / thesis / thesis_live / conviction / evidence_urls (forward._scans_dict drops
    conviction+urls, so we rebuild it here)."""
    out: dict = {}
    for wk, grp in log.groupby("week"):
        anchor = pd.Timestamp(str(wk) + " 16:30", tz="America/New_York")
        picks = []
        for _, r in grp.iterrows():
            tk = str(r.get("ticker", "")).strip()
            if not tk:
                continue
            conv = r.get("conviction")
            urls = [u for u in str(r.get("evidence_urls", "") or "").split(";") if u.strip()]
            picks.append({"ticker": tk.upper(), "thesis": str(r.get("thesis", "") or ""),
                          "thesis_live": str(r.get("thesis_live")) in ("True", "true", "1", "1.0"),
                          "conviction": (None if pd.isna(conv) else int(float(conv))),
                          "evidence_urls": urls})
        out[anchor] = picks
    return dict(sorted(out.items()))


def _forward_cost(sb_weeks: list[str]) -> float:
    """Forward spend over the window: LLM-ledger rows whose label is `gather-<week>` (stage
    forward-gather + any scout/agent) for a week in the window, last cost per label (matches
    build_dashboard.book_cost's last-per-label convention for re-runs)."""
    import costs  # noqa: PLC0415
    if not costs.LEDGER.exists() or not sb_weeks:
        return 0.0
    led = pd.read_csv(costs.LEDGER)
    led["wk"] = led["label"].astype(str).map(
        lambda s: (m.group(0) if (m := re.search(r"\d{4}-\d{2}-\d{2}", s)) else None))
    led = led[led["label"].astype(str).str.startswith("gather-") & led["wk"].isin(set(sb_weeks))]
    return round(float(led.groupby("label")["cost_usd"].last().sum()) if len(led) else 0.0, 2)


def _journal_arcs(journal: dict) -> dict:
    """arcs keyed by TICKER (Plot 12): attach every event's entries to each of its vehicle tickers,
    mapping the journal fields onto the arc shape build_gem produces."""
    arcs: dict = {}
    for eid, ev in journal.get("events", {}).items():
        cat = ev.get("catalyst", "")
        for e in ev.get("entries", []):
            row = {"date": str(e.get("date", ""))[:10], "live": bool(e.get("thesis_live")),
                   "conviction": e.get("conviction"), "thesis": cat, "src": "tavily",
                   "exit_case": e.get("exit_case", ""), "resolved": bool(e.get("catalyst_resolved")),
                   "assessment": e.get("assessment", ""), "exit_advice": e.get("exit_advice", ""),
                   "milestones": e.get("milestones", []) or []}
            for tk in ev.get("vehicles", []):
                arcs.setdefault(str(tk).strip().upper(), []).append(row)
    for tk in arcs:
        arcs[tk].sort(key=lambda r: r["date"])
    return arcs


def _storyline(journal: dict, week: str) -> str:
    """HTML summary: name each live event, its catalyst + basket, whether it's NEW this week (first
    entry == the as-of week) or CONTINUING (since its first entry date), and the latest weekly read."""
    live = []
    for eid, ev in journal.get("events", {}).items():
        ents = ev.get("entries", [])
        if not ents:
            continue
        last = ents[-1]
        if not last.get("thesis_live") or last.get("catalyst_resolved"):
            continue                                   # only events still live as of this week
        first_date = ents[0].get("date", "")[:10]
        new = (first_date == week)
        live.append((eid, ev, first_date, new, last))
    if not live:
        return ("<b>Forward paper-trade book</b> — no live events as of "
                f"{week}; the portfolio is the two always-on floors (SPY + defensive).")
    live.sort(key=lambda x: x[2])
    items = []
    for eid, ev, first_date, new, last in live:
        basket = ", ".join(ev.get("vehicles", []))
        tag = ('<b style="color:#1e7d34">NEW this week</b>' if new
               else f'<span class="sub">continuing since {first_date}</span>')
        assess = (last.get("assessment") or "").strip()          # full weekly read
        ec = (last.get("exit_case") or "").strip()
        ea = (last.get("exit_advice") or "").strip()
        exit_html = ("<br><b>Exit:</b> " + ea + (f' <span class="sub">(exit trigger: {ec})</span>' if ec else "")) \
            if (ea or ec) else ""
        items.append(
            f'<li style="margin:0 0 10px"><b>{eid}</b> · <b>{basket}</b>, conv {last.get("conviction","—")} — {tag}.'
            f'<br><b>Catalyst:</b> <i>{ev.get("catalyst","")}</i>'
            + (f'<br>{assess}' if assess else '')
            + exit_html + '</li>')
    return (f"<b>Forward paper-trade book</b> — {len(live)} live event(s) as of {week} "
            "(each catalyst = one event-agent; the optimizer sizes the baskets, SPY + defensive "
            f"floors always on):<ul style='margin:6px 0 0;padding-left:20px'>{''.join(items)}</ul>")


def _write_landing(out: Path, latest: str, final: float, cap: float, spy_final: float) -> None:
    """Landing page (out/index.html): link every preserved dated snapshot (newest first) + headline."""
    weeks = sorted((f.stem for f in out.glob("*.html") if f.stem != "index"), reverse=True)
    items = "\n".join(f'<li><a href="{w}.html">week ending {w}</a></li>' for w in weeks)
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Forward paper-trade</title>
<style>body{{font:14px/1.6 -apple-system,system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem}}
h1{{font-size:1.4rem}} .kpi{{font-size:1.15rem;background:#f4faff;border:1px solid #cfe3f5;border-radius:6px;padding:10px 14px}}
a{{color:#c0392b}}</style></head><body>
<h1>Forward paper-trade — weekly dashboards</h1>
<p class="kpi">Latest ({latest}): <b>${final:,.0f}</b> from ${cap:,.0f} ({final/cap-1:+.1%}) &middot; SPY {spy_final/cap-1:+.1%}</p>
<p>Every weekly snapshot is preserved (newest first):</p>
<ul>{items}</ul>
<p style="color:#888;font-size:12px">Paper trade; the recommended portfolio auto-follows the optimizer.</p>
</body></html>"""
    build_dashboard._write_page(out / "index.html", html)


def _join_series(sandbox: str, weeks: list) -> list:
    """Per-week GDELT-Wayback join rate (% of GDELT headlines that recovered an as-of lede), computed from the
    enriched archives (snippet present & != title). Empty for non-GDELT sandboxes -> plot is skipped."""
    out = []
    for w in weeks:
        f = Path(sandbox) / "archive" / f"{w}.json"
        if not f.exists():
            continue
        gg = [a for a in json.loads(f.read_text()).get("pool", []) if a.get("engine") == "gdelt"]
        if not gg:
            continue
        lede = sum(1 for a in gg if (a.get("snippet") or "").strip()
                   and (a.get("snippet") or "").strip() != (a.get("title") or "").strip())
        out.append({"week": w, "rate": round(100 * lede / len(gg), 1)})
    return out


def build(sandbox: str, out_dir: str, as_of: str | None, overrides: list | None = None) -> dict:
    sb = Path(sandbox)
    log = pd.read_csv(sb / "firehose_scans.csv")
    if as_of:
        log = log[log["week"].astype(str) <= as_of].reset_index(drop=True)
    weeks = sorted(log["week"].astype(str).unique())
    if not weeks:
        sys.exit("no weeks in scan log (after --as-of filter)")
    week = weeks[-1]

    fm = load_financial_model(str(ROOT / "investor_profile.forward.md"))
    for kv in (overrides or []):        # deterministic config sweep (no LLM cost) — re-size + re-render only
        k, v = kv.split("=", 1)
        try:
            fm[k.strip()] = int(v)
        except ValueError:
            fm[k.strip()] = float(v)
    capital = float(fm.get("initial_investment_usd", 50_000))

    # curator model that produced the run: the latest archived config wins over the profile knob
    arch_f = sb / "archive" / f"{week}.json"
    arch = json.loads(arch_f.read_text()) if arch_f.exists() else {}
    disp_model = (arch.get("config", {}) or {}).get("model") or arch.get("model") or fm.get("model", "?")

    scans = _enriched_scans(log)
    # overlay=SPY (a real, already-fetched ticker) so backtest never fetches an empty ticker; we null
    # the overlay in the payload afterwards (forward book has no single gem to overlay).
    bt = firehose.backtest(scans, fm, capital, daily=True,
                           overlay=score.BENCHMARK, overlay_anchor=weeks[0])
    d = bt["daily"]
    if d is None:
        sys.exit("no daily series — need >=1 week with prices.")

    # ---- firehose gems (child page) + watchlist/funding ----
    gems = []
    for a, picks in scans.items():
        for p in picks:
            if str(p.get("ticker", "")).strip():
                gems.append({"week": a.date().isoformat(), "ticker": p["ticker"],
                             "thesis": p.get("thesis", ""), "thesis_live": bool(p.get("thesis_live", True)),
                             "urls": [u for u in (p.get("evidence_urls", []) or []) if u]})
    watch = bt.get("watch") or firehose._stateful_watch(scans)
    funded_by_week = {lg["week"]: [s.split(":")[0] for s in lg["weights"].split(";") if s] for lg in bt["log"]}
    ever_funded = sorted({t for names in funded_by_week.values() for t in names})
    watchlist = [{"week": a.date().isoformat(), "names": watch[a],
                  "funded": funded_by_week.get(a.date().isoformat(), [])} for a in scans]

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

    # ============================================================================================
    # VERBATIM from build_dashboard.build_gem — pure Python on `scans` + `d` (bt["daily"]). Feeds
    # Plots 2/7/8/9: agent_meta (agents), agent_of, agent_marks, agent_gain, agent_conviction,
    # agent_convgain. Do not edit; it must stay in lockstep with the gem dashboard.
    # ============================================================================================
    thesis_id, agent_meta, agent_tks = {}, {}, {}
    for a in sorted(scans):
        for p in scans[a]:
            th, tk = p.get("thesis", ""), str(p.get("ticker", "")).strip().upper()
            if not th or not tk:
                continue
            aid = thesis_id.get(th)
            if aid is None:
                aid = thesis_id[th] = f"ev{len(thesis_id) + 1}"
                agent_meta[aid] = {"ticker": tk, "thesis": th,
                                   "first": a.date().isoformat(), "last": a.date().isoformat()}
            else:
                agent_meta[aid]["last"] = a.date().isoformat()
            agent_tks.setdefault(aid, set()).add(tk)
    agent_of: dict = {}
    for aid, tks in agent_tks.items():
        for tk in tks:
            agent_of.setdefault(tk, []).append(aid)
    agent_of = {tk: "+".join(sorted(set(ids))) for tk, ids in agent_of.items()}

    ag_dates, ag_gs = d["dates"], d.get("gain_series", {})

    def _idx_le(ds):
        j = -1
        for i, dd in enumerate(ag_dates):
            if dd <= ds:
                j = i
            else:
                break
        return j
    tk_agents: dict = {}
    for aid, tks in agent_tks.items():
        for tk in tks:
            tk_agents.setdefault(tk, []).append(aid)
    agent_gain = {aid: 0.0 for aid in agent_meta}
    for tk, aids in tk_agents.items():
        gs = ag_gs.get(tk)
        aids = sorted(aids, key=lambda a: agent_meta[a]["first"])
        if not gs:
            continue
        for k, a in enumerate(aids):
            s = _idx_le(agent_meta[a]["first"]) - 1
            start_val = gs[s] if s >= 0 else 0.0
            if k + 1 < len(aids):
                e = _idx_le(agent_meta[aids[k + 1]]["first"]) - 1
                end_val = gs[e] if e >= 0 else 0.0
            else:
                end_val = gs[-1]
            agent_gain[a] += round(end_val - start_val, 2)
    for aid, tks in agent_tks.items():
        funded = sorted(t for t in tks if t in ag_gs)
        agent_meta[aid]["basket"] = ", ".join(funded or sorted(tks))

    agent_conviction: dict = {}
    for a in sorted(scans):
        ds = a.date().isoformat()
        for p in scans[a]:
            aid = thesis_id.get(p.get("thesis", ""))
            if aid is not None and p.get("conviction") is not None:
                agent_conviction.setdefault(aid, []).append(
                    {"date": ds, "conviction": int(p.get("conviction", 5) or 5)})
    spy_agent = int(fm.get("spy_agent_conviction", 0) or 0)
    if spy_agent and ag_gs.get("SPY"):
        _sgs = ag_gs["SPY"]
        agent_meta["spy"] = {"ticker": "SPY", "thesis": "always-on SPY floor agent",
                             "first": ag_dates[0], "last": ag_dates[-1]}
        agent_gain["spy"] = round(_sgs[-1] - _sgs[0], 2)
        agent_conviction["spy"] = [{"date": a.date().isoformat(), "conviction": spy_agent}
                                   for a in sorted(scans)]
    defensive_agent = int(fm.get("defensive_agent_conviction", 0) or 0)
    _defv_tk = str(fm.get("defensive_ticker", "GLD")).upper()
    if defensive_agent and ag_gs.get(_defv_tk):
        _dgs = ag_gs[_defv_tk]
        agent_meta["defensive"] = {"ticker": _defv_tk, "thesis": f"always-on defensive ({_defv_tk}) floor agent",
                                   "first": ag_dates[0], "last": ag_dates[-1]}
        agent_gain["defensive"] = round(_dgs[-1] - _dgs[0], 2)
        agent_conviction["defensive"] = [{"date": a.date().isoformat(), "conviction": defensive_agent}
                                         for a in sorted(scans)]

    # per-agent BASKET gain series (sum the agent's tickers' gain_series). Forward agents are baskets, so
    # the gem-dashboard single-representative-ticker gain understates them (e.g. ev1's COIN was never held).
    _N = len(ag_dates)
    agent_gs_series: dict = {}
    for aid, tks in agent_tks.items():
        ser = [0.0] * _N
        for t in tks:
            g = ag_gs.get(t)
            if g:
                for i in range(_N):
                    ser[i] += g[i]
        agent_gs_series[aid] = ser
    if "spy" in agent_meta:
        agent_gs_series["spy"] = ag_gs.get("SPY", [0.0] * _N)
    if "defensive" in agent_meta:
        agent_gs_series["defensive"] = ag_gs.get(_defv_tk, [0.0] * _N)

    agent_convgain: dict = {}
    for aid, cpts in agent_conviction.items():
        gs = agent_gs_series.get(aid)
        s = (_idx_le(agent_meta[aid]["first"]) - 1) if aid in agent_meta else -1
        base = gs[s] if (gs and s >= 0) else 0.0
        agent_convgain[aid] = [
            {"date": cp["date"], "conviction": cp["conviction"],
             "gain": round(gs[_idx_le(cp["date"])] - base, 2) if (gs and _idx_le(cp["date"]) >= 0) else 0.0}
            for cp in cpts]

    agent_marks, _prev_live = {}, {}
    for a in sorted(scans):
        ds = a.date().isoformat()
        for p in scans[a]:
            tk = str(p.get("ticker", "")).strip().upper()
            if not tk:
                continue
            lv = bool(p.get("thesis_live"))
            m = agent_marks.setdefault(tk, {"live": [], "exit": []})
            was = _prev_live.get(tk, False)
            if lv and not was:
                m["live"].append(ds)
            elif was and not lv:
                m["exit"].append(ds)
            _prev_live[tk] = lv
    # ============================ end verbatim block ============================================

    # ---- journal-derived arcs (Plot 12) + storyline ----
    journal = json.loads((sb / "journal.json").read_text())
    if as_of:
        for ev in journal.get("events", {}).values():
            ev["entries"] = [e for e in ev.get("entries", []) if str(e.get("date", ""))[:10] <= as_of]
        journal["events"] = {k: v for k, v in journal["events"].items() if v.get("entries")}
    arcs = _journal_arcs(journal)
    storyline = _storyline(journal, week)

    # ---- colors: SPY=orange, defensive=yellow, everything else from the PWR palette ----
    _ORANGE, _YEL = "#ff7f0e", "#eab308"
    tickers = sorted(d["alloc"].keys())
    _ngpal = [c for c in build_dashboard.PALETTE if c not in (_ORANGE, _YEL)]
    _others = [t for t in tickers if t not in ("SPY", _defv_tk)]
    colors = {t: _ngpal[i % len(_ngpal)] for i, t in enumerate(_others)}
    colors["SPY"] = _ORANGE
    colors[_defv_tk] = _YEL

    _wk_start = (pd.Timestamp(week) - pd.Timedelta(days=7)).date().isoformat()   # this week's start (anchor-7d)
    payload = {
        # forward book: no single gem / overlay
        "gem": "Forward book", "overlay_label": "", "caught": False,
        "overlays": [], "gem_label": "Forward book",
        "combo_targets": [], "caught_all": 0, "both_held": False,
        "model": disp_model, "storyline": storyline, "ever_funded": ever_funded,
        "book_title": "Weekly report",                          # weekly page (index build below overrides to "Weekly results")
        "subtitle": f"{_wk_start} → {week}",                     # THIS week's start→end (index build below overrides to the run summary)
        "scan_range": [weeks[0], weeks[-1]] if weeks else [],    # kept as the JS fallback
        "seeds": [], "capital": capital,
        "dates": d["dates"], "value": d["value"], "spy": d["spy"],
        "gain": d.get("gain", {}), "gain_series": d.get("gain_series", {}),
        # overlay=None (not []): the Plot 1 JS tests `D.overlay?`, which is TRUTHY for an empty
        # array — null makes the no-overlay fallback fire cleanly (portfolio + SPY only).
        "overlay": None, "overlay_ticker": "", "overlay_anchor": "",
        "alloc": d["alloc"], "cash": d["cash"], "colors": colors,
        "metrics": build_dashboard.metrics(d["value"], d["spy"], capital),
        "cost_usd": _forward_cost(weeks), "weeks": bt["weeks"], "gems": gems,
        "watchlist": watchlist, "watch_daily": watch_daily,
        "retrieval": retstats.load(str(Path(sandbox) / "retrieval_stats.json")),   # GDELT+Wayback health (if captured)
        "params": {**fm, "model": disp_model},
        "arcs": arcs, "lifecycle": [], "agents": agent_meta, "agent_of": agent_of,
        "agent_marks": agent_marks, "agent_gain": agent_gain,
        "agent_conviction": agent_conviction, "agent_convgain": agent_convgain,
        "agent_precision": [{**r, "agent": thesis_id.get(r.get("thesis", ""), "")}
                            for r in bt.get("agent_precision", [])],   # tag each bar with its ev-id
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pj = json.dumps(payload).replace("</", "<\\/")
    (out / f"{week}.json").write_text(json.dumps(payload, indent=2))     # sidecar (debug / regression)
    rebd = int(fm.get("rebalance_days", 7))
    prev = (pd.Timestamp(week) - pd.Timedelta(days=rebd)).date().isoformat()
    nxt = (pd.Timestamp(week) + pd.Timedelta(days=rebd)).date().isoformat()      # next always anticipates the coming week
    is_first = (week == weeks[0])
    prev_l = (f'<span style="color:#bbb">&larr; {prev}</span>' if is_first
              else f'<a href="{prev}.html">&larr; {prev}</a>')

    def _nav(active_dash: bool) -> str:
        wk_cls = ' class="active"' if active_dash else ''
        fr_cls = '' if active_dash else ' class="active"'
        return (f'<nav class="nav"><a href="index.html">&uarr; All weeks</a>'
                f'{prev_l}'
                f'<a href="{week}.html"{wk_cls}>{week}</a>'
                f'<a href="{nxt}.html">{nxt} &rarr;</a>'
                f'<a href="firehose.html"{fr_cls}>Firehose log</a>'
                f'<a href="https://github.com/joehahn/geo-herd-rider/blob/main/README.md">README</a></nav>')

    # news-count histogram (articles/day across the pulled pool, weeks <= as-of) — forward-only, injected
    from collections import Counter as _Counter
    _by: dict = {}                                      # engine -> Counter(date); split when 'engine' tag present
    _tagged = False
    for _f in sorted((sb / "archive").glob("*.json")):
        if as_of and _f.stem > as_of:
            continue
        for _a in json.loads(_f.read_text()).get("pool", []):
            _dd = (_a.get("published_date") or "")[:10]
            if not _dd:
                continue
            _eng = _a.get("engine")
            if _eng:
                _tagged = True
            _by.setdefault(_eng or "news", _Counter())[_dd] += 1
    _hx = sorted({d for c in _by.values() for d in c})
    _cols = {"tavily": "#4a90d9", "anthropic": "#e07b39", "gdelt": "#2ca02c", "news": "#4a90d9"}
    _order = ["tavily", "anthropic", "gdelt"] if _tagged else ["news"]
    _traces = [{"x": _hx, "y": [_by.get(e, _Counter())[d] for d in _hx], "type": "bar", "name": e,
                "marker": {"color": _cols.get(e, "#888")}} for e in _order if e in _by]
    _hist = json.dumps(_traces)
    _leg = 'legend:{orientation:"h"},' if _tagged else ''
    _engs = [e for e in _order if e in _by]                  # accurate Plot-8 subtitle from the engines actually present
    _histsub = ("GDELT headlines per publication day" if _engs == ["gdelt"]
                else "articles per publication day, by retrieval source")

    # day-of-week distribution (Plot 9): fold the per-day counts into Mon..Sun buckets
    _dowlab = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    _dow = [0] * 7
    for _c in _by.values():
        for _d, _n in _c.items():
            try:
                _dow[pd.Timestamp(_d).dayofweek] += _n
            except Exception:  # noqa: BLE001
                pass
    _dowj = json.dumps(_dow)

    # query-effectiveness: gross GDELT article hits per search term, summed across the whole run (read
    # from the --trace transcript; pre-dedup, so it exceeds the deduped pool size). 0 = a dud beat.
    _qcnt: dict = {}
    _tf = sb / "transcript.jsonl"
    if _tf.exists():
        for _ln in _tf.open(encoding="utf-8"):
            try:
                _r = json.loads(_ln)
            except Exception:  # noqa: BLE001  (skip partial/last line of a live-appended trace)
                continue
            if _r.get("kind") == "search" and _r.get("engine") == "gdelt":
                _q = _r.get("query", "")
                _qcnt[_q] = _qcnt.get(_q, 0) + int(_r.get("n_results", 0) or 0)
    _qc = sorted(_qcnt.items(), key=lambda kv: kv[1])   # ascending -> largest beat at TOP of horizontal bars
    _qy = json.dumps([q for q, _ in _qc])
    _qx = [n for _, n in _qc]
    _qxj, _qcolor = json.dumps(_qx), json.dumps(["#d62728" if n == 0 else "#2ca02c" for n in _qx])
    _qh = str(max(180, 18 * len(_qc) + 60))

    def _inject_hist(html: str, is_index: bool = False) -> str:
        # Injected retrieval plots: 8 News-count, 9 GDELT-by-weekday, 10 Articles-per-search-term (only
        # when a --trace exists so _qc is populated). Push the static Plots 8..11 up by that count.
        # Forward-dashboard-only (shared INDEX_HTML / gem dashboards keep 1..11). Renumber DESCENDING so
        # each source number is renamed before it can be re-created downstream.
        _shift = 3 if _qc else 2
        for _n in (11, 10, 9, 8):
            html = html.replace(f"Plot {_n}", f"Plot {_n + _shift}")
        html = html.replace("agent colors match Plots 7–9",
                            f"agent colors match Plots 7, {8 + _shift} &amp; {9 + _shift}")
        sec = ('<h2>Plot 8 &mdash; News-count histogram <span class="sub">(' + _histsub + ')</span></h2>'
               '<div id="newshist" style="width:100%;height:300px"></div>')
        sec += ('<h2>Plot 9 &mdash; GDELT count by day of week '
                '<span class="sub">(articles bucketed by weekday of publication)</span></h2>'
                '<div id="dowhist" style="width:100%;height:280px"></div>')
        if _qc:
            sec += ('<h2>Plot 10 &mdash; Articles per GDELT search term '
                    '<span class="sub">(gross hits/beat summed across all weeks &mdash; query effectiveness; '
                    'red = 0-hit dud beat)</span></h2>'
                    '<div id="queryhist" style="width:100%;height:' + _qh + 'px"></div>')
        _conv = f'<h2>Plot {8 + _shift} — Conviction score over time, per event-agent (+ SPY/gold floors)</h2>'
        html = html.replace(_conv, sec + _conv, 1)
        scr = ('<script>Plotly.newPlot("newshist",' + _hist +
               ',{margin:{t:10,r:10},yaxis:{title:"articles"},bargap:0.05,barmode:"stack",' + _leg +
               '},{displayModeBar:false,responsive:true});</script>')
        html = html.replace("</body>", scr + "</body>", 1)
        dscr = ('<script>Plotly.newPlot("dowhist",[{type:"bar",x:' + json.dumps(_dowlab) + ',y:' + _dowj +
                ',marker:{color:"#2ca02c"}}],{margin:{t:10,r:10,b:30,l:45},yaxis:{title:"articles"},'
                'bargap:0.15},{displayModeBar:false,responsive:true});</script>')
        html = html.replace("</body>", dscr + "</body>", 1)
        if _qc:
            qscr = ('<script>Plotly.newPlot("queryhist",[{type:"bar",orientation:"h",y:' + _qy +
                    ',x:' + _qxj + ',marker:{color:' + _qcolor + '},'
                    'hovertemplate:"%{y}<br>%{x} article hits<extra></extra>"}],'
                    '{margin:{l:210,r:20,t:10,b:34},xaxis:{title:"gross article hits"},'
                    'yaxis:{automargin:true,tickfont:{size:10}}},{displayModeBar:false,responsive:true});</script>')
            html = html.replace("</body>", qscr + "</body>", 1)
        _join = _join_series(sandbox, weeks)                # GDELT-Wayback join rate over time (retrieval-health)
        if _join:
            jx = [j["week"] for j in _join]
            jy = [j["rate"] for j in _join]
            jsec = ('<h3 style="font-size:1rem;margin:16px 0 4px">GDELT&ndash;Wayback join rate over time '
                    '<span class="sub">(% of GDELT headlines with an as-of lede; dotted = 60% healthy bar)</span></h3>'
                    '<div id="joinrate" style="width:100%;height:260px"></div>')
            html = html.replace('<div id="retr"></div>', '<div id="retr"></div>' + jsec, 1)
            jscr = ('<script>Plotly.newPlot("joinrate",[{x:' + json.dumps(jx) + ',y:' + json.dumps(jy) +
                    ',mode:"lines+markers",line:{color:"#2ca02c"},marker:{size:7}}],'
                    '{margin:{t:10,r:10,b:40,l:45},yaxis:{title:"join %",range:[0,100]},'
                    'shapes:[{type:"line",x0:0,x1:1,xref:"paper",y0:60,y1:60,line:{color:"#888",dash:"dot"}}]},'
                    '{displayModeBar:false,responsive:true});</script>')
            html = html.replace("</body>", jscr + "</body>", 1)
        return html

    _navrx = re.compile(r'<nav class="nav">.*?</nav>', re.S)
    dash = _inject_hist(_navrx.sub(lambda _: _nav(True), build_dashboard.INDEX_HTML.replace("{{DATA}}", pj), count=1))
    fire = _navrx.sub(lambda _: _nav(False), build_dashboard.FIREHOSE_HTML.replace("{{DATA}}", pj), count=1)
    build_dashboard._write_page(out / f"{week}.html", dash)
    build_dashboard._write_page(out / "firehose.html", fire)
    if as_of is None:                                     # latest full build -> "All weeks" landing = full dashboard
        wk_links = "".join(f'<a href="{w}.html">{w}</a>' for w in weeks)
        allnav = ('<nav class="nav"><a href="index.html" class="active">All weeks</a>' + wk_links
                  + '<a href="firehose.html">Firehose log</a>'
                  + '<a href="https://github.com/joehahn/geo-herd-rider/blob/main/README.md">README</a></nav>')
        _pidx = {**payload, "book_title": "Weekly results",     # summary page: whole-run title + range
                 "subtitle": f"{len(weeks)} weekly scans · {weeks[0]} → {weeks[-1]}"}
        pj_index = json.dumps(_pidx).replace("</", "<\\/")
        idx = _inject_hist(_navrx.sub(lambda _: allnav, build_dashboard.INDEX_HTML.replace("{{DATA}}", pj_index), count=1), is_index=True)
        snap = ('<h2>Weekly snapshots</h2>'
                '<p class="sub">Each week&rsquo;s preserved as-of dashboard (newest first):</p>'
                '<ul style="font-size:14px;columns:2;max-width:520px;margin:0 0 8px">'
                + "".join(f'<li><a href="{w}.html">{w}</a>{" &larr; latest" if w == weeks[-1] else ""}</li>'
                          for w in reversed(weeks))
                + '</ul>')
        idx = idx.replace('<div class="cards" id="cards"></div>',
                          '<div class="cards" id="cards"></div>' + snap, 1)
        build_dashboard._write_page(out / "index.html", idx)
    else:
        _write_landing(out, week, bt["final"], capital, bt["spy_final"])
    m = payload["metrics"]
    print(f"  forward {week}: ${capital:,.0f} -> ${m['final']:,.0f} ({m['total_ret']:+.1%}) "
          f"vs SPY {m['spy_ret']:+.1%}  ({len(weeks)} weeks, {len(agent_meta)} agents)  -> {out}/")
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sandbox", required=True,
                    help="forward sandbox dir (firehose_scans.csv, journal.json, archive/)")
    ap.add_argument("--out", default=str(OUT), help="output dir (default docs_preview/forward)")
    ap.add_argument("--as-of", default=None, dest="as_of",
                    help="build AS OF this week (scan weeks <= as-of AND journal entries <= as-of)")
    ap.add_argument("--set", action="append", default=[], dest="overrides", metavar="KEY=VAL",
                    help="override an fm knob without re-scanning (repeatable), e.g. --set risk_aversion=0.67")
    a = ap.parse_args(argv)
    build(a.sandbox, a.out, a.as_of, a.overrides)


if __name__ == "__main__":
    main()
