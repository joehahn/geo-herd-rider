---
# ==========================================================================
# FROZEN FORWARD / PRODUCTION CONFIG — the live candidate under forward test.
#   * forward.py reads THIS file (falls back to investor_profile.backtest.md if absent).
#   * Keep STRATEGY knobs synced with investor_profile.backtest.md so the backtest stays a valid
#     proxy; RETRIEVAL-OPERATIONAL knobs (news_cap, gather_model, retrieval_engine) may differ.
#   * Changing any value = re-freezing a NEW candidate. Log it below and treat forward results
#     before/after as DIFFERENT SEGMENTS. Do NOT tune to backtest signal (CLAUDE.md #5/#6).
#
# Re-freeze log (dated discontinuities):
#   2026-07-07  seeded as a copy of the backtest candidate (cap 1.0 · risk 0.1 · 7/5/5 · sonnet5)
#   2026-07-10  `model` split into event_agent_model (sonnet5) + scout_model (llama4); window_cap -> news_cap
#   2026-07-12  3-knob split: gather_model broken out (the only Anthropic-only stage). NOT a
#               discontinuity — same models, byte-identical candidate.
#   2026-08-09  risk_aversion 0.1->1.0, lookback 14->45, concentration_cap 1.0->0.667, min_trade_size
#               0.0->0.1 promoted from the backtest; max_agents 5->7, max_new_events 2->3 adopted here.
#   2026-08-09  always_include / starter_watchlist / max_watchlist adopted from PWR; BIL is new, so
#               idle capital gains a zero-vol home. Unfunded prune ON (drop_unfunded 3, then 2 at
#               biweekly) with information-gated re-entry.
#   2026-08-09  rebalance_days 7 -> rebalance_period: biweekly. The *_scans knobs were halved with it
#               to hold their real-time horizons constant — a coherence fix, not a strategy change.
#               STRATEGY knobs (concentration_cap, risk_aversion, min_trade_size, max_watchlist) are
#               deliberately LEFT FROZEN while the 3-year backtest sweeps them, so the two profiles
#               are KNOWINGLY out of sync on those until that sweep promotes a dated re-freeze.
#   2026-08-14  BOOTSTRAP RE-FREEZE. Wholesale copy of investor_profile.backtest.md's strategy knobs,
#               resolving the 8-knob drift that had accumulated (risk_aversion 1.0->4.0,
#               discovery_filter false->true, max_events 0->8, drop_unfunded 2->4, min_trade_size
#               0.1->0.2, always_include GLD->BIL, event_agent_model sonnet5->deepseek4). The
#               optimizer six are the cross-curation-robust cell [6, 0.40, 45, 4, 4.0, 0.20]; see the
#               backtest profile's header for how it was chosen and why single-sweep fitting failed.
#               EXCEPTION: rebalance_period is WEEKLY, deliberately out of sync with the backtest's
#               monthly, to exercise the rebalance machinery often during the bootstrap smoketest.
#               That cadence was NEVER SWEPT (rebalance_period is not in the grid), so CBS performance
#               is not evidence for the config -- it is a MECHANICS test. Dial back to monthly if the
#               smoketest gives no reason to keep it.
#               Kept forward-side (retrieval-operational): gather_model (Anthropic-only, the live
#               web-search stage), news_cap, and the effort/relevance knobs the backtest file lacks.
#               retrieval_engine is deliberately ABSENT: it is the backtest's gkg selector, and
#               firehose.py would otherwise read 'gkg' for a web-search run.
#               Sync with the backtest is a GOAL, not a gate, while this is in development.
#   2026-08-14  news_lookback_days 0 -> 30 with rebalance_period held at WEEKLY: the news window is now
#               DECOUPLED from the trading cadence. optimizer.py documented this as live behaviour all
#               along, but only firehose.py (the backtest) implemented it -- forward.py used the cadence,
#               so the knob was a silent no-op until wired here today. At weekly+cadence-window an article
#               is visible to exactly ONE scan then ages out; at 30d it stays readable across ~4.
#               news_cap 500 -> 0 in the same breath: the cap truncated the 30d window (1,676 articles)
#               back to the newest 500, a ~5-day window, cutting scout intake from 149 to 38 and undoing
#               the widening. discovery_filter (~9% pass) is the read budget now, as in the backtest.
# ==========================================================================

# ---------- AI MODELS: who does what, and what it costs ----------
scout_model: llama4               # OPENS events. Reads the whole week's news (~1,500 headlines in ~10 chunked
                                  #   calls) and proposes ticker + catalyst. Also runs the matcher, ticker guard
                                  #   and relevance filter. ~90% of the AI bill, so keep it cheap.
event_agent_model: grok4          # CLOSES events. Once per live event per scan: still live? catalyst resolved?  
                                  #   which tickers? Decides how long the book holds things.

# ---------- CURATOR: what gets discovered, and when it is dropped ----------
discovery_filter: true            # gate the SCOUT to headlines carrying the gem tell (superlative + under-the-radar
                                  #   framing). Event agents still read the full corpus, so an event's ordinary
                                  #   follow-up coverage is never withheld from the agent tracking it.
news_lookback_days: 30            # trailing days of news each scan READS -- DECOUPLED from the
                                  #   cadence, so a weekly scan still reads a MONTH of news and a boundary-
                                  #   straddling article is not lost to the gap.
event_news_cap: 20                # articles each event-agent re-reads per scan. Raising it costs ~13% per 20.
max_new_events: 0                 # new events ADMITTED per scan; 0 = uncapped. Superseded by max_events: an admission
                                  #   cap bins candidates unexamined and forever, a concurrency cap keeps them rankable.
max_events: 0                     # 0 = UNCAPPED. How many events may be LIVE AT ONCE; when it binds, lowest-ranked are 
                                  #   retired -- ranked by PRESS COVERAGE (src/evscore.py): independent-source
                                  #   breadth, superlative count, coverage velocity, author breadth. No forecast.
picker_model:                     # BLANK = use the arithmetic coverage-rank (src/evscore.py). An LLM ranker
                                  #   has failed to beat its own null three times here. Set to a STRONG model only
                                  #   to re-test that.  # ranks live events by catalyst ARC (early/building over crested) and emits an ordered
                                  #   keep-list only -- never weights or returns. MUST be a strong model: sonnet5 hit the
                                  #   83rd percentile, a cheap picker came in BELOW random. ~1 call/scan.
exit_patience_scans: 2            # drops a TICKER after this many consecutive "thesis is dead" reads, avoids one bad week closing a good thesis.
max_stale_scans: 32               # drops a TICKER after this many scans with NO coverage at all.
  # x4 CADENCE PORT: a forward scan is a WEEK, a backtest scan is a MONTH, so the scan-counted
                                  #   knobs are x4 to preserve the same ELAPSED behaviour. Reverted to monthly
                                  #   2026-08-25 and back to weekly the same day: weekly is kept for now so the
                                  #   bootstrap gets scans on BOTH sides of the handoff (monthly gives ONE).
                                  #   Revisit once the websearch era is a few months long.
max_event_scans: 48               # retires the whole EVENT at this age, in scans (~1 year weekly).
  # x4 CADENCE PORT: a forward scan is a WEEK, a backtest scan is a MONTH, so the scan-counted
                                  #   knobs are x4 to preserve the same ELAPSED behaviour. Reverted to monthly
                                  #   2026-08-25 and back to weekly the same day: weekly is kept for now so the
                                  #   bootstrap gets scans on BOTH sides of the handoff (monthly gives ONE).
                                  #   Revisit once the websearch era is a few months long.
initial_investment_usd: 50000     # day-0 dollars.
starter_watchlist: [AAPL, GOOGL, AMZN]   # day-0 holdings, equal weight, until the curator's own picks replace them.
always_include: [SPY, BIL]        # always available to the optimizer; idle cash parks here. Outside max_watchlist.
max_watchlist: 6                  # how many tickers may hold capital at once.
cull_fresh_slots: 3               # of those slots, how many are held for brand-new events, which have no price history yet 
cull_fresh_scans: 2               # how new counts as new, in scans. NOT x4'd (2026-08-24): this one pairs
                                  # with cull_fresh_slots to hold slots for names too NEW to have price
                                  # history. 2 WEEKLY scans is already enough history to judge; x4 would
                                  # keep a name 'new' for two months.
drop_unfunded_weeks: 0            # scans a name can go unfunded before it is dropped from the watchlist.  # SYNCED to .backtest 2026-08-24
unfunded_reentry_on_new_catalyst: true   # lets a dropped name back in, but ONLY when the press names it under a DIFFERENT thesis.
concentration_cap: 0.25           # most of the book any one ticker may take.  # SYNCED to .backtest 2026-08-24
min_trade_size: 0.20              # positions smaller than this are dropped. At max_watchlist 6 an equal book
                                  #   is 16.7% a name, so this is a CONCENTRATION lever, not a dust filter:
                                  #   it holds only the strongest 2-3 convictions.
risk_aversion: 8.0                # λ in mean-variance. Higher = spreads wider, chases returns less.  # SYNCED to .backtest 2026-08-24
optimizer_lookback_days: 21       # days of price history behind μ and Σ.  # SYNCED to .backtest 2026-08-24
rebalance_period: weekly         # weekly | biweekly | monthly | quarterly. The trading cadence.
                                  # WEEKLY 2026-08-25: monthly puts ZERO scan anchors after the handoff while the
                                  #   websearch era is only 27 days old, so the bootstrap could not see it at all.
t_update_days: 1                  # trading days between the signal and the trade.
risk_free_rate: 0.04              # Sharpe reporting only; not in the weighting.

# ---------- SOURCES: which outlets the forward gather prefers and avoids ----------
# specialty_allow: MOVED to retrieval_config.json 2026-08-25 -- an INGEST parameter,
#   read by forward_gather.py and gkg.py. See that file's _domain_steering_note.
# mill_block: MOVED to retrieval_config.json 2026-08-25 -- an INGEST parameter,
#   read by forward_gather.py and gkg.py. See that file's _domain_steering_note.
# ---------- forward-only retrieval/operational knobs ----------
cull_rank: trend                  # trend = trailing risk-adjusted return + freshness reserve; keep-first = legacy alphabetical
curator_memory_weeks: 32          # SCANS of resolved catalysts the scout is reminded of; 0 = off.
  # x4 CADENCE PORT: a forward scan is a WEEK, a backtest scan is a MONTH, so the scan-counted
                                  #   knobs are x4 to preserve the same ELAPSED behaviour. Reverted to monthly
                                  #   2026-08-25 and back to weekly the same day: weekly is kept for now so the
                                  #   bootstrap gets scans on BOTH sides of the handoff (monthly gives ONE).
                                  #   Revisit once the websearch era is a few months long.
event_agent_effort: low            # matches .backtest: Grok 4.3's reasoning knob measured as a NULL. (forward_engine  # SYNCED to .backtest 2026-08-24
gather_model: sonnet5              # FIREHOSE stage (live web-search gather). Web search is Anthropic-ONLY,
news_cap: 0                       # articles the scout reads per scan; 0 = UNCAPPED (the daily --pull always is).
                                  #   Was 500, which silently defeated news_lookback_days: 30 -- measured
                                  #   2026-08-14, a 30d window holds 1,676 articles and the cap truncated it to
                                  #   the newest 500, i.e. back to a ~5-day window (08-09..08-13), cutting what
                                  #   reached the scout from 149 to 38. discovery_filter is the real read budget
                                  #   now (~9% of the pool carries the gem tell), so a second cap only re-narrows
                                  #   the window we just widened. Matches the backtest, which is uncapped.
picker_effort: low                # matches .backtest. Was high on a cost argument, but an unmeasured quality claim.  # SYNCED to .backtest 2026-08-24
relevance_filter: false                     # OFF: the forward's search index already does this, so the stage is inert relevance filter at pool assembly, standing in for the forward's
relevance_keep: 0                         # SAFETY CEILING on the filtered pool; 0 = none (intended)
unfunded_cooldown_weeks: 0        # scans after a prune before a name is eligible again; 0 = never (release on evidence)

---
