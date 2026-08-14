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
event_agent_model: deepseek4      # CLOSES events. Once per live event per scan: still live? catalyst resolved?
                                  #   which tickers? Decides how long the book holds things.

# ---------- CURATOR: what gets discovered, and when it is dropped ----------
discovery_filter: true            # gate the SCOUT to headlines carrying the gem tell (superlative + under-the-radar
                                  #   framing). Event agents still read the full corpus, so an event's ordinary
                                  #   follow-up coverage is never withheld from the agent tracking it.
news_lookback_days: 30            # trailing days of news each scan READS -- DECOUPLED from the trading
                                  #   cadence (rebalance_period: weekly). 0 would follow the cadence, and at
                                  #   weekly that gives each article exactly ONE scan before it ages out for
                                  #   good; 30 keeps it readable across ~4 scans, so an article published on a
                                  #   scan boundary or indexed late is still seen. Trades stay WEEKLY.
event_news_cap: 20                # articles each event-agent re-reads per scan. Raising it costs ~13% per 20.
max_new_events: 0                 # new events ADMITTED per scan; 0 = uncapped. Superseded by max_events: an admission
                                  #   cap bins candidates unexamined and forever, a concurrency cap keeps them rankable.
max_events: 8                     # how many events may be LIVE AT ONCE. When it binds, the lowest-ranked are
                                  #   retired -- ranked by PRESS COVERAGE (src/evscore.py): independent-source
                                  #   breadth, superlative count, coverage velocity, author breadth. No forecast.
picker_model:                     # BLANK = use the arithmetic coverage-rank (src/evscore.py). An LLM ranker
                                  #   has failed to beat its own null three times here. Set to a STRONG model only
                                  #   to re-test that.  # ranks live events by catalyst ARC (early/building over crested) and emits an ordered
                                  #   keep-list only -- never weights or returns. MUST be a strong model: sonnet5 hit the
                                  #   83rd percentile, a cheap picker came in BELOW random. ~1 call/scan.
exit_patience_scans: 2            # drops a TICKER after this many consecutive "thesis is dead" reads, avoids one bad week closing a good thesis.
max_stale_scans: 2                # drops a TICKER after this many scans with NO coverage at all.
max_event_scans: 12               # retires the whole EVENT at this age (~1 year of monthly scans). 

# ---------- OPTIMIZER: what gets funded, and how much ----------
initial_investment_usd: 50000     # day-0 dollars.
starter_watchlist: [AAPL, GOOGL, AMZN]   # day-0 holdings, equal weight, until the curator's own picks replace them.
always_include: [SPY, BIL]        # always available to the optimizer; idle cash parks here. Outside max_watchlist.
max_watchlist: 6                  # how many tickers may hold capital at once.
cull_fresh_slots: 3               # of those slots, how many are held for brand-new events, which have no price history yet for "trend" to judge.
cull_fresh_scans: 2               # how new counts as new, in scans.
drop_unfunded_weeks: 4            # scans a name can go unfunded before it is dropped from the watchlist.
unfunded_reentry_on_new_catalyst: true   # lets a dropped name back in, but ONLY when the press names it under a DIFFERENT thesis.
concentration_cap: 0.40           # most of the book any one ticker may take.
min_trade_size: 0.20              # positions smaller than this are dropped. At max_watchlist 6 an equal book
                                  #   is 16.7% a name, so this is a CONCENTRATION lever, not a dust filter:
                                  #   it holds only the strongest 2-3 convictions.
risk_aversion: 4.0                # λ in mean-variance. Higher = spreads wider, chases returns less.
optimizer_lookback_days: 45       # days of price history behind μ and Σ.
rebalance_period: weekly         # weekly | biweekly | monthly | quarterly. The trading cadence.
t_update_days: 1                  # trading days between the signal and the trade.
risk_free_rate: 0.04              # Sharpe reporting only; not in the weighting.

# ---------- SOURCES: which outlets the forward gather prefers and avoids ----------
specialty_allow:                  # GEM pass allowlist: specialty desks that carry the early gem call
  # generalist stock/ETF desks (all sectors):
  - etf.com
  - benzinga.com
  - seekingalpha.com
  - etftrends.com
  - stocktitan.net
  - tipranks.com
  - barchart.com
  - zerohedge.com                 # macro/markets commentary (added 2026-07-14 per request); wide-reach, contrarian
  - semianalysis.com
  - spacenews.com
  - payloadspace.com
  - therobotreport.com
  - endpts.com
  - statnews.com
  - biopharmadive.com
  - quantumcomputingreport.com
  - world-nuclear-news.org
  - breakingdefense.com
  - defensenews.com
  # maritime + commodities specialty desks (surfaced the early BWET-tanker + gold theses in the backtest):
  - seatrade-maritime.com
  # commodities / critical minerals -- the rare-earth + uranium beats are the top evidence producers
  # and had 0 and 1 desks. mining.com scores 347 evidence-hits/1k articles, ~2x benzinga.
  - mining.com
  - northernminer.com
  - argusmedia.com
  - benchmarkminerals.com          # lithium/battery price authority; not crawled by GDELT, forward-only
  # memory / semis pricing (semianalysis does analysis, not prices):
  - digitimes.com
  - trendforce.com                 # DRAM/NAND price authority; not crawled by GDELT, forward-only
  # power grid / datacenter energy -- an uncovered sector the AI-datacenter theme keeps hitting:
  - utilitydive.com
  - powermag.com
  # tanker/shipping desks (seatrade-maritime is conference-focused):
  - splash247.com                  # not crawled by GDELT, forward-only
mill_block:                       # COVERAGE pass blocklist: "N stocks to buy" listicle mills
  - fool.com
  - 247wallst.com
  - nerdwallet.com
  - kiplinger.com
  - money.usnews.com
  - stockstory.org
  - defenseworld.net              # automated aggregator / content farm (122 low-quality hits in the backtest)
  - ts2.tech                      # AI-generated content farm
  - marketbeat.com                # 64% automated boilerplate (13F churn / consensus ratings / moving-avg crosses)
  # MarketBeat-network syndication clones of the above -- same bot templates, different masthead. A/B'd
  # 2026-08-07 on a 14d GKG pool: 374 articles (19.8% of the pool) and a hand read of a random sample
  # found ZERO pieces of real reporting. Title patterns alone left 66-72% of them standing (the bots have
  # unbounded variants), so the domain block is what clears them. insidermonkey.com and financialcontent.com
  # were candidates in the same A/B and are deliberately NOT blocked -- they carry genuine catalyst
  # reporting that names tickers, which is exactly what the firehose is for.
  - tickerreport.com
  - dailypolitical.com
  - themarketsdaily.com
  # MarketBeat-network clones (2026-08-10): 13,447 articles = 10.5% of the 3-year pool, 100% bot
  # templates (13F position changes, "Stock Price Down 8.3%"), 13.8 curator-evidence hits per 1k vs
  # benzinga's 179. Blocking costs 1.8% of evidence.
  - wkrb13.com
  - modernreaders.com
  - theenterpriseleader.com
  - etfdailynews.com
  # Motley Fool international editions -- foreign-exchange listicles, no US-listed relevance.
  # fool.co.uk produced ZERO curator evidence in 3 years. fool.ca is deliberately NOT blocked (small
  # volume, 81.6/1k, covers US-listed names).
  - fool.com.au
  - fool.co.uk

# ---------- forward-only retrieval/operational knobs ----------
cull_rank: trend                  # trend = trailing risk-adjusted return + freshness reserve; keep-first = legacy alphabetical
curator_memory_weeks: 4           # SCANS of resolved catalysts the scout is reminded of; 0 = off, <0 = all
event_agent_effort: high           # keep FULL reasoning for the live forward candidate (quality). (forward_engine
gather_model: sonnet5              # FIREHOSE stage (live web-search gather). Web search is Anthropic-ONLY,
news_cap: 0                       # articles the scout reads per scan; 0 = UNCAPPED (the daily --pull always is).
                                  #   Was 500, which silently defeated news_lookback_days: 30 -- measured
                                  #   2026-08-14, a 30d window holds 1,676 articles and the cap truncated it to
                                  #   the newest 500, i.e. back to a ~5-day window (08-09..08-13), cutting what
                                  #   reached the scout from 149 to 38. discovery_filter is the real read budget
                                  #   now (~9% of the pool carries the gem tell), so a second cap only re-narrows
                                  #   the window we just widened. Matches the backtest, which is uncapped.
picker_effort: high               # forward = 1 picker call/week, trivial cost, so keep full reasoning (its likely only edge).
relevance_filter: false                     # OFF: the forward's search index already does this, so the stage is inert relevance filter at pool assembly, standing in for the forward's
relevance_keep: 0                         # SAFETY CEILING on the filtered pool; 0 = none (intended)
unfunded_cooldown_weeks: 0        # scans after a prune before a name is eligible again; 0 = never (release on evidence)

---
