# CLAUDE.md

Operating rules for Claude Code in the `geo-herd-rider` repo. Read this and the
[`README`](README.md) before doing anything here.

## What this project is

An LLM-curated, news-firehose-driven portfolio. Each week an AI reads the news firehose (plus
high-reach posts) and extracts the tickers the **financial press explicitly names** as
thesis-driven movers while still framed as early / under-the-radar; a plain mean-variance
optimizer then weights the watchlist. A position is held while its driving catalyst is live and
dropped when the thesis decays. The thesis: get to where the smarter part of the herd is already
heading — *as published, by ticker* — a little sooner than the slow herd. The earlier
causal-decision-tree design (have the LLM ladder the implication chain and bet the "middle band")
was **retired** once we found the press names the gem directly (see README, "Where the edge
actually was"). Full design in the [`README`](README.md) + [`agent_design.md`](agent_design.md); the empirical justification in `prior-work/`.

## Lineage — reuse, don't reinvent

- **Optimizer** is reused verbatim from `portfolio-wave-rider` (`src/optimizer.py`). Don't
  reimplement mean-variance; extend that file if you need more.
- **Firehose** (`src/firehose.py`) is the core: weekly scan → watchlist → optimizer → backtest.
  It reuses `curator._optimized_weights` (sizing), `score.py` (prices / entry timing), `costs.py`
  (LLM ledger), `trump_feed.py` (posts), `search.py` (look-ahead-safe Tavily), and `util.py`
  (shared helpers). `forward.py` is the look-ahead-clean forward eval.
- **Retired** (deleted): `map_event.py` (causal ladder), `synthesize.py` (central-development
  synthesis), `select_triggers.py` (tweet scout) — the decision-tree pipeline. `polymarket.py`
  and `llm.py` remain parked, not wired into the firehose.

## Non-negotiables

1. **The LLM never forecasts magnitude.** It picks which tickers the press names, and the
   live/exit switch — never expected return, weights, or position size. Sizing is mechanical and
   downstream. This is the load-bearing lesson from `portfolio-wave-rider`'s wave-tilt
   postmortem (numeric LLM tilts on μ destroyed value). Do not relax it.
2. **The early gem is the bet.** The value is catching a ticker the press has *named* (smart
   money already in) while it is still framed as *under-the-radar* (the herd hasn't piled in) —
   the "between smart money and herd" window. Read the firehose; don't reason a causal tree.
   Entry = press names it early; exit = its thesis decays (ceasefire, chokepoint reopens).
3. **The scoreboard is the filter, not the curator's confidence.** A source or curator change is
   kept only if the (forward) scoreboard shows it adds lift over the prior config. Numbers come
   from Python (`src/firehose.py`, `src/score.py`, `src/optimizer.py`), never from the LLM.
4. **Look-ahead hygiene everywhere.** The scan reasons only from pre-decision info; scoring
   fetches prices with explicit `start=/end=` bounds. Note the hard reality (proven): NO search
   tool gives true point-in-time retrieval — Anthropic `before:` and Tavily `end_date` both leak
   future-dated articles (`search.py` re-enforces the bound client-side off `published_date`).
   So a clean *retrospective* firehose test is impossible; the **forward paper trade is the only
   clean test**, and fixture/historical numbers are upper bounds, never the verdict.
5. **Don't tune the pre-registered lift bar to the data** once it's fixed. Same discipline
   that made geo-wave-rider credible.
6. **Backtesting is the development loop, not the verdict.** We iterate on historical data
   on purpose (forward is too slow), but every historical return is an **upper bound** —
   report it as such, prefer windows that postdate the curator model's training cutoff
   (less contaminated), and trust a backtest-driven win only once it survives the forward
   eval. Never tune the curator toward leaked signal.

## Scope discipline (baby steps)

Build one rung at a time, each gated by the (forward) scoreboard. **The firehose core is built**:
`firehose.py` scans the week's press for named gems → live watchlist → optimizer → weekly-
rebalanced backtest. The **mechanics are proven** via `--fixture` (a fixed set of the real early
BWET articles, look-ahead-clean per week): it enters BWET on the first under-the-radar write-up,
rides while the thesis is live, ~$50K → ~$157K (≈+210%) vs SPY ≈+9% — but this **assumes perfect
retrieval** and is therefore an upper bound, not lift. The dashboard (`scripts/build_dashboard.py`)
renders this fixture book + a firehose log page.

**The next rung is the forward eval** (`forward.py --scan` weekly, `--report`) — the only clean
test, since retrospective retrieval can't be de-contaminated (non-negotiable #4). Do NOT add new
firehose sources (Fed, Musk, Dimon, congressional trades) until the forward scoreboard shows the
news firehose pays. Confirm scope before jumping ahead.

## Conventions

- Python 3.12, std `venv`, deps in `requirements.txt`. Match the terse,
  docstring-heavy style already in `src/`.
- Deterministic work in Python; judgment (which gems the press names, the live/exit switch) in the LLM.
- Default model for LLM work is `claude-opus-4-8`; `claude-sonnet-4-6` is the cheaper
  option for high-volume curator/backtest runs.
- Outputs land in `data/`; `data/prices_cache/` is gitignored. Don't commit the venv.
- Commit/push only when asked. Commit-message trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
