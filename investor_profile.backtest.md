---
# ==========================================================================
# BACKTEST / DEV CONFIG — free to evolve. Read by backtest_gdelt.py, the sweeps and the dashboards.
# Promoting a settled candidate to the live forward test = copying the STRATEGY knobs into
# investor_profile.forward.md as a dated re-freeze (see that file's header).
#
# Order: AI models -> curator -> optimizer -> source allow/block lists.
# Any knob added here must also exist in optimizer._FINANCIAL_MODEL_DEFAULTS or it is SILENTLY
# IGNORED; load_financial_model warns about unknown keys.
# ==========================================================================

# ---------- AI MODELS: who does what, and what it costs ----------
scout_model: llama4               # OPENS events. Reads the whole week's news (~1,500 headlines in ~10 chunked
                                  #   calls) and proposes ticker + catalyst. Also runs the matcher, ticker guard
                                  #   and relevance filter. ~90% of the AI bill, so keep it cheap.
event_agent_model: deepseek4      # CLOSES events. Once per live event per scan: still live? catalyst resolved?
                                  #   which tickers? Decides how long the book holds things.
picker_model:                     # BLANK = off. Would pick which live events hold capital; cull_rank does that
                                  #   for free instead. ~$10 per  dashboard build, and a cheap picker is worse
                                  #   than random.

# ---------- CURATOR: what gets discovered, and when it is dropped ----------
retrieval_engine: gkg             # backtest news source (GDELT GKG on BigQuery). Forward always uses web search.
news_cap: 0                       # articles the scout reads per scan. 0 = all of them.
event_news_cap: 20                # articles each event-agent re-reads per scan. Raising it costs ~13% per 20.
max_new_events: 4                 # new events admitted per scan, best-sourced first (gem beats outrank generic coverage beats). 
curator_memory_weeks: 4           # Scans of already-resolved catalysts the scout is reminded of, so it does not re-open a thesis that is already over.
exit_patience_scans: 2            # drops a TICKER after this many consecutive "thesis is dead" reads, avoids one bad week closing a good thesis.
max_stale_scans: 2                # drops a TICKER after this many scans with NO coverage at all.
max_event_scans: 12               # retires the whole EVENT at this age (~1 year of monthly scans). A catalyst not resolved by then was a theme, not a catalyst.

# ---------- OPTIMIZER: what gets funded, and how much ----------
initial_investment_usd: 50000     # day-0 dollars.
starter_watchlist: [AAPL, GOOGL, AMZN]   # day-0 holdings, equal weight, until the curator's own picks replace them.
always_include: [SPY, GLD]        # always available to the optimizer; idle cash parks here. Outside max_watchlist.
max_watchlist: 6                  # how many tickers may hold capital at once. 
cull_rank: trend                  # when live events > max_watchlist, who gets money. trend=best recent risk-adjusted price; keep-first=alphabetical, kept ONLY as the sweep's null control.
cull_fresh_slots: 3               # of those slots, how many are held for brand-new events, which have no price history yet for "trend" to judge.
cull_fresh_scans: 2               # how new counts as new, in scans.
drop_unfunded_weeks: 0            # scans a name can go unfunded before it is dropped. 0 = never drop on unfunding alone; max_watchlist does the pruning.
unfunded_reentry_on_new_catalyst: true   # lets a dropped name back in, but ONLY when the press names it under a DIFFERENT thesis.
concentration_cap: 0.40           # most of the book any one ticker may take.
min_trade_size: 0.05              # positions smaller than this are dropped rather than held as dust.
risk_aversion: 3.0                # λ in mean-variance. Higher = spreads wider, chases returns less.
lookback_period_days: 60          # days of price history behind μ and Σ. Short lookbacks chase noise; cancellation falls monotonically 21d->60d.
rebalance_period: monthly         # weekly | biweekly | monthly | quarterly. Also the news window per scan.
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
