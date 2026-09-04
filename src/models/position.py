"""Live working state for an open (or being-managed) position. Distinct from Trade
(the immutable-once-closed journal record) -- Position is mutated in place by
PositionManager as the trade is managed intraday (spec section 17)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from models.trade import TradeState


@dataclass
class PartialExit:
    time: datetime
    price: float
    shares: int
    reason: str


@dataclass
class Position:
    ticker: str
    benchmark: str
    state: TradeState

    entry_time: datetime
    entry_price: float
    shares: int
    original_shares: int

    initial_stop: float
    current_stop: float
    initial_target: float
    current_target: float

    setup_type: Optional[str] = None
    setup_score: Optional[float] = None

    maximum_favorable_excursion: float = 0.0
    maximum_adverse_excursion: float = 0.0
    partial_exits: List[PartialExit] = field(default_factory=list)

    breakeven_moved: bool = False
    partial_exit_taken: bool = False
    overnight: bool = False
    overnight_multiplier_applied: Optional[float] = None

    @property
    def initial_risk_per_share(self) -> float:
        return self.entry_price - self.initial_stop

    def r_multiple(self, current_price: float) -> float:
        risk = self.initial_risk_per_share
        if risk <= 0:
            return 0.0
        return (current_price - self.entry_price) / risk

    def update_excursion(self, current_price: float) -> None:
        favorable = current_price - self.entry_price
        adverse = self.entry_price - current_price
        if favorable > self.maximum_favorable_excursion:
            self.maximum_favorable_excursion = favorable
        if adverse > self.maximum_adverse_excursion:
            self.maximum_adverse_excursion = adverse

    def is_open(self) -> bool:
        return self.state in (
            TradeState.POSITION_OPEN,
            TradeState.POSITION_PARTIAL,
            TradeState.OVERNIGHT_REVIEW,
            TradeState.OVERNIGHT_POSITION,
        )

    def apply_partial_exit(self, time: datetime, price: float, shares: int, reason: str) -> None:
        shares = min(shares, self.shares)
        self.partial_exits.append(PartialExit(time=time, price=price, shares=shares, reason=reason))
        self.shares -= shares
        self.partial_exit_taken = True
