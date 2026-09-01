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
#   2026-09-01b SECOND re-freeze today. risk_aversion 8.0 -> 12.0, synced with .backtest so the
#               strategy knobs stay aligned (CLAUDE.md requires it, or the backtest stops being a
#               valid proxy). Basis is the TEN-curation one-knob record favouring 12.0, NOT the new
#               sweep, which splits: 12.0 wins marginal median final (+1.9%) and median sharpe
#               (0.900 vs 0.860) but LOSES final at the live config ($259,707 vs $303,642). Every
#               gap is inside the unmeasurable band. This buys sharpe and pays in final.
#   2026-09-01  DEFECT RE-FREEZE + strategy sync. min_trade_size 0.20 -> 0.0 is a BUG FIX, not a
#               preference: as a post-filter it renormalized survivors and silently undid
#               concentration_cap, so this candidate has had NO EFFECTIVE CAP since it was frozen.
#               Also synced: max_watchlist 12->20, risk_aversion 12.0->8.0, concentration_cap 0.6->0.4,
#               drop_unfunded_weeks 4->0, and two risk gates ADDED (min_dollar_volume_usd 100000,
#               exclude_young_reverse_split [3, 0.1]).
#               CAVEAT, stated so it is not lost: the four TUNED knobs (max_watchlist, risk_aversion,
#               concentration_cap, drop_unfunded_weeks) were all selected by sweeps run while the cap
#               was disabled, so their justification does not survive the fix. The 13-curation
#               re-sweep under corrected sizing may move them again -- expect a second re-freeze.
#               Treat forward results before/after this date as DIFFERENT SEGMENTS.
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
#   2026-08-30  RE-FREEZE. Seven strategy knobs synced to .backtest, at the user's explicit
#               instruction. Treat forward results before/after this date as DIFFERENT SEGMENTS.
#                 max_watchlist 6->12 · cull_fresh_slots 3->2 · drop_unfunded_weeks 0->4
#                 risk_aversion 8.0->12.0 · optimizer_lookback_days 21->30 · max_silent_scans 0->8
#               PROVENANCE, recorded honestly: every one of these is BACKTEST-DRIVEN and NONE has
#               survived a forward eval, which is the situation non-negotiable #7 exists to guard.
#               The standing recommendation was to run max_watchlist 6 vs 12 as pre-registered
#               forward ARMS rather than promote one; the user chose to sync instead. The knobs are
#               real and measured -- max_silent_scans truncates a documented 9-scan silent hold that
#               cost the book PCG; max_watchlist 6->12 rests on 6-of-9 curations at p=0.25, with
#               three curations WORSE at every width and a ~0.5x worst case -- but "measured on
#               backtests" is not "validated forward", and the next re-freeze should say which of
#               these the forward actually paid for.
#               Deliberately NOT synced (retrieval-operational, correct to differ): gather_model
#               (sonnet5, the Anthropic-only live web-search stage; FORWARD_ONLY_KNOBS) and
#               org_tagger_model (grok4; the backtest corpus is already tagged).
#
#   !! FIVE KNOBS WERE INERT ON THE LIVE SCAN PATH -- FIXED 2026-08-30, same day, see below.
#      forward_engine.run_week did not receive `fm`; it took explicit parameters and handed
#      process_week only curator_memory_weeks, workers and scout_client, so this file said one thing
#      and `forward.py --scan` ran another:
#          max_event_scans      12   -> runs 0     (age cap OFF)
#          max_silent_scans      8   -> runs 0     (silence cap OFF)
#          discovery_filter   true   -> runs false
#          max_new_events        0   -> runs 3
#          event_agent_effort  low   -> runs high
#      discovery_filter was the one that mattered: the backtest's scout reads the ~9% gem-tell slice
#      while the live forward scout read the WHOLE pool, so the two were not the same curator and
#      process_week's "byte-identical logic" docstring was false for these knobs.
#      THE BOOTSTRAP NEVER HAD THIS BUG. backtest_gdelt.py --bootstrap also reads THIS file and
#      passed the full knob set all along -- which is precisely why the gap survived unnoticed: CBS
#      rehearsed settings the live scan was ignoring.
#      FIXED by passing `fm` itself into run_week -> process_week rather than a keyword per knob;
#      a per-knob call site is what let five be dropped silently. Verified: discovery_filter true,
#      max_event_scans 12, max_silent_scans 8, max_new_events 0, event_agent_effort low now all
#      arrive. THIS IS A SECOND FORWARD DISCONTINUITY on the same day as the re-freeze above, and a
#      real one: the live scout now reads ~90% less (the gem-tell slice), the two caps begin
#      retiring live events, and effort low changes the live bill. Segment forward results
#      accordingly -- the config did not change here, but what the code obeys did.
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
max_silent_scans: 8               # retires an EVENT after this many consecutive scans whose agent entry
                                  # cites NO sources -- the mechanical signature of "no confirming news".
                                  # NEW 2026-08-30, SYNCED to .backtest. Sibling of max_event_scans below:
                                  # that retires an event that ran too LONG, this one an event that ran too
                                  # QUIET, and it fires first. It does NOT write `retired` -- silence is
                                  # absence of evidence, not evidence the thesis died, so the ticker stays
                                  # re-chaseable. Both files are rebalance_period: monthly, so 8 scans means
                                  # ~8 months in BOTH -- the knob transfers literally, no cadence arithmetic.
max_event_scans: 12               # retires the whole EVENT at this age, in scans (~1 year monthly).
initial_investment_usd: 50000     # day-0 dollars.
starter_watchlist: [AAPL, GOOGL, AMZN]   # day-0 holdings, equal weight, until the curator's own picks replace them.
always_include: [SPY, BIL]        # always available to the optimizer; idle cash parks here. Outside max_watchlist.
max_watchlist: 20                 # 12 -> 20,# SYNCED to .backtest 2026-09-01
                                  # how many tickers may COMPETE for capital at once (not how many hold it).
                                  # 6 -> 12, SYNCED to .backtest 2026-08-30. The book does NOT widen:
                                  # it funds a median of 2 names and a max of 3 at 6, 12, 20 and 32 alike,
                                  # because min_trade_size and concentration_cap fix concentration
                                  # downstream. What widens is the SLATE the optimizer picks from.
cull_fresh_slots: 2               # 3 -> 2, SYNCED to .backtest 2026-08-30.
                                  # of those slots, how many are held for brand-new events, which have no price history yet 
cull_fresh_scans: 2               # how new counts as new, in scans. NOT x4'd (2026-08-24): this one pairs
                                  # with cull_fresh_slots to hold slots for names too NEW to have price
                                  # history. 2 WEEKLY scans is already enough history to judge; x4 would
                                  # keep a name 'new' for two months.
drop_unfunded_weeks: 0            # scans a name can go unfunded before it is dropped from the watchlist.  # 4 -> 0, SYNCED to .backtest 2026-09-01
unfunded_reentry_on_new_catalyst: true   # lets a dropped name back in, but ONLY when the press names it under a DIFFERENT thesis.
min_dollar_volume_usd: 100000    # UNIVERSE FLOOR, ADDED 2026-09-01 (synced from .backtest). A name whose
                                  # TRAILING 60-day median dollar volume is under this cannot be FUNDED.
                                  # Keeps the book out of names too thin to exit. BOOK knob -- replay only.
exclude_young_reverse_split: [3, 0.1]   # [max years listed, worst reverse-split ratio]. ADDED 2026-09-01
                                  # (synced from .backtest). Refuse to FUND a name listed under 3 years that
                                  # has ALREADY executed a 1-for-10-or-worse reverse split -- the death-spiral
                                  # financing signature. [] = off. RISK GATE, NOT an alpha filter, and NOT
                                  # backtest-validated: across 843 funded positions in 12 curations it flags
                                  # exactly one (WOK, the case that generated it). It FAILS OPEN when corporate
                                  # actions are unavailable, so it is incomplete by design.
concentration_cap: 0.4             # most of the book any one ticker may take.
                                  # 0.6 -> 0.4, SYNCED to .backtest 2026-09-01. NOTE: every prior
                                  # justification for this knob below was measured while min_trade_size
                                  # was renormalizing the cap away, i.e. while the cap did not bind.
                                  # Treat the reasoning that follows as UNVERIFIED under the corrected
                                  # sizing until the 13-curation re-sweep lands.
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

min_trade_size: 0.0               # OFF. 0.20 -> 0.0, SYNCED to .backtest 2026-09-01. NOT a tuning change --
                                  #   a DEFECT FIX. It ran as a post-filter that dropped sub-floor names and
                                  #   renormalized the survivors to sum to 1, which silently UNDID
                                  #   concentration_cap. A lone survivor sitting at the cap became 100%. On the
                                  #   canonical backtest journal that put 690 of 774 days over the cap and 147
                                  #   days at 100% in one name. This candidate has been running WITHOUT AN
                                  #   EFFECTIVE CONCENTRATION CAP since it was frozen.
                                  #   As a box LOWER bound it is also the wrong shape: a lower bound applies to
                                  #   every asset, so it cannot express "hold nothing OR hold >= x" -- it either
                                  #   goes infeasible or forces names to be held at exactly the floor. At 0 the
                                  #   cap is a plain box bound and one QP solves it exactly.
risk_aversion: 12.0               # λ in mean-variance. Higher = spreads wider, chases returns less.  # 8.0 -> 12.0, SYNCED to .backtest 2026-09-01 (SECOND re-freeze today)
optimizer_lookback_days: 30       # days of price history behind μ and Σ.  # 21 -> 30, SYNCED to .backtest 2026-08-30
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
# ORG TAGGER -- FORWARD-ONLY, and deliberately absent from .backtest.md.
# Websearch articles arrive with no `orgs`; GKG articles arrive with it on 82%. Company bundling is
# seeded from `orgs`, so post-handoff 90% of what this system reads falls to the beat/orphan path
# and the scout stops seeing a firm's news as one block. Measured 2026-08-27: articles reaching a
# company bundle of 2+ run at 62.3% in the backtest era and 9.2% post-handoff; tagging takes it to
# 39.3% (an undercount -- see TODO). This knob exists to close that gap, which is why it does NOT
# belong in .backtest.md: the backtest already has GKG's orgs and IS the 62.3% baseline. Tagging it
# too would move the target rather than reach it. Retrieval-operational, like gather_model, so the
# two profiles are allowed to differ here.
# `off` (or unset) = no tagging at all, byte-identical to the behaviour before this knob existed.
# OFF 2026-08-28 after measuring it, not before. The tagger works -- 1.46 bundle memberships per
# article against GKG's 1.17, an identical 66/34 singleton-to-multi bundle split -- but the CURATION
# it produces loses to no-tagger on 5 of 6 mechanism metrics (cbs_v6 vs cbs_v8: events opened 72->64,
# distinct tickers 386->318, vehicles live 309->219). Only cull-at-birth improved, 27.8%->23.4%.
# Non-negotiable #3: a curator change is kept only if the scoreboard shows lift. This does not.
# UNRESOLVED, and the reason this is `off` rather than deleted: fewer events with a BETTER
# cull-at-birth rate may be bundling working as designed -- consolidating a firm's news into one
# bundle means one event where three were opened before. That is deduplication, which is the point.
# Whether fewer-but-better is an improvement is a P&L question and non-negotiable #6 says one
# curation cannot answer it. Revisit when the forward scoreboard can.
org_tagger_model: grok4            # ON. The tagger matches or beats GKG on every structural measure
                                   # (1.46 bundle memberships/article vs 1.17, identical 66/34
                                   # singleton-to-multi split, 17.0% vs 16.1% no-key) and 97.8% of its
                                   # names survive the GKG-era filters unchanged. Whether that improves
                                   # the BOOK is not answerable yet -- the websearch era is ~31 days old
                                   # and one curation's mechanism metrics swing 8% on identical input --
                                   # so it ships on the structural evidence and FBS tracks it daily.
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
