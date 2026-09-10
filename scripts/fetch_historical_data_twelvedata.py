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
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from config_loader import load_config, ConfigError  # noqa: E402
from data.historical_data import cache_path, load_bars_csv, save_bars_csv, DEFAULT_CACHE_DIR  # noqa: E402
from data.twelvedata_historical_data import TwelveDataError, fetch_twelvedata_bars  # noqa: E402


_ET = ZoneInfo("America/New_York")


def merge_bars(existing, new):
    """Combine two bar lists, de-duplicating on timestamp (new wins) and returning
    them in chronological order. Lets a deep backfill run across several days
    without re-fetching -- or losing -- what previous runs already cached."""
    by_ts = {b.timestamp: b for b in existing}
    by_ts.update({b.timestamp: b for b in new})
    return sorted(by_ts.values(), key=lambda b: b.timestamp)


def missing_ranges(existing, start, end):
    """Which [from, to] windows still need fetching to cover start..end, given
    what's already cached. Cached bars are tz-aware America/New_York (see
    twelvedata_historical_data.py), so start/end must be too -- comparing naive
    against aware datetimes raises TypeError (this repo has been bitten by that
    before, see the bar-close fix in commit f307434)."""
    if not existing:
        return [(start, end)]
    have_start, have_end = existing[0].timestamp, existing[-1].timestamp
    gaps = []
    if start < have_start:
        gaps.append((start, have_start))      # extend backwards (the deep-history case)
    if end > have_end:
        gaps.append((have_end, end))          # top up recent bars
    return gaps


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Bypass the cache and re-fetch everything")
    parser.add_argument("--days", type=int, default=180, help="Calendar days of history to pull (default: 180 -- comfortably fits one day's free-tier credit budget for the full universe)")
    parser.add_argument(
        "--symbols", type=str, default=None,
        help="Comma-separated symbols to fetch instead of the whole universe+benchmarks. "
             "A deep backfill costs far more than one day of the free tier's credits, so "
             "pulling only what a given experiment needs is usually the difference between "
             "finishing today and not. Symbols outside the configured universe are allowed "
             "(benchmarks are not tradeable tickers).",
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    if args.symbols:
        # Preserve the order given, de-duplicating. Sorting them would be actively
        # harmful: a deep backfill can exhaust the day's credits partway, so the
        # caller's ordering is a priority list -- fetch the symbols that unlock
        # tradeable tickers first, and whatever is reached before the budget runs
        # out is still a usable universe.
        seen = set()
        symbols = []
        for raw in args.symbols.split(","):
            sym = raw.strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                symbols.append(sym)
    else:
        universe = config.auto_tradeable_universe()
        benchmarks = sorted({config.benchmark_of(t) for t in universe})
        symbols = sorted(set(universe) | set(benchmarks))

    end = datetime.now(tz=_ET)
    start = end - timedelta(days=args.days)

    print(f"Fetching {len(symbols)} symbols ({args.days}d, 5m bars) from Twelve Data into {DEFAULT_CACHE_DIR} ...")
    ok, failed = [], []
    for i, symbol in enumerate(symbols):
        path = cache_path(symbol)
        existing = []
        if os.path.exists(path) and not args.refresh:
            existing = load_bars_csv(path)
        gaps = missing_ranges(existing, start, end)
        if not gaps:
            print(f"  {symbol}: already covers {args.days}d, skipping")
            ok.append(symbol)
            continue
        if i > 0:
            # Same pacing as between chunks within one symbol (see
            # fetch_twelvedata_bars) -- without this, back-to-back symbols would
            # burst past the free tier's 8-credits/minute cap.
            time.sleep(8.0)
        try:
            fetched = []
            for gap_start, gap_end in gaps:
                fetched.extend(fetch_twelvedata_bars(symbol, gap_start, gap_end))
            bars = merge_bars(existing, fetched)
            if not bars:
                print(f"  {symbol}: FAILED -- no bars returned")
                failed.append(symbol)
                continue
            # Write after every symbol, so a run that stops partway (credits
            # exhausted, network) keeps everything it already fetched.
            save_bars_csv(bars, path)
            added = len(bars) - len(existing)
            print(f"  {symbol}: {len(bars)} bars (+{added} new) "
                  f"{bars[0].timestamp.date()} to {bars[-1].timestamp.date()}")
            ok.append(symbol)
        except TwelveDataError as e:
            message = str(e).lower()
            if "credit" in message or "limit" in message:
                print(f"  {symbol}: STOPPING -- free-tier credits exhausted for today ({e})")
                print(f"\n{len(ok)} symbols cached so far, {len(symbols) - len(ok)} remaining.")
                print("Re-run this script (without --refresh) tomorrow -- it now merges into the "
                  "existing cache and only fetches the still-missing date ranges.")
                return 1
            print(f"  {symbol}: FAILED -- {e}")
            failed.append(symbol)
        except Exception as e:  # noqa: BLE001 -- see below
            # A multi-hour backfill must not die on one symbol. Anything the data
            # layer didn't already classify (a parse surprise, an unexpected
            # payload shape) is logged and skipped; whatever was cached before it
            # is already on disk, and re-running fills the gap.
            print(f"  {symbol}: FAILED -- unexpected {type(e).__name__}: {e}")
            failed.append(symbol)

    print(f"\nDone. {len(ok)} succeeded, {len(failed)} failed.")
    if failed:
        print(f"Failed symbols: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
