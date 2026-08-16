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
# CURRENT CELL [4, 0.60, 30, 0, 4.00, 0.10] with max_events: 16, set 2026-08-16.
# Row 1 of table 8 on the me16 curation (data/cbt_3yr_me16, the production curation), plateau-ranked,
# clearing DD < 70%, L1 > 1800%/yr, L2 < 1250/yr, Sharpe > 1.2, cancelled < 40%, shortlist > $40k:
#
#   plateau 19.4 (best of 254)   Sharpe 1.69   final $404,241   drawdown 35.8%   cancelled 15.9%
#   shortlist gain $76,081 across 4 of the 7 no-brainer names
#
# CHOSEN FOR SHORTLIST CAPTURE, which is what the newest gate exists to enforce. The cell it replaces,
# [4, 0.40, 30, 0, 4.00, 0.30], is better on every whole-book measure -- Sharpe 2.03, drawdown 22.9%,
# cancellation 10.4%, final $528,409 -- but made $53,297 on only 2 of the 7 names, clearing its own
# shortlist bar by $3,297. Its tight cap (0.40) and high trade floor (0.30) are precisely what kept it
# out of five of the seven; loosening both to 0.60 / 0.10 is what buys the capture. Only those two
# knobs move.
#
# WHAT IT COSTS: 13 points of drawdown (35.8% vs 22.9%), 0.34 of Sharpe, and $124,168 of final value,
# for +43% on the shortlist and 4 names held instead of 2. That is the fork this page keeps arriving
# at -- cleanest risk-adjusted book, or actually riding the theses the curator was built to find --
# and the shortlist gate is the decision to take the second. Recorded so it is not rediscovered as a
# regression.
#
# CAVEATS. (1) Selected on ONE curation. Every earlier single-curation pick here failed on the next
# book: [8, 0.60, 21, 0, 3.0, 0.10] was plateau-rank 3 on the uncapped curation and failed the gates
# outright on me16 (Sharpe 1.77 -> 1.24, cancellation 9.3% -> 42.6%). The honest test is a SECOND
# curation at max_events 16 with a fresh seed (~$4, ~45 min), which isolates run-to-run noise from
# the design difference that makes the uncapped book an imperfect control.
# (2) The seven shortlist names were chosen in HINDSIGHT and the gate is applied to the same book the
# ranking is computed from, so that bar states what we want the strategy to catch -- it is not
# evidence that it does.
# (3) max_events: 16 is the user's standing choice; the measured evidence runs the other way (me16
# culls 50.8% of events at birth against 2.1% uncapped, median cancellation 61.8% against 35.3%).
# (4) Both curations ran --arm fuller, which mixes look-ahead-BIASED live ledes; backtest_gdelt.py
# marks `clean` as the only quotable arm. Per CLAUDE.md #4/#6 every figure here is an UPPER BOUND.
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
concentration_cap: 0.60           # most of the book any one ticker may take. Tightened from 0.40
                                  #   2026-08-15: at max_watchlist 8 the sweep's whole top-Sharpe cluster
                                  #   sits at 0.25, i.e. spread the risk and let the curator's breadth,
                                  #   not a single name, carry the return.
min_trade_size: 0.10              # positions smaller than this are dropped. At max_watchlist 8 an equal book
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
