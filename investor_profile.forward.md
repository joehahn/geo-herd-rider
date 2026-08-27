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
#               EXCEPTION (RETIRED 2026-08-26, see below): rebalance_period was WEEKLY, deliberately
#               out of sync with the backtest's monthly, to exercise the rebalance machinery often
#               during the bootstrap smoketest.
#               Kept forward-side (retrieval-operational): gather_model (Anthropic-only, the live
#               web-search stage), news_cap, and the effort/relevance knobs the backtest file lacks.
#               retrieval_engine is ABSENT -- correctly, but NOT for the reason recorded here until
#               2026-08-26. Omitting it does not stop anything reading 'gkg': that IS the default in
#               optimizer._FINANCIAL_MODEL_DEFAULTS, so load_financial_model fills it in either way.
#               It is inert on the forward path for a different reason -- firehose.pool() resolves the
#               knob against `investor_profile.backtest.md` explicitly, never this file. So the knob
#               is genuinely backtest-only and absence is right; the old justification just did not
#               hold, and a wrong reason in a config header is a trap for whoever edits it next.
#               Sync with the backtest is a GOAL, not a gate, while this is in development.
#   2026-08-14  news_lookback_days 0 -> 30 with rebalance_period held at WEEKLY: the news window is now
#               DECOUPLED from the trading cadence. optimizer.py documented this as live behaviour all
#               along, but only firehose.py (the backtest) implemented it -- forward.py used the cadence,
#               so the knob was a silent no-op until wired here today. At weekly+cadence-window an article
#               is visible to exactly ONE scan then ages out; at 30d it stays readable across ~4.
#               news_cap 500 -> 0 in the same breath: the cap truncated the 30d window (1,676 articles)
#               back to the newest 500, a ~5-day window, cutting scout intake from 149 to 38 and undoing
#               the widening. discovery_filter (~9% pass) is the read budget now, as in the backtest.
#   2026-08-26  rebalance_period WEEKLY -> MONTHLY, and the x4 CADENCE PORT UNWOUND with it:
#               max_event_scans 48->12, max_stale_scans 32->8, curator_memory_weeks 32->8,
#               news_lookback_days 30->0. The x4 existed only to preserve elapsed behaviour across a
#               cadence mismatch; with both files monthly a scan means the same thing again, so the
#               scan-counted knobs are literally equal rather than equal-after-arithmetic. That is
#               strictly safer: every x4 was a place the two files could drift apart silently, and
#               two of them already had to be exempted by hand (exit_patience_scans, cull_fresh_scans,
#               which count SLOTS/READS rather than elapsed time and were never x4'd).
#               news_lookback_days 30 -> 0 is a NO-OP TODAY: 0 tracks rebalance_period, which at
#               monthly is 30 days. It is the backtest's spelling of the same window, and it follows
#               the cadence if that ever changes instead of silently decoupling from it.
#               THE PROFILES NOW DIFFER IN EXACTLY ONE KNOB: gather_model (Anthropic-only, the live
#               web-search stage, inert in the backtest) -- i.e. the only remaining difference is one
#               the CLAUDE.md rule explicitly permits.
#               COST, measured before making the change: on the current bootstrap span
#               (2026-04-27..2026-08-25, handoff 07-28) monthly gives 5 scans of which 1 is
#               post-handoff, where weekly gave 17 of which 4. So CBS exercises the websearch era far
#               less until the post-handoff window is a few months long -- one more post-handoff scan
#               per month from here. That is the price of making the backtest a valid proxy, which is
#               the thing the forward eval actually depends on.
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
news_lookback_days: 0             # trailing days of news each scan reads. 0 = track rebalance_period,
                                  #   which at MONTHLY is 30 days -- the SAME window the explicit 30 gave,
                                  #   now expressed the way the backtest expresses it, and it follows the
                                  #   cadence if that changes again instead of silently decoupling.
event_news_cap: 20                # articles each event-agent re-reads per scan. Raising it costs ~13% per 20.
max_new_events: 0                 # new events ADMITTED per scan; 0 = uncapped. Superseded by max_events: an admission
                                  #   cap bins candidates unexamined and forever, a concurrency cap keeps them rankable.
# NOTE (2026-08-26): max_events: 0 also DISABLES src/evscore.py entirely. The concurrency cull sits
# behind `if max_events:` in agent.curate, and evscore.rank is called only inside it -- so the
# velocity/breadth ranker ("a name the press has started naming while it is still under-owned",
# weights: velocity 4.0, source_breadth 2.0) never executes at the current config. That ranker is
# the project's MEASURED definition of under-the-radar, and it is the intended partner to the
# catalyst gate in retrieval_config `discovery_catalysts`. Re-enabling it means setting max_events
# to a positive value, which is a CURATION knob and its own bake-off.
max_events: 0                     # 0 = UNCAPPED. How many events may be LIVE AT ONCE; when it binds, lowest-ranked are 
                                  #   retired -- ranked by PRESS COVERAGE (src/evscore.py): independent-source
                                  #   breadth, superlative count, coverage velocity, author breadth. No forecast.
picker_model:                     # BLANK = use the arithmetic coverage-rank (src/evscore.py). An LLM ranker
                                  #   has failed to beat its own null three times here. Set to a STRONG model only
                                  #   to re-test that.  # ranks live events by catalyst ARC (early/building over crested) and emits an ordered
                                  #   keep-list only -- never weights or returns. MUST be a strong model: sonnet5 hit the
                                  #   83rd percentile, a cheap picker came in BELOW random. ~1 call/scan.
exit_patience_scans: 2            # drops a TICKER after this many consecutive "thesis is dead" reads, avoids one bad week closing a good thesis.
max_stale_scans: 8                # drops a TICKER after this many scans with NO coverage at all.
max_event_scans: 12               # retires the whole EVENT at this age, in scans (~1 year monthly).
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
concentration_cap: 0.6             # most of the book any one ticker may take.
                                  # 0.25 -> 0.6 on 2026-08-27, from SBT table 20. It is the single
                                  # change that moves the live config into panel 19's green cluster
                                  # on its own: worst-arm percentile 73.5 -> 81.9 across THREE
                                  # independent curations (monthly / biweekly / weekly), and it
                                  # appears in eleven of the twelve top regions. Chosen over the
                                  # other one-knob move (risk_aversion 8 -> 16, worst-arm 81.7)
                                  # because that one rides the timidity tilt panel 9 documents --
                                  # Sharpe rewards a low-volatility book by construction, so part of
                                  # its gain is the metric preferring a book that does less.
                                  # A BOOK knob: rebuild only, no re-curation, fingerprint unchanged.
                                  #   Prior note: loosened from 0.25 with the move to max_watchlist 4
                                  #   -- at four names an equal book is 25% each, so a 0.25 cap would
                                  #   force exactly equal weights and give the optimizer nothing to do.

min_trade_size: 0.20              # positions smaller than this are dropped. At max_watchlist 6 an equal book
                                  #   is 16.7% a name, so this is a CONCENTRATION lever, not a dust filter:
                                  #   it holds only the strongest 2-3 convictions.
risk_aversion: 8.0                # λ in mean-variance. Higher = spreads wider, chases returns less.  # SYNCED to .backtest 2026-08-24
optimizer_lookback_days: 21       # days of price history behind μ and Σ.  # SYNCED to .backtest 2026-08-24
rebalance_period: monthly        # weekly | biweekly | monthly | quarterly. The trading cadence.
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
# NAME IS WRONG, KEPT DELIBERATELY: this counts SCANS, not weeks. agent.process_week tests
# `(week_idx - retired_idx) < curator_memory_weeks` where week_idx is the enumerate index over
# scan anchors, so the unit is whatever the cadence is -- 8 means 8 MONTHS at monthly and 8
# WEEKS at weekly. It is renamed only when a fingerprint break is acceptable: the key is a
# member of provenance.CURATION_KNOBS, so changing it rehashes every curation. See TODO.md.
curator_memory_weeks: 8           # SCANS of resolved catalysts the scout is reminded of; 0 = off.
event_agent_effort: low            # matches .backtest: Grok 4.3's reasoning knob measured as a NULL. (forward_engine  # SYNCED to .backtest 2026-08-24
gather_model: sonnet5              # FIREHOSE stage (live web-search gather). Web search is Anthropic-ONLY,
# THESE THREE ARE STATED, NOT INHERITED (2026-08-26). Each is a CURATION knob the backtest file
# states explicitly while this file used to leave it to optimizer's default -- so the two agreed only
# because the default happened to equal the backtest's value. Change the backtest and this file
# follows silently, in the one direction that matters: it changes what the CURATOR READS. Stating
# them makes a divergence show up in a diff of the two files, which is the only place anyone looks.
# All three are honoured by forward.py as of today; before that, writing them here would have been a
# claim the live path did not keep.
max_article_chars: 800            # how much of ONE article's text the curator sees. Also the cap
                                  #   lede.apply() uses, so it bounds a SEARCH SNIPPET's only copy --
                                  #   at the old 280 the curator saw 39% of what the pull paid for.
scout_articles_per_call: 30       # BATCHING ONLY: how many articles' worth of ticker-groups share one
                                  #   scout call. Never truncates a group.
min_bundle_articles: 1            # a company bundle needs >= this many articles to be shown AS a
                                  #   company bundle; under it, demoted. 1 = nothing is demoted.
news_cap: 0                       # articles the scout reads per scan; 0 = UNCAPPED (the daily --pull always is).
                                  #   Was 500, which silently defeated news_lookback_days: 30 -- measured
                                  #   2026-08-14, a 30d window holds 1,676 articles and the cap truncated it to
                                  #   the newest 500, i.e. back to a ~5-day window (08-09..08-13), cutting what
                                  #   reached the scout from 149 to 38. discovery_filter is the real read budget
                                  #   now (~9% of the pool carries the gem tell), so a second cap only re-narrows
                                  #   the window we just widened. Matches the backtest, which is uncapped.
picker_effort: low                # matches .backtest. Was high on a cost argument, but an unmeasured quality claim.  
relevance_filter: false           # OFF: the forward's search index already does this, so the stage is inert relevance filter
relevance_keep: 0                 # SAFETY CEILING on the filtered pool; 0 = none (intended)
unfunded_cooldown_weeks: 0        # scans after a prune before a name is eligible again; 0 = never (release on evidence)

---
