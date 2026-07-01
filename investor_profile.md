---
# Active optimizer settings 
model: sonnet                     # Curator LLM that reads the firehose. Choices:
                                  #   deepseek = deepseek-chat V3 (OpenRouter)     ~$0.1  -- DEFAULT: caught all 3 gems, cheapest
                                  #   llama4   = llama-4-maverick (OpenRouter)     ~$0.3
                                  #   mimo     = xiaomi/mimo-v2.5-pro (OpenRouter) ~$0.4
                                  #   sonnet   = claude-sonnet-4-6 (Anthropic)     ~$3.6
                                  #   grok4    = x-ai/grok-4.3 (OpenRouter)        ~$3.7
                                  #   opus     = claude-opus-4-8 (Anthropic)       ~$4.4
initial_investment_usd: 50000     # Day-0 dollar allocation.
concentration_cap: 0.9            # Per-ticker max allocation.
risk_aversion: 0.25               # lambda in mean-variance utility (μᵀw − λ·wᵀΣw).
max_tickers_per_event: 16         # Cap on tickers kept per event.
t_update_days: 1                  # Assumed number of business days from event detection to trade execution
min_trade_size: 0.1               # Drop holdings smaller than this & reallocate
max_concurrent_positions: 2       # Visibility/risk cap: fund only the top-N optimizer-weighted names/week (0 = uncapped)
prune_zero_weight_weeks: 4        # Drop a name the optimizer keeps starving (~0 weight) for this many weeks (0 = off)
hold_benchmark: true              # SPY is ALWAYS in the optimizer watchlist/universe (every week); gems must beat SPY to be funded, else capital stays in SPY
min_corroboration: 0              # OFF: helped BWET but clipped MP's thin-sourced WINNERS (RKLB/WSR) — evidence-count != conviction (fails cross-gem)
reentry_block_weeks: 0            # OFF (the 'K weeks' hack): re-holds are prevented at the SOURCE now — the scout is told which catalysts resolved and won't re-chase the hype
lookback_period_days: 7           # Optimizer's Trailing lookback window, in calendar days
rebalance_days: 7                 # The firehose scans/rebalances every N days AND reads that same trailing news window
risk_free_rate: 0.04              # reporting only (Sharpe); not in the weight optimization.
---
