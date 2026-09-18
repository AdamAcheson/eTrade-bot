#!/usr/bin/env python3
"""Caches supplementary instruments that are NOT part of the tradeable universe --
currently SI=F, COMEX silver futures.

Why a separate directory from data_cache/historical/: that directory defines the
backtest's trading-day universe (scripts/benchmark_compare.py derives its day list
by scanning every CSV in it). SI=F trades nearly 24 hours including Sunday
evenings, so dropping it in there would inject non-equity-session dates into that
day list and silently shift every backtest window.

Coverage limit, which is the reason SI=F is not used by the backtest: Yahoo serves
only ~60 days of 5-minute history for it, against the 761 days cached for the
universe. Twelve Data cannot fill the gap either -- XAG/USD requires a paid plan
and the bare symbol SI resolves to an unrelated NYSE listing.

What it IS good for: silver futures move overnight and predict the next morning's
gap in the silver complex with ~0.97 correlation, so this is the pre-market read a
live session can consult before the open. The backtest does not need it, because
by the time the bot evaluates anything the gap is already visible in the stock's
own open versus its prior close.

Usage:
    python3 scripts/fetch_reference_data.py [--symbols SI=F,...]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from data.historical_data import save_bars_csv, fetch_yahoo_bars, HistoricalDataError  # noqa: E402

REFERENCE_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_cache", "reference"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="SI=F")
    parser.add_argument("--range", dest="range_", default="60d")
    args = parser.parse_args()

    os.makedirs(REFERENCE_CACHE_DIR, exist_ok=True)
    for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        try:
            # market_open/market_close span the whole day: unlike an equity, the
            # point of this instrument is the session the stock market is closed for.
            from datetime import time as dtime
            bars = fetch_yahoo_bars(symbol, range_=args.range_, interval="5m",
                                    market_open=dtime(0, 0), market_close=dtime(23, 59))
        except HistoricalDataError as e:
            print(f"{symbol}: {e}", file=sys.stderr)
            return 1
        path = os.path.join(REFERENCE_CACHE_DIR, f"{symbol}.csv")
        save_bars_csv(bars, path)
        days = {b.timestamp.date() for b in bars}
        print(f"{symbol}: {len(bars):,} bars over {len(days)} days "
              f"({min(days)} -> {max(days)}) -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
