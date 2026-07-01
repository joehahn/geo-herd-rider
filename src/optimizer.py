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
  - load_financial_model   read the optimizer knobs from investor_profile.md
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

_FINANCIAL_MODEL_DEFAULTS: dict[str, Any] = {
    "initial_investment_usd": 50_000,  # LIVE (display/scale): day-0 dollars. The optimizer works in
                                       #   fractions, so this only sets dollar labels, not picks/weights.
    "risk_aversion": 1.0,              # LIVE: optimizer lambda (mean-variance)
    "concentration_cap": 0.25,         # LIVE: per-position max weight (top-level profile key)
    "max_tickers_per_event": 16,       # LIVE: cap on tickers kept per event's basket (the
                                       #   "limit the options" knob; truncates to the first N)
    "t_update_days": 1,                # LIVE: business days from event detection to execution
                                       #   (enter at that day's close). 1=next session, 2/3=wait.
    "min_trade_size": 0.0,             # LIVE: drop basket positions below this fraction and
                                       #   renormalize (pile in). ~1/N caps funded names near N.
    "lookback_period_days": 547,       # LIVE: trailing window (calendar days, ending at entry)
                                       #   for the optimizer's mu/Sigma fit. Short = noisier weights.
    "model": "mimo",                   # LIVE (curator/scan): which LLM reads the firehose. Short
                                       #   names resolved by resolve_curator_model(): mimo (cheap,
                                       #   OpenRouter) | sonnet | opus. Stamped into the scan + shown
                                       #   on the dashboards as the curator model that produced it.
    "risk_free_rate": 0.04,            # reporting only (Sharpe); not in the mean-variance weights
    "rebalance_days": 7,               # LIVE: the single cadence knob — the firehose scans/rebalances
                                       #   every N days AND reads that same trailing news window. 7=weekly.
    "news_lookback_days": None,        # optional: override the news window ONLY (advanced; rare
                                       #   sparse-coverage smoothing). None => news window = rebalance_days.
    "max_concurrent_positions": 0,     # LIVE (firehose backtest): fund only the top-N optimizer-weighted
                                       #   names/week (0 = uncapped). Visibility/risk cap on the tail.
    "prune_zero_weight_weeks": 0,      # LIVE (firehose backtest): drop a name the optimizer keeps
                                       #   starving (~0 weight) for this many weeks (0 = off).
    "hold_benchmark": False,           # LIVE (firehose backtest): park idle capital (cash residual after
                                       #   gem sizing) in SPY so the book starts 100% SPY, never sits in cash.
    "min_corroboration": 0,            # LIVE (firehose backtest): a name may only ENTER on a live read
                                       #   backed by >=N evidence sources (0 = off; kills thin 1-source theses).
    "reentry_block_weeks": 0,          # LIVE (firehose backtest): after a ticker exits, block re-entry for
                                       #   K weeks (0 = off; kills sequential-fragmentation re-opens).
    # Vestigial from portfolio-wave-rider's architecture — loaded but NOT applied here:
    "max_watchlist_size": 12,          # (no single rolling watchlist to cap)
}


# Curator-model registry: short name -> (provider model id, provider). The profile's `model` knob
# holds the short name; scanning + the dashboard resolve through here so there is ONE source of truth.
CURATOR_MODELS: dict[str, tuple[str, str]] = {
    "mimo":     ("xiaomi/mimo-v2.5-pro",          "openrouter"),  # ~1T MoE open-weight (cheap)
    "sonnet":   ("claude-sonnet-4-6",             "anthropic"),
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


def load_financial_model(profile_path: str = "investor_profile.md") -> dict[str, Any]:
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
