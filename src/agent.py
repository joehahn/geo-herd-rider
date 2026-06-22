"""agent.py — the per-event agent loop: scout -> fan-out -> (journal) -> picks.

The variant the harness A/Bs against the single-scan baseline. Each weekly anchor:
  1. SCOUT (one aggregate call) reads the week's firehose and proposes candidate events.
  2. FAN-OUT: for every open event + new candidate, a per-event agent reads its prior journal
     entry (memory) + this week's news targeted to that event, then writes a new entry — an
     assessment, maturity tag, the thesis_live/exit call, and hotlinked sources.
  3. The live events' tickers become the week's picks (same shape the backtest/optimizer expects).

Journals are the agent's memory and carry the thesis forward (continuity -> steadier exits). In
backtest they live in memory and are dumped at the end (data/windows/agent_journals.json); in
forward they'd be per-event files + dashboard pages.

GUARDRAIL: the LLM never forecasts HOW HIGH (magnitude/target — never feeds sizing, which is
mechanical). It DOES judge WHEN TO EXIT — when the catalyst resolves (the thesis_live call). See
agent_design.md.

Look-ahead: backtest retrieval is the date-bounded GDELT pool (+ seeds) filtered to each event;
targeted live search is clean only forward. All backtest numbers are upper bounds.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
import costs  # noqa: E402
import gdelt as gd  # noqa: E402
import firehose  # noqa: E402
from util import scan_anchors  # noqa: E402

CANDIDATE_CAP = 6        # max candidate events the scout proposes per week (bound the fan-out)
WINDOW_CAP = 80          # max firehose headlines shown to the scout per week

SCOUT_SYSTEM = """You are a markets desk scanning a week of financial-news headlines to DISCOVER
candidate hidden-gem events — a specific US-listed ticker (incl. ADRs / theme ETFs) the press is
naming as a thesis-driven mover, ideally still early/under-the-radar. Be selective: propose only
the few clearest (0-3), skip names merely mentioned in passing. Prefer the PUREST vehicle for a
theme (a rate/commodity ETN or clean pure-play over diluted operators; a single ADR over a broad
ETF). You forecast NOTHING. Output ONLY JSON: {"candidates":[{"ticker":"BWET","thesis":"<=12
words: the catalyst","why_now":"<=12 words"}]}. Empty is fine."""

AGENT_SYSTEM = """You manage ONE event for an event-driven book. You are given the event, YOUR
prior weekly note (your memory), and THIS week's news for this event. Write the new weekly note.

Decide:
  thesis_live  — TRUE while the driving CATALYST is still active/unresolved; FALSE once it RESOLVES
                 (ceasefire signed and shipping resumes, chokepoint reopens, the supply shock ends,
                 the policy passes/fails). This is the HOLD/EXIT switch. Use common sense about WHEN
                 the event is over — that is your job. Mainstream hype ("up 600%, everyone in") is
                 CROWDING, not resolution; do NOT exit on crowding alone.
  maturity     — early | building | consensus | crested  (INFO only).
  exit_advice  — <=20 words: the concrete condition that would end the thesis.
  assessment   — <=40 words: what changed this week and your read, continuous with your prior note.
  news_claims  — OPTIONAL <=12 words: attribute any size/return figure to the PRESS ("press cites
                 ~600% YTD"). NEVER your own price target or magnitude forecast — you do not predict
                 how high it goes.

Output ONLY JSON: {"thesis_live":true,"maturity":"early|building|consensus|crested","exit_advice":
"...","assessment":"...","news_claims":"","sources":["url","url"]}."""


def _extract(text: str) -> dict:
    t = text.strip()
    if "```" in t:
        for c in reversed(t.split("```")):
            c = c.strip()
            c = c[4:].strip() if c.startswith("json") else c
            if c.startswith("{"):
                return json.loads(c)
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e > s:
        return json.loads(t[s:e + 1])
    return {}


def _block(arts: list[dict]) -> str:
    return "\n".join(f"[{a.get('published_date','')} | {a.get('source','')}] {a.get('title','')}"
                     f" — {a.get('snippet','')[:200]} ({a.get('url','') or 'no url'})" for a in arts)


def scout(client, model: str, anchor: pd.Timestamp, arts: list[dict]) -> list[dict]:
    if not arts:
        return []
    user = (f"Week ending {anchor.date()}. Headlines:\n\n{_block(arts)}\n\n"
            "Which tickers is the press naming as thesis-driven movers? Output the JSON.")
    r = client.messages.create(model=model, max_tokens=1200, system=SCOUT_SYSTEM,
                               messages=[{"role": "user", "content": user}])
    costs.record("agent", model, f"scout-{anchor.date()}", costs.extract(r.usage))
    cands = _extract("".join(b.text for b in r.content if b.type == "text")).get("candidates", [])
    out = []
    for c in cands[:CANDIDATE_CAP]:
        tk = str(c.get("ticker", "")).strip().upper()
        if tk:
            out.append({"ticker": tk, "thesis": c.get("thesis", ""), "why_now": c.get("why_now", "")})
    return out


def _targeted(arts: list[dict], event: dict) -> list[dict]:
    """Backtest 'targeted retrieval': this event's coverage = articles naming its ticker or
    sharing thesis keywords. (Forward, this is a live web_search for the event's terms.)"""
    tk = event["ticker"].lower()
    kws = [w for w in event.get("thesis", "").lower().replace(",", " ").split() if len(w) > 4]
    hits = []
    for a in arts:
        hay = (a.get("title", "") + " " + a.get("snippet", "")).lower()
        if tk in hay or any(k in hay for k in kws):
            hits.append(a)
    return hits[:20]


def event_agent(client, model: str, anchor: pd.Timestamp, event: dict, prior: dict | None,
                news: list[dict]) -> dict:
    pj = json.dumps(prior, default=str) if prior else "(none — this is the first week)"
    nb = _block(news) if news else "(no fresh coverage for this event this week)"
    user = (f"Event: {event['ticker']} — {event.get('thesis','')}\nWeek ending {anchor.date()}.\n"
            f"Your prior note: {pj}\n\nThis week's news for this event:\n{nb}\n\nWrite the new note (JSON).")
    r = client.messages.create(model=model, max_tokens=900, system=AGENT_SYSTEM,
                               messages=[{"role": "user", "content": user}])
    costs.record("agent", model, f"agent-{event['ticker']}-{anchor.date()}", costs.extract(r.usage))
    d = _extract("".join(b.text for b in r.content if b.type == "text"))
    return {"date": anchor.date().isoformat(),
            "thesis_live": bool(d.get("thesis_live", True)),
            "maturity": d.get("maturity", "?"), "exit_advice": d.get("exit_advice", ""),
            "assessment": d.get("assessment", ""), "news_claims": d.get("news_claims", ""),
            "sources": [u for u in (d.get("sources") or []) if u][:6]}


def run_agent_scans(start, end, rebalance_days, model, workers, queries=None, seed=None,
                    pool_chunk_days=90, pool_per=150) -> dict:
    """Scout -> per-event fan-out across the window. Returns {anchor: [picks]} like the single
    scan, so backtest()/scoring are unchanged. Weeks run SEQUENTIALLY (journals are stateful);
    the fan-out within a week runs in parallel."""
    import anthropic
    import hashlib
    client = anthropic.Anthropic()
    anchors = scan_anchors(start, end, rebalance_days)
    qs = queries or firehose.GDELT_QUERIES
    win_start = anchors[0] - pd.Timedelta(days=35)
    key = hashlib.md5(f"{qs}{win_start.date()}{anchors[-1].date()}{pool_chunk_days}{pool_per}".encode()).hexdigest()[:10]
    cache_f = REPO_ROOT / "data" / "windows" / f"gdelt_pool_{key}.json"
    cache_f.parent.mkdir(parents=True, exist_ok=True)
    print(f"Agent: scout->fan-out over {len(anchors)} weeks; pool fetch/resume ...", file=sys.stderr)
    gpool = gd.pool(qs, win_start, anchors[-1], chunk_days=pool_chunk_days, per=pool_per,
                    cache_path=str(cache_f))
    seeds = firehose._fixture_articles(seed) if seed else []
    print(f"  pool {len(gpool)} + {len(seeds)} seeds; running agents ...", file=sys.stderr)

    journals: dict[str, dict] = {}   # ticker -> {ticker, thesis, status, entries:[]}
    out: dict[pd.Timestamp, list[dict]] = {}
    for a in anchors:
        win = (firehose._window(seeds, a, rebalance_days)
               + sorted(firehose._window(gpool, a, rebalance_days),
                        key=lambda x: x.get("published_date", ""), reverse=True)[:WINDOW_CAP])
        cands = scout(client, model, a, win)
        open_ev = [{"ticker": t, "thesis": j["thesis"]} for t, j in journals.items()
                   if j["status"] == "live"]
        seen = {e["ticker"] for e in open_ev}
        events = open_ev + [c for c in cands if c["ticker"] not in seen]

        def work(ev):
            j = journals.get(ev["ticker"])
            prior = j["entries"][-1] if j and j["entries"] else None
            return ev, event_agent(client, model, a, ev, prior, _targeted(win, ev))

        picks = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for ev, entry in ex.map(work, events) if events else []:
                j = journals.setdefault(ev["ticker"], {"ticker": ev["ticker"],
                                                        "thesis": ev["thesis"], "status": "live",
                                                        "entries": []})
                j["entries"].append(entry)
                j["status"] = "live" if entry["thesis_live"] else "exited"
                picks.append({"ticker": ev["ticker"], "thesis": ev["thesis"],
                              "thesis_live": entry["thesis_live"], "crowding": entry["maturity"],
                              "evidence_urls": entry["sources"]})
        out[a] = picks
    (REPO_ROOT / "data" / "windows" / "agent_journals.json").write_text(
        json.dumps(list(journals.values()), indent=2, default=str))
    return out
