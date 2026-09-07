"""Historical market data fetcher for the backtest driver only.

Pulls free, delayed OHLCV bars from Yahoo Finance's public chart endpoint (no API
key, no account needed). This is NOT a data vendor the live/paper/sandbox bot
depends on -- it exists solely to feed InMemoryMarketDataProvider historical bars
for scripts/backtest.py (see backtest/engine.py), completely separate from
data/etrade_market_data.py.

Known limitations -- read before trusting backtest results:
  * Yahoo's 5-minute intraday bars only go back ~60 calendar days; there is no
    free source of longer intraday history.
  * No historical bid/ask is available from this endpoint -- backtest/engine.py
    synthesizes a small spread around each bar's close. Real fills would differ,
    especially on wider-spread names.
  * It's free/delayed retail data: expect occasional gaps or bad ticks relative
    to what a paid feed would show.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from datetime import time as dtime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from models.bar import Bar

NY_TZ = ZoneInfo("America/New_York")
CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "cache"
)

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; eTrade-bot-backtest/1.0)"}
_MARKET_OPEN = dtime(9, 30)
_MARKET_CLOSE = dtime(16, 0)


class DataFetchError(Exception):
    pass


def _cache_path(symbol: str, interval: str, range_: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe = symbol.replace("/", "_")
    return os.path.join(CACHE_DIR, f"{safe}_{interval}_{range_}.json")


def _fetch_chart(symbol: str, interval: str, range_: str, use_cache: bool = True, max_retries: int = 4) -> dict:
    path = _cache_path(symbol, interval, range_)
    if use_cache and os.path.exists(path):
        with open(path) as f:
            return json.load(f)

    params = {"range": range_, "interval": interval, "includePrePost": "false"}
    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(_CHART_URL.format(symbol=symbol), params=params, headers=_HEADERS, timeout=20)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            payload = resp.json()
            result = payload.get("chart", {}).get("result")
            if not result:
                error = payload.get("chart", {}).get("error")
                raise DataFetchError(f"{symbol}: no chart data returned ({error})")
            with open(path, "w") as f:
                json.dump(payload, f)
            time.sleep(0.3)  # be polite to the free endpoint
            return payload
        except (requests.RequestException, DataFetchError) as e:
            last_error = e
            time.sleep(2 ** attempt)
    raise DataFetchError(f"failed to fetch {symbol!r} ({interval}, {range_}) after {max_retries} attempts: {last_error}")


def _bars_from_chart(payload: dict, symbol: str) -> List[Bar]:
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]

    bars: List[Bar] = []
    for i, ts in enumerate(timestamps):
        o, h, l, c, v = (quote[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c, v):
            continue
        bars.append(
            Bar(
                timestamp=datetime.fromtimestamp(ts, tz=NY_TZ),
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v),
            )
        )
    bars.sort(key=lambda b: b.timestamp)
    return bars


def fetch_intraday_bars(symbol: str, range_: str = "60d", interval: str = "5m", use_cache: bool = True) -> List[Bar]:
    """Regular-session OHLCV bars only (09:30-16:00 America/New_York), oldest
    first. Bars with a null OHLC field (Yahoo returns these for minutes with no
    trades on thin names) are dropped."""
    payload = _fetch_chart(symbol, interval, range_, use_cache=use_cache)
    bars = _bars_from_chart(payload, symbol)
    return [b for b in bars if _MARKET_OPEN <= b.timestamp.time() < _MARKET_CLOSE]


def fetch_daily_bars(symbol: str, range_: str = "1y", use_cache: bool = True) -> List[Bar]:
    payload = _fetch_chart(symbol, "1d", range_, use_cache=use_cache)
    return _bars_from_chart(payload, symbol)


def daily_volume_baseline(daily_bars: List[Bar], lookback: int = 20) -> Dict[date, float]:
    """Trailing `lookback`-day average volume as of the START of each date (a day
    never sees its own volume in its own baseline), keyed by calendar date."""
    baseline: Dict[date, float] = {}
    volumes = [b.volume for b in daily_bars]
    dates = [b.timestamp.date() for b in daily_bars]
    for i in range(len(daily_bars)):
        window = volumes[max(0, i - lookback):i]
        if window:
            baseline[dates[i]] = sum(window) / len(window)
    return baseline
