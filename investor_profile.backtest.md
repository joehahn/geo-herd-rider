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
gather_model: sonnet5              # FIREHOSE stage (live web-search gather) — Anthropic-ONLY. INERT in the
                                  #   backtest (no live gather; the pool is GDELT/Tavily), so this is a
                                  #   forward-only knob like news_cap; kept = .forward.md for validity.
event_agent_model: sonnet5         # JUDGMENT stage (the per-event agents: live/exit switch + conviction).
                                  #   Reads the gathered pool with NO web search -> ANY provider (3-knob split,
                                  #   2026-07-12). On sonnet5 (Anthropic) for the strong-judgment HL rerun — the
                                  #   test of whether a real judge holds conviction through the live thesis and
                                  #   calls the exit near the peak (vs llama4's noise). scout_model stays llama4.
                                  #   Keep on a strong model. Choices (approx $/run):
                                  #   deepseek = deepseek-chat V3 (OpenRouter)     ~$0.1
                                  #   llama4   = llama-4-maverick (OpenRouter)     ~$0.3
                                  #   mimo     = xiaomi/mimo-v2.5-pro (OpenRouter) ~$0.4
                                  #   sonnet4  = claude-sonnet-4-6 (Anthropic)     ~$3.6
                                  #   sonnet5  = claude-sonnet-5 (Anthropic)       ~$3.8
                                  #   grok4    = x-ai/grok-4.3 (OpenRouter)        ~$3.7
                                  #   opus     = claude-opus-4-8 (Anthropic)       ~$4.4
scout_model: sonnet5               # EXTRACTION/ROUTING stage (scout + matcher): reads the whole firehose
                                  #   pool, so it's the token-cost driver. TEMPORARILY on sonnet5 for the HL
                                  #   matcher-fix confirmation (does a strong matcher split silver HL/SVM off
                                  #   MP's rare-earth event?). Restore llama4 after — this is the cost driver.
initial_investment_usd: 50000     # Day-0 dollar allocation.
concentration_cap: 1.0            # Per-ticker max allocation.
risk_aversion: 0.1              # lambda in mean-variance utility (μᵀw − λ·wᵀΣw).
t_update_days: 1                  # Assumed number of business days from event detection to trade execution
min_trade_size: 0.0               # Drop holdings smaller than this & reallocate
max_agents: 7                     # PORTFOLIO cull: top-N EVENT-agents that hold capital (SPY/GLD added AFTER the cull,
                                  #   not competing). With a picker (opt-in) the LLM ranks; else the legacy conviction sort. 0=uncapped
max_events: 3                     # scout INFLOW cap: max NEW events the scout admits per week (bounds event-agent LLM
                                  #   cost). Cheap cull = catalyst gate + (TODO) diversity tiebreak. 0 = uncapped. (was CANDIDATE_CAP)
picker_model: sonnet5             # the max_agents cull = the LLM agent-picker (src/picker.py), ranking live events on
                                  #   catalyst-arc + P&L -> keep-list. STRONG model required (cheap ties/trails random).
                                  #   Used by proto_select --picker + forward --report; INERT on plain dashboard rebuilds.
news_cap: 0                       # Per-SCAN (per-week) cap on articles the scout reads; 0 = UNCAPPED.
spy_agent_conviction: 5           # Conviction of the always-on SPY agent in the max_agents ranking; a live event must out-rank it to take a slot
defensive_agent_conviction: 0     # OFF (2026-07-14, per request): idle/rotated-off capital parks in SPY only, no
                                  #   GLD<->SPY hops. Gold exposure comes only via a LIVE gold gem (GDX/NEM) — the
                                  #   "very good reason to ride GLD". (Was 5 = always-on GLD floor competing with SPY.)
defensive_ticker: GLD             # defensive asset (GLD=gold, BND=bonds); auto-skipped on gems of the same theme
curator_memory_weeks: 8           # Weeks of RESOLVED catalysts the scout is reminded of so it won't re-chase a done thesis: 0 = off, <0 = whole history, >0 = last N weeks
lookback_period_days: 14           # Optimizer trailing lookback (calendar days); short = responsive to recent moves
momentum_gate_pct: 0.0            # OFF (2026-07-14). CANDIDATE->LIVE gate: only FUND a curator-named ticker once
                                  #   its trailing return (over momentum_window_days) clears this. It lifted GAINS
                                  #   on the whole-era book (+17..+157%), but (a) we no longer judge by gains, and
                                  #   (b) waiting for +20% before entry is in TENSION with non-negotiable #2 (catch
                                  #   the gem EARLY/under-the-radar, before the herd) — it enters LATE, so the agent
                                  #   captures little of its assigned ticker's rise. 0.0 = fund on curator-naming.
momentum_window_days: 30          # trailing calendar-day window for momentum_gate_pct.
rvol_gate: 0.0                    # OFF (2026-07-14). BREAKOUT VOLUME CO-CONFIRM: fund a name only if recent volume
                                  #   >= Nx its 20-day avg. The +$79K "win" was OVERFIT to the whole-era book; on the
                                  #   per-gem thematic books it EVICTS the gem from its own dashboard — volume fades
                                  #   faster than price during a real run, so the climbing gem fails the 1.5x test at
                                  #   most rebalances and capital parks in calmer peers/SPY (MP capture 28%->59% with
                                  #   this OFF; MP was never even held with it ON). Delete if still unused later (TODO).
rvol_window_days: 20             # trailing trading-day window for the RVOL average.
trailing_low_days: 0             # let-winners-run N-day-low exit. OFF: redundant with the +20% momentum gate here
                                  #   (a name at a 20d low already fails the gate) — swept 2026-07-13, no effect.
aging_floor: 1                    # CURATOR aging->retire: conviction at/below which a live event is "aging". Keep at 1
                                  #   — floor=2 retires funded/revivable events (MP -62%, TSM -45%, CIFR -69% in the sweep).
aging_patience: 3                 # retire an event after this many consecutive weeks at <= aging_floor -> stops it
                                  #   spawning an agent. Validated post-hoc 2026-07-14: peak concurrency 20-27 -> 8-9,
                                  #   returns flat-to-+50% (TSM), revival-safe (BWET flat). 0 = OFF.
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
---
