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
concentration_cap: 0.8            # Per-ticker max allocation.
risk_aversion: 0.67              # lambda in mean-variance utility (μᵀw − λ·wᵀΣw).
t_update_days: 1                  # Assumed number of business days from event detection to trade execution
min_trade_size: 0.1               # Drop holdings smaller than this & reallocate
max_agents: 8                     # Keep only the top-N agents in the weekly watchlist, incl. the always-on SPY agent; 0 = uncapped
spy_agent_conviction: 4           # Conviction of the always-on SPY agent in the max_agents ranking; a live event must out-rank it to take a slot
defensive_agent_conviction: 4     # a 2nd always-on defensive-default agent (parks faded-event capital in the defensive asset); 0 = off
defensive_ticker: GLD             # defensive asset (GLD=gold, BND=bonds); auto-skipped on gems of the same theme
curator_memory_weeks: 8           # Weeks of RESOLVED catalysts the scout is reminded of so it won't re-chase a done thesis: 0 = off, <0 = whole history, >0 = last N weeks
lookback_period_days: 14           # Optimizer trailing lookback (calendar days); short = responsive to recent moves
rebalance_days: 7                 # The firehose scans/rebalances every N days AND reads that same trailing news window
risk_free_rate: 0.04              # reporting only (Sharpe); not in the weight optimization.
---
