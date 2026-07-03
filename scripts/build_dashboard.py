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


def _write_page(path, html: str) -> None:
    """Write an HTML page with a 'generated <local timestamp>' bar at the TOP so a viewer can tell
    fresh from a stale GitHub-Pages deploy at a glance."""
    import datetime  # noqa: PLC0415
    ts = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    bar = ('<div style="font:12px ui-monospace,monospace;color:#666;text-align:right;'
           'padding:4px 12px;background:#eef3f6;border-bottom:1px solid #dbe3e8">'
           f'\U0001F552 generated {ts}</div>')
    if "<body>" in html:
        html = html.replace("<body>", "<body>" + bar, 1)
    else:
        html = html.replace("</body>", bar + "</body>", 1)  # fallback
    path.write_text(html)


def _gem_seeds(ticker: str) -> list:
    """Seed articles that NAME this gem (ticker as a word or '(TICKER)' in title/snippet), across all
    fixture seed files — for marking the seed's publish date + its lede on the value chart."""
    import glob  # noqa: PLC0415
    import re  # noqa: PLC0415
    tk = ticker.upper()
    pat = re.compile(rf"(\b{re.escape(tk)}\b|\({re.escape(tk)}\))")
    out, seen = [], set()
    for f in sorted(glob.glob(str(ROOT / "data" / "fixtures" / "*seed*.json"))):
        try:
            with open(f, encoding="utf-8") as fh:
                arts = json.load(fh).get("articles", [])
        except Exception:  # noqa: BLE001
            continue
        for a in arts:
            if pat.search(f"{a.get('title', '')} {a.get('snippet', '')}".upper()):
                key = (a.get("published_date", ""), a.get("title", ""))
                if key not in seen:
                    seen.add(key)
                    out.append({"date": a.get("published_date", ""), "title": a.get("title", ""),
                                "snippet": a.get("snippet", ""), "source": a.get("source", "")})
    return sorted(out, key=lambda s: s["date"])


def _overlay_curve(tk: str, anchor: str, dates: list, values: list) -> list | None:
    """A target gem's price scaled to the portfolio value at its anchor (same as the primary overlay
    in firehose._daily_series) — for adding extra gem curves to a combo card's Plot 1."""
    import pandas as pd  # noqa: PLC0415
    import score  # noqa: PLC0415
    try:
        s = score.fetch_panel([tk], dates[0], dates[-1], use_cache=False)[tk].dropna()
    except Exception:  # noqa: BLE001
        return None
    if not len(s):
        return None
    if getattr(s.index, "tz", None) is not None:
        s.index = s.index.tz_localize(None)
    idx = pd.DatetimeIndex([pd.Timestamp(x) for x in dates])
    ov = s.reindex(idx, method="ffill")
    at = pd.Timestamp(anchor)
    ai = next((i for i, x in enumerate(idx) if x >= at), None)
    if ai is None or pd.isna(ov.iloc[ai]) or ov.iloc[ai] <= 0:
        return None
    scale = values[ai] / float(ov.iloc[ai])
    return [None if pd.isna(v) else round(float(v) * scale, 2) for v in ov.tolist()]


def build_gem(ticker: str, capital_override: float | None = None, *, extra_overlays: list | None = None,
              scans_override: str | None = None, out_override: str | None = None,
              label_override: str | None = None) -> dict:
    """Build one gem's dashboard into docs/<gem>/ (data.json + index.html + firehose.html).
    The gem's own price is the overlay, anchored at its trigger date. Returns the payload.
    A COMBO card (e.g. GEO+MSTR from one concurrency book) passes scans_override + out_override +
    label_override + extra_overlays to overlay MULTIPLE target gems on the one shared portfolio."""
    import retstats
    cfg = gem_config(ticker)
    if scans_override:
        cfg = {**cfg, "scans": ROOT / "data" / "windows" / scans_override}
    if out_override:
        cfg = {**cfg, "out": OUT_DIR / out_override}
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
    watch = bt.get("watch") or firehose._stateful_watch(scans)   # pruned watch (matches the backtest)
    funded_by_week = {lg["week"]: [s.split(":")[0] for s in lg["weights"].split(";") if s] for lg in bt["log"]}
    ever_funded = sorted({t for names in funded_by_week.values() for t in names})  # got real capital >=1 week
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
    # per-event agent journal arc (week-by-week exit-case/read/exit), if the scan persisted it
    arcs = {}
    for a in sorted(scans):
        for p in scans[a]:
            t = str(p.get("ticker", "")).strip().upper()
            if t and (p.get("assessment") or p.get("exit_case")):
                arcs.setdefault(t, []).append({
                    "date": a.date().isoformat(), "live": p.get("thesis_live"),
                    "thesis": p.get("thesis", ""), "src": p.get("src", ""),
                    "exit_case": p.get("exit_case", ""), "resolved": p.get("catalyst_resolved", False),
                    "assessment": p.get("assessment", ""), "exit_advice": p.get("exit_advice", "")})

    # stable agent ids: each distinct event (catalyst/thesis) = one agent, numbered ev1, ev2... in
    # first-appearance order (matches the engine's event-creation numbering). A ticker maps to >1
    # agent when its thesis exits and the same ticker later re-emerges as a fresh event (e.g. BWET).
    thesis_id, agent_meta = {}, {}
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
    agent_of: dict = {}                       # ticker -> "ev1" (or "ev2+ev6" if it had >1 event)
    for th, aid in thesis_id.items():
        agent_of.setdefault(agent_meta[aid]["ticker"], []).append(aid)
    agent_of = {tk: "+".join(ids) for tk, ids in agent_of.items()}

    # per-agent $ attribution (Plot 7): partition each ticker's cumulative gain across its agents by
    # their active windows -> telescopes to the ticker total, and splits a shared ticker (BWET ev2/ev6).
    ag_dates, ag_gs = d["dates"], d.get("gain_series", {})

    def _idx_le(ds):
        j = -1
        for i, dd in enumerate(ag_dates):
            if dd <= ds:
                j = i
            else:
                break
        return j
    by_tk: dict = {}
    for aid, mt in agent_meta.items():
        by_tk.setdefault(mt["ticker"], []).append(aid)
    agent_gain = {}
    for tk, aids in by_tk.items():
        gs = ag_gs.get(tk)
        aids = sorted(aids, key=lambda a: agent_meta[a]["first"])
        if not gs:
            for a in aids:
                agent_gain[a] = 0.0
            continue
        for k, a in enumerate(aids):
            s = _idx_le(agent_meta[a]["first"]) - 1
            start_val = gs[s] if s >= 0 else 0.0
            if k + 1 < len(aids):                      # bounded by the next agent's start
                e = _idx_le(agent_meta[aids[k + 1]]["first"]) - 1
                end_val = gs[e] if e >= 0 else 0.0
            else:                                      # last agent keeps the tail
                end_val = gs[-1]
            agent_gain[a] = round(end_val - start_val, 2)

    # per-agent conviction over time (Plot 8) + the synthetic SPY agent-agent's row in the gain/
    # conviction plots: its $ P&L is booked on SPY holdings, its conviction is the constant spy_agent_conviction.
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
        agent_meta["spy"] = {"ticker": "SPY", "thesis": "always-on SPY agent",
                             "first": ag_dates[0], "last": ag_dates[-1]}
        agent_gain["spy"] = round(_sgs[-1] - _sgs[0], 2)
        agent_conviction["spy"] = [{"date": a.date().isoformat(), "conviction": spy_agent}
                                   for a in sorted(scans)]

    # per-agent (conviction, cumulative-gain) TIME-HISTORY for Plot 9: trace each agent as a connected
    # path through (conviction, $gain) space week by week (gain = its ticker's cumulative gain since the
    # agent's start), so a rising-right path = conviction and gain climbing together.
    agent_convgain: dict = {}
    for aid, cpts in agent_conviction.items():
        gs = ag_gs.get(agent_meta.get(aid, {}).get("ticker"))
        s = (_idx_le(agent_meta[aid]["first"]) - 1) if aid in agent_meta else -1
        base = gs[s] if (gs and s >= 0) else 0.0
        agent_convgain[aid] = [
            {"date": cp["date"], "conviction": cp["conviction"],
             "gain": round(gs[_idx_le(cp["date"])] - base, 2) if (gs and _idx_le(cp["date"]) >= 0) else 0.0}
            for cp in cpts]

    # Plot-2 markers: the weeks each ticker's agent went LIVE (entry) and EXITED (thesis_live -> False)
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

    # full-window lifecycle for THIS gem (the dashboard's subject): EVERY scanned week labeled
    # pre (scanned, not yet flagged) / live / exit / post (dropped) — so the agent's behavior
    # before, during, and after the event is visible, not just the live span.
    lifecycle = []
    seen = False
    for a in sorted(scans):
        gp = [p for p in scans[a] if str(p.get("ticker", "")).strip().upper() == ticker]
        if gp:
            seen = True
            p = gp[0]
            lifecycle.append({"date": a.date().isoformat(),
                              "state": "live" if p.get("thesis_live") else "exit",
                              "agent": thesis_id.get(p.get("thesis", ""), ""),
                              "src": p.get("src", ""), "exit_case": p.get("exit_case", ""),
                              "assessment": p.get("assessment", ""), "exit_advice": p.get("exit_advice", "")})
        else:
            lifecycle.append({"date": a.date().isoformat(), "state": "post" if seen else "pre",
                              "agent": "", "src": "", "exit_case": "", "assessment": "", "exit_advice": ""})

    # curator model that PRODUCED this book: the scan sidecar wins over the current profile knob
    meta_p = cfg["scans"].with_suffix(".meta.json")
    disp_model = fm.get("model", "mimo")
    if meta_p.exists():
        try:
            disp_model = json.loads(meta_p.read_text()).get("model", disp_model)
        except (ValueError, OSError):
            pass
    # combo-card overlays: primary gem + extra target gems, each scaled to the portfolio value at its
    # anchor; caught_all/both_held = concurrency metrics (all targets named / weeks all held together).
    overlays = ([{"ticker": ticker, "vals": d["overlay"], "anchor": d["overlay_anchor"], "color": PALETTE[0]}]
                if d.get("overlay") else [])
    _named = {str(p.get("ticker", "")).strip().upper() for wk in scans.values() for p in wk}
    for _i, _xt in enumerate(extra_overlays or []):
        _anch = gem_config(_xt)["trigger"]
        _vals = _overlay_curve(_xt, _anch, d["dates"], d["value"])
        if _vals:
            overlays.append({"ticker": _xt, "vals": _vals, "anchor": _anch, "color": PALETTE[(_i + 1) % len(PALETTE)]})
    combo_targets = [ticker, *(extra_overlays or [])]
    caught_all = all(t in _named for t in combo_targets)
    both_held = (sum(1 for r in bt.get("rows", [])
                     if all(f"{t}:" in (r.get("held") or "") for t in combo_targets)) if extra_overlays else 0)
    payload = {
        "gem": ticker, "overlay_label": f"{ticker} trigger", "caught": caught,
        "overlays": overlays, "gem_label": label_override or ticker,
        "combo_targets": combo_targets, "caught_all": caught_all, "both_held": both_held,
        "model": disp_model, "storyline": STORYLINE.get(ticker, ""), "ever_funded": ever_funded,
        "seeds": _gem_seeds(ticker),
        "capital": capital, "dates": d["dates"], "value": d["value"], "spy": d["spy"],
        "gain": d.get("gain", {}), "gain_series": d.get("gain_series", {}),
        "overlay": d["overlay"], "overlay_ticker": d["overlay_ticker"],
        "overlay_anchor": d["overlay_anchor"], "alloc": d["alloc"], "cash": d["cash"],
        "colors": {t: PALETTE[i % len(PALETTE)] for i, t in enumerate(tickers)},
        "metrics": metrics(d["value"], d["spy"], capital),
        "cost_usd": book_cost(d["dates"]), "weeks": bt["weeks"], "gems": gems,
        "watchlist": watchlist, "watch_daily": watch_daily,
        "retrieval": retstats.load(str(cfg["stats"])), "params": {**fm, "model": disp_model},
        "arcs": arcs, "lifecycle": lifecycle, "agents": agent_meta, "agent_of": agent_of,
        "agent_marks": agent_marks, "agent_gain": agent_gain,
        "agent_conviction": agent_conviction, "agent_convgain": agent_convgain,
        "agent_precision": bt.get("agent_precision", []),
    }
    out = cfg["out"]; out.mkdir(parents=True, exist_ok=True)
    pj = json.dumps(payload).replace("</", "<\\/")   # inline data (works from file:// too), </script>-safe
    (out / "data.json").write_text(json.dumps(payload, indent=2))   # kept: landing/sweeps read this
    _write_page(out / "index.html", INDEX_HTML.replace("{{DATA}}", pj))
    _write_page(out / "firehose.html", FIREHOSE_HTML.replace("{{DATA}}", pj))
    m = payload["metrics"]
    print(f"  {ticker}: ${capital:,.0f} -> ${m['final']:,.0f} ({m['total_ret']:+.1%}), "
          f"maxDD {m['max_dd']:.1%}  caught={caught}  -> {out}/")
    return payload


ACTIVE_GEMS = {"MP", "BWET", "GEO", "MSTR"}   # the event-driven gems we're actively tuning on — colored + first-row; rest greyed
                               # (SMR dropped: theme/AI-nuclear gem, no discrete catalyst — same wall as GDX)
PLOT1_PALETTE = ["#2980b9", "#c0392b", "#27ae60", "#8e44ad", "#e67e22", "#16a085"]  # active-gem colors: Plot 1 curve == card name


def _gem_universe() -> list:
    """EVERY candidate gem's ticker price over ~24 months from its trigger, indexed to 100 at the
    trigger — the full universe (not just the scanned few), each tagged with its max multiple over the
    window and whether it's been scanned. Lets us cherry-pick the big movers (3x+) and stop judging the
    solution on weak ones (e.g. GDX ~2x vs SMR/MP/BWET 3x+)."""
    import pandas as pd  # noqa: PLC0415
    import score  # noqa: PLC0415
    spec = json.loads(GEMS_JSON.read_text())
    scanned = {p.parent.name.upper() for p in OUT_DIR.glob("*/data.json")}
    tks = sorted({g["ticker"] for g in spec["gems"]})
    try:
        panel = score.fetch_panel(tks, spec["window"]["start"], "2026-07-08", use_cache=False)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for g in spec["gems"]:
        tk, trig = g["ticker"], g.get("trigger_date")
        if tk not in panel.columns or not trig:
            continue
        s = panel[tk].dropna()
        wend = g.get("window_end")   # per-gem window end if set, else 24 months from trigger
        end = pd.Timestamp(wend) if wend else pd.Timestamp(trig) + pd.Timedelta(days=730)
        s = s[(s.index >= pd.Timestamp(trig)) & (s.index <= end)]
        if len(s) < 2 or not float(s.iloc[0]):
            continue
        base = float(s.iloc[0])
        out.append({"ticker": tk, "name": g.get("name", ""), "start": trig, "scanned": tk in scanned,
                    "active": tk in ACTIVE_GEMS,   # the gems we're currently tuning on (colored; rest greyed)
                    "mult": round(float(s.max()) / base, 1),   # legend label, 1 decimal (8.38 -> 8.4)
                    "x": [d.strftime("%Y-%m-%d") for d in s.index],
                    "y": [round(float(v) / base, 3) for v in s]})   # normalized: starts at 1.0 (= the multiple)
    out = sorted(out, key=lambda r: -r["mult"])   # biggest movers first
    ci = 0
    for r in out:                                  # assign each active gem its palette color (curve == card name)
        r["color"] = None
        if r["active"]:
            r["color"] = PLOT1_PALETTE[ci % len(PLOT1_PALETTE)]
            ci += 1
    return out


def build_combo() -> None:
    """GEO+MSTR concurrency card from the ONE election2024 book (both gems overlaid on one portfolio) —
    the 2-agents-riding-2-gems view. Uses the reframed-seed v2 book once it exists, else the v1 book."""
    v2 = ROOT / "data" / "windows" / "firehose_scans_election2024_v2.json"
    src = "firehose_scans_election2024_v2.json" if v2.exists() else "firehose_scans_election2024.json"
    build_gem("GEO", extra_overlays=["MSTR"], scans_override=src,
              out_override="geo_mstr", label_override="GEO + MSTR — 2024 election concurrency")


def build_landing() -> None:
    """Landing page at docs/index.html: Plot 1 = every candidate gem's price curve (cherry-pick the
    big movers) + one card per SCANNED gem (docs/<gem>/data.json), ordered chronologically."""
    rows = []
    for sub in sorted(OUT_DIR.glob("*/data.json")):
        d = json.loads(sub.read_text())
        if "metrics" not in d or "gem" not in d:
            continue                      # skip non-gem subdirs (e.g. docs/sweeps/)
        m = d["metrics"]
        _targets = d.get("combo_targets") or [d.get("gem", sub.parent.name.upper())]
        _combo = len(_targets) > 1
        gem = d.get("gem_label") or d.get("gem", sub.parent.name.upper())
        rows.append({"gem": gem, "url": f"{sub.parent.name}/index.html",
                     "active": any(t in ACTIVE_GEMS for t in _targets),
                     "ret": m["total_ret"], "spy": m["spy_ret"], "maxdd": m["max_dd"],
                     "caught": d.get("caught_all") if _combo else d.get("caught"),
                     "both_held": d.get("both_held") if _combo else None,
                     "window": f'{d["dates"][0]} → {d["dates"][-1]}', "model": d.get("model", "—"),
                     "join": (d.get("retrieval") or {}).get("wayback", {}).get("join_rate_pct")})
    series = _gem_universe()                       # Plot 1: all candidate gems (sorted by multiple)
    cmap = {s["ticker"]: s["color"] for s in series if s.get("color")}   # gem -> Plot 1 curve color
    rows.sort(key=lambda r: (not r["active"], r["window"]))   # active gems first (first row), then the rest

    def card(r):
        cls = "pos" if r["ret"] >= 0 else "neg"
        cc = "pos" if r["caught"] else "neg"
        caught = "✓ caught" if r["caught"] else "✗ missed"
        jn = f"{r['join']}%" if r["join"] is not None else "—"
        mut = "" if r["active"] else " muted"      # grey out gems we're not currently focused on
        gstyle = f' style="color:{cmap[r["gem"]]}"' if r["gem"] in cmap else ""  # name matches its curve
        return (f'<a class="gcard{mut}" href="{r["url"]}"><div class="gt"{gstyle}>{r["gem"]}</div>'
                f'<div class="gv {cls}">{r["ret"]*100:+.0f}%</div>'
                f'<div class="gs">vs SPY {r["spy"]*100:+.0f}% · maxDD {r["maxdd"]*100:.0f}%</div>'
                f'<div class="gs"><span class="{cc}">{caught}</span> · Wayback join {jn}</div>'
                f'<div class="gs">{r["window"]} · model <b>{r["model"]}</b></div></a>')
    cards = "".join(card(r) for r in rows) or '<p class="sub">No gem dashboards built yet.</p>'
    OUT_DIR.mkdir(exist_ok=True)
    html = LANDING_HTML.replace("{{CARDS}}", cards).replace("{{GEMSPLOT}}", json.dumps(series))
    _write_page(OUT_DIR / "index.html", html)
    print(f"  landing: {len(rows)} gem(s) -> {OUT_DIR}/index.html")


# Parameter sweeps. Each entry re-scores every gem's book across `values` of `key` (an fm knob)
# and the sweeps dashboard plots SUM-across-gems of final curated value vs the parameter. Extensible:
# add risk_aversion / min_trade_size here later (left commented so they're not run yet).
# Short vertical/theme per gem, for sweep-legend labels (e.g. "SMR (nuclear)").
GEM_VERTICAL = {
    "BWET": "shipping", "MP": "rare earth", "SMR": "nuclear", "RNMBY": "defense", "NVDA": "AI",
    "CVNA": "consumer", "SMCI": "AI servers", "PLTR": "AI software", "URA": "uranium",
    "YPF": "Argentina", "HIMS": "GLP-1", "MSTR": "crypto", "GDX": "gold",
}

SWEEPS = [
    {"key": "lookback_period_days", "label": "lookback_period_days", "log": True,
     "values": [3, 7, 14, 21, 30, 45, 60, 75, 100, 120, 150, 180]},   # ~3 days -> ~6mo μ/Σ fit (log x)
    {"key": "concentration_cap", "label": "concentration_cap",
     "values": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0]},
    {"key": "min_trade_size", "label": "min_trade_size",
     "values": [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]},
    {"key": "risk_aversion", "label": "risk_aversion",
     "values": [0.0, 0.1, 0.25, 0.5, 0.67, 0.85, 1.0, 1.25, 1.5, 2.0, 3.0]},   # 0 = pure-μ -> high λ = risk-averse
    {"key": "max_agents", "label": "max_agents",
     "values": [1, 2, 3, 4, 5, 6, 8]},              # top-N events (by conviction) kept in the weekly watchlist
    {"key": "spy_agent_conviction", "label": "spy_agent_conviction",
     "values": [0, 3, 4, 5, 6, 7, 8]},              # 0 = off; SPY agent an event must out-rank to be held
]

# The (non-LLM) parameter sweeps are restricted to these gems only.
SWEEP_GEMS = {"BWET", "MP"}

# Baseline fm overrides for the SWEEPS only — the non-swept knobs are held at these values
# (independent of the live gem-dashboard defaults). Pin cap=0.5 here per request.
SWEEP_BASE = {}   # empty -> sweeps follow the live investor_profile defaults (add keys to pin a
                  # sweep-only baseline that differs from the gem dashboards)

# Model bake-off: re-score each curator LLM's 3-gem books on the SAME per-gem panel and compare.
# (short -> (display label, scale, approx $/3-gem). Order = display order, cheap/small -> big.)
BAKEOFF_INFO = {  # (label, scale, MEASURED $ /3-gem scan, MEASURED wall-clock /3-gem scan) — today's ledger
    "mimo":     ("mimo",     "~1T MoE / 42B act",  "$0.4", "83min"),
    "llama4":   ("llama4",   "400B MoE / 17B act", "$0.4", "10min"),
    "deepseek": ("deepseek", "671B MoE / 37B act", "$0.1", "14min"),
    "grok4":    ("grok-4.3", "frontier",           "$3.7", "9min"),
    "sonnet":   ("sonnet",   "1-2T (est)",         "$3.6", "61min"),
    "sonnet5":  ("sonnet5",  "near-Opus",          "$3.8", "8min"),
    "opus":     ("opus",     "2-5T (est)",         "$4.4", "16min"),
}


def _model_book_path(short: str, gem: str):
    """Every model's bake-off book lives under bakeoff/ as firehose_scans_<gem>__<model>.json —
    one canonical store, all written by the 6-model bake-off re-scan under the current prompts."""
    return ROOT / "data" / "windows" / "bakeoff" / f"firehose_scans_{gem.lower()}__{short}.json"


def build_sweeps() -> None:
    """Sweep dashboard at docs/sweeps/: for each parameter, re-score every gem's book across its
    values (ONE fixed price panel per gem, so the cap comparison is clean) and write the SUM across
    gems of Final Curated Portfolio value + Sum Final SPY (flat benchmark). Extensible via SWEEPS."""
    import score
    fm0 = {**load_financial_model(str(ROOT / "investor_profile.md")), **SWEEP_BASE}
    capital = float(fm0.get("initial_investment_usd", 50_000))
    # Include only gems with a CURRENT built dashboard (docs/<gem>/data.json) — this couples sweep
    # membership to the live curated set, so a stale/other-prompt scan (e.g. a pre-gate RNMBY) that
    # has no dashboard is auto-excluded, and any gem joins the sweep once its dashboard is built.
    gem_tickers = [g["ticker"] for g in json.loads(GEMS_JSON.read_text())["gems"]
                   if g["ticker"] in SWEEP_GEMS
                   and gem_config(g["ticker"])["scans"].exists()
                   and (gem_config(g["ticker"])["out"] / "data.json").exists()]
    if not gem_tickers:
        print("  sweeps: no built gem dashboards yet — skipped"); return
    # enough pre-window history to cover the LONGEST lookback being swept (else early-week μ/Σ fits
    # would run short); +30d buffer, floor 70d.
    pre = max([70] + [max(sw["values"]) + 30 for sw in SWEEPS if sw["key"] == "lookback_period_days"])
    # bake-off models with a COMPLETE set of books (all 3 gems) — these get scored on the panels too
    # bake-off runs on the gems that HAVE bake-off books (3-gem set) — NOT all built gems: GDX has a
    # dashboard but no bake-off books, so requiring it would empty bake_models and drop the LLM plot.
    bake_tickers = [t for t in gem_tickers if any(_model_book_path(s, t).exists() for s in BAKEOFF_INFO)]
    bake_models = [s for s in BAKEOFF_INFO if bake_tickers and all(_model_book_path(s, t).exists() for t in bake_tickers)]
    # load each gem's scans + fetch ONE panel, reused across every param/value (deterministic compare).
    # Panel tickers = union across ALL model books for the gem, so every model can be scored on it.
    gem_data = {}
    models = {}
    for t in gem_tickers:
        cfg = gem_config(t)
        meta_p = cfg["scans"].with_suffix(".meta.json")
        models[t] = (json.loads(meta_p.read_text()).get("model") if meta_p.exists()
                     else fm0.get("model", "mimo"))
        scans = load_scans(cfg["scans"])
        ana = list(scans)
        tix = {score.BENCHMARK, t} | {p["ticker"] for v in scans.values() for p in v
                                      if str(p.get("ticker", "")).strip()}
        for s in bake_models:  # add every bake-off model's tickers so its book is scorable
            bp = _model_book_path(s, t)
            if bp.exists():
                for v in load_scans(bp).values():
                    tix |= {p["ticker"] for p in v if str(p.get("ticker", "")).strip()}
        start = (ana[0] - pd.Timedelta(days=pre)).strftime("%Y-%m-%d")
        end = (ana[-1] + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
        gem_data[t] = (scans, score.fetch_panel(sorted(tix), start, end, use_cache=False), cfg["trigger"])
    # GEO+MSTR election concurrency book as an extra sweep series. The weekly review cadence CAN'T
    # harvest this gem (only insiders / smart money catch GEO+MSTR in time), so it's swept purely to
    # find params that MINIMIZE the election loss (damage control) — not to profit. Injected directly
    # since it's not a single gems.json ticker.
    combo_p = ROOT / "data" / "windows" / "firehose_scans_election2024_v2.json"
    if combo_p.exists():
        cscans = load_scans(combo_p)
        cana = list(cscans)
        ctix = {score.BENCHMARK, "GEO", "MSTR"} | {p["ticker"] for v in cscans.values()
                                                   for p in v if str(p.get("ticker", "")).strip()}
        cstart = (cana[0] - pd.Timedelta(days=pre)).strftime("%Y-%m-%d")
        cend = (cana[-1] + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
        gem_data["GEO_MSTR"] = (cscans, score.fetch_panel(sorted(ctix), cstart, cend, use_cache=False), "2024-11-05")
        _cm = combo_p.with_suffix(".meta.json")
        models["GEO_MSTR"] = json.loads(_cm.read_text()).get("model") if _cm.exists() else fm0.get("model", "mimo")
        gem_tickers = [*gem_tickers, "GEO_MSTR"]
        # include GEO_MSTR in the bake-off too (bake_tickers/bake_models were computed before this add);
        # requiring its book excludes half-finished models (only fully-scanned models appear -> clean staging).
        if any(_model_book_path(s, "GEO_MSTR").exists() for s in BAKEOFF_INFO):
            bake_tickers.append("GEO_MSTR")
            bake_models[:] = [s for s in BAKEOFF_INFO if all(_model_book_path(s, t).exists() for t in bake_tickers)]
    out = {"gems": gem_tickers, "capital_per_gem": capital, "params": {}, "models": models,
           "verticals": {t: GEM_VERTICAL.get(t, "") for t in gem_tickers},
           "baseline": {k: fm0.get(k) for k in
                        ("concentration_cap", "min_trade_size", "lookback_period_days", "risk_aversion")}}
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
        out["params"][key] = {"label": sw["label"], "values": vals, "log": sw.get("log", False),
                              "sum_curated": sum_cur, "sum_spy": sum_spy, "per_gem": per_gem}
        print(f"  sweep {key}: " + " ".join(f"{v}->${c:,.0f}" for v, c in zip(vals, sum_cur)))
    # ---- LLM bake-off: each model's 3 books re-scored on the same panels at the live defaults ----
    if bake_models:
        bo = {"models": [], "label": [], "scale": [], "cost": [], "time": [], "sum_curated": [],
              "per_gem": {t: [] for t in bake_tickers}, "caught": {}}
        for s in bake_models:
            total = 0.0; caught = {}
            for t in bake_tickers:
                bk = load_scans(_model_book_path(s, t))
                _, panel, anchor = gem_data[t]
                bt = firehose.backtest(bk, fm0, capital, panel=panel, overlay=t, overlay_anchor=anchor)
                total += bt["final"]; bo["per_gem"][t].append(round(bt["final"]))
                _tg = {"GEO", "MSTR"} if t == "GEO_MSTR" else {t}   # combo: caught if either target named
                caught[t] = any(str(p.get("ticker", "")).strip().upper() in _tg
                                for v in bk.values() for p in v)
            lbl, scl, cst, tm = BAKEOFF_INFO[s]
            bo["models"].append(s); bo["label"].append(lbl); bo["scale"].append(scl)
            bo["cost"].append(cst); bo["time"].append(tm)
            bo["sum_curated"].append(round(total)); bo["caught"][s] = caught
        out["bakeoff"] = bo
        print("  bake-off: " + " ".join(f"{l}->${c:,.0f}" for l, c in zip(bo["label"], bo["sum_curated"])))
    sd = OUT_DIR / "sweeps"; sd.mkdir(parents=True, exist_ok=True)
    (sd / "data.json").write_text(json.dumps(out, indent=2))
    _write_page(sd / "index.html", SWEEPS_HTML.replace("{{DATA}}", json.dumps(out).replace("</", "<\\/")))
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


STORYLINE = {
    "SMR": (
        "<b>NuScale Power (SMR)</b> builds small modular reactors. It rose through mid-2024 on the "
        "nuclear-revival trade — AI datacenters scrambling for clean baseload power, and Washington "
        "turning pro-nuclear. The move <b>peaked when the ADVANCE Act was signed (July 9, 2024)</b> — "
        "the <i>Accelerating Deployment of Versatile, Advanced Nuclear for Clean Energy Act</i>, which "
        "streamlines NRC licensing and fees for advanced / small modular reactors. Classic "
        "buy-the-rumor / sell-the-news: SMR topped ~July 15, then fell ~44%. "
        "<b>What we'd want:</b> exit as the bill is signed (the catalyst resolves) — which the agent did, "
        "exiting 2024-07-12 within ~3 days and ~3% of the peak, dodging the crash."
    ),
    "MP": (
        "<b>MP Materials (MP)</b> is the main US rare-earth miner. It rose on <b>China's rare-earth "
        "export curbs</b> — Beijing restricting critical-mineral exports makes a domestic supplier "
        "strategically valuable. The catalyst is <b>open-ended</b> (ongoing trade tension, no single "
        "resolution date); MP's price flattened around Nov 2025 as the scarcity premium faded. "
        "<b>What we'd want:</b> exit when the curbs ease / the premium fades (~Nov 2025). Here the agent "
        "held the position live the whole window — the <i>under-exit</i> case, because an open-ended "
        "catalyst never cleanly 'resolves' on a date."
    ),
    "BWET": (
        "<b>Breakwave Dry Bulk Shipping (BWET)</b> tracks dry-bulk freight rates. It spiked on the "
        "<b>2026 Iran war and Strait-of-Hormuz risk</b>, which threatened Gulf shipping lanes and sent "
        "freight rates soaring. The catalyst <b>resolves when the conflict de-escalates</b> (a ceasefire, "
        "or the Strait reopening) and rates roll over. "
        "<b>What we'd want:</b> ride it while the war risk is live and exit when it resolves — <i>not</i> "
        "when coverage merely gets crowded (crowding is not thesis death)."
    ),
}

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
 .story{background:#fff;border:1px solid var(--line);border-left:4px solid #2980b9;border-radius:8px;padding:12px 16px;font-size:13.5px;line-height:1.55;margin:12px 0 20px}
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
 details.clip{display:inline}
 details.clip>summary{display:inline;cursor:pointer;color:#2980b9;font-size:11px;list-style:none}
 details.clip>summary::-webkit-details-marker{display:none}
 details.clip[open]>summary{display:none}
</style></head>
<body><div class="wrap">
 <nav class="nav"><a href="../index.html">↑ All gems</a>
   <a href="index.html" class="active">Dashboard</a>
   <a href="firehose.html">Firehose log</a>
   <a href="../sweeps/index.html">Sweeps</a>
   <a href="https://github.com/joehahn/geo-herd-rider/blob/main/README.md">README</a></nav>
 <h1 id="gemtitle">Gem scan</h1>
 <p class="sub" id="sub"></p>
 <div class="story" id="story"></div>
 <div class="cards" id="cards"></div>

 <h2>Scan parameters</h2>
 <table id="params" style="border-collapse:collapse;font-size:13px;max-width:560px"></table>

 <h2>Plot 1 — Portfolio value</h2>
 <div id="chart"></div>

 <h2>Plot 2 — Cumulative $ gain per agent (event)</h2>
 <p class="sub">Each funded event's running $ contribution to the book — a line <b>climbs while the agent
   holds, then flatlines at its realized gain once it exits.</b> <b>▲</b> marks the week the agent went
   live, <b>✕</b> its exit. The bold <b>Total</b> is the portfolio's gain (the lines sum to it).
   Flat-near-zero = an agent that contributed little; never-funded agents are omitted.</p>
 <div id="gainseries"></div>

 <h2>Plot 3 — Allocation over time</h2>
 <p class="sub">Capital committed per ticker (cash fills the rest). Fully invested while the
   watchlist is non-empty; to cash when the press names nothing live.</p>
 <div id="alloc"></div>
 <p class="sub" id="allocnote" style="margin-top:4px"></p>

 <h2>Plot 4 — Holdings timeline (proposed vs funded)</h2>
 <p class="sub">One row per ticker the curator <b>named</b>. <span style="color:#aab">Thin gray, small
   dots</span> = <b>proposed</b> (on the live watchlist); <b>thick colored, large dots</b> = <b>funded</b>
   (the optimizer actually bought it).</p>
 <div id="gantt"></div>

 <h2>Plot 5 — Dollars held per ticker</h2>
 <p class="sub">Capital in <b>dollars</b> per ticker over time (cash fills to the portfolio total, so the
   stack's top edge is the portfolio value). Plot 3 shows the same split as percentages.</p>
 <div id="dollars"></div>

 <h2>Plot 6 — Cumulative $ gain per holding</h2>
 <p class="sub" style="margin:0 0 6px">Total dollar P&amp;L each holding contributed over the window
   (Σ daily position-value × daily return). Green = winner, red = loser; the bars sum to the
   portfolio's total gain.</p>
 <div id="gain"></div>

 <h2>Plot 7 — Cumulative $ earned per agent (event)</h2>
 <p class="sub" style="margin:0 0 6px">Total dollar P&amp;L attributed to each <b>distinct agent</b> (event id),
   partitioning a ticker's gain across its agents by their active windows — so a ticker that spawned two
   agents (e.g. BWET's <code>ev2</code> then <code>ev6</code>) shows each one's own contribution. Green =
   winner, red = loser; the bars sum to the portfolio's total gain.</p>
 <div id="agentgain"></div>

 <h2>Plot 8 — Conviction score over time, per agent</h2>
 <p class="sub" style="margin:0 0 6px">Each agent's <b>catalyst-conviction</b> rating (1-10) week by week —
   how strong / early / datable it judged its own catalyst. The dashed grey line is the always-on
   <b>SPY agent</b> (<code>spy_agent_conviction</code>): a live event must out-rank it to be held.</p>
 <div id="convtime"></div>

 <h2>Plot 9 — Gain vs conviction over time, per agent</h2>
 <p class="sub" style="margin:0 0 6px">Each agent's <b>time-history</b> as a connected path through
   (conviction&nbsp;x, cumulative&nbsp;$&nbsp;gain&nbsp;y) space — one dot per week, joined in time order. A path that
   climbs <b>up-and-right</b> = conviction and gain rising together (a good, self-aware thesis); a path
   <b>high on x but sinking on y</b> = high conviction that isn't paying off (a conviction miss). SPY (the
   always-on agent) is the dashed grey path.</p>
 <div id="gainconv"></div>

 <h2 id="agentprec_h"></h2>
 <p class="sub" style="margin:0 0 6px">CURATOR SKILL, <b>unmasked by the optimizer</b>: for <i>every</i> agent
   the curator created, the standalone return of its ticker over the weeks it was thesis-live — "if you'd
   simply held what this agent named while it said hold, did it rise?" Independent of sizing/caps, so it
   measures whether the scout/agent picks <b>good theses</b> or <b>manufactures losers</b>. Green = winning
   thesis, red = losing; the headline is the <b>precision</b> (share of agents that made money).</p>
 <div id="agentprec"></div>

 <h2>Plot 11 — Watchlist by date</h2>
 <p class="sub" style="margin:0 0 0">Each row is a date the live watchlist (or its funding) changed —
   the names the press kept thesis-live that week. <b>Bold + colored</b> = actually funded by the
   optimizer; <span style="color:#aaa">gray</span> = on the watchlist but pruned by the sizing floor.</p>
 <table class="atab" id="watchtable"></table>

 <h2>Plot 12 — Gem lifecycle — full window (pre / live / exit / post)</h2>
 <p class="sub" style="margin:0 0 6px">EVERY week the firehose was scanned, and what the agent did with
   this gem: <b>pre</b> = scanned but not yet flagged; <b>live</b> / <b>exit</b> = held / thesis called
   dead; <b>post</b> = dropped, watching it stay dead. Shows the agent's reaction to the firehose
   <i>before, during, and after</i> the event — not just the live span.</p>
 <table class="atab" id="lifecycle"></table>

 <h2>Plot 13 — Agent journal — week-by-week (per event)</h2>
 <p class="sub" style="margin:0 0 6px">Each event-agent's arc since entry — one collapsible block per
   ticker (gem first), captioned with the event <b>thesis</b>. Columns are the raw journal fields:
   <code>thesis_live</code> (hold/exit), <code>thesis</code> (the event/catalyst), <code>exit_case</code>
   (devil's-advocate; RESOLVED flag forces the exit), <code>assessment</code> (the weekly read), and
   <code>exit_advice</code> (the exit trigger). Use it to confirm what event/ticker was discovered, when,
   and that it exited as the thesis decayed.</p>
 <div id="arcs"></div>

 <h2>What it cost</h2>
 <div id="costs"></div>

 <h2>Retrieval health (GDELT + Wayback)</h2>
 <p class="sub" style="margin:0 0 6px">Health of the news-retrieval for the run that built this book.
   The Wayback miss-split distinguishes a real archive gap (confirmed) from a rate-limit/transient
   failure (deferred — recoverable on re-run).</p>
 <div id="retr"></div>
</div>
<script>
Promise.resolve({{DATA}}).then(D=>{
  const fmt=x=>"$"+Math.round(x).toLocaleString();
  const pct=x=>(x>=0?"+":"")+(x*100).toFixed(1)+"%";
  const last=D.dates.length-1, m=D.metrics, cls=x=>x>=0?"pos":"neg";
  document.title = `Scan of the ${D.gem} gem — geo-herd-rider`;
  document.getElementById("gemtitle").textContent = `Scan of the ${D.gem} gem`;
  const gn=document.getElementById("gemname"); if(gn) gn.textContent = D.gem;
  const st=document.getElementById("story"); if(st){ if(D.storyline){st.innerHTML=D.storyline;} else {st.style.display="none";} }
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
  const order=["model","initial_investment_usd","concentration_cap","min_trade_size","risk_aversion",
    "max_agents","spy_agent_conviction","trailing_stop_pct",
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
  // overlay curves: the list D.overlays (primary + any extra target gems for a combo card), or the
  // single legacy D.overlay. Each gem's scaled price + a dotted trigger line, in its own color.
  const OVL=(D.overlays&&D.overlays.length)?D.overlays
    :(D.overlay?[{ticker:D.overlay_ticker,vals:D.overlay,anchor:D.overlay_anchor,color:OVC}]:[]);
  OVL.forEach(o=>{
    const oc=o.color||(D.colors&&D.colors[o.ticker])||OVC;
    vtraces.push({x:D.dates,y:o.vals,name:o.ticker+" (scaled)",
      line:{color:oc,width:1.8,dash:"dash"},connectgaps:true});
    vshapes.push({type:"line",x0:o.anchor,x1:o.anchor,yref:"paper",y0:0,y1:1,
      line:{color:oc,width:1,dash:"dot"}});
    vann.push({x:o.anchor,y:1,yref:"paper",yanchor:"bottom",showarrow:false,
      text:o.ticker+" trigger",font:{color:oc,size:10}});
  });
  // seed markers: a big star at each press-seed's publish date on the value curve; lede on hover
  const SD=(D.seeds||[]).filter(s=>s.date&&s.date>=D.dates[0]&&s.date<=D.dates[D.dates.length-1]);
  if(SD.length){
    const sx=SD.map(s=>{let i=D.dates.findIndex(d=>d>=s.date);return i<0?D.dates[D.dates.length-1]:D.dates[i];});
    const sy=sx.map(x=>{let i=D.dates.indexOf(x);return i<0?D.value[0]:D.value[i];});
    const clean=t=>String(t||"").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    vtraces.push({x:sx,y:sy,mode:"markers",name:"🌱 press seed",
      marker:{size:16,color:"#f1c40f",symbol:"star",line:{color:"#a67c00",width:1.5}},
      text:SD.map(s=>"🌱 <b>SEED</b> "+s.date+(s.source?" · "+clean(s.source):"")
        +"<br><b>"+clean(s.title)+"</b><br>"+clean(s.snippet).slice(0,150)),
      hovertemplate:"%{text}<extra></extra>"});
  }
  const XR=[D.dates[0],D.dates[last]];  // shared date range so plots 1-4 line up horizontally
  Plotly.newPlot("chart",vtraces,
    {margin:{l:80,r:140,t:24,b:36},legend:{orientation:"h",y:1.14},annotations:vann,shapes:vshapes,
     xaxis:{type:"date",range:XR,autorange:false},
     yaxis:{tickprefix:"$",separatethousands:true,automargin:false},hovermode:"x unified"},
    {displayModeBar:false,responsive:true});

  // Plot 2 — cumulative $ gain per agent (event): one line per FUNDED event (flatlines at exit) + bold Total
  const GS=D.gain_series||{}, FF=new Set(D.ever_funded||[]), AO=D.agent_of||{};
  const alab=t=>AO[t]?AO[t]+" ("+t+")":t;   // legend by agent id (ticker in parens)
  const gtr=Object.keys(GS).filter(t=>FF.has(t)).map(t=>({x:D.dates,y:GS[t],name:alab(t),mode:"lines",
    line:{color:D.colors[t]||"#888",width:2},hovertemplate:alab(t)+" $%{y:,.0f}"}));
  gtr.push({x:D.dates,y:D.value.map(v=>+(v-D.capital).toFixed(2)),name:"Total",mode:"lines",
    line:{color:"#111",width:3},hovertemplate:"Total $%{y:,.0f}"});
  // entry/exit markers LAST so they draw ON TOP of the Total curve: ▲ = went live, ✕ = exit
  const AM=D.agent_marks||{};
  const idxOf=ds=>{let j=0;for(let i=0;i<D.dates.length;i++){if(D.dates[i]<=ds)j=i;else break;}return j;};
  Object.keys(GS).filter(t=>FF.has(t)).forEach(t=>{
    const col=D.colors[t]||"#888", mk=AM[t]||{};
    const mkTrace=(dates,sym,tag)=>{const pts=(dates||[]).map(idxOf);if(!pts.length)return;
      gtr.push({x:pts.map(i=>D.dates[i]),y:pts.map(i=>GS[t][i]),mode:"markers",showlegend:false,cliponaxis:false,
        marker:{symbol:sym,size:26,color:col,line:{color:"#fff",width:2.5}},
        hovertemplate:alab(t)+" "+tag+" %{x|%Y-%m-%d}<extra></extra>"});};
    mkTrace(mk.live,"triangle-up","went live"); mkTrace(mk.exit,"x","exit");
  });
  // legend keys for the two marker symbols (neutral gray, no data point drawn)
  gtr.push({x:[D.dates[0]],y:[null],mode:"markers",name:"▲ went live",marker:{symbol:"triangle-up",size:15,color:"#666"}});
  gtr.push({x:[D.dates[0]],y:[null],mode:"markers",name:"✕ exit",marker:{symbol:"x",size:14,color:"#666"}});
  Plotly.newPlot("gainseries",gtr,
    {margin:{l:80,r:140,t:24,b:36},legend:{orientation:"h",y:1.14},
     xaxis:{type:"date",range:XR,autorange:false},
     yaxis:{tickprefix:"$",separatethousands:true,automargin:false,zeroline:true},hovermode:"x unified"},
    {displayModeBar:false,responsive:true});

  const traces=[];
  for(const t in D.alloc) traces.push({x:D.dates,y:D.alloc[t].map(v=>v*100),name:t,
    stackgroup:"a",line:{width:0},fillcolor:D.colors[t]||"#bbb",hovertemplate:"%{y:.0f}%"});
  traces.push({x:D.dates,y:D.cash.map(v=>v*100),name:"cash",stackgroup:"a",
    line:{width:0},fillcolor:"#dfe3e6",hovertemplate:"%{y:.0f}%"});
  Plotly.newPlot("alloc",traces,{margin:{l:80,r:140,t:40,b:36},
    xaxis:{type:"date",range:XR,autorange:false},
    yaxis:{ticksuffix:"%",range:[0,100],automargin:false},legend:{orientation:"h",y:1.22},hovermode:"x unified"},
    {displayModeBar:false,responsive:true});
  const dep=D.cash.filter(v=>v<0.999).length, n=D.cash.length;
  const peak={}; for(const t in D.alloc) peak[t]=Math.max(...D.alloc[t])*100;
  const top=Object.entries(peak).sort((a,b)=>b[1]-a[1]).slice(0,4).map(([t,v])=>`${t} ${v.toFixed(0)}%`).join(" · ");
  document.getElementById("allocnote").innerHTML=
    `Deployed <b>${(dep/n*100).toFixed(0)}%</b> of trading days (cash ${((n-dep)/n*100).toFixed(0)}%). Peak weights — ${top}.`;

  // Plot 4 — holdings Gantt: every curator-named ticker. Thin gray + small markers = PROPOSED
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
    yaxis:{tickmode:"array",tickvals:gord.map((_,i)=>i),ticktext:gord,autorange:"reversed",automargin:false},
    xaxis:{type:"date",range:XR,autorange:false},hovermode:"closest"},
    {displayModeBar:false,responsive:true});

  // Plot 5 — dollars held per ticker over time (stacked area; top edge = portfolio value).
  // FUNDED tickers only (gord above includes proposed-never-funded names that have no alloc series).
  const ord=Object.keys(D.alloc).filter(t=>D.alloc[t].some(w=>w>0.0001))
              .sort((a,b)=>D.alloc[a].findIndex(w=>w>0.0001)-D.alloc[b].findIndex(w=>w>0.0001));
  const dtraces=[];
  for(const t of ord) dtraces.push({x:D.dates,y:D.alloc[t].map((w,i)=>w*D.value[i]),name:t,
    stackgroup:"d",line:{width:0},fillcolor:D.colors[t]||"#bbb",hovertemplate:"$%{y:,.0f}"});
  dtraces.push({x:D.dates,y:D.cash.map((c,i)=>c*D.value[i]),name:"cash",stackgroup:"d",
    line:{width:0},fillcolor:"#dfe3e6",hovertemplate:"$%{y:,.0f}"});
  Plotly.newPlot("dollars",dtraces,{margin:{l:80,r:140,t:40,b:36},
    xaxis:{type:"date",range:XR,autorange:false},
    yaxis:{tickprefix:"$",separatethousands:true,automargin:false},legend:{orientation:"h",y:1.22},
    hovermode:"x unified"},{displayModeBar:false,responsive:true});

  // Plot 6 — cumulative $ gain per holding (sorted bar; green win / red loss; sums to total gain).
  const G=Object.entries(D.gain||{}).sort((a,b)=>b[1]-a[1]);
  Plotly.newPlot("gain",[{type:"bar",x:G.map(e=>e[0]),y:G.map(e=>e[1]),
    marker:{color:G.map(e=>e[1]>=0?"#2ca02c":"#d62728")},
    hovertemplate:"%{x}<br>$%{y:,.0f}<extra></extra>"}],
    {margin:{l:72,r:30,t:18,b:50},xaxis:{tickangle:-30},
     yaxis:{tickprefix:"$",separatethousands:true,zeroline:true,zerolinecolor:"#888"}},
    {displayModeBar:false,responsive:true});

  // Plot 7 — cumulative $ earned per distinct agent (event); bars sum to total gain.
  const AG=D.agent_gain||{}, AGM=D.agents||{};
  const aglab=id=>AGM[id]?id+" ("+AGM[id].ticker+")":id;
  const agS=Object.entries(AG).sort((a,b)=>b[1]-a[1]);
  Plotly.newPlot("agentgain",[{type:"bar",x:agS.map(e=>aglab(e[0])),y:agS.map(e=>e[1]),
    marker:{color:agS.map(e=>e[1]>=0?"#2ca02c":"#d62728")},
    hovertemplate:"%{x}<br>$%{y:,.0f}<extra></extra>"}],
    {margin:{l:72,r:30,t:18,b:70},xaxis:{tickangle:-30},
     yaxis:{tickprefix:"$",separatethousands:true,zeroline:true,zerolinecolor:"#888"}},
    {displayModeBar:false,responsive:true});

  // Plot 8 — conviction score over time, per agent (one line each; SPY agent = dashed grey flat line).
  const AC=D.agent_conviction||{};
  const convTraces=Object.entries(AC).map(([aid,pts])=>({
    x:pts.map(p=>p.date), y:pts.map(p=>p.conviction), mode:"lines+markers", name:aglab(aid),
    line:aid==="spy"?{color:"#888",dash:"dash",width:1.5}:{width:2}, marker:{size:5}}));
  if(convTraces.length) Plotly.newPlot("convtime",convTraces,
    {margin:{l:46,r:130,t:16,b:40},legend:{orientation:"v",x:1.02,y:1},
     xaxis:{type:"date"},yaxis:{title:"conviction (1-10)",range:[0,10.5],dtick:2}},
    {displayModeBar:false,responsive:true});

  // Plot 9 — each agent's time-history as a connected path through (conviction, cumulative $ gain).
  const CG=D.agent_convgain||{};
  const cgTraces=Object.entries(CG).filter(([a,s])=>s&&s.length).map(([aid,s])=>({
    x:s.map(p=>p.conviction), y:s.map(p=>p.gain), mode:"lines+markers", name:aglab(aid),
    line:aid==="spy"?{color:"#888",dash:"dash",width:1.5}:{width:1.5}, marker:{size:6},
    customdata:s.map(p=>p.date),
    hovertemplate:"%{fullData.name}<br>%{customdata}<br>conviction %{x}<br>$%{y:,.0f}<extra></extra>"}));
  if(cgTraces.length) Plotly.newPlot("gainconv",cgTraces,
    {margin:{l:74,r:130,t:16,b:44},legend:{orientation:"v",x:1.02,y:1},
     xaxis:{title:"conviction (1-10)",range:[0,10.5],dtick:1},
     yaxis:{title:"cumulative $ gain",tickprefix:"$",separatethousands:true,zeroline:true,zerolinecolor:"#888"}},
    {displayModeBar:false,responsive:true});

  // Plot 10 — agent precision: standalone return per agent over its live span (curator skill, unmasked).
  const AP=(D.agent_precision||[]).filter(r=>r.ret!==null&&r.ret!==undefined);
  const nwin=AP.filter(r=>r.ret>0).length, nap=AP.length;
  const prec = nap ? Math.round(100*nwin/nap) : 0;
  const aph=document.getElementById("agentprec_h");
  if(aph) aph.textContent=`Plot 10 — Agent precision: ${nwin}/${nap} agents profitable (${prec}%) — curator skill, unmasked by sizing`;
  const APs=AP.slice().sort((a,b)=>a.ret-b.ret);
  Plotly.newPlot("agentprec",[{type:"bar",orientation:"h",
    y:APs.map(r=>r.ticker+" · "+String(r.thesis||"").slice(0,40)), x:APs.map(r=>r.ret*100),
    marker:{color:APs.map(r=>r.ret>=0?"#2ca02c":"#d62728")},
    hovertemplate:"%{y}<br>%{x:.0f}% over live span<extra></extra>"}],
    {margin:{l:280,r:30,t:10,b:40},height:Math.max(180,20*APs.length+60),
     xaxis:{ticksuffix:"%",zeroline:true,zerolinecolor:"#888"},yaxis:{automargin:false,tickfont:{size:10}}},
    {displayModeBar:false,responsive:true});

  // Plot 11 — watchlist by date: rows where the live watchlist or its funding changed.
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

  // Agent journal arcs: gem first, then FUNDED events, then never-funded proposals (muted, collapsed).
  const A=D.arcs||{}, F=new Set(D.ever_funded||[]);
  const ats=Object.keys(A).sort((x,y)=>{
    if(x===D.gem)return -1; if(y===D.gem)return 1;
    const fx=F.has(x), fy=F.has(y); if(fx!==fy)return fx?-1:1;
    return A[y].length-A[x].length;});
  const esc=s=>(s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
  // long text -> first n chars + a native pulldown ("more") for the rest (Plots 8 & 9)
  const clip=(s,n=160)=>{s=s||"";return s.length<=n?esc(s)
    :esc(s.slice(0,n))+'…<details class="clip"><summary>more</summary>'+esc(s.slice(n))+'</details>';};
  const nF=ats.filter(t=>F.has(t)).length, nU=ats.length-nF;
  const hdr = ats.length ? `<p class="sub">${nF} funded event(s) · ${nU} never-funded proposal(s) (the ineffective agents — muted below)</p>` : "";
  document.getElementById("arcs").innerHTML = hdr + (ats.length ? ats.map(t=>{
    const funded=F.has(t);
    const rows=A[t].map(e=>`<tr><td>${e.date}</td><td>${e.live?"live":"<b style='color:#c00'>EXIT</b>"}</td>`
      +`<td>${esc(e.src)}</td><td>${clip(e.thesis)}</td>`
      +`<td>${e.resolved?"<b style='color:#c00'>RESOLVED</b> · ":""}${clip(e.exit_case)||"—"}</td>`
      +`<td>${clip(e.assessment)}</td><td class="sub">${clip(e.exit_advice)}</td></tr>`).join("");
    const open = t===D.gem ? " open" : "";
    const thesis = (A[t][A[t].length-1]||{}).thesis || "";   // event catalyst (latest)
    const disc = (A[t][0]||{}).src || "";   // provenance of the FIRST week it appeared (discovery)
    const discTag = disc ? ` · <b style="color:${disc==='seed'?'#b45309':'#0d9488'}">discovered via ${disc}</b>` : "";
    const fundTag = funded ? "" : ` · <span style="color:#aaa">never funded</span>`;
    const style = funded ? "margin:0 0 6px" : "margin:0 0 6px;opacity:.5";
    return `<details${open} style="${style}"><summary><b>${t}</b>${AO[t]?` <span class="sub">agent ${AO[t]}</span>`:""} · ${A[t].length} wk`
      +`${t===D.gem?" (gem)":""}${discTag}${fundTag} — <span class="sub">${clip(thesis)}</span></summary>`
      +`<table class="atab"><thead><tr><th>Date</th><th>thesis_live</th><th>src</th><th>thesis (event)</th>`
      +`<th>exit_case</th><th>assessment</th><th>exit_advice</th></tr></thead><tbody>${rows}</tbody></table></details>`;
  }).join("") : '<p class="sub">No agent journal persisted for this book (re-scan to populate).</p>');

  // Gem lifecycle: full-window timeline for the overlay gem (pre / live / exit / post)
  const LC=D.lifecycle||[], sc={pre:"#999",post:"#999",live:"#0a7a0a",exit:"#c00"};
  document.getElementById("lifecycle").innerHTML = LC.length
    ? `<thead><tr><th>Date</th><th>agent</th><th>${D.gem}</th><th>src</th><th>exit_case</th><th>assessment</th><th>exit_advice</th></tr></thead><tbody>`
      + LC.map(e=>`<tr><td>${e.date}</td><td>${esc(e.agent||'')}</td><td style="color:${sc[e.state]||'#000'};font-weight:${e.state==='exit'?'bold':'normal'}">`
        +`${e.state==='pre'?'— not flagged':e.state==='post'?'— dropped':e.state}</td>`
        +`<td>${esc(e.src)}</td><td>${clip(e.exit_case)}</td><td>${clip(e.assessment)}</td>`
        +`<td class="sub">${clip(e.exit_advice)}</td></tr>`).join("") + `</tbody>`
    : '<tr><td class="sub">No lifecycle (re-scan to populate).</td></tr>';

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
Promise.resolve({{DATA}}).then(D=>{
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
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
 :root{--ink:#1a1a1a;--mut:#666;--line:#e3e3e3;--bg:#fafafa}
 h2{font-size:17px;margin:18px 0 2px}
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
 .muted{opacity:.4;filter:grayscale(1)}
 .muted:hover{opacity:.75;filter:grayscale(.3)}
 .foot{color:var(--mut);font-size:12px;margin-top:34px;border-top:1px solid var(--line);padding-top:12px}
</style></head>
<body><div class="wrap">
 <h1>geo-herd-rider — gem scans</h1>
 <p class="sub">Each card is one hidden-gem event scanned through the LLM news-firehose + a mean-variance
   optimizer. Return is the book vs SPY over the gem's window; <b>caught</b> = the firehose named the
   gem itself. Every number is a hindsight <b>upper bound</b> — the clean test is the forward eval.
   &nbsp;<a href="sweeps/index.html"><b>Parameter sweeps →</b></a> &middot;
   <a href="https://github.com/joehahn/geo-herd-rider/blob/main/README.md">README</a></p>
 <h2>Plot 1 — Each gem's price over its window <span class="sub" style="font-size:13px;font-weight:400">— normalized to 1.0× at each gem's trigger (24-mo window); the y-axis is the price multiple. The gems we're currently tuning on (SMR/MP/BWET) are in color with the axes scaled to them; the rest are greyed for context.</span></h2>
 <div id="gemsplot" style="height:380px;margin-bottom:6px"></div>
 <div class="grid">{{CARDS}}</div>
 <p class="foot">geo-herd-rider · generated by <code>scripts/build_dashboard.py --all</code></p>
<script>
 const S={{GEMSPLOT}};
 if(S&&S.length){
  const traces=S.map(s=>({x:s.x,y:s.y,name:s.ticker+" "+s.mult+"×",mode:"lines",
    line:{color:s.active?s.color:"#d2d2d2",width:s.active?2.8:1.5},
    opacity:s.active?1:0.5,showlegend:s.active,connectgaps:false,
    hovertemplate:s.ticker+" %{y:.2f}× ("+s.mult+"× peak)<br>%{x|%Y-%m-%d}<extra></extra>"}));
  const A=S.filter(s=>s.active);
  const xs=A.flatMap(s=>[s.x[0],s.x[s.x.length-1]]).sort();          // x-range = active gems' span
  const xr=xs.length?[xs[0],xs[xs.length-1]]:undefined;
  const ay=A.flatMap(s=>s.y).filter(v=>v!=null);                     // y-range fit to the colored curves
  const yr=ay.length?[Math.max(0,Math.min(...ay)-0.3),Math.max(...ay)*1.05]:undefined;
  Plotly.newPlot("gemsplot", traces,
   {margin:{l:46,r:14,t:6,b:34},legend:{orientation:"h",y:1.16,font:{size:11}},
    xaxis:{type:"date",range:xr},yaxis:{title:"multiple (start = 1.0×)",range:yr,zeroline:false},hovermode:"closest"},
   {displayModeBar:false,responsive:true});
 }
</script>
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
 <table id="sparams" style="border-collapse:collapse;font-size:13px;max-width:520px;margin:0 0 10px"></table>
 <div id="charts"></div>
 <p class="foot">geo-herd-rider · generated by <code>scripts/build_dashboard.py --all</code></p>
</div>
<script>
Promise.resolve({{DATA}}).then(D=>{
  const gems=D.gems||[], n=gems.length;
  const B=D.baseline||{};
  document.getElementById("sub").textContent =
    `Sum across ${n} gem book(s) (${gems.join(", ")}) · $${(D.capital_per_gem*n).toLocaleString()} total start. `
    +`Each book re-scored on a fixed price panel per gem; each plot sweeps one knob, the rest held at the baseline below.`;
  const prow=(k,v)=>`<tr><td style="padding:3px 16px 3px 0;border-bottom:1px solid #eee"><code>${k}</code></td>`
    +`<td style="padding:3px 0;border-bottom:1px solid #eee;text-align:right">${v}</td></tr>`;
  const M=D.models||{}, mvals=[...new Set(Object.values(M))];
  const mdisp = mvals.length<=1 ? (mvals[0]||"—")
              : gems.map(g=>`${g}:${M[g]||"—"}`).join(", ");
  document.getElementById("sparams").innerHTML=
    prow("model", mdisp)
    + prow("gems", gems.join(", ")) + prow("total start", "$"+(D.capital_per_gem*n).toLocaleString())
    + prow("concentration_cap", B.concentration_cap) + prow("min_trade_size", B.min_trade_size)
    + prow("lookback_period_days", B.lookback_period_days) + prow("risk_aversion", B.risk_aversion);
  const host=document.getElementById("charts"), P=D.params||{};
  const pal=["#1f77b4","#2ca02c","#9467bd","#ff7f0e","#17becf"];
  // ---- Plot 1: LLM bake-off — connected line chart, Final Curated value per curator model ----
  const BO=D.bakeoff;
  if(BO && BO.models && BO.models.length){
    const h2=document.createElement("h2");
    h2.textContent="Plot 1 — LLM bake-off — Final Curated value per curator model (3 gems, live defaults)";
    host.appendChild(h2);
    const div=document.createElement("div"); div.className="chart"; div.id="c_bakeoff"; host.appendChild(div);
    const ncost=s=>parseFloat(String(s).replace(/[^0-9.]/g,""))||0;
    const idx=BO.models.map((_,i)=>i).sort((a,b)=>ncost(BO.cost[a])-ncost(BO.cost[b]));  // cheapest -> priciest
    const labels=idx.map(i=>BO.label[i]+"<br>"+BO.scale[i]+"<br>"+BO.cost[i]+(BO.time&&BO.time[i]?" · "+BO.time[i]:""));  // size · cost · wall-clock
    const gems=Object.keys(BO.per_gem||{});  // bake-off tickers ONLY (not D.gems — excludes GEO_MSTR, which has no bake books)
    const traces=[{type:"scatter", mode:"lines+markers+text", name:"Sum (3 gems)", x:labels,
      y:idx.map(i=>BO.sum_curated[i]), line:{color:"#d62728",width:2.8}, marker:{size:9},
      text:idx.map(i=>gems.map(g=>g+(BO.caught[BO.models[i]][g]?"✓":"✗")).join(" ")),
      textposition:"top center", textfont:{size:9},
      hovertemplate:"%{x}<br>Sum $%{y:,.0f}<extra></extra>"}];
    gems.forEach((g,gi)=>traces.push({type:"scatter", mode:"lines+markers", name:g, x:labels,
      y:idx.map(i=>BO.per_gem[g][i]), line:{color:pal[gi%pal.length],width:2,dash:"dash"}, marker:{size:6},
      customdata:idx.map(i=>BO.caught[BO.models[i]][g]?"caught ✓":"missed ✗"),
      hovertemplate:`%{x}<br>${g} $%{y:,.0f} (%{customdata})<extra></extra>`}));
    Plotly.newPlot(div.id, traces, {margin:{l:72,r:20,t:24,b:66},
      yaxis:{tickprefix:"$",separatethousands:true}, xaxis:{tickangle:0, tickfont:{size:11}},
      legend:{orientation:"h",y:1.14}, hovermode:"x unified"}, {displayModeBar:false,responsive:true});
  }
  Object.keys(P).forEach((k,i)=>{
    const p=P[k];
    const h2=document.createElement("h2"); h2.textContent=`Plot ${i+2} — Sum Final Curated Portfolio vs ${p.label}`; host.appendChild(h2);
    const div=document.createElement("div"); div.className="chart"; div.id="c_"+k; host.appendChild(div);
    const traces=[
      {x:p.values,y:p.sum_curated,name:"Sum Final Curated",mode:"lines+markers",line:{color:"#d62728",width:2.6},marker:{size:8}},
      {x:p.values,y:p.sum_spy,name:"Sum Final SPY",mode:"lines+markers",line:{color:"#7f7f7f",width:2},marker:{size:6}},
    ];
    const V=D.verticals||{};
    gems.forEach((g,gi)=>{ if(p.per_gem&&p.per_gem[g]) traces.push(
      {x:p.values,y:p.per_gem[g],name:g+(V[g]?" ("+V[g]+")":""),mode:"lines+markers",
       line:{color:pal[gi%pal.length],width:2.2,dash:"dash"},marker:{size:6}}); });
    Plotly.newPlot(div.id,traces,{margin:{l:72,r:30,t:14,b:46},
      xaxis:{title:p.label+(p.log?" (log)":""),type:p.log?"log":"linear",tickvals:p.values,ticktext:p.values.map(String)},
      yaxis:{tickprefix:"$",separatethousands:true},
      legend:{orientation:"h",y:1.16},hovermode:"x unified"},{displayModeBar:false,responsive:true});
  });
  if(!Object.keys(P).length) host.innerHTML='<p class="sub">No sweeps recorded yet.</p>';
});
</script></body></html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
