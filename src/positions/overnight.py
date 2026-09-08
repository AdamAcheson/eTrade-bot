"""Overnight holding evaluation (spec sections 18-21). Run at ~3:50pm for every open
position: the trade thesis must still be valid, not just "target not yet reached"."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from data.indicators import IndicatorSnapshot
from models.bar import Bar
from models.position import Position


@dataclass
class OvernightDecision:
    eligible: bool
    reasons: List[str] = field(default_factory=list)
    size_multiplier: float = 1.0


def _closed_near_upper_range(bars, threshold_fraction: float = 0.6) -> bool:
    if not bars:
        return False
    day_high = max(b.high for b in bars)
    day_low = min(b.low for b in bars)
    day_range = day_high - day_low
    if day_range <= 0:
        return True
    last_close = bars[-1].close
    position_in_range = (last_close - day_low) / day_range
    return position_in_range >= threshold_fraction


def _abnormal_selling_pressure(bars, lookback: int = 3) -> bool:
    if len(bars) < lookback:
        return False
    tail = bars[-lookback:]
    return all(not b.is_green for b in tail)


def evaluate_overnight_eligibility(
    position: Position,
    stock_bars: List[Bar],
    stock_snapshot: IndicatorSnapshot,
    benchmark_bars: List[Bar],
    benchmark_snapshot: IndicatorSnapshot,
    overnight_category: str,
    overnight_position_multiplier: float,
    max_spread_pct: float,
    has_earnings_before_next_session: bool = False,
    has_known_binary_event: bool = False,
    require_at_or_above_entry: bool = True,
) -> OvernightDecision:
    """require_at_or_above_entry=False lets a currently-red position still be held
    overnight if every other thesis check (benchmark trend, structure, volume,
    selling pressure) passes -- for use only alongside a stop/sizing regime that
    already bounds the dollar loss if the thesis is wrong (e.g. a wider stop with
    max_risk_dollars_per_trade), never as a bare override on the default sizing."""
    if overnight_category == "manual_only":
        return OvernightDecision(False, ["MANUAL_ONLY securities are never held automatically"], 0.0)

    reasons: List[str] = []

    if position.shares <= 0:
        reasons.append("position_not_near_or_above_entry")
    elif require_at_or_above_entry and stock_snapshot.last_price < position.entry_price * 0.995:
        reasons.append("position_not_near_or_above_entry")

    if stock_snapshot.vwap is None or not (stock_snapshot.last_price > stock_snapshot.vwap):
        reasons.append("stock_below_vwap")

    if stock_snapshot.ema_20 is None or not (stock_snapshot.last_price > stock_snapshot.ema_20):
        reasons.append("stock_below_trend_support")

    if benchmark_snapshot.vwap is None or not (benchmark_snapshot.last_price > benchmark_snapshot.vwap):
        reasons.append("benchmark_below_vwap")

    if (
        benchmark_snapshot.ema_9 is None
        or benchmark_snapshot.ema_20 is None
        or not (benchmark_snapshot.ema_9 >= benchmark_snapshot.ema_20)
    ):
        reasons.append("benchmark_trend_not_bullish")

    if not _closed_near_upper_range(stock_bars):
        reasons.append("not_closing_near_upper_range")

    if stock_snapshot.relative_volume is None or stock_snapshot.relative_volume < 1.0:
        reasons.append("late_day_volume_not_supportive")

    if _abnormal_selling_pressure(stock_bars):
        reasons.append("abnormal_selling_pressure")

    if has_earnings_before_next_session:
        reasons.append("earnings_before_next_session")

    if has_known_binary_event:
        reasons.append("known_binary_corporate_event")

    spread_pct = stock_snapshot.spread_pct
    if spread_pct is None or spread_pct > max_spread_pct:
        reasons.append("spread_liquidity_deteriorated")

    eligible = len(reasons) == 0
    size_multiplier = 1.0
    if eligible and overnight_category == "conditional":
        size_multiplier = overnight_position_multiplier

    return OvernightDecision(eligible=eligible, reasons=reasons, size_multiplier=size_multiplier)


def shares_to_hold_overnight(current_shares: int, size_multiplier: float) -> int:
    """spec section 20: e.g. 200 shares -> multiplier 0.50 -> hold 100, sell 100."""
    import math

    return math.floor(current_shares * size_multiplier)
