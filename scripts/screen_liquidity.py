#!/usr/bin/env python3
"""Liquidity screen for candidate tickers, run against cached bars BEFORE backtesting.

This exists because the backtest cannot make this judgement. scripts/backtest.py
synthesizes bid/ask as 30% of each ticker's configured max_spread_pct (see its
module docstring), so the spread gate in risk_manager.py can never reject anything
in a backtest, and a bar that never printed in real life simply isn't in the file
rather than showing up as an unfillable moment. A name too thin to trade therefore
backtests as if it were liquid. The screen has to come from the bar data directly.

Two measurements, both medians over session days:

  bars_per_day   -- of the 78 five-minute bars in a regular session, how many
                    actually printed. A name that prints 59 traded in 19 of the
                    intervals not at all; the strategy's VWAP, EMA and RVOL inputs
                    are all computed off a bar series that assumes continuity.
  dollar_volume  -- median close x volume summed over the session. Position sizing
                    is risk-based and can ask for a large share count in a cheap
                    stock, so notional liquidity is what matters, not share count.

Thresholds are anchored to the shipped universe rather than picked: the weakest
ENABLED name is VZLA (78 bars, $5.7M) and NEXA was EXCLUDED at 37 bars, $0.3M.

Usage:
    python3 scripts/screen_liquidity.py GFI HMY TRX RMCO
    python3 scripts/screen_liquidity.py --all        # every cached symbol
"""

import argparse
import csv
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from data.historical_data import DEFAULT_CACHE_DIR  # noqa: E402

BARS_PER_SESSION = 78
MIN_BARS_PER_DAY = 74          # 95% of a regular session
MIN_DOLLAR_VOLUME = 5_000_000  # just under VZLA, the weakest name already enabled


def passes(bars_per_day, dollar_volume):
    """The screen itself. Returns (ok, [reasons it failed])."""
    reasons = []
    if bars_per_day < MIN_BARS_PER_DAY:
        reasons.append(
            f"bar coverage {bars_per_day / BARS_PER_SESSION:.0%} < "
            f"{MIN_BARS_PER_DAY / BARS_PER_SESSION:.0%}"
        )
    if dollar_volume < MIN_DOLLAR_VOLUME:
        reasons.append(f"${dollar_volume / 1e6:.1f}M median daily $vol < ${MIN_DOLLAR_VOLUME / 1e6:.0f}M")
    return not reasons, reasons


def profile(symbol, cache_dir=DEFAULT_CACHE_DIR):
    """Median per-session statistics for one cached symbol, or None if uncached."""
    path = os.path.join(cache_dir, symbol + ".csv")
    if not os.path.exists(path):
        return None
    days = defaultdict(list)
    with open(path) as f:
        for row in csv.DictReader(f):
            days[row["timestamp"][:10]].append(row)
    if not days:
        return None
    ranges, volumes, closes = [], [], []
    for rows in days.values():
        high = max(float(r["high"]) for r in rows)
        low = min(float(r["low"]) for r in rows)
        close = float(rows[-1]["close"])
        ranges.append((high - low) / close * 100)
        closes.append(close)
        volumes.append(sum(float(r["close"]) * float(r["volume"]) for r in rows))
    ordered = sorted(days)
    return {
        "symbol": symbol,
        "first": ordered[0],
        "last": ordered[-1],
        "sessions": len(days),
        "bars_per_day": statistics.median(len(v) for v in days.values()),
        "daily_range_pct": statistics.median(ranges),
        "dollar_volume": statistics.median(volumes),
        "close": statistics.median(closes),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--all", action="store_true", help="screen every symbol in the cache")
    args = ap.parse_args()

    symbols = args.symbols
    if args.all:
        symbols = sorted(f[:-4] for f in os.listdir(DEFAULT_CACHE_DIR) if f.endswith(".csv"))
    if not symbols:
        ap.error("pass symbols or --all")

    print(f"{'sym':7s}{'from':12s}{'sess':>5s}{'bars':>6s}{'cover':>7s}{'$vol':>9s}{'close':>9s}{'range%':>8s}  verdict")
    failures = 0
    for symbol in symbols:
        p = profile(symbol)
        if p is None:
            print(f"{symbol:7s}{'NOT CACHED':12s}")
            failures += 1
            continue
        ok, reasons = passes(p["bars_per_day"], p["dollar_volume"])
        verdict = "PASS" if ok else "EXCLUDE: " + "; ".join(reasons)
        failures += 0 if ok else 1
        print(f"{symbol:7s}{p['first']:12s}{p['sessions']:5d}{p['bars_per_day']:6.0f}"
              f"{p['bars_per_day'] / BARS_PER_SESSION:7.0%}{p['dollar_volume'] / 1e6:8.1f}M"
              f"{p['close']:9.2f}{p['daily_range_pct']:8.2f}  {verdict}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
