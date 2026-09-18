"""Compares backtest results against a buy-and-hold benchmark over the SAME trading
days the backtest covered.

The comparison is deliberately unflattering to the bot in one respect and flattering
in another, and both are worth stating plainly:

  * Buy-and-hold uses ADJUSTED closes, so it gets credit for dividends. SPY yields
    over 1% a year and ignoring that would be a thumb on the scale.
  * The bot is FLAT OVERNIGHT and flat most of each session. It is exposed to the
    market for a small fraction of the time buy-and-hold is, so equal returns are
    not equal risk. Exposure is reported for that reason.

Usage:
    python3 scripts/benchmark_compare.py --tag ship05_hold --first-days 568
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import requests  # noqa: E402

from data.historical_data import DEFAULT_CACHE_DIR, YAHOO_CHART_URL, _HEADERS  # noqa: E402

TRADING_DAYS_PER_YEAR = 252


def trading_days() -> list:
    """The backtest's day universe: every day present in any cached symbol."""
    days = set()
    for name in os.listdir(DEFAULT_CACHE_DIR):
        if not name.endswith(".csv"):
            continue
        with open(os.path.join(DEFAULT_CACHE_DIR, name)) as fh:
            for row in csv.DictReader(fh):
                days.add(row["timestamp"][:10])
    return sorted(days)


def fetch_daily(symbol: str, cache: str) -> dict:
    """Daily adjusted closes. Cached on disk so repeat runs don't re-hit Yahoo."""
    if os.path.exists(cache):
        with open(cache) as fh:
            blob = json.load(fh)
        if symbol in blob:
            return blob[symbol]
    resp = requests.get(
        YAHOO_CHART_URL.format(symbol=symbol),
        params={"interval": "1d", "range": "5y"},
        headers=_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    adj = result.get("indicators", {}).get("adjclose", [{}])[0].get("adjclose")
    import datetime as dt

    series = {}
    for i, stamp in enumerate(result["timestamp"]):
        close = quote["close"][i]
        if close is None:
            continue
        day = dt.datetime.utcfromtimestamp(stamp).strftime("%Y-%m-%d")
        series[day] = (adj[i] if adj and adj[i] is not None else close)
    blob = {}
    if os.path.exists(cache):
        with open(cache) as fh:
            blob = json.load(fh)
    blob[symbol] = series
    with open(cache, "w") as fh:
        json.dump(blob, fh)
    return series


def drawdown(curve: list) -> float:
    peak, worst = curve[0], 0.0
    for value in curve:
        peak = max(peak, value)
        worst = max(worst, (peak - value) / peak)
    return worst * 100.0


def sharpe(returns: list) -> float:
    """Annualized, zero risk-free. Reported for shape, not as a precise figure --
    daily samples over a few hundred days carry a wide error bar."""
    if len(returns) < 2:
        return float("nan")
    sd = statistics.stdev(returns)
    if sd == 0:
        return float("nan")
    return statistics.mean(returns) / sd * math.sqrt(TRADING_DAYS_PER_YEAR)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, help="Backtest tag to read reports/backtest_trades<TAG>.jsonl")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--first-days", type=int, default=None)
    parser.add_argument("--equity", type=float, default=100_000.0)
    parser.add_argument("--benchmarks", default="SPY,QQQ")
    parser.add_argument(
        "--cost-bps", default="0,2,5,10",
        help="Comma-separated per-side transaction costs in basis points to model. The "
             "backtest itself assumes PERFECT FILLS -- gross_profit == net_profit on "
             "every trade, no commission and no slippage -- so 0 is the raw backtest "
             "number and anything above it is the honest version. Buy-and-hold pays "
             "this cost once, on the single opening trade.",
    )
    args = parser.parse_args()

    days = trading_days()
    if args.first_days:
        days = days[: args.first_days]
    elif args.days:
        days = days[-args.days :]
    start, end = days[0], days[-1]
    years = len(days) / TRADING_DAYS_PER_YEAR

    pnl = defaultdict(float)
    active = set()
    with open(f"reports/backtest_trades{args.tag}.jsonl") as fh:
        for line in fh:
            trade = json.loads(line)
            pnl[trade["date"]] += trade["net_profit"]
            active.add(trade["date"])

    # Per-day traded notional, so a cost assumption can be applied to the equity
    # curve rather than just subtracted from the total -- drawdown and Sharpe move
    # with costs too, and only the curve shows that.
    notional = defaultdict(float)
    trades = 0
    with open(f"reports/backtest_trades{args.tag}.jsonl") as fh:
        for line in fh:
            trade = json.loads(line)
            notional[trade["date"]] += trade["shares"] * (trade["entry_price"] + trade["exit_price"])
            trades += 1
    total_notional = sum(notional.values())

    def curve_for(bps: float):
        equity, curve, rets = args.equity, [], []
        for day in days:
            prev = equity
            equity += pnl.get(day, 0.0) - notional.get(day, 0.0) * bps / 10000.0
            curve.append(equity)
            rets.append((equity - prev) / prev)
        return equity, curve, rets

    equity, curve, rets = curve_for(0.0)
    net = equity - args.equity
    total_ret = net / args.equity * 100.0
    cagr = ((equity / args.equity) ** (1 / years) - 1) * 100.0

    print(f"\nPeriod: {start} -> {end}  ({len(days)} trading days, {years:.2f} years)")
    print(f"Starting equity: ${args.equity:,.0f}\n")
    print(f"{'':<22}{'return':>10}{'CAGR':>9}{'max DD':>9}{'ret/DD':>8}{'Sharpe':>8}{'exposure':>10}")
    bot_exposure = len(active) / len(days) * 100.0
    print(f"{'BOT ' + args.tag:<22}{total_ret:>9.1f}%{cagr:>8.1f}%{drawdown(curve):>8.2f}%"
          f"{total_ret / drawdown(curve):>8.1f}{sharpe(rets):>8.2f}{bot_exposure:>9.0f}%*")

    cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data_cache", "benchmarks_daily.json")
    for symbol in args.benchmarks.split(","):
        series = fetch_daily(symbol, os.path.abspath(cache))
        have = [d for d in days if d in series]
        if len(have) < 2:
            print(f"{symbol:<22}  no overlapping daily data")
            continue
        prices = [series[d] for d in have]
        shares = args.equity / prices[0]
        bcurve = [p * shares for p in prices]
        brets = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]
        bret = (bcurve[-1] - args.equity) / args.equity * 100.0
        bcagr = ((bcurve[-1] / args.equity) ** (1 / years) - 1) * 100.0
        bdd = drawdown(bcurve)
        print(f"{symbol + ' buy & hold':<22}{bret:>9.1f}%{bcagr:>8.1f}%{bdd:>8.2f}%"
              f"{bret / bdd:>8.1f}{sharpe(brets):>8.2f}{'100':>9}%")

    print(f"\nTransaction costs. The backtest fills at bar prices with no commission and no")
    print(f"slippage, so the row above is a zero-cost number. {trades:,} round trips move")
    print(f"${total_notional:,.0f} of notional, and the strategy breaks even at")
    print(f"{net / total_notional * 10000:.1f} bps per side.\n")
    print(f"{'':<22}{'return':>10}{'CAGR':>9}{'max DD':>9}{'ret/DD':>8}{'Sharpe':>8}")
    for raw in args.cost_bps.split(","):
        bps = float(raw)
        eq, cv, rt = curve_for(bps)
        ret = (eq - args.equity) / args.equity * 100.0
        ddv = drawdown(cv)
        cg = ((eq / args.equity) ** (1 / years) - 1) * 100.0 if eq > 0 else float("nan")
        label = f"  bot @ {raw} bps/side"
        print(f"{label:<22}{ret:>9.1f}%{cg:>8.1f}%{ddv:>8.2f}%{ret / ddv:>8.1f}{sharpe(rt):>8.2f}")

    print("\n* exposure = share of trading days on which the bot held any position at all.")
    print("  Within those days it is intraday-only and flat overnight, so true")
    print("  time-in-market is a small fraction of buy & hold's.")
    print("  Benchmarks use ADJUSTED closes (dividends reinvested).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
