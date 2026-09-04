"""Market data abstraction (spec section 33: MarketDataProvider must be separate from
StrategyEngine). Phase 1 ships an in-memory provider fed by bars/quotes the caller
supplies (tests, replay/backtest, or a future adapter) -- there is no network call to a
real vendor here yet. A real provider (e.g. wrapping E*TRADE market data or another
vendor) would implement the same ABC and be a drop-in replacement."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from models.bar import Bar


@dataclass
class Quote:
    bid: float
    ask: float
    last: float
    timestamp: datetime


@dataclass
class TickerMarketState:
    """Rolling state for one symbol: bars for the current session plus the latest
    quote. `received_at` (wall-clock) drives staleness checks independent of the
    market timestamp on the data itself."""

    ticker: str
    bars: List[Bar] = field(default_factory=list)
    quote: Optional[Quote] = None
    average_volume_baseline: float = 0.0
    received_at: Optional[datetime] = None

    def latest_bar(self) -> Optional[Bar]:
        return self.bars[-1] if self.bars else None


class MarketDataProvider(ABC):
    @abstractmethod
    def get_state(self, ticker: str) -> TickerMarketState:
        ...

    @abstractmethod
    def is_stale(self, ticker: str, staleness_limit_seconds: float, now: Optional[datetime] = None) -> bool:
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        ...


class InMemoryMarketDataProvider(MarketDataProvider):
    """Feed bars/quotes explicitly (tests, replay of historical data, or a future
    websocket/poll adapter translating vendor messages into these same calls)."""

    def __init__(self) -> None:
        self._states: Dict[str, TickerMarketState] = {}
        self._connected = True

    def _state_for(self, ticker: str) -> TickerMarketState:
        return self._states.setdefault(ticker, TickerMarketState(ticker=ticker))

    def push_bar(self, ticker: str, bar: Bar, received_at: Optional[datetime] = None) -> None:
        state = self._state_for(ticker)
        state.bars.append(bar)
        state.received_at = received_at or datetime.utcnow()

    def push_quote(self, ticker: str, quote: Quote, received_at: Optional[datetime] = None) -> None:
        state = self._state_for(ticker)
        state.quote = quote
        state.received_at = received_at or datetime.utcnow()

    def set_average_volume_baseline(self, ticker: str, baseline: float) -> None:
        self._state_for(ticker).average_volume_baseline = baseline

    def get_state(self, ticker: str) -> TickerMarketState:
        return self._state_for(ticker)

    def is_stale(self, ticker: str, staleness_limit_seconds: float, now: Optional[datetime] = None) -> bool:
        state = self._states.get(ticker)
        if state is None or state.received_at is None:
            return True
        now = now or datetime.utcnow()
        return (now - state.received_at) > timedelta(seconds=staleness_limit_seconds)

    def set_connected(self, connected: bool) -> None:
        self._connected = connected

    def is_connected(self) -> bool:
        return self._connected
