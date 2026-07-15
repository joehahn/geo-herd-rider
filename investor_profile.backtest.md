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
event_agent_effort: medium         # reasoning effort for the per-event judgment call (the curator COST DRIVER,
                                  #   ~$0.056/call on sonnet5 at 'high'). 'medium' ~halves backtest curator cost.
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
scout_model: llama4                # EXTRACTION/ROUTING stage (scout + matcher): reads the whole firehose pool,
                                  #   so it's the token-cost driver -> runs a CHEAP model (llama4, OpenRouter ~$0.3/run).
                                  #   Any provider (no web search).
picker_model: sonnet5             # PORTFOLIO-cull agent-picker (src/picker.py): ranks live events on catalyst-arc -> keep-list.
                                  #   Opt-in (proto_select --picker / forward --report); INERT on plain dashboard rebuilds.
                                  #   STRONG model required (cheap pickers tie/trail random).
picker_effort: low                # Anthropic reasoning effort for the picker: 'low' = cheap/fast for backtest replays;
                                  #   'high' for forward (1 call/week, trivial cost, reasoning may be its only edge).
initial_investment_usd: 50000     # Day-0 dollar allocation.
concentration_cap: 0.667          # Per-ticker max allocation (conservative: no single name > ~2/3 the book).
risk_aversion: 0.5              # lambda in mean-variance utility (μᵀw − λ·wᵀΣw). Conservative: 0.5 favors lower-variance spread (was 0.1 = aggressive-μ).
t_update_days: 1                  # Assumed number of business days from event detection to trade execution
min_trade_size: 0.1               # Drop holdings smaller than 10% & reallocate (fewer dust positions).
max_agents: 5                     # PORTFOLIO cull: top-N EVENT-agents that hold capital. SPY + GLD appended AFTER the
                                  #   cull (not competing). With a picker (opt-in) the LLM ranks; else keep-first-N. 0=uncapped
drop_unfunded_weeks: 0            # CULL: drop an event the optimizer leaves UNFUNDED for N straight weeks. 0 = OFF.
                                  #   Set to 0 (2026-07-15): the 1000-draw Monte-Carlo showed =4 was an overfit lever
                                  #   (+38% Q1 / -45% H1 vs +0% neutral); =0 dominates on worst-window return AND gem-capture,
                                  #   and matches the frozen forward profile. Do not re-enable without cross-window support.
max_new_events: 2                 # scout INFLOW cap: max NEW events the scout admits/week (bounds event-agent LLM cost).
                                  #   Cheap cull = catalyst gate + (TODO) diversity tiebreak. 0 = uncapped. (was CANDIDATE_CAP)
news_cap: 0                       # Per-SCAN (per-week) cap on articles the scout reads; 0 = UNCAPPED.
curator_memory_weeks: 8           # Weeks of RESOLVED catalysts the scout is reminded of so it won't re-chase a done thesis: 0=off, <0=all, >0=last N
lookback_period_days: 14          # Optimizer trailing lookback (calendar days); short = responsive to recent moves
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
