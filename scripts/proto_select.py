#!/usr/bin/env python3
"""proto_select.py — does SMARTER max_agents SELECTION move the book? Mechanical rankers + the LLM PICKER.

Post-hoc replay over firehose_scans_full.json (the WHOLE curator book, where the max_agents cull bites).
Swaps the cull's ranking rule and scores each against the RANDOM-selection distribution ACROSS SUB-WINDOWS
(so a lucky full-window headline can't fool us — selection here is luck-dominated, range -72%..+3692%).

Rankers: conviction (baseline) · age_new/age_old · momentum · random(N seeds) · PICKER (LLM keep-list,
ranks on catalyst-arc + P&L context, emits an ordered keep-list only — no numbers to the optimizer, #1-safe).

The picker replays over the SAVED events (catalyst/milestones/exit/P&L already in the scans) — NO scout /
event-agent re-run. Responses are cached (data/windows/picker_cache.json) so re-runs are free. It only calls
the LLM in weeks where #live-events > max_agents.

    python scripts/proto_select.py                 # mechanical rankers + random distribution
    python scripts/proto_select.py --picker        # + the LLM picker, scored vs random across sub-windows
"""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
from util import load_dotenv; load_dotenv()  # noqa: E402
import pandas as pd  # noqa: E402
import firehose, score, curator, build_dashboard as bd  # noqa: E402
from optimizer import load_financial_model, resolve_curator_model  # noqa: E402

GEMS = ["MP", "MU", "HL", "NEM", "FRO", "DHT", "STNG", "BA", "ITA", "COIN", "CRCL", "AG", "WPM"]

PICKER_SYS = (
    "You are the weekly AGENT-PICKER for a news-driven portfolio. You are given every currently-live "
    "event-agent, each with: ticker, catalyst, milestones-to-date, its stated exit condition, weeks_alive, "
    "and cumulative P&L in dollars. Output an ORDERED keep-list of the agents most worth holding capital "
    "next week (top = most worth a slot). You do NOT assign weights, sizes, or expected returns — a "
    "mechanical optimizer sizes whatever you keep. Rank on THESIS ARC AND HEALTH, using the evidence:\n"
    "- FAVOR catalysts still early / building — the shock is unfolding, milestones still landing, thesis "
    "unresolved and under-owned.\n"
    "- DEMOTE catalysts that have crested or are near resolution — if a long-lived winner's catalyst is "
    "about to resolve (award final, vote passed, chokepoint reopening), rank it DOWN: take the gain before "
    "the thesis dies, don't ride it into resolution.\n"
    "- RESERVE a few slots for the newest agents — fresh events are fishing expeditions; most won't pay, so "
    "keep several lines in the water. Don't let established agents crowd out all exploration.\n"
    "- Cumulative P&L is CONTEXT, not the ranking key — a working thesis with live milestones outranks a "
    "stalled one, but do NOT chase the number.\n"
    'Return JSON: {"keep": ["TICKER", ...]} ordered best-first.'
)
PICKER_SCHEMA = {"type": "object", "properties": {"keep": {"type": "array", "items": {"type": "string"}}},
                 "required": ["keep"]}
_CACHE_PATH = ROOT / "data" / "windows" / "picker_cache.json"


def make_picker(fm):
    """Return (pick_fn, stats_fn). pick_fn(cand_meta, max_keep) -> ordered keep-list. Cached by prompt hash."""
    import llm  # noqa: PLC0415
    short = fm.get("picker_model") or "deepseek"        # cheap by default (NOT the sonnet5 scout) — keep cost low
    mid, prov = resolve_curator_model(short)
    client = llm.make_client(prov, mid)
    cache = json.loads(_CACHE_PATH.read_text()) if _CACHE_PATH.exists() else {}
    calls = [0]

    def pick(cand_meta, max_keep):
        user = json.dumps({"max_keep": max_keep, "agents": cand_meta}, sort_keys=True)
        key = hashlib.sha256((short + "|" + PICKER_SYS + user).encode()).hexdigest()[:20]   # model-specific
        if key in cache:
            return cache[key]
        calls[0] += 1
        try:
            txt = client.complete(PICKER_SYS, user, use_web_search=False, label="picker",
                                  stage="picker", json_schema=PICKER_SCHEMA)
            m = __import__("re").search(r"\{.*\}", txt, __import__("re").S)   # tolerate markdown fences / prose
            keep = json.loads(m.group(0) if m else txt).get("keep", [])
            cache[key] = keep                            # cache ONLY clean successes (errors get retried)
            _CACHE_PATH.write_text(json.dumps(cache))
        except Exception as e:  # noqa: BLE001
            print(f"  picker error ({e}); keeping all this week", file=sys.stderr)
            keep = [a["ticker"] for a in cand_meta]
        return keep

    return pick, lambda: (calls[0], f"{short} ({prov})")


def run(scans, fm, rank, max_agents=7, cap=50_000.0, mom_win=30, picker=None):
    """One replay. Merged weight+value loop so cum-P&L is available to the picker. Returns weekly value series."""
    lookback = int(fm.get("lookback_period_days", 14))
    spy_agent = int(fm.get("spy_agent_conviction", 5) or 0)
    bench = score.BENCHMARK
    watch = firehose._stateful_watch(scans)
    anchors = list(scans)
    tickers = {bench} | {t for w in watch.values() for t in w}
    start = (anchors[0] - pd.Timedelta(days=lookback + 40)).strftime("%Y-%m-%d")
    end = (anchors[-1] + pd.Timedelta(days=21)).strftime("%Y-%m-%d")
    panel = score.fetch_panel(sorted(tickers), start, end, use_cache=True)
    days = panel[bench].dropna().index
    valid = {t for t in tickers if t in panel.columns and panel[t].notna().any()}
    watch = {a: [t for t in w if t in valid] for a, w in watch.items()}
    reb = [score.entry_index(days, a.strftime("%Y-%m-%dT%H:%M:%S%z"), fm.get("t_update_days")) for a in anchors]
    conv, first_k, meta, cum_pnl = {}, {}, {}, {}       # last conviction / first-week / event metadata / running $P&L
    val, spyv, vseries, held = cap, cap, [], {g: 0 for g in GEMS}
    for k, a in enumerate(anchors):
        for p in scans[a]:                              # refresh metadata from this week's picks
            t = p["ticker"]; first_k.setdefault(t, k); conv[t] = p.get("conviction", conv.get(t, 5))
            meta[t] = {"catalyst": (p.get("thesis") or "")[:160],
                       "milestones": (str(p.get("milestones") or "")[:200]),
                       "exit_condition": (str(p.get("exit_advice") or p.get("exit_case") or "")[:140])}
        i = reb[k]
        if i is None:
            vseries.append(round(val, 2)); continue
        di = days[i]
        events = [t for t in watch[a] if t in valid]
        if rank == "picker" and max_agents and len(events) > max_agents:
            cm = [{"ticker": t, **meta.get(t, {}), "weeks_alive": k - first_k.get(t, k)} for t in events]   # NO cum_pnl (feedback loop)
            keep = picker(cm, max_agents)
            events = [t for t in keep if t in events][:max_agents] or events[:max_agents]
        elif rank != "picker":
            keyf = {"conviction": lambda t: (-int(conv.get(t, 5) or 5), t),
                    "age_new": lambda t: (-first_k.get(t, 0), t),
                    "age_old": lambda t: (first_k.get(t, 0), t),
                    "momentum": lambda t: (-(firehose._trailing_return(panel, t, di, mom_win) or -9.0), t),
                    }.get(rank, (lambda t: (hash((k, t, rank)) % 100000,)) if rank.startswith("random") else None)
            if keyf and max_agents and len(events) > max_agents:
                keep = set(sorted(events, key=keyf)[:max_agents])
                events = [t for t in events if t in keep]
        uni = list(dict.fromkeys(events + ([bench] if spy_agent else [])))
        w = (curator._optimized_weights(uni, panel, di, fm, lookback) or {}) if uni else {}
        j = reb[k + 1] if k + 1 < len(anchors) else None
        if j is not None and j > i:                     # accrue this week's segment return + per-ticker $P&L
            d0, d1 = days[i], days[j]
            for t in w:
                if pd.notna(panel.loc[d0, t]) and pd.notna(panel.loc[d1, t]):
                    seg = w[t] * (panel.loc[d1, t] / panel.loc[d0, t] - 1) * val
                    cum_pnl[t] = cum_pnl.get(t, 0.0) + seg
            val += sum(w[t] * (panel.loc[d1, t] / panel.loc[d0, t] - 1) * val
                       for t in w if pd.notna(panel.loc[d0, t]) and pd.notna(panel.loc[d1, t]))
            spyv *= panel.loc[d1, bench] / panel.loc[d0, bench]
        vseries.append(round(val, 2))
        for g in GEMS:
            if w.get(g, 0) > 0.01:
                held[g] += 1
    return {"final": val, "ret": val / cap - 1, "spy": spyv / cap - 1,
            "gems_held": {g: n for g, n in held.items() if n}, "vseries": vseries}


def _subret(vseries, a, b):
    v0, v1 = vseries[a], vseries[b]
    return (v1 / v0 - 1) if v0 else 0.0


if __name__ == "__main__":
    use_picker = "--picker" in sys.argv
    if "--log-picker" in sys.argv:                      # audit the picker's inputs+outputs (OFF by default)
        import picker_log
        picker_log.enable(ROOT / "data" / "windows" / "picker_decisions.jsonl")
        print("picker decision log -> data/windows/picker_decisions.jsonl\n")
    scans = bd.load_scans(ROOT / "data" / "windows" / "firehose_scans_full.json")
    fm = load_financial_model(str(ROOT / "investor_profile.backtest.md"))
    pmodel = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--model" and i + 1 < len(sys.argv)), None)
    if pmodel:                                          # --model sonnet5|opus|llama4 overrides the picker model
        fm["picker_model"] = pmodel
    n = len(scans)
    print(f"WHOLE curator book: {n} weeks, max_agents={fm.get('max_agents')}, gates OFF, aging OFF.\n")
    print(f"{'rank rule':<14}{'portfolio':<12}{'vs SPY':<10}{'gem slots won'}")
    for rank in ["conviction", "age_new", "age_old", "momentum", "nocap"]:
        r = run(scans, fm, rank, max_agents=(0 if rank == "nocap" else 7))
        gh = ", ".join(f"{g}:{n_}" for g, n_ in sorted(r["gems_held"].items(), key=lambda x: -x[1])) or "none"
        print(f"{rank:<14}{r['ret']*100:>+7.0f}%    {r['spy']*100:>+6.0f}%   {gh[:52]}")

    NSEED = 30
    rand = [run(scans, fm, f"random{s}", max_agents=7) for s in range(NSEED)]
    rr = sorted(x["ret"] for x in rand)
    print(f"\nRANDOM selection ({NSEED} seeds): min {rr[0]*100:+.0f}%  median {rr[len(rr)//2]*100:+.0f}%  "
          f"max {rr[-1]*100:+.0f}%")

    # sub-window scoring: thirds of the timeline. picker/rules judged by PERCENTILE within random per window.
    edges = [0, n // 3, 2 * n // 3, n - 1]
    wins = [(edges[w], edges[w + 1]) for w in range(3)]

    def pctile(run_res, rand_runs, a, b):
        me = _subret(run_res["vseries"], a, b)
        pool = sorted(_subret(x["vseries"], a, b) for x in rand_runs)
        return 100 * sum(1 for r in pool if r < me) / len(pool), me

    conv_run = run(scans, fm, "conviction")
    print(f"\n{'window':<16}{'conviction %ile':<18}" + ("picker %ile" if use_picker else ""))
    if use_picker:
        pick_fn, stats = make_picker(fm)
        pk_run = run(scans, fm, "picker", picker=pick_fn)
        ncalls, model = stats()
    labels = ["full", *[f"third {i+1}" for i in range(3)]]
    ranges = [(0, n - 1), *wins]
    for lab, (a, b) in zip(labels, ranges):
        cp, _ = pctile(conv_run, rand, a, b)
        line = f"{lab:<16}{cp:>5.0f}th{'':<11}"
        if use_picker:
            pp, _ = pctile(pk_run, rand, a, b)
            line += f"{pp:>5.0f}th"
        print(line)
    if use_picker:
        print(f"\npicker={model} · {ncalls} LLM calls this run (rest cached) · "
              f"picker full-window return {pk_run['ret']*100:+.0f}% vs SPY {pk_run['spy']*100:+.0f}%")
        gh = ", ".join(f"{g}:{n_}" for g, n_ in sorted(pk_run["gems_held"].items(), key=lambda x: -x[1]))
        print(f"picker gem slots won: {gh or 'none'}")
