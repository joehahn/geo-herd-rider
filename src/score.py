"""score.py — mechanical price/timing utilities for the firehose.

The shared price layer: `fetch_panel` (look-ahead-safe adjusted-close panel via yfinance, cached)
and `entry_index` (resolve the actable entry close with the execution lag), plus the `BENCHMARK`.
The per-event scorer + report + CLI that once lived here belonged to the retired decision-tree
pipeline (its `events_mapped.csv` input came from the deleted map_event.py) and have been removed.

Look-ahead hygiene: prices are pulled with explicit ``start=/end=`` bounds (never a relative
period), so nothing after a window's end can leak into it.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

REPO_ROOT = Path(__file__).resolve().parent.parent
PRICE_CACHE = REPO_ROOT / "data" / "prices_cache" / "panel.csv"
VOLUME_CACHE = REPO_ROOT / "data" / "prices_cache" / "volume.csv"

BENCHMARK = "SPY"

# t_update_days: business days from the (post-close, ~4:30pm cron) detection of an event to when
# the trade is placed — enter that many trading days after the first actable close, at that day's
# CLOSE. 1 = next session, 2/3 = wait. (Fractional 0.5 = next-morning OPEN needs intraday data.)
T_UPDATE_DAYS = 1

ET = "America/New_York"
MARKET_CLOSE_HOUR = 16  # 16:00 ET


def fetch_panel(tickers: list[str], start: str, end: str, use_cache: bool = True) -> pd.DataFrame:
    """Adjusted-close panel for `tickers` over [start, end] (DatetimeIndex, tz-naive). Cached to
    data/prices_cache/panel.csv so a re-run is offline and reproducible."""
    tickers = sorted(set(tickers))
    if use_cache and PRICE_CACHE.exists():
        cached = pd.read_csv(PRICE_CACHE, index_col=0, parse_dates=True)
        if set(tickers).issubset(cached.columns) and cached.index.min() <= pd.Timestamp(start) \
                and cached.index.max() >= pd.Timestamp(end):
            return cached[tickers]

    raw = yf.download(tickers, start=start, end=end, interval="1d", auto_adjust=True, progress=False)
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"yfinance returned no data for {tickers}")
    prices = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    if isinstance(prices, pd.Series):
        prices = prices.to_frame(tickers[0])
    elif list(prices.columns) == ["Close"]:
        prices = prices.rename(columns={"Close": tickers[0]})
    prices.index = pd.to_datetime(prices.index).tz_localize(None)

    PRICE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    prices.to_csv(PRICE_CACHE)
    return prices


def fetch_volume_panel(tickers: list[str], start: str, end: str, use_cache: bool = True) -> pd.DataFrame:
    """Daily VOLUME panel for `tickers` over [start, end] (DatetimeIndex, tz-naive), cached to
    data/prices_cache/volume.csv. Powers the RVOL breakout-confirmation gate (a +X% move on thin
    volume is a false breakout). Same look-ahead hygiene as fetch_panel (explicit start/end)."""
    tickers = sorted(set(tickers))
    if use_cache and VOLUME_CACHE.exists():
        cached = pd.read_csv(VOLUME_CACHE, index_col=0, parse_dates=True)
        if set(tickers).issubset(cached.columns) and cached.index.min() <= pd.Timestamp(start) \
                and cached.index.max() >= pd.Timestamp(end):
            return cached[tickers]

    raw = yf.download(tickers, start=start, end=end, interval="1d", auto_adjust=True, progress=False)
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"yfinance returned no volume for {tickers}")
    vol = raw["Volume"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Volume"]]
    if isinstance(vol, pd.Series):
        vol = vol.to_frame(tickers[0])
    elif list(vol.columns) == ["Volume"]:
        vol = vol.rename(columns={"Volume": tickers[0]})
    vol.index = pd.to_datetime(vol.index).tz_localize(None)

    VOLUME_CACHE.parent.mkdir(parents=True, exist_ok=True)
    vol.to_csv(VOLUME_CACHE)
    return vol


def entry_index(trading_days: pd.DatetimeIndex, telegraph_ts: str,
                t_update_days: int = None) -> int | None:
    """Position in `trading_days` of the entry close, modeling the update lag.

    First the ACTABLE close: posted before 16:00 ET on a trading day -> that day's close; otherwise
    (after close, or a non-trading day) -> the next trading day's close. Then enter `t_update_days`
    trading days later, at that day's close (default T_UPDATE_DAYS = 1). Returns None if it runs off
    the end of the data."""
    lag = T_UPDATE_DAYS if t_update_days is None else int(t_update_days)
    ts = pd.Timestamp(telegraph_ts)
    ts_et = ts.tz_convert(ET) if ts.tzinfo is not None else ts.tz_localize(ET)
    day = ts_et.normalize().tz_localize(None)
    same_day_actable = ts_et.hour < MARKET_CLOSE_HOUR

    base = None
    for i, d in enumerate(trading_days):
        if d < day:
            continue
        base = i if (d == day and same_day_actable) else (i if d > day else i + 1)
        break
    if base is None:
        return None
    idx = base + lag
    return idx if idx < len(trading_days) else None
