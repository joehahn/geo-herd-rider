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
    "lookback_period_days": 14,        # LIVE: trailing window (calendar days, ending at entry)
                                       #   for the optimizer's mu/Sigma fit. Short = noisier weights.
    "gather_model": "sonnet5",         # LIVE, FORWARD-ONLY (firehose): the LLM that runs the live web-search
                                       #   gather (forward_gather). Web search is Anthropic-only, so this MUST
                                       #   resolve to an Anthropic model. The backtest has NO gather (its pool
                                       #   is GDELT/Tavily), so this knob is inert there — like news_cap it may
                                       #   legitimately differ from .forward.md. Falls back to event_agent_model
                                       #   (then legacy `model:`) if unset.
    "event_agent_model": "sonnet5",     # LIVE (judgment): the LLM that runs the per-event agents (the
                                       #   live/exit switch + conviction). Reads the ALREADY-gathered pool with
                                       #   NO web search, so ANY provider works (decoupled from gather_model as
                                       #   of the 3-knob split). Keep on a strong model for judgment quality.
                                       #   Short names resolved by resolve_curator_model(). (Legacy `model:` is
                                       #   still read as a fallback for all stages.)
    "scout_model": "llama4",           # LIVE (extraction/routing): the cheap, high-volume LLM that reads
                                       #   the firehose pool and does the scout + matcher stages. This is
                                       #   where the token cost lives, so it runs a cheap model (llama4,
                                       #   OpenRouter). Falls back to event_agent_model if unset.
    "risk_free_rate": 0.04,            # reporting only (Sharpe); not in the mean-variance weights
    "momentum_gate_pct": 0.0,          # LIVE (candidate->live PROMOTION gate): a curator-named ticker is only
                                       #   FUNDED once its own realized trailing return (over momentum_window_days)
                                       #   clears this threshold — price-confirmation that the market is already
                                       #   rewarding the thesis (the LLM conviction is a weak return-predictor; a
                                       #   +20%/1mo gate lifted backtests +17..+157% across MP/HL/BWET). Below the
                                       #   gate a name stays a monitored CANDIDATE (unfunded). 0.0 = OFF (no gate).
                                       #   Deterministic + look-ahead-clean (trailing). Meant to be SWEPT.
    "momentum_window_days": 30,        # LIVE: trailing calendar-day window for the momentum_gate_pct confirmation.
    "rvol_gate": 0.0,                  # LIVE (breakout CO-confirmation): only FUND a name whose recent volume >=
                                       #   rvol_gate x its trailing-avg volume — a +X% move on THIN volume is a false
                                       #   breakout (rejects the "caught-but-fake" case). 1.5 = 150% of avg. 0.0 = OFF.
    "rvol_window_days": 20,            # LIVE: trailing trading-day window for the RVOL average.
    "trailing_low_days": 0,            # LIVE (let-winners-run EXIT): unfund a LIVE name that makes a new N-trading-day
                                       #   price low (Turtle-style trailing breakdown) — cut losers, let winners ride.
                                       #   0 = OFF. Typical 10 (tight) or 20 (loose).
    "aging_floor": 1,                  # CURATOR (aging->retire): conviction at/below which a LIVE event counts as
                                       #   "aging" (a spent/faded thesis). Paired with aging_patience.
    "aging_patience": 0,               # CURATOR: retire an event once it's been at/below aging_floor for this many
                                       #   consecutive weeks -> stops it spawning an agent (clears the concurrent-agent
                                       #   pileup). Revival-safe (scout may re-nominate on fresh news). 0 = OFF.
    "rebalance_days": 7,               # LIVE: the single cadence knob — the firehose scans/rebalances
                                       #   every N days AND reads that same trailing news window. 7=weekly.
    "news_lookback_days": None,        # optional: override the news window ONLY (advanced; rare
                                       #   sparse-coverage smoothing). None => news window = rebalance_days.
    "news_cap": 0,                     # per-SCAN (per-week) cap on how many articles the scout reads
                                       #   (most-recent kept); ONE meaning everywhere. 0 = UNCAPPED. The
                                       #   forward's daily pull fetches uncapped; only this weekly scout
                                       #   read is capped. (backtest_gdelt overrides via --news-cap.)
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
    "max_agents": 7,                   # LIVE (PORTFOLIO cull): keep only the top-N EVENT-agents that hold capital.
                                       #   SPY/GLD are NOT agents here (added to the optimizer AFTER the cull). When a
                                       #   caller passes a picker, the LLM agent-picker ranks; else the legacy conviction
                                       #   sort. 0 = uncapped. (Old spy/defensive-agent-conviction ranking is legacy.)
    "max_events": 3,                   # LIVE (scout INFLOW cap): max NEW events the scout admits per week. Bounds
                                       #   event-agent creation -> weekly LLM cost. Enforced CHEAPLY (catalyst gate,
                                       #   then a mechanical diversity/novelty tiebreak — NOT the picker, NOT reward-
                                       #   ranking, NOT source-count). Rename of the old CANDIDATE_CAP=3. 0 = uncapped.
    "picker_model": "sonnet5",         # the model src/picker.make_picker uses for the max_agents cull WHEN a caller
                                       #   opts in (proto_select --picker / forward). INERT otherwise (no auto LLM calls
                                       #   on dashboard rebuilds). Needs a STRONG model — cheap pickers tie/trail random.
    "picker_effort": "low",            # Anthropic reasoning effort for the picker call. 'low' = cheap/fast (a ranking
                                       #   task needs little thinking) — use for backtest replays. 'high' for forward
                                       #   (1 call/week, trivial cost, and reasoning may be the picker's only edge).
    "spy_agent_conviction": 5,
    "defensive_agent_conviction": 5,   # LIVE (firehose backtest): a 2nd always-on "agent" (defensive default, e.g.
                                       #   gold) at this conviction; a faded event ranked below it is displaced and
                                       #   capital parks in the defensive asset. 0 = off. Auto-skipped on same-theme gems.
    "defensive_ticker": "GLD",         # the defensive asset the defensive-agent parks in (GLD=gold, BND=bonds, ...)         # LIVE (firehose backtest): SPY as an always-on "agent" that always recommends SPY — a synthetic
                                       #   candidate at this conviction that a live event must OUT-RANK to be held;
                                       #   else capital parks in SPY. Replaces the mechanical hold_benchmark add. 0 = off.
    "hold_benchmark": True,            # LIVE (firehose backtest): SPY always in the optimizer universe
                                       #   (gems must beat SPY to be funded; idle capital rides the market).
    "curator_memory_weeks": 8,         # LIVE (scan): weeks of RESOLVED catalysts the scout is reminded of
                                       #   (so it won't re-chase a done thesis): 0 = off, <0 = whole history, >0 = last N.
    # Vestigial from portfolio-wave-rider's architecture — loaded but NOT applied here:
    "max_watchlist_size": 12,          # (no single rolling watchlist to cap)
}


# Curator-model registry: short name -> (provider model id, provider). The profile's `model` knob
# holds the short name; scanning + the dashboard resolve through here so there is ONE source of truth.
CURATOR_MODELS: dict[str, tuple[str, str]] = {
    "mimo":     ("xiaomi/mimo-v2.5-pro",          "openrouter"),  # ~1T MoE open-weight (cheap)
    "sonnet4":   ("claude-sonnet-4-6",             "anthropic"),
    "sonnet5":  ("claude-sonnet-5",               "anthropic"),  # near-Opus reasoning, intro $2/$10
    "opus":     ("claude-opus-4-8",               "anthropic"),
    # bake-off models (all OpenRouter):
    "llama4":   ("meta-llama/llama-4-maverick",   "openrouter"),  # 400B MoE / 17B active
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
    out.update({k: v for k, v in data.items() if k in _FINANCIAL_MODEL_DEFAULTS})
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
