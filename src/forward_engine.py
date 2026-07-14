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
from pathlib import Path

import anthropic
import pandas as pd

import agent
import forward_gather
import forward_gather_tavily
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


def run_week(anchor: pd.Timestamp, event_model: str, rebalance_days: int,
             curator_memory_weeks: int = 8, workers: int = 8, capture: dict | None = None,
             news_cap: int = 0, gather_engine: str = "both",
             pool: list | None = None, scout_model: str | None = None,
             scout_provider: str = "anthropic", gather_model: str | None = None,
             event_provider: str = "anthropic") -> list[dict]:
    """Run one live event-first week: gather -> scout -> match -> event agents -> save journal.
    Returns this week's picks (the live watchlist). `capture` (if given) is filled with the gather's
    raw queries+results for the Phase-B archive.

    Three-tier model split (all decoupled):
      * gather_model — the live web-search firehose (Anthropic-only). Defaults to event_model.
      * event_model/event_provider — the per-event judgment agents; reads the gathered pool with NO
        web search, so ANY provider works.
      * scout_model/scout_provider — the cheap scout+matcher; ANY provider (falls back to the event
        client). A single-model caller (event_model only) preserves the pre-split behavior byte-for-byte."""
    events, retired, nid, week_seq = _load()
    gather_model = gather_model or event_model             # gather does web search -> must be Anthropic
    eclient = llm.make_client(event_provider, event_model)   # judgment (event agents); any provider
    sclient = llm.make_client(scout_provider, scout_model) if scout_model else eclient   # cheap scout+matcher
    cap = capture if capture is not None else {}
    if pool:                                               # pre-accumulated daily pulls -> use as-is (no weekly gather)
        arts = pool
        cap.setdefault("arts", arts)
        cap.setdefault("queries", [])
        cap.setdefault("results", [])
    elif gather_engine == "tavily":                          # opt-in: date-honoring live search (reaches old weeks)
        arts = forward_gather_tavily.gather(None, gather_model, anchor, rebalance_days, capture=cap, cap=news_cap)
    elif gather_engine == "both":                            # UNION: Anthropic (etf.com) + Tavily (Dow Jones)
        acap, tcap = {}, {}
        a_arts = forward_gather.gather(anthropic.Anthropic(), gather_model, anchor, rebalance_days, capture=acap, cap=news_cap)
        t_arts = forward_gather_tavily.gather(None, gather_model, anchor, rebalance_days, capture=tcap, cap=news_cap)
        arts = forward_gather.merge_pools(a_arts, t_arts)
        cap.setdefault("arts", arts)
        cap.setdefault("queries", (acap.get("queries") or []) + (tcap.get("queries") or []))
        cap.setdefault("results", (acap.get("results") or []) + (tcap.get("results") or []))
    else:                                                  # default: Anthropic/Brave adaptive web search
        raw = anthropic.Anthropic()                        # gather (web search — Anthropic only)
        arts = forward_gather.gather(raw, gather_model, anchor, rebalance_days, capture=cap, cap=news_cap)
    print(f"  gather: {len(arts)} in-window articles; events held={sum(1 for e in events.values() if e['status']=='live')}",
          flush=True)

    # ---- the SHARED event-first engine (the SAME code the backtest runs) — scout -> match -> agents ----
    picks_full, nid = agent.process_week(eclient, anchor, arts, events, retired, nid, week_seq,
                                         curator_memory_weeks=curator_memory_weeks, workers=workers,
                                         scout_client=sclient)
    _save(events, retired, nid, week_seq + 1)
    # forward logs only the LIVE picks. milestones + exit_advice are carried so the weekly max_agents
    # PICKER (src/picker.py) has the catalyst-arc evidence it ranks on at --report time.
    return [{"ticker": p["ticker"], "thesis": p["thesis"], "thesis_live": p["thesis_live"],
             "conviction": p["conviction"], "evidence_urls": p["evidence_urls"],
             "milestones": p.get("milestones", ""), "exit_advice": p.get("exit_advice", "")}
            for p in picks_full if p["thesis_live"]]
