"""forward_engine.py — ONE live week of the EVENT-FIRST engine for the forward paper trade.

Replicates the backtest's per-week loop (agent.run_event_agent_scans) for a single live week, but
fed by the live firehose GATHER (forward_gather) instead of a GDELT pool, with state persisted to a
LOCAL journal across weekly runs (the backtest keeps it in-memory per batch run). Reuses agent.py's
scout / matcher / event_agent_v2 / consolidation unchanged.

State (`data/forward/journal.json`) is LOCAL-ONLY / gitignored — pulled+derived data. The committed
record is the dashboard (see the forward-test-plan). Journal starts EMPTY: the series holds nothing
until a NEW early gem is discovered, then tracks it week to week.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
import pandas as pd

import agent
import forward_gather
import llm

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_F = REPO_ROOT / "data" / "forward" / "journal.json"   # LOCAL-ONLY (gitignored)


def _load() -> tuple[dict, dict, int, int]:
    """-> (events, retired, nid, week_seq). Empty on first run."""
    if STATE_F.exists():
        st = json.loads(STATE_F.read_text())
        events = {k: {**v, "vehicles": set(v["vehicles"])} for k, v in st.get("events", {}).items()}
        return events, dict(st.get("retired", {})), int(st.get("nid", 0)), int(st.get("week_seq", 0))
    return {}, {}, 0, 0


def _save(events: dict, retired: dict, nid: int, week_seq: int) -> None:
    STATE_F.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_F.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "events": {k: {**v, "vehicles": sorted(v["vehicles"])} for k, v in events.items()},
        "retired": retired, "nid": nid, "week_seq": week_seq}, indent=2, default=str))
    tmp.replace(STATE_F)


def _retired_block(retired: dict, week_seq: int, memory_weeks: int) -> str:
    """Resolved-catalyst reminder for the scout (curator_memory_weeks: 0=off, <0=all, >0=last N)."""
    if memory_weeks == 0:
        return ""
    return "\n".join(f"- {t}: {c}" for t, (c, ri) in retired.items()
                     if memory_weeks < 0 or (week_seq - int(ri)) < memory_weeks)


def run_week(anchor: pd.Timestamp, model: str, rebalance_days: int,
             curator_memory_weeks: int = 8, workers: int = 8, capture: dict | None = None) -> list[dict]:
    """Run one live event-first week: gather -> scout -> match -> event agents -> save journal.
    Returns this week's picks (the live watchlist). `capture` (if given) is filled with the gather's
    raw queries+results for the Phase-B archive."""
    events, retired, nid, week_seq = _load()
    lclient = llm.make_client("anthropic", model)          # scout/matcher/agents (web search OFF)
    raw = anthropic.Anthropic()                            # gather (web search — Anthropic only)
    cap = capture if capture is not None else {}
    arts = forward_gather.gather(raw, model, anchor, rebalance_days, capture=cap)
    print(f"  gather: {len(arts)} in-window articles; events held={sum(1 for e in events.values() if e['status']=='live')}",
          flush=True)

    # ---- scout: discover NEW early gems from the gathered pool ----
    rmem = _retired_block(retired, week_seq, curator_memory_weeks)
    cands = agent.scout(lclient, anchor, arts, retired=rmem)

    # ---- deterministic same-ticker guard + LLM matcher for genuinely new tickers ----
    held = {v: eid for eid, ev in events.items() if ev["status"] == "live" for v in ev["vehicles"]}
    new_cands = [c for c in cands if c["ticker"] not in held]
    match = agent.match_to_events(lclient, anchor, new_cands, events) if new_cands else {}
    for c in new_cands:
        tk, eid = c["ticker"], match.get(c["ticker"], "new")
        peers = {p.strip().upper() for p in c.get("peers", [])
                 if p.strip() and "." not in p and p.strip().upper() != tk}
        if eid in events and events[eid]["status"] == "live":
            events[eid]["vehicles"] |= {tk, *peers}
        else:
            nid += 1
            events[f"ev{nid}"] = {"id": f"ev{nid}", "catalyst": c["thesis"],
                                  "status": "live", "vehicles": {tk, *peers}, "entries": []}
    agent._consolidate_events(events)

    # ---- event agents: each live event re-reads its journal + its filtered news -> hold/exit ----
    live_events = [ev for ev in events.values() if ev["status"] == "live"]

    def work(ev):
        return ev, agent.event_agent_v2(lclient, anchor, ev, ev["entries"], agent._filter_event(arts, ev))

    picks: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for ev, entry in (ex.map(work, live_events) if live_events else []):
            ev["entries"].append(entry)
            ev["status"] = "live" if entry["thesis_live"] else "exited"
            if entry.get("catalyst_resolved"):                  # remember so the scout won't re-chase
                for tk in ev["vehicles"]:
                    retired[tk] = (f"{ev['catalyst']} (resolved {anchor.date()})", week_seq)
            if entry["thesis_live"]:
                for tk in entry["vehicles"]:
                    picks.append({"ticker": tk, "thesis": ev["catalyst"], "thesis_live": True,
                                  "conviction": entry.get("conviction", 5),
                                  "evidence_urls": entry.get("sources", [])})

    _save(events, retired, nid, week_seq + 1)
    return picks
