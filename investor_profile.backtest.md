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
# ============================ READ THIS BEFORE TRUSTING ANY NUMBER BELOW ============================
# MEASURED 2026-08-17: SINGLE-CURATION BACKTEST P&L CANNOT ADJUDICATE A CHANGE. The same settings
# (max_events 16, corpus backtest_3yr_v4), run TWICE with nothing altered but the LLM's sampling:
#
#                        run A (me16)   run B (repeat)
#     median final           $117,200        $62,997     -46%
#     p25 / p90 final    $77,199/$245K   $29,710/$195K
#     best cell in grid    $2,152,119     $1,011,264
#     median Sharpe              0.83           0.53
#     cells beating SPY         4,245          2,355
#     cell [4,.6,30,0,4,.3]    $588,538        $75,132     -87%
#
# The 6,300 sweep cells are NOT 6,300 samples -- they are ONE curation viewed 6,300 ways, so a lucky
# or unlucky book shifts every percentile together and looks exactly like a real improvement.
#
# THIS INVALIDATED THREE ATTRIBUTIONS MADE THE SAME DAY, each of which looked convincing at the time:
#   - "max_events 0 halved the book"  -> disproved by a controlled run (v6 vs v7: medians within 0.7%)
#   - "the corpus v4->v5 swap hurt"   -> 15.7% of articles changed text source, but 91% of those are
#                                        >=0.99 identical; only 0.7% of the corpus materially differs
#   - "the bundling changes hurt"     -> measured deterministically at ~1% of what the scout reads
# All three were noise being read as signal. The repeat run settles it: run A was simply a lucky draw.
#
# WHAT REMAINS TRUSTWORTHY: deterministic or near-deterministic MECHANISM measures -- bundle-payoff
# monotonicity (5.1% -> 67.8% proposal rate by bundle size), cull-at-birth rates, corpus coverage,
# cancellation, orphan counts, the effect of a code change on scout input. Those reproduce. Book value
# does not. Judge changes on mechanism; treat any P&L difference under ~2x as unmeasurable.
# ====================================================================================================
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
# CURRENT CELL [8, 0.25, 21, 0, 4.00, 0.10] with max_events: 0, set 2026-08-17.
# Chosen off the v6 curation (data/cbt_3yr_v6: corpus v5, uncapped, title-ticker grouping), from the
# 171 of 6,300 cells clearing DD < 45%, L1 1500-1800, L2 600-1100, Sharpe > 1, cancelled < 50%,
# shortlist gain > $10k:
#
#   plateau 27.2   Sharpe 1.65   final $210,592   drawdown 21.7%   cancellation 24.3%
#   shortlist $16,200 across 2 of the 7 no-brainer names        SPY $86,817
#
# Best Sharpe and much the best drawdown in the leading group (21.7% against 27.8-39.5%), plateau
# within 1.4 of the leader. `lookback 21 / drop_unfunded 0` is shared by all six leaders, so that part
# is a plateau rather than a point.
#
# TWO THINGS THIS CELL IS NOT, both worth stating because the numbers above invite the opposite read.
#
# 1. IT IS NOT TRANSFER-TESTED. Zero of the 171 v6 survivors also clear the gates on the me16 book --
#    but that is mostly an ARTEFACT of the L1 1500-1800 band, which is fitted to v6's churn regime
#    (v6 L1 median 1573, me16 2155), so 100% fail L1 there. Cancellation (64%) and drawdown (53%)
#    also fail often, and those are real. The transfer criterion that picked the previous two configs
#    simply cannot be applied across these two books.
# 2. IT IS THE BEST CELL IN A WEAKER BOOK. The v6 curation is worse than me16 across the whole grid:
#    median Sharpe 0.53 vs 0.83, median final $73,304 vs $117,195, 2,268 of 6,300 cells beating SPY
#    vs 4,245. This cell pays $210,592 where the me16 grid had cells above $2M. Optimising here is
#    optimising inside the worse curation.
#
# THE OPEN QUESTION IS max_events, NOT THIS CELL. v6 ran uncapped; me16 ran at 16 on the older corpus.
# Four things differ between them (cap, corpus v4->v5, title-ticker fallback, stoplist), but the
# grouping changes are measurably tiny -- deterministically +/-1 group and +0.5-10% articles shown --
# leaving the cap as the only lever large enough to explain a grid-wide halving. One curation at
# max_events 16 on corpus v5 (~$4.50, ~50 min) would settle it and is the next thing worth spending on.
#
# CAVEATS unchanged: ONE curation; the seven shortlist names were chosen in HINDSIGHT and scored on
# the same book the ranking comes from; --arm fuller mixes look-ahead-BIASED live ledes (clean text is
# 56% of corpus v5). Per CLAUDE.md #4/#6 every figure here is an UPPER BOUND; forward is the verdict.
# ==========================================================================

# ---------- AI MODELS: who does what, and what it costs ----------
scout_model: llama4               # OPENS events. Reads the whole week's news (~1,500 headlines in ~10 chunked
                                  #   calls) and proposes ticker + catalyst. Also runs the matcher, ticker guard
                                  #   and relevance filter. ~90% of the AI bill, so keep it cheap.
event_agent_model: grok4          # CLOSES events: is this thesis still live, has its catalyst resolved?
                                  #   Grok 4.3, swapped from deepseek4 2026-08-19 on the 8-model bake-off
                                  #   (SBT panels 16-21). Fable-5 audited 4,527 event-agent calls on PROCESS
                                  #   only -- catalyst datable / write-up within its sources / exit call
                                  #   coherent -- with no prices in front of the judge. Grok 4.3 low scored
                                  #   61.1% clean at $6.42/curation against deepseek4's 42.6% at $5.96, and
                                  #   posted the best `dated` rate of the eight. NOT chosen on P&L: final
                                  #   value across the 8 arms sat inside the 1.86x noise floor (#6), so the
                                  #   switch rests on decision quality, which reproduces.
event_agent_effort: low           # Grok 4.3's reasoning knob measured as a NULL: low scored 61.1% vs
                                  #   high's 57.9% at the same cost and runtime, so low is chosen for
                                  #   being no worse and marginally cheaper -- not for being better.
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
max_events: 16                    # how many events may be LIVE AT ONCE. When it binds, the lowest-ranked are
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
max_watchlist: 12                 # NEIGHBOURHOOD-CHOSEN 2026-08-21 on the canonical curation alone.
                                  #   Was 6, chosen 2026-08-19 by pooling 15 sweeps across as many
                                  #   curations. That pooling was withdrawn: those curations are not
                                  #   repeat draws of one setup -- the text they read ranges from 9.6%
                                  #   to 45.3% clean archived lede, one fed the curator 41.7% bare
                                  #   headlines, four read a different article pool, and three of the
                                  #   15 were the same curation swept twice. It averaged over
                                  #   RETRIEVAL REGIMES, not over news noise.
                                  #   The replacement scores a config by its own 22-cell one-knob
                                  #   NEIGHBOURHOOD (SBT panel 8), trimming the luckiest and
                                  #   unluckiest member: a knife-edge cell cannot win, because its
                                  #   neighbours are inside its score. [12, 0.25, 21, 0, 4.0, 0.05]
                                  #   ranks 1 of 6,300 at a regional median of $278,249 +/- 38,560
                                  #   against 6's $105,442 +/- 10,667 -- non-overlapping.
                                  #   TWO CAVEATS, both live. 12 is the TOP of the swept grid, so the
                                  #   optimum may lie outside it and this is a ceiling, not a maximum.
                                  #   And the top-50 regions favour 8 (33 of 50) over 12 (16 of 50),
                                  #   so the best single row and the weight of the band disagree.
cull_fresh_slots: 3               # of those slots, how many are held for brand-new events, which have no price history yet for "trend" to judge.
cull_fresh_scans: 2               # how new counts as new, in scans.
drop_unfunded_weeks: 0            # scans a name can go unfunded before it is dropped from the watchlist.
                                  #   0 = NEVER drop for being unfunded. Every top-Sharpe cell on the v4
                                  #   sweep sets this to 0: with 191 events competing for 8 slots a name is
                                  #   often unfunded because something else outranked it this month, not
                                  #   because its thesis died -- the curator's exit switch already handles
                                  #   that. Dropping on 4 kept evicting names the optimizer then re-bought.
unfunded_reentry_on_new_catalyst: true   # lets a dropped name back in, but ONLY when the press names it under a DIFFERENT thesis.
concentration_cap: 0.25           # most of the book any one ticker may take. Tightened from 0.40
                                  #   2026-08-15: at max_watchlist 8 the sweep's whole top-Sharpe cluster
                                  #   sits at 0.25, i.e. spread the risk and let the curator's breadth,
                                  #   not a single name, carry the return.
min_trade_size: 0.05              # positions smaller than this are dropped. NOT a dust filter -- a
                                  #   concentration lever, because positions under the floor are DROPPED
                                  #   rather than shrunk. Loosened from 0.10 2026-08-21 with the
                                  #   max_watchlist move: at 12 names an equal book is 8.3% each, so a
                                  #   0.10 floor would cancel most of the book's intended positions.
                                  #   0.05 is the winning region's value and the grid is nearly flat
                                  #   across 0.0-0.2 there (regional median $273K-$298K), so this is
                                  #   the least load-bearing of the six. Watch it: paired with the old
                                  #   [6, 0.40, 45, 4] cell a 0.20 floor cancelled 32% of trades and
                                  #   left the book in cash 53% of days.
risk_aversion: 4.0                # λ in mean-variance. Higher = spreads wider, chases returns less.
optimizer_lookback_days: 21       # days of price history behind μ and Σ. Cut from 45 2026-08-15:
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
