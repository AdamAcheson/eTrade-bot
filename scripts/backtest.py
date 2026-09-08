#!/usr/bin/env python3
"""Backtests the strategy against real historical price data (see
src/data/historical_data.py -- Yahoo Finance's public chart API, ~last 60 days of
5-minute bars). Uses the same TradingBot/PaperBrokerAdapter/InMemoryMarketDataProvider
stack as everything else -- this is not a separate simulation engine, it just feeds
historical bars through the real pipeline in chronological order instead of live
quotes.

Run scripts/fetch_historical_data.py first to populate the local cache.

Known approximations, since Yahoo's free intraday data doesn't include these:
  * Bid/ask is synthesized as a fraction of the ticker's configured max_spread_pct
    around each bar's close -- not real historical spread.
  * Relative volume baseline is computed from PRIOR days only (no lookahead bias):
    for each 5-minute bar-of-day index, the baseline is the average cumulative
    volume by that point across all earlier days in the loaded history. The very
    first backtest day has no prior data, so RVOL is unavailable that day only
    (mirrors a real bot's cold start).

Usage:
    python3 scripts/backtest.py [--days N]   # default: all cached days
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from broker.paper import PaperBrokerAdapter  # noqa: E402
from config_loader import AppConfig, ConfigError, load_config  # noqa: E402
from data.historical_data import HistoricalDataError, get_bars, group_bars_by_day  # noqa: E402
from data.market_data import InMemoryMarketDataProvider, Quote  # noqa: E402
from main import TradingBot  # noqa: E402
from models.bar import Bar  # noqa: E402
from models.signal import Decision  # noqa: E402
from reporting.daily_report import generate_daily_report, generate_feedback_suggestions  # noqa: E402
from reporting.journal import SignalJournal, TradeJournal  # noqa: E402


def synthesize_quote(bar: Bar, max_spread_pct: float) -> Quote:
    """Yahoo's free intraday data has no bid/ask -- approximate a typical (not
    maximal) spread as 30% of the ticker's configured max, centered on the close."""
    spread_pct = max_spread_pct * 0.3 / 100.0
    half_spread = bar.close * spread_pct / 2.0
    return Quote(bid=bar.close - half_spread, ask=bar.close + half_spread, last=bar.close, timestamp=bar.timestamp)


def build_volume_baselines(bars_by_day: Dict[str, List[Bar]]) -> Dict[str, Dict[int, float]]:
    """For each calendar day (sorted), maps bar-index-within-day -> average
    cumulative volume by that index across all STRICTLY EARLIER days. No lookahead:
    a given day's baseline only ever uses days before it."""
    days_sorted = sorted(bars_by_day.keys())
    cum_by_day_index: Dict[str, List[float]] = {}
    for day in days_sorted:
        cum = 0.0
        cums = []
        for b in bars_by_day[day]:
            cum += b.volume
            cums.append(cum)
        cum_by_day_index[day] = cums

    baseline_by_day: Dict[str, Dict[int, float]] = {}
    for idx, day in enumerate(days_sorted):
        prior_days = days_sorted[:idx]
        if not prior_days:
            baseline_by_day[day] = {}
            continue
        max_len = max(len(cum_by_day_index[d]) for d in prior_days)
        baseline = {}
        for i in range(max_len):
            values = [cum_by_day_index[d][i] for d in prior_days if i < len(cum_by_day_index[d])]
            if values:
                baseline[i] = sum(values) / len(values)
        baseline_by_day[day] = baseline
    return baseline_by_day


def find_missed_opportunities_multiday(rejected_signals, bars_by_symbol: Dict[str, List[Bar]], lookahead_bars: int = 78) -> List[dict]:
    """Per-signal version of reporting.daily_report.find_missed_opportunities --
    that function assumes one signal per ticker per call (fine for a single day's
    live report); a multi-day backtest has many signals per ticker at different
    times, so each one needs its own slice of "bars after this specific signal,"
    not the ticker's entire history. Caps the lookahead to ~1 trading day of bars
    so a rejection isn't credited with a "miss" from an unrelated future session."""
    missed = []
    for s in rejected_signals:
        if s.entry_price is None or s.stop is None or s.target is None:
            continue
        future_bars = [b for b in bars_by_symbol.get(s.ticker, []) if b.timestamp > s.timestamp][:lookahead_bars]
        for b in future_bars:
            if b.low <= s.stop:
                break
            if b.high >= s.target:
                missed.append({
                    "ticker": s.ticker,
                    "timestamp": s.timestamp.isoformat(),
                    "rejection_reason": s.rejection_reason.value if s.rejection_reason else None,
                    "setup_type": s.setup_type,
                    "entry_price": s.entry_price,
                    "target": s.target,
                })
                break
    return missed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None, help="Limit to the most recent N trading days (default: all cached)")
    parser.add_argument(
        "--first-days", type=int, default=None,
        help="Limit to the OLDEST N trading days instead of the most recent -- for validating "
             "against a holdout slice that hasn't already been eyeballed while tuning config/strategy.yaml. "
             "Mutually exclusive with --days.",
    )
    parser.add_argument(
        "--max-risk-dollars", type=float, default=None,
        help="EXPERIMENT override: flat dollar risk cap per trade instead of config/risk.yaml's "
             "%%-of-equity sizing (applied in-memory only -- never written back to the config file).",
    )
    parser.add_argument(
        "--stop-atr-multiplier", type=float, default=None,
        help="EXPERIMENT override: replaces all three of config/risk.yaml's stops.atr_multiplier "
             "categories with this single value (applied in-memory only).",
    )
    parser.add_argument(
        "--max-hold-days", type=int, default=None,
        help="EXPERIMENT override: hard force-exit a position after this many calendar days "
             "(applied in-memory only).",
    )
    parser.add_argument(
        "--allow-red-overnight", action="store_true",
        help="EXPERIMENT override: a position below entry price can still pass the overnight "
             "review if every other thesis check passes (applied in-memory only). Only sound "
             "combined with --max-risk-dollars, which already bounds the loss if wrong.",
    )
    parser.add_argument(
        "--scale-target-with-stop", action="store_true",
        help="EXPERIMENT override: ignore each ticker's fixed profit_target_pct and always "
             "target preferred_r_min x the actual stop distance, so a wider stop (e.g. via "
             "--stop-atr-multiplier) doesn't silently shrink the R-ratio (applied in-memory only).",
    )
    parser.add_argument(
        "--preferred-r-min", type=float, default=None,
        help="EXPERIMENT override: replaces reward_risk.preferred_r_min, the fixed R-multiple "
             "used by --scale-target-with-stop (applied in-memory only).",
    )
    parser.add_argument(
        "--disable-orb", action="store_true",
        help="EXPERIMENT override: skip ORB_PULLBACK_CONTINUATION entirely and always try "
             "VWAP_RECLAIM instead (applied in-memory only).",
    )
    args = parser.parse_args()
    if args.days and args.first_days:
        print("--days and --first-days are mutually exclusive", file=sys.stderr)
        return 1

    try:
        config: AppConfig = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    experiment_active = any([
        args.max_risk_dollars is not None, args.stop_atr_multiplier is not None,
        args.max_hold_days is not None, args.allow_red_overnight, args.scale_target_with_stop,
        args.preferred_r_min is not None, args.disable_orb,
    ])
    if experiment_active:
        print("EXPERIMENT overrides active (in-memory only, config/risk.yaml is untouched):")
        if args.max_risk_dollars is not None:
            config.risk["account"]["max_risk_dollars_per_trade"] = args.max_risk_dollars
            print(f"  max_risk_dollars_per_trade = {args.max_risk_dollars}")
        if args.stop_atr_multiplier is not None:
            for category in config.risk["stops"]["atr_multiplier"]:
                config.risk["stops"]["atr_multiplier"][category] = args.stop_atr_multiplier
            print(f"  stops.atr_multiplier (all categories) = {args.stop_atr_multiplier}")
        if args.max_hold_days is not None:
            config.risk["safety"]["max_hold_days"] = args.max_hold_days
            print(f"  safety.max_hold_days = {args.max_hold_days}")
        if args.allow_red_overnight:
            config.risk["overnight"]["require_at_or_above_entry"] = False
            print("  overnight.require_at_or_above_entry = False")
        if args.scale_target_with_stop:
            config.strategy["reward_risk"]["scale_target_with_stop"] = True
            print("  reward_risk.scale_target_with_stop = True")
        if args.preferred_r_min is not None:
            config.strategy["reward_risk"]["preferred_r_min"] = args.preferred_r_min
            print(f"  reward_risk.preferred_r_min = {args.preferred_r_min}")
        if args.disable_orb:
            config.strategy["setups"]["enable_orb_pullback"] = False
            print("  setups.enable_orb_pullback = False")
        print()

    universe = config.auto_tradeable_universe()
    benchmarks = sorted({config.benchmark_of(t) for t in universe})
    all_symbols = sorted(set(universe) | set(benchmarks))

    print(f"Loading cached historical bars for {len(all_symbols)} symbols...")
    bars_by_symbol: Dict[str, List[Bar]] = {}
    for symbol in all_symbols:
        try:
            bars_by_symbol[symbol] = get_bars(symbol)
        except HistoricalDataError as e:
            print(f"  {symbol}: no cached data ({e}) -- run scripts/fetch_historical_data.py first", file=sys.stderr)
            return 1

    days_by_symbol = {s: group_bars_by_day(bars_by_symbol[s]) for s in all_symbols}
    all_days = sorted({day for s in all_symbols for day in days_by_symbol[s].keys()})
    if args.days:
        all_days = all_days[-args.days:]
    elif args.first_days:
        all_days = all_days[:args.first_days]
    print(f"Backtesting {len(all_days)} trading days: {all_days[0]} to {all_days[-1]}")

    baselines_by_symbol = {s: build_volume_baselines(days_by_symbol[s]) for s in all_symbols}

    provider = InMemoryMarketDataProvider()
    broker = PaperBrokerAdapter(
        starting_equity=config.broker["paper"]["starting_equity"],
        fill_model=config.broker["paper"]["fill_model"],
    )
    # Journals append -- start clean each run so results aren't mixed with a
    # previous backtest's output.
    for path in ("logs/backtest_signals.jsonl", "reports/backtest_trades.jsonl"):
        if os.path.exists(path):
            os.remove(path)
    signal_journal = SignalJournal("logs/backtest_signals.jsonl")
    trade_journal = TradeJournal("reports/backtest_trades.jsonl")
    bot = TradingBot(config, provider, broker, signal_journal, trade_journal)

    seen_signals = 0
    entries = 0

    for day in all_days:
        # Without this, RiskManager's daily counters (trades_today, consecutive
        # losses, cooldowns) never reset across a multi-day backtest -- confirmed:
        # max_trades_per_day silently blocked every signal after the 3rd trade of
        # the entire 60-day run, not just the 3rd trade of each day.
        bot.risk_manager.reset_daily_counters()

        # bar-index-within-day per symbol for this day, keyed by timestamp, so we
        # can look up the right volume baseline as we replay in time order.
        index_by_ts: Dict[str, Dict[datetime, int]] = {}
        for symbol in all_symbols:
            day_bars = days_by_symbol[symbol].get(day, [])
            index_by_ts[symbol] = {b.timestamp: i for i, b in enumerate(day_bars)}

        day_timestamps = sorted({b.timestamp for s in all_symbols for b in days_by_symbol[s].get(day, [])})

        for ts in day_timestamps:
            for symbol in all_symbols:
                idx = index_by_ts[symbol].get(ts)
                if idx is None:
                    continue
                bar = days_by_symbol[symbol][day][idx]
                max_spread = config.max_spread_pct(symbol)
                provider.push_bar(symbol, bar, received_at=ts)
                provider.push_quote(symbol, synthesize_quote(bar, max_spread), received_at=ts)
                baseline = baselines_by_symbol[symbol].get(day, {}).get(idx, 0.0)
                provider.set_average_volume_baseline(symbol, baseline)

            bot.run_cycle(ts)

            new_signals = bot.signal_journal.signals[seen_signals:]
            seen_signals = len(bot.signal_journal.signals)
            for s in new_signals:
                if s.decision == Decision.ENTRY_CANDIDATE:
                    entries += 1
                    print(
                        f"[{ts:%Y-%m-%d %H:%M}] ENTRY  {s.ticker:<6} setup={s.setup_type} "
                        f"score={s.setup_score:.1f} entry={s.entry_price:.2f} stop={s.stop:.2f} "
                        f"target={s.target:.2f} R={s.r_ratio:.2f}"
                    )

        print(f"[{day}] day complete -- open positions: {len(bot.position_manager.open_positions())}")

    all_signals = bot.signal_journal.signals
    all_trades = bot.trade_journal.trades

    print("\n" + "=" * 70)
    print("BACKTEST RESULTS")
    print("=" * 70)
    print(f"Period: {all_days[0]} to {all_days[-1]} ({len(all_days)} trading days)")
    print(f"Entry candidates: {entries}")
    print(f"Trades closed: {len(all_trades)}")

    report = generate_daily_report(
        date=f"{all_days[0]}_to_{all_days[-1]}",
        signals=all_signals,
        trades=all_trades,
        open_overnight_tickers=[p.ticker for p in bot.position_manager.open_positions()],
    )
    print(f"\nStocks evaluated: {report.stocks_evaluated}")
    print(f"Setups identified: {report.setups_identified}")
    print(f"Trades taken: {report.trades_taken}")
    print(f"Rejection reasons: {report.rejection_reasons}")
    print(f"Wins: {report.wins}  Losses: {report.losses}  Win rate: {report.win_rate}")
    print(f"Gross P&L: ${report.gross_pnl:,.2f}  Net P&L: ${report.net_pnl:,.2f}")
    print(f"Average gain: {report.average_gain}  Average loss: {report.average_loss}  Average R: {report.average_r}")
    print(f"Best trade: {report.best_trade}  Worst trade: {report.worst_trade}")
    print(f"Max drawdown: ${report.max_drawdown:,.2f}")
    print(f"Open overnight positions: {report.open_overnight_positions}")

    suggestions = generate_feedback_suggestions(all_trades, all_signals)
    if suggestions:
        print("\nFeedback (informational only -- no rule changes applied automatically):")
        for s in suggestions:
            print(f"  - {s}")

    rejected = [s for s in all_signals if s.decision == Decision.REJECTED]
    missed = find_missed_opportunities_multiday(rejected, bars_by_symbol)
    print(f"\nMissed opportunities (rejected signals that would have hit target): {len(missed)}")
    for m in missed[:20]:
        print(f"  {m['timestamp']}  {m['ticker']:<6} {m['rejection_reason']} -> would have hit target {m['target']:.2f}")
    if len(missed) > 20:
        print(f"  ... and {len(missed) - 20} more")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
