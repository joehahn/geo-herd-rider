# Per-event agent loop — design sketch (draft)

The next architecture step: replace the single weekly scan with **scout → per-event agents →
joint optimize**. Motivated by the seed decomposition (retrieval, not reasoning, is the wall):
a per-event agent attacks retrieval directly (targeted search for *its* event's early + ongoing
coverage) and carries a journal (continuity + exit calls). Discovery stays aggregate; sizing stays
mechanical. To be built as an **optional harness variant** and A/B'd against the single-scan
baseline — kept only if the scoreboard says it pays.

## State — one journal per event
`data/events/<event_id>.json` (append-only weekly entries; the journal IS the agent's memory):

```json
{
  "event_id": "ev_iran_hormuz",
  "tickers": ["BWET"],
  "thesis": "Iran war spikes tanker freight rates",
  "discovered": "2026-02-20",
  "status": "live",                      // live | exited
  "entries": [
    {"date": "2026-02-20",
     "assessment": "First press naming BWET as a tanker-rate play; war just opened, rates spiking. Early, under-owned.",
     "maturity": "early",               // early | building | consensus | crested  (INFO)
     "thesis_live": true,               // the hold/exit switch (catalyst active?)
     "exit_advice": "Hold while Hormuz disruption persists; exit on ceasefire / rates rolling over.",
     "news_claims": "press cites ~240% YTD",   // ATTRIBUTION ONLY — never our own forecast, never feeds sizing
     "sources": [{"title": "...", "url": "...", "date": "2026-02-16"}]}
  ]
}
```

## The weekly loop (cadence = `rebalance_days`)
```
for each weekly anchor:
  # 1. SCOUT (aggregate, 1 call) — discovery MUST read the whole firehose
  candidates = scout(firehose_window, trump_posts)        # [{ticker, thesis, why_now}]
  candidates = dedup_against(open_events)                  # don't re-open tracked events

  # 2. FAN-OUT (parallel, 1 agent per event) — open events + new candidates
  events = open_events ∪ candidates
  parallel for ev in events:
      news   = targeted_retrieve(ev.query_terms, before=anchor)   # THIS event's coverage, date-bounded
      prior  = read_journal(ev)                                   # memory
      entry  = event_agent(ev, prior, news)                       # writes assessment+maturity+thesis_live+exit+sources
      append_journal(ev, entry)

  # 3. CONSOLIDATE — sticky hold, now journal-driven
  watchlist = [ev.tickers for ev in events if ev.latest.thesis_live]   # + hysteresis from _stateful_watch

  # 4. OPTIMIZE (joint, after all agents) — unchanged
  weights = optimized_weights(watchlist, panel, anchor, fm)           # mean-variance; LLM never sizes
```

## Wiring into existing code
- **Reuse:** `scan_anchors` (cadence), the GDELT pool / seeds / forward `web_search` (retrieval),
  `curator._optimized_weights` (sizing), `_stateful_watch` (hysteresis over journal `thesis_live`),
  `score` (prices), `costs` (ledger), the dashboard (add per-event journal pages + hotlinks).
- **New:** `src/agent.py` — `scout()` and `event_agent()`; an event/journal store (`data/events/*.json`);
  `targeted_retrieve(query_terms, before_date)` (forward: live `web_search`/Tavily; backtest: the
  GDELT pool filtered to the event + any seeds). The backtest runs scout+fan-out at each anchor in
  place of the single `scan_fixture`.
- **Harness A/B:** add `variant="agent"` to `run_harness.py`; compare recall / precision / tail /
  capture against the single-scan baseline and the seeded run.

## What it fixes (mapped to the decomposition)
- **Entry retrieval** (0%→92% gap): targeted search digs for the early under-the-radar naming the
  broad firehose misses. Clean **forward** ("search this event now"); in backtest, still seed/date-bounded.
- **Hold retrieval** (niche names dropped ~4wk): the agent keeps pulling *its* event's coverage, so
  it doesn't go stale and get cut before the run completes → captures more of the move.
- **Precision** (27%, lots of noise): each candidate gets a dedicated verify ("is this a real
  thesis-driven gem, or thematically-adjacent noise?") → fewer false positives.
- **Continuity:** the journal carries the thesis forward → steadier exit timing, auditable, hotlinked.

## Cost
~`N+1` LLM calls per week (1 scout + N≈3–8 event agents) vs 1 for the single scan. Backtest over
~198 weeks ≈ 5–8× the single-scan cost (~$25–40). Gate on the harness before it becomes default.

## Non-negotiable guardrails (carried forward)
- **Never forecast how HIGH; DO judge when to EXIT.** The LLM must not predict magnitude / a price
  target (that destroyed value in prior work, and no number ever feeds sizing — sizing is mechanical).
  But it *should* use common sense to judge **when to exit** — i.e. when the catalyst resolves (BWET:
  a ceasefire is signed and shipping resumes through Hormuz). "How long / when to exit" is an
  allowed, qualitative, catalyst-driven judgment (it IS the `thesis_live` / `exit_advice` call);
  "how high" is forbidden. Any magnitude in the journal is only *attribution of what the news claims*.
- **Look-ahead.** Targeted search is clean only forward; backtest uses date-bounded/seeded retrieval
  and remains an upper bound. The forward eval is the verdict.
- **Discovery first.** Can't target-search an undiscovered event → scout (aggregate) precedes fan-out.
