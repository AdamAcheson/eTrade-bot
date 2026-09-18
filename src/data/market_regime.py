"""Day-level market regime filter: trade only on days when a set of reference
instruments was rising.

LOOKAHEAD IS THE WHOLE DIFFICULTY HERE. "Only trade on days when the Dow and
silver were up" is, read literally, a rule that needs today's CLOSE -- which is not
known when the trade is placed. Backtesting it that way would produce a spectacular
and entirely fake result, because it amounts to trading only on days already known
to be good. Both modes below are therefore decidable BEFORE the first trade of the
day can be placed:

  prior_day -- every reference instrument closed up YESTERDAY versus the day
               before. Known the night before, and the more conservative reading:
               a persistence bet that yesterday's direction carries.
  open_gap  -- every reference instrument OPENED above its previous close today.
               Known at 09:30:00, before the bot's first entry window.

Daily bars are enough: the question is a daily one, and daily history is available
for years where 5-minute history is not (DIA has 183 days of intraday in the cache,
against the 761 the universe has).
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Dict, Iterable, Set

import requests

from data.historical_data import YAHOO_CHART_URL, _HEADERS

REFERENCE_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data_cache", "reference",
)
MODES = ("prior_day", "open_gap")


def fetch_daily(symbol: str, range_: str = "5y", cache_dir: str = REFERENCE_CACHE_DIR) -> Dict[str, dict]:
    """Daily open/close by ISO date, cached on disk so repeat runs don't re-hit the API."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"daily_{symbol.replace('/', '_')}.json")
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    resp = requests.get(
        YAHOO_CHART_URL.format(symbol=symbol),
        params={"interval": "1d", "range": range_},
        headers=_HEADERS, timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    series: Dict[str, dict] = {}
    for i, stamp in enumerate(result["timestamp"]):
        close, open_ = quote["close"][i], quote["open"][i]
        if close is None or open_ is None:
            continue
        day = dt.datetime.utcfromtimestamp(stamp).strftime("%Y-%m-%d")
        series[day] = {"open": open_, "close": close}
    with open(path, "w") as fh:
        json.dump(series, fh)
    return series


def qualifying_days(symbols: Iterable[str], mode: str,
                    cache_dir: str = REFERENCE_CACHE_DIR) -> Set[str]:
    """Dates on which EVERY symbol satisfies `mode`. A date missing from any
    symbol's series is excluded -- an unknown regime is not a tradeable one."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    per_symbol = []
    for symbol in symbols:
        series = fetch_daily(symbol, cache_dir=cache_dir)
        days = sorted(series)
        good = set()
        for i, day in enumerate(days):
            if i == 0:
                continue
            prev = series[days[i - 1]]
            if mode == "prior_day":
                # yesterday's close vs the close before it -- settled before today
                if i < 2:
                    continue
                prev2 = series[days[i - 2]]
                if prev["close"] > prev2["close"]:
                    good.add(day)
            else:  # open_gap: today's open vs yesterday's close, known at 09:30
                if series[day]["open"] > prev["close"]:
                    good.add(day)
        per_symbol.append(good)
    return set.intersection(*per_symbol) if per_symbol else set()
