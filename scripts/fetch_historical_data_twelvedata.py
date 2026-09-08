#!/usr/bin/env python3
"""Fetches and caches historical 5-minute bars via Twelve Data (see
src/data/twelvedata_historical_data.py) for the approved universe + benchmarks,
replacing Yahoo's 60-day cache (data/historical_data.py) with deeper history for
use by scripts/backtest.py.

Free tier is capped at 800 credits/day, 8 credits/minute -- pulling a full year
for the whole universe costs more than one day's budget, so this stops cleanly
(not a crash) the moment the API reports credits are exhausted, having already
cached everything it got to. Safe to re-run the next day: already-cached symbols
are skipped unless --refresh is passed, so a second run picks up where the first
stopped.

Requires TWELVEDATA_API_KEY (see .env.example).

Usage:
    python3 scripts/fetch_historical_data_twelvedata.py [--refresh] [--days 180]
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from config_loader import load_config, ConfigError  # noqa: E402
from data.historical_data import cache_path, save_bars_csv, DEFAULT_CACHE_DIR  # noqa: E402
from data.twelvedata_historical_data import TwelveDataError, fetch_twelvedata_bars  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Bypass the cache and re-fetch everything")
    parser.add_argument("--days", type=int, default=180, help="Calendar days of history to pull (default: 180 -- comfortably fits one day's free-tier credit budget for the full universe)")
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    universe = config.auto_tradeable_universe()
    benchmarks = sorted({config.benchmark_of(t) for t in universe})
    symbols = sorted(set(universe) | set(benchmarks))

    end = datetime.now()
    start = end - timedelta(days=args.days)

    print(f"Fetching {len(symbols)} symbols ({args.days}d, 5m bars) from Twelve Data into {DEFAULT_CACHE_DIR} ...")
    ok, failed = [], []
    for i, symbol in enumerate(symbols):
        path = cache_path(symbol)
        if not args.refresh and os.path.exists(path):
            print(f"  {symbol}: cached, skipping")
            ok.append(symbol)
            continue
        if i > 0:
            # Same pacing as between chunks within one symbol (see
            # fetch_twelvedata_bars) -- without this, back-to-back symbols would
            # burst past the free tier's 8-credits/minute cap.
            time.sleep(8.0)
        try:
            bars = fetch_twelvedata_bars(symbol, start, end)
            if not bars:
                print(f"  {symbol}: FAILED -- no bars returned")
                failed.append(symbol)
                continue
            save_bars_csv(bars, path)
            print(f"  {symbol}: {len(bars)} bars ({bars[0].timestamp.date()} to {bars[-1].timestamp.date()})")
            ok.append(symbol)
        except TwelveDataError as e:
            message = str(e).lower()
            if "credit" in message or "limit" in message:
                print(f"  {symbol}: STOPPING -- free-tier credits exhausted for today ({e})")
                print(f"\n{len(ok)} symbols cached so far, {len(symbols) - len(ok)} remaining.")
                print("Re-run this script (without --refresh) tomorrow to pick up where it left off.")
                return 1
            print(f"  {symbol}: FAILED -- {e}")
            failed.append(symbol)

    print(f"\nDone. {len(ok)} succeeded, {len(failed)} failed.")
    if failed:
        print(f"Failed symbols: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
