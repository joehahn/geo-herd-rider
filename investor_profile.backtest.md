---
# ==========================================================================
# BACKTEST / DEV CONFIG — free to evolve. The GDELT backtest (backtest_gdelt.py),
# sweeps, gem-dashboards, and the model bake-off read THIS file. To promote a
# backtest-settled candidate to the live forward test, copy the STRATEGY knobs into
# investor_profile.forward.md (a dated re-freeze; see that file's header).
#   * Keep the STRATEGY knobs (event_agent_model, scout_model, max_agents, floors,
#     risk_aversion, concentration_cap) in sync with .forward.md so the backtest stays a
#     valid proxy; only RETRIEVAL-operational knobs (news_cap) legitimately differ.
#   * backtest_gdelt.py can override news_cap via --news-cap (CLI); the value below is
#     the default for profile-reading tools (run_harness).
# ==========================================================================
# Active optimizer settings
event_agent_model: sonnet5         # JUDGMENT stage (the per-event agents: live/exit switch + conviction).
                                  #   Keep on a strong model. Choices (approx $/run):
                                  #   deepseek = deepseek-chat V3 (OpenRouter)     ~$0.1
                                  #   llama4   = llama-4-maverick (OpenRouter)     ~$0.3
                                  #   mimo     = xiaomi/mimo-v2.5-pro (OpenRouter) ~$0.4
                                  #   sonnet4  = claude-sonnet-4-6 (Anthropic)     ~$3.6
                                  #   sonnet5  = claude-sonnet-5 (Anthropic)       ~$3.8
                                  #   grok4    = x-ai/grok-4.3 (OpenRouter)        ~$3.7
                                  #   opus     = claude-opus-4-8 (Anthropic)       ~$4.4
scout_model: llama4                # EXTRACTION/ROUTING stage (scout + matcher): reads the whole firehose
                                  #   pool, so it's the token-cost driver -> runs a cheap model. Any provider
                                  #   (needs no web search). Falls back to event_agent_model if unset.
initial_investment_usd: 50000     # Day-0 dollar allocation.
concentration_cap: 1.0            # Per-ticker max allocation.
risk_aversion: 0.1              # lambda in mean-variance utility (μᵀw − λ·wᵀΣw).
t_update_days: 1                  # Assumed number of business days from event detection to trade execution
min_trade_size: 0.0               # Drop holdings smaller than this & reallocate
max_agents: 7                     # Keep only the top-N agents in the weekly watchlist, incl. the always-on SPY agent; 0 = uncapped
news_cap: 0                       # Per-SCAN (per-week) cap on articles the scout reads; 0 = UNCAPPED. backtest_gdelt overrides via --news-cap.
spy_agent_conviction: 5           # Conviction of the always-on SPY agent in the max_agents ranking; a live event must out-rank it to take a slot
defensive_agent_conviction: 5     # a 2nd always-on defensive-default agent (parks faded-event capital in the defensive asset); 0 = off
defensive_ticker: GLD             # defensive asset (GLD=gold, BND=bonds); auto-skipped on gems of the same theme
curator_memory_weeks: 8           # Weeks of RESOLVED catalysts the scout is reminded of so it won't re-chase a done thesis: 0 = off, <0 = whole history, >0 = last N weeks
lookback_period_days: 14           # Optimizer trailing lookback (calendar days); short = responsive to recent moves
rebalance_days: 7                 # The firehose scans/rebalances every N days AND reads that same trailing news window
risk_free_rate: 0.04              # reporting only (Sharpe); not in the weight optimization.
# --- forward web-search domain steering (used by forward_gather; synced here for visibility). Curate by OUTLET TYPE. ---
specialty_allow:                  # GEM pass allowlist: specialty desks that carry the early gem call
  # generalist stock/ETF desks (all sectors):
  - etf.com
  - benzinga.com
  - seekingalpha.com
  - etftrends.com
  - stocktitan.net
  - tipranks.com
  - marketbeat.com
  - barchart.com
  # sector trade press (from portfolio-wave-rider/news_sources.md; tech-growth + defense):
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
  - kitco.com
mill_block:                       # COVERAGE pass blocklist: "N stocks to buy" listicle mills
  - fool.com
  - 247wallst.com
  - nerdwallet.com
  - kiplinger.com
  - money.usnews.com
  - stockstory.org
  - defenseworld.net              # automated aggregator / content farm (122 low-quality hits in the backtest)
  - ts2.tech                      # AI-generated content farm
---
