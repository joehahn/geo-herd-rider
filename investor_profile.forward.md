---
# ==========================================================================
# FROZEN FORWARD / PRODUCTION CONFIG — the live candidate under forward test.
#   * forward.py reads THIS file (falls back to investor_profile.backtest.md if absent).
#   * Backtest / sweeps / gem-dashboard dev uses investor_profile.backtest.md, which is
#     free to keep evolving. This file is DELIBERATELY independent, not synced.
#   * Do NOT tune this to backtest signal (CLAUDE.md #5/#6). Changing any value
#     = re-freezing a NEW candidate — note it as a dated discontinuity in the
#     forward series (or start a fresh series).
#   * Seeded 2026-07-07 as an exact copy of investor_profile.backtest.md: the aggressive
#     backtest-settled candidate (cap 1.0 · risk 0.1 · 7/5/5 · sonnet5).
#   * 2026-07-10 re-freeze (dated discontinuity): split the single `model` knob into
#     event_agent_model (sonnet5, judgment + Anthropic gather) + scout_model (llama4, cheap
#     scout+matcher); renamed window_cap -> news_cap (per-week scout budget). Scout is now
#     an UNVALIDATED cheap model — treat forward results after this date as a new segment.
# ==========================================================================
# Active optimizer settings
event_agent_model: sonnet5         # JUDGMENT stage (per-event agents) AND the live gather (Anthropic web
                                  #   search) — so this MUST resolve to an Anthropic model. Choices:
                                  #   sonnet4  = claude-sonnet-4-6 (Anthropic)     ~$3.6
                                  #   sonnet5  = claude-sonnet-5 (Anthropic)       ~$3.8
                                  #   opus     = claude-opus-4-8 (Anthropic)       ~$4.4
scout_model: llama4                # EXTRACTION/ROUTING stage (scout + matcher): the cost driver, runs a
                                  #   cheap model. Any provider (no web search). Falls back to
                                  #   event_agent_model if unset.  llama4 = llama-4-maverick (OpenRouter) ~$0.3
initial_investment_usd: 50000     # Day-0 dollar allocation.
concentration_cap: 1.0            # Per-ticker max allocation.
risk_aversion: 0.1                # lambda in mean-variance utility (μᵀw − λ·wᵀΣw).
t_update_days: 1                  # Assumed number of business days from event detection to trade execution
min_trade_size: 0.0               # Drop holdings smaller than this & reallocate
max_agents: 7                     # Keep only the top-N agents in the weekly watchlist, incl. the always-on SPY agent; 0 = uncapped
news_cap: 0                       # Per-WEEK scout budget (articles the weekly --scan reads). 0 = UNCAPPED. The daily --pull fetches uncapped regardless; this caps only the weekly scout read. Renamed from window_cap + set 80->0 on 2026-07-10 (dated discontinuity).
spy_agent_conviction: 5           # Conviction of the always-on SPY agent in the max_agents ranking; a live event must out-rank it to take a slot
defensive_agent_conviction: 5     # a 2nd always-on defensive-default agent (parks faded-event capital in the defensive asset); 0 = off
defensive_ticker: GLD             # defensive asset (GLD=gold, BND=bonds); auto-skipped on gems of the same theme
curator_memory_weeks: 8           # Weeks of RESOLVED catalysts the scout is reminded of so it won't re-chase a done thesis: 0 = off, <0 = whole history, >0 = last N weeks
lookback_period_days: 14          # Optimizer trailing lookback (calendar days); short = responsive to recent moves
rebalance_days: 7                 # The firehose scans/rebalances every N days AND reads that same trailing news window
gather_engine: anthropic          # forward gather: anthropic (Brave live web search) or tavily (date-honoring, reaches old weeks). Default anthropic.
risk_free_rate: 0.04              # reporting only (Sharpe); not in the weight optimization.
# --- forward web-search domain steering (two-pass gather). Curate by OUTLET TYPE, never by "named a winner". ---
specialty_allow:                  # GEM pass allowlist: specialty desks that carry the early gem call (reaches Cloudflare-walled etf.com)
  # generalist stock/ETF desks (cover ALL sectors incl. maritime/energy):
  - etf.com
  - benzinga.com
  - seekingalpha.com
  - etftrends.com
  - stocktitan.net
  - tipranks.com
  - marketbeat.com
  - barchart.com
  # sector trade press (from portfolio-wave-rider/news_sources.md; tech-growth + defense — deepen those verticals):
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
mill_block:                       # COVERAGE pass blocklist: "N stocks to buy" listicle mills that crowd out the gem call
  - fool.com
  - 247wallst.com
  - nerdwallet.com
  - kiplinger.com
  - money.usnews.com
  - stockstory.org
  - defenseworld.net              # automated aggregator / content farm (122 low-quality hits in the backtest)
  - ts2.tech                      # AI-generated content farm
---
