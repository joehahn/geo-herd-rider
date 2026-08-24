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
scout_model: llama4               # OPENS events. Reads the whole week's news in chunked calls (~1,600 per
                                  #   curation), so this is where the token cost lives -- 85% of a curation's
                                  #   bill, measured on mb2rep: $6.08 of $7.17 at $3.76/1k calls.
                                  #   KEEP IT CHEAP. grok4 was set here briefly on 2026-08-22 and reverted
                                  #   the same day: grok-4.3 has measured $41.96/1k on this stage, ~11x, which
                                  #   projects a curation at ~$69 against $7.17 and would have taken the
                                  #   3-arm min_bundle_articles sweep from $20 to ~$200.
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
min_bundle_articles: 1            # a company bundle needs >= this many articles to be shown AS a company
                                  #   bundle; 1 = every bundle qualifies. CURATION knob.
                                  #   Back to 1 on 2026-08-22. The 2 was chosen on mechanism (events opened
                                  #   12.2x the same-config noise floor, evidence-cited picks 22.1x) and
                                  #   those measurements stand -- but 2 also opens more events that are
                                  #   never read (cull-at-birth 7.1x noise), and with max_events now
                                  #   uncapped that trade no longer applies.
event_news_cap: 20                # articles each event-agent re-reads per scan. Raising it costs ~13% per 20.
max_new_events: 0                 # new events ADMITTED per scan; 0 = uncapped. Superseded by max_events: an admission
                                  #   cap bins candidates unexamined and forever, a concurrency cap keeps them rankable.
max_events: 0                     # how many events may be LIVE at once. 0 = UNCAPPED (the code tests
                                  #   `if max_events:`), so no limit on concurrency. CURATION knob.
                                  #   Was 16. Uncapping removes the cull that made cull-at-birth 52-61% of
                                  #   events across the mb sweep -- work paid for and thrown away.
picker_model:                     # BLANK = use the arithmetic coverage-rank (src/evscore.py). An LLM ranker
                                  #   has failed to beat its own null three times here. Set to a STRONG model only
                                  #   to re-test that.  # ranks live events by catalyst ARC (early/building over crested) and emits an ordered
                                  #   keep-list only -- never weights or returns. MUST be a strong model: sonnet5 hit the
                                  #   83rd percentile, a cheap picker came in BELOW random. ~1 call/scan.
exit_patience_scans: 2            # drops a TICKER after this many consecutive "thesis is dead" reads, avoids one bad week closing a good thesis.
max_stale_scans: 8                # SCANS a held name may go UNMENTIONED before it is dropped.
                                 # BOOK knob (replay-time; reclassified 2026-08-22) -- free to
                                 # change, rebuild only, no re-curation.
                                 # 2 -> 8 on 2026-08-22. At 2, with ~monthly scans, TWO MONTHS OF
                                 # PRESS SILENCE ended a position -- the cause of the 1-2 month
                                 # watchlist tenure on BE/CORZ. At 8: BE 7->11 scans, CORZ 5->11,
                                 # NVTS 5->21, IREN 10->20 (reproduces across all 7 curations).
                                 # CHOSEN ON 7-DRAW POOLING, not one book: rank-sum 15 vs 20 for
                                 # ms=2, medians tied (111K vs 115K), and ONE THIRD the dispersion
                                 # (4.9x vs 14.7x) -- ms=2's higher MEAN is one lucky draw.
                                 # TRAP: 0 does NOT disable -- `int(fm.get(...) or MAX_STALE)`
                                 # falls back to 4. Negative drops everything. Use a big number.
max_event_scans: 12               # retires an EVENT at this age, in scans. CURATION knob.
                                 # UNCHANGED. A 12 arm was tried and reverted 2026-08-22: the
                                 # mechanism finding is real (55.5% of events die pinned at
                                 # exactly this cap -- the timer, not the thesis, ends the median
                                 # event) but the one clean 12-draw was not publishable. See
                                 # TODO.md 2026-08-22 (c). NEVER set negative: the test is
                                 # `len(entries) >= max_event_scans`, so -1 retires every event at
                                 # its FIRST scan. 0 disables the timer entirely.
curator_memory_weeks: 8          # SCANS a RETIRED ticker stays on the scout's do-not-re-propose
                                 # list. 0 = off, <0 = whole history. Scans are ~monthly, so 8 is an
                                 # ~8-MONTH ban. Was inherited silently from optimizer defaults until
                                 # 2026-08-22; written out here because it is a CURATION knob and the
                                 # forward profile sets it to 4 -- an unsynced strategy knob nobody
                                 # could see by reading this file. Value unchanged (8), so the
                                 # canonical curation fingerprint is untouched. See TODO.md
                                 # 'retired-ticker guard' -- the guard is keyed by TICKER, not by
                                 # catalyst, so a new dated catalyst on a retired name is barred too.
                                  #   Halved from 12 on 2026-08-22: at monthly cadence 12 scans is a year,
                                  #   which is a long time to keep re-reading a catalyst that has resolved.

# ---------- OPTIMIZER: what gets funded, and how much ----------
initial_investment_usd: 50000     # day-0 dollars.
starter_watchlist: [AAPL, GOOGL, AMZN]   # day-0 holdings, equal weight, until the curator's own picks replace them.
always_include: [SPY, BIL]        # always available to the optimizer; idle cash parks here. Outside max_watchlist.
max_watchlist: 6                  # how many tickers may hold capital at once. Set 2026-08-22 with the three
                                  #   knobs below as ONE config: 4 · 0.6 · 21 · 4 · 4.0 · 0.05.
                                  #   CHOSEN AS THE CENTRE OF THE SWEET SPOT, NOT ITS PEAK. Of its 22 one-knob
                                  #   neighbours, 11 are themselves top-100 regions -- the densest overlap in
                                  #   the grid. The top-SCORING config (4 · 0.6 · 30 · 2 · 4.0 · 0.1, score
                                  #   93.5) has only 8, so it sits on the shoulder of its own good region,
                                  #   which is where a noise-driven peak tends to sit. This one scores 92.9 at
                                  #   rank 7 -- marginally lower, materially better surrounded.
                                  #   The whole top band shares max_watchlist 4 with cap 0.4-0.6 and risk 3-4:
                                  #   a narrow, coherent corner (concentrate hard in few names), not scattered
                                  #   lucky cells.
                                  #   TWO CAVEATS, both live. The overlap measure is SCALE-DEPENDENT -- at
                                  #   top-500 a different family wins (4 · 0.4 · 30 · 4, 17/22 but rank 164).
                                  #   And it is all WITHIN ONE CURATION: two runs of the same config disagree
                                  #   about the best region as much as two different configs do, so this corner
                                  #   keeps winning single sweeps and has not yet survived the next one.
cull_fresh_slots: 3               # of those slots, how many are held for brand-new events, which have no price history yet for "trend" to judge.
cull_fresh_scans: 2               # how new counts as new, in scans.
drop_unfunded_weeks: 0            # scans a name can go unfunded before it is dropped from the watchlist.
                                  #   Was 0 (never drop).
unfunded_reentry_on_new_catalyst: true   # lets a dropped name back in, but ONLY when the press names it under a DIFFERENT thesis.
concentration_cap: 0.25            # most of the book any one ticker may take. Loosened from 0.25 with the
                                  #   move to max_watchlist 4 -- at four names an equal book is 25% each, so a
                                  #   0.25 cap would force exactly equal weights and give the optimizer nothing
                                  #   to do.
min_trade_size: 0.2              # positions smaller than this are DROPPED, not shrunk -- a concentration
                                  #   lever, not a dust filter. At four names an equal book is 25% each, so a
                                  #   0.05 floor is far below the smallest intended position and effectively
                                  #   inert here.
risk_aversion: 8.0                 # λ in mean-variance. Higher = spreads wider, chases returns less.
                                  #   16.0 -> 8.0 on 2026-08-24, promoting row 2 of SBT's region table.
                                  #   The only knob that changed: row 2 is 6 / 0.25 / 21 / 0 / 8.0 / 0.2
                                  #   against the previous 6 / 0.25 / 21 / 0 / 16.0 / 0.2. Row 2 ties row 1
                                  #   on score (99.79 vs 99.79) but reaches it from the OTHER side of the
                                  #   lambda range -- row 1 is lambda 24, the grid's top EDGE, where
                                  #   "best" and "as far as the grid goes" cannot be told apart. Row 2 sits
                                  #   interior, so it is the defensible half of a tie.
                                  #   BOOK knob (src/provenance.py): acts at replay time over the fixed
                                  #   journal, so this costs a rebuild, not a re-curation and not a re-sweep.
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
