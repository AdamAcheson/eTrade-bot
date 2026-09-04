"""MarketDataProvider backed by E*TRADE quote polling.

Important limitation to know before relying on this: E*TRADE's public API has no
historical/intraday OHLCV bar endpoint at all (sandbox or production) -- only a
real-time quote endpoint (`/v1/market/quote/{symbol}`). There is no documented way to
ask E*TRADE for "the last N 5-minute bars." So instead of pretending otherwise, this
provider polls the quote endpoint on an interval (spec section 6: continuous
polling, not a fixed 15/30-minute schedule) and aggregates ticks into 5-minute bars
itself, using `totalVolume` (E*TRADE's cumulative session volume) deltas for each
bar's volume.

Consequences worth knowing:
  * There is no pre-market/opening-range history before this provider starts
    polling -- the bot needs to be running (or backfilled from a real data vendor)
    before 9:30am to have opening-range data available at 9:40am.
  * The current partial (still-forming) bar is intentionally not exposed via
    `get_state().bars` -- only closed bars are, matching how the rest of Phase 1
    consumes bar history. `TradingBot._snapshot_for` reads `state.quote.last` as
    the current price rather than the last closed bar's close, to avoid acting on
    up-to-5-minute-stale prices.
  * A production-grade version of this bot should replace quote polling with a
    real historical-bars data vendor for backfill/indicator warm-up, and keep
    E*TRADE purely for execution + live quotes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

from broker.etrade import ETradeBrokerAdapter
from data.market_data import InMemoryMarketDataProvider, MarketDataProvider, Quote, TickerMarketState
from models.bar import Bar


@dataclass
class _FormingBar:
    bar_start: datetime
    open: float
    high: float
    low: float
    close: float
    volume_at_bar_open: float
    last_cumulative_volume: float


class ETradeMarketDataProvider(MarketDataProvider):
    def __init__(self, adapter: ETradeBrokerAdapter, bar_interval_seconds: int = 300) -> None:
        self._adapter = adapter
        self._inner = InMemoryMarketDataProvider()
        self._forming: Dict[str, _FormingBar] = {}
        self.bar_interval_seconds = bar_interval_seconds

    def _bar_start(self, now: datetime) -> datetime:
        epoch_seconds = int(now.timestamp())
        floored = epoch_seconds - (epoch_seconds % self.bar_interval_seconds)
        return datetime.fromtimestamp(floored, tz=now.tzinfo)

    def poll(self, ticker: str, now: Optional[datetime] = None) -> None:
        """Fetch one quote for `ticker` and fold it into the in-progress bar. Call
        this on a short interval (a handful of seconds) for every ticker in the
        universe during eligible trading periods -- this IS the event loop's
        "continuously update" data step for a real E*TRADE-backed run."""
        now = now or datetime.utcnow()
        quote_data = self._adapter.get_quote(ticker)

        self._inner.push_quote(
            ticker,
            Quote(bid=quote_data["bid"], ask=quote_data["ask"], last=quote_data["last"], timestamp=now),
            received_at=now,
        )
        self._fold_into_bar(ticker, quote_data, now)

    def _fold_into_bar(self, ticker: str, quote_data: dict, now: datetime) -> None:
        bar_start = self._bar_start(now)
        last_price = quote_data["last"]
        cumulative_volume = quote_data["volume"]

        forming = self._forming.get(ticker)
        if forming is None or forming.bar_start != bar_start:
            if forming is not None:
                self._close_bar(ticker, forming)
            self._forming[ticker] = _FormingBar(
                bar_start=bar_start,
                open=last_price,
                high=last_price,
                low=last_price,
                close=last_price,
                volume_at_bar_open=cumulative_volume,
                last_cumulative_volume=cumulative_volume,
            )
        else:
            forming.high = max(forming.high, last_price)
            forming.low = min(forming.low, last_price)
            forming.close = last_price
            forming.last_cumulative_volume = cumulative_volume

    def _close_bar(self, ticker: str, forming: _FormingBar) -> None:
        bar_volume = max(forming.last_cumulative_volume - forming.volume_at_bar_open, 0.0)
        bar = Bar(
            timestamp=forming.bar_start,
            open=forming.open,
            high=forming.high,
            low=forming.low,
            close=forming.close,
            volume=bar_volume,
        )
        self._inner.push_bar(ticker, bar)

    def flush_forming_bar(self, ticker: str) -> None:
        """Force-close whatever bar is currently in progress. Useful at end-of-day
        so the last partial bar isn't silently dropped."""
        forming = self._forming.pop(ticker, None)
        if forming is not None:
            self._close_bar(ticker, forming)

    def set_average_volume_baseline(self, ticker: str, baseline: float) -> None:
        self._inner.set_average_volume_baseline(ticker, baseline)

    # --- MarketDataProvider ---------------------------------------------------
    def get_state(self, ticker: str) -> TickerMarketState:
        return self._inner.get_state(ticker)

    def is_stale(self, ticker: str, staleness_limit_seconds: float, now: Optional[datetime] = None) -> bool:
        return self._inner.is_stale(ticker, staleness_limit_seconds, now)

    def is_connected(self) -> bool:
        return self._adapter.is_connected()
