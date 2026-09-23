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
    min_stop_distance: Optional[float] = None,
) -> float:
    """The final stop is the WIDEST (lowest) of the ATR-based stop, the
    structure-based stop, and an optional absolute floor, so a normal-sized
    pullback doesn't stop the trade out before the thesis is actually invalidated.
    Position size (not the stop) absorbs the resulting risk-per-share, per spec
    section 14's last line.

    `min_stop_distance` exists because ATR here is a 14-period average of FIVE-MINUTE
    bars -- it measures roughly the last 70 minutes, and collapses in a quiet
    afternoon. On 2026-09-17 that produced a CDE stop 0.22% below entry against a
    5.09% median daily range for that name, and a 0.42% move against the position
    took it out. A floor lets the stop reference the instrument's real volatility
    without redefining `atr` itself, which the chase rule's max_atr_above_vwap is
    separately calibrated against."""
    atr_stop = atr_stop_price(entry_price, atr_value, atr_multiplier)
    candidates = [atr_stop]
    structure_stop = structure_stop_price(swing_low)
    if structure_stop is not None:
        candidates.append(structure_stop)
    if min_stop_distance:
        candidates.append(entry_price - min_stop_distance)
    return min(candidates)


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


def cap_shares_to_exposure(
    shares: int,
    entry_price: float,
    open_notional: float,
    account_equity: float,
    max_total_exposure_pct_equity: Optional[float],
) -> int:
    """Trim a new position so every open position together stays within
    `max_total_exposure_pct_equity` of the account's equity. At 1.0 that is "never
    hold more than the account is worth" -- the rule for a cash account, or any
    account that must not borrow.

    The per-position caps alone do not guarantee it: two $2,500 positions fit a
    $5,000 account, but after a losing day equity may be $4,900, and a broker that
    offers intraday buying power (the IBKR paper account reports $20,000 on $5,000)
    would fill the second order anyway. Open positions are counted at COST, which is
    what was actually spent. None disables the check.
    """
    if max_total_exposure_pct_equity is None or shares <= 0:
        return max(shares, 0)
    if entry_price <= 0:
        return 0
    room = max_total_exposure_pct_equity * account_equity - open_notional
    if room <= 0:
        return 0
    return min(shares, math.floor(room / entry_price))


def cap_shares_to_settled_cash(
    shares: int,
    entry_price: float,
    purchases_today: float,
    day_start_equity: float,
    max_daily_purchases_pct_equity: Optional[float],
) -> int:
    """Cash account: buy only with SETTLED cash. Money from a sale settles the next
    business day (T+1), so selling a position does not free its cash for another
    purchase the same day. Because every position is closed before the close,
    settled cash at the open equals the equity the day started with; today's
    purchases, at cost, are what has been spent from it.

    On $5,000 at $2,500 a trade this allows two entries a day, full stop -- a third
    signal after the first two have closed is refused, where a margin account (or
    the IBKR paper account, which reports $20,000 of buying power) would take it.
    Buying with unsettled proceeds and selling before they settle is a "good faith
    violation" in a cash account. None disables the check.
    """
    return cap_shares_to_exposure(
        shares=shares, entry_price=entry_price, open_notional=purchases_today,
        account_equity=day_start_equity,
        max_total_exposure_pct_equity=max_daily_purchases_pct_equity,
    )
