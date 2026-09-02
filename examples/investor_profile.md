---
# ==========================================================================
# TEMPLATE PROFILE: copy this to `investor_profile.backtest.md` before running anything.
#
# Every knob this solution is parameterized on is listed below, with a one-line description of
# what it governs. The VALUES here are neutral placeholders chosen to be plausible and runnable.
# They are NOT the settings behind the published dashboards, which are not distributed;
# see HOLDBACK.md.
#
# Order: AI models -> curator -> optimizer.
#
# TWO THINGS TO KNOW BEFORE YOU TUNE ANY OF THIS:
#
# 1. Knobs are partitioned by WHERE THEY ACT (src/provenance.py). A CURATION knob acts upstream of
#    the journal, so changing it invalidates an existing curation and the news must be re-read at
#    LLM cost. A BOOK knob acts at replay over a fixed journal, so changing it just rebuilds the
#    page in seconds. Adding a knob without classifying it fails the build, deliberately.
#
# 2. A single curation's profit and loss CANNOT adjudicate a change. The same settings run twice,
#    with nothing different but the LLM's sampling, have produced median finals differing by 46%.
#    A sweep of N cells over one curation is not N samples. It is one curation viewed N ways, so a
#    lucky book lifts every percentile at once and looks exactly like a real improvement. Judge a
#    change on MECHANISM (coverage, cull rates, cancellation, what the scout actually reads), and
#    treat any P&L difference under about 2x as unmeasurable.
#
# Any knob added here must also exist in optimizer._FINANCIAL_MODEL_DEFAULTS or it is SILENTLY
# IGNORED; load_financial_model warns about unknown keys.
# ==========================================================================

# ---------- AI MODELS: who does what ----------
scout_model: sonnet5              # OPENS events. Reads the period's news in chunked calls and names
                                  # the tickers the press is flagging. Any provider, no web search.
event_agent_model: sonnet5        # CLOSES events: is this thesis still live, has its catalyst
                                  # resolved? Any provider; reads a gathered pool, no web search.
event_agent_effort: medium        # reasoning-effort knob for the event agent, where the model has one.
gather_model:                     # the LIVE web-search firehose. Web search is Anthropic-only, so this
                                  # is the one stage that must be an Anthropic model. Forward-only;
                                  # inert in the backtest. Blank falls back to the legacy `model` key.
org_tagger_model:                 # tags each ingested article with the organizations it names.
picker_effort: low                # reasoning-effort knob for the picker, when picker_model is set.

# ---------- CURATOR: what gets discovered, and when it is dropped ----------
retrieval_engine: gkg             # backtest news source (GDELT GKG on BigQuery). Forward always
                                  # uses live web search regardless of this setting.
discovery_filter: true            # gate the scout to headlines carrying the early-gem framing.
news_lookback_days: 0             # trailing days of news each scan reads. 0 = track rebalance_period.
news_cap: 0                       # per-scan cap on how many articles the scout reads. 0 = uncapped.
                                  # The forward test's daily pull fetches uncapped regardless.
relevance_filter: false           # run a second relevance pass over the pool before the scout reads it.
relevance_keep: 0                 # safety ceiling on the filtered pool. 0 = none.
scout_articles_per_call: 25       # BATCHING ONLY: how many articles' ticker-groups share one call.
max_article_chars: 1000           # how much of one article's text the curator sees.
min_bundle_articles: 2            # articles a company bundle needs before it is shown as a company.
event_news_cap: 15                # articles each event-agent re-reads per scan. Raising it costs money.
max_new_events: 0                 # new events admitted per scan. 0 = uncapped.
max_events: 0                     # how many events may be LIVE at once. 0 = uncapped.
picker_model:                     # BLANK = arithmetic coverage-rank (src/evscore.py). Naming a model
                                  # here swaps in an LLM ranker over the live events' evidence arcs.
exit_patience_scans: 1            # consecutive "thesis is dead" reads before a TICKER is dropped.
max_stale_scans: 6                # scans a held name may go unmentioned before it is dropped.
max_silent_scans: 6               # SILENCE CAP: scans of no fresh coverage before an EVENT retires.
max_event_scans: 10               # retires an EVENT at this age, in scans.
curator_memory_weeks: 6           # scans a retired ticker stays on the scout's do-not-re-propose list.
                                  # Counts SCANS, not weeks, whatever the cadence.

# ---------- OPTIMIZER: what gets funded, and how much ----------
initial_investment_usd: 50000     # day-0 dollars.
starter_watchlist: [AAPL, GOOGL, AMZN]   # day-0 holdings, equal weight, until the curator's own
                                  # picks replace them. Also the buy-and-hold control on the pages.
always_include: [SPY, BIL]        # always available to the optimizer; idle cash parks here.
                                  # Sits outside max_watchlist and never competes in the cull.
max_watchlist: 10                 # how many tickers may COMPETE for capital at once, not how many
                                  # hold it. A BOOK knob: it acts only at replay.
cull_rank: trend                  # how the cull chooses which events hold capital when more are
                                  # live than max_watchlist allows. Never reads conviction.
cull_fresh_slots: 1               # of those slots, how many are held for brand-new events, which
                                  # have no price history yet for a trend measure to judge.
cull_fresh_scans: 2               # how new counts as new, in scans.
drop_unfunded_weeks: 0            # scans a name may go unfunded before it leaves the watchlist. 0 = off.
unfunded_reentry_on_new_catalyst: true   # lets a dropped name back in, but only when the press
                                  # names it under a DIFFERENT thesis.
unfunded_cooldown_weeks: 0        # re-entry on a clock instead. 0 = drops are permanent until a new
                                  # catalyst. Measured and rejected as a lever; left here for sweeps.
concentration_cap: 0.35           # most of the book any one ticker may take.
exclude_young_reverse_split: [3, 0.1]    # [max years listed, worst reverse-split ratio]. Refuses to
                                  # fund the death-spiral pattern.
min_dollar_volume_usd: 100000     # universe floor on trailing 60-day median dollar volume.
min_trade_size: 0.0               # minimum weight change worth trading. 0 = off.
risk_aversion: 5.0                # lambda in mean-variance. Higher spreads wider and chases returns less.
optimizer_lookback_days: 45       # days of price history behind mu and Sigma.
rebalance_period: monthly         # weekly | biweekly | monthly | quarterly. The trading cadence.
t_update_days: 1                  # trading days between the signal and the trade.
risk_free_rate: 0.04              # Sharpe reporting only; not used in the weighting.
---

# Investor profile: template

This file is the single source of truth for every knob the curator and the optimizer read. Copy it
to `investor_profile.backtest.md` and edit the front matter above; nothing in this repo hardcodes a
value that has a source here.

The prose below the front matter is free-form and is not parsed. Use it to record what you are
trying to do and why, which is the part that turns out to matter most when a number surprises you
six weeks later.
