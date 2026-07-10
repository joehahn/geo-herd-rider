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


def run_week(anchor: pd.Timestamp, model: str, rebalance_days: int,
             curator_memory_weeks: int = 8, workers: int = 8, capture: dict | None = None,
             news_cap: int = 0, gather_engine: str = "anthropic",
             pool: list | None = None, scout_model: str | None = None,
             scout_provider: str = "anthropic") -> list[dict]:
    """Run one live event-first week: gather -> scout -> match -> event agents -> save journal.
    Returns this week's picks (the live watchlist). `capture` (if given) is filled with the gather's
    raw queries+results for the Phase-B archive.

    `model` is the Anthropic event/gather model (gather does web search — Anthropic-only). The cheap
    scout+matcher use `scout_model`/`scout_provider` when given (may be any provider); else they reuse
    the event client, preserving pre-split behavior."""
    events, retired, nid, week_seq = _load()
    lclient = llm.make_client("anthropic", model)          # gather + event agents (web search = Anthropic)
    sclient = llm.make_client(scout_provider, scout_model) if scout_model else lclient   # cheap scout+matcher
    cap = capture if capture is not None else {}
    if pool:                                               # pre-accumulated daily pulls -> use as-is (no weekly gather)
        arts = pool
        cap.setdefault("arts", arts)
        cap.setdefault("queries", [])
        cap.setdefault("results", [])
    elif gather_engine == "tavily":                          # opt-in: date-honoring live search (reaches old weeks)
        arts = forward_gather_tavily.gather(None, model, anchor, rebalance_days, capture=cap, cap=news_cap)
    else:                                                  # default: Anthropic/Brave adaptive web search
        raw = anthropic.Anthropic()                        # gather (web search — Anthropic only)
        arts = forward_gather.gather(raw, model, anchor, rebalance_days, capture=cap, cap=news_cap)
    print(f"  gather: {len(arts)} in-window articles; events held={sum(1 for e in events.values() if e['status']=='live')}",
          flush=True)

    # ---- the SHARED event-first engine (the SAME code the backtest runs) — scout -> match -> agents ----
    picks_full, nid = agent.process_week(lclient, anchor, arts, events, retired, nid, week_seq,
                                         curator_memory_weeks=curator_memory_weeks, workers=workers,
                                         scout_client=sclient)
    _save(events, retired, nid, week_seq + 1)
    # forward logs only the LIVE picks, in the forward format (conviction carried through)
    return [{"ticker": p["ticker"], "thesis": p["thesis"], "thesis_live": p["thesis_live"],
             "conviction": p["conviction"], "evidence_urls": p["evidence_urls"]}
            for p in picks_full if p["thesis_live"]]
