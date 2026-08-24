#!/usr/bin/env python3
"""sweep_optimizer.py — FULL-FACTORIAL grid over the optimizer knobs, at ZERO LLM cost.

The curation is held FIXED (one run dir); only the book math varies. Nothing here re-runs the
curator, so a 300-cell grid costs nothing but wall-clock and can be repeated as often as we like.

WHY A GRID, not the 1-D sweeps PWR and GHR's old dashboards used. The knobs interact: measured
2026-08-11, concentration_cap 0.60 was harmless at max_watchlist 6 (66% of gains cancelled) and
catastrophic at max_watchlist 10 (99%). A one-at-a-time sweep cannot see that, and reports whichever
slice it happened to hold fixed.

THE HEADLINE MEASURE IS CANCELLATION, not return. `cancelled` = the share of the winners' gains that
the losers give back (|sum of losses| / sum of gains). It is the thing the user actually asked to
fix -- a book whose winners are erased by its losers is not a book with a return problem, it is a
selection problem -- and unlike final value it is not dominated by one lucky name.

    python scripts/sweep_optimizer.py --run data/cbt_3yr_v7 --out data/sweep_v7.json
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import itertools
import json
import sys
import time
from pathlib import Path

import pandas as pd

import statistics

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import firehose as fh  # noqa: E402
import optimizer  # noqa: E402
import score  # noqa: E402

# The grid. Two families, both FREE because neither touches the curation:
#   WHEN capital enters and leaves (max_watchlist, lookback_period_days,
#     drop_unfunded_weeks) -- what the eyeball review of the price popups flagged, since 8 of 10
#     questionable rides were the optimizer defunding while the curator still had the thesis live;
#   HOW MUCH each name gets (concentration_cap, risk_aversion, min_trade_size) -- the mean-variance
#     sizing knobs, swept because PWR's sweep found lambda and the cap to be its live levers.
# cull_rank is NOT swept: fixed at `trend`. Settled 2026-08-12 on the v10 book -- trend wins every
# full-grid marginal (cancelled 44.8 vs 46.2, Sharpe 0.85 vs 0.77, ann 34.9 vs 27.5) and takes all 20
# of the top plateau rows. `keep-first` (alphabetical) survives the shortlist gates MORE often, but
# only because it never rotates on price, so it is steadier and poorer -- not a real contender.
# Worth re-adding as a periodic NULL CONTROL: on the v8 book trend and alphabetical tied, which was
# the tell that the trend ranker was doing nothing, and only a null could have shown that.
GRID = {
    # TRIMMED 2026-08-24, from 31,500 cells to 7,200 (~78 min -> ~18 min). NO KNOB WAS REMOVED, only
    # values, and every value dropped was measured to appear in ZERO of the top-100 regions the page
    # actually ranks (the one exception, risk_aversion 6.0, appeared in 2 and is bracketed one step
    # either side by 4.0 and 8.0). Checked before cutting by restricting the existing 31,500-cell
    # sweep to this sub-grid and re-scoring: it keeps ALL 20 of the full grid's top-20 regions and
    # puts the full grid's #1 at rank 4, so the recommendation survives the trim.
    # WHAT THE TRIM COSTS, stated because it is not free: a region is self plus every one-knob
    # neighbour, so shrinking the axes shrinks the neighbourhood from 31 members to 22. The medians
    # are taken over fewer cells and the +- column widens. That is the price of the 4.4x.
    # NOT the reason the sweep is slow, in case that is the next instinct: profiled 2026-08-24, a
    # cell is ~750 ms of which fh.backtest is ~97% and the pc_* metrics are 28 ms. Cutting metrics
    # would buy nothing; only fewer cells or a faster backtest will.
    "max_watchlist":        [4, 6, 8, 12, 16, 20],
    # 0.25 IS THE BOTTOM EDGE AND THE BEST VALUE (median Sharpe 0.81 vs 0.61 at 1.0), which is the
    # same "as far as the grid goes" pattern that has been misread as an optimum three times on this
    # file. This axis wants EXTENDING DOWNWARD (0.15, 0.20), not trimming. Left intact for now.
    "concentration_cap":    [0.25, 0.40, 0.60, 0.80, 1.00],
    # DROPPED 7, 10 (dead: zero appearances in the top-100 regions, the top-1000 regions AND the
    # top-1000 cells -- the trend cull cannot rank on a window that short) and 60. 14 and 45 are kept
    # as the anchors either side of the 21/30 peak, so the optimum is still visibly INTERIOR.
    "lookback_period_days": [14, 21, 30, 45],
    "drop_unfunded_weeks":  [0, 2, 4],
    # DROPPED 0.5, 1.0, 2.0, 3.0 (all zero in the top-100 regions) and 6.0. 24 remains the top edge
    # and still has the best median Sharpe, so the caveat that predates this trim stands: the turn is
    # near 24 by a 1-D probe, but this grid cannot see past it.
    "risk_aversion":        [4.0, 8.0, 12.0, 16.0, 24.0],
    # DROPPED 0.3 (zero in the top-100). The remaining four are nearly indistinguishable on median
    # Sharpe (0.69 / 0.70 / 0.71 / 0.69), which is itself the finding about this knob.
    "min_trade_size":       [0.0, 0.05, 0.10, 0.20],
}

# Worker state. The frozen price panel is several MB and every cell needs it, so it is handed to each
# process ONCE at start-up rather than pickled per task -- the difference between an 8-minute grid and
# an 80-minute one.
_W: dict = {}


def _init(fm0, scans, anchors, panel, cap):
    _W.update(fm0=fm0, scans=scans, anchors=anchors, panel=panel, cap=cap,
              scan_dates=sorted(scans))


def _cell(combo_keys):
    keys, combo = combo_keys
    fm = {**_W["fm0"], **dict(zip(keys, combo))}
    try:
        b = fh.backtest(_W["scans"], fm, capital=_W["cap"], daily=True, panel=_W["panel"])
        return {**dict(zip(keys, combo)),
                **metrics(b, _W["anchors"], fm, _W["panel"], _W["scan_dates"])}
    except Exception as e:  # noqa: BLE001 - one bad cell must not lose the grid
        return {"_error": f"{type(e).__name__}: {e}", **dict(zip(keys, combo))}


def load_scans(run: Path) -> dict:
    """Rebuild the curator's per-scan picks from a completed run. Same shape backtest() expects."""
    import csv
    rows = [r for r in csv.DictReader((run / "firehose_scans.csv").open())
            if (r.get("ticker") or "").strip()]
    sc: dict = collections.defaultdict(list)
    for r in rows:
        ts = pd.Timestamp(str(r["week"]) + " 16:30", tz="America/New_York")
        sc[ts].append({"ticker": r["ticker"], "thesis": r.get("thesis") or "",
                       "thesis_live": str(r.get("thesis_live", "True")) == "True",
                       "catalyst_resolved": str(r.get("catalyst_resolved", "False")) == "True",
                       "conviction": 5})
    return dict(sorted(sc.items()))


# THE NO-BRAINER SHORTLIST. Tickers with a big multi-year rise WHOSE PRESS NAMED DATED, VERIFIABLE
# CATALYSTS -- contract awards, FDA decisions, licensing milestones -- rather than diffuse narrative,
# AND WHICH OUR CORPUS ACTUALLY NAMES. One per sector, so a config cannot score well by loading a
# single theme:
#   RKLB space/defense · MU AI-infra/semis · IREN crypto->AI datacenter · QUBT quantum
#   VRT data-center power · LEU nuclear fuel
# `focus_gain` is what a config made ON THESE NAMES. It answers a question total return cannot: did
# this config get paid for catching the obvious ones, or for something else entirely?
#
# THE COVERAGE REQUIREMENT IS LOAD-BEARING, and this list was CUT FROM SEVEN TO SIX on 2026-08-17
# because three members failed it. Measured over the exact backtest window 2023-08-11..2026-08-14
# (SPY +81%) against the v8 curation's 37 scans:
#   DRUG +3191%, named in 0 of 37 scans -- the BEST return of the original seven, and invisible.
#   BE   +1469%, named in 0 of 37 scans -- second best, also invisible. Neither reached the price
#        panel at all, since the panel universe is built from the tickers the curator names.
#   MP    +141%, named in 37 of 37 scans -- the WORST return, and the most-covered name we have.
#        Funded by 0.9% of the 6,300 cells for $0. The optimizer declining a +141% name against
#        +1392% alternatives is mean-variance working correctly, NOT a defect to fix.
# So the list was INVERTED against our own coverage: its two biggest movers were unseen and its
# weakest name was seen every week. `focus_gain` was in practice measuring four names.
#
# WHOLE SECTORS ARE DELIBERATELY ABSENT, and the reason is always a retrieval finding, not a
# judgement about the sector. Our corpus samples these INVERTED -- the winners are invisible, the
# non-movers are well covered -- so including them would measure a retrieval hole as a config failure:
#   ROBOTICS   winners unseen (RCAT +868% -> 1 article, UMAC +762% -> 0, ONDS +699% -> 3,
#              KTOS +283% -> named 0), losers well covered (PATH -1% -> 90, SERV -79% -> 37).
#   CRIT. MIN. winners unseen (UAMY +1087% -> named 0, ASM +947% -> 0), covered names flat or down
#              (MP +141% -> 37, USAR +84% -> 28, LAC -66% -> 17, CRML -39% -> 12).
#   BIOTECH    best covered name is LLY, +134% on 22 namings; the real movers are near-invisible
#              (CADL +926% -> named 1, SMMT +714% -> 2, TSHA +711% -> 1). Two independent screens
#              (our corpus, and PWR's own CBT list) found no covered biotech that moved.
#
# THE TWO ADDITIONS ARE NOT SELECTED ON OUR OWN P&L, which would make this gate a restatement of
# `final` rather than an independent check. We LOSE $14.1M on VRT across the grid and MAKE $23.3M on
# LEU; one of each is deliberate.
#   VRT +760%, named 6, 9 corpus articles. Data-center power/cooling; catalysts are hyperscaler
#       contract awards. Chosen independently by PWR's CBT, which is corroboration from a system
#       that does not share our retrieval.
#   LEU +349%, named 23, the purest catalyst on the board: DOE HALEU contract awards, dated and
#       verifiable, exactly the archetype this comment describes. Well covered, so it is a genuine
#       config test rather than a retrieval test.
# Rejected from PWR's list: NVDA/AMD (fail the premise -- a press-reading curator is not what finds
# them, and MU already holds semis), KTOS/AXON/BWXT (named 0), IAU (an ETF wrapper; our measured
# retrieval asymmetry says the thesis is retrievable and the vehicle is not), RGTI/IONQ (quantum,
# already held by QUBT), CCJ/LLY/GOOGL/AMZN (mega-cap or modest), SMCI +54% / LMT +43% (below SPY).
# OKLO +349% (named 5, 21 corpus articles) is a defensible 7th but sits adjacent to LEU in nuclear.
FOCUS = ("RKLB", "MU", "IREN", "QUBT", "VRT", "LEU")


def _pc_scores(b: dict, fm: dict, panel, scan_dates, anchor_set: set, tag=("watch", "fund")) -> dict:
    """CURATOR SUCCESS, one rebalance period at a time (user's metric, 2026-08-23).

    For every ticker on the watchlist, its fractional price change from THIS rebalance to the NEXT --
    pc = (p1 - p0) / p0 -- pooled over every (ticker, period) pair and summarised as median / mean /
    sd. Deliberately NOT the change across the whole span a ticker was watched: that is
    firehose._agent_precision, which gives one span-weighted number per agent (n ~ 100, no dispersion),
    where this gives one per ticker-week (n ~ 200-4,000, with an error bar). The question it answers is
    the one no other column here asks -- "while the curator was watching them, were these tickers
    ramping?" -- and it asks it independently of how much capital each got.

    TWO POPULATIONS, because the answer differs by stage and the DIFFERENCE is the diagnostic:
      watch  every ticker on the post-cull watchlist that period, funded or not -> the CURATION score.
      fund   the subset the optimizer actually gave weight (>1%)                -> the EXECUTION score.
    Measured on the canonical v21 book: median +2.70% watched vs +4.28% funded, and the sd FALLS
    19.1 -> 14.8, so the sizing stage is picking less dispersed names as well as better ones.
    `pc_gap` pairs them WEEK BY WEEK (funded mean - watched mean, then the t over weeks), which cancels
    the market move without needing SPY and puts the error bar on weeks (n ~ 36) rather than on
    ticker-weeks. That distinction is not cosmetic: same book, the naive ticker-week t is +5.70 where
    the week-clustered one is +0.62, a 9x overstatement, because every ticker in a week shares the
    market's move and the pooled sd pretends they are independent.

    READ THE MEDIAN, NOT THE MEAN. Per-period sd is ~20%, so variance drag sd^2/2 ~ 2pt is the same
    size as the mean itself -- on the PRE-CULL population the arithmetic mean is +2.17% while the log
    mean is -0.17%. An arithmetic mean rewards a config for watchlisting wilder tickers at zero skill.
    Both are reported; only the median is meant to be compared across cells.

    NOT SCORED, and not because it is weak. It is partly CIRCULAR on the funded side -- the trend cull
    and the mean-variance sizing both read the same `lookback_period_days` price window, so "the funded
    names ramped" is in part momentum autocorrelation. The matched-random null (fund the same NUMBER of
    names in the same weeks, drawn at random from the watchlist) is what would settle how much of the
    gap is skill, and it has not been run.
    """
    days = panel[score.BENCHMARK].dropna().index
    reb = [score.entry_index(days, a.strftime("%Y-%m-%dT%H:%M:%S%z"), fm.get("t_update_days"))
           for a in scan_dates]
    watch = b.get("watch") or {}
    d = b.get("daily") or {}
    pos_of = {s: i for i, s in enumerate(d.get("dates") or [])}
    al = d.get("alloc") or {}
    W, F = [], []
    for k in range(len(scan_dates) - 1):
        i, j = reb[k], reb[k + 1]
        if i is None or j is None or j <= i:
            continue
        d0, d1 = days[i], days[j]
        pos = pos_of.get(d0.strftime("%Y-%m-%d"))
        for t in watch.get(scan_dates[k], ()):
            # ANCHORS EXCLUDED, same reasoning that pulled them out of capital_hit/edge on 2026-08-22:
            # SPY/BIL are on the watchlist every week by construction and say nothing about curation.
            if t in anchor_set or t == score.BENCHMARK or t not in panel.columns:
                continue
            p0, p1 = panel.loc[d0, t], panel.loc[d1, t]
            if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
                continue
            pc = float(p1 / p0 - 1)
            W.append((k, pc))
            if pos is not None and al.get(t) and al[t][pos] > 0.01:
                F.append((k, pc))

    def _s(rows, name):
        x = [v for _, v in rows]
        if len(x) < 3:
            return {f"pc_{name}_n": len(x)}
        return {f"pc_{name}_n": len(x),
                f"pc_{name}_med": round(100 * statistics.median(x), 2),
                f"pc_{name}_mean": round(100 * statistics.fmean(x), 2),
                f"pc_{name}_sd": round(100 * statistics.pstdev(x), 2)}

    out = {**_s(W, tag[0]), **_s(F, tag[1])}
    wk_w, wk_f = collections.defaultdict(list), collections.defaultdict(list)
    for k, v in W:
        wk_w[k].append(v)
    for k, v in F:
        wk_f[k].append(v)
    gap = [statistics.fmean(wk_f[k]) - statistics.fmean(wk_w[k]) for k in sorted(set(wk_f) & set(wk_w))]
    if len(gap) > 2:
        sd = statistics.stdev(gap)
        out["pc_gap"] = round(100 * statistics.fmean(gap), 2)
        out["pc_gap_t"] = round(statistics.fmean(gap) / (sd / len(gap) ** 0.5), 2) if sd else None
        out["pc_gap_wk"] = len(gap)
    return out


def metrics(b: dict, anchors: set, fm: dict, panel=None, scan_dates=None) -> dict:
    """The scoreboard for one cell. Cancellation leads; return is reported but not optimised for.

    `panel`/`scan_dates` are optional only so collect_max_events.py can keep calling this with three
    arguments; without them the pc_* curator-success columns are simply absent."""
    d = b.get("daily") or {}
    g = d.get("gain") or {}
    pos = sum(v for v in g.values() if v > 0)
    neg = sum(v for v in g.values() if v < 0)
    al = d.get("alloc") or {}
    n = len(d.get("dates") or [])
    fpd = (sum(sum(1 for t in al if t not in anchors and al[t][i] > 0.01) for i in range(n))
           / max(n, 1))
    vals = d.get("value") or [b.get("final", 0)]
    peak, mdd = vals[0], 0.0
    for v in vals:                                   # max drawdown: the risk side of the frontier
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak if peak else 0)
    # TURNOVER, in weight space, PWR's two norms. `alloc` holds each rebalance's weights flat across
    # the days until the next one, so every nonzero step IS a trade -- drift never enters, which is
    # the distinction PWR draws ("Sigma|dweight| from share changes; drift between rebalances is not
    # a trade"). L1 = one-way turnover; L2 weights concentrated single-name rotations more heavily.
    # Both annualized to %/yr so they do not simply grow with window length.
    tk = list(al)
    l1 = l2 = 0.0
    churn, prev_set = 0, None
    for i in range(1, n):
        step = [al[t][i] - al[t][i - 1] for t in tk]
        if any(abs(x) > 1e-9 for x in step):
            l1 += sum(abs(x) for x in step)
            l2 += sum(x * x for x in step) ** 0.5
        cur = {t for t in tk if al[t][i] > 0.01}
        if prev_set is not None:
            churn += len(cur ^ prev_set)
        prev_set = cur
    # Risk-adjusted sanity columns. Cancellation alone cannot tell a genuinely steady book from one
    # that barely traded and barely earned; these two catch that.
    rets = [(vals[i] / vals[i - 1] - 1) for i in range(1, n) if vals[i - 1]]
    sharpe = gp = None
    if len(rets) > 2:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
        rf = float(fm.get("risk_free_rate", 0.04)) / 252.0
        sharpe = ((mu - rf) / sd * (252 ** 0.5)) if sd else None
        pain = abs(sum(r for r in rets if r < 0))       # Schwager's Gain-to-Pain: all up-moves over
        gp = (sum(rets) / pain) if pain else None       # the sum of every down-move
    yrs = max(n, 1) / 252.0
    v0, vN = (vals[0] or 1), vals[-1]
    ann = ((vN / v0) ** (1 / yrs) - 1) if v0 > 0 and vN > 0 else None
    # WHEN the return arrives, not just how much. Nothing else on the scoreboard says anything about
    # timing, and it turns out to be a config choice rather than a property of the strategy: on the
    # me16 book, moving min_trade_size 0.10 -> 0.30 alone takes the durable lead from 15.0 months to
    # 6.3 and the worst losing spell from 91 days to 32.
    #   lead_months  = months until the book is ahead of SPY AND STAYS ahead for the rest of the run.
    #                  Deliberately the strict reading: this book leads SPY within FOUR DAYS, so a
    #                  "first time ahead" measure says nothing. What matters is when the lead stops
    #                  being given back. Note it is dominated by the LAST time a config fell behind,
    #                  so it is a lagging measure by construction -- read it with worst_behind.
    #   worst_behind = the longest unbroken run of days behind SPY, in trading days.
    vals = d.get("value") or []
    spyv = d.get("spy") or []
    lead_m, worst = None, 0
    if vals and spyv and len(vals) == len(spyv):
        ahead = [a > b for a, b in zip(vals, spyv)]
        run = 0
        for x in ahead:
            run = run + 1 if not x else 0
            worst = max(worst, run)
        for i in range(len(ahead)):
            if all(ahead[i:]):
                lead_m = round(i / len(ahead) * (len(ahead) / 252) * 12, 1)
                break
    # SECOND-HALF SLOPE (user's suggestion, 2026-08-16): (final - midpoint) / 1.5 years, in $/yr.
    # Complements lead_months, which is a LAGGING measure -- it is fixed by the last time a config fell
    # behind and says nothing about whether returns are still arriving. Slope asks the forward-looking
    # version: is this book still compounding in its back half, or did it make its money early and
    # then coast? A high final value with a flat second half is a config that got lucky once.
    half = round(((vals[-1] - vals[len(vals) // 2]) / 1.5), 2) if len(vals) > 2 else None
    # ---- DID THE CAPITAL GO WHERE THE MONEY WAS? -------------------------------------------------
    # `robust` (cancellation + drawdown) scores a book on what it GAVE BACK, and both inputs are
    # scale-free ratios -- so a config that funds little and risks little can rank well without ever
    # having picked a rising ticker. These two measure the picking directly, and neither is a P&L
    # number, so both survive the noise floor that sinks final value.
    #   capital_hit  the share of allocated capital-days that sat in tickers which ENDED UP PROFITABLE.
    #                Capital-weighted on purpose: a config that funds ten winners at 1% and one loser
    #                at 40% has not picked well, and an unweighted count would say it had.
    #   edge         the same idea in dollars per unit of exposure: total gain divided by total
    #                capital-days. Answers "what did each dollar-day of risk actually earn?", which
    #                separates a book that earns a lot by holding a lot from one that earns a lot per
    #                unit held.
    cap_days = {t: sum(al[t]) for t in al}
    # ANCHORS ARE EXCLUDED FROM BOTH PICKING METRICS (2026-08-22). They were in the denominators, and
    # they distorted the two in OPPOSITE directions, so a config's score partly reflected how much
    # idle capital it parked rather than how well it picked -- the opposite of what both measure.
    # Measured on the canonical book: anchors take 8.1% of capital-days and contribute 1.3% of the
    # gain, earning $25.47 per capital-day against the picks' $175.91. Including them inflated
    # capital_hit by +2.8pt (SPY and BIL both end profitable, so parking counts as a WIN) and
    # deflated edge by 12.17 (~7%). Small here; on a capped curation that parks far more it is not,
    # and a config sitting 50% in SPY would collect ~50 points of free capital_hit.
    pick_days = {t: cd for t, cd in cap_days.items() if t not in anchors}
    tot_cd = sum(pick_days.values())
    win_cd = sum(cd for t, cd in pick_days.items() if g.get(t, 0) > 0)
    los_cd = sum(cd for t, cd in pick_days.items() if g.get(t, 0) <= 0)
    anc_cd = sum(cd for t, cd in cap_days.items() if t in anchors)
    pick_gain = sum(v for t, v in g.items() if t not in anchors)
    capital_hit = round(100 * win_cd / tot_cd, 1) if tot_cd else None
    edge = round(pick_gain / tot_cd, 2) if tot_cd else None
    # SAFE-PARKING SHARE (user's proposal, 2026-08-22). Of the capital that did NOT end up in a
    # winner, how much sat in the anchors rather than in a losing pick? Parking in SPY when nothing
    # is worth funding is a correct decision; holding a loser is a mistake, and every other metric
    # here treats the two as the same "not a winner".
    # BOUNDED FORM ON PURPOSE. The proposal was anchors/losers, which is rank-IDENTICAL to this
    # (x/(1+x) is strictly increasing) but unbounded: a config with no losing capital-days divides by
    # zero and lands at infinity, which a percentile rank cannot order. This form gives it 100%.
    # NOT SCORED, deliberately -- see the note in build_sbt_dashboard: taken alone it is maximised by
    # not participating at all.
    safe_park = round(100 * anc_cd / (anc_cd + los_cd), 1) if (anc_cd + los_cd) else None
    # ---- BLOCK STATISTICS, for CSCV / PBO -----------------------------------------------------------
    # Bailey-Borwein-Lopez de Prado's Combinatorially Symmetric Cross-Validation needs a performance
    # matrix over TIME BLOCKS, so that a config can be scored on any half of the history and checked on
    # the complement. Storing the whole daily return series for 6,300 cells would be ~38MB; storing per
    # block (n, sum r, sum r^2, sum of positive r, sum of negative r) is 300x smaller and lets mean,
    # stdev and a cancellation analogue be reconstructed EXACTLY on any union of blocks -- because all
    # five are additive. Nothing here is a new metric; it is the same daily series, pre-summed.
    # THE DAILY RETURN SERIES ITSELF, for PURGED CSCV. Block sums are additive and so are exact on any
    # union of BLOCKS, but purging works at DAY granularity -- it removes observations whose holding
    # window overlaps the test half, and that boundary falls inside a block. So the daily series has to
    # be kept too. 6,300 configs x ~753 days is 19MB as float32, which is nothing; stored rounded to
    # 6dp as a flat list because JSON has no float32.
    daily_r = [round(x, 6) for x in rets]
    NB = 16                                    # even, so CSCV can split 8 train / 8 test
    blocks = []
    if len(rets) >= NB * 4:
        edges = [round(i * len(rets) / NB) for i in range(NB + 1)]
        for i in range(NB):
            r = rets[edges[i]:edges[i + 1]]
            blocks.append([len(r), round(sum(r), 8), round(sum(x * x for x in r), 10),
                           round(sum(x for x in r if x > 0), 8),
                           round(sum(x for x in r if x < 0), 8)])
    focus = round(sum(v for t, v in g.items() if t in FOCUS), 2)
    pc = (_pc_scores(b, fm, panel, scan_dates, anchors)
          if panel is not None and scan_dates is not None else {})
    return {**pc,
            "final": round(b.get("final", 0), 2),
            "blocks": blocks, "daily_r": daily_r,
            "capital_hit": capital_hit, "edge": edge, "safe_park": safe_park,
            "focus_gain": focus,
            "lead_months": lead_m,
            "slope_2h": half,
            "worst_behind": worst,
            "focus_held": sum(1 for t in FOCUS if t in g),
            "cancelled": round(100 * abs(neg) / pos, 1) if pos else None,
            "ann": round(100 * ann, 1) if ann is not None else None,
            "winners": sum(1 for v in g.values() if v > 0),
            "losers": sum(1 for v in g.values() if v < 0),
            "funded_per_day": round(fpd, 2),
            "max_drawdown": round(100 * mdd, 1),
            "sharpe": round(sharpe, 2) if sharpe is not None else None,
            "gain_pain": round(gp, 2) if gp is not None else None,
            "l1": round(100 * l1 / yrs, 1),
            "l2": round(100 * l2 / yrs, 1),
            "churn": churn,
            "spy": round(b.get("spy_final", 0), 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="data/cbt_3yr_v7")
    ap.add_argument("--out", default="data/sweep_v7.json")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--panel-only", action="store_true",
                    help="freeze data/<run>/panel.csv and stop, without running the 6,300-cell grid. "
                         "CBT needs the frozen panel to be reproducible but does NOT need the sweep, "
                         "and the grid saturates every core -- so this lets the price panel be built "
                         "while something else (a curation) is still running.")
    ap.add_argument("--every", type=int, default=1,
                    help="keep every Nth scan -- proxies a slower rebalance cadence on the SAME "
                         "curation (2 turns a biweekly run into a monthly one). A lower bound on a "
                         "true slower run: the skipped scans' news is lost rather than folded into a "
                         "wider window.")
    a = ap.parse_args(argv)
    run = ROOT / a.run
    fm0 = optimizer.load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    scans = load_scans(run)
    if a.every > 1:
        ks = sorted(scans)
        scans = {k: scans[k] for k in ks[::a.every]}
        print(f"  cadence proxy: every {a.every}th scan -> {len(scans)} of {len(ks)}", flush=True)
    anchors = set(fh.anchor_tickers(fm0))

    uni = sorted({p["ticker"] for v in scans.values() for p in v}
                 | set(fm0.get("starter_watchlist") or []) | anchors | {score.BENCHMARK, "BWET"})
    lo = (min(scans) - pd.Timedelta(days=max(GRID["lookback_period_days"]) + 90)).strftime("%Y-%m-%d")
    hi = (max(scans) + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
    # THE PANEL IS FROZEN TO DISK, per run, and reused. `use_cache=False` here meant every sweep
    # re-downloaded prices, and two sweeps of the SAME journal with the SAME code then disagreed on
    # 919 of 6,300 cells (15%) -- median 1.17x apart, 274 cells over 1.5x, one at 36x ($2,914 vs
    # $106,328 for [8, 0.25, 21, 2, 0.5, 0.3]). Small differences in adjusted closes cascade through
    # the covariance and the min_trade_size threshold, and knife-edge cells flip their whole book.
    # That is a SECOND noise source stacked on the curation noise floor of CLAUDE.md #6, and it sat
    # underneath every cross-sweep comparison made before 2026-08-19.
    # The panel is written next to the RUN, not to the shared price cache, so a sweep is reproducible
    # against its own curation for as long as that directory exists, and re-fetching is an explicit
    # choice (delete the file) rather than the default.
    _pf = run / "panel.csv"
    if _pf.exists():
        panel = pd.read_csv(_pf, index_col=0, parse_dates=True)
        print(f"  panel: reusing frozen {_pf} ({panel.shape[1]} tickers)", flush=True)
    else:
        print(f"  fetching one frozen panel: {len(uni)} tickers {lo}..{hi}", flush=True)
        panel = score.fetch_panel(uni, lo, hi, use_cache=False)
        panel.to_csv(_pf)
        # RE-READ WHAT WE JUST WROTE. to_csv/read_csv is NOT lossless -- measured 2026-08-22, a
        # round-trip moves float64 closes by ~6e-14 (last-bit decimal repr). That is nothing on its
        # own and everything here: this file's own header records small close differences cascading
        # through the covariance and the min_trade_size threshold to flip a knife-edge cell from
        # $2,914 to $106,328. Handing the workers the IN-MEMORY frame while storing the round-tripped
        # one made the sweep's own numbers unreproducible from its own frozen panel -- SBT's cell for
        # the live config recorded $44,405 where recomputing off panel.csv gave $40,498, a 10% gap on
        # what is supposed to be a deterministic replay, and CBT (which reads the CSV) could never
        # agree with SBT. Re-reading makes the fetch path byte-identical to the reuse path.
        panel = pd.read_csv(_pf, index_col=0, parse_dates=True)
        print(f"  panel: froze {panel.shape[1]} tickers -> {_pf} (re-read for exactness)", flush=True)
    # THE PRE-CULL pc SCORE, stamped ONCE for the whole sweep. The population here is every ticker the
    # curator declared thesis_live that week (~52/week), BEFORE max_watchlist trims it -- and NO knob in
    # GRID reaches the scan path, so this number is identical in all 31,500 cells. It is the baseline the
    # per-cell pc_watch_* is read against: on the v21 book the curator's live names (median +0.61%) are
    # indistinguishable from the ones the cull throws away (+0.51%), so whatever separation exists
    # appears at the cull and the sizing, not at the naming.
    _starter = [str(t).strip().upper() for t in (fm0.get("starter_watchlist") or []) if str(t).strip()]
    precull = _pc_scores({"watch": fh._stateful_watch(scans, seed=_starter, fm=fm0)},
                         fm0, panel, sorted(scans), anchors, tag=("precull", "_unused"))
    precull.pop("pc__unused_n", None)
    print(f"  pre-cull pc (all thesis_live, constant across the grid): "
          f"median {precull.get('pc_precull_med')}%  mean {precull.get('pc_precull_mean')}%  "
          f"sd {precull.get('pc_precull_sd')}%  n={precull.get('pc_precull_n')}", flush=True)

    if a.panel_only:
        print(f"  --panel-only: stopping before the grid.", flush=True)
        return 0

    keys = list(GRID)
    cells = list(itertools.product(*(GRID[k] for k in keys)))
    print(f"  {len(cells)} cells over {keys}", flush=True)
    cap = float(fm0.get("initial_investment_usd", 50_000))
    out, t0, bad = [], time.time(), 0
    with cf.ProcessPoolExecutor(max_workers=a.workers, initializer=_init,
                                initargs=(fm0, scans, anchors, panel, cap)) as ex:
        for i, r in enumerate(ex.map(_cell, [(keys, c) for c in cells], chunksize=8), 1):
            if "_error" in r:
                bad += 1
            else:
                out.append(r)
            if i % 250 == 0 or i == len(cells):
                el = time.time() - t0
                print(f"    {i}/{len(cells)} cells · {el/60:.1f} min · "
                      f"~{(len(cells)-i)*el/i/60:.1f} min left", flush=True)
    if bad:
        print(f"  {bad} cells failed and are omitted", file=sys.stderr)
    Path(ROOT / a.out).write_text(json.dumps(
        {"run": a.run, "grid": GRID, "base": {k: fm0.get(k) for k in keys},
         "precull": precull, "cells": out}, indent=1))
    ok = [c for c in out if c.get("cancelled") is not None]
    ok.sort(key=lambda c: c["cancelled"])
    print(f"\n  wrote {a.out}: {len(out)} cells")
    print(f"  LOWEST cancellation : {ok[0]['cancelled']}%  {[ok[0][k] for k in keys]}  ${ok[0]['final']:,.0f}")
    best = max(ok, key=lambda c: c["final"])
    print(f"  HIGHEST final       : ${best['final']:,.0f}  {[best[k] for k in keys]}  {best['cancelled']}% cancelled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
