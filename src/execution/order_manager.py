"""Pre-submit rechecks + duplicate-order protection (spec section 11). This sits
between "signal engine + risk manager approved a candidate" and "broker.submit_limit_order
is actually called" -- the last line of defense against a stale or duplicate entry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from broker.base import BrokerInterface, Order, OrderSide
from data.indicators import IndicatorSnapshot
from positions.position_manager import PositionManager


@dataclass
class OrderSubmitResult:
    submitted: bool
    order: Optional[Order] = None
    rejection_reason: Optional[str] = None


class OrderManager:
    def __init__(self, broker: BrokerInterface, position_manager: PositionManager) -> None:
        self.broker = broker
        self.position_manager = position_manager

    def submit_entry_order(
        self,
        ticker: str,
        current_snapshot: IndicatorSnapshot,
        max_spread_pct: float,
        planned_entry_price: float,
        planned_limit_price: float,
        shares: int,
        benchmark_still_confirmed: bool,
        risk_check_passed: bool,
    ) -> OrderSubmitResult:
        # Confirm no existing duplicate order / incompatible position.
        if self.position_manager.has_pending_order(ticker):
            return OrderSubmitResult(False, rejection_reason="duplicate_order_pending")
        if self.position_manager.has_open_position(ticker) and not self.position_manager.pyramiding:
            return OrderSubmitResult(False, rejection_reason="incompatible_open_position")

        # Recheck spread.
        spread_pct = current_snapshot.spread_pct
        if spread_pct is None or spread_pct > max_spread_pct:
            return OrderSubmitResult(False, rejection_reason="spread_recheck_failed")

        # Recheck current price hasn't run away from the planned entry (>0.3% drift
        # is treated as stale-signal territory for a limit entry).
        if planned_entry_price > 0:
            drift_pct = abs(current_snapshot.last_price - planned_entry_price) / planned_entry_price * 100.0
            if drift_pct > 0.3:
                return OrderSubmitResult(False, rejection_reason="price_recheck_failed")

        # Recheck benchmark.
        if not benchmark_still_confirmed:
            return OrderSubmitResult(False, rejection_reason="benchmark_recheck_failed")

        # Recheck position risk (RiskManager's verdict, computed by the caller with
        # up-to-date daily counters).
        if not risk_check_passed:
            return OrderSubmitResult(False, rejection_reason="risk_recheck_failed")

        if shares <= 0:
            return OrderSubmitResult(False, rejection_reason="zero_shares")

        self.position_manager.mark_order_pending(ticker)
        order = self.broker.submit_limit_order(ticker, OrderSide.BUY, shares, planned_limit_price)
        return OrderSubmitResult(True, order=order)

    def submit_exit_order(self, ticker: str, shares: int, limit_price: float) -> OrderSubmitResult:
        """Submit a SELL to actually flatten (or partially flatten) a position at the
        broker -- stop/target/EOD/overnight-reduction exits must go through this, not
        just PositionManager bookkeeping, or the broker's own position/cash state
        never reflects the exit. `limit_price` should be a marketable price (e.g. the
        current bid) since the position needs to close now, not the entry-style
        "wait for a good fill" limit."""
        if shares <= 0:
            return OrderSubmitResult(False, rejection_reason="zero_shares")
        order = self.broker.submit_limit_order(ticker, OrderSide.SELL, shares, limit_price)
        return OrderSubmitResult(True, order=order)
