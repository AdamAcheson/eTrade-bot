"""Per-ticker state machine + intraday trade management (spec sections 17 & 23).
PositionManager is the only component allowed to move a ticker between states, so
contradictory actions (e.g. a second independent entry while POSITION_OPEN) are
structurally prevented rather than merely discouraged by convention."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from models.position import Position
from models.trade import ALLOWED_TRANSITIONS, ExitReason, Trade, TradeState


class InvalidTransition(Exception):
    pass


@dataclass
class ManagementAction:
    breakeven_moved: bool = False
    partial_exit_shares: int = 0
    should_exit: bool = False
    exit_reason: Optional[ExitReason] = None
    exit_price: Optional[float] = None


class PositionManager:
    def __init__(self, max_concurrent_positions: int, pyramiding: bool = False) -> None:
        self.max_concurrent_positions = max_concurrent_positions
        self.pyramiding = pyramiding
        self._states: Dict[str, TradeState] = {}
        self._positions: Dict[str, Position] = {}
        self._pending_orders: Dict[str, bool] = {}

    # --- state machine -----------------------------------------------------
    def get_state(self, ticker: str) -> TradeState:
        return self._states.get(ticker, TradeState.WATCHING)

    def transition(self, ticker: str, new_state: TradeState) -> None:
        current = self.get_state(ticker)
        if new_state != current and new_state not in ALLOWED_TRANSITIONS.get(current, set()):
            raise InvalidTransition(f"{ticker}: cannot go {current} -> {new_state}")
        self._states[ticker] = new_state

    def has_open_position(self, ticker: str) -> bool:
        pos = self._positions.get(ticker)
        return pos is not None and pos.is_open()

    def has_pending_order(self, ticker: str) -> bool:
        return self._pending_orders.get(ticker, False)

    def open_position_count(self) -> int:
        return sum(1 for p in self._positions.values() if p.is_open())

    def get_position(self, ticker: str) -> Optional[Position]:
        return self._positions.get(ticker)

    def open_positions(self) -> List[Position]:
        return [p for p in self._positions.values() if p.is_open()]

    # --- order lifecycle -----------------------------------------------------
    def mark_order_pending(self, ticker: str) -> None:
        if self.has_open_position(ticker) and not self.pyramiding:
            raise InvalidTransition(f"{ticker}: position already open, pyramiding disabled")
        self.transition(ticker, TradeState.ORDER_PENDING)
        self._pending_orders[ticker] = True

    def cancel_pending_order(self, ticker: str) -> None:
        self._pending_orders[ticker] = False
        if self.get_state(ticker) == TradeState.ORDER_PENDING:
            self.transition(ticker, TradeState.WATCHING)

    def open_position(
        self,
        ticker: str,
        benchmark: str,
        entry_time: datetime,
        entry_price: float,
        shares: int,
        stop_price: float,
        target_price: float,
        setup_type: str,
        setup_score: float,
    ) -> Position:
        if self.has_open_position(ticker) and not self.pyramiding:
            raise InvalidTransition(f"{ticker}: position already open, pyramiding disabled")

        position = Position(
            ticker=ticker,
            benchmark=benchmark,
            state=TradeState.POSITION_OPEN,
            entry_time=entry_time,
            entry_price=entry_price,
            shares=shares,
            original_shares=shares,
            initial_stop=stop_price,
            current_stop=stop_price,
            initial_target=target_price,
            current_target=target_price,
            setup_type=setup_type,
            setup_score=setup_score,
        )
        self._positions[ticker] = position
        self._pending_orders[ticker] = False
        self.transition(ticker, TradeState.POSITION_OPEN)
        return position

    # --- intraday management (+1R breakeven, +1.5R partial exit, stop/target) ---
    def manage(
        self,
        ticker: str,
        current_price: float,
        current_time: datetime,
        breakeven_trigger_r: float,
        partial_exit_enabled: bool,
        partial_exit_trigger_r: float,
        partial_exit_sell_fraction: float,
        trailing_enabled: bool = False,
        trailing_atr_multiplier: float = 1.0,
        trailing_activate_r: float = 1.0,
        atr: Optional[float] = None,
    ) -> ManagementAction:
        position = self._positions.get(ticker)
        if position is None or not position.is_open():
            return ManagementAction()

        position.update_excursion(current_price)
        r = position.r_multiple(current_price)
        action = ManagementAction()

        if not position.breakeven_moved and r >= breakeven_trigger_r:
            position.current_stop = max(position.current_stop, position.entry_price)
            position.breakeven_moved = True
            action.breakeven_moved = True

        # Ratchet the stop up under the high-water mark. Without this the stop moves
        # to entry once at +1R and then never again, so a trade that runs to +1.8R
        # and retraces exits at exactly the entry price: in the 183-day backtest, 23
        # of 96 trades closed for $0.00 that way, having reached a median 1.12%
        # favorable excursion first. Those are not break-even trades in any useful
        # sense -- they consumed the position slot (which binds: ~90% of qualifying
        # signals are rejected because one is already open) and returned nothing.
        # Off by default; the break-even move above still applies either way.
        if trailing_enabled and atr and atr > 0 and r >= trailing_activate_r:
            high_water = position.entry_price + position.maximum_favorable_excursion
            position.current_stop = max(
                position.current_stop, high_water - atr * trailing_atr_multiplier
            )

        if (
            partial_exit_enabled
            and not position.partial_exit_taken
            and r >= partial_exit_trigger_r
        ):
            shares_to_sell = int(position.original_shares * partial_exit_sell_fraction)
            if shares_to_sell > 0:
                position.apply_partial_exit(current_time, current_price, shares_to_sell, "partial_target_1_5R")
                action.partial_exit_shares = shares_to_sell
                if position.shares > 0:
                    self.transition(ticker, TradeState.POSITION_PARTIAL)

        if current_price <= position.current_stop:
            action.should_exit = True
            action.exit_reason = ExitReason.TRAILING_STOP if position.breakeven_moved else ExitReason.STOP_HIT
            action.exit_price = position.current_stop
        elif current_price >= position.current_target:
            action.should_exit = True
            action.exit_reason = ExitReason.TARGET_HIT
            action.exit_price = position.current_target

        return action

    def close_position(
        self,
        ticker: str,
        exit_time: datetime,
        exit_price: float,
        exit_reason: ExitReason,
        overnight: bool = False,
        benchmark_return: Optional[float] = None,
    ) -> Trade:
        position = self._positions.get(ticker)
        if position is None:
            raise ValueError(f"no position for {ticker}")

        self.transition(ticker, TradeState.EXIT_PENDING)

        trade = Trade(
            ticker=ticker,
            date=position.entry_time.date().isoformat(),
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            shares=position.original_shares,
            initial_stop=position.initial_stop,
            initial_target=position.initial_target,
            setup_score=position.setup_score,
            setup_type=position.setup_type,
            overnight_yes_no=overnight,
        )
        trade.maximum_favorable_excursion = position.maximum_favorable_excursion
        trade.maximum_adverse_excursion = position.maximum_adverse_excursion
        trade.close(exit_time, exit_price, exit_reason, benchmark_return=benchmark_return)

        self.transition(ticker, TradeState.CLOSED)
        self.transition(ticker, TradeState.WATCHING)
        del self._positions[ticker]
        return trade

    def lock_out(self, ticker: str) -> None:
        self.transition(ticker, TradeState.LOCKED_OUT)
