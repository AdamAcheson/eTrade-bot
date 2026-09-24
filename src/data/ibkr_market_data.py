"""MarketDataProvider backed by Interactive Brokers (TWS / IB Gateway via ib_async).

Two streams per symbol, both opened once by subscribe():

  * 5-minute TRADES bars from reqHistoricalData(keepUpToDate=True): two sessions of
    history (the prior close feeds the gap calculation), then IBKR keeps the list
    current. The LAST bar in that list is the one still forming, so it is never
    exposed. The rest of the bot acts only on closed bars, as in the backtest.
  * A quote (bid / ask / last) from reqMktData.

Live or delayed: market data type 3 is requested, which IBKR answers with LIVE data
where the account has a subscription and 15-minute-DELAYED data where it does not.
`data_delayed()` reports which one actually arrived, and scripts/run_bot.py prints
it in its startup summary. Delayed data is only good for testing the plumbing.

If IBKR refuses the streaming bar request, the provider falls back to asking for
the bars again once per bar interval.

No quote is ever invented. A symbol with no valid bid and ask simply has no quote,
and the bot does not trade it.

ib_async only processes IBKR's messages while its event loop runs. Wait with
`wait(seconds)` (ib.sleep), never time.sleep, or nothing updates.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from data.market_data import InMemoryMarketDataProvider, MarketDataProvider, Quote, TickerMarketState
from models.bar import Bar

ET = ZoneInfo("America/New_York")
DELAYED_TYPES = (3, 4)


def _valid(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if not math.isnan(f) and f > 0 else None


def _as_et(d) -> datetime:
    if isinstance(d, datetime):
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(ET)
    # a date (daily bars) -- not expected for 5-minute bars, but never crash on it
    return datetime(d.year, d.month, d.day, tzinfo=ET)


def contract_resolver(ib) -> Callable[[str], object]:
    """Stock contracts on SMART routing, looked up once per symbol."""
    cache: Dict[str, object] = {}

    def resolve(symbol: str):
        if symbol not in cache:
            from ib_async import Stock
            found = ib.qualifyContracts(Stock(symbol, "SMART", "USD"))
            if not found:
                raise ValueError(f"IBKR does not recognise {symbol} (SMART, USD)")
            cache[symbol] = found[0]
        return cache[symbol]

    return resolve


class IBKRMarketDataProvider(MarketDataProvider):
    def __init__(self, ib, contract_for: Callable[[str], object], bar_interval_seconds: int = 300,
                 clock: Optional[Callable[[], datetime]] = None) -> None:
        self.ib = ib
        self._contract_for = contract_for
        self.bar_interval = timedelta(seconds=bar_interval_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._inner = InMemoryMarketDataProvider()
        self._bars: Dict[str, list] = {}
        self._tickers: Dict[str, object] = {}
        self._streaming: Dict[str, bool] = {}
        self._fetched_at: Dict[str, datetime] = {}
        self._bars_updated_at: Dict[str, datetime] = {}
        self.failed: Dict[str, str] = {}

    # --- setup -------------------------------------------------------------------
    def subscribe(self, symbols: List[str]) -> None:
        self.ib.reqMarketDataType(3)   # live where subscribed, delayed otherwise
        for symbol in symbols:
            try:
                self._subscribe(symbol)
            except Exception as e:  # noqa: BLE001 -- one bad symbol must not stop the rest
                self.failed[symbol] = f"{type(e).__name__}: {e}"

    def _request_bars(self, contract, keep_up_to_date: bool):
        return self.ib.reqHistoricalData(
            contract, endDateTime="", durationStr="2 D", barSizeSetting="5 mins",
            whatToShow="TRADES", useRTH=True, formatDate=2, keepUpToDate=keep_up_to_date)

    def _subscribe(self, symbol: str) -> None:
        contract = self._contract_for(symbol)
        self._tickers[symbol] = self.ib.reqMktData(contract, "", False, False)
        try:
            bars = self._request_bars(contract, keep_up_to_date=True)
        except Exception:  # noqa: BLE001 -- fall back to asking again each bar
            bars = None
        if bars:
            self._streaming[symbol] = True
            bars.updateEvent += lambda *_, s=symbol: self._bars_updated_at.__setitem__(s, self._clock())
        else:
            self._streaming[symbol] = False
            bars = self._request_bars(contract, keep_up_to_date=False)
        self._bars[symbol] = bars or []
        self._fetched_at[symbol] = self._bars_updated_at[symbol] = self._clock()

    def close(self) -> None:
        for symbol, bars in self._bars.items():
            try:
                if self._streaming.get(symbol):
                    self.ib.cancelHistoricalData(bars)
                self.ib.cancelMktData(self._contract_for(symbol))
            except Exception:  # noqa: BLE001 -- best effort on the way out
                pass

    # --- the loop's per-symbol step -----------------------------------------------
    def poll(self, symbol: str, now: Optional[datetime] = None) -> None:
        """Copy what IBKR has streamed for `symbol` into the state the bot reads.
        Makes no request, except in the fallback mode below, once per bar."""
        if symbol not in self._bars:
            return
        wall = self._clock()
        if not self._streaming[symbol] and wall - self._fetched_at[symbol] >= self.bar_interval:
            fresh = self._request_bars(self._contract_for(symbol), keep_up_to_date=False)
            if fresh:
                self._bars[symbol] = fresh
                self._bars_updated_at[symbol] = wall
            self._fetched_at[symbol] = wall

        state = self._inner.get_state(symbol)
        raw = list(self._bars[symbol])
        # The last bar is the one still forming, so drop it. Only closed bars go to
        # the strategy.
        state.bars = [Bar(timestamp=_as_et(b.date), open=float(b.open), high=float(b.high),
                          low=float(b.low), close=float(b.close), volume=float(b.volume))
                      for b in raw[:-1]]

        t = self._tickers[symbol]
        bid, ask, last = _valid(t.bid), _valid(t.ask), _valid(t.last)
        quote_time = getattr(t, "time", None)
        if bid and ask and ask >= bid:
            stamp = quote_time or wall
            state.quote = Quote(bid=bid, ask=ask, last=last or (bid + ask) / 2, timestamp=stamp)
        else:
            state.quote = None
        heard = [d for d in (quote_time, self._bars_updated_at.get(symbol)) if d is not None]
        state.received_at = max(heard) if heard else None

    def wait(self, seconds: float) -> None:
        self.ib.sleep(seconds)

    # --- reporting -------------------------------------------------------------------
    def data_delayed(self) -> Optional[bool]:
        """True if any symbol's quotes are arriving delayed, False if all are live,
        None if nothing has arrived yet to tell."""
        kinds = [getattr(t, "marketDataType", None) for t in self._tickers.values()]
        kinds = [k for k in kinds if k]
        if not kinds:
            return None
        return any(k in DELAYED_TYPES for k in kinds)

    def session_volumes(self, symbol: str) -> Dict[str, float]:
        """Total volume per session date (YYYY-MM-DD) in IBKR's bars. Used to check that
        IBKR's volume matches the cached history the RVOL baselines come from."""
        totals: Dict[str, float] = {}
        for b in self._bars.get(symbol, []):
            day = _as_et(b.date).strftime("%Y-%m-%d")
            totals[day] = totals.get(day, 0.0) + float(b.volume)
        return totals

    # --- MarketDataProvider ------------------------------------------------------------
    def set_average_volume_baseline(self, ticker: str, baseline: float) -> None:
        self._inner.set_average_volume_baseline(ticker, baseline)

    def get_state(self, ticker: str) -> TickerMarketState:
        return self._inner.get_state(ticker)

    def is_stale(self, ticker: str, staleness_limit_seconds: float, now: Optional[datetime] = None) -> bool:
        return self._inner.is_stale(ticker, staleness_limit_seconds, now or self._clock())

    def is_connected(self) -> bool:
        return bool(self.ib.isConnected())
