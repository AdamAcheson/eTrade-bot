"""Historical 5-minute bar loader for backtesting, using Yahoo Finance's public
chart API (the same endpoint the `yfinance` package calls internally). We call it
directly with `requests` rather than through `yfinance` because `yfinance`'s
curl_cffi-based TLS fingerprint impersonation is incompatible with TLS-terminating
proxies in some environments -- a plain `requests` call works fine and needs no
extra dependency.

This is for backtesting only -- it has nothing to do with the live trading path
(broker/etrade.py, data/etrade_market_data.py), which never uses this module.
Yahoo's intraday data has real limitations worth knowing:
  * 5-minute bars are only available for roughly the last 60 days.
  * No bid/ask is provided, only OHLCV -- callers must synthesize a spread.
  * Occasional bars have null OHLC (no trades) and are dropped here.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests

from models.bar import Bar

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
_ET = ZoneInfo("America/New_York")

DEFAULT_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data_cache", "historical"
)


class HistoricalDataError(Exception):
    pass


def fetch_yahoo_bars(
    symbol: str,
    range_: str = "60d",
    interval: str = "5m",
    market_open: dtime = dtime(9, 30),
    market_close: dtime = dtime(16, 0),
    timeout: float = 15.0,
) -> List[Bar]:
    """Fetches intraday bars for `symbol` and filters to regular trading hours
    (America/New_York), dropping any bar with a null OHLC value."""
    resp = requests.get(
        YAHOO_CHART_URL.format(symbol=symbol),
        params={"interval": interval, "range": range_},
        headers=_HEADERS,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise HistoricalDataError(f"Yahoo chart API returned HTTP {resp.status_code} for {symbol}: {resp.text[:200]}")

    data = resp.json()
    chart = data.get("chart", {})
    if chart.get("error"):
        raise HistoricalDataError(f"Yahoo chart API error for {symbol}: {chart['error']}")
    results = chart.get("result")
    if not results:
        raise HistoricalDataError(f"Yahoo chart API returned no result for {symbol}")

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]

    bars: List[Bar] = []
    for i, ts in enumerate(timestamps):
        o, h, l, c, v = quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i], quote["volume"][i]
        if None in (o, h, l, c):
            continue
        ts_et = datetime.fromtimestamp(ts, tz=_ET)
        if not (market_open <= ts_et.time() < market_close):
            continue
        bars.append(Bar(timestamp=ts_et, open=float(o), high=float(h), low=float(l), close=float(c), volume=float(v or 0.0)))

    bars.sort(key=lambda b: b.timestamp)
    return bars


def cache_path(symbol: str, cache_dir: str = DEFAULT_CACHE_DIR) -> str:
    return os.path.join(cache_dir, f"{symbol}.csv")


def save_bars_csv(bars: List[Bar], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for b in bars:
            writer.writerow([b.timestamp.isoformat(), b.open, b.high, b.low, b.close, b.volume])


def load_bars_csv(path: str) -> List[Bar]:
    bars: List[Bar] = []
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars.append(Bar(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            ))
    return bars


def get_bars(
    symbol: str,
    cache_dir: str = DEFAULT_CACHE_DIR,
    range_: str = "60d",
    interval: str = "5m",
    force_refresh: bool = False,
) -> List[Bar]:
    """Cached accessor: loads from the local CSV cache if present, otherwise fetches
    from Yahoo and writes the cache. Use force_refresh=True to bypass the cache."""
    path = cache_path(symbol, cache_dir)
    if not force_refresh and os.path.exists(path):
        return load_bars_csv(path)
    bars = fetch_yahoo_bars(symbol, range_=range_, interval=interval)
    save_bars_csv(bars, path)
    return bars


def group_bars_by_day(bars: List[Bar]) -> "dict[str, List[Bar]]":
    """Groups bars by their America/New_York calendar date (as an ISO date string),
    preserving chronological order within each day."""
    by_day: "dict[str, List[Bar]]" = {}
    for b in bars:
        day = b.timestamp.astimezone(_ET).date().isoformat()
        by_day.setdefault(day, []).append(b)
    return by_day
