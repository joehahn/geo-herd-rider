---
# ==========================================================================
# FROZEN FORWARD / PRODUCTION CONFIG — the live candidate under forward test.
#   * forward.py reads THIS file (falls back to investor_profile.backtest.md if absent).
#   * Keep STRATEGY knobs synced with investor_profile.backtest.md so the backtest stays a valid
#     proxy; RETRIEVAL-OPERATIONAL knobs (news_cap, gather_model, retrieval_engine) may differ.
#   * Changing any value = re-freezing a NEW candidate. Log it below and treat forward results
#     before/after as DIFFERENT SEGMENTS. Do NOT tune to backtest signal (CLAUDE.md #5/#6).
#
# Re-freeze log (dated discontinuities):
#   2026-07-07  seeded as a copy of the backtest candidate (cap 1.0 · risk 0.1 · 7/5/5 · sonnet5)
#   2026-07-10  `model` split into event_agent_model (sonnet5) + scout_model (llama4); window_cap -> news_cap
#   2026-07-12  3-knob split: gather_model broken out (the only Anthropic-only stage). NOT a
#               discontinuity — same models, byte-identical candidate.
#   2026-08-09  risk_aversion 0.1->1.0, lookback 14->45, concentration_cap 1.0->0.667, min_trade_size
#               0.0->0.1 promoted from the backtest; max_agents 5->7, max_new_events 2->3 adopted here.
#   2026-08-09  always_include / starter_watchlist / max_watchlist adopted from PWR; BIL is new, so
#               idle capital gains a zero-vol home. Unfunded prune ON (drop_unfunded 3, then 2 at
#               biweekly) with information-gated re-entry.
#   2026-08-09  rebalance_days 7 -> rebalance_period: biweekly. The *_scans knobs were halved with it
#               to hold their real-time horizons constant — a coherence fix, not a strategy change.
#               STRATEGY knobs (concentration_cap, risk_aversion, min_trade_size, max_watchlist) are
#               deliberately LEFT FROZEN while the 3-year backtest sweeps them, so the two profiles
#               are KNOWINGLY out of sync on those until that sweep promotes a dated re-freeze.
# ==========================================================================
# Active optimizer settings
gather_model: sonnet5              # FIREHOSE stage (live web-search gather). Web search is Anthropic-ONLY,
                                  #   so this MUST resolve to an Anthropic model. This is the ONLY stage that
                                  #   requires Anthropic. Choices:
                                  #   sonnet4  = claude-sonnet-4-6 (Anthropic)     ~$3.6
                                  #   sonnet5  = claude-sonnet-5 (Anthropic)       ~$3.8
                                  #   opus     = claude-opus-4-8 (Anthropic)       ~$4.4
event_agent_effort: high           # keep FULL reasoning for the live forward candidate (quality). (forward_engine
                                  #   currently uses the process_week default 'high'; backtest reads 'medium' for cost.)
event_agent_model: sonnet5         # JUDGMENT stage (per-event agents): live/exit switch + conviction. Reads the
                                  #   ALREADY-gathered pool with NO web search, so ANY provider works (decoupled
                                  #   from gather_model as of the 2026-07-12 3-knob split). Kept on sonnet5 for the
                                  #   frozen candidate; a cheaper judgment model is now a legal forward config.
scout_model: llama4                # EXTRACTION/ROUTING stage (scout + matcher): the cost driver, runs a
                                  #   cheap model. Any provider (no web search). Falls back to
                                  #   event_agent_model if unset.  llama4 = llama-4-maverick (OpenRouter) ~$0.3
picker_model: sonnet5             # PORTFOLIO-cull agent-picker (src/picker.py): forward --report ranks live events -> keep-list.
                                  #   FORWARD is the clean test of the picker (post-cutoff, no memorized winners). STRONG model required.
picker_effort: high               # forward = 1 picker call/week, trivial cost, so keep full reasoning (its likely only edge).
initial_investment_usd: 50000     # day-0 dollar allocation
always_include: [SPY, GLD]   # permanent optimizer anchors (equity/gold/T-bill), OUTSIDE max_watchlist
starter_watchlist: [AAPL, GOOGL, AMZN]   # inception holdings (equal-weight); aged out as the curator's picks take over
concentration_cap: 0.40          # per-position cap: a single position may be at most 66.7%
risk_aversion: 1.0                # λ in mean_variance utility (μᵀw − λ·wᵀΣw); higher = more diversified/risk-averse
t_update_days: 1                  # business days from event detection to trade execution
min_trade_size: 0.1               # drop positions below this fraction of the book and reallocate
max_watchlist: 6                  # hard cap on tickers that may hold capital; anchors ride outside it
cull_rank: trend                  # trend = trailing risk-adjusted return + freshness reserve; keep-first = legacy alphabetical
cull_fresh_slots: 3               # watchlist slots reserved for newly-opened events
cull_fresh_scans: 2               # how recent counts as fresh, in scans
drop_unfunded_weeks: 2            # prune a name the optimizer leaves unfunded this many SCANS running; 0 = off
unfunded_reentry_on_new_catalyst: true   # a pruned name returns when the curator names it under a DIFFERENT thesis
unfunded_cooldown_weeks: 0        # scans after a prune before a name is eligible again; 0 = never (release on evidence)
max_new_events: 6                 # scout inflow cap: max NEW events admitted per scan; 0 = uncapped
exit_patience_scans: 2            # consecutive explicit thesis-dead SCANS before a position exits (hysteresis vs churn)
max_stale_scans: 2                # SCANS a held name may go unmentioned before it is dropped
max_event_scans: 26               # AGE CAP: retire an event still live after this many scans (~1yr biweekly).
                                  #   The mechanical backstop for catalyst_resolved: a thesis that never resolves
                                  #   is a theme. max_stale_scans only fires on SILENCE, which a well-covered
                                  #   theme never triggers. 0 = OFF.
curator_memory_weeks: 4           # SCANS of resolved catalysts the scout is reminded of; 0 = off, <0 = all
news_cap: 500                     # articles the scout reads per scan; 0 = uncapped (the daily --pull is always uncapped)
event_news_cap: 20                # articles handed to EACH event-agent per scan (the curator cost knob); 0 = uncapped
relevance_filter: false                     # OFF: the forward's search index already does this, so the stage is inert relevance filter at pool assembly, standing in for the forward's
                                  #   here. Kept for parity so both profiles carry the same knobs.
relevance_keep: 0                         # SAFETY CEILING on the filtered pool; 0 = none (intended)
lookback_period_days: 45          # trailing calendar days of prices used to estimate μ and Σ
rebalance_period: biweekly        # weekly | biweekly | monthly | quarterly; how often the curator runs & rebalances,
                                  #   and the trailing news window each scan reads. NOTE the *_scans knobs above count
                                  #   SCANS, so their real-time horizon follows this.
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
  # commodities / critical minerals -- the rare-earth + uranium beats are the top evidence producers
  # and had 0 and 1 desks. mining.com scores 347 evidence-hits/1k articles, ~2x benzinga.
  - mining.com
  - northernminer.com
  - argusmedia.com
  - benchmarkminerals.com          # lithium/battery price authority; not crawled by GDELT, forward-only
  # memory / semis pricing (semianalysis does analysis, not prices):
  - digitimes.com
  - trendforce.com                 # DRAM/NAND price authority; not crawled by GDELT, forward-only
  # power grid / datacenter energy -- an uncovered sector the AI-datacenter theme keeps hitting:
  - utilitydive.com
  - powermag.com
  # tanker/shipping desks (seatrade-maritime is conference-focused):
  - splash247.com                  # not crawled by GDELT, forward-only
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
  # MarketBeat-network syndication clones of the above -- same bot templates, different masthead. A/B'd
  # 2026-08-07 on a 14d GKG pool: 374 articles (19.8% of the pool) and a hand read of a random sample
  # found ZERO pieces of real reporting. Title patterns alone left 66-72% of them standing (the bots have
  # unbounded variants), so the domain block is what clears them. insidermonkey.com and financialcontent.com
  # were candidates in the same A/B and are deliberately NOT blocked -- they carry genuine catalyst
  # reporting that names tickers, which is exactly what the firehose is for.
  - tickerreport.com
  - dailypolitical.com
  - themarketsdaily.com
  # MarketBeat-network clones (2026-08-10): 13,447 articles = 10.5% of the 3-year pool, 100% bot
  # templates (13F position changes, "Stock Price Down 8.3%"), 13.8 curator-evidence hits per 1k vs
  # benzinga's 179. Blocking costs 1.8% of evidence.
  - wkrb13.com
  - modernreaders.com
  - theenterpriseleader.com
  - etfdailynews.com
  # Motley Fool international editions -- foreign-exchange listicles, no US-listed relevance.
  # fool.co.uk produced ZERO curator evidence in 3 years. fool.ca is deliberately NOT blocked (small
  # volume, 81.6/1k, covers US-listed names).
  - fool.com.au
  - fool.co.uk
---
