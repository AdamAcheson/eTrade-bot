"""Wiring + event loop for the mining stock trading bot.

PHASE 1/2 STATUS: this module wires the full pipeline (data -> strategy -> risk ->
execution -> positions -> reporting) together and is exercised by the test suite. By
default (broker.yaml: mode: paper) it runs against an in-memory data provider and the
simulated broker only. Running `python src/main.py` prints a status message and exits
rather than starting a live loop either way -- see `if __name__ == "__main__"` below.

`build_default_bot()` can also build a real (sandbox-only) E*TRADE-backed bot when
broker.yaml: mode is "sandbox" -- see broker/etrade.py and data/etrade_market_data.py.
That path makes real network calls to E*TRADE's sandbox API using credentials from
your local environment; it still cannot reach production (see the safety notes in
those two modules and in config_loader.py).

Safety invariants enforced here (spec section 37):
  * `config_loader.load_config()` raises unless broker.yaml mode is "paper" or
    "sandbox" -- "live"/production always raises.
  * The kill switch is checked every loop iteration before any new entry is allowed.
  * `build_default_bot()` only ever constructs PaperBrokerAdapter or
    ETradeBrokerAdapter(environment="sandbox") -- nothing else.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Dict, List, Optional

from broker.base import BrokerInterface, OrderStatus
from broker.etrade import ETradeBrokerAdapter
from broker.paper import PaperBrokerAdapter
from config_loader import AppConfig, load_config
from data.etrade_market_data import ETradeMarketDataProvider
from data.indicators import IndicatorSnapshot, compute_snapshot
from data.market_data import InMemoryMarketDataProvider, MarketDataProvider
from execution.order_manager import OrderManager
from models.bar import Bar
from models.signal import Decision
from models.trade import ExitReason
from positions.overnight import evaluate_overnight_eligibility, shares_to_hold_overnight
from positions.position_manager import PositionManager
from reporting.daily_report import generate_daily_report
from reporting.journal import SignalJournal, TradeJournal
from risk.position_sizing import calc_position_size
from risk.risk_manager import RiskManager
from strategy.signal_engine import EvaluationContext, evaluate_ticker


@dataclass
class ScheduleWindow:
    name: str
    start: dtime
    end: dtime
    allow_new_entries: bool
    min_score_key: Optional[str] = None


def _parse_hhmm(s: str) -> dtime:
    hh, mm = s.split(":")
    return dtime(int(hh), int(mm))


def build_schedule_windows(schedule_cfg: dict) -> List[ScheduleWindow]:
    return [
        ScheduleWindow(
            name=w["name"],
            start=_parse_hhmm(w["start"]),
            end=_parse_hhmm(w["end"]),
            allow_new_entries=w["allow_new_entries"],
            min_score_key=w.get("min_score_key"),
        )
        for w in schedule_cfg["windows"]
    ]


def _same_session_day(bar_timestamp: datetime, now: datetime) -> bool:
    if now.tzinfo is not None and bar_timestamp.tzinfo is not None:
        return bar_timestamp.astimezone(now.tzinfo).date() == now.date()
    return bar_timestamp.date() == now.date()


def _prior_session_close(bars: List[Bar], now: datetime) -> Optional[float]:
    """Close of the most recent bar NOT in today's session -- the benchmark's last
    settled price, used for trend confirmation that (unlike VWAP) doesn't reset every
    morning. None on a cold start with no prior-day bars yet."""
    prior_bars = [b for b in bars if not _same_session_day(b.timestamp, now)]
    return prior_bars[-1].close if prior_bars else None


def current_window(windows: List[ScheduleWindow], now: datetime) -> Optional[ScheduleWindow]:
    now_t = now.time()
    for w in windows:
        if w.start <= now_t < w.end:
            return w
    return None


def is_near_overnight_review(schedule_cfg: dict, now: datetime, tolerance_minutes: int = 1) -> bool:
    review_t = _parse_hhmm(schedule_cfg["overnight_review_time"])
    review_dt = now.replace(hour=review_t.hour, minute=review_t.minute, second=0, microsecond=0)
    return abs((now - review_dt).total_seconds()) <= tolerance_minutes * 60


class TradingBot:
    """Owns the wiring described in docs/ARCHITECTURE.md section 10. Every dependency
    is injected so tests can drive it with fakes/in-memory implementations."""

    def __init__(
        self,
        config: AppConfig,
        data_provider: MarketDataProvider,
        broker: BrokerInterface,
        signal_journal: SignalJournal,
        trade_journal: TradeJournal,
    ) -> None:
        self.config = config
        self.data_provider = data_provider
        self.broker = broker
        self.signal_journal = signal_journal
        self.trade_journal = trade_journal

        self.risk_manager = RiskManager(config.risk)
        self.position_manager = PositionManager(
            max_concurrent_positions=config.risk["behavior"]["max_concurrent_positions"],
            pyramiding=config.risk["behavior"]["pyramiding"],
        )
        self.order_manager = OrderManager(broker, self.position_manager)
        self.schedule_windows = build_schedule_windows(config.schedule)

    def _snapshot_for(self, ticker: str, now: datetime) -> Optional[IndicatorSnapshot]:
        state = self.data_provider.get_state(ticker)
        if not state.bars or state.quote is None:
            return None
        # Only today's session bars -- state.bars accumulates every bar ever pushed
        # for this ticker, with no day-boundary reset (by design, so other uses like
        # historical analysis keep full history). Without this filter, opening
        # range/VWAP/EMA/RVOL would silently blend yesterday's session into today's
        # once the bot (or a backtest) runs past a single day, which is exactly how
        # it's meant to run.
        today_bars = [b for b in state.bars if _same_session_day(b.timestamp, now)]
        if not today_bars:
            return None
        strat = self.config.strategy
        snapshot = compute_snapshot(
            today_bars,
            bid=state.quote.bid,
            ask=state.quote.ask,
            average_volume_baseline=state.average_volume_baseline,
            ema_fast_period=strat["indicators"]["ema_fast"],
            ema_slow_period=strat["indicators"]["ema_slow"],
            atr_period=strat["indicators"]["atr_period"],
            opening_range_bar_count=max(strat["setups"]["opening_range_minutes"] // 5, 1),
        )
        # Use the live quote's last trade price as "current price" rather than the
        # last CLOSED bar's close -- for a polling data provider (see
        # data/etrade_market_data.py) the latest bar can be up to one bar-interval
        # stale, but the quote itself is refreshed every poll.
        snapshot.last_price = state.quote.last
        return snapshot

    def evaluate_and_maybe_enter(self, ticker: str, now: datetime) -> None:
        ticker_cfg = self.config.tickers[ticker]
        window = current_window(self.schedule_windows, now)

        stock_snapshot = self._snapshot_for(ticker, now)
        bench_snapshot = self._snapshot_for(ticker_cfg.benchmark, now)
        stock_state = self.data_provider.get_state(ticker)
        bench_state = self.data_provider.get_state(ticker_cfg.benchmark)

        staleness_limit = self.config.risk["safety"]["data_staleness_limit_seconds"]
        data_stale = (
            stock_snapshot is None
            or bench_snapshot is None
            or self.data_provider.is_stale(ticker, staleness_limit, now)
            or self.data_provider.is_stale(ticker_cfg.benchmark, staleness_limit, now)
        )
        if data_stale:
            return

        min_score = self.config.strategy["scoring"]["minimum_entry_score"].get(
            window.min_score_key if window else "midday_window", 80
        )

        # Same-session filter as run_overnight_review below -- state.bars accumulates
        # every bar ever pushed with no day-boundary reset (see _snapshot_for), so
        # setup detection and the chase rule (which assume "bars" means "today's
        # session, in order") need this slice too, or they silently treat day one's
        # opening range/open as if it were today's.
        today_stock_bars = [b for b in stock_state.bars if _same_session_day(b.timestamp, now)]
        today_bench_bars = [b for b in bench_state.bars if _same_session_day(b.timestamp, now)]

        ctx = EvaluationContext(
            ticker=ticker,
            benchmark=ticker_cfg.benchmark,
            bars=today_stock_bars,
            benchmark_bars=today_bench_bars,
            benchmark_prior_close=_prior_session_close(bench_state.bars, now),
            snapshot=stock_snapshot,
            benchmark_snapshot=bench_snapshot,
            max_spread_pct=ticker_cfg.max_spread_pct,
            volatility_category=ticker_cfg.volatility_category,
            manual_only=ticker_cfg.manual_only,
            allow_new_entries=bool(window and window.allow_new_entries),
            minimum_entry_score=min_score,
            now=now,
            profit_target_pct=ticker_cfg.profit_target_pct,
        )
        signal = evaluate_ticker(ctx, self.config.strategy, self.config.risk)
        self.signal_journal.log(signal)

        if signal.decision != Decision.ENTRY_CANDIDATE:
            return

        account = self.broker.get_account()
        risk_check = self.risk_manager.can_open_new_position(
            ticker=ticker,
            account_equity=account.equity,
            has_open_position=self.position_manager.has_open_position(ticker),
            open_position_count=self.position_manager.open_position_count(),
            has_pending_order_for_ticker=self.position_manager.has_pending_order(ticker),
            spread_pct=stock_snapshot.spread_pct,
            max_spread_pct=ticker_cfg.max_spread_pct,
            data_is_stale=False,
            broker_connected=self.broker.is_connected(),
            kill_switch_active=self.config.kill_switch_active(),
            now=now,
        )
        if not risk_check.allowed:
            return

        sizing = calc_position_size(
            account_equity=account.equity,
            max_account_risk_per_trade=self.config.risk["account"]["max_account_risk_per_trade"],
            entry_price=signal.entry_price,
            stop_price=signal.stop,
            max_position_size_dollars=self.config.risk["sizing"]["max_position_size_dollars"],
            max_position_size_pct_equity=self.config.risk["sizing"]["max_position_size_pct_equity"],
        )
        if sizing.shares <= 0:
            return

        result = self.order_manager.submit_entry_order(
            ticker=ticker,
            current_snapshot=stock_snapshot,
            max_spread_pct=ticker_cfg.max_spread_pct,
            planned_entry_price=signal.entry_price,
            # Marketable at the current ask, not pinned to the theoretical signal
            # entry price -- a limit resting exactly at signal.entry_price would sit
            # below any real (or synthesized-in-backtest) ask and never fill, with
            # no pending-order reconciliation loop to catch it later. This is a
            # deliberate small amount of realistic slippage, not the "wait
            # indefinitely for a passive fill" behavior a limit order normally has.
            planned_limit_price=stock_snapshot.ask,
            shares=sizing.shares,
            benchmark_still_confirmed=True,
            risk_check_passed=True,
        )
        if not result.submitted:
            return

        # PaperBrokerAdapter only fills a resting order once it sees a quote
        # (process_quote) -- feed it the current one immediately, since the order
        # was just placed at (or inside) the current market. A real broker
        # (ETradeBrokerAdapter) fills asynchronously on its own; reconciling a
        # pending order against a later fill confirmation isn't implemented yet,
        # so on that path the ticker simply stays ORDER_PENDING until a future
        # enhancement adds status polling.
        if isinstance(self.broker, PaperBrokerAdapter):
            self.broker.process_quote(ticker, stock_snapshot.bid, stock_snapshot.ask)

        order = self.broker.get_order_status(result.order.order_id)
        if order.status == OrderStatus.FILLED:
            self.position_manager.open_position(
                ticker=ticker,
                benchmark=ticker_cfg.benchmark,
                entry_time=now,
                entry_price=order.avg_fill_price or order.limit_price,
                shares=order.filled_quantity,
                stop_price=signal.stop,
                target_price=signal.target,
                setup_type=signal.setup_type,
                setup_score=signal.setup_score,
            )

    def manage_open_positions(self, now: datetime) -> None:
        strat = self.config.strategy["trade_management"]
        for position in list(self.position_manager.open_positions()):
            snapshot = self._snapshot_for(position.ticker, now)
            if snapshot is None:
                continue
            action = self.position_manager.manage(
                position.ticker,
                current_price=snapshot.last_price,
                current_time=now,
                breakeven_trigger_r=strat["breakeven_trigger_r"],
                partial_exit_enabled=strat["partial_exit"]["enabled"],
                partial_exit_trigger_r=strat["partial_exit"]["trigger_r"],
                partial_exit_sell_fraction=strat["partial_exit"]["sell_fraction"],
            )
            if action.should_exit:
                self._submit_exit_and_simulate(position.ticker, position.shares, snapshot.bid)
                trade = self.position_manager.close_position(
                    position.ticker, now, action.exit_price, action.exit_reason
                )
                self.trade_journal.record(trade)
                self.risk_manager.record_trade_result(trade.net_profit or 0.0, now, position.ticker)

    def run_overnight_review(self, now: datetime) -> None:
        for position in list(self.position_manager.open_positions()):
            ticker_cfg = self.config.tickers[position.ticker]
            stock_state = self.data_provider.get_state(position.ticker)
            bench_state = self.data_provider.get_state(ticker_cfg.benchmark)
            stock_snapshot = self._snapshot_for(position.ticker, now)
            bench_snapshot = self._snapshot_for(ticker_cfg.benchmark, now)
            if stock_snapshot is None or bench_snapshot is None:
                self._exit_position(position.ticker, now, ExitReason.SYSTEM_SAFETY_EXIT)
                continue

            today_stock_bars = [b for b in stock_state.bars if _same_session_day(b.timestamp, now)]
            today_bench_bars = [b for b in bench_state.bars if _same_session_day(b.timestamp, now)]
            decision = evaluate_overnight_eligibility(
                position=position,
                stock_bars=today_stock_bars,
                stock_snapshot=stock_snapshot,
                benchmark_bars=today_bench_bars,
                benchmark_snapshot=bench_snapshot,
                overnight_category=ticker_cfg.overnight_category,
                overnight_position_multiplier=ticker_cfg.overnight_position_multiplier,
                max_spread_pct=ticker_cfg.max_spread_pct,
            )
            if not decision.eligible:
                self._exit_position(position.ticker, now, ExitReason.OVERNIGHT_REJECTED)
                continue

            hold_shares = shares_to_hold_overnight(position.shares, decision.size_multiplier)
            sell_shares = position.shares - hold_shares
            if sell_shares > 0:
                self._submit_exit_and_simulate(position.ticker, sell_shares, stock_snapshot.bid)
                position.apply_partial_exit(now, stock_snapshot.last_price, sell_shares, "overnight_size_reduction")
            position.overnight = True
            position.overnight_multiplier_applied = decision.size_multiplier

    def _exit_position(self, ticker: str, now: datetime, reason: ExitReason) -> None:
        snapshot = self._snapshot_for(ticker, now)
        position = self.position_manager.get_position(ticker)
        exit_price = snapshot.last_price if snapshot else position.current_stop
        if snapshot is not None:
            self._submit_exit_and_simulate(ticker, position.shares, snapshot.bid)
        trade = self.position_manager.close_position(ticker, now, exit_price, reason)
        self.trade_journal.record(trade)
        self.risk_manager.record_trade_result(trade.net_profit or 0.0, now, ticker)

    def _submit_exit_and_simulate(self, ticker: str, shares: int, limit_price: float) -> None:
        """Actually submit the SELL to the broker (spec section 32/17: exits must
        reach the broker, not just PositionManager's bookkeeping). PaperBrokerAdapter
        only fills resting orders when it next sees a quote (`process_quote`), so for
        the simulated broker we feed it the current quote immediately -- an exit is
        meant to happen now, not wait for the next unrelated poll to fill it."""
        self.order_manager.submit_exit_order(ticker, shares, limit_price)
        if isinstance(self.broker, PaperBrokerAdapter):
            state = self.data_provider.get_state(ticker)
            if state.quote is not None:
                self.broker.process_quote(ticker, state.quote.bid, state.quote.ask)

    def run_cycle(self, now: datetime) -> None:
        """One pass over the approved universe -- see docs/ARCHITECTURE.md section 10
        for the full loop this is nested inside."""
        if self.config.kill_switch_active():
            return

        if is_near_overnight_review(self.config.schedule, now):
            self.run_overnight_review(now)
            return

        for ticker in self.config.auto_tradeable_universe():
            if self.position_manager.has_open_position(ticker):
                self.manage_open_positions(now)
            else:
                self.evaluate_and_maybe_enter(ticker, now)


def _parse_timeframe_seconds(timeframe: str) -> int:
    unit = timeframe[-1]
    value = int(timeframe[:-1])
    if unit == "s":
        return value
    if unit == "m":
        return value * 60
    if unit == "h":
        return value * 3600
    raise ValueError(f"unrecognized candle_timeframe: {timeframe!r}")


def build_default_bot(config: Optional[AppConfig] = None) -> TradingBot:
    """Builds a bot per broker.yaml: mode (which broker executes orders) and
    market_data_source (where quotes/bars come from) -- these are independent
    settings. "paper" mode always uses PaperBrokerAdapter for orders/fills/P&L.
    "sandbox" mode uses ETradeBrokerAdapter for real (sandbox) order placement.
    market_data_source: "etrade" polls E*TRADE's real quote endpoint regardless of
    which broker is active -- e.g. mode: paper + market_data_source: etrade watches
    the strategy evaluate real sandbox quotes while keeping fills/P&L in the local
    simulator, since E*TRADE's sandbox doesn't realistically track order/position
    state itself (see docs/ARCHITECTURE.md section 11). Nothing here can reach
    production -- config_loader.load_config already enforces that independently for
    both settings."""
    config = config or load_config()
    mode = config.broker["mode"]
    market_data_source = config.broker.get("market_data_source", "memory")

    if mode == "sandbox":
        broker: BrokerInterface = ETradeBrokerAdapter(config.broker)
        quote_source = broker
    else:
        broker = PaperBrokerAdapter(
            starting_equity=config.broker["paper"]["starting_equity"],
            fill_model=config.broker["paper"]["fill_model"],
        )
        quote_source = None

    if market_data_source == "etrade":
        if quote_source is None:
            quote_source = ETradeBrokerAdapter(config.broker)
        bar_seconds = _parse_timeframe_seconds(config.strategy["candle_timeframe"])
        data_provider: MarketDataProvider = ETradeMarketDataProvider(quote_source, bar_interval_seconds=bar_seconds)
    else:
        data_provider = InMemoryMarketDataProvider()

    signal_journal = SignalJournal("logs/signals.jsonl")
    trade_journal = TradeJournal("reports/trades.jsonl")
    return TradingBot(config, data_provider, broker, signal_journal, trade_journal)


if __name__ == "__main__":
    print(
        "Phase 1/2 wiring is implemented (see TradingBot in src/main.py), but this "
        "entry point does not start a live loop by design (spec sections 36-37).\n"
        "Run the test suite instead: pytest tests/\n\n"
        "To exercise the pipeline against the fully simulated broker, feed "
        "InMemoryMarketDataProvider bars/quotes yourself (see tests/) and call "
        "TradingBot.run_cycle(now) in a loop.\n\n"
        "To connect to E*TRADE's SANDBOX (real network calls, fake sandbox money): "
        "set broker.yaml: mode: sandbox, run scripts/etrade_authorize.py once "
        "locally to get an access token, put all 5 ETRADE_SANDBOX_* credentials in "
        "your local .env, then run scripts/etrade_sandbox_check.py (read-only) "
        "before trusting build_default_bot()'s sandbox path for anything more."
    )
