"""Backtest driver: replays historical bars through the exact same TradingBot
pipeline the live/paper bot uses (data -> signal engine -> risk -> order manager
-> PaperBrokerAdapter -> position manager), one 5-minute bar at a time, so a
backtest result reflects the real strategy code in src/strategy, src/risk, and
src/positions -- not a re-implementation of it.

Sessions are handled one calendar day at a time: VWAP, opening range, and the
session EMA/ATR windows are all defined relative to a single day's bars (see
data/indicators.py), so each day gets a fresh InMemoryMarketDataProvider and the
risk manager's daily counters are reset -- exactly like a real overnight restart.
PositionManager (and any overnight-held position) carries over between days
unchanged, since overnight holding is a first-class feature of the strategy
(positions/overnight.py).

Caveat that matters for interpreting results: there is no historical bid/ask
here, only OHLCV closes (see backtest/data_fetch.py). Quotes are synthesized as a
fixed percentage spread around each bar's close. This makes spread-based
rejections and fill prices an approximation, not a replay of real order-book
conditions.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from broker.paper import PaperBrokerAdapter
from config_loader import AppConfig
from data.market_data import InMemoryMarketDataProvider, Quote
from main import TradingBot
from models.bar import Bar
from models.signal import Signal
from models.trade import Trade
from reporting.journal import SignalJournal, TradeJournal

NY_TZ = ZoneInfo("America/New_York")


def _session_grid(day: date, interval_minutes: int = 5) -> List[datetime]:
    start = datetime.combine(day, dtime(9, 30), tzinfo=NY_TZ)
    end = datetime.combine(day, dtime(15, 55), tzinfo=NY_TZ)
    grid = []
    t = start
    while t <= end:
        grid.append(t)
        t += timedelta(minutes=interval_minutes)
    return grid


@dataclass
class DailyEquityPoint:
    day: date
    equity: float
    open_positions: int


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    signals: List[Signal] = field(default_factory=list)
    equity_curve: List[DailyEquityPoint] = field(default_factory=list)
    still_open_at_end: List[str] = field(default_factory=list)
    days_simulated: int = 0
    starting_equity: float = 0.0
    ending_equity: float = 0.0


class BacktestEngine:
    def __init__(
        self,
        config: AppConfig,
        bars_by_symbol: Dict[str, List[Bar]],
        volume_baseline_by_symbol: Dict[str, Dict[date, float]],
        synthetic_spread_pct: float = 0.05,
        min_spread_dollars: float = 0.01,
        signal_journal_path: str = "reports/backtest_signals.jsonl",
        trade_journal_path: str = "reports/backtest_trades.jsonl",
    ) -> None:
        self.config = config
        self.universe = config.auto_tradeable_universe()
        self.benchmarks = sorted({config.benchmark_of(t) for t in self.universe})
        self.all_symbols = sorted(set(self.universe) | set(self.benchmarks))

        missing = [s for s in self.all_symbols if s not in bars_by_symbol or not bars_by_symbol[s]]
        if missing:
            raise ValueError(f"no bars supplied for required symbols: {missing}")

        self.synthetic_spread_pct = synthetic_spread_pct
        self.min_spread_dollars = min_spread_dollars
        self.volume_baseline_by_symbol = volume_baseline_by_symbol

        # index bars per symbol by (date -> {timestamp -> Bar}) for O(1) lookup on
        # the fixed 5-minute grid each day.
        self._by_day: Dict[str, Dict[date, Dict[datetime, Bar]]] = {}
        for symbol, bars in bars_by_symbol.items():
            per_day: Dict[date, Dict[datetime, Bar]] = defaultdict(dict)
            for b in bars:
                per_day[b.timestamp.date()][b.timestamp] = b
            self._by_day[symbol] = per_day

        # trading days = any day at least one tradeable ticker (not just a
        # benchmark) actually has bars for.
        self.trading_days: List[date] = sorted(
            {d for t in self.universe for d in self._by_day.get(t, {}).keys()}
        )

        self._equity_curve: List[DailyEquityPoint] = []
        self.broker = PaperBrokerAdapter(starting_equity=config.broker["paper"]["starting_equity"])
        self.signal_journal = SignalJournal(signal_journal_path)
        self.trade_journal = TradeJournal(trade_journal_path)
        self.bot = TradingBot(
            config=config,
            data_provider=InMemoryMarketDataProvider(),
            broker=self.broker,
            signal_journal=self.signal_journal,
            trade_journal=self.trade_journal,
        )

    def _spread_for(self, close: float) -> float:
        return max(close * self.synthetic_spread_pct / 100.0 / 2.0, self.min_spread_dollars / 2.0)

    def _push_symbol_bar(self, provider: InMemoryMarketDataProvider, symbol: str, bar: Bar, now: datetime) -> None:
        provider.push_bar(symbol, bar, received_at=now)
        half_spread = self._spread_for(bar.close)
        provider.push_quote(
            symbol,
            Quote(bid=bar.close - half_spread, ask=bar.close + half_spread, last=bar.close, timestamp=now),
            received_at=now,
        )

    def run(self) -> BacktestResult:
        starting_equity = self.broker.get_account().equity

        for day in self.trading_days:
            provider = InMemoryMarketDataProvider()
            for symbol in self.all_symbols:
                baseline = self.volume_baseline_by_symbol.get(symbol, {}).get(day)
                if baseline:
                    provider.set_average_volume_baseline(symbol, baseline)
            self.bot.data_provider = provider
            self.bot.risk_manager.reset_daily_counters()

            for now in _session_grid(day):
                for symbol in self.all_symbols:
                    bar = self._by_day.get(symbol, {}).get(day, {}).get(now)
                    if bar is not None:
                        self._push_symbol_bar(provider, symbol, bar, now)
                self.bot.run_cycle(now)

            self._equity_curve.append(
                DailyEquityPoint(
                    day=day,
                    equity=self.broker.get_account().equity,
                    open_positions=self.bot.position_manager.open_position_count(),
                )
            )

        ending_equity = self.broker.get_account().equity
        still_open = [p.ticker for p in self.bot.position_manager.open_positions()]
        return BacktestResult(
            trades=self.trade_journal.trades,
            signals=self.signal_journal.signals,
            equity_curve=self._equity_curve,
            still_open_at_end=still_open,
            days_simulated=len(self.trading_days),
            starting_equity=starting_equity,
            ending_equity=ending_equity,
        )
