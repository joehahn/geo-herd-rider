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
#   * 2026-07-12 (NOT a discontinuity — same models): 3-knob split. The Anthropic requirement
#     moved off event_agent_model onto its own `gather_model` (the web-search firehose is the only
#     Anthropic-only stage); event_agent_model is now free to be any provider but is KEPT on sonnet5,
#     so the live candidate is byte-identical to the 07-10 freeze. gather_model=sonnet5=event_agent_model.
# ==========================================================================
# Active optimizer settings
gather_model: sonnet5              # FIREHOSE stage (live web-search gather). Web search is Anthropic-ONLY,
                                  #   so this MUST resolve to an Anthropic model. This is the ONLY stage that
                                  #   requires Anthropic. Choices:
                                  #   sonnet4  = claude-sonnet-4-6 (Anthropic)     ~$3.6
                                  #   sonnet5  = claude-sonnet-5 (Anthropic)       ~$3.8
                                  #   opus     = claude-opus-4-8 (Anthropic)       ~$4.4
event_agent_model: sonnet5         # JUDGMENT stage (per-event agents): live/exit switch + conviction. Reads the
                                  #   ALREADY-gathered pool with NO web search, so ANY provider works (decoupled
                                  #   from gather_model as of the 2026-07-12 3-knob split). Kept on sonnet5 for the
                                  #   frozen candidate; a cheaper judgment model is now a legal forward config.
scout_model: llama4                # EXTRACTION/ROUTING stage (scout + matcher): the cost driver, runs a
                                  #   cheap model. Any provider (no web search). Falls back to
                                  #   event_agent_model if unset.  llama4 = llama-4-maverick (OpenRouter) ~$0.3
initial_investment_usd: 50000     # Day-0 dollar allocation.
concentration_cap: 1.0            # Per-ticker max allocation.
risk_aversion: 0.1                # lambda in mean-variance utility (μᵀw − λ·wᵀΣw).
t_update_days: 1                  # Assumed number of business days from event detection to trade execution
min_trade_size: 0.0               # Drop holdings smaller than this & reallocate
max_agents: 7                     # PORTFOLIO cull: top-N EVENT-agents that hold capital (SPY/GLD added AFTER the cull via
                                  #   the picker path, not competing). 0 = uncapped.
max_events: 3                     # scout INFLOW cap: max NEW events/week (bounds event-agent LLM cost). 0 = uncapped.
picker_model: sonnet5             # forward --report's max_agents cull = the LLM agent-picker (src/picker.py). STRONG model
                                  #   required. FORWARD is the clean test of the picker (post-cutoff, no memorized winners).
                                  #   (spy_agent_conviction/defensive_agent_conviction below are IGNORED under the picker.)
news_cap: 500                     # Per-WEEK scout budget (the weekly --scan reads the freshest N of the week's pool; drops the older tail, warns on drop). 0=UNCAPPED. The daily --pull still fetches uncapped.
spy_agent_conviction: 5           # Conviction of the always-on SPY agent in the max_agents ranking; a live event must out-rank it to take a slot
defensive_agent_conviction: 5     # a 2nd always-on defensive-default agent (parks faded-event capital in the defensive asset); 0 = off
defensive_ticker: GLD             # defensive asset (GLD=gold, BND=bonds); auto-skipped on gems of the same theme
curator_memory_weeks: 8           # Weeks of RESOLVED catalysts the scout is reminded of so it won't re-chase a done thesis: 0 = off, <0 = whole history, >0 = last N weeks
lookback_period_days: 14          # Optimizer trailing lookback (calendar days); short = responsive to recent moves
momentum_gate_pct: 0.0            # CANDIDATE->LIVE momentum-confirmation gate. OFF here (frozen candidate) — under
                                  #   backtest validation in .backtest.md at 0.20; promote by setting this (a dated
                                  #   re-freeze) only once the FORWARD scoreboard confirms the lift (non-negotiable #6).
momentum_window_days: 30          # trailing calendar-day window for momentum_gate_pct (synced with .backtest.md).
rvol_gate: 0.0                    # breakout volume co-confirm. OFF here (frozen) — validated in .backtest.md at 1.5;
                                  #   promote with momentum_gate as one dated re-freeze once the forward confirms.
rvol_window_days: 20             # trailing trading-day window for RVOL (synced with .backtest.md).
trailing_low_days: 0             # let-winners-run N-day-low exit — OFF (redundant with the momentum gate in backtests).
aging_floor: 1                   # CURATOR aging->retire floor (synced with .backtest.md; keep at 1).
aging_patience: 0                # OFF here (frozen candidate) — validated post-hoc in .backtest.md at 3 (cuts concurrent
                                 #   agents ~60%, returns-neutral, revival-safe); promote once the forward confirms.
rebalance_days: 7                 # The firehose scans/rebalances every N days AND reads that same trailing news window
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
  - marketbeat.com                # 64% automated boilerplate (13F churn / consensus ratings / moving-avg crosses)
---
