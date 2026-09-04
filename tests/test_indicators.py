import pytest

from data.indicators import (
    atr,
    atr_percent,
    bid_ask_spread_pct,
    ema,
    is_fresh_intraday_low,
    opening_range,
    recent_swing_high,
    recent_swing_low,
    relative_volume,
    session_vwap,
)

from factories import bar


def test_session_vwap_basic():
    bars = [bar(0, 10, 10, 10, 10, 100), bar(5, 20, 20, 20, 20, 300)]
    # typical price == close for these flat bars: (10*100 + 20*300) / 400 = 17.5
    assert session_vwap(bars) == pytest.approx(17.5)


def test_session_vwap_empty():
    assert session_vwap([]) is None


def test_ema_matches_known_value():
    # EMA(3) of [1,2,3,4,5]: seed=SMA(1,2,3)=2.0, k=0.5
    # -> 4*0.5+2.0*0.5=3.0 -> 5*0.5+3.0*0.5=4.0
    values = [1, 2, 3, 4, 5]
    result = ema(values, 3)
    assert result == pytest.approx(4.0)


def test_ema_insufficient_data_returns_none():
    assert ema([1, 2], 3) is None


def test_atr_wilder():
    # 16 bars of constant true range 1.0 -> ATR should converge to 1.0
    bars = [bar(i * 5, 10, 10.5, 9.5, 10.0, 1000) for i in range(16)]
    value = atr(bars, period=14)
    assert value == pytest.approx(1.0, rel=1e-6)


def test_atr_insufficient_bars_returns_none():
    bars = [bar(i * 5, 10, 10.5, 9.5, 10.0, 1000) for i in range(5)]
    assert atr(bars, period=14) is None


def test_atr_percent():
    assert atr_percent(0.5, 10.0) == pytest.approx(5.0)
    assert atr_percent(None, 10.0) is None


def test_relative_volume():
    assert relative_volume(150, 100) == pytest.approx(1.5)
    assert relative_volume(100, 0) is None


def test_bid_ask_spread_pct():
    assert bid_ask_spread_pct(9.99, 10.01) == pytest.approx(0.2)
    assert bid_ask_spread_pct(0, 10.0) is None


def test_opening_range():
    bars = [bar(0, 10, 10.2, 9.9, 10.1, 100), bar(5, 10.1, 10.3, 9.95, 10.2, 100)]
    high, low = opening_range(bars)
    assert high == pytest.approx(10.3)
    assert low == pytest.approx(9.9)


def test_opening_range_empty():
    assert opening_range([]) is None


def test_recent_swing_low_high():
    bars = [bar(i * 5, 10, 10 + i * 0.1, 9.5 - i * 0.05, 10, 100) for i in range(6)]
    swing_low = recent_swing_low(bars, lookback=5)
    swing_high = recent_swing_high(bars, lookback=5)
    # excludes the last (still forming) bar
    assert swing_low == min(b.low for b in bars[:-1])
    assert swing_high == max(b.high for b in bars[:-1])


def test_is_fresh_intraday_low_true():
    bars = [bar(0, 10, 10, 9.5, 10, 100), bar(5, 10, 10, 9.4, 10, 100)]
    assert is_fresh_intraday_low(bars) is True


def test_is_fresh_intraday_low_false():
    bars = [bar(0, 10, 10, 9.0, 10, 100), bar(5, 10, 10, 9.5, 10, 100)]
    assert is_fresh_intraday_low(bars) is False
