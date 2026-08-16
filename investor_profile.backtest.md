---
# ==========================================================================
# BACKTEST / DEV CONFIG — free to evolve. Read by backtest_gdelt.py, the sweeps and the dashboards.
# Promoting a settled candidate to the live forward test = copying the STRATEGY knobs into
# investor_profile.forward.md as a dated re-freeze (see that file's header).
#
# Order: AI models -> curator -> optimizer -> source allow/block lists.
# Any knob added here must also exist in optimizer._FINANCIAL_MODEL_DEFAULTS or it is SILENTLY
# IGNORED; load_financial_model warns about unknown keys.
#
# HOW THE SIX OPTIMIZER KNOBS BELOW WERE CHOSEN (2026-08-15) -- AND HOW NOT TO CHOOSE THEM.
# DO NOT re-fit these to the top of a single sweep. Three times now that has produced a number that
# did not survive the next curation, because a fresh curation moves the book more than any knob does:
#   - nominal [4, 0.60, 45, 0, 4.0, 0.30] topped v14's sweep at $324,524, then paid $122,408 when
#     v15 re-curated the same window.
#   - v15's own top cell [8, 0.40, 30, 2, 4.0, 0.30] pays $441,877 on v15 and $61,471 on v14 --
#     BELOW SPY's $86,213. A sweep winner is a curation-specific spike until proven otherwise.
#   - and the cell chosen on 2026-08-14 by worst-case plateau across v14+v15,
#     [6, 0.40, 45, 4, 4.0, 0.20], fell to $165,772 on the v4 grouped curation -- 78th percentile,
#     Sharpe 0.85, and it left the book holding NOTHING on 53% of days (32% of trades cancelled).
#     Robustness across two curations of the SAME design did not survive a change of design.
#
# CURRENT CELL [4, 0.40, 30, 0, 4.00, 0.30] with max_events: 16, set 2026-08-16.
# CHOSEN ON CROSS-CURATION TRANSFER, not on being best in sample -- the first config here selected
# that way, after four straight failures of the in-sample method. It is measured on BOTH available
# 3-year curations (uncapped = data/cbt_3yr_v4, and max_events=16 = data/cbt_3yr_me16) against the
# gates DD < 60%, L2 < 1200/yr, Sharpe > 1.2, cancelled < 35%:
#
#   curation    Sharpe      final     DD     cancelled   gates
#   uncapped      1.31   $167,023   23.9%       18.7%    PASS
#   me16          2.03   $528,410   22.9%       10.4%    PASS
#
# Drawdown moves 1 point and cancellation improves across two structurally different books. That
# stability is the whole case; it is NOT the biggest number on either curation.
#
# WHAT IT REPLACES, and why -- worth keeping because it is the failure mode this repo keeps hitting.
# [8, 0.60, 21, 0, 3.00, 0.10] was picked hours earlier as plateau-rank 3 on the uncapped curation
# (Sharpe 1.77, $312,787, drawdown 29.6%, cancellation 9.3%). On the me16 curation the SAME cell
# scores Sharpe 1.24, drawdown 54.0%, cancellation 42.6% -- it fails the gates outright. Being 3rd of
# 319 by plateau on one curation predicted nothing about the next. Earlier failures, same shape:
#   - [4, 0.60, 45, 0, 4.0, 0.30]: top of v14 at $324,524, paid $122,408 on v15.
#   - [8, 0.40, 30, 2, 4.0, 0.30]: $441,877 on v15, $61,471 on v14 -- below SPY.
#   - [6, 0.40, 45, 4, 4.0, 0.20]: chosen on worst-case plateau across v14+v15, then paid $165,772
#     on the v4 grouped curation and left the book in cash 53% of days.
# The lesson taken: rank on AGREEMENT ACROSS CURATIONS, and treat any single curation's ordering --
# plateau included -- as one noisy sample.
#
# COST OF BEING RIGHT ABOUT THIS: on the uncapped curation this cell pays $167,023 against the
# replaced cell's $312,787. Roughly $146K of backtest return traded for a risk profile that does not
# depend on which curation you happen to hold. Recorded so the trade is not rediscovered as a bug.
#
# CAVEATS. (1) max_events: 16 is the USER'S standing choice; the measured evidence runs the other way
# and is left here rather than argued again -- the me16 curation culls 50.8% of its events at birth
# against 2.1% uncapped, its median cancellation is 61.8% against 35.3%, and under the previous,
# tighter gates ZERO of 6,300 cells cleared on it. (2) Both curations ran --arm fuller, which mixes
# look-ahead-BIASED live ledes; backtest_gdelt.py marks `clean` as the only quotable arm.
# (3) TWO curations is a better test than one and still a small sample. Per CLAUDE.md #4/#6 every
# figure here is an UPPER BOUND; the forward test is the verdict.
# ==========================================================================

# ---------- AI MODELS: who does what, and what it costs ----------
scout_model: llama4               # OPENS events. Reads the whole week's news (~1,500 headlines in ~10 chunked
                                  #   calls) and proposes ticker + catalyst. Also runs the matcher, ticker guard
                                  #   and relevance filter. ~90% of the AI bill, so keep it cheap.
event_agent_model: deepseek4      # CLOSES events. Once per live event per scan: still live? catalyst resolved?
                                  #   which tickers? Decides how long the book holds things.

# ---------- CURATOR: what gets discovered, and when it is dropped ----------
retrieval_engine: gkg             # backtest news source (GDELT GKG on BigQuery). Forward always uses web search.
discovery_filter: true            # gate the SCOUT to headlines carrying the gem tell (superlative + under-the-radar
                                  #   framing). Event agents still read the full corpus, so an event's ordinary
                                  #   follow-up coverage is never withheld from the agent tracking it.
news_lookback_days: 0             # trailing days of news each scan reads. 0 = track rebalance_period
scout_articles_per_call: 30       # BATCHING ONLY: how many articles' worth of ticker-groups share
                                  #   one scout call. It NEVER truncates a group -- a group larger
                                  #   than this simply gets a call to itself, intact. Replaces
                                  #   max_group_articles, which capped a group and so DELETED news;
                                  #   it was caught dropping the one Rocket Lab article the whole
                                  #   grouping design exists to surface.
max_article_chars: 800            # how much of ONE article's text the curator sees. Caps the article,
                                  #   NOT the ticker-group total -- the signal is corroboration across
                                  #   articles, so a group cap would hide the very article that explains
                                  #   the move. Was hardcoded at 200 in agent._block.
event_news_cap: 20                # articles each event-agent re-reads per scan. Raising it costs ~13% per 20.
max_new_events: 0                 # new events ADMITTED per scan; 0 = uncapped. Superseded by max_events: an admission
                                  #   cap bins candidates unexamined and forever, a concurrency cap keeps them rankable.
max_events: 16  # how many events may be LIVE AT ONCE. When it binds, the lowest-ranked are
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
max_watchlist: 4                  # how many tickers may hold capital at once.
cull_fresh_slots: 3               # of those slots, how many are held for brand-new events, which have no price history yet for "trend" to judge.
cull_fresh_scans: 2               # how new counts as new, in scans.
drop_unfunded_weeks: 0            # scans a name can go unfunded before it is dropped from the watchlist.
                                  #   0 = NEVER drop for being unfunded. Every top-Sharpe cell on the v4
                                  #   sweep sets this to 0: with 191 events competing for 8 slots a name is
                                  #   often unfunded because something else outranked it this month, not
                                  #   because its thesis died -- the curator's exit switch already handles
                                  #   that. Dropping on 4 kept evicting names the optimizer then re-bought.
unfunded_reentry_on_new_catalyst: true   # lets a dropped name back in, but ONLY when the press names it under a DIFFERENT thesis.
concentration_cap: 0.40           # most of the book any one ticker may take. Tightened from 0.40
                                  #   2026-08-15: at max_watchlist 8 the sweep's whole top-Sharpe cluster
                                  #   sits at 0.25, i.e. spread the risk and let the curator's breadth,
                                  #   not a single name, carry the return.
min_trade_size: 0.30              # positions smaller than this are dropped. At max_watchlist 8 an equal book
                                  #   is 12.5% a name, so at 0.20 this still BITES -- a concentration lever,
                                  #   not a dust filter. Watch it: paired with the old [6, 0.40, 45, 4] cell
                                  #   it cancelled 32% of trades and left the book in cash 53% of days,
                                  #   because positions under the floor are DROPPED rather than shrunk.
risk_aversion: 4.0                # λ in mean-variance. Higher = spreads wider, chases returns less.
optimizer_lookback_days: 30       # days of price history behind μ and Σ. Cut from 45 2026-08-15:
                                  #   the sweep's top-Sharpe cluster is all 14. A 45-day window on a book
                                  #   rebalanced monthly averages over two regimes of a fast-moving
                                  #   catalyst name, which is the wrong estimate of its mu.
rebalance_period: monthly         # weekly | biweekly | monthly | quarterly. The trading cadence.
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
---
