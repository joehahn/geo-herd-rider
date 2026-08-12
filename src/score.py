"""score.py — mechanical price/timing utilities for the firehose.

The shared price layer: `fetch_panel` (look-ahead-safe adjusted-close panel via yfinance, cached)
and `entry_index` (resolve the actable entry close with the execution lag), plus the `BENCHMARK`.
The per-event scorer + report + CLI that once lived here belonged to the retired decision-tree
pipeline (its `events_mapped.csv` input came from the deleted map_event.py) and have been removed.

Look-ahead hygiene: prices are pulled with explicit ``start=/end=`` bounds (never a relative
period), so nothing after a window's end can leak into it.
"""
from __future__ import annotations

import json
import re
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


VALIDATION_CACHE = REPO_ROOT / "data" / "prices_cache" / "ticker_validation.json"

# A US-listed symbol: 1-5 letters, optionally a class/exchange suffix (BRK.B, RDS-A). Deliberately
# strict -- anything else is far more likely to be prose than a ticker.
_TICKER_RE = re.compile(r"^[A-Z]{1,5}([.\-][A-Z]{1,2})?$")
# Exchange qualifiers the press (and the curator, copying it) prefixes onto tickers.
_EXCHANGE_PREFIX = re.compile(r"^(NYSE|NASDAQ|NYSEARCA|NYSEAMERICAN|AMEX|OTCMKTS|OTC|CBOE|BATS)\s*[:.]\s*",
                              re.IGNORECASE)


def normalize_ticker(raw: str) -> str:
    """Best-effort cleanup of one curator-emitted symbol: strip a `$` sigil, an exchange prefix
    (`NASDAQ:RGTI` -> `RGTI`), surrounding brackets/whitespace, and upper-case it. Returns "" for
    input that cannot be a symbol at all. Does NOT decide validity -- that's validate_tickers."""
    s = str(raw or "").strip().strip("()[]").strip()
    s = _EXCHANGE_PREFIX.sub("", s)
    s = s.lstrip("$").strip()
    return s.upper()


def _load_validation_cache() -> dict:
    if VALIDATION_CACHE.exists():
        try:
            return json.loads(VALIDATION_CACHE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def validate_tickers(tickers, as_of: str | None = None,
                     use_network: bool = True) -> tuple[list[str], dict[str, str]]:
    """Split curator-emitted symbols into (tradeable, {symbol: rejection reason}).

    WHY THIS EXISTS. Nothing between the curator LLM and yfinance checked that a "ticker" was a
    ticker. A smoke run had an event agent emit the vehicle `RIGETTI COMPUTING` -- a company NAME.
    yfinance returns no data for it, the position silently vanishes from the book, and the backtest
    under-counts positions the curator actually picked with NOTHING in the output saying so. A
    rejection has to be LOUD; a silent drop is the bug.

    Two gates:
      1. SHAPE (free, offline) -- normalize, then require a plausible US symbol. This is what catches
         company names, prose, and empty strings.
      2. LISTING (network, cached) -- the symbol must have traded on or before `as_of`. This is a
         LOOK-AHEAD guard, not just a typo check: without it a backtest can "buy" a company that had
         not listed yet on the decision date. Skipped when `as_of` is None or `use_network=False`.

    Results are cached in data/prices_cache/ticker_validation.json keyed by symbol, so a replay is
    offline and free. Reasons are human-readable because they get printed and logged, not swallowed."""
    accepted: list[str] = []
    rejected: dict[str, str] = {}
    cache = _load_validation_cache()
    dirty = False

    for raw in tickers:
        sym = normalize_ticker(raw)
        if not sym:
            rejected[str(raw)] = "empty after normalization"
            continue
        if " " in sym:
            rejected[str(raw)] = f"looks like a company name, not a ticker ({sym!r})"
            continue
        if not _TICKER_RE.match(sym):
            rejected[str(raw)] = f"not a US-symbol shape ({sym!r})"
            continue
        if not (use_network and as_of):
            accepted.append(sym)
            continue

        ent = cache.get(sym)
        if ent is None:
            try:                    # earliest close we can see; None/empty => nothing ever traded
                h = yf.Ticker(sym).history(period="max", interval="1d", auto_adjust=True)
                first = None if h is None or h.empty else str(pd.to_datetime(h.index[0]).date())
            except Exception as e:  # noqa: BLE001 -- a lookup failure must not sink a whole week
                rejected[raw if isinstance(raw, str) else sym] = f"listing lookup failed: {type(e).__name__}"
                continue
            ent = {"first_date": first}
            cache[sym] = ent
            dirty = True
        first = ent.get("first_date")
        if not first:
            rejected[str(raw)] = f"no price history on yfinance ({sym})"
        elif first > as_of[:10]:
            rejected[str(raw)] = f"not listed until {first}, after as-of {as_of[:10]} (look-ahead)"
        else:
            accepted.append(sym)

    if dirty:
        VALIDATION_CACHE.parent.mkdir(parents=True, exist_ok=True)
        VALIDATION_CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True))
    # de-dup while preserving order (a normalize can collapse "$RGTI" and "NASDAQ:RGTI" into one)
    return list(dict.fromkeys(accepted)), rejected


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


# (fetch_volume_panel removed 2026-07-14 — it powered the RVOL breakout gate, now ripped.)


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
