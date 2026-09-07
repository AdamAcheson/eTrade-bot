#!/usr/bin/env python3
"""Backtests the mining strategy (src/strategy) against the approved universe in
config/tickers.yaml, using free historical bars fetched on demand (see
src/backtest/data_fetch.py) and the same TradingBot pipeline the paper/sandbox
bot runs (see src/backtest/engine.py). Nothing here places any real order --
this only ever drives PaperBrokerAdapter against historical data.

Usage:
    python3 scripts/backtest.py [--range 60d] [--tickers AG,SVM,HL] [--no-cache]

Notes on what this measures (see src/backtest/engine.py and data_fetch.py
docstrings for the full caveats):
  * ~60 calendar days of 5-minute bars is the most free intraday history
    available (Yahoo Finance) -- this is a recent-period backtest, not a
    multi-year one.
  * Quotes are synthesized (a small spread around each bar's close) since no
    historical bid/ask is available for free -- fills and spread-based
    rejections are an approximation.
  * Past performance of a rules-based strategy on recent data is not a
    guarantee of future results.
"""

import argparse
import dataclasses
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from backtest.data_fetch import (  # noqa: E402
    DataFetchError,
    daily_volume_baseline,
    fetch_daily_bars,
    fetch_intraday_bars,
)
from backtest.engine import BacktestEngine  # noqa: E402
from config_loader import ConfigError, load_config  # noqa: E402
from reporting.daily_report import generate_daily_report, generate_feedback_suggestions  # noqa: E402


def fetch_all(symbols, intraday_range: str, use_cache: bool):
    bars_by_symbol = {}
    baseline_by_symbol = {}
    for i, symbol in enumerate(symbols, 1):
        print(f"  [{i}/{len(symbols)}] fetching {symbol} ...", file=sys.stderr)
        try:
            bars_by_symbol[symbol] = fetch_intraday_bars(symbol, range_=intraday_range, use_cache=use_cache)
            daily = fetch_daily_bars(symbol, range_="1y", use_cache=use_cache)
            baseline_by_symbol[symbol] = daily_volume_baseline(daily, lookback=20)
        except DataFetchError as e:
            print(f"    WARNING: {e}", file=sys.stderr)
    return bars_by_symbol, baseline_by_symbol


def buy_and_hold_returns(bars_by_symbol, universe):
    """Equal-weight buy-and-hold return per ticker over the same bar window, for
    context: did the strategy miss a real move, or was there nothing to catch?"""
    returns = {}
    for t in universe:
        bars = bars_by_symbol.get(t)
        if not bars:
            continue
        first, last = bars[0].close, bars[-1].close
        if first:
            returns[t] = (last - first) / first * 100.0
    return returns


def print_report(result, universe, bars_by_symbol) -> None:
    closed = [t for t in result.trades if t.net_profit is not None]
    report = generate_daily_report(
        date="ALL",
        signals=result.signals,
        trades=result.trades,
        open_overnight_tickers=result.still_open_at_end,
    )

    total_return_pct = (
        (result.ending_equity - result.starting_equity) / result.starting_equity * 100.0
        if result.starting_equity
        else 0.0
    )

    print("\n" + "=" * 72)
    print("BACKTEST RESULT")
    print("=" * 72)
    print(f"  Universe:            {len(universe)} tickers -- {', '.join(universe)}")
    print(f"  Trading days:        {result.days_simulated}")
    print(f"  Starting equity:     ${result.starting_equity:,.2f}")
    print(f"  Ending equity:       ${result.ending_equity:,.2f}")
    print(f"  Total return:        {total_return_pct:+.2f}%")
    print()
    print(f"  Stocks evaluated:    {report.stocks_evaluated}")
    print(f"  Setups identified:   {report.setups_identified}")
    print(f"  Trades taken:        {report.trades_taken}")
    print(f"  Signals rejected:    {report.trades_rejected}")
    print()
    if closed:
        print(f"  Win rate:            {report.win_rate:.1%}  ({report.wins}W / {report.losses}L)")
        print(f"  Gross P&L:           ${report.gross_pnl:,.2f}")
        print(f"  Net P&L:             ${report.net_pnl:,.2f}")
        print(f"  Avg win:             ${report.average_gain:,.2f}" if report.average_gain else "  Avg win:             n/a")
        print(f"  Avg loss:            ${report.average_loss:,.2f}" if report.average_loss else "  Avg loss:            n/a")
        print(f"  Avg R multiple:      {report.average_r:.2f}R" if report.average_r is not None else "  Avg R multiple:      n/a")
        print(f"  Best trade:          {report.best_trade}")
        print(f"  Worst trade:         {report.worst_trade}")
        print(f"  Max drawdown (trades): ${report.max_drawdown:,.2f}")
    else:
        print("  No trades were taken -- see rejection reasons below.")

    if result.still_open_at_end:
        print(f"  Still open at end:   {', '.join(result.still_open_at_end)}")

    print("\n  Top rejection reasons:")
    for reason, count in Counter(report.rejection_reasons).most_common(8):
        print(f"    {reason:<32} {count}")

    if closed:
        print("\n  Per-ticker breakdown:")
        by_ticker = defaultdict(list)
        for t in closed:
            by_ticker[t.ticker].append(t)
        for ticker in sorted(by_ticker, key=lambda k: -len(by_ticker[k])):
            trades = by_ticker[ticker]
            wins = sum(1 for t in trades if t.net_profit > 0)
            pnl = sum(t.net_profit for t in trades)
            print(f"    {ticker:<6} {len(trades):>3} trades  {wins}/{len(trades)} win  net ${pnl:,.2f}")

    suggestions = generate_feedback_suggestions(result.trades, result.signals)
    if suggestions:
        print("\n  Observations (read-only, not auto-applied):")
        for s in suggestions:
            print(f"    - {s}")

    bh = buy_and_hold_returns(bars_by_symbol, universe)
    if bh:
        avg_bh = sum(bh.values()) / len(bh)
        print(f"\n  For context, equal-weight buy-and-hold over the same window: {avg_bh:+.1f}%")
        print("  (did the strategy have a real move to catch, or was the tape flat?)")
        for t, pct in sorted(bh.items(), key=lambda kv: -kv[1])[:5]:
            print(f"    {t:<6} {pct:+.1f}%")

    print("=" * 72)


def write_json_summary(path, result, universe, bars_by_symbol) -> None:
    report = generate_daily_report(
        date="ALL",
        signals=result.signals,
        trades=result.trades,
        open_overnight_tickers=result.still_open_at_end,
    )
    bh = buy_and_hold_returns(bars_by_symbol, universe)
    payload = {
        "universe": universe,
        "days_simulated": result.days_simulated,
        "starting_equity": result.starting_equity,
        "ending_equity": result.ending_equity,
        "still_open_at_end": result.still_open_at_end,
        "trades_taken": report.trades_taken,
        "win_rate": report.win_rate,
        "gross_pnl": report.gross_pnl,
        "net_pnl": report.net_pnl,
        "average_r": report.average_r,
        "rejection_reasons": report.rejection_reasons,
        "buy_and_hold_pct_by_ticker": bh,
        "trades": [t.as_log_row() for t in result.trades],
        "equity_curve": [
            {"date": p.day.isoformat(), "equity": p.equity, "open_positions": p.open_positions}
            for p in result.equity_curve
        ],
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--range", default="60d", help="Yahoo intraday range, e.g. 60d (max for 5m bars)")
    parser.add_argument("--tickers", default=None, help="Comma-separated subset of config/tickers.yaml to test")
    parser.add_argument("--spread-pct", type=float, default=0.05, help="Synthetic bid/ask spread %% around each close")
    parser.add_argument("--no-cache", action="store_true", help="Ignore data/cache/ and refetch everything")
    parser.add_argument("--output", default="reports/backtest_summary.json", help="Where to write the JSON summary")
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    universe = config.auto_tradeable_universe()
    if args.tickers:
        requested = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        unknown = [t for t in requested if t not in universe]
        if unknown:
            print(f"Not in the auto-tradeable universe: {unknown}", file=sys.stderr)
            return 1
        # BacktestEngine and TradingBot.run_cycle both derive the tradeable
        # universe from config.auto_tradeable_universe() -- to actually restrict
        # which tickers get evaluated (not just which ones we fetch data for), a
        # requested subset must be reflected in the config itself.
        universe = requested
        config = dataclasses.replace(config, tickers={t: config.tickers[t] for t in requested})

    benchmarks = sorted({config.benchmark_of(t) for t in universe})
    all_symbols = sorted(set(universe) | set(benchmarks))

    print(f"Fetching historical data for {len(all_symbols)} symbols "
          f"({len(universe)} tickers + {len(benchmarks)} benchmarks)...", file=sys.stderr)
    bars_by_symbol, baseline_by_symbol = fetch_all(all_symbols, args.range, use_cache=not args.no_cache)

    missing = [s for s in all_symbols if not bars_by_symbol.get(s)]
    if missing:
        print(f"\nERROR: could not fetch data for: {missing} -- aborting.", file=sys.stderr)
        return 1

    engine = BacktestEngine(
        config=config,
        bars_by_symbol=bars_by_symbol,
        volume_baseline_by_symbol=baseline_by_symbol,
        synthetic_spread_pct=args.spread_pct,
    )
    print(f"Replaying {len(engine.trading_days)} trading days through TradingBot...", file=sys.stderr)
    result = engine.run()

    print_report(result, universe, bars_by_symbol)
    write_json_summary(args.output, result, universe, bars_by_symbol)
    print(f"\nFull summary written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
