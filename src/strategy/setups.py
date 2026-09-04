"""Basic eligibility (spec section 8), the "do not chase" rule (spec section 9), and
the two preferred entry setups (spec section 10): opening-range breakout + pullback +
continuation, and VWAP reclaim."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from data.indicators import IndicatorSnapshot, vwap_series
from models.bar import Bar


@dataclass
class EligibilityResult:
    eligible: bool
    reason: Optional[str] = None


def basic_eligibility(
    snapshot: IndicatorSnapshot,
    max_spread_pct: float,
    min_relative_volume: float,
    min_liquidity_volume: float = 0.0,
) -> EligibilityResult:
    if snapshot.vwap is None or snapshot.ema_9 is None:
        return EligibilityResult(False, "REJECTED_DATA_QUALITY")

    if not (snapshot.last_price > snapshot.vwap):
        return EligibilityResult(False, "REJECTED_BELOW_VWAP")

    if not (snapshot.last_price > snapshot.ema_9):
        return EligibilityResult(False, "REJECTED_EMA_ALIGNMENT")

    if snapshot.relative_volume is None or snapshot.relative_volume < min_relative_volume:
        return EligibilityResult(False, "REJECTED_LOW_VOLUME")

    spread_pct = snapshot.spread_pct
    if spread_pct is None or spread_pct > max_spread_pct:
        return EligibilityResult(False, "REJECTED_WIDE_SPREAD")

    if min_liquidity_volume and snapshot.volume < min_liquidity_volume:
        return EligibilityResult(False, "REJECTED_LOW_VOLUME")

    return EligibilityResult(True, None)


@dataclass
class ChaseResult:
    overextended: bool
    reason: Optional[str] = None


def is_overextended(
    bars: Sequence[Bar],
    snapshot: IndicatorSnapshot,
    max_consecutive_large_green_candles: int = 3,
    large_candle_body_atr_multiple: float = 1.0,
    max_atr_above_vwap: float = 1.0,
    max_pct_above_open_without_consolidation: float = 5.0,
) -> ChaseResult:
    if snapshot.atr is None or snapshot.vwap is None or not bars:
        return ChaseResult(False, None)

    # Rule 1: N consecutive unusually large green candles with no consolidation bar.
    if len(bars) >= max_consecutive_large_green_candles:
        tail = bars[-max_consecutive_large_green_candles:]
        large_threshold = large_candle_body_atr_multiple * snapshot.atr
        if all(b.is_green and b.body >= large_threshold for b in tail):
            return ChaseResult(True, "consecutive_large_green_candles")

    # Rule 2: price more than ~1 ATR above VWAP.
    if snapshot.last_price - snapshot.vwap > max_atr_above_vwap * snapshot.atr:
        return ChaseResult(True, "extended_above_vwap")

    # Rule 3: price already >5% above the opening price without a consolidation
    # structure (approximated as: no bar in the session so far had a range materially
    # tighter than the average range, i.e. no pause has occurred).
    session_open = bars[0].open
    if session_open > 0:
        pct_above_open = (snapshot.last_price - session_open) / session_open * 100.0
        if pct_above_open > max_pct_above_open_without_consolidation:
            avg_range = sum(b.range for b in bars) / len(bars)
            has_consolidation = any(b.range < avg_range * 0.6 for b in bars)
            if not has_consolidation:
                return ChaseResult(True, "extended_above_open_no_consolidation")

    return ChaseResult(False, None)


@dataclass
class SetupResult:
    matched: bool
    setup_type: Optional[str] = None
    entry_price: Optional[float] = None
    structure_stop_reference: Optional[float] = None
    reason: Optional[str] = None


def detect_opening_range_breakout_pullback(
    bars: Sequence[Bar],
    opening_range_high: Optional[float],
    opening_range_bar_count: int,
    pullback_max_atr_from_breakout: float,
    atr_value: Optional[float],
    ema_9: Optional[float],
    vwap_value: Optional[float],
) -> SetupResult:
    if opening_range_high is None or atr_value is None or len(bars) <= opening_range_bar_count + 2:
        return SetupResult(False, reason="insufficient_bars")

    post_range_bars = bars[opening_range_bar_count:]

    # 1-2-3: find the breakout bar (first close above the opening-range high).
    breakout_index = None
    for i, b in enumerate(post_range_bars):
        if b.close > opening_range_high:
            breakout_index = i
            break
    if breakout_index is None or breakout_index >= len(post_range_bars) - 2:
        return SetupResult(False, reason="no_breakout_yet")

    after_breakout = post_range_bars[breakout_index + 1:]

    # 4-5: a pullback bar whose low holds above VWAP / 9EMA / breakout level, within
    # pullback_max_atr_from_breakout ATRs of the breakout level.
    support_level = max(v for v in (ema_9, vwap_value, opening_range_high) if v is not None)
    pullback_index = None
    for i, b in enumerate(after_breakout):
        pulled_back = b.close < post_range_bars[breakout_index].close
        holds_support = b.low >= support_level - pullback_max_atr_from_breakout * atr_value
        if pulled_back and holds_support:
            pullback_index = i
        elif pullback_index is not None:
            break
    if pullback_index is None:
        return SetupResult(False, reason="no_valid_pullback")

    remaining = after_breakout[pullback_index + 1:]
    if len(remaining) < 2:
        return SetupResult(False, reason="awaiting_higher_low_and_confirmation")

    pullback_bar = after_breakout[pullback_index]

    # 6: higher low.
    higher_low_bar = None
    for b in remaining:
        if b.low > pullback_bar.low:
            higher_low_bar = b
            break
    if higher_low_bar is None:
        return SetupResult(False, reason="no_higher_low")

    # 7: bullish confirmation candle closing above the higher-low bar's high.
    confirmation_bar = bars[-1]
    if not (confirmation_bar.is_green and confirmation_bar.close > higher_low_bar.high):
        return SetupResult(False, reason="no_confirmation_candle")

    return SetupResult(
        True,
        setup_type="ORB_PULLBACK_CONTINUATION",
        entry_price=confirmation_bar.close,
        structure_stop_reference=pullback_bar.low,
    )


def detect_vwap_reclaim(
    bars: Sequence[Bar],
    lookback_bars: int,
) -> SetupResult:
    if len(bars) < lookback_bars + 2:
        return SetupResult(False, reason="insufficient_bars")

    window = bars[-lookback_bars:]
    vwaps = vwap_series(bars)[-lookback_bars:]

    # 1: was trading at/below VWAP earlier in the window.
    below_vwap_index = None
    for i, (b, vw) in enumerate(zip(window[:-2], vwaps[:-2])):
        if vw is not None and b.close <= vw:
            below_vwap_index = i
    if below_vwap_index is None:
        return SetupResult(False, reason="never_below_vwap")

    # 2: reclaims VWAP with increasing volume.
    reclaim_index = None
    for i in range(below_vwap_index + 1, len(window) - 1):
        b, vw = window[i], vwaps[i]
        prev = window[i - 1]
        if vw is not None and b.close > vw and b.volume > prev.volume:
            reclaim_index = i
            break
    if reclaim_index is None:
        return SetupResult(False, reason="no_reclaim")

    # 3: VWAP holds on retest (subsequent bar's low stays at/above VWAP).
    retest_bar = window[reclaim_index + 1]
    retest_vwap = vwaps[reclaim_index + 1]
    if retest_vwap is None or retest_bar.low < retest_vwap:
        return SetupResult(False, reason="vwap_failed_retest")

    # 5: bullish confirmation candle (the latest bar).
    confirmation_bar = bars[-1]
    if not confirmation_bar.is_green:
        return SetupResult(False, reason="no_confirmation_candle")

    return SetupResult(
        True,
        setup_type="VWAP_RECLAIM",
        entry_price=confirmation_bar.close,
        structure_stop_reference=retest_bar.low,
    )
