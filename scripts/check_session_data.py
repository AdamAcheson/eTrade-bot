#!/usr/bin/env python3
"""Sanity-checks the most recent cached session before backtesting it.

Twelve Data serves the CURRENT session's volume from a real-time feed carrying
only a fraction of the consolidated tape. On 2026-09-11 AG's full 78-bar session
totalled 522,955 shares against an 8,590,724 ten-session median -- 6% -- while
prices (O 20.43 H 20.73 L 19.43 C 19.57 against a 20.16 prior close) described a
perfectly ordinary -2.9% day.

This does NOT resolve by waiting out the session. AG's opening bar read 13,784
shares at 12:00 ET and the identical 13,784 at 17:00, an hour after the close.
It resolves the NEXT day: 2026-09-10 fetched on 2026-09-11 read a normal
8,552,365. So a session becomes backtestable the morning after, not the evening of.

Relative volume is a hard entry gate (min_relative_volume), so a session fetched
too early produces zero entries that look like a quiet market rather than a data
problem -- which is exactly how it first presented.

Run this before scripts/backtest.py --days 1.

Usage:
    python3 scripts/check_session_data.py [--day YYYY-MM-DD] [--min-volume-ratio 0.5]
"""

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from config_loader import ConfigError, load_config  # noqa: E402
from data.historical_data import HistoricalDataError, get_bars, group_bars_by_day  # noqa: E402

FULL_SESSION_BARS = 78


def check_symbol(symbol, day, lookback, min_ratio):
    """Returns (status, detail). status is 'ok', 'warn' or 'missing'."""
    try:
        days = group_bars_by_day(get_bars(symbol))
    except HistoricalDataError as e:
        return "missing", str(e)
    if day not in days:
        return "missing", f"no bars cached for {day}"

    bars = days[day]
    prior = [days[d] for d in sorted(days) if d < day][-lookback:]
    if not prior:
        return "warn", "no prior sessions to compare against"

    volume = sum(b.volume for b in bars)
    typical = statistics.median(sum(b.volume for b in p) for p in prior)
    ratio = volume / typical if typical else 0.0
    detail = f"{len(bars):2d}/{FULL_SESSION_BARS} bars, volume {ratio:5.2f}x the {lookback}-session median"

    if len(bars) < FULL_SESSION_BARS * 0.9:
        return "warn", detail + "  <- session incomplete"
    if ratio < min_ratio:
        return "warn", detail + "  <- volume too thin, RVOL will be wrong"
    return "ok", detail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=None, help="Session to check (default: the most recent cached)")
    parser.add_argument("--lookback", type=int, default=10, help="Sessions to compare against (default: 10)")
    parser.add_argument("--min-volume-ratio", type=float, default=0.5,
                        help="Flag a session whose volume is below this fraction of typical (default: 0.5)")
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    universe = config.auto_tradeable_universe()
    symbols = sorted(set(universe) | {config.benchmark_of(t) for t in universe})

    day = args.day
    if day is None:
        newest = []
        for s in symbols:
            try:
                newest.append(max(group_bars_by_day(get_bars(s))))
            except HistoricalDataError:
                continue
        if not newest:
            print("No cached data at all.", file=sys.stderr)
            return 1
        day = max(newest)

    print(f"Checking {day} across {len(symbols)} symbols "
          f"(against the previous {args.lookback} sessions)\n")

    counts = {"ok": 0, "warn": 0, "missing": 0}
    for symbol in symbols:
        status, detail = check_symbol(symbol, day, args.lookback, args.min_volume_ratio)
        counts[status] += 1
        mark = {"ok": "  ok  ", "warn": " WARN ", "missing": " MISS "}[status]
        print(f"{mark} {symbol:6} {detail}")

    print(f"\n{counts['ok']} ok, {counts['warn']} suspect, {counts['missing']} missing")
    if counts["warn"] or counts["missing"]:
        print("\nDo NOT trust a backtest of this session yet. If this is today's session,")
        print("waiting out the close does not help -- the current day is served from a")
        print("partial real-time feed. Re-fetch tomorrow and check again; a completed")
        print("prior session reads full consolidated volume.")
        return 1
    print("\nSession looks complete. Safe to run: python3 scripts/backtest.py --days 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
