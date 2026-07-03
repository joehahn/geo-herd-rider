---
# Active optimizer settings 
model: sonnet5                     # Curator LLM that reads the firehose. Choices:
                                  #   deepseek = deepseek-chat V3 (OpenRouter)     ~$0.1  
                                  #   llama4   = llama-4-maverick (OpenRouter)     ~$0.3
                                  #   mimo     = xiaomi/mimo-v2.5-pro (OpenRouter) ~$0.4
                                  #   sonnet4  = claude-sonnet-4-6 (Anthropic)     ~$3.6  
                                  #   sonnet5  = claude-sonnet-5 (Anthropic)       ~$3.8  
                                  #   grok4    = x-ai/grok-4.3 (OpenRouter)        ~$3.7
                                  #   opus     = claude-opus-4-8 (Anthropic)       ~$4.4
initial_investment_usd: 50000     # Day-0 dollar allocation.
concentration_cap: 0.9            # Per-ticker max allocation.
risk_aversion: 0.67              # lambda in mean-variance utility (μᵀw − λ·wᵀΣw).
max_tickers_per_event: 16         # Cap on tickers kept per event.
t_update_days: 1                  # Assumed number of business days from event detection to trade execution
min_trade_size: 0.0               # Drop holdings smaller than this & reallocate
max_agents: 2                     # Keep only the top-N agents (by catalyst-conviction) in the weekly watchlist, incl. the always-on SPY agent; 0 = uncapped; sweepable
trailing_stop_pct: 0.0            # Mechanical peak-exit: force-exit a held name once it is this fraction below its trailing high; 0 = off
prune_zero_weight_weeks: 0        # Drop a name the optimizer keeps starving (~0 weight) for this many weeks (0 = off)
spy_agent_conviction: 0           # Conviction of the always-on SPY agent in the max_agents ranking; a live event must out-rank it to take a slot, else capital parks in SPY (0 = SPY agent off)
curator_memory_weeks: 8           # Weeks of RESOLVED catalysts the scout is reminded of so it won't re-chase a done thesis: 0 = off, <0 = whole history, >0 = last N weeks; sweepable
lookback_period_days: 7           # Optimizer trailing lookback (calendar days); short = responsive to recent moves
rebalance_days: 7                 # The firehose scans/rebalances every N days AND reads that same trailing news window
risk_free_rate: 0.04              # reporting only (Sharpe); not in the weight optimization.
---
