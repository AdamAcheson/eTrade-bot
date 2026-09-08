"""Stop calculation, R-based targets, and risk-based position sizing (spec sections
14, 15, 16). Position size adjusts to the stop distance -- the stop is never
tightened just to buy more shares."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


def atr_stop_price(entry_price: float, atr_value: float, atr_multiplier: float) -> float:
    return entry_price - atr_multiplier * atr_value


def structure_stop_price(swing_low: Optional[float]) -> Optional[float]:
    return swing_low


def final_stop_price(
    entry_price: float,
    atr_value: float,
    atr_multiplier: float,
    swing_low: Optional[float],
) -> float:
    """The final stop is the WIDER (lower) of the ATR-based stop and the
    structure-based stop, so a normal-sized pullback doesn't stop the trade out
    before the thesis is actually invalidated. Position size (not the stop) absorbs
    the resulting risk-per-share, per spec section 14's last line."""
    atr_stop = atr_stop_price(entry_price, atr_value, atr_multiplier)
    structure_stop = structure_stop_price(swing_low)
    if structure_stop is None:
        return atr_stop
    return min(atr_stop, structure_stop)


def atr_multiplier_for_category(volatility_category: str, multipliers: dict) -> float:
    mapping = {
        "low": multipliers["low_volatility_diversified"],
        "moderate": multipliers["normal"],
        "normal": multipliers["normal"],
        "high": multipliers["high_volatility_speculative"],
        "very_high": multipliers["high_volatility_speculative"],
    }
    return mapping.get(volatility_category, multipliers["normal"])


def risk_per_share(entry_price: float, stop_price: float) -> float:
    return entry_price - stop_price


def reward_per_share(entry_price: float, target_price: float) -> float:
    return target_price - entry_price


def r_multiple_target(entry_price: float, stop_price: float, r_multiple: float) -> float:
    return entry_price + r_multiple * risk_per_share(entry_price, stop_price)


def reward_risk_ratio(entry_price: float, stop_price: float, target_price: float) -> Optional[float]:
    risk = risk_per_share(entry_price, stop_price)
    if risk <= 0:
        return None
    return reward_per_share(entry_price, target_price) / risk


@dataclass
class PositionSizeResult:
    shares: int
    risk_dollars: float
    risk_per_share: float
    capped_by: Optional[str] = None


def calc_position_size(
    account_equity: float,
    max_account_risk_per_trade: float,
    entry_price: float,
    stop_price: float,
    max_position_size_dollars: Optional[float] = None,
    max_position_size_pct_equity: Optional[float] = None,
    max_risk_dollars_per_trade: Optional[float] = None,
) -> PositionSizeResult:
    """max_risk_dollars_per_trade, when set, replaces the %-of-equity risk budget with
    a flat dollar amount -- lets a wider stop be tested without the risk taken
    scaling with account size. None (default) preserves the original %-of-equity
    sizing."""
    rps = risk_per_share(entry_price, stop_price)
    if rps <= 0:
        return PositionSizeResult(shares=0, risk_dollars=0.0, risk_per_share=rps, capped_by="invalid_stop")

    if max_risk_dollars_per_trade is not None:
        risk_dollars = max_risk_dollars_per_trade
    else:
        risk_dollars = account_equity * max_account_risk_per_trade
    shares = math.floor(risk_dollars / rps)
    capped_by = None

    if max_position_size_dollars is not None and entry_price > 0:
        dollar_cap_shares = math.floor(max_position_size_dollars / entry_price)
        if dollar_cap_shares < shares:
            shares = dollar_cap_shares
            capped_by = "max_position_size_dollars"

    if max_position_size_pct_equity is not None and entry_price > 0:
        pct_cap_shares = math.floor((account_equity * max_position_size_pct_equity) / entry_price)
        if pct_cap_shares < shares:
            shares = pct_cap_shares
            capped_by = "max_position_size_pct_equity"

    return PositionSizeResult(shares=max(shares, 0), risk_dollars=risk_dollars, risk_per_share=rps, capped_by=capped_by)
