"""Mean-variance optimizer + price/return helpers.

Reused from the sibling project portfolio-wave-rider (`src/portfolio.py`) — the
proven optimizer spine, extracted clean of that project's wave-specific curator,
backtest, and dashboard code. Same contract: the LLM never touches these numbers;
it only decides which tickers enter the watchlist, and this module weights whatever
results. See https://github.com/joehahn/portfolio-wave-rider.

Public surface:
  - fetch_prices       download adjusted-close prices via yfinance
  - compute_returns    log-returns + annualized mean (mu) + covariance (Sigma)
  - optimize_portfolio mean-variance optimization via scipy
  - risk_metrics       Sharpe, vol, max drawdown, VaR, CVaR for a weight vector
  - analyze            one-shot: fetch + returns + optimize + risk
  - load_financial_model   read the optimizer knobs from investor_profile.backtest.md
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.optimize import minimize

TRADING_DAYS = 252

# Defaults MIRROR the settled investor-profile candidate (backtest.md == forward.md on strategy knobs,
# as of 2026-07-10): cap 1.0 · risk 0.1 · 7/5/5 · lookback 14 · sonnet5-event / llama4-scout · news_cap 0.
# They are the fallback for a profile that omits a knob, so keeping them == the live config means an
# omission degrades gracefully to what we actually run, not to a stale conservative floor.
_FINANCIAL_MODEL_DEFAULTS: dict[str, Any] = {
    "initial_investment_usd": 50_000,  # LIVE (display/scale): day-0 dollars. The optimizer works in
                                       #   fractions, so this only sets dollar labels, not picks/weights.
    "risk_aversion": 0.1,              # LIVE: optimizer lambda (mean-variance)
    "concentration_cap": 1.0,          # LIVE: per-position max weight (top-level profile key)
    "t_update_days": 1,                # LIVE: business days from event detection to execution
                                       #   (enter at that day's close). 1=next session, 2/3=wait.
    "min_trade_size": 0.0,             # LIVE: drop basket positions below this fraction and
                                       #   renormalize (pile in). ~1/N caps funded names near N.
    "optimizer_lookback_days": 14,     # LIVE: trailing window (calendar days, ending at entry) behind
                                       #   mu and Sigma. Renamed 2026-08-12 from lookback_period_days to
                                       #   say WHOSE lookback it is -- news_lookback_days is the other one.
    "lookback_period_days": 14,        # LIVE: trailing window (calendar days, ending at entry) [LEGACY ALIAS of optimizer_lookback_days]
                                       #   for the optimizer's mu/Sigma fit. Short = noisier weights.
    "model": "sonnet5",                # LEGACY/UMBRELLA: the default for every curator stage. The per-stage
                                       #   knobs below override it (chain: scout -> event -> model). Registered
                                       #   here because load_financial_model DROPS any key absent from this dict
                                       #   -- omitting it silently sent judgment back to the sonnet5 default.
    "gather_model": None,      # unset -> falls back to event_agent_model, then `model`         # LIVE, FORWARD-ONLY (firehose): the LLM that runs the live web-search
                                       #   gather (forward_gather). Web search is Anthropic-only, so this MUST
                                       #   resolve to an Anthropic model. The backtest has NO gather (its pool
                                       #   is GDELT/Tavily), so this knob is inert there — like news_cap it may
                                       #   legitimately differ from .forward.md. Falls back to event_agent_model
                                       #   (then legacy `model:`) if unset.
    "event_agent_model": None, # unset -> falls back to `model`     # LIVE (judgment): the LLM that runs the per-event agents (the
                                       #   live/exit switch + conviction). Reads the ALREADY-gathered pool with
                                       #   NO web search, so ANY provider works (decoupled from gather_model as
                                       #   of the 3-knob split). Keep on a strong model for judgment quality.
                                       #   Short names resolved by resolve_curator_model(). (Legacy `model:` is
                                       #   still read as a fallback for all stages.)
    "event_agent_effort": "high",       # Anthropic reasoning effort for the per-event JUDGMENT call (the curator cost
                                       #   driver: ~$0.056/call on sonnet5 at 'high'). 'medium' roughly halves cost for
                                       #   backtest curator runs; keep 'high' for the forward candidate. Only affects
                                       #   Anthropic event models (ignored on OpenRouter).
    "scout_model": None,       # unset -> falls back to event_agent_model, then `model`           # LIVE (extraction/routing): the cheap, high-volume LLM that reads
                                       #   the firehose pool and does the scout + matcher stages. This is
                                       #   where the token cost lives, so it runs a cheap model (llama4,
                                       #   OpenRouter). Falls back to event_agent_model if unset.
    "picker_model": None,              # PORTFOLIO-cull agent-picker (src/picker.make_picker): ranks live events -> keep-list.
                                       #   Default None so ABSENT = OFF, which is what an omitted knob should mean. It used to
                                       #   default to sonnet5, so deleting the blank profile line silently ENABLED a ~$10/run picker.
                                       #   Opt-in (proto_select --picker / forward); INERT on plain dashboard rebuilds. Needs a
                                       #   STRONG model — cheap pickers tie/trail random.
    "picker_effort": "low",            # Anthropic reasoning effort for the picker call: 'low' = cheap/fast (ranking needs little
                                       #   thinking) for backtest replays; 'high' for forward (1 call/week, trivial cost).
    "risk_free_rate": 0.04,            # reporting only (Sharpe); not in the mean-variance weights
    "rebalance_period": "weekly",      # LIVE: the cadence, NAMED (weekly|biweekly|monthly|quarterly) -- PWR's
                                       #   vocabulary, adopted 2026-08-09. THE ONLY cadence knob: always read it
                                       #   through util.resolve_cadence(), never as a raw dict key.
                                       #   `rebalance_days` was the old numeric knob and is RETIRED (2026-08-14).
                                       #   Leaving it here as a default was not harmless: load_financial_model
                                       #   injected 7 into every profile, so any code that read the raw key got 7
                                       #   for a run whose real cadence was 30 -- which is exactly what happened
                                       #   twice in build_cbt_dashboard (a gate bar 5x too small, and a watchlist
                                       #   span 4x too short). A retired knob that still resolves to a plausible
                                       #   number is worse than one that is absent. A numeric override now lives
                                       #   ONLY on the CLI (--rebalance-days), where it cannot masquerade as config.
    "news_lookback_days": None,        # optional: override the news window ONLY (advanced; rare
                                       #   sparse-coverage smoothing). None => news window follows the cadence.
    "max_events": 0,                   # LIVE (scan): how many events may be LIVE AT ONCE; 0 = uncapped. The
                                       #   picker decides which survive. Prefer this over max_new_events: an
                                       #   ADMISSION cap bins candidates unexamined and forever, a CONCURRENCY
                                       #   cap leaves them rankable next scan. Needs picker_model set.
    "discovery_filter": False,         # LIVE (scan): gate the SCOUT's pool to headlines carrying the gem tell
                                       #   (a superlative + under-the-radar framing). Keeps ~7% of the corpus, so
                                       #   the scout -- 91% of the LLM bill -- reads ~10x less, and an added source
                                       #   can no longer be crowded out of the admission slots by routine coverage.
                                       #   The EVENT AGENTS are unaffected: they still read the full corpus, because
                                       #   tracking an event needs its ordinary follow-up, which carries no superlative.
    "news_lookback_days": 0,           # LIVE: trailing calendar days of news each scan reads. 0 = follow
                                       #   the cadence. Set it LONGER than the cadence for a deliberate
                                       #   OVERLAP, so an article GDELT indexes late -- or one published
                                       #   right on a scan boundary -- still gets read on the next scan
                                       #   instead of falling in the gap. PWR carries the same knob.
    "news_cap": 0,                     # per-SCAN (per-week) cap on how many articles the scout reads
                                       #   (most-recent kept); ONE meaning everywhere. 0 = UNCAPPED. The
                                       #   forward's daily pull fetches uncapped; only this weekly scout
                                       #   read is capped. (backtest_gdelt overrides via --news-cap.)
    "retrieval_engine": "gkg",         # BACKTEST-ONLY discovery engine (forward always uses web search):
                                       #   "gkg" = GDELT's GKG on BigQuery (src/gkg.py) -- one partitioned
                                       #     SQL query per window, no throttle, semantic theme+org gating,
                                       #     vocabulary from retrieval_config.json. Needs gcp-key.json.
                                       #   "doc" = the legacy GDELT DOC API (src/gdelt.py) -- keyless, but
                                       #     measured at 67% HTTP-429 and ~28 items/min. The no-key fallback.
                                       #   Both are date-honest (look-ahead-clean); they differ in the
                                       #   SURFACE they match (DOC = full text, GKG = title+URL), so the two
                                       #   pools are NOT interchangeable article-for-article.
    # forward web-search domain steering (forward_gather two-pass). Curate by OUTLET TYPE, never by outcome.
    "specialty_allow": ["etf.com", "benzinga.com", "seekingalpha.com", "etftrends.com", "stocktitan.net",
                        "tipranks.com", "barchart.com", "zerohedge.com",   # generalist stock/ETF + macro desks (all sectors)
                        "semianalysis.com", "spacenews.com", "payloadspace.com", "therobotreport.com",
                        "endpts.com", "statnews.com", "biopharmadive.com", "quantumcomputingreport.com",
                        "world-nuclear-news.org", "breakingdefense.com", "defensenews.com",  # sector trade press (tech-growth/defense)
                        "seatrade-maritime.com", "kitco.com"],  # maritime + commodities desks (early tanker/gold theses)
    "mill_block": ["fool.com", "247wallst.com", "nerdwallet.com", "kiplinger.com", "money.usnews.com",
                   "stockstory.org", "defenseworld.net", "ts2.tech",   # listicle mills + content farms
                   "marketbeat.com"],  # 64% automated boilerplate (13F churn / consensus ratings / moving-avg crosses)
    "cull_rank": "trend",              # how the max_watchlist cull chooses who holds capital:
                                       #   "trend"      = trailing risk-adjusted return + a freshness reserve (free,
                                       #                  deterministic; 83rd %ile vs a 60-seed random null)
                                       #   "keep-first" = the legacy ev[:N] over an ALPHABETICAL list (67th %ile)
                                       #   a `picker` passed in code overrides both.
    "cull_fresh_slots": 3,             # slots reserved for events first seen within cull_fresh_scans. A trailing
                                       #   statistic cannot see a catalyst younger than its own window, so without
                                       #   this the trend rank evicts exactly the early gems (non-negotiable #2).
    "cull_fresh_scans": 2,             # how recent counts as "fresh", in SCANS.
    "max_watchlist": 7,                # PORTFOLIO cull: hard cap on the tickers that may hold capital. In GHR one held
                                       #   ticker IS one event-agent, so this is the same cap the old `max_agents` named --
                                       #   renamed 2026-08-09 to share PWR's vocabulary. `max_agents` still works as a
                                       #   deprecated alias (firehose reads max_watchlist first). always_include names ride
                                       #   post-cull and are NOT agents. With a picker the LLM ranks the keep-list; without
                                       #   one, a deterministic keep-first-N. 0 = uncapped.
    "max_agents": 7,                   # DEPRECATED alias for max_watchlist. Kept because the sweeps, the gem dashboards and
                                       #   the frozen forward profile all still write it; remove once those are migrated.
    "max_new_events": 3,               # scout INFLOW cap: max NEW events the scout admits/week (bounds event-agent LLM cost).
                                       #   Enforced by the catalyst gate + (TODO) a diversity tiebreak. 0 = uncapped. (was CANDIDATE_CAP)
    "drop_unfunded_weeks": 3,          # UNFUNDED PRUNE: drop a name the optimizer leaves UNFUNDED (weight ~0) this many
                                       #   CONSECUTIVE weeks, freeing its capital for events the math will actually back.
                                       #   Turned ON 2026-08-09: against a matched null (same drop count, same weeks, random
                                       #   victims) it scores 88th-100th %ile for every N in 2..8, so the signal is robust.
                                       #   3 = the smallest count that means "persistent" rather than "a blip"; do NOT tune
                                       #   it to the dollar peak, which is one path's noise (CLAUDE.md #5). 0 = OFF.
    "unfunded_cooldown_weeks": 0,      # RE-ENTRY on a CLOCK. Keep at 0. Measured 2026-08-10 and REJECTED: 4wk $58K,
                                       #   8wk $104K, 12wk $94K against $130K for the new-catalyst release, and
                                       #   non-monotone. A clock readmits a name with NO new information, so the same
                                       #   fading thesis walks back in. Release on evidence, not elapsed time. Dropped
                                       #   from the profiles so it does not invite being switched on.
    "unfunded_reentry_on_new_catalyst": False,  # RE-ENTRY: let a dropped name back the moment the curator names it under a
                                       #   DIFFERENT thesis (a new bet, not the old one).
    "always_include": ["SPY", "GLD"],   # PERMANENT optimizer anchors, appended AFTER the max_watchlist cull and
                                       #   OUTSIDE it -- they never compete for a watchlist slot. Idle capital always has a
                                       #   home: SPY = equity beta, GLD = gold. BIL (T-bills) was added 2026-08-09 on
                                       #   PWR's precedent and REMOVED 2026-08-10: swept over a fixed curation it cost
                                       #   ~$40K of book value ($154K->$194K, $180K->$205K). A zero-volatility asset is
                                       #   exactly what a variance-penalised objective wants to hold, so it absorbs
                                       #   capital by construction rather than by thesis.
                                       #   [] = no anchors.
    "starter_watchlist": ["AAPL", "GOOGL", "AMZN"],  # INCEPTION holdings, equal-weight, held from day 0 until the sticky
                                       #   watch ages them out (MAX_STALE weeks unmentioned) as the curator's own picks take
                                       #   over. Also the basket the CBT's buy-and-hold baseline is built from. Deliberately
                                       #   a boring mega-cap base chosen WITHOUT hindsight about the backtest window --
                                       #   its job is to be a fair yardstick, not a good portfolio. [] = start empty.
    "defensive_ticker": "GLD",         # DEPRECATED, superseded by always_include. Still read by the pre-GKG gem dashboards
                                       #   (scripts/build_dashboard.py) and by the always_include fallback. "" = none.
    "exit_patience_scans": 2,          # consecutive explicit thesis-dead SCANS before a position exits (hysteresis
                                       #   vs churn). Counts SCANS, so its real-time length follows rebalance_period.
                                       #   Kept at 2 even biweekly: 2 is the minimum that IS hysteresis, and a
                                       #   catalyst_resolved verdict bypasses it with an immediate hard exit anyway.
    "max_stale_scans": 4,              # SCANS a held name may go UNMENTIONED before it is dropped. Also counts SCANS
                                       #   -- halve it when you halve the cadence, or the silence timeout doubles.
    "relevance_filter": False,         # BACKTEST-ONLY: cheap-LLM relevance filter at pool assembly (src/relevance.py),
                                       #   the stand-in for the forward's search-engine ranking. NO quota -- it judges
                                       #   each article on its merits, so pool size floats with the week's news the way
                                       #   the forward's does. Inert forward (the search index already filters).
    "relevance_keep": 0,               # SAFETY CEILING on the filtered pool; 0 = none (intended).
    "max_group_articles": 12,          # LIVE: articles per TICKER-GROUP handed to the scout, newest
                                       #   kept. Without it a mega-cap swamps the call -- NVDA carried
                                       #   261 articles in one 30-day window against Rocket Lab's 51,
                                       #   so one group would crowd out every other. Newest-kept
                                       #   because a group is read to answer "is the driver still
                                       #   running", which the recent end answers.
    "max_article_orgs": 4,             # LIVE: above this many subject companies, an article is treated
                                       #   as a LISTICLE and joins a ticker-group ONLY if that ticker is
                                       #   named in its TITLE. At or below it, the article joins every
                                       #   org's group, because a 2-3 company story is genuine evidence
                                       #   for each. Measured 2026-08-14: listicles are 4% of gate-passers
                                       #   and 82% of them name NO company in the title -- "3 Stocks to
                                       #   Make the Most of the Surge in Crude Oil" joins nothing, while
                                       #   "Why Rocket Lab Is Skyrocketing Now" still joins RKLB. The
                                       #   title is the publisher's claim about what the piece is ABOUT;
                                       #   the org list is only what it MENTIONS.
    "max_article_chars": 800,          # LIVE: how much of ONE article's text the curator sees. Was
                                       #   hardcoded in THREE disagreeing places -- lede.enrich_live and
                                       #   lede.apply cut at 280, then agent._block cut again at 200,
                                       #   which was the binding limit. 200 chars of an insidermonkey
                                       #   listicle is "We recently compiled a list of..." -- the driver,
                                       #   if stated, is past the cut, so the scout saw a stock that moved
                                       #   with no reason attached. This caps ONE article; it deliberately
                                       #   does NOT cap a ticker-group's total, because the signal is
                                       #   corroboration ACROSS articles ("RKLB is skyrocketing" + "$5.6B
                                       #   Neutron win"), and capping the group would re-create the bug.
    "event_news_cap": 20,              # articles handed to EACH event-agent per scan. THE cost knob: the judgment
                                       #   stage is ~95% of the curator bill and its input is this slice, so cost is
                                       #   scans x live-events x this. Binds in 76% of event-weeks (median event-week
                                       #   matches 55 articles). 0 = uncapped.
    "max_event_scans": 26,             # AGE CAP: retire an event still live after this many SCANS. The mechanical
                                       #   backstop for the catalyst_resolved problem -- a catalyst that has not
                                       #   resolved in 26 scans (~1yr biweekly) is a THEME by the design's own
                                       #   definition. max_stale_scans only fires on SILENCE, which a well-covered
                                       #   theme never triggers. The scout may re-propose on fresh evidence. 0 = OFF.
    "curator_memory_weeks": 8,         # LIVE (scan): weeks of RESOLVED catalysts the scout is reminded of
                                       #   (so it won't re-chase a done thesis): 0 = off, <0 = whole history, >0 = last N.
}


# Curator-model registry: short name -> (provider model id, provider). The profile's `model` knob
# holds the short name; scanning + the dashboard resolve through here so there is ONE source of truth.
# MEASURED $/M input tokens (2026-08-10, from data/llm_costs.csv on the 3-year run -- not vendor list
# prices): llama-4-maverick $0.401, deepseek-v4-flash $0.280. The old per-run estimates that used to sit
# in both investor profiles were stale and are gone; this registry is the one home for model facts.
CURATOR_MODELS: dict[str, tuple[str, str]] = {
    "mimo":     ("xiaomi/mimo-v2.5-pro",          "openrouter"),  # ~1T MoE open-weight (cheap)
    "sonnet4":   ("claude-sonnet-4-6",             "anthropic"),
    "sonnet5":  ("claude-sonnet-5",               "anthropic"),  # near-Opus reasoning, intro $2/$10
    "opus":     ("claude-opus-4-8",               "anthropic"),
    # bake-off models (all OpenRouter):
    "llama4":   ("meta-llama/llama-4-maverick",   "openrouter"),  # 400B MoE / 17B active
    "deepseek4": ("deepseek/deepseek-v4-flash",        "openrouter"),  # cheap JUDGMENT candidate
    "deepseek": ("deepseek/deepseek-chat",        "openrouter"),  # V3, 671B MoE / 37B active
    "grok4":    ("x-ai/grok-4.3",                 "openrouter"),  # grok-4 deprecated -> 4.3 (frontier reasoning)
}


def resolve_curator_model(short: str) -> tuple[str, str]:
    """Map a profile `model` short name (mimo|sonnet|opus) to (model_id, provider).
    Unknown names fall back to mimo (the safe, cheap default)."""
    return CURATOR_MODELS.get(str(short).strip().lower(), CURATOR_MODELS["mimo"])


def resolve_stage_models(fm: dict) -> tuple[tuple[str, str], tuple[str, str]]:
    """CURATOR stage split from a loaded financial model. Returns
    ((scout_id, scout_provider), (event_id, event_provider)).

    * event_agent_model — the judgment stage (event agents); reads the already-gathered
      pool with no web search, so it may be ANY provider (decoupled from the gather).
    * scout_model — the cheap high-volume extraction/routing stage (scout + matcher);
      falls back to the event model if unset.
    * The live web-search GATHER is a THIRD, separate stage — see resolve_gather_model.
    * Legacy: a single `model:` key (old profiles/archives) is honored as the fallback
      for both curator stages, so pre-split configs keep resolving unchanged."""
    legacy = fm.get("model") or "sonnet5"
    event_short = fm.get("event_agent_model") or legacy
    scout_short = fm.get("scout_model") or event_short
    return resolve_curator_model(scout_short), resolve_curator_model(event_short)


def resolve_gather_model(fm: dict) -> tuple[str, str]:
    """The live web-search GATHER model (the 'firehose' stage) -> (model_id, provider).

    Web search is Anthropic-only, so this must resolve to an Anthropic model (the caller —
    forward.py — validates the provider and errors clearly otherwise). Forward-only: the
    backtest has no gather. Falls back to event_agent_model, then legacy `model:`, if unset."""
    short = fm.get("gather_model") or fm.get("event_agent_model") or fm.get("model") or "sonnet5"
    return resolve_curator_model(short)


def load_financial_model(profile_path: str = "investor_profile.backtest.md") -> dict[str, Any]:
    """Read the optimizer knobs from the profile's YAML front matter; missing fields fall back
    to defaults. Knobs are flat top-level keys (one per line). The optimizer is always
    mean-variance — `risk_aversion` (lambda) is the only investor-facing knob.

    A legacy nested `financial_model:` block is still honored (top-level keys win) so old
    profiles keep loading."""
    import re
    import yaml

    p = Path(profile_path)
    if not p.exists():
        return dict(_FINANCIAL_MODEL_DEFAULTS)
    text = p.read_text()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return dict(_FINANCIAL_MODEL_DEFAULTS)
    data = yaml.safe_load(m.group(1)) or {}
    out = dict(_FINANCIAL_MODEL_DEFAULTS)
    legacy = data.get("financial_model")
    if isinstance(legacy, dict):
        out.update(legacy)
    known = {k: v for k, v in data.items() if k in _FINANCIAL_MODEL_DEFAULTS}
    out.update(known)
    # LOUD about the silent-drop trap (CLAUDE.md): a knob absent from _FINANCIAL_MODEL_DEFAULTS is
    # dropped without a word, so a profile edit can look applied and do nothing. `_meta` keys and the
    # legacy nested block are exempt; everything else gets named.
    unknown = [k for k in data
               if k not in _FINANCIAL_MODEL_DEFAULTS and k != "financial_model" and not k.startswith("_")]
    # Keep the renamed knob and its legacy alias in lockstep, in whichever direction the profile
    # wrote it. Existing readers of either name then see the same number and no call site has to move.
    _new, _old = "optimizer_lookback_days", "lookback_period_days"
    if _old in data and _new not in data:
        out[_new] = out[_old]
    elif _new in data:
        out[_old] = out[_new]

    if unknown:
        import sys as _sys
        print(f"WARN {p.name}: {len(unknown)} profile key(s) NOT in optimizer._FINANCIAL_MODEL_DEFAULTS "
              f"and therefore IGNORED: {sorted(unknown)}", file=_sys.stderr)
    return out


def _period_to_start(period: str) -> pd.Timestamp | None:
    """Parse '1.3y'/'6mo'/'30d' into a start Timestamp; None for 'max'/'ytd'
    (which yfinance handles natively). Supports fractional periods yfinance rejects."""
    import re
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(d|mo|y)", period.strip())
    if not m:
        return None
    n = float(m.group(1))
    days = {"d": n, "mo": n * 30, "y": n * 365}[m.group(2)]
    return pd.Timestamp.today().normalize() - pd.Timedelta(days=days)


def fetch_prices(tickers: list[str], period: str = "3y", interval: str = "1d",
                 min_history: bool = False) -> pd.DataFrame:
    """Adjusted-close prices via yfinance.

    With min_history=True, drop tickers whose history doesn't span ~the full
    lookback before the row-wise dropna — a single recent IPO would otherwise
    truncate the whole panel to its first trading day and collapse the covariance
    estimate. Excluded tickers land on `.attrs['excluded_short_history']`."""
    if not tickers:
        raise ValueError("tickers must be non-empty")
    clean = [t.upper().strip() for t in tickers]
    start = _period_to_start(period)
    if start is not None:
        end = pd.Timestamp.today().normalize() + pd.Timedelta(days=1)
        data = yf.download(clean, start=start, end=end, interval=interval,
                           auto_adjust=True, progress=False, group_by="column")
    else:
        data = yf.download(clean, period=period, interval=interval,
                           auto_adjust=True, progress=False, group_by="column")
    if data.empty:
        raise RuntimeError(f"yfinance returned no data for {clean} over {period}")

    prices = data["Close"] if isinstance(data.columns, pd.MultiIndex) \
        else data[["Close"]].rename(columns={"Close": clean[0]})
    prices = prices.dropna(how="all").ffill()

    excluded: list[str] = []
    if min_history and start is not None and len(prices) > 0:
        window_days = (prices.index[-1] - start).days
        cutoff = start + pd.Timedelta(days=round(0.05 * window_days))
        eligible = [t for t in prices.columns
                    if (fv := prices[t].first_valid_index()) is not None and fv <= cutoff]
        excluded = [t for t in prices.columns if t not in eligible]
        if not eligible:
            raise RuntimeError(
                f"no ticker has enough history to span the {period} lookback; "
                f"excluded: {excluded}")
        prices = prices[eligible]

    prices = prices.dropna()
    prices.attrs["excluded_short_history"] = excluded
    return prices


def compute_returns(prices: pd.DataFrame, frequency: str = "daily") -> dict[str, Any]:
    """Log-returns + annualized mean + covariance from a prices frame."""
    factor = {"daily": TRADING_DAYS, "weekly": 52, "monthly": 12}[frequency]
    log_returns = np.log(prices / prices.shift(1)).dropna()
    return {
        "log_returns": log_returns,
        "mean": log_returns.mean() * factor,
        "cov": log_returns.cov() * factor,
        "annualization": factor,
    }


def optimize_portfolio(
    returns: dict[str, Any],
    objective: str = "max_sharpe",
    risk_free_rate: float = 0.04,
    target_return: float | None = None,
    max_weight: float = 1.0,
    min_weight: float = 0.0,
    risk_aversion: float = 1.0,
) -> dict[str, Any]:
    """Solve the mean-variance problem and return weights + summary stats.

    Objectives: max_sharpe (tangent portfolio), min_variance, mean_variance
    (maximize mu^T w - lambda * w^T Sigma w), target_return. Long-only by default
    with an optional per-asset cap."""
    if objective not in {"max_sharpe", "min_variance", "target_return", "mean_variance"}:
        raise ValueError(f"unknown objective: {objective}")
    if objective == "target_return" and target_return is None:
        raise ValueError("target_return is required when objective='target_return'")
    if objective == "mean_variance" and risk_aversion < 0:
        raise ValueError("risk_aversion (lambda) must be >= 0 for mean_variance objective")

    tickers = list(returns["mean"].index)
    mu = returns["mean"].to_numpy(dtype=float)
    sigma = returns["cov"].to_numpy(dtype=float)
    n = len(tickers)

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    if objective == "target_return":
        constraints.append({"type": "eq", "fun": lambda w: float(w @ mu) - target_return})

    bounds = [(min_weight, max_weight)] * n
    w0 = np.full(n, 1.0 / n)

    if objective == "max_sharpe":
        def neg_sharpe(w: np.ndarray) -> float:
            vol = float(np.sqrt(w @ sigma @ w))
            return 0.0 if vol < 1e-10 else -(float(w @ mu) - risk_free_rate) / vol
        result = minimize(neg_sharpe, w0, method="SLSQP", bounds=bounds, constraints=constraints)
    elif objective == "mean_variance":
        result = minimize(lambda w: -(w @ mu) + risk_aversion * (w @ sigma @ w),
                          w0, method="SLSQP", bounds=bounds, constraints=constraints)
    else:
        result = minimize(lambda w: w @ sigma @ w, w0, method="SLSQP",
                          bounds=bounds, constraints=constraints)

    if not result.success:
        return {"success": False, "message": result.message, "objective": objective}

    w = result.x
    vol = float(np.sqrt(w @ sigma @ w))
    ret = float(w @ mu)
    weights = {t: float(w[i]) for i, t in enumerate(tickers)}
    at_bound = [t for i, t in enumerate(tickers)
                if abs(w[i] - max_weight) < 1e-4 or abs(w[i] - min_weight) < 1e-4]

    return {
        "success": True,
        "objective": objective,
        "weights": weights,
        "expected_annual_return": ret,
        "annual_volatility": vol,
        "sharpe_ratio": (ret - risk_free_rate) / vol if vol > 1e-10 else None,
        "assets_at_boundary": at_bound,
        "concentration_warning": (
            f"Top holding is {max(weights, key=weights.get)} at "
            f"{max(weights.values()) * 100:.1f}%."
            if max(weights.values()) > 0.5 else None
        ),
    }


def risk_metrics(
    returns: dict[str, Any],
    weights: dict[str, float],
    risk_free_rate: float = 0.04,
    var_confidence: float = 0.95,
) -> dict[str, Any]:
    """Portfolio Sharpe, vol, max drawdown, VaR, CVaR for the given weights."""
    log_returns = returns["log_returns"]
    missing = [t for t in log_returns.columns if t not in weights]
    if missing:
        raise ValueError(f"weights missing for tickers: {missing}")
    w = np.array([weights[t] for t in log_returns.columns], dtype=float)
    port = pd.Series(log_returns.values @ w, index=log_returns.index)

    ann_ret = float(port.mean() * TRADING_DAYS)
    ann_vol = float(port.std() * np.sqrt(TRADING_DAYS))
    sharpe = (ann_ret - risk_free_rate) / ann_vol if ann_vol > 1e-10 else None
    equity = (1 + port).cumprod()
    max_dd = float(((equity - equity.cummax()) / equity.cummax()).min())

    alpha = 1 - var_confidence
    var = float(np.quantile(port.values, alpha))
    below_var = port.values[port.values <= var]

    return {
        "annual_return": ann_ret,
        "annual_volatility": ann_vol,
        "sharpe_ratio": float(sharpe) if sharpe is not None else None,
        "max_drawdown": max_dd,
        "var_1d": var,
        "cvar_1d": float(below_var.mean()) if below_var.size else var,
        "var_confidence": var_confidence,
        "n_observations": len(port),
        "period_start": str(port.index[0].date()),
        "period_end": str(port.index[-1].date()),
    }


def analyze(
    tickers: list[str],
    period: str = "3y",
    objective: str = "max_sharpe",
    max_weight: float = 0.25,
    risk_free_rate: float = 0.04,
    risk_aversion: float = 1.0,
) -> dict[str, Any]:
    """Run the full pipeline and return a single JSON-serializable dict."""
    prices = fetch_prices(tickers, period=period, min_history=True)
    returns = compute_returns(prices)
    opt = optimize_portfolio(
        returns, objective=objective, risk_free_rate=risk_free_rate,
        max_weight=max_weight, risk_aversion=risk_aversion,
    )
    risk = risk_metrics(returns, opt["weights"], risk_free_rate=risk_free_rate) \
        if opt.get("success") else None

    return {
        "tickers": list(prices.columns),
        "excluded_short_history": prices.attrs.get("excluded_short_history", []),
        "period": {
            "start": str(prices.index[0].date()),
            "end": str(prices.index[-1].date()),
            "n_observations": len(prices),
        },
        "last_prices": {t: float(prices[t].iloc[-1]) for t in prices.columns},
        "annualized_mean_return": {k: float(v) for k, v in returns["mean"].items()},
        "annualized_volatility": {
            t: float(np.sqrt(returns["cov"].loc[t, t])) for t in returns["cov"].index
        },
        "optimization": opt,
        "risk": risk,
    }


if __name__ == "__main__":
    import sys
    print(json.dumps(analyze(sys.argv[1:] or ["SPY", "QQQ", "GLD"]), indent=2))
