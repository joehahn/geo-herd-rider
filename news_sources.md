# News & influencer sources

User-managed list of sources the **news curator** consults when reading the week's aggregate
signal (the analog of portfolio-wave-rider's `news_sources.md`). Edit freely — no code depends
on the exact URLs; it's a preferred list, not an exclusive one.

**The premise.** Every author here — a head of state, a CEO, a central banker, a beat reporter —
is an *influencer* trying to nudge the herd. Some move markets far more than others. The curator
reads them in **aggregate** (not one-trigger-per-post) to decide what the dominant market-moving
development of the week is — the center of the decision tree — and where the causal branches head.

**How it's (to be) used.** Each weekly run, for the trailing `news_lookback_days` window, the
curator gathers signal from these sources via dated, look-ahead-safe search (`before:<date>`),
synthesizes the central development(s), then ladders to instruments. Sources that go dark or
paywall heavily can be dropped.

---

## Primary influencers (highest herd-moving power)

- **Donald Trump — Truth Social** — gathered directly and completely via `src/trump_feed.py`
  (timestamped archive). The highest-reach single influencer; **v1's main source.**
- *(planned)* **The Federal Reserve** — FOMC statements, minutes, the dot plot, Chair pressers,
  and Governors' speeches. Rate-path signal. https://www.federalreserve.gov/newsevents.htm
- *(planned)* **Elon Musk — X / @elonmusk** — moves single names (TSLA) and themes (AI, robotics,
  crypto, space).
- *(planned)* **Bank principals** — Jamie Dimon (JPM letters/interviews) and peers; macro &
  credit-cycle tone.
- *(planned)* **Congressional trading disclosures** — "Pelosi-watch" style feeds of lawmakers'
  filed trades (smart-money confirmation).

## Major market-moving news (geopolitics, macro, policy, commodities)

- **Reuters** — https://www.reuters.com — fast, broad, geopolitics + markets.
- **Bloomberg** — https://www.bloomberg.com — markets, deals, macro (paywalled).
- **The Wall Street Journal** — https://www.wsj.com — policy, corporate, markets (paywalled).
- **Financial Times** — https://www.ft.com — global macro, commodities, energy (paywalled).
- **CNBC** — https://www.cnbc.com — fast market reaction, earnings, Fed.
- **AP News** — https://apnews.com — wire coverage of breaking geopolitical events.

## Energy, shipping & commodities (the BWET chain lives here)

- **TradeWinds** — https://www.tradewindsnews.com — shipping/freight-rate trade press.
- **Lloyd's List** — https://www.lloydslist.com — maritime, tanker/dry-bulk rates, chokepoints.
- **OilPrice** — https://oilprice.com — crude, gas, refining, geopolitics of energy.
- **S&P Global Commodity Insights (Platts)** — https://www.spglobal.com/commodityinsights —
  benchmark commodity/freight pricing.

## General markets (catch-all)

- **MarketWatch** — https://www.marketwatch.com
- **Yahoo Finance** — https://finance.yahoo.com
