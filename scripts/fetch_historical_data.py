#!/usr/bin/env python3
"""Fetches and caches historical 5-minute bars (via Yahoo Finance's public chart
API, see src/data/historical_data.py) for the approved universe + benchmarks, for
use by scripts/backtest.py. Safe to re-run -- skips symbols already cached unless
--refresh is passed.

Usage:
    python3 scripts/fetch_historical_data.py [--refresh] [--range 60d]
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from config_loader import load_config, ConfigError  # noqa: E402
from data.historical_data import DEFAULT_CACHE_DIR, HistoricalDataError, cache_path, get_bars  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Bypass the cache and re-fetch everything")
    parser.add_argument("--range", default="60d", help="Yahoo range parameter (default: 60d, the practical max for 5m bars)")
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    universe = config.auto_tradeable_universe()
    benchmarks = sorted({config.benchmark_of(t) for t in universe})
    symbols = sorted(set(universe) | set(benchmarks))

    print(f"Fetching {len(symbols)} symbols ({args.range}, 5m bars) into {DEFAULT_CACHE_DIR} ...")
    ok, failed = [], []
    for symbol in symbols:
        path = cache_path(symbol)
        if not args.refresh and os.path.exists(path):
            print(f"  {symbol}: cached, skipping")
            ok.append(symbol)
            continue
        try:
            bars = get_bars(symbol, range_=args.range, force_refresh=args.refresh)
            print(f"  {symbol}: {len(bars)} bars")
            ok.append(symbol)
        except HistoricalDataError as e:
            print(f"  {symbol}: FAILED -- {e}")
            failed.append(symbol)
        time.sleep(0.5)  # be polite to Yahoo's free endpoint

    print(f"\nDone. {len(ok)} succeeded, {len(failed)} failed.")
    if failed:
        print(f"Failed symbols: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
