"""Historical 5-minute bar loader using Twelve Data's REST API
(https://twelvedata.com/docs#time-series). Unlike data/historical_data.py (Yahoo
Finance, hard-capped at ~60 days of intraday history), Twelve Data's free tier
serves multiple years of 5-minute bars -- confirmed empirically against the live
API (AG 5-minute bars pulled cleanly as far back as 2022), not just assumed from
their docs, which don't state a retention limit.

This writes into the SAME cache files data/historical_data.py reads
(data_cache/historical/{symbol}.csv), so scripts/backtest.py needs no changes --
running scripts/fetch_historical_data_twelvedata.py simply replaces a symbol's
Yahoo-sourced (60-day) cache with a deeper Twelve Data one.

Free tier constraints (twelvedata.com/pricing, confirmed via response headers):
8 credits/minute, 800 credits/day. A request's credit cost scales with how much
data it returns, so this chunks a long date range into multiple requests and
paces them rather than firing one huge request or a burst of small ones.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests

from models.bar import Bar

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"
API_KEY_ENV = "TWELVEDATA_API_KEY"
_ET = ZoneInfo("America/New_York")


class TwelveDataError(Exception):
    pass


def _api_key(api_key: Optional[str] = None) -> str:
    key = api_key or os.environ.get(API_KEY_ENV)
    if not key:
        raise TwelveDataError(f"Set {API_KEY_ENV} (see .env.example) to use Twelve Data.")
    return key


def _get_with_retry(params: dict, timeout: float, attempts: int = 4):
    """A deep backfill makes hundreds of calls over hours, so a single transient
    network blip must not end the run -- one killed a 3-year pull on its fourth
    symbol. Retries connection-level failures with exponential backoff; HTTP
    responses (including rate-limit ones) are returned to the caller to interpret,
    since those are answers, not failures to reach the server."""
    delay = 2.0
    last_status = None
    for attempt in range(attempts):
        try:
            resp = requests.get(TWELVEDATA_URL, params=params, timeout=timeout)
        except requests.exceptions.RequestException as e:
            if attempt == attempts - 1:
                raise TwelveDataError(
                    f"network error after {attempts} attempts for {params.get('symbol')}: {e}"
                ) from e
            time.sleep(delay)
            delay *= 2
            continue

        # A 5xx is the server failing to answer, not an answer -- retry it exactly
        # like a dropped connection. FCX died on a Cloudflare 521 ("origin down")
        # mid-backfill and was skipped, because every HTTP response used to be
        # handed straight back to the caller. 4xx is deliberately NOT retried: those
        # are real answers (bad symbol, bad key), and 429 in particular is how the
        # free tier reports exhausted credits, which the caller stops cleanly on
        # rather than hammering.
        if resp.status_code < 500:
            return resp
        last_status = resp.status_code
        if attempt == attempts - 1:
            break
        time.sleep(delay)
        delay *= 2

    raise TwelveDataError(
        f"HTTP {last_status} after {attempts} attempts for {params.get('symbol')}"
    )


def _fetch_window(symbol: str, window_start: datetime, window_end: datetime, api_key: str, interval: str, timeout: float) -> List[Bar]:
    resp = _get_with_retry(
        {
            "symbol": symbol,
            "interval": interval,
            "start_date": window_start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": window_end.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": "America/New_York",
            "outputsize": 5000,
            "apikey": api_key,
        },
        timeout=timeout,
    )
    # "No data is available" means this window predates the symbol's listing, which
    # is an answer, not a failure -- and the API returns it as HTTP 400 with the
    # explanation in the body, not only as a 200 with an error payload. Checking the
    # status code first cost CRML its whole backfill: it listed during 2024, so the
    # first 2023 chunk 400'd and aborted the symbol, discarding the two years of data
    # that do exist. Parse the body before deciding.
    data = None
    try:
        data = resp.json()
    except ValueError:
        data = None

    message = data.get("message", "") if isinstance(data, dict) else ""
    if "no data is available" in message.lower():
        return []

    if resp.status_code != 200:
        raise TwelveDataError(f"Twelve Data returned HTTP {resp.status_code} for {symbol}: {resp.text[:200]}")

    if isinstance(data, dict) and data.get("status") == "error":
        raise TwelveDataError(f"Twelve Data error for {symbol} {window_start.date()}..{window_end.date()}: {message}")

    bars: List[Bar] = []
    for row in data.get("values", []):
        ts = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_ET)
        bars.append(Bar(
            timestamp=ts,
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row["volume"]),
        ))
    return bars


def fetch_twelvedata_bars(
    symbol: str,
    start: datetime,
    end: datetime,
    api_key: Optional[str] = None,
    interval: str = "5min",
    chunk_days: int = 90,
    pause_seconds: float = 8.0,
    timeout: float = 20.0,
) -> List[Bar]:
    """Fetches 5-minute bars for `symbol` between start/end (naive, treated as
    America/New_York) by chunking into `chunk_days`-sized windows to stay well
    under the 5000-bars-per-request cap, pausing `pause_seconds` between requests
    to respect the free tier's 8-credits/minute throttle."""
    key = _api_key(api_key)
    bars: List[Bar] = []
    window_start = start
    first_request = True
    while window_start < end:
        window_end = min(window_start + timedelta(days=chunk_days), end)
        if not first_request:
            time.sleep(pause_seconds)
        first_request = False

        bars.extend(_fetch_window(symbol, window_start, window_end, key, interval, timeout))
        window_start = window_end

    bars.sort(key=lambda b: b.timestamp)
    return bars
