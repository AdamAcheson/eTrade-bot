"""Pure-Python indicator calculations. No third-party numeric dependency is required
so these run anywhere Python + PyYAML run. All functions operate on a list of
models.bar.Bar (oldest first) or plain floats, and are used both in batch (tests,
backtests) and incrementally (IndicatorState, for continuous intraday updates)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from models.bar import Bar


def session_vwap(bars: Sequence[Bar]) -> Optional[float]:
    """Volume-weighted average price across the given bars (typically all bars since
    the session open -- callers are responsible for slicing to the current session)."""
    if not bars:
        return None
    total_pv = sum(b.typical_price * b.volume for b in bars)
    total_v = sum(b.volume for b in bars)
    if total_v == 0:
        return None
    return total_pv / total_v


def ema_series(values: Sequence[float], period: int) -> List[float]:
    """Standard EMA, seeded with a SMA of the first `period` values."""
    if len(values) < period:
        raise ValueError(f"need at least {period} values to seed EMA({period})")
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out = [seed]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def ema(values: Sequence[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return ema_series(values, period)[-1]


def ema_update(previous_ema: float, new_value: float, period: int) -> float:
    """Incremental EMA update for streaming use (spec section 6: continuously update)."""
    k = 2.0 / (period + 1)
    return new_value * k + previous_ema * (1 - k)


def true_range(current: Bar, previous_close: Optional[float]) -> float:
    if previous_close is None:
        return current.high - current.low
    return max(
        current.high - current.low,
        abs(current.high - previous_close),
        abs(current.low - previous_close),
    )


def atr(bars: Sequence[Bar], period: int = 14) -> Optional[float]:
    """Wilder's ATR. Needs at least period+1 bars (one extra for the first true range's
    previous close)."""
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        trs.append(true_range(bars[i], bars[i - 1].close))
    # Wilder smoothing: seed with simple average of first `period` TRs, then smooth.
    seed = sum(trs[:period]) / period
    value = seed
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def atr_percent(atr_value: Optional[float], current_price: float) -> Optional[float]:
    if atr_value is None or current_price <= 0:
        return None
    return atr_value / current_price * 100.0


def relative_volume(current_volume: float, average_volume: float) -> Optional[float]:
    if not average_volume:
        return None
    return current_volume / average_volume


def vwap_series(bars: Sequence[Bar]) -> List[Optional[float]]:
    """Cumulative session VWAP evaluated after each bar -- needed to detect
    reclaim/pullback-holds-vwap patterns across a sequence of bars, not just the
    latest snapshot value."""
    out: List[Optional[float]] = []
    cum_pv = 0.0
    cum_v = 0.0
    for b in bars:
        cum_pv += b.typical_price * b.volume
        cum_v += b.volume
        out.append(cum_pv / cum_v if cum_v else None)
    return out


def bid_ask_spread(bid: float, ask: float) -> float:
    return max(ask - bid, 0.0)


def bid_ask_spread_pct(bid: float, ask: float) -> Optional[float]:
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (ask + bid) / 2.0
    if mid == 0:
        return None
    return bid_ask_spread(bid, ask) / mid * 100.0


def opening_range(bars: Sequence[Bar]) -> Optional[tuple]:
    """High/low over the supplied bars, which should already be sliced to the
    observation window (e.g. 9:30-9:40, spec section 5)."""
    if not bars:
        return None
    return (max(b.high for b in bars), min(b.low for b in bars))


def recent_swing_low(bars: Sequence[Bar], lookback: int = 10) -> Optional[float]:
    """Simple swing low: lowest low over the last `lookback` bars, excluding the most
    recent (still-forming) bar."""
    window = bars[-(lookback + 1):-1] if len(bars) > 1 else []
    if not window:
        return None
    return min(b.low for b in window)


def recent_swing_high(bars: Sequence[Bar], lookback: int = 10) -> Optional[float]:
    window = bars[-(lookback + 1):-1] if len(bars) > 1 else []
    if not window:
        return None
    return max(b.high for b in window)


def is_fresh_intraday_low(bars: Sequence[Bar]) -> bool:
    """True if the most recent bar's low is the lowest low of the session so far."""
    if len(bars) < 2:
        return False
    *history, current = bars
    return current.low <= min(b.low for b in history)


@dataclass
class IndicatorSnapshot:
    """A point-in-time bundle of the indicators the strategy needs for one ticker,
    matching the "continuously update" list in spec section 6."""

    last_price: float
    bid: float
    ask: float
    vwap: Optional[float]
    ema_9: Optional[float]
    ema_20: Optional[float]
    atr: Optional[float]
    atr_percent: Optional[float]
    volume: float
    relative_volume: Optional[float]
    opening_range_high: Optional[float]
    opening_range_low: Optional[float]
    swing_low: Optional[float]
    swing_high: Optional[float]

    @property
    def spread(self) -> float:
        return bid_ask_spread(self.bid, self.ask)

    @property
    def spread_pct(self) -> Optional[float]:
        return bid_ask_spread_pct(self.bid, self.ask)


def compute_snapshot(
    bars: Sequence[Bar],
    bid: float,
    ask: float,
    average_volume_baseline: float,
    ema_fast_period: int = 9,
    ema_slow_period: int = 20,
    atr_period: int = 14,
    opening_range_bar_count: int = 2,
    swing_lookback: int = 10,
) -> IndicatorSnapshot:
    """Convenience batch computation from a full session's bars. Used by tests and by
    any offline/backtest driver; the live loop uses incremental updates instead but
    should produce equivalent values."""
    closes = [b.close for b in bars]
    last = bars[-1]
    cumulative_volume = sum(b.volume for b in bars)
    return IndicatorSnapshot(
        last_price=last.close,
        bid=bid,
        ask=ask,
        vwap=session_vwap(bars),
        ema_9=ema(closes, ema_fast_period),
        ema_20=ema(closes, ema_slow_period),
        atr=atr(bars, atr_period),
        atr_percent=atr_percent(atr(bars, atr_period), last.close),
        volume=cumulative_volume,
        relative_volume=relative_volume(cumulative_volume, average_volume_baseline),
        opening_range_high=opening_range(bars[:opening_range_bar_count])[0] if len(bars) >= opening_range_bar_count else None,
        opening_range_low=opening_range(bars[:opening_range_bar_count])[1] if len(bars) >= opening_range_bar_count else None,
        swing_low=recent_swing_low(bars, swing_lookback),
        swing_high=recent_swing_high(bars, swing_lookback),
    )
