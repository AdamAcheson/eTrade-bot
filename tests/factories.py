"""Shared test data builders."""
from datetime import datetime, timedelta

from data.indicators import IndicatorSnapshot
from models.bar import Bar

SESSION_START = datetime(2026, 3, 2, 9, 30)


def bar(minute_offset: int, o: float, h: float, l: float, c: float, v: float) -> Bar:
    return Bar(
        timestamp=SESSION_START + timedelta(minutes=minute_offset),
        open=o, high=h, low=l, close=c, volume=v,
    )


def base_stock_bars():
    """6 x 5-minute bars: opening range, ORB breakout, pullback, higher low,
    bullish confirmation. See docs/ARCHITECTURE.md / test_signals.py comments for the
    worked-out numbers behind this sequence."""
    return [
        bar(0, 10.00, 10.10, 9.95, 10.05, 100_000),   # opening range bar 1
        bar(5, 10.05, 10.12, 10.00, 10.08, 90_000),   # opening range bar 2 (ORH=10.12)
        bar(10, 10.08, 10.30, 10.07, 10.25, 200_000),  # breakout bar
        bar(15, 10.25, 10.26, 10.14, 10.18, 80_000),   # pullback bar (low=10.14)
        bar(20, 10.18, 10.32, 10.16, 10.28, 85_000),   # higher-low bar
        bar(25, 10.28, 10.45, 10.27, 10.40, 95_000),   # bullish confirmation bar
    ]


def base_stock_snapshot(**overrides) -> IndicatorSnapshot:
    defaults = dict(
        last_price=10.40,
        bid=10.39,
        ask=10.40,
        vwap=10.28,
        ema_9=10.20,
        ema_20=10.05,
        atr=0.15,
        atr_percent=1.44,
        volume=650_000,
        relative_volume=1.5,
        opening_range_high=10.12,
        opening_range_low=9.95,
        swing_low=9.95,
        swing_high=10.32,
    )
    defaults.update(overrides)
    return IndicatorSnapshot(**defaults)


def base_bench_bars():
    return [
        bar(0, 50.00, 50.30, 49.80, 50.10, 100_000),
        bar(25, 50.10, 50.60, 50.00, 50.50, 120_000),
    ]


def base_bench_snapshot(**overrides) -> IndicatorSnapshot:
    defaults = dict(
        last_price=50.5,
        bid=50.49,
        ask=50.50,
        vwap=50.0,
        ema_9=50.2,
        ema_20=50.0,
        atr=0.5,
        atr_percent=0.99,
        volume=220_000,
        relative_volume=1.3,
        opening_range_high=50.30,
        opening_range_low=49.80,
        swing_low=49.80,
        swing_high=50.30,
    )
    defaults.update(overrides)
    return IndicatorSnapshot(**defaults)
