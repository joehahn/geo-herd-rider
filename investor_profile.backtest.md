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
max_silent_scans: 8              # SILENCE CAP: retires an EVENT after this many consecutive scans
                                 # whose agent entry cites NO sources -- the mechanical signature of
                                 # "no confirming news". Sibling of max_event_scans below: that one
                                 # retires an event that ran too LONG, this retires one that ran too
                                 # QUIET, and it fires first. CURATION knob. 0 = off.
                                 # WHY 8, MEASURED not assumed (2026-08-29): across cbt v21+v22 the
                                 # run-length distribution is bimodal -- massed at 0-2 (events that
                                 # actually get covered) with a tail at 9-12 that survives only until
                                 # max_event_scans kills it. 8 sits in the gap: retires 37/284 (13%)
                                 # of v22 events and 35/317 (11%) of v21, skipping 8% of the agent
                                 # budget in both. The case that named it: ev222 ("$1B loan for Three
                                 # Island restart") ran NINE consecutive zero-source entries --
                                 # "No news on the $1B TMI loan; catalyst remains pending", verbatim,
                                 # nine times -- staying live and holding PCG the whole way.
                                 # It does NOT write `retired`: silence is absence of evidence, not
                                 # evidence the thesis died, so the ticker stays re-chaseable and the
                                 # scout can reopen the event when coverage returns.
                                 # UNIT IS SCANS, so elapsed meaning follows rebalance_period -- 8 is
                                 # ~8 months here (monthly) but ~2 months on a weekly forward cadence.
                                 # Same hazard max_event_scans documents via age_offset.
max_event_scans: 12               # retires an EVENT at this age, in scans. CURATION knob.
                                 # UNCHANGED. A 12 arm was tried and reverted 2026-08-22: the
                                 # mechanism finding is real (55.5% of events die pinned at
                                 # exactly this cap -- the timer, not the thesis, ends the median
                                 # event) but the one clean 12-draw was not publishable. See
                                 # TODO.md 2026-08-22 (c). NEVER set negative: the test is
                                 # `len(entries) >= max_event_scans`, so -1 retires every event at
                                 # its FIRST scan. 0 disables the timer entirely.
# NAME IS WRONG, KEPT DELIBERATELY: this counts SCANS, not weeks. agent.process_week tests
# `(week_idx - retired_idx) < curator_memory_weeks` where week_idx is the enumerate index over
# scan anchors, so the unit is whatever the cadence is -- 8 means 8 MONTHS at monthly and 8
# WEEKS at weekly. It is renamed only when a fingerprint break is acceptable: the key is a
# member of provenance.CURATION_KNOBS, so changing it rehashes every curation. See TODO.md.
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
max_watchlist: 12                 # how many tickers may COMPETE for capital at once (not how many hold it).
                                  #   6 -> 12 on 2026-08-30. THE BOOK DOES NOT GET WIDER: it funds a
                                  #   median of 2 names and a max of 3 at max_watchlist 6, 12, 20 AND 32
                                  #   alike, because min_trade_size 0.2 and concentration_cap 0.6 fix the
                                  #   concentration downstream. What widens is the SLATE the optimizer
                                  #   chooses those 2-3 names from. That is the whole change.
                                  #   WHY, three independent lines pointing the same way:
                                  #     1. MECHANISM. 93.6% of live ticker-scans are culled BEFORE the
                                  #        optimizer sees them (3,196 of 3,415 on v22); only 2.0% are
                                  #        funded. MU was culled for ten consecutive scans while its DRAM
                                  #        thesis was live and correct, and the stock ran +312% over
                                  #        exactly that stretch. risk_aversion cannot touch this: the
                                  #        culled share is 93.6% at ra=1 and at ra=40.
                                  #     2. Both SBT sweeps put the optimum ABOVE 6 -- v24 (over v22) at 8,
                                  #        v25 (over v23) at 16.
                                  #     3. CROSS-CURATION, the method that has actually held up here: one
                                  #        knob varied, `final` over 9 curations. Every wide value beats 6
                                  #        in the SAME 6 of 9 books, median 1.53-1.70x.
                                  #   WHY 12 AND NOT 20/26/32/uncapped: they are indistinguishable --
                                  #   medians 1.53 / 1.70 / 1.60 / 1.68 / 1.68x, all 6/9, worst cases
                                  #   0.50 / 0.55 / 0.52 / 0.51 / 0.44x. It is ONE binary effect (tight vs
                                  #   wide), not a tunable curve, so take the smallest step. 12 is also
                                  #   interior; 20+ is a flat plateau where any value is arbitrary, and
                                  #   uncapped was already killed (0/54 replays).
                                  #   KNOWINGLY ACCEPTED: one-sided binomial p = 0.25. Three curations
                                  #   (v22, v21, bw21) are WORSE at every width, worst case ~0.5x. This is
                                  #   a lead, not a proven win.
                                  #   BACKTEST-DRIVEN, so NOT promoted to .forward.md (non-negotiable #7).
                                  #   The recommendation on record is to run 6 and 12 as the two
                                  #   pre-registered arms of the forward eval and let it decide.
                                  #   RATIONALE REWRITTEN 2026-08-29. The previous block argued this
                                  #   value as "the centre of the sweet spot" for the config
                                  #   4 · 0.6 · 21 · 4 · 4.0 · 0.05, chosen 2026-08-22. FOUR of those six
                                  #   knobs have moved since (concentration_cap 0.25->0.6 on 08-27,
                                  #   risk_aversion, min_trade_size, drop_unfunded_weeks), so that
                                  #   argument described a config that is no longer live and is deleted
                                  #   rather than left to be read as current.
                                  #   WHERE THE LIVE CONFIG ACTUALLY SITS, measured on sweep_v21 with the
                                  #   scored pair (sharpe + pc_fund_med): 6 · 0.6 · 21 · 0 · 8.0 · 0.2 is
                                  #   region rank 128 of 7,200, score 96.6, with 6 of its 21 one-knob
                                  #   neighbours themselves top-100 regions. The top-scoring regions all
                                  #   share concentration_cap 0.25, which the live config deliberately
                                  #   does not (see the concentration_cap note below -- it was raised for
                                  #   panel 19's cross-curation cluster, not for this sweep's ranking).
                                  #   So this value is NOT the in-sample peak and is not claimed to be.
                                  #   STILL TRUE, and the reason the peak is not chased: the ranking is
                                  #   WITHIN ONE CURATION, and two runs of the same config disagree about
                                  #   the best region as much as two different configs do.
cull_fresh_slots: 2               # of those slots, how many are held for brand-new events, which have no price history yet for "trend" to judge.
                                  #   3 -> 2 on 2026-08-29. At 3 this tier took HALF a six-slot book,
                                  #   allocated by recency alone with no quality signal, and it saturated
                                  #   in 36 of 36 scans. Paired replays over 54 book-knob configs, vs 3:
                                  #     v21 1.121x (wins 38/54) · bw21 1.056x (30/54) · mb2rep 1.519x (54/54)
                                  #   1 also beats 3 on all three (1.023 / 1.200 / 1.787); 4 does not
                                  #   (0.897 / 1.019 / 0.973). 2 is chosen over 1 for winning the MAJORITY
                                  #   of paired configs on every curation rather than the largest median.
                                  #   The tier is still needed: 0 measures 0.834x, winning only 9 of 54.
                                  #   BACKTEST-DRIVEN, so NOT promoted to .forward.md (non-negotiable #7):
                                  #   .forward.md stays at 3 until the forward eval speaks.
cull_fresh_scans: 2               # how new counts as new, in scans.
drop_unfunded_weeks: 2            # scans a name can go unfunded before it is dropped from the watchlist.
                                  #   4 -> 2 on 2026-08-30, WITH risk_aversion 12.0 -> 8.0: together these
                                  #   are the TOP ROW of SBT table 10 on sweep_v26 (12 / 0.6 / 30 / 2 /
                                  #   8.0 / 0.2, score 99.8), promoted at the user's instruction.
                                  #   RECORDED AGAINST IT, measured the same day, one knob varied over
                                  #   TEN curations at the live config: drop_unfunded_weeks=2 is the
                                  #   WORST value on the grid -- median 0.51x vs 4, beating it in only
                                  #   2 of 10 curations (0 -> 0.71x 3/10, 6 -> 1.00x 5/10). Inside
                                  #   cbt_3yr_v24_wirelede ALONE, which is the curation sweep_v26 ranks,
                                  #   2 looks like a 3x win ($581,995 vs $197,172). That gap between the
                                  #   one-curation sweep and the ten-curation replay is the whole reason
                                  #   non-negotiable #6 exists, and it is why the previous 4 was chosen.
                                  #   PRIOR RATIONALE for 4, kept because it is the case that has to be
                                  #   beaten if this is ever revisited:
                                  #   0 -> 4 on 2026-08-29, WITH risk_aversion 8 -> 12. They move as a
                                  #   PAIR and must be read as one change: neither works alone.
                                  #   Config 6 / 0.6 / 30 / 4 / 12.0 / 0.2 beats the previous live
                                  #   config in ALL FOUR curations on hand -- median 1.45x, and 1.18x
                                  #   even in its weakest arm, so it never loses. Only one other cell
                                  #   in table 10 goes 4/4, and that one needs three knob changes and
                                  #   ties (1.02x) in its worst arm.
                                  #   THE CAVEAT, recorded rather than buried: on its OWN this knob has
                                  #   no reproducible signal -- its best level differs in every curation
                                  #   (marginal medians, lookback held at 30). And risk_aversion 12 ALONE
                                  #   wins only 1 of 4 at the old cell. The gain lives in the pair, not
                                  #   in either part, which is the interaction effect that main effects
                                  #   (39-55% of variance here) cannot see. Re-read table 10 after any
                                  #   further move: shifting two knobs moves the whole neighbourhood the
                                  #   table is computed over.
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

min_trade_size: 0.2              # positions smaller than this are DROPPED, not shrunk -- a concentration
                                  #   lever, not a dust filter. At four names an equal book is 25% each, so a
                                  #   0.05 floor is far below the smallest intended position and effectively
                                  #   inert here.
risk_aversion: 8.0                # λ in mean-variance. Higher = spreads wider, chases returns less.
                                  #   12.0 -> 8.0 on 2026-08-30, PAIRED with drop_unfunded_weeks 4 -> 2 as
                                  #   the top row of SBT table 10 on sweep_v26. See that knob.
                                  #   RECORDED AGAINST IT, one knob varied over TEN curations at the live
                                  #   config: 12.0 is the best value tested -- nothing beats it in more
                                  #   than 4 of 10, and 8.0 specifically is median 0.88x, 4/10.
                                  #   PRIOR RATIONALE for 12.0, superseded but kept for the reasoning:
                                  #   8.0 -> 12.0 on 2026-08-29, PAIRED with drop_unfunded_weeks 0 -> 4;
                                  #   see that knob for the evidence. Every one of table 10's sixteen
                                  #   configs carries risk_aversion >= 12 -- the old 8.0 was the sole
                                  #   outlier -- but the knob does not stand alone: changing only this
                                  #   one at the previous config wins 1 of 4 curations.
                                  #   Prior note below is superseded but kept for the reasoning.
                                  #   16.0 -> 8.0 on 2026-08-24, promoting row 2 of SBT's region table.
                                  #   The only knob that changed: row 2 is 6 / 0.25 / 21 / 0 / 8.0 / 0.2
                                  #   against the previous 6 / 0.25 / 21 / 0 / 16.0 / 0.2. Row 2 ties row 1
                                  #   on score (99.79 vs 99.79) but reaches it from the OTHER side of the
                                  #   lambda range -- row 1 is lambda 24, the grid's top EDGE, where
                                  #   "best" and "as far as the grid goes" cannot be told apart. Row 2 sits
                                  #   interior, so it is the defensible half of a tie.
                                  #   BOOK knob (src/provenance.py): acts at replay time over the fixed
                                  #   journal, so this costs a rebuild, not a re-curation and not a re-sweep.
optimizer_lookback_days: 30       # days of price history behind μ and Σ.
                                  #   21 -> 30 on 2026-08-29. This is the ONLY knob in the grid with
                                  #   real leverage: a one-way variance decomposition of log(final)
                                  #   over all four sweeps on hand gives it 23-44% (mean 30%), against
                                  #   4.9% for drop_unfunded_weeks, 4.2% risk_aversion, 3.3%
                                  #   max_watchlist, 1.8% concentration_cap and 0.4% min_trade_size.
                                  #   The other five are inside the noise BY CONSTRUCTION, which is why
                                  #   every recommendation to move them failed a direct check.
                                  #   PAIRED, same other-five knobs, 30 vs 21 over 1,800 pairs per
                                  #   curation: 30 wins 79% (v24), 49% (v23), 55% (bw23), 68% (wk23) --
                                  #   it wins or ties in all four and never loses.
                                  #   AND IT IS STABLE. Across the two monthly REPLICATES (v21 and v22,
                                  #   same config, same corpus, different LLM draw) the live config's
                                  #   book at lookback 30 lands at $118,261 and $119,569 -- a 1.01x
                                  #   swing. At 21 the same pair swings 3.94x ($108,905 / $429,309),
                                  #   and at 14 it swings 4.19x. The cull ranks on mean/sd over this
                                  #   window; at 21 days that statistic is too poorly conditioned to
                                  #   survive a different candidate set, so one early difference
                                  #   cascades through every later scan. 30 is wide enough that the
                                  #   book converges regardless of draw.
                                  #   NOT a single-cell result: the one-knob comparison at the live
                                  #   config alone favours 21, but only because v23's cell there is the
                                  #   $429K lucky draw already retired -- over 1,800 paired configs
                                  #   that same curation is a coin flip.
                                  #   Prior note (2026-08-15, cut 45 -> 21): a 45-day window on a book
                                  #   rebalanced monthly averages over two regimes of a fast-moving
                                  #   catalyst name. Still true; 30 does not reach that far.
                                  #   A BOOK knob: rebuild only, no re-curation. BACKTEST-DRIVEN, so
                                  #   .forward.md stays at 21 until the forward eval speaks (#7).
rebalance_period: monthly         # weekly | biweekly | monthly | quarterly. The trading cadence.
t_update_days: 1                  # trading days between the signal and the trade.
risk_free_rate: 0.04              # Sharpe reporting only; not in the weighting.

# ---------- SOURCES: which outlets the forward gather prefers and avoids ----------
# specialty_allow: MOVED to retrieval_config.json 2026-08-25 -- an INGEST parameter,
#   read by forward_gather.py and gkg.py. See that file's _domain_steering_note.
# mill_block: MOVED to retrieval_config.json 2026-08-25 -- an INGEST parameter,
#   read by forward_gather.py and gkg.py. See that file's _domain_steering_note.
---
