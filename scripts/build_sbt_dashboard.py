#!/usr/bin/env python3
"""build_sbt_dashboard.py — the Sweep Backtest (SBT) dashboard: docs/sbt.html

Renders the FULL-FACTORIAL optimizer grid from scripts/sweep_optimizer.py. Zero LLM cost, zero
network: the curation is fixed and only the book math varies, so every number here is reproducible
by re-running the sweep.

Borrowed from PWR's sweep dashboard: the return-vs-drawdown frontier and a recommended-settings
table. The per-knob marginal panels and the two-knob heatmap were DROPPED 2026-08-21 along with the
max_events risk/cost pair and the judge audit -- the recommendation no longer comes from reading one
knob at a time, so panels showing knobs in isolation invited a way of choosing the page no longer
supports.

THE HEADLINE MEASURE IS THE REGION (panel 10): a config scored on its own one-knob neighbourhood
rather than on its single cell, over the scored metrics turned into percentile ranks. Cancellation is one
of the ten, not the headline it used to be -- it is a RATIO and blind to magnitude, so it never
rewarded picking well, only not losing.

    python scripts/build_sbt_dashboard.py                     # canonical sweep -> docs/sbt.html
    python scripts/build_sbt_dashboard.py --sweep data/sweep_v7.json \
        --out docs_preview/sbt_v7.html                        # any other sweep: NOT to docs/
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics as _stats
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import dash_nav  # noqa: E402
import provenance as _canon  # noqa: E402  canonical-inputs gate
from build_fbt_dashboard import (CSS, DARK, LIGHT, PLOTLY_CDN, PROFILE_URL, STATUS,  # noqa: E402
                                 _LINK, esc, panel, table_html, tile)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep", default=_canon.CANON_SWEEP)
    ap.add_argument("--out", default="docs/sbt.html")
    a = ap.parse_args(argv)
    S = json.loads((ROOT / a.sweep).read_text())
    # THE GATE, two parts. (1) The sweep records the curation it was computed on, so this checks the
    # BOOK rather than the filename. (2) The GRID must vary book knobs only. A sweep that varies a
    # CURATION knob is a different thing entirely -- it re-reads the news and produces one new
    # curation per cell -- and its cells are not comparable to the canonical book at all. Publishing
    # one here would put re-curated results on the page that claims to sweep the canonical curation.
    _p = []
    _iv = _canon.check_interpreter()
    if _iv:
        _p.append(_iv)
    _srun = S.get("run", "(unrecorded)")
    if _srun != _canon.CANON_RUN:
        _p.append(f"sweep was computed on {_srun}, canonical curation is {_canon.CANON_RUN}")
    _cur_knobs = sorted(set(S.get("grid") or {}) & _canon.CURATION_KNOBS)
    if _cur_knobs:
        _p.append(f"grid varies CURATION knobs {_cur_knobs}, which re-read the news -- every cell is "
                  f"its own curation, so these are not sweeps of {_canon.CANON_RUN}")
    _canon.require_publishable(a.out, "SBT", _p)
    cells = [c for c in S["cells"] if c.get("cancelled") is not None]
    keys = list(S["grid"])

    # ---- PCR: PER-CURATION RETURN (user's call, 2026-08-28) -------------------------------------
    # The book's percent change from one curation to the next -- CBT panel 2, one number per cell.
    # DERIVED HERE rather than in sweep_optimizer.metrics, because `daily_r` is already stored and
    # the scan dates are in the run dir: computing it at render time costs a second and works on
    # every sweep already on disk, where adding it upstream would mean re-running a 7,200-cell
    # sweep to see one column.
    #
    # SHOWN, NOT SCORED, and the measurement is the reason. Tested 2026-08-28 against the same bar
    # that rejected `edge` and `slope_2h` -- does adding it enrich the top-100 regions, and does
    # that reproduce on a second curation? Change in top-100 region median `final`:
    #                       v21        mb2rep      bw21
    #   PCR median       +2,680      -19,341     +2,345    FLIPS SIGN
    #   PCR mean         +8,406      +32,183     +2,697    consistent, but rho 0.90-0.98 with `ann`
    #   PCR mean/SE      -9,166      -12,054     +3,043    FLIPS SIGN; rho +0.97 with `sharpe`
    # The median flips sign, the mean re-admits `ann` (cut 2026-08-21 as return counted twice), and
    # the t-statistic IS Sharpe at a coarser sampling frequency -- the sweep's Sharpe is built from
    # DAILY returns, so mean-over-noise at the rebalance frequency is the same ratio resampled.
    # Cross-curation region-rank transfer puts PCR-median at rho +0.29 against `pc_fund_med` +0.65
    # and `sharpe` +0.58. So it earns a column, not a vote.
    #
    # CLAMP to the last book day <= the scan date, never an exact lookup: 12 of the 37 scan dates on
    # the canonical run are weekends, and indexOf-style matching would silently drop a third of the
    # periods -- the same bug class CBT panel 2 documents.
    _pcr_run = ROOT / _srun
    _pcr_idx: list[int] = []
    try:
        import pandas as _pd
        _n_daily = max((len(c.get("daily_r") or []) for c in cells), default=0)
        if _n_daily:
            _pan = _pd.read_csv(_pcr_run / "panel.csv", index_col=0)
            _bdates = [str(x) for x in _pan.index[-(_n_daily + 1):]]
            _wk = sorted(set(_pd.read_csv(_pcr_run / "firehose_scans.csv")["week"].astype(str)))
            for _w in _wk:
                _i = next((i for i in range(len(_bdates) - 1, -1, -1) if _bdates[i] <= _w), -1)
                if _i >= 0 and _i not in _pcr_idx:
                    _pcr_idx.append(_i)
            _pcr_idx.sort()
    except Exception as _e:                      # a sweep whose run dir is gone still builds
        print(f"  PCR unavailable ({_e.__class__.__name__}: {_e}) -- the column will read em-dash")
    _pcr_n = 0
    for _c in cells:
        _dr = _c.get("daily_r")
        if not _dr or len(_pcr_idx) < 3:
            continue
        _v = [1.0]
        for _r in _dr:
            _v.append(_v[-1] * (1.0 + _r))
        _pts = [_v[_b] / _v[_a] - 1.0 for _a, _b in zip(_pcr_idx, _pcr_idx[1:])
                if _b < len(_v) and _v[_a] > 0]
        if len(_pts) >= 3:
            _c["pcr"] = 100.0 * statistics.median(_pts)
            _pcr_n += 1
    if _pcr_n:
        print(f"  PCR: {_pcr_n:,} of {len(cells):,} cells over {len(_pcr_idx) - 1} curation periods")
    # `base` = where the LIVE profile sits in the grid (the star in panels 2-9, the "current" row).
    # Read it from the profile at BUILD time, not from S["base"], which froze when the sweep ran. The
    # cells never change when a knob moves -- only which one is "current" -- so a profile edit should
    # cost a 2-second rebuild, not a re-sweep. Falls back to the stored base for any key the profile
    # no longer carries (e.g. cull_rank, which now defaults rather than being listed).
    import optimizer as _opt
    _fm = _opt.load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    base = {k: (_fm.get(k) if _fm.get(k) is not None else S["base"].get(k)) for k in keys}

    def is_base(c):
        return all(c[k] == base[k] for k in keys)
    cur = next((c for c in cells if is_base(c)), None)
    by_canc = sorted(cells, key=lambda c: c["cancelled"])
    by_ret = sorted(cells, key=lambda c: -c["final"])

    # THE max_events SERIES (panel 12), if it has been collected. Optional on purpose: it is the one
    # thing on this page that is NOT free -- max_events is a CURATION knob, so each point cost a full
    # re-curation (~$3-4.50, ~45 min) rather than a replay of fixed book math. Absent -> panel omitted.
    _mb = ROOT / "data/sweep_min_bundle.json"
    mb = json.loads(_mb.read_text()) if _mb.exists() else None
    _me = ROOT / "data/sweep_max_events.json"
    me = json.loads(_me.read_text()) if _me.exists() else None
    # CADENCE ARMS. rebalance_period is a CURATION knob -- it sets the scan anchors -- so unlike the
    # 7,200-cell grid above, each arm is a whole separate curation with its own LLM bill. The three
    # sweeps are the SAME book-knob grid replayed over three different journals, which is what makes
    # them comparable cell-for-cell. Absent -> panel omitted, exactly like max_events and min_bundle.
    _LIVE_KEYS = ("max_watchlist", "concentration_cap", "lookback_period_days",
                  "drop_unfunded_weeks", "risk_aversion", "min_trade_size")
    # THE MONTHLY ARM IS THE CANONICAL ONE, so it derives from provenance rather than naming a
    # path. Hard-coding it here (as this did until 2026-08-29) meant promoting CANON_SWEEP updated
    # panels 2-19 while THIS panel silently kept reading the old sweep -- one page describing two
    # books, which is the failure CLAUDE.md's "do NOT hard-code a run or corpus path in a builder
    # again" rule exists to prevent. The other two arms are non-canonical by construction and stay
    # explicit.
    _ARM_SWEEPS = (("monthly",  _canon.CANON_SWEEP, Path(_canon.CANON_RUN).name),
                   ("biweekly", "data/sweep_bw23.json", "cbt_3yr_bw21"),
                   ("weekly",   "data/sweep_wk23.json", "cbt_3yr_wk14"))
    arms = []
    for _nm, _sw, _run in _ARM_SWEEPS:
        _f = ROOT / _sw
        if not _f.exists():
            continue
        _c = json.loads(_f.read_text())
        _c = _c if isinstance(_c, list) else (_c.get("cells") or _c.get("rows") or [])
        # THE LIVE CONFIG'S CELL, not a summary of the grid. "The portfolio value this arm
        # generates" is the book it actually produces at the settings we run, and every arm's sweep
        # contains that exact cell -- the grid is the same six knobs in all three. Cross-check: the
        # monthly arm's live cell equals CBT's book AT THE LAST REBALANCE -- NOT the CBT page's
        # headline, which runs to the last priced day and is a few weeks further on (429,309.49 vs
        # 440,697.17 on 2026-08-29, 13 trading days apart). The sweep stores `final`, which the
        # rebalance loop stops computing at the final scan. Comparing against the headline is how
        # this cross-check read as broken for months. No figure is written down here any more: the
        # $272,233 that used to be was three profile changes out of date.
        _LIVE = _LIVE_KEYS
        _want = {k: _fm.get(k) for k in _LIVE}
        _hit = [x for x in _c
                if all(abs(float(x[k]) - float(_want[k])) < 1e-9 for k in _LIVE if _want[k] is not None)]
        if not _hit:
            continue
        _cell = _hit[0]
        # THE LIVE REGION, not the live cell alone: the cell plus every ONE-KNOB neighbour, the same
        # neighbourhood panel 10 ranks on. A single cell is one book and one lucky name; a region
        # median only moves if the settings AROUND it work too.
        _by = {tuple(x[k] for k in _LIVE): x for x in _c}
        _grid = {k: sorted({x[k] for x in _c}) for k in _LIVE}
        _lt = tuple(_want[k] for k in _LIVE)
        _mem = [_by[_lt]]
        for _i, _k in enumerate(_LIVE):
            for _v in _grid[_k]:
                if _v != _lt[_i] and (_lt[:_i] + (_v,) + _lt[_i + 1:]) in _by:
                    _mem.append(_by[_lt[:_i] + (_v,) + _lt[_i + 1:]])
        _rf = [m["final"] for m in _mem if m.get("final") is not None]
        _rs = [m["sharpe"] for m in _mem if m.get("sharpe") is not None]
        _k = {}
        try:
            _pj = json.loads((ROOT / "data" / _run.split("/")[-1] / "provenance.json").read_text())
            _k = {**(_pj.get("knobs") or {}), **(_pj.get("book_knobs") or {})}
        except Exception:  # noqa: BLE001 -- an unstamped arm still plots, it just says less
            pass
        arms.append({"name": _nm, "run": _run, "sweep": _sw, "n": len(_c), "rn": len(_rf),
                     "final": _stats.median(_rf), "sd": _stats.pstdev(_rf),
                     "cell": _cell.get("final"),
                     "sharpe": _stats.median(_rs) if _rs else None,
                     "period": _k.get("rebalance_period") or _nm,
                     "lookback": (_k.get("news_lookback_days") or 0) or None,
                     "evscans": _k.get("max_event_scans"),
                     "stale": _k.get("max_stale_scans"),
                     "memw": _k.get("curator_memory_weeks")})

    # ---- CROSS-ARM CONSENSUS -------------------------------------------------------------------
    # THE ONE THING THE REGION MACHINERY ABOVE CANNOT DO. A region median is robust to CONFIG noise
    # -- it only moves if the neighbours move too -- but every cell in it replays the SAME journal,
    # so it is not robust to the curation draw at all. That is non-negotiable #6: 7,200 cells are one
    # curation viewed 7,200 ways, and a lucky book lifts every percentile at once.
    #
    # Three arms are three INDEPENDENT curations over the same book-knob grid, so a region's rank can
    # be compared across them. Measured 2026-08-27, Spearman rho of region-median rank between arms:
    #   sharpe  monthly/biweekly +0.576   monthly/weekly +0.420   biweekly/weekly +0.424
    #   final   monthly/biweekly +0.511   monthly/weekly +0.236   biweekly/weekly +0.180
    # Rank transfers -- rho would be ~0 if region quality were curation luck. And it DECAYS WITH
    # CADENCE DISTANCE (adjacent arms agree best), which is the signature of a real cadence effect
    # mixed into the disagreement, so these are a LOWER BOUND on same-cadence transfer.
    #
    # MIN-ARM IS THE CRITERION, not the mean. A region can average well by being excellent in one
    # curation and mediocre in another, which is exactly the over-fit this is meant to exclude. The
    # floor across arms is the number that cannot be bought with one lucky journal.
    CONS = {}
    if len(arms) > 1:
        # SCORED on the pair, SHOWN for all four. Measured 2026-08-27 -- cross-arm Spearman of the
        # composite score, which is the only thing that matters for selection:
        #     sharpe only                        +0.474
        #     sharpe + pc_fund_med               +0.517   <- best, and this is the SBT score
        #     all four (adding slope_2h, final)  +0.428   <- WORSE than sharpe alone
        # Adding the two unstable metrics picks configs that do not hold up in another curation, which
        # is the opposite of the panel's purpose. slope_2h was already rejected once on a re-curation
        # transfer test (v21 +7,346 / v18 -4,845, sign flip) and this is the same verdict by a
        # different method. The result also VALIDATES pc_fund_med, whose transfer test the note in
        # this file records as not done: it lifts agreement 0.474 -> 0.517.
        _cm = ("sharpe", "pc_fund_med")
        _show_only = ("slope_2h", "final")
        _per_arm = {}
        for _a in arms:
            _cc = json.loads((ROOT / _a["sweep"]).read_text())
            _cc = _cc if isinstance(_cc, list) else (_cc.get("cells") or _cc.get("rows") or [])
            _bb = {tuple(x[k] for k in _LIVE_KEYS): x for x in _cc}
            _gg = {k: sorted({x[k] for x in _cc}) for k in _LIVE_KEYS}
            _rs = {}
            for _t in _bb:
                _mm = [_bb[_t]]
                for _i, _k in enumerate(_LIVE_KEYS):
                    for _v in _gg[_k]:
                        _nb = _t[:_i] + (_v,) + _t[_i + 1:]
                        if _v != _t[_i] and _nb in _bb:
                            _mm.append(_bb[_nb])
                if len(_mm) < 5:
                    continue
                _rs[_t] = {_m: _stats.median([c[_m] for c in _mm if c.get(_m) is not None])
                           for _m in (*_cm, *_show_only)
                           if any(c.get(_m) is not None for c in _mm)}
            _per_arm[_a["name"]] = _rs
        _shared = set.intersection(*[set(v) for v in _per_arm.values()])
        # percentile WITHIN each arm, per metric, then the metric-mean -> one score per region per arm
        _sc = {}
        for _nm, _rs in _per_arm.items():
            _pm = {}
            for _m in _cm:
                _ord = sorted((t for t in _shared if _m in _rs[t]), key=lambda t: _rs[t][_m])
                for _i, _t in enumerate(_ord):
                    _pm.setdefault(_t, []).append(100 * _i / max(len(_ord) - 1, 1))
            _sc[_nm] = {t: _stats.mean(v) for t, v in _pm.items()}
        CONS = {"keys": list(_LIVE_KEYS), "arms": [a["name"] for a in arms],
                "rows": sorted(({"cfg": list(t),
                                 "per": [round(_sc[a["name"]].get(t, 0), 1) for a in arms],
                                 "mean": round(_stats.mean([_sc[a["name"]].get(t, 0) for a in arms]), 1),
                                 "floor": round(min(_sc[a["name"]].get(t, 0) for a in arms), 1)}
                                for t in _shared),
                               key=lambda r: -r["floor"])[:400],
                "live": [float(_fm[k]) for k in _LIVE_KEYS], "metrics": list(_cm),
                "shown": list(_show_only),
                "rho": {"sharpe only": 0.474, "sharpe + pc_fund_med": 0.517,
                        "+ slope_2h + final": 0.428}}
        # THE ACTIONABLE TABLE: what has to change for the live config to reach the green cluster.
        # Sorted by how FEW knobs move, then by worst-arm floor -- a one-knob change that gets there
        # is worth more than a three-knob one that scores marginally higher, because every extra knob
        # is another axis fitted to these three particular curations.
        _lv = next((r for r in CONS["rows"]
                    if all(abs(a - b) < 1e-9 for a, b in zip(r["cfg"], CONS["live"]))), None)
        CONS["live_floor"] = _lv["floor"] if _lv else 0.0
        # THE NOISE SCALE, and the reason the green cut is gone (2026-08-29). The panel used to
        # highlight the top 12 by worst-arm score and panel 21 told you to chase them. 12 was
        # arbitrary: the cut landed mid-TIE (ranks 11, 12 and 13 all floored at 86.3, rank 14 at
        # 86.2) on a distribution with no gap anywhere -- 89.9 at rank 1, 79.7 at rank 101. And
        # `cut` was DEFINED as rows[11]["floor"], so panel 21 quoted as a threshold the very number
        # its own slice produced.
        # Measured instead: a region's three arm scores spread by a median of 16.2 points (stdev
        # 8.7), and the LIVE config's own spread is 17.7 (monthly 99.2, biweekly 81.5, weekly 85.9).
        # The best floor in the grid beats live by 8.4. So ZERO of 400 regions clear the live config
        # by more than the arm-to-arm noise of a single config, and the whole "green cluster" was a
        # ranking of differences smaller than the measurement error.
        _per_sd = [_stats.stdev(r["per"]) for r in CONS["rows"]] or [0.0]
        CONS["noise"] = round(_stats.median(_per_sd), 1)
        CONS["n_clear"] = sum(1 for r in CONS["rows"]
                              if r["floor"] > CONS["live_floor"] + CONS["noise"])
        CONS["live_per"] = _lv["per"] if _lv else [0.0]
        CONS["cut"] = CONS["rows"][11]["floor"] if len(CONS["rows"]) > 11 else 0.0
        _mv = []
        for _r in CONS["rows"][:12]:
            # a real arrow, NOT "&rarr;": table_html escapes its cells, so an entity renders as
            # literal text -- the same trap the curation-log colours hit on 2026-08-26.
            _d = [f"{_LIVE_KEYS[_i].replace('_', ' ')} {CONS['live'][_i]:g} \u2192 {_r['cfg'][_i]:g}"
                  for _i in range(len(_LIVE_KEYS))
                  if abs(_r["cfg"][_i] - CONS["live"][_i]) > 1e-9]
            _mv.append((len(_d), [str(len(_d)), ", ".join(_d), f"{_r['floor']:.1f}",
                                  f"{_r['mean']:.1f}"] + [f"{x:.0f}" for x in _r["per"]]))
        CONS["moves"] = [r for _n, r in sorted(_mv, key=lambda t: (t[0], -float(t[1][2])))]

    # ORDER THE SERIES ONCE, HERE. max_events=0 means "uncapped", i.e. the LIMIT of the series, so it
    # belongs at the right-hand end -- sorting numerically puts it at the left where it reads as the
    # smallest cap, the exact opposite of what it is. Done at load so panel 1's table and panels 11-12
    # cannot disagree: they did, the table showing 4..20,uncapped and the plots uncapped,4..20.
    if me and me.get("rows"):
        me["rows"] = sorted(me["rows"], key=lambda r: (r["max_events"] == 0, r["max_events"]))

    payload = {"cells": cells, "keys": keys, "cur": cur, "me": me, "mb": mb}

    # What was swept, and what the profile currently says -- PWR's "Parameter settings" panel. The
    # `current` column is what makes it readable: without it the grid is a list of numbers with no
    # indication of where we actually stand in it.
    # DISPLAY the canonical knob name. The sweep grid is keyed on `lookback_period_days`, which is a
    # LEGACY ALIAS that load_financial_model keeps in sync with `optimizer_lookback_days`; showing the
    # alias made this table name a knob the profile no longer uses. Renaming the grid key itself would
    # invalidate every stored sweep, so the substitution is display-only.
    _CANON = {"lookback_period_days": "optimizer_lookback_days"}
    ps_rows = [[_CANON.get(k, k), ", ".join(str(v) for v in S["grid"][k]), str(base[k]),
                "free — book replay"] for k in keys]
    # max_events belongs in this table -- it IS swept on this page (panels 11-12) -- but it is swept
    # on completely different terms and listing it beside the six without saying so would be the
    # misleading part. The six are FREE: they re-weight a fixed curation, so 6,300 cells cost nothing.
    # max_events is a CURATION knob, so each value needed its own re-curation and its own LLM bill.
    # Hence the fourth column: it exists to keep that distinction on the page rather than in a commit
    # message. Only shown once the series has actually been collected.
    if me and me.get("rows"):
        _mer = me["rows"]                      # already in canonical order (see above)
        _cost = sum(r.get("cost_usd") or 0 for r in _mer)
        ps_rows.append([
            "max_events",
            ", ".join("uncapped" if r["max_events"] == 0 else str(r["max_events"]) for r in _mer),
            ("0 = uncapped" if not base.get("max_events") else str(base.get("max_events")))
            if "max_events" in base else
            ("0 = uncapped" if not _fm.get("max_events") else str(_fm.get("max_events"))),
            f"${_cost:.2f} — {len(_mer)} re-curations"])
    # min_bundle_articles, same treatment and for the same reason: a CURATION knob, one re-curation
    # per value, so it does not belong beside the six free ones without the cost column saying so.
    if mb and mb.get("rows"):
        _mbr = mb["rows"]
        ps_rows.append([
            "min_bundle_articles",
            ", ".join(str(r["min_bundle_articles"]) for r in _mbr),
            str(_fm.get("min_bundle_articles", 1)),
            f"$20.23 — {len(_mbr)} re-curations"])
    # THE MODEL SWEEP (panels 14-18). Listed here because a reader looking for "what was varied"
    # looks at this table, and the eight-arm bake-off is otherwise invisible until panel 14.
    #
    # IT WAS event_agent_model THAT MOVED, NOT scout_model. The scout was held FIXED at llama4 in all
    # eight arms -- every arm read the same 1,248 scout chunks off the same corpus -- and that is
    # exactly what makes the comparison controlled: any difference downstream is attributable to the
    # judgment stage. Both rows are shown so the held-fixed one is as visible as the varied one.
    _bof = ROOT / "data/bakeoff_summary.json"
    if _bof.exists():
        _bo = sorted(json.loads(_bof.read_text()), key=lambda r: r["cost"])
        ps_rows.append([
            "event_agent_model",
            ", ".join(r["disp"].replace("<br>", " ") for _bo_i, r in enumerate(_bo)),
            str(_fm.get("event_agent_model")),
            f"${sum(r['cost'] for r in _bo):.2f} — {len(_bo)} re-curations"])
        ps_rows.append([
            "scout_model",
            "not swept — held fixed in all 8 arms, which is what makes them comparable",
            str(_fm.get("scout_model")), "—"])
    param_tbl = table_html(["parameter", "values swept", "current (profile)", "cost to sweep"], ps_rows)

    # ---- PLATEAU: the anti-overfit rank, ported from PWR ------------------------------------------
    # A config's score is half its own cancellation and half the mean of its GRID NEIGHBOURS -- every
    # cell one step away on exactly one axis. A lone in-sample spike (great cell, poor surroundings)
    # sinks below a broad shallow region, which is the point: a robust neighbourhood is likelier to
    # hold FORWARD than a fragile peak, and with 5,760 cells on ONE 3-year path the best raw cell is
    # very likely noise. PWR plateaus over IR; we plateau over CANCELLATION because that is the
    # objective here -- the mechanism is what ports, not the metric.
    idx = {tuple(c[k] for k in keys): c for c in cells}
    pos = {k: {v: i for i, v in enumerate(S["grid"][k])} for k in keys}
    for c in cells:
        key = tuple(c[k] for k in keys)
        nb = []
        for ki, k in enumerate(keys):
            i0 = pos[k][c[k]]
            for step in (-1, 1):
                if 0 <= i0 + step < len(S["grid"][k]):
                    alt = list(key)
                    alt[ki] = S["grid"][k][i0 + step]
                    n = idx.get(tuple(alt))
                    if n:
                        nb.append(n["cancelled"])
        c["plateau"] = round(0.5 * c["cancelled"] + 0.5 * (sum(nb) / len(nb) if nb else c["cancelled"]), 1)

    # ---- ROBUST: the rank table 10 actually sorts on ------------------------------------------------
    # Mean of a config's CANCELLATION rank and its DRAWDOWN rank, both 0 (best) .. 1 (worst), taken
    # over every cell in the sweep. Ranks rather than raw values, because the two are on unrelated
    # scales (cancellation runs 4-269%, drawdown 0-100%) and averaging them directly would let
    # cancellation set the whole score.
    #
    # CHOSEN BY MEASUREMENT, 2026-08-17, not by argument. The only clean test available is the
    # noise-experiment pair -- data/sweep_me16.json and data/sweep_rep.json, the SAME settings curated
    # twice, differing only in LLM sampling. Rank all 6,300 configs on curation A, then look at where
    # that top 50 actually lands on curation B. Percentile of B's final value, and B's median final:
    #
    #     rank(canc)+rank(DD)   86th   $189,137   <- this
    #     + rank(sharpe)        85th   $160,259
    #     plateau(cancellation) 83rd   $156,393   <- what table 10 used before
    #     drawdown alone        67th   $ 94,531
    #     SHARPE                54th   $ 64,075   <- a coin flip
    #     slope_2h              53rd   $ 75,001
    #     grid median           50th   $ 62,997
    #     final                 43rd   $ 47,322   <- WORSE THAN RANDOM
    #     annualized return     41st   $ 44,803
    #     gain_pain             41st   $ 49,487
    #
    # Ranking by P&L -- final, annualized, gain-to-pain -- puts its winners BELOW the median on the
    # re-curation. That is non-negotiable #6 expressed as a number: those metrics select one
    # curation's luck. Sharpe carries essentially nothing across a re-run, which is why adding it to
    # this composite makes it slightly worse rather than better. Do not re-add it without a new
    # transfer test.
    #
    # PLATEAU SMOOTHING IS NOT WHAT WAS DOING THE WORK. Varying the self/neighbour weight on the
    # cancellation plateau moved the result barely at all (w=0.5 -> 83rd, w=0.3 -> 84th, w=0.0, i.e.
    # neighbours only -> 83rd). Cancellation is simply a REPRODUCIBLE metric while P&L is not.
    # `plateau` is therefore kept and still shown as a column, but it no longer sets the order.
    _rank = {}
    for fld in ("cancelled", "max_drawdown"):
        order = sorted(cells, key=lambda c, f=fld: (c.get(f) is None, c.get(f) or 0))
        n = max(len(order) - 1, 1)
        for i, c in enumerate(order):
            _rank.setdefault(id(c), []).append(i / n)
    for c in cells:
        r = _rank.get(id(c)) or [1.0, 1.0]
        c["robust"] = round(100 * sum(r) / len(r), 1)

    # 50% squeeze / 30% squeeze, by column name (position-independent, so a grid change cannot
    # silently point these at the wrong column).
    TIER_A = {"concentration_cap", "lookback_period_days", "drop_unfunded_weeks"}
    TIER_B = {"max_watchlist", "risk_aversion", "min_trade_size"}

    def _cls(i, headers):
        h = headers[i]
        if h in TIER_A:
            return ' class="k kA"'
        if h in TIER_B:
            return ' class="k kB"'
        return ""

    def _compact(v):
        """0.25 -> .25, 3.0 -> 3 : same number, fewer glyphs. A cell cannot render narrower than its
        own text, so this is what makes the 50% tier reachable at all."""
        t = str(v)
        if t.startswith("0.") and len(t) > 2:
            t = t[1:]
        if t.endswith(".0"):
            t = t[:-2]
        return t

    def _rot_table(headers, rows):
        """Same as table_html, but each header sits in a span the CSS can rotate.

        17 columns of 1-3 characters under headers like `concentration_cap` means the HEADER sets the
        column width and the table runs off the page. Rotating the labels ~30 degrees lets each column
        shrink to its DATA width, which is what fits the whole grid on one screen."""
        def _lab(x):
            # Break the long knob names on their underscores. Horizontal headers stacked over 2-3
            # short lines take LESS width than the rotated version did, and stay readable straight on.
            return ("<br>".join(esc(t) for t in x.split("_"))
                    if x in TIER_A | TIER_B else esc(x))
        h = "".join(f"<th{_cls(i, headers)}><span>{_lab(x)}</span></th>"
                    for i, x in enumerate(headers))
        b = "".join("<tr>" + "".join(
            f"<td{_cls(i, headers)}>{esc(_compact(c) if headers[i] in TIER_A | TIER_B else c)}</td>"
            for i, c in enumerate(r)) + "</tr>" for r in rows)
        return f'<table class="rot"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'

    def _f(x, s="", d=2):
        return "—" if x is None else (f"{x:.{d}f}{s}")

    # ---- SHORTLIST: read the gates straight off panels 2-7 ---------------------------------------
    # Panels 2-5 plot annualized return against drawdown, L1, L2 and cancellation. Rather than eyeball
    # a "good corner" in four separate clouds, cut all four at once and let what survives be the
    # candidate set. These are ABSOLUTE bars, deliberately: a percentile gate would always pass the
    # same fraction no matter how bad the book, which is how a weak sweep flatters itself.
    # NO CHURN GATE. Both L1<850 and L2<700 were excluding the best region of the grid: median Sharpe
    # rises monotonically with churn here (0.73 at L2<500 -> 1.17 at L2>900), so a churn ceiling was
    # selecting for not trading. Trading is free in an IRA, so churn is only ever a robustness PROXY --
    # and what it was proxying for is drawdown, which the upper bands really do carry (median 52-55%).
    # So gate the thing itself, and let churn fall where it may.
    # Re-cut 2026-08-13 for the v11 book. The old DD<40 / canc<45 pair left 17 of 6,300 -- and EXCLUDED
    # the grid's best cell (Sharpe 1.75, $1.35M) on a 51% drawdown. A shortlist that omits the top
    # config is a threshold set on a different book, not a filter. DD and cancellation do the selecting;
    # the Sharpe floor is a sanity check that cannot bind at the top of the list, since the table RANKS
    # by Sharpe -- it only trims a tail the 20-row display already hides.
    # Re-cut for the v14 book. Its cancellation spread is far wider than v13's (18-637% vs 20-309%),
    # so `cancelled` now does most of the selecting -- it alone keeps 586 of 6,300, against L2's 4,611.
    # The churn bars are back, but LOOSE: they exclude the runaway-turnover tail without excluding the
    # profitable high-churn region that the old L1<850 gate was silently cutting out.
    # RE-CUT 2026-08-17 (seventh set, on the v8 book): DD < 65%, L1 > 2000%/yr, L2 750-1350/yr,
    # Sharpe > 0.8, cancelled < 65%. 2,288 of 6,300 survive.
    #
    # READ `cancelled` AS A PERCENT. It is stored 4.1-268.6 (median 58.6), not 0-1, so the requested
    # "cancel < 0.65" is taken as < 65%. Read literally it would admit ZERO of the 6,300 cells, since
    # the lowest cancellation anywhere on the grid is 4.1%.
    #
    # SHARPE AT 0.8 barely separates -- 53.0% of the grid clears it, against 30.2% at the 1.0 bar. It
    # is still the largest single cut here (3,850 -> 2,372); DD < 65% is inert at 90.4%.
    #
    # A RETURN GATE WAS ADDED HERE ON 2026-08-17 AND REVERTED THE SAME DAY. `final > $200K` was
    # proposed to make the shortlist "scale with gains". It should not have been: bootstrapped over
    # 300 resampled config subsets, adding it to the gate set was worth a median +1.0 percentage point
    # in the A->B direction of the transfer test and EXACTLY 0.0 in the B->A direction (P(better) 94%
    # and 0% respectively). A one-way one-point effect is not a reason to change what the page
    # recommends.
    #
    # IT ALSO BROKE THE PAGE'S OWN LOGIC, which is the more important reason it is gone. With the
    # floor in, the cumulative gate counts ran 3,850 -> 706 -> 706 -> 706: the return threshold cut
    # everything and CANCELLATION THEN REMOVED NOTHING AT ALL. Cancellation is this module's stated
    # headline measure and the objective the sweep exists to serve, so a shortlist where it no longer
    # constrains anything -- and a P&L threshold fitted to THIS curation does all the selecting -- is
    # the failure non-negotiable #6 describes, not a refinement of it.
    #
    # THE UNDERLYING FINDING STILL STANDS and is what `robust` rests on: ranking by cancellation and
    # drawdown transfers across a re-curation (86th/68th percentile) while ranking by Sharpe (54th),
    # slope (53rd) or final value (43rd, worse than random) does not. That gap is large and shows in
    # both directions. The micro-differences BETWEEN good options -- gate vs no gate at +1/0, plateau
    # vs robust at +3/0 -- are not, and should not be treated as decisions.
    #
    # THE 'NEVER GAINED OR LOST' DEGENERATE CASE DOES NOT ARISE, which is why the score needs no
    # return term to defend against it. Both of robust's inputs are SCALE-FREE RATIOS: cancellation is
    # |losses| / gains, so a book making $1K and losing $200 scores the same as one making $500K and
    # losing $100K, and sitting still does not lower it (with no gains it is undefined and the cell is
    # dropped). Measured on the top 100 by robust with NO gates applied: median final $199,584 against
    # a grid median of $103,541, median annualized 83% against 43%, median L1 2,195 -- fully trading.
    # One cell of 100 finished under $100K, and NO cell anywhere in the 6,300 has L1 < 50%/yr.
    #
    # THE LIVE CONFIG [8, 0.25, 21, 0, 4.00, 0.10] PASSES ALL SIX: DD 31.1, L1 2230, L2 806,
    # Sharpe 1.80, cancellation 35.6, final $302,079.
    GATES = [("max DD", "max_drawdown", lambda v: v < 65, "&lt; 65%"),
             ("L1", "l1", lambda v: v > 2000, "&gt; 2000%/yr"),
             ("L2", "l2", lambda v: 750 < v < 1350, "750&ndash;1350/yr"),
             ("Sharpe", "sharpe", lambda v: v > 0.8, "&gt; 0.8"),
             ("cancelled", "cancelled", lambda v: v < 65, "&lt; 65%")]
    _pos = {id(c): i for i, c in enumerate(cells)}
    # ---- THE REGION: ONE CURATION, LOCAL NEIGHBOURHOODS ------------------------------------------
    # WHAT CHANGED AND WHY (2026-08-21). This used to pool 15 sweeps across as many curations, on the
    # argument that averaging over draws of the news beats trusting one. The premise did not survive
    # being checked. Those curations are not repeat draws of one setup: the text they read ranges
    # from 2.5% to 56.9% clean (wayback) lede, and one fed the curator 46.7% bare headlines. Four
    # read a different article pool entirely and one varied
    # max_events. Three of the "15" were the same curation swept twice, which inflated n and shrank
    # the standard error the region width is built from. Averaging over that is averaging over
    # RETRIEVAL REGIMES, not over curation noise.
    #
    # So the population is now ONE curation -- the canonical one, which read the best text of any of
    # them -- and the noise control moves inside the grid instead. A config's REGION is itself plus
    # every config differing in exactly ONE knob: 1 + 3+2+6+2+4+4 = 22 cells. The luckiest and
    # unluckiest member BY FINAL VALUE are dropped, and each metric is summarised as the median and
    # standard error of the mean over the surviving 20.
    #
    # This is a different defence against the same error, and it is the one that fits a single
    # history. Ranking cells picks the cell that best fits this history's accidents. Ranking
    # NEIGHBOURHOODS cannot: a knife-edge cell whose neighbours are bad scores badly, because its
    # neighbours are in its own score. A config only ranks well if the settings AROUND it also work,
    # which is the property you actually want when the number will be run forward on new news.
    #
    # RANKING is the mean of per-metric percentile ranks across all 6,300 regions, each metric
    # oriented so higher is better. Percentiles because the metrics have incomparable units; the mean
    # because no weighting is defensible without evidence for one. Ranking instead on each metric's
    # conservative (median - 1 SE) bound picks the SAME top config and shares 18 of its top 20, so the
    # simpler form is kept and the SE is shown rather than folded in.
    import math as _math
    _pos = {id(c): i for i, c in enumerate(cells)}
    _by = {tuple(c[k] for k in keys): c for c in cells}
    # metric -> +1 higher is better, -1 lower is better
    # SCORED SET, cut from ten to seven on 2026-08-21. `final` and `ann` are gone because they are the
    # SAME AXIS counted twice more: across the 6,300 config means annualized correlates +0.93 with
    # final value, so keeping both silently tripled the weight on return and let a lucky book carry a
    # region. `worst_behind` is gone as the weakest of the risk measures and the one most driven by a
    # single bad stretch. What remains is one return-shape measure (slope), two risk-adjusted ones
    # (sharpe, gain_pain), two picking measures (capital_hit, edge) and two give-back measures
    # (cancelled, max_drawdown). final and ann are still COLUMNS -- they are what a reader wants to
    # see -- they just no longer vote.
    # SAFE_PARK DEMOTED TO DISPLAY-ONLY (2026-08-22). It is `anchor_capital_days /
    # (anchor_cd + loser_cd)` -- the share of non-winning capital sitting in SPY/BIL rather than in a
    # losing pick. Raising risk_aversion pushes the optimizer into the anchors, which raises the
    # NUMERATOR BY CONSTRUCTION, so the metric pays a book for not investing. It is the only member
    # of the set that improves by doing nothing.
    # MEASURED at canonical 6 . 0.25 . 21 . 0 . _ . 0.2: past the `final` peak at ra=12, taking
    # aversion to 80 HALVES the book ($285,755 -> $139,305) while safe_park runs 32.3 -> 72.5 -- a
    # 40-point move, far larger than any other metric's, and in the wrong direction. max_drawdown
    # (23.0 -> 14.6) and cancelled (30.1 -> 23.9) tilt the same way for the same reason (a book in
    # T-bills neither draws down nor gives back), so three of eight were voting for idleness against
    # only edge, capital_hit and slope_2h pushing the other way.
    # INVISIBLE BEFORE THE GRID WAS EXTENDED: on the old sweep (risk_aversion stopped at 4.0) every
    # metric correlated with ra at |r| <= 0.12. The tilt only bites in the range 6.0->24 opened up
    # on 2026-08-22, so extending the grid did not merely move the optimum, it exposed this.
    # Same treatment as `final`/`ann`: still summarised per region and still a column, just no vote.
    # SHARPE ONLY (2026-08-22). Every other metric was TESTED as an addition to Sharpe and none
    # earned a place. Criterion: does adding it make the top-100 regions richer, and does that
    # reproduce on a SECOND curation? Change in top-100 median region-final vs Sharpe alone:
    #                    v21 (31,500)   v18 (6,300)
    #   edge                  +9,004        -5,066   FLIPS SIGN (and r=0.94 with sharpe anyway)
    #   slope_2h              +7,346        -4,845   FLIPS SIGN (r=0.83)
    #   gain_pain             -5,484        -9,463   degrades on both (r=0.99 -- it IS sharpe)
    #   cancelled            -55,666        -6,160   degrades on both
    #   capital_hit          -55,688       -14,389   degrades on both
    #   max_drawdown         -74,655       -12,869   degrades on both
    #   safe_park            -80,137       -16,815   degrades on both
    # So the elaborate score was not adding judgement, it was adding drift: five metrics reliably
    # pulled the ranking toward poorer regions, and the two that looked useful on one curation
    # reversed on the other. Sharpe already embeds risk, which is why a separate drawdown or
    # cancellation term subtracts rather than adds.
    # NOTE the tilt this does NOT fix: Sharpe alone still selects risk_aversion 16-24. Rewarding low
    # volatility favours a timid book by construction; metric-pruning cannot remove that, only a cap
    # on lambda or scoring on raw return would.
    # pc_fund_med ADDED TO THE SCORE 2026-08-24, by the user's call. What it does to the ranking was
    # measured on the 31,500-cell grid before the change: the top region moves from risk_aversion 16
    # to 8 while 10 of the top 20 regions stay put, so it nudges rather than overturns, and the nudge
    # is AWAY from the timidity tilt noted below -- pc_fund_med is not a volatility measure, so it
    # pushes back against the low-vol preference Sharpe cannot escape on its own.
    # TRANSFER-TESTED 2026-08-28, and it PASSES. The test the note here used to call for was run on
    # sweep_bw21 and sweep_mb2rep rather than me16/rep (bw21 already carries the pc_* columns, so no
    # re-sweep was needed). Method: rank regions on ONE curation, then measure Spearman against
    # `final` on ANOTHER, over all ~7,000 COMMON regions -- not a top-K slice, which is far noisier.
    # Four matched pairs, every score facing the same four:
    #                                  v21>bw21  bw21>v21  v21>mb2r  bw21>mb2r    mean
    #   final alone (naive)              +0.511    +0.511    -0.083     +0.381   +0.330
    #   sharpe + pc_fund_med  <- LIVE    +0.442    +0.526    -0.047     +0.227   +0.287
    #   sharpe + pc_fund_med + pcr       +0.463    +0.500    -0.072     +0.159   +0.262
    #   sharpe alone                     +0.394    +0.573    -0.156     +0.177   +0.247
    #   sharpe + pcr                     +0.410    +0.498    -0.129     +0.100   +0.220
    #   pcr alone                        +0.334    +0.335    -0.114     +0.021   +0.144
    # So the live pair beats Sharpe alone (+0.287 vs +0.247), and beats sharpe+pcr on 4 of 4 pairs.
    # `final alone` tops the table, and that row is NOT a recommendation: the outcome metric IS
    # `final`, so a score containing it has home-field advantage. pc_fund_med vs pcr is the clean
    # comparison -- neither is the target -- and pc_fund_med wins it outright.
    # TWO METHOD TRAPS, both hit while running this. Normalise percentiles WITHIN the common region
    # set: against all test regions a RANDOM pick scored 74 instead of 50, because the grids differ
    # (7,200 vs 6,300 regions). And the `v21>mb2rep` column is negative for EVERY score -- that
    # curation anti-transfers with everything, so no single pair should carry a conclusion.
    _MET = {"sharpe": 1, "pc_fund_med": 1}
    # SHOWN BUT NOT SCORED. Summarised per region exactly like the scored ones so the columns and the
    # payload have them, but excluded from the percentile mean -- see the note above.
    _SHOW = ("final", "ann", "safe_park", "gain_pain", "slope_2h", "capital_hit",
             "edge", "cancelled", "max_drawdown",
             "pcr",          # per-curation return -- see the PCR note above for why it does not vote
             )
    # pc_watch_med and pc_gap are still computed by the sweep and read by scripts/null_pc_gap.py;
    # they are simply not displayed and not scored.

    def _neigh(t):
        yield t
        for i, k in enumerate(keys):
            for v in S["grid"][k]:
                if v != t[i]:
                    yield t[:i] + (v,) + t[i + 1:]

    _reg_stat, _reg_mem = {}, {}
    for _t in _by:
        _mem = [_by[n] for n in _neigh(_t) if n in _by]
        _mem = [c for c in _mem if c.get("final") is not None]
        if len(_mem) < 5:
            continue
        _reg_mem[_t] = _mem
        # NO TRIM (2026-08-22). The region statistic is a MEDIAN, and a median already ignores the
        # extremes -- that is what it is for. Dropping the luckiest and unluckiest member before
        # taking one was advertised as protecting the medians and measured as doing nothing: across
        # all 6,300 regions the median moved by EXACTLY 0 at every trim level from +-0 to +-3, and
        # the rankings correlate 0.998-0.9995 with each other.
        # What the trim DID change was the error bar, in the wrong direction. Taking a standard
        # deviation over a deliberately truncated sample shrinks it mechanically -- median SE(final)
        # ran 9,781 at +-0, 7,459 at +-1, 5,228 at +-3 -- so trimming made the +- column look
        # tighter without the underlying uncertainty changing at all. Since the panel tells readers
        # to trust the errors over the ordering, an artificially narrow error bar is the worst
        # failure available here. The whole neighbourhood is used.
        _keep = _mem
        _st = {}
        for _m in (*_MET, *_SHOW):
            _v = [c[_m] for c in _keep if c.get(_m) is not None]
            if len(_v) > 1:
                _st[_m] = (statistics.median(_v),
                           statistics.stdev(_v) / _math.sqrt(len(_v)))
        _st["_n"] = len(_keep)
        _reg_stat[_t] = _st

    import bisect as _bis
    # THE SCORE IS THE REGION'S WORST MEMBER (user's call, 2026-08-29). Previously each metric was
    # MEDIANED over the region's members, percentile-ranked across regions, and the two ranks
    # averaged. That orders the whole grid slightly better but its top slice still admits knife-edge
    # peaks: a cell can hold a high median while one neighbour is terrible. Scoring a region by its
    # WORST member is the criterion the panel actually wants -- a config only ranks high if every
    # one-knob neighbour also works, which is the risk you take by running one config forward.
    # MEASURED across all three re-swept arms, ranking on one curation and reading `final` on
    # another (median test-final percentile of the selected configs, 50 = random):
    #      top-K       4     8    16    32    64   128   256
    #      median   52.0  53.0  54.3  56.9  55.5  58.0  59.3
    #      worst    55.8  67.4  68.8  70.6  71.8  70.5  67.3
    # The worst-member score wins at EVERY cut size, by 12-16 points. Two honest qualifications
    # kept here so the next reader does not overstate it: it wins on the MEAN, not per pair (3-4 of
    # 6), and over the WHOLE grid the old median score orders slightly better (+0.315 vs +0.273
    # Spearman) -- `min` is a worse gradient and a better selector, which is exactly the trade
    # wanted when only the top matters.
    # The old score is kept and displayed beside it, so this change is auditable rather than silent.
    # REGION SIZE: every region here has exactly 22 members (the grid is complete), so `min` is not
    # biased toward small neighbourhoods. It WOULD be if the grid ever went ragged -- fewer draws,
    # higher expected minimum -- so the count is asserted rather than assumed.
    _cell_pct: dict = {}
    for _m, _sgn in _MET.items():
        _vals = sorted(c[_m] for c in cells if c.get(_m) is not None)
        for _c in cells:
            if _c.get(_m) is None:
                continue
            _p = 100 * _bis.bisect_left(_vals, _c[_m]) / max(len(_vals) - 1, 1)
            _cell_pct.setdefault(id(_c), []).append(_p if _sgn > 0 else 100 - _p)
    _cell_score = {k: statistics.mean(v) for k, v in _cell_pct.items() if len(v) == len(_MET)}
    _score, _score_med, _nsz = {}, {}, set()
    for _t, _mem in _reg_mem.items():
        _sc = [_cell_score[id(c)] for c in _mem if id(c) in _cell_score]
        if _sc:
            _score[_t] = min(_sc)
            _nsz.add(len(_mem))
    # the OLD median-based score, retained as a displayed column
    for _m, _sgn in _MET.items():
        _sorted = sorted(r[_m][0] for r in _reg_stat.values() if _m in r)
        for _t, r in _reg_stat.items():
            if _m not in r:
                continue
            _p = 100 * _bis.bisect_left(_sorted, r[_m][0]) / len(_sorted)
            _score_med.setdefault(_t, []).append(_p if _sgn > 0 else 100 - _p)
    _score_med = {t: statistics.mean(v) for t, v in _score_med.items()}
    _score = {t: v for t, v in _score.items() if t in _reg_stat}
    print(f"  region score = WORST member; region sizes present: {sorted(_nsz)}"
          + ("  <-- RAGGED GRID: min favours the small ones" if len(_nsz) > 1 else ""))
    _rank = sorted(_score, key=lambda t: -_score[t])
    _best = _rank[0]
    _live = tuple(base[k] for k in keys)

    def _pm(t, m, fmt, scale=1.0):
        r = _reg_stat[t].get(m)
        if not r:
            return "\u2014"
        return f"{fmt.format(r[0] * scale)} \u00b1 {fmt.format(r[1] * scale)}"

    def _rrow(t, tag):
        return [tag + " · ".join(str(x) for x in t),
                f"{_score[t]:.1f}",
                f"{_score_med.get(t, 0):.1f}",
                _pm(t, "final", "{:,.0f}"),
                _pm(t, "ann", "{:.0f}") + "%",
                _pm(t, "sharpe", "{:.2f}"),
                _pm(t, "slope_2h", "{:,.0f}"),
                _pm(t, "cancelled", "{:.0f}") + "%",
                _pm(t, "max_drawdown", "{:.0f}") + "%",
                _pm(t, "gain_pain", "{:.2f}"),
                _pm(t, "capital_hit", "{:.0f}") + "%",
                _pm(t, "edge", "{:,.0f}"),
                _pm(t, "safe_park", "{:.0f}") + "%",
                _pm(t, "pc_fund_med", "{:.2f}") + "%",
                _pm(t, "pcr", "{:.2f}") + "%"]

    _TOPR = 16
    _shown = _rank[:_TOPR]
    _rows = [_rrow(t, "★ " if t == _live else "") for t in _shown]
    _cls = ["reg"] * len(_shown)
    if _live in _reg_stat and _live not in _shown:      # always show where the LIVE config lands
        _rows.append(_rrow(_live, "★ "))
        _cls.append("mid")

    def _ctable(headers, rows, cls):
        """table_html plus a per-row class, so the winning band and the live row are visible without
        a column that just repeats what the banding already says."""
        h = "".join(f"<th>{x}</th>" for x in headers)
        b = "".join(f'<tr class="{c}">' + "".join(f"<td>{esc(x)}</td>" for x in r) + "</tr>"
                    for r, c in zip(rows, cls))
        return f'<table class="cfg"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'

    reg_tbl = _ctable(
        ["config &mdash; watch &middot; cap &middot; lookback &middot; drop &middot; risk "
         "&middot; trade", "score (worst member)", "old score (median)", "final (median &plusmn; SE)", "annualized", "sharpe",
         "2nd-half slope $/yr", "cancelled", "max DD",
         "gain/pain", "capital hit-rate", "edge $/exposure", "safe-park %",
         "pc-funded", "PCR"], _rows, _cls)

    _live_rank = (_rank.index(_live) + 1) if _live in _reg_stat else None
    _live_str = " \u00b7 ".join(str(x) for x in _live)
    payload["region"] = {
        "best": [str(x) for x in _best], "best_score": round(_score[_best], 1),
        "n_members": len(_reg_mem[_best]), "n_kept": _reg_stat[_best]["_n"],
        "live": [str(x) for x in _live], "live_rank": _live_rank,
        "live_score": round(_score[_live], 1) if _live in _score else None,
        "n_regions": len(_reg_stat), "n_metrics": len(_MET),
        "best_final": round(_reg_stat[_best]["final"][0]),
        "best_final_se": round(_reg_stat[_best]["final"][1]),
        "live_final": round(_reg_stat[_live]["final"][0]) if _live in _reg_stat else None,
        "live_final_se": round(_reg_stat[_live]["final"][1]) if _live in _reg_stat else None}

    # THE SQUARES MARK THE LIVE CONFIG'S REGION, not the top-scoring one (changed 2026-08-21).
    # They used to mark row 1. But the two sweeps on hand disagree about row 1 -- mb2's winner ranks
    # 662nd on v9 and v9's ranks 2,003rd on mb2 -- so the profile is deliberately set to the config
    # that survives BOTH (best combined rank), which is not the in-sample peak. Highlighting the peak
    # while running something else would point every scatter at a config we looked at and declined.
    # All members are drawn. There is no trim any more -- see the note at _keep.
    # TWO MARKED NEIGHBOURHOODS, so the panels can be read as a comparison rather than a lookup:
    # amber = the LIVE config's region, cyan = the BEST-scoring one's. Previously only one set was
    # drawn (live if present, else best), which answered "where am I" but never "where is the thing
    # I would move to, and do the two overlap".
    _liveset = {id(c) for c in _reg_mem.get(_live, [])}
    _bestset = {id(c) for c in _reg_mem.get(_best, [])}
    payload["topn"] = [i for i, c in enumerate(cells) if id(c) in _liveset]
    payload["bestn"] = [i for i, c in enumerate(cells) if id(c) in _bestset]
    payload["bestcfg"] = [float(x) for x in _best]
    # THE TOP-100 REGIONS' CENTRES, for the scatters. The raw cloud shows real dispersion, which is
    # its job -- but it HIDES where the good neighbourhoods are, because they sit at the edge of the
    # cloud rather than in its dense middle and read as sparse outliers. Measured on this grid the
    # top-100 regions occupy 25-43% of the axis on Sharpe, capital hit-rate, cancellation and
    # drawdown, and on Sharpe (1.0-1.2 against the grid's 0.1-0.7) they barely overlap the
    # interquartile range at all. Smoothing the cloud to reveal them would be worse -- regional
    # medians shrink the visible spread to 63-83% of the truth, and adjacent regions share 21 of 22
    # members, so it would draw 6,300 points that are ~95% the same data as their neighbours.
    # Marking them keeps the honest dispersion AND shows the sweet spot.
    # topreg (hollow rings on the top-100 region centres) DELETED 2026-08-29. With the live and
    # best neighbourhoods both marked, a third overlay of 100 more points was noise on a 7,200-point
    # cloud, and it encoded the same arbitrary-rank-cut idea that panel 20's green cluster did.

    # THE LLM BAKE-OFF (panels 14-18). Five full re-curations that differ ONLY in which model runs the
    # event-agent JUDGMENT stage, plus a Fable-5 audit of all 2,849 decisions they made. Optional: absent
    # -> the panels are simply omitted, exactly like the max_events series.
    _bo = ROOT / "data/bakeoff_summary.json"
    bo = json.loads(_bo.read_text()) if _bo.exists() else None
    if bo:
        bo = sorted(bo, key=lambda r: r["cost"])      # ORDER BY LLM SPEND -- the x-axis of every panel
        # DISPLAY NAMES carry the VERSION, because "deepseek4" or "luna" dates badly and means nothing
        # to an outside reader. The Kimi pair is labelled by REASONING EFFORT, not by size: they are
        # the SAME 2.8T model and "kimi-low" reads as a smaller variant, which is the opposite of what
        # that arm tests (does more thinking beat a bigger model?).
        _DISP = {"deepseek4": "DeepSeek<br>V4 Flash", "minimax": "MiniMax<br>M3",
                 "luna": "GPT-5.6<br>Luna", "kimi-high": "Kimi K3<br>HIGH reasoning",
                 "kimi-low": "Kimi K3<br>LOW reasoning",
                 "grok-high": "Grok 4.3<br>HIGH reasoning", "grok-low": "Grok 4.3<br>LOW reasoning",
                 "sonnet5": "Claude<br>Sonnet 5"}
        # FALL BACK TO THE SUMMARY'S OWN `disp`, not the raw slug. This line used to end
        # `.get(r["arm"], r["arm"])`, so the three arms added after the map was written rendered as
        # "grok-low" / "sonnet5" on every axis and in the table -- the version number, and the fact
        # that the Grok pair differ only in REASONING EFFORT, both silently lost. bakeoff_summary.json
        # already carries a correct `disp`; preferring it means a new arm is named in one place.
        for r in bo:
            r["disp"] = _DISP.get(r["arm"]) or r.get("disp") or r["arm"]
    payload["bo"] = bo


    # ---- FIGURES THE PANEL PROSE QUOTES -----------------------------------------------------------
    # These were hard-coded at 6,300 cells and were already wrong at 31,500; the 2026-08-24 trim to
    # 7,200 would have made them wrong a third time. Computed here so a grid change cannot silently
    # falsify the text. Historical measurements in the SOURCE comments are left at the size they were
    # measured on -- rewriting those would falsify the record rather than update it.
    _sh = sorted((c["sharpe"] for c in cells if c.get("sharpe") is not None), reverse=True)
    _maxfin = max(cells, key=lambda c: c["final"])
    _maxfin_rank = (_sh.index(_maxfin["sharpe"]) + 1) if _maxfin.get("sharpe") in _sh else None
    _bestsh = max((c for c in cells if c.get("sharpe") is not None), key=lambda c: c["sharpe"])
    _neg = [c for c in cells if c.get("slope_2h") is not None and c["slope_2h"] < 0]
    _neghi = [c for c in _neg if (c.get("ann") or 0) >= 50]
    # the LIVE region (was `_markset`, which resolved to live-or-best; now that both are marked
    # separately the caption below means the live one, and says so)
    _wsm = [c for c in _reg_mem.get(_live, []) if c.get("slope_2h") is not None]
    _wspos = sum(1 for c in _wsm if c["slope_2h"] > 0)
    _wsmed = statistics.median([c["slope_2h"] for c in _wsm]) if _wsm else 0
    _regsz = payload["region"]["n_members"]

    # ---- CURATOR SUCCESS, the pre-cull baseline the two per-cell pc columns are read against -------
    # Stamped once per sweep by sweep_optimizer.main because NO grid axis reaches the scan path, so
    # this number is identical in all 31,500 cells and cannot be a column.
    _pre = S.get("precull") or {}
    _pre_med = (f"{_pre['pc_precull_med']:+.2f}%" if _pre.get("pc_precull_med") is not None
                else "not recorded in this sweep")
    _pre_txt = (
        f"median <b>{_pre['pc_precull_med']:+.2f}%</b> per period "
        f"(mean {_pre['pc_precull_mean']:+.2f}%, sd {_pre['pc_precull_sd']:.1f}%, "
        f"n={_pre['pc_precull_n']:,} ticker-weeks)"
        if _pre.get("pc_precull_med") is not None else
        "not recorded &mdash; this sweep predates the metric; re-run scripts/sweep_optimizer.py")

    panels = "".join([
        # table-only panels: no plot div, so the render check does not report a phantom blank chart
        ('<section class="panel"><h2>1. Parameter settings</h2><p class="lead">'
         f"The {len(keys)} FREE swept knobs &mdash; every combination is a cell, "
         f"{'&times;'.join(str(len(S['grid'][k])) for k in keys)} = {len(cells)} configs &mdash; and the "
         "values considered. These knobs only RE-WEIGHT a fixed set of curator picks, which is what "
         "makes the grid free: no LLM call is made and no event is discovered or closed differently. "
         "<b>The last two rows are the exceptions</b>, and the cost column says why. "
         "<code>max_events</code> (panels 11\u201312) and <code>min_bundle_articles</code> "
         "(panel 13) are CURATION knobs: the first decides which events stay live and so which "
         "tickers ever reach the optimizer, the second decides which bundles the scout is shown "
         "as a company\u2019s news. Neither can be replayed \u2014 every value needed its own full "
         "re-curation and its own LLM bill. Every other optimizer / curator parameter is held at its "
         f"{_LINK(PROFILE_URL, 'investor_profile.backtest.md')} value."
         f'</p><div class="scroll">{param_tbl}</div></section>'),
        panel(2, "Return vs drawdown",
              "The horizontal axis is max drawdown &mdash; the book's biggest peak-to-trough loss as a "
              "fraction of its running peak; further right = deeper loss. The vertical axis is "
              "annualized return, so <b>upper-left is best</b>. Each point is one config; colour is "
              "<b>capital hit-rate</b> \u2014 the share of exposure that sat in tickers which ended "
              "profitable. It was cancellation until 2026-08-22, but cancellation is <b>83% return</b> "
              "across the grid, so it merely recoloured the y-axis. Hit-rate is +0.45, independent "
              "enough to add a third dimension: a dark point in the upper-left earned its return "
              "WITHOUT the money sitting in winners, which is luck rather than picking. The live "
              "config is the magenta &#9733; star. <b>Amber squares are its 22-cell region</b>; "
              "<b>cyan squares are the region of the best-scoring config</b> "
              f"({' &middot; '.join(str(x) for x in _best)}), which is what table 10 now ranks by "
              "worst member. The two sets are disjoint here &mdash; no cell belongs to both &mdash; "
              "so reaching the top of the grid is more than a one-knob move.<br><br>"
              "<b>These are RAW per-config values.</b> Table 9 reports the median across each "
              "config\u2019s 22-cell neighbourhood, so a point here and its row there are not the "
              "same number. The raw cloud is kept on purpose: regional medians would shrink the "
              "visible spread to 63\u201383% of the truth, and adjacent regions share "
              f"{_regsz - 1} of {_regsz} members, so smoothing would draw {len(cells):,} points that "
              "are ~95% the same data as their neighbours. The rings are how the good neighbourhoods stay visible without "
              "flattering the picture \u2014 they sit at the EDGE of the cloud, not in its dense "
              "middle, which is exactly why dispersion hides them.",
              "s-dd", 470),
        panel(3, "Return vs Sharpe",
              "The same cloud with <b>Sharpe on the horizontal</b> &mdash; return per unit of "
              "volatility, one of the two measures table 10 ranks on. This is the one panel here where "
              "<b>upper-RIGHT is best</b>, since higher Sharpe is better; every other risk axis on "
              "this page reads the other way. Colour is <b>risk aversion</b> (\u03bb, the optimizer\u2019s own setting) \u2014 dark is timid. It is an INPUT, not an outcome, so it explains the cloud rather than re-describing it, and it is orthogonal to return on this grid (+0.21). If the good cells are uniformly dark the ranking is buying safety rather than skill; if they span the ramp, it is not. "
              " The cloud is a tight rising diagonal "
              "&mdash; return and Sharpe correlate <b>+0.92</b> across the grid, so for most configs "
              "they say the same thing and there is no return/risk trade to agonise over. <b>The "
              "divergence is all in the tail, which is exactly where a config gets picked.</b> On "
              f"this book the grid's biggest final value (${_maxfin['final']:,.0f}, "
              f"{_maxfin['ann']:.0f}%/yr) ranks only <b>{_maxfin_rank:,} of {len(cells):,} by "
              f"Sharpe</b> because it earns that return on a {_maxfin['max_drawdown']:.0f}% drawdown. "
              f"The best-Sharpe cell ({_bestsh['sharpe']:.2f}) makes ${_bestsh['final']:,.0f} on a "
              f"{_bestsh['max_drawdown']:.0f}% drawdown instead. Read "
              "the top-right corner, not the top edge: a point that is high but far left is return "
              "bought with volatility a live account has to actually sit through.",
              "s-sharpe", 470),
        panel(4, "Return vs cancellation",
              "The fourth view of the same points, and the one that matters most: the horizontal axis "
              "is the share of the winners' gains handed back by the losers, so <b>upper-left is "
              "best</b> &mdash; a book that earns and keeps it. Colour is <b>churn</b> (trades over the run) \u2014 dark is heavy trading. Orthogonal to return here (+0.24), so it answers what neither axis can: was this bought with turnover? A pale "
              "upper-left point earns well, keeps it, and does so without a deep hole. The cloud's "
              "shape is itself the finding: if it were a tight rising diagonal these knobs would only "
              "be trading return against cancellation, and it is not one.",
              "s-canc", 470),
        panel(5, "Return vs second-half slope",
              "Return against <b>when</b> it arrived. The horizontal is the second-half slope &mdash; "
              "(final &minus; midpoint) &divide; 1.5 years, in dollars per year &mdash; so a point far "
              "right was still compounding in the back half of the run, and a point at or left of zero "
              "made its money early and then coasted or gave it back. <b>Upper-right is best.</b> "
              "Colour is <b>safe-park</b> \u2014 of the capital that did not end in a winner, the share "
              "parked in anchors rather than sunk in a loser. It is the only metric here that is "
              "orthogonal to return (-0.07), so it is the one colour that shows something the two "
              "axes cannot.<br><br>"
              "The cloud is a tight rising diagonal &mdash; return and slope correlate <b>+0.95</b>, "
              "tighter even than return and Sharpe &mdash; so for almost every config the two say the "
              "same thing. <b>That tightness is the finding, and it is a warning about slope, not a "
              "recommendation of it:</b> a measure this correlated with return carries almost no "
              "information return does not, which is consistent with slope ranking configs at the 53rd "
              "percentile on the re-curation transfer test, i.e. no better than random. Only "
              f"<b>{len(_neghi)} of "
              f"{len(cells):,}</b> cells clear 50%/yr while finishing with a negative slope, so the "
              "made-it-early-then-coasted failure this panel was built to expose barely happens on "
              f"this book. <b>{len(_neg):,} cells ({100*len(_neg)/len(cells):.0f}%) do have a negative "
              "slope</b>, but they are the low-return cells you would drop anyway.<br><br>"
              "Two things to read carefully. The axis runs <b>p0.2 to p99.8</b> of the cells, not the full "
              "range \u2014 a handful of extremes would otherwise squash the middle 90% into a fifth "
              "of the plot. However many cells fall outside is stated in the panel\u2019s top-right "
              "corner rather than silently dropped. And the "
              "two axes are computed off <b>different equity curves</b> &mdash; annualized return "
              "compounds rebalance-window to rebalance-window while slope comes from the daily series, "
              "which for the live config end at $302,079 and $460,556 respectively. The rank ordering "
              "is unaffected, but do not read a ratio off this panel. Blue squares are table 10\'s "
              f"LIVE config&rsquo;s region (amber) &mdash; {_wspos} of its {len(_wsm)} members have a "
              f"positive slope, median "
              f"<b>${_wsmed:,.0f}</b>/yr.",
              "s-slope", 470),
        panel(6, "Return vs capital hit-rate",
              "The share of allocated capital-days that sat in tickers which ended up profitable. "
              "Capital-WEIGHTED on purpose: ten winners at 1% and one loser at 40% is not good "
              "picking, and an unweighted count would say it was. Colour is <b>risk aversion</b> (\u03bb, the optimizer\u2019s own setting) \u2014 dark is timid. It is an INPUT, not an outcome, so it explains the cloud rather than re-describing it, and it is orthogonal to return on this grid (+0.21). If the good cells are uniformly dark the ranking is buying safety rather than skill; if they span the ramp, it is not. "
              "\u2014 what the winners handed back \u2014 so the corner you want is right and pale: "
              "the money sat in the right names AND kept the gains.",
              "s-hit", 470),
        panel(7, "Return vs edge",
              "Dollars earned per unit of exposure: total gain divided by total capital-days. It "
              "separates a book that earns a lot by HOLDING a lot from one that earns a lot per "
              "dollar-day of risk. Colour is <b>churn</b> (trades over the run) \u2014 dark is heavy trading. Orthogonal to return here (+0.24), so it answers what neither axis can: was this bought with turnover? A dark dot far right earns "
              "well per unit of exposure without that exposure being in winners \u2014 which is luck, "
              "not picking.",
              "s-edge", 470),
        panel(8, "Return vs the pc score of what actually got funded",
              "For every ticker the optimizer funded (weight above 1%), its fractional price change "
              "from one rebalance to the next. The horizontal axis is the median of those changes "
              "across every ticker and every period, so further right means the money went into "
              "tickers whose price was rising. On the live config that median is +4.28%, against "
              "+2.70% for the whole watchlist and " + _pre_med + " for every ticker the "
              "curator declared live that week. Dot colour is how many tickers hold capital at once.",
              "s-pcf", 470),
        panel(9, "Return vs per-curation return (PCR)",
              "The x-axis is <b>PCR</b> \u2014 the book\u2019s median percent change from one curation "
              "to the next, the quantity CBT panel 2 plots, one number per config. The y-axis is "
              "annualized return, as on every scatter above. "
              "<b>The two axes are related by construction</b> and measure +0.66 on this grid, so the "
              "cloud leans; what the panel is for is the SPREAD around that lean. A cell sitting well "
              "above the trend earned its annual return in a few big periods, one below it ground the "
              "same return out steadily. Colour is funded names/day, the same channel as panel 8, so "
              "the two read as a pair. "
              "PCR is a COLUMN in table 10 and does not vote: across three curations its median flips "
              "sign, its mean form restates <code>annualized</code>, and its mean-over-noise form "
              "measures +0.97 against Sharpe.",
              "s-pcr", 470),
        ('<section class="panel"><h2>10. The best region of the grid</h2><p class="lead">'
         "A config\u2019s region is itself plus every config one setting away &mdash; "
         f"{payload['region']['n_members']} in all &mdash; and every number here is the median and "
         "standard error across them. The score is the mean of the region\u2019s percentile rank on "
         "<b>Sharpe</b> and on <b>pc-funded</b>; every other column is shown but does not vote. "
         "<b>PCR</b> is the per-curation return \u2014 the book\u2019s median percent change from one curation to the next, the quantity CBT panel 2 plots. Shown, not scored: tested across three curations its median flips sign, its mean form restates <code>annualized</code>, and its mean-over-noise form measures +0.97 against Sharpe. "
         "Ranking neighbourhoods instead of single cells means a config only wins if the settings "
         "around it work too. Sharpe scores 1.95 for the live config against 1.00 for a random book "
         "drawn from the same watchlist (<code>scripts/null_book.py</code>), so it is measuring the "
         "config and not just the curation it was handed.</p>"
         f"{reg_tbl}</section>"),

    ] + ([panel(11, "Portfolio value vs max_events",
              "The one knob on this page that is <b>not free to sweep</b>. Everything above replays a "
              f"FIXED curation through different book math, so {len(cells):,} cells cost nothing; "
              "<code>max_events</code> decides which events stay live and so which tickers ever reach "
              "the optimizer, meaning each point here is a full re-curation "
              f"(${sum(r.get('cost_usd') or 0 for r in (me or {{}}).get('rows', [])):.2f} and several "
              "hours for the series). Bars are final portfolio value and, beside it, the gain on the "
              "six no-brainer names from panel 10 &mdash; same axis, same unit. The line is the "
              "share of events "
              "<b>culled at birth</b> &mdash; opened and retired without a single agent read, i.e. work "
              "paid for and thrown away. Read the CULL LINE first: it is a structural count the cap "
              "moves directly, while final value is one lucky name away from noise, and each point is "
              "a single stochastic sample (the scout is an LLM; two runs at the same cap would differ). "
              "A monotone trend across the six is worth something; a one-point spike in dollars is not.",
              "s-me", 460),
        panel(12, "Risk-adjusted quality vs max_events",
              "Sharpe per cap, against the <b>&gt; 0.8 floor</b> the current gate set uses. Read it "
              "beside panel 11: a cap that wins on final value while dropping below the floor has not "
              "won anything the shortlist would admit. <b>Sharpe is NOT what table 10 ranks by</b> "
              "&mdash; that is <code>robust</code>, cancellation rank + drawdown rank &mdash; and "
              "Sharpe measured at the <b>54th percentile</b> on the re-curation transfer test, i.e. a "
              "coin flip. It is kept here as a gate and a sanity column, not as a ranking.",
              "s-me-sharpe", 380),
    ] if me and me.get("rows") else []) + ([
        panel(13, "Portfolio value vs min_bundle_articles",
              "<code>min_bundle_articles</code> is the fewest articles a company bundle needs before "
              "the scout is shown it as that company\u2019s news. Below the floor the bundle is not "
              "built and its article falls to the beat or unclustered path \u2014 nothing is dropped, "
              "only reframed.",
              "s-mb", 380),
    ] if mb and mb.get("rows") else []) + ([
        panel(14, "Portfolio value vs LLM spend",
              "Final portfolio value from eight complete 3-year curations that differ only in "
              "<b><code>event_agent_model</code></b>, ordered left to right by what that model "
              "cost, with wall-clock per curation on the right axis. The shaded band is the "
              "measured noise floor: the same settings curated twice finished 1.86&times; apart.",
              "s-bo-pnl", 430),
        panel(15, "What the money actually buys: decision quality vs spend",
              "Each arm made ~565 keep-or-exit calls over its 3-year curation, graded on three "
              "tests: was the catalyst datable, did the write-up claim more than its cited sources "
              "establish, did the live/exit call contradict its own stated exit condition. The bar "
              "is the share <b>Claude Fable-5</b> judged clean on all three, estimated from a "
              "stratified sample of each arm's calls. The judge saw no prices and no outcomes, and "
              "was blind to which model produced the decision.",
              "s-bo-quality", 430),
        panel(16, "Decision quality, ranked by what the model costs",
              "Panel 17's numbers, arranged to be read on their own: dearest model at the top, "
              "cheapest at the bottom, bar length is the share of calls judged clean, and the "
              "shade is price &mdash; dark is dear. Labels give each model's cost as a multiple "
              "of the cheapest arm; the colour bar carries the dollars. <b>If spending more "
              "bought better decisions, the dark bars would be the long ones.</b> Six models "
              "rather than eight: the two arms that re-ran a model at a different reasoning "
              "effort are kept in panels 14, 16 and 17 but dropped here, where they would cost a "
              "sentence of explanation without changing the picture.",
              "s-bo-rank", 560),
        panel(17, "Where each model actually fails",
              "Panel 17's three tests, split out, same estimate. <b><code>dated</code></b> is "
              "Fable-5's verdict on the catalyst the model chose to open an event on: a specific "
              "resolvable event such as a contract award, a ruling or an FDA decision, rather than "
              "an open-ended trend like \u201cAI demand grows\u201d. <b><code>supported</code></b> "
              "is whether the write-up stayed within what its own cited sources establish. "
              "<b><code>consistent</code></b> is whether that period's live-or-exit call cohered "
              "with the model's own stated exit condition &mdash; it applies to every call, not "
              "only the exits. A call counts as clean in panel 14 only if it passes all three, which "
              "is close to but not the product of these bars &mdash; the failures overlap, since a "
              "vague catalyst tends to come with thin sourcing.",
              "s-bo-perdollar", 430),
        ('<section class="panel"><h2>18. The bake-off, in full</h2><p class="lead">'
         "Every arm, every measure, ordered by LLM spend. <b>Cancellation, drawdown and Sharpe are "
         "book behaviour; dated / supported / clean are decision quality; final value is the number "
         "that cannot be trusted alone.</b> Read the last three columns against the first: the "
         "correlation between what an arm costs and what its book returns is the thing this table "
         "exists to let you check for yourself."
         '</p><div class="scroll">'
         + table_html(["arm", "LLM $", "final value", "cancelled", "max DD", "Sharpe",
                       "FOCUS $", "events", "examined", "decisions", "dated", "supported", "clean (screen)", "overturned", "wrongly passed", "CLEAN (2-sided)"],
                      [[r.get("disp", r["arm"]).replace("<br>", " "), f"${r['cost']:.2f}", f"${r['final']:,.0f}", f"{r['cancelled']:.1f}%",
                        f"{r['max_drawdown']:.1f}%", f"{r['sharpe']:.2f}", f"${r['focus_gain']:,.0f}",
                        str(r["events"]), str(r["examined"]), str(r["decisions"]),
                        f"{r['dated']:.0f}%", f"{r['supported']:.0f}%", f"{r['clean']:.0f}%",
                        f"{r['overturn']:.0f}%", f"{r['fn_rate']:.0f}%", f"{r['clean_2s']:.0f}%"]
                       for r in bo])
         + "</div></section>"),
    ] if bo else []) + ([panel(19, "Portfolio value vs rebalance cadence",
          "<b>A reminder that <code>rebalance_period</code> is now swept, not a verdict.</b> One bar "
          "per cadence arm, and the two settings that DEFINE an arm are printed under it: "
          "<code>rebalance_period</code>, how often the curator looks, and "
          "<code>news_lookback_days</code>, how much news it reads each time.<br><br>"
          "The bar is the <b>median over the live REGION</b> &mdash; the live config plus every "
          "one-knob neighbour, 22 cells, the same neighbourhood panel 10 ranks on. A region rather "
          "than a single cell because one cell is one book and one lucky name; a region only moves "
          "if the settings around it work too. The live cell alone is in the hover.<br><br>"
          "<b>The error bar is &plusmn;1 sd across those 22 configs, which is CONFIG sensitivity, "
          "not re-curation noise.</b> It says how far the book moves when one knob is nudged a "
          "step. It is drawn anyway because it is the conservative of the two: measured here the "
          "region CV is 0.40 / 0.44 / 0.73 against a curation-to-curation CV of 0.30 documented for "
          "this project (identical settings, $117,200 and $62,997), so reading it as re-curation "
          "spread under-claims rather than over. Measuring re-curation spread properly needs the "
          "same arm curated twice and both swept.<br><br>"
          "Everything else on this page replays ONE curation through different book math, so its "
          f"{len(cells):,} cells cost nothing. <code>rebalance_period</code> sets the scan anchors, "
          "so each bar is a separate curation with its own LLM bill and its own draw of the "
          "curator's randomness. <b>Three bars cannot separate a cadence effect from that draw</b>, "
          "and final value is the weakest number on the page &mdash; note that Sharpe does not "
          "follow it here.",
          "s-arms", 400)] if len(arms) > 1 else [])
        + ([panel(20, "Which configs survive ALL THREE curations",
          "Each point is one config&rsquo;s neighbourhood, scored on "
          f"{' + '.join(CONS.get('metrics', []))} across all three cadence curations. Within an arm "
          "a region is summarised by the MEDIAN over its ~22 one-knob members; the y axis is then "
          "the WORST of the three arms, the x axis their mean. A single curation cannot separate a "
          "good config from a lucky one, so ranking on the worst arm is the right instinct."
          "<br><br><b>It does not resolve anything here.</b> The shaded band is the live "
          f"config&rsquo;s floor plus the median spread of a region across the three arms "
          f"(&plusmn;{CONS.get('noise', 0):.1f} percentile points). "
          f"<b>{CONS.get('n_clear', 0)} of {len(CONS.get('rows', []))} regions clear it.</b> The "
          f"live config&rsquo;s own three arms span "
          f"{max(CONS.get('live_per', [0])) - min(CONS.get('live_per', [0])):.1f} points, and the "
          "best floor in the grid beats it by less than that. This panel used to highlight the top "
          "twelve in green; twelve was arbitrary, the cut fell inside a three-way tie, and the "
          "distribution has no gap. The cloud is a continuum and the live config is inside it.",
          "s-cons", 430),
          ('<section class="panel"><h2>21. What the highest-floor configs change, and why it is not a recommendation</h2>'
           '<p class="lead">'
           "Every knob here is a BOOK knob, so each row is a rebuild &mdash; seconds, no LLM, no "
           "re-curation. This table used to be headed &ldquo;how to move the live config into the "
           f"green&rdquo;. <b>That framing is withdrawn.</b> The live config floors at "
           f"<b>{CONS['live_floor']:.1f}</b> and the best region in the grid at "
           f"<b>{CONS['rows'][0]['floor']:.1f}</b> &mdash; a gap smaller than the "
           f"&plusmn;{CONS.get('noise', 0):.1f} points a single region moves between arms, and far "
           f"smaller than the live config&rsquo;s own "
           f"{max(CONS.get('live_per', [0])) - min(CONS.get('live_per', [0])):.1f}-point spread. "
           f"<b>{CONS.get('n_clear', 0)} of {len(CONS.get('rows', []))} regions</b> are "
           "distinguishable from it. So read these rows as <i>what the top of the cloud looks "
           "like</i>, not as changes worth making: every gain listed is inside the measurement "
           "error. <code>risk_aversion</code> deserves extra suspicion &mdash; panel 10 records "
           "that Sharpe alone selects 16&ndash;24 through a timidity tilt that rewards a "
           "low-volatility book by construction."
           '</p><div class="scroll">'
           + table_html(["knobs", "changes from live", "worst arm", "mean", "monthly", "biweekly", "weekly"],
                        CONS["moves"]) + "</div></section>")] if CONS else []))

    ARMS_JS = [{"name": a["name"], "n": a["n"], "rn": a["rn"], "final": a["final"],
                "sd": a["sd"], "cell": a["cell"], "sharpe": a["sharpe"],
                "period": a["period"], "lookback": a["lookback"],
                "evscans": a["evscans"], "stale": a["stale"], "memw": a["memw"]} for a in arms]

    def _slim(pl):
        """Drop the analysis-only arrays before the payload is inlined into the HTML.

        `daily_r` (753 returns per cell) and `blocks` (16x5 per cell) exist for the CSCV/PBO harness
        in scripts/pbo*.py and are never read by this page. Left in, they took docs/sbt.html to 56 MB
        -- past GitHub's file-size warning and far past what a browser should be asked to parse. A
        dashboard nobody can load is worse than one missing a panel.

        Done HERE, at serialisation, rather than by rebinding `cells` earlier: the table rows, the
        shortlist and `_pos` all key off the ORIGINAL cell objects by identity, and copying them
        mid-function silently detached the `robust` and `plateau` values computed after that point.
        """
        # Same reasoning applied to the pc_* columns (2026-08-23). Ten keys per cell x 31,500 cells
        # took the page 17.6 -> 24.0 MB; the panels and table 10 read only pc_fund_med, and the
        # rest (means, sds, counts, the gap t) stay in data/sweep_*.json for analysis. Keeping them
        # inline would spend 6 MB of everyone's page load on numbers nothing on the page displays.
        drop = ("daily_r", "blocks",
                "pc_watch_med", "pc_watch_mean", "pc_watch_sd", "pc_watch_n",
                "pc_fund_mean", "pc_fund_sd", "pc_fund_n",
                "pc_gap", "pc_gap_t", "pc_gap_wk")
        return {**pl, "cells": [{k: v for k, v in c.items() if k not in drop} for c in pl["cells"]]}

    nknob1 = 1 + len(keys)          # last knob column index, for the narrow-column CSS rule
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sweep Backtest (SBT)</title>
<script src="{PLOTLY_CDN}"></script>
<style>{CSS}
.plot{{width:100%}} .scroll{{overflow-x:auto}}
/* PANEL 8's config table. Three bands -- region members, rival corners, the grid's median config --
   distinguished by a left rule and weight instead of a "region: IN/out" column, which spent a whole
   column restating the row order. Numbers right-aligned so the error bars line up and the eye can
   compare magnitudes down a column. */
table.cfg{{border-collapse:collapse;width:100%;font-size:13px;margin-top:9px}}
table.cfg th{{text-align:right;padding:5px 9px;border-bottom:1px solid var(--line);
  color:var(--text2);font-weight:600;white-space:nowrap}}
table.cfg td{{text-align:right;padding:5px 9px;border-bottom:1px solid var(--line);white-space:nowrap}}
table.cfg th:first-child,table.cfg td:first-child{{text-align:left;font-variant-numeric:tabular-nums}}
table.cfg tr.reg td{{font-weight:600;border-left:3px solid {LIGHT['s1']}}}
table.cfg tr.riv td{{color:var(--text2);border-left:3px solid transparent}}
table.cfg tr.mid td{{color:var(--text2);border-left:3px solid transparent;font-style:italic;
  border-top:1px solid var(--line)}}
/* HORIZONTAL headers, with the long knob names broken onto their underscores. Stacking
   `concentration_cap` as concentration/cap over two short lines costs less width than rotating it did,
   and reads straight on instead of at an angle. */
table.rot{{border-collapse:collapse;width:auto}}
table.rot thead th{{vertical-align:bottom;padding:0 4px 5px;border-bottom:1px solid var(--line);
  background:none}}
table.rot thead th span{{display:block;font-size:12.5px;font-weight:600;color:var(--text2);
  line-height:1.15;text-align:center;white-space:normal}}
table.rot td{{padding:3px 6px;font-size:14.5px;white-space:nowrap;text-align:right;
  border-bottom:1px solid var(--line)}}
table.rot td:first-child{{text-align:left;font-weight:600}}
table.rot tr:last-child td{{font-weight:600}}
/* KNOB columns hold 1-5 characters -- 6/0.25/45/2/3.0/0.05 -- while their headers are up to 20, so
   upright they reserved room they never use. Two tiers: the narrowest data (tier A) is squeezed
   hardest. A cell cannot go below its own glyphs, so the leading zero is dropped from decimals
   (0.25 -> .25) -- a normal compaction for a numeric table, and the only way to actually reach 50%. */
table.rot td.k, table.rot thead th.k {{ text-align:center; }}
table.rot td.kA, table.rot thead th.kA {{ padding-left:1px; padding-right:1px; }}
table.rot td.kA {{ font-size:11.5px; }}
table.rot td.kB, table.rot thead th.kB {{ padding-left:2px; padding-right:2px; }}
table.rot td.kB {{ font-size:12.5px; }}
</style></head><body><div class="wrap">
{dash_nav.render('sbt.html')}
<h1>Sweep Backtest (SBT)</h1>
<p class="sub">{len(cells)} optimizer configurations over one FIXED curation ({esc(S['run'])}) &middot;
no LLM, no re-curation &middot; knobs from {_LINK(PROFILE_URL, 'investor_profile.backtest.md')}</p>
{panels}
</div>
<script>
const DATA = {json.dumps(_slim(payload))};
const ARMS = {json.dumps(ARMS_JS)};
const CONS = {json.dumps(CONS)};
const LIGHT = {json.dumps(LIGHT)}, DARK = {json.dumps(DARK)}, ST = {json.dumps(STATUS)};
const CFG = {{displayModeBar:false, responsive:true}};
function base(p, o){{ return Object.assign({{
  paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
  font:{{color:p.text2, size:11.5}}, margin:{{l:60,r:20,t:20,b:44}}}}, o); }}
function draw(){{
  const dark = matchMedia('(prefers-color-scheme: dark)').matches
            && document.documentElement.getAttribute('data-theme') !== 'light';
  const p = dark ? DARK : LIGHT;
  const C = DATA.cells, K = DATA.keys;
  // THE TWO ANNOTATION MARKS MUST NOT BE BLUE. The cloud became a single blue hue on 2026-08-22,
  // which left the light-blue region squares and the purple star sitting INSIDE their own colour
  // family -- both read as just another dot. Annotations are not part of the sequential encoding and
  // should sit as far from it in hue as possible, so: amber squares (the recommendation) and a
  // magenta star (where you actually are). Amber and magenta are ~120 degrees apart from each other
  // and from blue, so the three stay separable in colour-vision deficiency as well as in normal
  // vision, and neither is a reserved status colour. Both carry the existing 1.5px surface ring, so
  // they stay legible where the cloud is dense.
  const PUR = dark ? '#f472b6' : '#db2777';   // star marking the LIVE config (magenta)
  const REGSQ = dark ? '#fbbf24' : '#d97706'; // squares marking the recommended region (amber)

  // PWR panels 1-3: annualized return against risk, then against each churn norm. One builder,
  // three x-axes -- they differ only in what is on the horizontal.
  const curKey = DATA.cur ? K.map(k=>DATA.cur[k]).join('|') : null;
  // SEQUENTIAL = ONE HUE, and NEITHER END MAY VANISH INTO THE SURFACE. The old ramp was YlOrRd, a
  // three-hue yellow-orange-red whose low end is very nearly white: on a white page most of the cloud
  // simply disappeared, which is what a reader sees as "the dots are almost white". A single blue
  // hue, floored at a visible light step and stopped short of black, keeps every point on the page.
  // The dark-theme ramp is CHOSEN, not an automatic flip -- same hue, run the other way, so the light
  // end does not glare against a dark surface.
  const SEQ = dark ? [[0,'#1e3a8a'],[0.5,'#3b82f6'],[1,'#bfdbfe']]
                   : [[0,'#c7dcf5'],[0.5,'#3b82f6'],[1,'#12306e']];
  function _pctl(a, q) {{
    const v = a.filter(x => x !== null && x !== undefined && isFinite(x)).sort((m,n) => m-n);
    return v.length ? v[Math.min(v.length-1, Math.max(0, Math.round(q*(v.length-1))))] : 0;
  }}
  function scat(div, xf, xlab, xsuf, cf, clab, xmax, cmin, cmax, xmin) {{
    const _cv = C.map(cf).filter(x => x !== null && x !== undefined && isFinite(x)).sort((m,n)=>m-n);
    const _rank = (v, x) => {{                     // fraction of the cloud at or below x
      let lo = 0, hi = v.length;
      while (lo < hi) {{ const m = (lo+hi)>>1; if (v[m] < x) lo = m+1; else hi = m; }}
      return v.length > 1 ? lo/(v.length-1) : 0;
    }};
    const _fmtq = x => Math.abs(x) >= 1000 ? Math.round(x).toLocaleString()
                     : (Math.abs(x) >= 10 ? x.toFixed(0) : x.toFixed(1));
    const isCur = c => K.map(k=>c[k]).join('|') === curKey;
    const mk = sel => ({{
      type:'scatter', mode:'markers',
      x:C.filter(sel).map(xf), y:C.filter(sel).map(c=>c.ann),
      marker: sel === isCur
        ? {{size:20, color:PUR, symbol:'star',
           line:{{width:1.5, color:p.surface}}}}
        : {{size:7, color:C.filter(sel).map(c=>_rank(_cv, cf(c))), colorscale:SEQ, reversescale:(['Sharpe','capital hit-rate %','edge $/exposure','safe-park %','risk aversion \u03bb','churn (trades)','funded names/day'].includes(clab)), showscale:true,
           // COLOUR BY RANK, NOT BY VALUE. These metrics are not merely right-skewed, they pile
           // up at zero: safe_park runs p25=1.2, median=6.2, p75=22 against a 65% max. Clipping to
           // p2..p98 still left the MEDIAN dot at 11% of the ramp, so most of 6,300 points shared
           // one colour and the cloud read as a single wash. Ranking is the only mapping that
           // spreads an arbitrary distribution evenly by construction -- the median lands at 50%
           // whatever the shape. Order is preserved (rank is monotone in value), so a darker dot
           // still means more; only the SPACING changes. The colourbar is relabelled with the real
           // values at each quartile so the reader never has to think in percentiles.
           cmin:0, cmax:1,
           colorbar:{{title:{{text:clab, font:{{size:10}}}}, thickness:10,
                     tickvals:[0,0.25,0.5,0.75,1],
                     ticktext:[0,0.25,0.5,0.75,1].map(q => _fmtq(_pctl(_cv, q)))}},
           line:{{width:1, color:p.surface}}}},
      text:C.filter(sel).map(c=>(sel===isCur?'<b>CURRENT CONFIG</b><br>':'')+K.map(k=>k+'='+c[k]).join('<br>')),
      hovertemplate:'%{{text}}<br>ann %{{y:.0f}}%<br>'+xlab+' %{{x:,.0f}}'+xsuf+'<extra></extra>',
      showlegend:false}});
    const _xv = C.map(xf).filter(x => x !== null && x !== undefined && isFinite(x)).sort((m,n)=>m-n);
    // SHOW EVERY DOT (2026-08-22, user's call). Was p0.2..p99.8, which silently trimmed both
    // edges -- 68 of 18,900 on max drawdown, 73 on edge. Now the axis spans the FULL data range, so
    // nothing is clipped and _nclip is 0 by construction (the corner annotation disappears).
    // KNOWN COST, stated so it is not mistaken for a bug: a few extremes now set the scale, and on
    // `edge` that is severe -- p99.8 is 756 against a max of 2,906, so the bulk of the cloud is
    // compressed into the left third of that panel. That is the honest picture of the data; the
    // previous behaviour traded completeness for legibility, and this trades back.
    const _xlo = (xmin !== undefined) ? xmin : _xv[0];
    const _xhi = (xmax !== undefined) ? xmax : _xv[_xv.length - 1];
    const _nclip = _xv.filter(x => x < _xlo || x > _xhi).length;
    const tr=[mk(c=>!isCur(c))];
    // REGION MEMBERS as squares: smaller than the star and drawn UNDER it, so the live config
    // still reads first. Layer order is the point -- cloud, then the two neighbourhoods, then you.
    // TWO NEIGHBOURHOODS: cyan = the BEST-scoring config's region, amber = the LIVE config's.
    // The top-100 hollow rings that used to sit here are gone -- a third overlay of 100 points on a
    // 7,200-point cloud was noise, and it encoded the same arbitrary rank cut panel 20 just shed.
    // Cyan is drawn FIRST so an overlapping amber square stays visible on top; where the two regions
    // share a cell that overlap is the answer to "how far would I actually be moving".
    const BEST = new Set(DATA.bestn || []);
    const bestm = C.filter((c,i) => BEST.has(i) && !isCur(c));
    if (bestm.length) tr.push({{
      type:'scatter', mode:'markers', x:bestm.map(xf), y:bestm.map(c=>c.ann),
      marker:{{size:11, symbol:'square', color:'#22b8cf',
               line:{{width:1.5, color:p.surface}}}},
      text:bestm.map(c=>'<b>BEST REGION</b><br>'+K.map(k=>k+'='+c[k]).join('<br>')),
      hovertemplate:'%{{text}}<br>ann %{{y:.0f}}%<extra></extra>', showlegend:false}});
    const TOP = new Set(DATA.topn || []);
    const top = C.filter((c,i) => TOP.has(i) && !isCur(c));
    if (top.length) tr.push({{
      type:'scatter', mode:'markers', x:top.map(xf), y:top.map(c=>c.ann),
      marker:{{size:11, symbol:'square', color:REGSQ,
               line:{{width:1.5, color:p.surface}}}},
      text:top.map(c=>'<b>LIVE REGION</b><br>'+K.map(k=>k+'='+c[k]).join('<br>')),
      hovertemplate:'%{{text}}<br>ann %{{y:.0f}}%<extra></extra>', showlegend:false}});
    if (curKey && C.some(isCur)) tr.push(mk(isCur));
    Plotly.react(div, tr, base(p, {{margin:{{l:64,r:20,t:16,b:48}},
      annotations: _nclip ? [{{xref:'paper', x:1, xanchor:'right', yref:'paper', y:1.02,
        yanchor:'bottom', showarrow:false, font:{{size:10, color:p.text2}},
        text:_nclip + ' of ' + _xv.length.toLocaleString() + ' cells fall outside this axis'}}] : [],
      xaxis:{{gridcolor:p.grid, ticksuffix:xsuf, range:[_xlo, _xhi],
             title:{{text:xlab+(div==='s-sharpe'||div==='s-slope'||div==='s-hit'||div==='s-edge'||div==='s-pcf'||div==='s-pcr'?' (HIGHER is better)':(div==='s-dd'||div==='s-canc'?' (lower is better)':' (lower = steadier)')), font:{{size:11}}}}}},
      yaxis:{{gridcolor:p.grid, ticksuffix:'%',
             title:{{text:'annualized return', font:{{size:11}}}}}}}}), CFG);
  }}
  const CANC = c=>c.cancelled, DD = c=>c.max_drawdown, SP = c=>c.safe_park;
  // COLOUR FIELDS, mixed across 3-7 (2026-08-22) -- all three measured ORTHOGONAL to the
  // y-axis on THIS grid (risk_aversion +0.21, churn +0.24, safe_park +0.13), so each is
  // showing something neither axis can. slope_2h was considered and REJECTED: +0.84 with
  // return, so as colour it would simply re-draw the y-axis.
  const RA = c=>c.risk_aversion, CH = c=>c.churn;
  // COLOUR CHOICE IS MEASURED, NOT AESTHETIC (2026-08-22). A colour is only worth a channel if it
  // is orthogonal to BOTH axes -- otherwise it recolours a position the reader can already see.
  // Four of the eight metrics are RETURN wearing a hat: sharpe +0.88 with annualized, edge +0.92,
  // slope +0.85, cancelled -0.83. Colouring a return chart by any of them says nothing. Only
  // max_drawdown (-0.30), capital_hit (+0.45) and safe_park (-0.07) are independent enough.
  // Panels 2 and 6 were the worst offenders, coloured by `cancelled` (83% return) on charts whose
  // y-axis IS return. max_drawdown is now the colour NOWHERE -- it is already an axis on panel 2.
  scat('s-dd',   DD,   'max drawdown',         '%', c=>c.capital_hit, 'capital hit-rate %',
       undefined, 30, 81);
  // L1/L2 get SHARPE, not cancellation: neither axis carries any risk, so colour is doing real work
  // here, and Sharpe answers the question churn actually poses -- is the extra trading buying
  // risk-adjusted quality or just noise? Both panels use the same channel so the two norms stay
  // comparable at a glance.
  const SH = c=>c.sharpe;
  // Capped at 125%. Uncapped, a handful of blown-up cells (max 309% on the v11 book) squash the
  // whole decision-relevant 0-100% region into the left margin. Anything past 125% has given back
  // more than it made and fails the shortlist gate regardless, so the clip hides nothing selectable.
  // Colour scale PINNED to 20-120% drawdown rather than auto-scaled. Auto-scaling re-normalises the
  // ramp to whatever book is loaded (v11 spans 29-99%), so the same colour meant different drawdowns
  // between rebuilds and the panel could not be compared across curations. A fixed band fixes that.
  // X-axis 0-250%. Set explicitly rather than auto-ranged so the panel means the same thing across
  // rebuilds -- the cloud's position shifts between curations, and an auto axis re-centres it every
  // time, which hides exactly the drift worth seeing.
  // Return vs SHARPE. The only panel whose x-axis is better HIGHER, so the axis label is
  // switched below rather than inheriting the shared 'lower is better' suffix. Drawdown
  // colour is pinned to 20-120 to match panel 6, so the two read as one picture.
  scat('s-sharpe', SH, 'Sharpe', '', RA, 'risk aversion \u03bb', undefined, 0.5, 24);
  scat('s-canc', CANC, 'gains cancelled',      '%', CH, 'churn (trades)', undefined, 70, 275);
  // slope in $/yr: the axis is p0.2..p99.8 like every other scatter, so the bulk stays
  // readable. xmin -160K keeps the single deeply-negative cell on the page.
  scat('s-slope', c=>c.slope_2h, 'second-half slope', '', SP, 'safe-park %', undefined, 0, 60);
  // DID THE CAPITAL GO WHERE THE MONEY WAS? Both x-axes measure PICKING rather than give-back, which
  // is what cancellation and drawdown cannot see: a config that funds little and risks little scores
  // well on those without ever having held a rising ticker.
  // Coloured by the OTHER half of the pair on purpose -- hit-rate against what it gave back, edge
  // against whether the exposure was in winners -- so each panel carries two independent readings
  // instead of repeating the max-DD colour a fourth time.
  scat('s-hit',  c=>c.capital_hit, 'capital hit-rate', '%', RA, 'risk aversion \u03bb', undefined, 0.5, 24);
  scat('s-edge', c=>c.edge, 'edge $/exposure', '', CH, 'churn (trades)', undefined, 70, 275);

  // CURATOR SUCCESS (2026-08-23). x is the MEDIAN per-rebalance-period price change of the tickers the
  // optimizer funded. Not capital-weighted, which is what capital_hit and edge cannot say: those two
  // score the optimizer's dollars as much as its picks.
  // COLOUR, PICKED BY MEASUREMENT (2026-08-24). funded_per_day is +0.17 with return and +0.03 with
  // this x-axis -- the most orthogonal informative field on the grid -- and it answers
  // concentrated-or-diluted, the question the max_watchlist axis was extended to 20 to settle.
  // Rejected: capital_hit and max_drawdown (0.59/0.65 with return), pc_gap (0.73), worst_behind (0.69).
  // A companion panel over the WATCHLIST (not just the funded subset) was built and dropped
  // 2026-08-24; pc_watch_med and pc_gap are still in data/sweep_*.json for scripts/null_pc_gap.py.
  scat('s-pcf', c=>c.pc_fund_med,  'pc score, funded',      '%',
       c=>c.funded_per_day, 'funded names/day');

  // PCR (2026-08-28, user's call). x is the book's MEDIAN per-curation return -- the portfolio-level
  // twin of the panel above, which is per-TICKER and unweighted. Both are on this page on purpose:
  // pc-funded asks whether the names were rising, PCR asks whether the BOOK was, and a config can
  // do one without the other.
  // COLOUR MEASURED, per the rule above: funded_per_day is +0.16 with the y-axis and +0.27 with this
  // x-axis, the same channel panel 8 uses, so the pair stays comparable. Rejected as colour here for
  // redrawing an axis: edge (+0.96 with y), slope_2h (+0.94), final (+0.88), sharpe (+0.81).
  // NOTE the axes are NOT independent -- rho(pcr, ann) = +0.66. Said in the caption rather than
  // hidden, because a reader who takes a leaning cloud for a discovery has been misled by the panel.
  scat('s-pcr', c=>c.pcr,          'per-curation return (PCR)', '%',
       c=>c.funded_per_day, 'funded names/day');

  // 5. max_events: value (bars) against cull-at-birth (line). TWO y-axes is normally forbidden, and
  // is legitimate here only because the second series is a PERCENTAGE OF A DIFFERENT THING (events
  // culled), not a second measure of the same book on a rescaled money axis -- the trap that makes
  // dual axes lie. The line is the trustworthy series and is drawn on top.
  const ME = DATA.me;
  if (ME && ME.rows && ME.rows.length) {{
    const xs = ME.rows.map(r => r.max_events === 0 ? 'uncapped' : String(r.max_events));
    Plotly.react('s-me', [
      {{type:'bar', name:'final value', x:xs, y:ME.rows.map(r=>r.final),
        marker:{{color:p.s2, line:{{width:2, color:p.surface}}}},
        text:ME.rows.map(r=>'$'+Math.round(r.final).toLocaleString()), textposition:'outside',
        textfont:{{color:p.text2, size:10}}, cliponaxis:false,
        customdata:ME.rows.map(r=>[r.events, r.agent_reads, r.funded, r.cost_usd]),
        hovertemplate:'max_events %{{x}}<br>final $%{{y:,.0f}}<br>%{{customdata[0]}} events · '+
                      '%{{customdata[1]}} agent-reads<br>%{{customdata[2]}} tickers funded · '+
                      '$%{{customdata[3]}} to curate<extra></extra>'}},
      // Same axis as final value because it is the SAME UNIT -- dollars. Total return says whether a
      // cap paid; this says whether it paid on the seven theses the strategy exists to catch, which a
      // cap that culls discoveries should damage first.
      {{type:'bar', name:'gain on the 7 shortlist names', x:xs,
        y:ME.rows.map(r=>r.focus_gain || 0),
        marker:{{color:p.s1, line:{{width:2, color:p.surface}}}},
        customdata:ME.rows.map(r=>[r.focus_held || 0]),
        hovertemplate:'max_events %{{x}}<br>shortlist $%{{y:,.0f}}<br>'+
                      '%{{customdata[0]}} of 7 names held<extra></extra>'}},
      {{type:'scatter', mode:'lines+markers', name:'culled at birth', x:xs,
        y:ME.rows.map(r=>r.cull_pct), yaxis:'y2',
        line:{{width:2, color:ST.serious}}, marker:{{size:9}},
        hovertemplate:'%{{y:.1f}}% of events culled unread<extra></extra>'}}
    ], base(p, {{barmode:'group', margin:{{l:74,r:64,t:34,b:44}},
        legend:{{orientation:'h', y:1.12, x:0, font:{{size:11}}}},
        xaxis:{{title:{{text:'max_events (events allowed live at once)', font:{{size:11}}}},
               type:'category', categoryorder:'array', categoryarray:xs}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$', title:{{text:'final portfolio value', font:{{size:11}}}}}},
        yaxis2:{{overlaying:'y', side:'right', ticksuffix:'%', range:[0,100], showgrid:false,
                 title:{{text:'events culled at birth', font:{{size:11}}}}}},
        // SPY over the same window, as the bar every cap has to clear. Without it the panel invites
        // reading the tallest bar as "good" when the question is whether ANY cap beats buy-and-hold.
        shapes:[{{type:'line', xref:'paper', x0:0, x1:1, yref:'y',
                  y0:ME.rows[0].spy, y1:ME.rows[0].spy,
                  line:{{color:p.text2, width:1.5, dash:'dash'}}}}],
        annotations:[{{xref:'paper', x:0.99, xanchor:'right', yref:'y', y:ME.rows[0].spy,
                       yanchor:'bottom', showarrow:false, font:{{size:10.5, color:p.text2}},
                       text:'SPY $'+Math.round(ME.rows[0].spy).toLocaleString()}}]}}), CFG);

    // 11. Sharpe, against the shortlist floor
    Plotly.react('s-me-sharpe', [{{
      type:'bar', x:xs, y:ME.rows.map(r=>r.sharpe||0),
      marker:{{color:ME.rows.map(r=>(r.sharpe||0) >= 1.2 ? p.s1 : ST.critical),
               line:{{width:2, color:p.surface}}}},
      text:ME.rows.map(r=>(r.sharpe||0).toFixed(2)), textposition:'outside',
      textfont:{{color:p.text2, size:11}}, cliponaxis:false,
      hovertemplate:'max_events %{{x}}<br>Sharpe %{{y:.2f}}<extra></extra>', showlegend:false}}],
      base(p, {{margin:{{l:64,r:20,t:16,b:46}},
        xaxis:{{title:{{text:'max_events', font:{{size:11}}}},
               type:'category', categoryorder:'array', categoryarray:xs}},
        yaxis:{{gridcolor:p.grid, title:{{text:'Sharpe', font:{{size:11}}}}}},
        shapes:[{{type:'line', xref:'paper', x0:0, x1:1, yref:'y', y0:1.2, y1:1.2,
                  line:{{color:ST.warning, width:1.5, dash:'dash'}}}}],
        annotations:[{{xref:'paper', x:0.99, xanchor:'right', yref:'y', y:1.2, yanchor:'bottom',
                       showarrow:false, font:{{size:10.5, color:p.text2}},
                       text:'shortlist floor 1.2'}}]}}), CFG);

  }}
  // PANEL 11 -- min_bundle_articles. Bars are the book; the SHADED BAND is the measured same-config
  // noise floor (5.8x between two curations of identical settings), drawn so the eye cannot read the
  // monotone rise as a trend. Same reason panel 14 carries the bake-off's 1.86x band.
  const MB = DATA.mb;
  if (MB && MB.rows && MB.rows.length) {{
    const xs = MB.rows.map(r => String(r.min_bundle_articles));
    const ys = MB.rows.map(r => r.final);
    const lo = Math.min(...ys), band = lo * 5.8;
    Plotly.react('s-mb', [
      {{type:'bar', name:'final portfolio value', x:xs, y:ys,
        marker:{{color:p.s1, line:{{width:2, color:p.surface}}}},
        text:ys.map(v => '$' + Math.round(v).toLocaleString()), textposition:'outside',
        cliponaxis:false, textfont:{{size:11, color:p.fg}},
        customdata:MB.rows.map(r => [r.events, r.run]),
        hovertemplate:'min_bundle_articles=%{{x}}<br>$%{{y:,.0f}}<br>'+
                      '%{{customdata[0]}} events<br>%{{customdata[1]}}<extra></extra>'}},
      {{type:'scatter', name:'SPY', x:xs, y:MB.rows.map(r => r.spy), mode:'lines',
        line:{{color:ST.warning, width:1.5, dash:'dash'}},
        hovertemplate:'SPY $%{{y:,.0f}}<extra></extra>'}}
    ], base(p, {{margin:{{l:74,r:20,t:34,b:52}}, showlegend:true,
        legend:{{orientation:'h', y:1.16, x:0, font:{{size:11}}}},
        shapes:[{{type:'rect', xref:'paper', x0:0, x1:1, yref:'y', y0:lo, y1:band,
                  fillcolor:(dark ? '#64748b' : '#cbd5e1'), opacity:0.22, line:{{width:0}},
                  layer:'below'}}],
        // AXIS PINNED $50K-$250K. $50,000 is the stake, so the floor is "did it make money at all";
        // the ceiling keeps the three bars legible. The noise band starts at the lowest book and runs
        // to 5.8x it ($574K), i.e. clean OFF THE TOP of this axis -- which is the point, and the
        // annotation says so rather than letting a clipped rectangle read as a bounded range.
        annotations:[{{xref:'paper', x:0.99, xanchor:'right', yref:'paper', y:0.97, yanchor:'top',
                       showarrow:false, font:{{size:10.5, color:p.text2}},
                       text:'shaded = same-config noise floor, 5.8\u00d7 the lowest book '+
                            '($' + Math.round(band).toLocaleString() + ') \u2014 it runs off the top'}}],
        xaxis:{{title:{{text:'min_bundle_articles', font:{{size:11}}}}, type:'category'}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$', range:[50000, 250000],
               title:{{text:'final portfolio value', font:{{size:11}}}}}}}}), CFG);
  }}
  // ---- LLM BAKE-OFF (panels 14-18) --------------------------------------------------------------
  // Already sorted by LLM spend python-side, so every panel shares one left-to-right ordering: the
  // cheapest judgment model first. Labels carry the dollar figure because the arm NAMES mean nothing
  // to a reader arriving from outside the project -- the money is the axis that travels.
  const BO = DATA.bo || [];
  if (BO.length) {{
    const nm = BO.map(r => (r.disp || r.arm) + '<br>$' + r.cost.toFixed(2));
    const cost = BO.map(r => r.cost);
    const fin = BO.map(r => r.final);
    // THE NOISE FLOOR, measured not assumed: two curations at IDENTICAL settings finished 1.86x apart.
    // Drawn as a band around the arms' midpoint so a reader sees at a glance which differences are
    // resolvable. Without it panel 14 invites exactly the over-reading it exists to prevent.
    const mid = fin.reduce((a, b) => a + b, 0) / fin.length;
    const lo = mid / Math.sqrt(1.86), hi = mid * Math.sqrt(1.86);
    // BARS, not a line. Five discrete `event_agent_model` choices are not a continuum, and the line
    // drawn here first implied an interpolation between (say) MiniMax and Luna that has no meaning.
    // Ordered by spend left-to-right, with the MODEL NAME on the mark so the panel reads standalone:
    // arm labels mean nothing to a reader arriving from outside the project, so the name and the
    // price both ride on the bar rather than hiding in a legend.
    // The noise band is a horizontal SHAPE, not a filled trace -- on a category axis a scatter-fill
    // has no continuous x to lie along and would silently vanish.
    Plotly.react('s-bo-pnl', [
      {{type:'bar', x:nm, y:fin, name:'final portfolio value',
        marker:{{color:REGSQ, line:{{width:1.5, color:p.surface}}}},
        text:BO.map(r => '$' + Math.round(r.final / 1000) + 'K'), textposition:'outside',
        textfont:{{size:11, color:p.fg}}, cliponaxis:false,
        hovertext:BO.map(r => 'event_agent_model = ' + r.arm + '  (LLM $' + r.cost.toFixed(2) + ')'),
        hovertemplate:'%{{hovertext}}<br>final $%{{y:,.0f}}<extra></extra>'}},
      // WALL-CLOCK on a second axis. This is a dual-axis chart, which the house rule normally
      // forbids -- but the two series answer ONE question here ("what does this model cost me?") in
      // the two currencies that matter, dollars and hours, and splitting them would break the
      // comparison the panel exists to make. The line is deliberately thin and unfilled so the bars
      // stay the primary read.
      {{type:'scatter', mode:'lines+markers', x:nm, y:BO.map(r => r.minutes), yaxis:'y2',
        name:'wall-clock (min)', line:{{width:2, color:'#fbbf24'}},
        marker:{{size:9, color:'#fbbf24', line:{{width:1.5, color:p.surface}}}},
        hovertemplate:'%{{x}}<br>%{{y:.0f}} min to curate 3 years<extra></extra>'}}
    ], base(p, {{margin:{{l:70,r:62,t:40,b:82}}, showlegend:true,
        legend:{{orientation:'h', y:1.13, x:0, font:{{size:11}}}},
        shapes:[{{type:'rect', xref:'paper', x0:0, x1:1, yref:'y', y0:lo, y1:hi, layer:'below',
                 fillcolor:(dark ? 'rgba(148,163,184,.22)' : 'rgba(100,116,139,.16)'), line:{{width:0}}}}],
        annotations:[{{xref:'paper', x:0.995, xanchor:'right', yref:'y', y:hi, yanchor:'bottom',
                      text:'measured noise floor \u2014 the SAME settings re-run land 1.86x apart',
                      showarrow:false, font:{{size:10, color:p.fg}}}}],
        xaxis:{{type:'category',
               title:{{text:'event_agent_model', font:{{size:11}}}}}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$', range:[90000, 300000],
               title:{{text:'final portfolio value', font:{{size:11}}}}}},
        yaxis2:{{overlaying:'y', side:'right', showgrid:false, rangemode:'tozero',
                ticksuffix:' min', title:{{text:'wall-clock per curation', font:{{size:11}}}}}}}}), CFG);

    // GROUPED BARS on a category axis, matching panel 14. Two rates over five discrete models is
    // not a curve, and the earlier line invited reading a trend between points that do not connect.
    // ONE series, not two. The grey bar was the cheap screening judge's own rate, which measures
    // how wrong THAT judge was about each arm -- a property of the screen, not evidence about the
    // arm, not evidence about it (it lived in the judge-audit panel, dropped 2026-08-21). Two
    // readers in a row took grey for the swept model's
    // self-assessment and concluded the best arm was the one whose bars MATCHED, which inverts the
    // panel. Dropping it also removes the need to name the screening model here at all.
    Plotly.react('s-bo-quality', [
      {{type:'bar', x:nm, y:BO.map(r => r.clean_2s),
        marker:{{color:'#34d399', line:{{width:1.5, color:p.surface}}}},
        text:BO.map(r => r.clean_2s.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
        textfont:{{size:11, color:p.fg}},
        hovertext:BO.map(r => (r.disp || r.arm).replace('<br>', ' ') + ' - ' + r.decisions + ' calls'),
        hovertemplate:'%{{hovertext}}<br>clean %{{y:.1f}}%<extra></extra>'}}
    ], base(p, {{margin:{{l:64,r:20,t:20,b:86}}, showlegend:false,
        xaxis:{{type:'category', title:{{text:'event_agent_model', font:{{size:11}}}}, tickfont:{{size:10}}}},
        yaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[25, 75],
               title:{{text:'decisions clean on all 3 tests', font:{{size:11}}}}}}}}), CFG);

    // PANEL 18 -- the same numbers as 17, laid out for a feed rather than for the sweep. HORIZONTAL
    // bars in a tall frame: model names read left-to-right at full size instead of being rotated or
    // wrapped under a category axis, which is what makes this the shareable version.
    // COST IS ENCODED TWICE, deliberately -- position (most expensive on top) and shade (dark = dear).
    // One sequential hue, light to dark, because price is a MAGNITUDE; a categorical palette here
    // would imply the models are unordered, which is the one thing this chart is arguing they are not.
    // Reversed y so the dearest model sits at the TOP: the eye then travels down through falling price
    // and the bars get LONGER, which is the finding.
    {{
      // SIX BARS, NOT EIGHT. The two HIGH-reasoning arms are dropped from this view only -- panels 14,
      // 16 and 17 keep all eight. This is the chart meant to travel on its own, and "same model twice
      // at different reasoning effort" costs a sentence of explanation that a reader scrolling a feed
      // will not spend. With the pair gone the surviving arm needs no effort suffix either.
      const DROP = new Set(['kimi-high', 'grok-high']);
      const SHORT = {{'grok-low': 'Grok 4.3', 'kimi-low': 'Kimi K3'}};
      const byCost = BO.filter(r => !DROP.has(r.arm)).slice().sort((a, b) => a.cost - b.cost);
      const base$ = Math.min(...byCost.map(r => r.cost));          // CHEAPEST arm = the 1x anchor
      // MULTIPLES ONLY on the axis; the dollars live on the colourbar. A reader whose workload is a
      // different size can use 5.3x; they cannot use $31.66, and repeating both crowds the label.
      const lab = byCost.map(r => (SHORT[r.arm] || (r.disp || r.arm).replace('<br>', ' '))
                                  + '   ' + (r.cost / base$).toFixed(1) + 'x');
      // WARM ramp, not blue. This chart is built to be read in a LinkedIn feed, whose own chrome is
      // blue -- a blue chart there reads as part of the UI rather than as content. Amber to deep
      // orange contrasts with that surround, and hot = expensive needs no legend. Still ONE hue
      // family light-to-dark, because price is a magnitude.
      Plotly.react('s-bo-rank', [
        {{type:'bar', orientation:'h', x:byCost.map(r => r.clean_2s), y:lab,
          marker:{{color:byCost.map(r => r.cost),
                  colorscale:[[0,'#cfe3f2'],[0.28,'#8ab6da'],[0.55,'#c193ac'],[0.8,'#d05f5f'],[1,'#8f1d1d']],
                  cmin:0, cmax:Math.max(...byCost.map(r => r.cost)),
                  line:{{width:1.5, color:p.surface}},
                  colorbar:{{title:{{text:'LLM $ per<br>curation', font:{{size:10}}}}, thickness:10,
                            tickprefix:'$', len:0.55, y:0.5}}}},
          text:byCost.map(r => r.clean_2s.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
          textfont:{{size:13, color:p.fg}},
          hovertemplate:'%{{y}}<br>%{{x:.1f}}%% of calls clean<extra></extra>'}}
      ], base(p, {{margin:{{l:170, r:64, t:16, b:48}}, showlegend:false,
          xaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[0, 78],
                 title:{{text:'decisions clean on all 3 tests', font:{{size:11}}}}}},
          yaxis:{{automargin:true, tickfont:{{size:12}}}}}}), CFG);
    }}

    Plotly.react('s-bo-perdollar', [
      {{type:'bar', name:'dated', x:nm, y:BO.map(r => r.dated_adj), marker:{{color:'#fbbf24'}},
        text:BO.map(r => r.dated_adj.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
        textfont:{{size:10, color:p.fg}},
        hovertemplate:'%{{x}}<br>dated %{{y:.0f}}%<extra></extra>'}},
      {{type:'bar', name:'supported', x:nm, y:BO.map(r => r.supported_adj), marker:{{color:'#34d399'}},
        text:BO.map(r => r.supported_adj.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
        textfont:{{size:10, color:p.fg}},
        hovertemplate:'%{{x}}<br>supported %{{y:.0f}}%<extra></extra>'}},
      {{type:'bar', name:'consistent', x:nm, y:BO.map(r => r.consistent_adj), marker:{{color:'#60a5fa'}},
        text:BO.map(r => r.consistent_adj.toFixed(0) + '%'), textposition:'outside', cliponaxis:false,
        textfont:{{size:10, color:p.fg}},
        hovertemplate:'%{{x}}<br>consistent %{{y:.0f}}%<extra></extra>'}}
    ], base(p, {{barmode:'group', margin:{{l:60,r:20,t:38,b:86}},
        legend:{{orientation:'h', y:1.15, x:0, font:{{size:11}}}},
        xaxis:{{type:'category', title:{{text:'event_agent_model', font:{{size:11}}}}, tickfont:{{size:10}}}},
        yaxis:{{gridcolor:p.grid, ticksuffix:'%', range:[35, 105],
               title:{{text:'process test pass rate (Fable-5 corrected)', font:{{size:11}}}}}}}}), CFG);

  }}

  // 18. CADENCE ARMS -- one BAR per curation at the LIVE book config. Bars, not boxes: the ask was
  // "the portfolio value each arm generates", which is a single book, not a summary of 7,200
  // hypothetical ones. The two knobs that define an arm are printed under each bar, because the
  // whole point of the panel is to remember WHAT is being varied.
  if (typeof ARMS !== 'undefined' && ARMS.length > 1 && document.getElementById('s-arms')) {{
    const COL = [p.s1, p.s2, p.s4];
    const lab = ARMS.map(a => a.period + '<br><span style="font-size:9px">news '
                              + (a.lookback || 30) + 'd</span>');
    Plotly.react('s-arms', [{{
      type:'bar', x:lab, y:ARMS.map(a => a.final),
      marker:{{color:ARMS.map((a, i) => COL[i % COL.length]), line:{{width:2, color:p.bg}}}},
      // +- ONE STANDARD DEVIATION ACROSS THE REGION. This is CONFIG sensitivity -- how much the book
      // moves when a single knob is nudged one step -- NOT a re-curation confidence interval, and
      // the lead says so. It is drawn because it happens to be the CONSERVATIVE of the two: measured
      // 2026-08-26 the region CV is 0.40/0.44/0.73 against a documented curation-to-curation CV of
      // 0.30, so a reader who mistakes it for re-curation noise still under-claims rather than over.
      error_y:{{type:'data', array:ARMS.map(a => a.sd), visible:true,
                color:p.fg, thickness:1.4, width:8}},
      text:ARMS.map(a => '$' + Math.round(a.final).toLocaleString()),
      textposition:'outside', cliponaxis:false, textfont:{{size:11.5, color:p.fg}},
      customdata:ARMS.map(a => [a.sharpe, a.sd, a.cell, a.rn, a.evscans, a.stale, a.memw]),
      hovertemplate:'%{{x}}<br>region median %{{y:$,.0f}}'
                  + '<br>region sd $%{{customdata[1]:,.0f}} over %{{customdata[3]}} configs'
                  + '<br>the live cell alone $%{{customdata[2]:,.0f}}'
                  + '<br>region Sharpe %{{customdata[0]:.2f}}'
                  + '<br>max_event_scans %{{customdata[4]}}'
                  + '<br>max_stale_scans %{{customdata[5]}}'
                  + '<br>curator_memory %{{customdata[6]}} scans<extra></extra>'}}],
      base(p, {{showlegend:false, margin:{{l:76,r:24,t:26,b:66}},
        yaxis:{{gridcolor:p.grid, tickprefix:'$', rangemode:'tozero',
                title:{{text:'final value \u2014 region median \u00b1 1 sd', font:{{size:11}}}}}},
        xaxis:{{type:'category', tickfont:{{size:11}},
                title:{{text:'rebalance_period \u00b7 news_lookback_days', font:{{size:11}}}}}}}}), CFG);
  }}

  // 19. CROSS-ARM CONSENSUS. x = mean score over the three curations, y = the WORST arm. The y axis
  // is the point: a region high on x but low on y won one journal and lost another, which is the
  // over-fit this panel exists to expose. Only the top-right corner is good in every curation.
  if (typeof CONS !== 'undefined' && CONS.rows && document.getElementById('s-cons')) {{
    const R = CONS.rows, K = CONS.keys;
    const lbl = c => K.map((k, i) => k.split('_')[0] + '=' + c[i]).join(' · ');
    const same = (a, b) => a.every((v, i) => Math.abs(v - b[i]) < 1e-9);
    const isLive = r => same(r.cfg, CONS.live);
    // NO HIGHLIGHTED CLUSTER. This drew the top 12 by worst-arm score in green and panel 21 told
    // you to chase them; 12 was arbitrary, the cut fell mid-tie, and the distribution has no gap.
    // What replaces it is the measured noise band: nothing in the grid beats the live config by
    // more than the arm-to-arm spread of a single config, so there is no cluster to move to.
    const rest = R.filter(r => !isLive(r));
    const liveRow = R.find(isLive);
    const tr = [
      {{type:'scatter', mode:'markers', name:'all regions', x:rest.map(r => r.mean),
        y:rest.map(r => r.floor), text:rest.map(r => lbl(r.cfg)),
        marker:{{size:5, color:p.grid, opacity:0.55}},
        hovertemplate:'%{{text}}<br>mean %{{x:.1f}} · worst arm %{{y:.1f}}<extra></extra>'}}
    ];
    if (liveRow) tr.push({{
      type:'scatter', mode:'markers+text', name:'the LIVE config', x:[liveRow.mean],
      y:[liveRow.floor], text:['live'], textposition:'top center',
      textfont:{{size:11, color:ST.critical}},
      marker:{{size:15, color:ST.critical, symbol:'diamond', line:{{width:1.5, color:p.bg}}}},
      hovertemplate:lbl(liveRow.cfg) + '<br>mean %{{x:.1f}} · worst arm %{{y:.1f}}<extra></extra>'}});
    // THE NOISE BAND. Shaded from the live config's floor to floor + the median per-region spread
    // across arms. A point must sit ABOVE this band to be distinguishable from the live config;
    // CONS.n_clear counts how many do.
    const _lo = CONS.live_floor, _hi = CONS.live_floor + CONS.noise;
    Plotly.react('s-cons', tr, base(p, {{showlegend:true,
        legend:{{orientation:'h', y:1.12, x:0, font:{{size:11}}}},
        margin:{{l:70,r:24,t:40,b:56}},
        shapes:[{{type:'rect', xref:'paper', x0:0, x1:1, yref:'y', y0:_lo, y1:_hi,
                 fillcolor:ST.warning, opacity:0.13, line:{{width:0}}, layer:'below'}},
                {{type:'line', xref:'paper', x0:0, x1:1, yref:'y', y0:_hi, y1:_hi,
                 line:{{color:p.text2, width:1.4, dash:'dash'}}}}],
        annotations:[{{xref:'paper', x:0.01, xanchor:'left', yref:'y', y:_hi, yanchor:'bottom',
                      showarrow:false, font:{{size:10.5, color:p.text2}},
                      text:'live + inter-arm noise (' + CONS.noise.toFixed(1) + ') — '
                           + CONS.n_clear + ' of ' + R.length + ' regions clear it'}}],
        xaxis:{{gridcolor:p.grid, title:{{text:'mean score across the 3 curations (percentile)', font:{{size:11}}}}}},
        yaxis:{{gridcolor:p.grid, title:{{text:'score in the WORST arm (percentile)', font:{{size:11}}}}}}}}), CFG);
  }}
}}

draw();
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);
</script></body></html>"""
    out = ROOT / a.out
    out.write_text(dash_nav.stamp(html))
    print(f"wrote {out}  ({len(html)//1024} KB, {len(cells)} cells)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
