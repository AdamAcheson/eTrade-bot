"""Ticker state machine + the completed-trade journal record (spec sections 23 & 28)."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Optional


class TradeState(str, Enum):
    WATCHING = "WATCHING"
    SETUP_FORMING = "SETUP_FORMING"
    ENTRY_ELIGIBLE = "ENTRY_ELIGIBLE"
    ORDER_PENDING = "ORDER_PENDING"
    POSITION_OPEN = "POSITION_OPEN"
    POSITION_PARTIAL = "POSITION_PARTIAL"
    OVERNIGHT_REVIEW = "OVERNIGHT_REVIEW"
    OVERNIGHT_POSITION = "OVERNIGHT_POSITION"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"
    LOCKED_OUT = "LOCKED_OUT"


# Valid transitions for PositionManager to enforce (spec section 23: "prevent
# contradictory actions"). LOCKED_OUT is reachable from any state.
ALLOWED_TRANSITIONS = {
    # WATCHING -> ORDER_PENDING / POSITION_OPEN directly is allowed because
    # SETUP_FORMING/ENTRY_ELIGIBLE are represented by Signal log entries rather than
    # persisted PositionManager states in Phase 1/2 -- the granular states remain
    # available for a future pipeline that tracks them explicitly.
    TradeState.WATCHING: {
        TradeState.SETUP_FORMING,
        TradeState.ORDER_PENDING,
        TradeState.POSITION_OPEN,
        TradeState.LOCKED_OUT,
    },
    TradeState.SETUP_FORMING: {TradeState.ENTRY_ELIGIBLE, TradeState.WATCHING, TradeState.LOCKED_OUT},
    TradeState.ENTRY_ELIGIBLE: {TradeState.ORDER_PENDING, TradeState.WATCHING, TradeState.LOCKED_OUT},
    TradeState.ORDER_PENDING: {TradeState.POSITION_OPEN, TradeState.WATCHING, TradeState.LOCKED_OUT},
    TradeState.POSITION_OPEN: {
        TradeState.POSITION_PARTIAL,
        TradeState.OVERNIGHT_REVIEW,
        TradeState.EXIT_PENDING,
        TradeState.LOCKED_OUT,
    },
    TradeState.POSITION_PARTIAL: {
        TradeState.OVERNIGHT_REVIEW,
        TradeState.EXIT_PENDING,
        TradeState.LOCKED_OUT,
    },
    TradeState.OVERNIGHT_REVIEW: {
        TradeState.OVERNIGHT_POSITION,
        TradeState.EXIT_PENDING,
        TradeState.LOCKED_OUT,
    },
    TradeState.OVERNIGHT_POSITION: {
        TradeState.POSITION_OPEN,
        TradeState.EXIT_PENDING,
        TradeState.LOCKED_OUT,
    },
    TradeState.EXIT_PENDING: {TradeState.CLOSED, TradeState.LOCKED_OUT},
    TradeState.CLOSED: {TradeState.WATCHING},
    TradeState.LOCKED_OUT: set(),
}


class ExitReason(str, Enum):
    TARGET_HIT = "TARGET_HIT"
    STOP_HIT = "STOP_HIT"
    TRAILING_STOP = "TRAILING_STOP"
    END_OF_DAY = "END_OF_DAY"
    OVERNIGHT_REJECTED = "OVERNIGHT_REJECTED"
    MANUAL_EXIT = "MANUAL_EXIT"
    RISK_LIMIT = "RISK_LIMIT"
    SYSTEM_SAFETY_EXIT = "SYSTEM_SAFETY_EXIT"


@dataclass
class Trade:
    ticker: str
    date: str
    entry_time: datetime
    entry_price: float
    shares: int
    initial_stop: float
    initial_target: float

    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[ExitReason] = None

    gross_profit: Optional[float] = None
    net_profit: Optional[float] = None
    percentage_return: Optional[float] = None
    r_return: Optional[float] = None

    maximum_favorable_excursion: float = 0.0
    maximum_adverse_excursion: float = 0.0
    benchmark_return_during_trade: Optional[float] = None

    setup_score: Optional[float] = None
    setup_type: Optional[str] = None
    overnight_yes_no: bool = False

    def close(self, exit_time: datetime, exit_price: float, reason: ExitReason,
              commission: float = 0.0, benchmark_return: Optional[float] = None) -> None:
        self.exit_time = exit_time
        self.exit_price = exit_price
        self.exit_reason = reason
        self.gross_profit = (exit_price - self.entry_price) * self.shares
        self.net_profit = self.gross_profit - commission
        self.percentage_return = (exit_price - self.entry_price) / self.entry_price * 100.0
        risk_per_share = self.entry_price - self.initial_stop
        self.r_return = (exit_price - self.entry_price) / risk_per_share if risk_per_share else 0.0
        self.benchmark_return_during_trade = benchmark_return

    def update_excursion(self, current_price: float) -> None:
        favorable = current_price - self.entry_price
        adverse = self.entry_price - current_price
        if favorable > self.maximum_favorable_excursion:
            self.maximum_favorable_excursion = favorable
        if adverse > self.maximum_adverse_excursion:
            self.maximum_adverse_excursion = adverse

    def as_log_row(self) -> dict:
        row = asdict(self)
        row["entry_time"] = self.entry_time.isoformat()
        row["exit_time"] = self.exit_time.isoformat() if self.exit_time else None
        row["exit_reason"] = self.exit_reason.value if self.exit_reason else None
        return row
