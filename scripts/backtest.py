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

    # Running totals rather than re-summing every prior day for every bar index.
    # The naive form is O(days^2 x bars) per symbol, which was tolerable at 60 days
    # of Yahoo history but takes ~35 minutes a run now that some symbols carry 750
    # days -- long enough to make parameter sweeps impractical. Semantics are
    # identical: when day `idx` is processed, sums/counts contain exactly the days
    # strictly before it, so there is still no lookahead.
    baseline_by_day: Dict[str, Dict[int, float]] = {}
    sums: Dict[int, float] = defaultdict(float)
    counts: Dict[int, int] = defaultdict(int)
    for day in days_sorted:
        baseline_by_day[day] = {i: sums[i] / counts[i] for i in sums if counts[i]}
        for i, cumulative in enumerate(cum_by_day_index[day]):
            sums[i] += cumulative
            counts[i] += 1
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
        "--end-offset", type=int, default=0,
        help="Drop the most recent N trading days before applying --days. Combined with --days "
             "this selects an arbitrary window, which is what a train/test split needs: "
             "'--days 91 --end-offset 92' is the older half of the last 183 days, and "
             "'--days 92' the newer half.",
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
    parser.add_argument(
        "--max-concurrent-positions", type=int, default=None,
        help="EXPERIMENT override: replaces behavior.max_concurrent_positions, how many "
             "positions may be open at once (applied in-memory only).",
    )
    parser.add_argument(
        "--trailing-atr", type=float, default=None,
        help="EXPERIMENT override: enable the ratcheting trailing stop at this ATR multiple "
             "below the high-water mark (applied in-memory only).",
    )
    parser.add_argument(
        "--score-bump", type=float, default=None,
        help="EXPERIMENT override: add this many points to BOTH minimum entry score "
             "windows, preserving the 10-point primary/midday gap. Trades less, and "
             "each avoided trade saves its spread as well as its risk.",
    )
    parser.add_argument(
        "--regime-filter", choices=("prior_day", "open_gap"), default=None,
        help="EXPERIMENT override: only trade days when every --regime-symbol was "
             "rising. prior_day = all closed up YESTERDAY; open_gap = all opened "
             "above their previous close TODAY. Both are decidable before the first "
             "entry window, so neither peeks at the session's outcome.",
    )
    parser.add_argument(
        "--regime-symbols", default="DIA,SLV",
        help="Comma-separated reference instruments for --regime-filter.",
    )
    parser.add_argument(
        "--gap-day-pct", type=float, default=None,
        help="EXPERIMENT override: treat a session as a GAP DAY when |open vs prior "
             "close| is at least this percent, and apply --gap-day-rvol in place of "
             "the standard relative-volume gate. 0 disables.",
    )
    parser.add_argument(
        "--gap-day-rvol", type=float, default=None,
        help="EXPERIMENT override: the relaxed relative-volume threshold used on gap "
             "days (see --gap-day-pct).",
    )
    parser.add_argument(
        "--min-price", type=float, default=None,
        help="EXPERIMENT override: reject entries below this share price. A tick-cost "
             "screen -- the one-cent tick costs 12 bps on a $4 stock and 0.5 on a $95 "
             "one. 10 caps tick cost at 5 bps per side, 20 at 2.5.",
    )
    parser.add_argument(
        "--spread-ticks", type=float, default=None,
        help="EXPERIMENT override: assumed quoted spread in CENTS. 1.0 (the shipped "
             "default) is the tightest a US equity quote can legally be and so is a "
             "floor on real cost; 0 restores the old frictionless backtest.",
    )
    parser.add_argument(
        "--impact-bps", type=float, default=None,
        help="EXPERIMENT override: market impact in bps per side on notional.",
    )
    parser.add_argument(
        "--commission", type=float, default=None,
        help="EXPERIMENT override: commission per order in dollars.",
    )
    parser.add_argument(
        "--min-stop-pct", type=float, default=None,
        help="EXPERIMENT override: floor the stop at this %% of entry price (in-memory "
             "only). Proxy for sizing stops off DAILY volatility rather than the "
             "14-period ATR of 5-minute bars, which measures ~70 minutes and collapses "
             "in a quiet afternoon. These names average ~5%% daily range, so 1.0 here is "
             "roughly 0.2x daily ATR.",
    )
    parser.add_argument(
        "--min-relative-volume", type=float, default=None,
        help="EXPERIMENT override: eligibility.min_relative_volume, the hard RVOL entry "
             "gate (in-memory only). The shipped 1.10 demands today's cumulative volume "
             "exceed 110%% of the all-history average at the same bar index, which refuses "
             "moves that happen on ordinary participation.",
    )
    parser.add_argument(
        "--config-dir", type=str, default=None,
        help="Load config/*.yaml from this directory instead of config/. Used to run the "
             "strategy against an ALTERNATE UNIVERSE without adding those tickers to the "
             "shipped config -- putting e.g. TSLA in config/tickers.yaml would silently widen "
             "what the live bot trades. The experiment directory symlinks strategy.yaml, "
             "risk.yaml, broker.yaml and schedule.yaml back to config/, so only the universe "
             "differs and the parameters cannot drift apart.",
    )
    parser.add_argument(
        "--only-tickers", type=str, default=None,
        help="Restrict the tradeable universe to these comma-separated tickers (in-memory "
             "only). Needed to compare periods fairly: the deep-history holdout can only "
             "trade the four tickers cached that far back, so measuring the recent period on "
             "the same four separates a period effect from a universe-size effect.",
    )
    parser.add_argument(
        "--starting-equity", type=float, default=None,
        help="EXPERIMENT override: paper account starting equity (in-memory only). "
             "Results do NOT scale linearly: at small balances the 25%%-of-equity cap "
             "binds instead of the $25k notional cap, and share counts round down to "
             "whole shares, which bites hardest on expensive tickers.",
    )
    parser.add_argument(
        "--no-chase-rule", action="store_true",
        help="EXPERIMENT override: disable the do-not-chase rule entirely (in-memory only).",
    )
    parser.add_argument(
        "--chase-max-atr-above-vwap", type=float, default=None,
        help="EXPERIMENT override: how far above VWAP a stock may trade and still be "
             "entered, in ATRs (in-memory only). The shipped 1.0 is close to the "
             "definition of a strong mover, and 97%% of holdout profit comes from 6.7%% "
             "of trades, so this rule may be refusing the trades that pay.",
    )
    parser.add_argument(
        "--no-partial-exit", action="store_true",
        help="EXPERIMENT override: disable the 1.5R partial exit (applied in-memory only). "
             "It sells sell_fraction of EVERY winner at 1.5R -- including the small number of "
             "trades that go on to carry the whole P&L.",
    )
    parser.add_argument(
        "--partial-exit-trigger-r", type=float, default=None,
        help="EXPERIMENT override: R-multiple at which the partial exit fires (in-memory only).",
    )
    parser.add_argument(
        "--no-trailing", action="store_true",
        help="EXPERIMENT override: force the ratcheting trailing stop OFF (applied in-memory "
             "only). Needed to reproduce pre-trailing behavior now that config/strategy.yaml "
             "enables it by default -- without this there is no way to run the old exit rule "
             "as a comparison baseline.",
    )
    parser.add_argument(
        "--trailing-activate-r", type=float, default=None,
        help="EXPERIMENT override: R-multiple at which the trailing stop starts ratcheting "
             "(applied in-memory only). Requires --trailing-atr.",
    )
    parser.add_argument(
        "--max-position-size-dollars", type=float, default=None,
        help="EXPERIMENT override: replaces sizing.max_position_size_dollars, the notional cap "
             "per position (applied in-memory only). This cap -- not the risk budget -- is what "
             "actually binds on most trades, so it is the real position-size lever.",
    )
    parser.add_argument(
        "--max-position-pct-equity", type=float, default=None,
        help="EXPERIMENT override: replaces sizing.max_position_size_pct_equity (applied "
             "in-memory only). Position size is capped by BOTH this and "
             "--max-position-size-dollars, whichever binds first, so raising only one of "
             "them leaves the other in force.",
    )
    parser.add_argument(
        "--max-trades-per-day", type=int, default=None,
        help="EXPERIMENT override: replaces safety.max_trades_per_day (applied in-memory only).",
    )
    parser.add_argument(
        "--tag", type=str, default="",
        help="Suffix for this run's journal files (logs/backtest_signals<TAG>.jsonl, "
             "reports/backtest_trades<TAG>.jsonl). Give each variant of a sweep its own tag -- "
             "runs sharing a journal overwrite each other's results.",
    )
    args = parser.parse_args()
    if args.days and args.first_days:
        print("--days and --first-days are mutually exclusive", file=sys.stderr)
        return 1

    try:
        config: AppConfig = (
            load_config(args.config_dir) if args.config_dir else load_config()
        )
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    experiment_active = any([
        args.max_risk_dollars is not None, args.stop_atr_multiplier is not None,
        args.max_hold_days is not None, args.allow_red_overnight, args.scale_target_with_stop,
        args.preferred_r_min is not None, args.disable_orb,
        args.max_concurrent_positions is not None,
        args.max_position_size_dollars is not None, args.max_trades_per_day is not None,
        args.trailing_atr is not None, args.max_position_pct_equity is not None,
        args.no_trailing, args.only_tickers is not None,
        args.no_partial_exit, args.partial_exit_trigger_r is not None,
        args.no_chase_rule, args.chase_max_atr_above_vwap is not None,
        args.starting_equity is not None, args.min_relative_volume is not None,
        args.min_stop_pct is not None,
        args.spread_ticks is not None, args.impact_bps is not None, args.commission is not None,
        args.min_price is not None,
        args.gap_day_pct is not None, args.gap_day_rvol is not None,
        args.score_bump is not None,
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
        if args.max_concurrent_positions is not None:
            # Read in two places (PositionManager construction and RiskManager's
            # gate), both off config.risk -- overriding here, before TradingBot is
            # built below, covers both.
            config.risk["behavior"]["max_concurrent_positions"] = args.max_concurrent_positions
            print(f"  behavior.max_concurrent_positions = {args.max_concurrent_positions}")
        if args.starting_equity is not None:
            config.broker["paper"]["starting_equity"] = args.starting_equity
            print(f"  paper.starting_equity = {args.starting_equity}")
        if args.no_chase_rule:
            # Every threshold to a value no bar can reach, rather than adding a
            # branch to the strategy layer for an experiment.
            c = config.strategy["chase_rule"]
            c["max_consecutive_large_green_candles"] = 10_000
            c["max_atr_above_vwap"] = 1e9
            c["max_pct_above_open_without_consolidation"] = 1e9
            print("  chase_rule = OFF")
        if args.chase_max_atr_above_vwap is not None:
            config.strategy["chase_rule"]["max_atr_above_vwap"] = args.chase_max_atr_above_vwap
            print(f"  chase_rule.max_atr_above_vwap = {args.chase_max_atr_above_vwap}")
        if args.no_partial_exit:
            config.strategy["trade_management"]["partial_exit"]["enabled"] = False
            print("  partial_exit = OFF")
        if args.partial_exit_trigger_r is not None:
            config.strategy["trade_management"]["partial_exit"]["trigger_r"] = args.partial_exit_trigger_r
            print(f"  partial_exit.trigger_r = {args.partial_exit_trigger_r}")
        if args.score_bump is not None:
            mes = config.strategy["scoring"]["minimum_entry_score"]
            for key in ("primary_window", "midday_window"):
                mes[key] = mes[key] + args.score_bump
            print(f"  scoring.minimum_entry_score = {mes}")
        if args.gap_day_pct is not None or args.gap_day_rvol is not None:
            gd = config.strategy["eligibility"].setdefault("gap_day", {})
            if args.gap_day_pct is not None:
                gd["min_gap_pct"] = args.gap_day_pct
            if args.gap_day_rvol is not None:
                gd["min_relative_volume"] = args.gap_day_rvol
            print(f"  eligibility.gap_day = {gd}")
        if args.min_price is not None:
            config.strategy["eligibility"]["min_price"] = args.min_price
            print(f"  eligibility.min_price = {args.min_price}")
        for flag, key in (("spread_ticks", "spread_ticks"), ("impact_bps", "impact_bps"),
                          ("commission", "commission_per_order")):
            value = getattr(args, flag)
            if value is not None:
                config.risk.setdefault("transaction_costs", {})[key] = value
                print(f"  transaction_costs.{key} = {value}")
        if args.min_stop_pct is not None:
            config.risk["stops"]["min_stop_pct_of_price"] = args.min_stop_pct
            print(f"  stops.min_stop_pct_of_price = {args.min_stop_pct}")
        if args.min_relative_volume is not None:
            config.strategy["eligibility"]["min_relative_volume"] = args.min_relative_volume
            print(f"  eligibility.min_relative_volume = {args.min_relative_volume}")
        if args.only_tickers is not None:
            keep = {t.strip().upper() for t in args.only_tickers.split(",") if t.strip()}
            missing = keep - set(config.tickers)
            if missing:
                print(f"unknown ticker(s): {', '.join(sorted(missing))}", file=sys.stderr)
                return 1
            # auto_tradeable_universe() filters on manual_only, so excluding a ticker
            # means marking it manual-only -- the same switch that keeps NZAUF/AAGAF
            # out, and one the strategy layer already honours everywhere.
            for name, cfg in config.tickers.items():
                if name not in keep:
                    cfg.manual_only = True
            print(f"  universe restricted to {', '.join(sorted(keep))}")
        if args.no_trailing:
            config.strategy["trade_management"].setdefault("trailing_stop", {})["enabled"] = False
            print("  trailing_stop = OFF")
        if args.trailing_atr is not None:
            ts = config.strategy["trade_management"].setdefault("trailing_stop", {})
            ts["enabled"] = True
            ts["atr_multiplier"] = args.trailing_atr
            if args.trailing_activate_r is not None:
                ts["activate_at_r"] = args.trailing_activate_r
            print(f"  trailing_stop = ON, {args.trailing_atr} x ATR, "
                  f"activate at {ts.get('activate_at_r', 1.0)}R")
        if args.max_position_size_dollars is not None:
            # Two copies of this number live in risk.yaml (sizing.* is what
            # position_sizing.py receives via main.py; safety.* is the RiskManager's
            # independent guard). Overriding only one leaves the other binding.
            config.risk["sizing"]["max_position_size_dollars"] = args.max_position_size_dollars
            config.risk["safety"]["max_position_size_dollars"] = args.max_position_size_dollars
            print(f"  max_position_size_dollars = {args.max_position_size_dollars}")
        if args.max_position_pct_equity is not None:
            config.risk["sizing"]["max_position_size_pct_equity"] = args.max_position_pct_equity
            print(f"  max_position_size_pct_equity = {args.max_position_pct_equity}")
        if args.max_trades_per_day is not None:
            config.risk["safety"]["max_trades_per_day"] = args.max_trades_per_day
            print(f"  safety.max_trades_per_day = {args.max_trades_per_day}")
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
    if args.end_offset:
        all_days = all_days[:-args.end_offset]
    if args.days:
        all_days = all_days[-args.days:]
    elif args.first_days:
        all_days = all_days[:args.first_days]
    if args.regime_filter:
        from data.market_regime import qualifying_days
        symbols = [s.strip() for s in args.regime_symbols.split(",") if s.strip()]
        good = qualifying_days(symbols, args.regime_filter)
        before = len(all_days)
        all_days = [d for d in all_days if d in good]
        if not all_days:
            print("regime filter removed every day", file=sys.stderr)
            return 1
        print(f"  regime filter {args.regime_filter} on {','.join(symbols)}: "
              f"{len(all_days)}/{before} days kept ({len(all_days)/before:.0%})")
    print(f"Backtesting {len(all_days)} trading days: {all_days[0]} to {all_days[-1]}")

    baselines_by_symbol = {s: build_volume_baselines(days_by_symbol[s]) for s in all_symbols}

    provider = InMemoryMarketDataProvider()
    broker = PaperBrokerAdapter(
        starting_equity=config.broker["paper"]["starting_equity"],
        fill_model=config.broker["paper"]["fill_model"],
    )
    # Journals append -- start clean each run so results aren't mixed with a
    # previous backtest's output.
    signals_path = f"logs/backtest_signals{args.tag}.jsonl"
    trades_path = f"reports/backtest_trades{args.tag}.jsonl"
    for path in (signals_path, trades_path):
        if os.path.exists(path):
            os.remove(path)
    signal_journal = SignalJournal(signals_path)
    trade_journal = TradeJournal(trades_path)
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
