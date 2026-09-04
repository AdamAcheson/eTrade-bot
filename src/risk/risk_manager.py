"""Daily safety rules (spec section 24), no-averaging-down (section 25), no-revenge-
trading / ticker cooldown (section 26), and the kill switch. This is the gate between
"the signal engine found a candidate" and "the order manager may submit an order"."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional

from models.signal import RejectionReason


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: Optional[RejectionReason] = None
    detail: Optional[str] = None


class RiskManager:
    def __init__(self, risk_config: dict) -> None:
        self.config = risk_config
        self.safety = risk_config["safety"]
        self.behavior = risk_config["behavior"]

        self.trades_today: int = 0
        self.daily_realized_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.cooldown_until: Dict[str, datetime] = {}
        self._entries_blocked: bool = False
        self._block_reason: Optional[str] = None

    # --- external state feeds -------------------------------------------------
    def block_new_entries(self, reason: str) -> None:
        self._entries_blocked = True
        self._block_reason = reason

    def unblock_new_entries(self) -> None:
        self._entries_blocked = False
        self._block_reason = None

    def record_trade_result(self, pnl: float, now: Optional[datetime] = None, ticker: Optional[str] = None) -> None:
        now = now or datetime.utcnow()
        self.trades_today += 1
        self.daily_realized_pnl += pnl
        if pnl < 0:
            self.consecutive_losses += 1
            if ticker:
                cooldown_minutes = self.safety["ticker_cooldown_after_stop_minutes"]
                self.cooldown_until[ticker] = now + timedelta(minutes=cooldown_minutes)
        else:
            self.consecutive_losses = 0

    def reset_daily_counters(self) -> None:
        self.trades_today = 0
        self.daily_realized_pnl = 0.0
        self.consecutive_losses = 0
        self.cooldown_until.clear()
        self.unblock_new_entries()

    # --- checks ----------------------------------------------------------------
    def is_ticker_cooling_down(self, ticker: str, now: Optional[datetime] = None) -> bool:
        until = self.cooldown_until.get(ticker)
        if until is None:
            return False
        return (now or datetime.utcnow()) < until

    def can_open_new_position(
        self,
        ticker: str,
        account_equity: float,
        has_open_position: bool,
        open_position_count: int,
        has_pending_order_for_ticker: bool,
        spread_pct: Optional[float],
        max_spread_pct: float,
        data_is_stale: bool,
        broker_connected: bool,
        kill_switch_active: bool,
        now: Optional[datetime] = None,
    ) -> RiskCheckResult:
        if kill_switch_active:
            return RiskCheckResult(False, RejectionReason.REJECTED_MAX_DAILY_RISK, "kill_switch_active")

        if self._entries_blocked:
            return RiskCheckResult(False, RejectionReason.REJECTED_DATA_QUALITY, self._block_reason)

        if data_is_stale:
            return RiskCheckResult(False, RejectionReason.REJECTED_DATA_QUALITY, "stale_market_data")

        if not broker_connected:
            return RiskCheckResult(False, RejectionReason.REJECTED_DATA_QUALITY, "broker_disconnected")

        if has_open_position or has_pending_order_for_ticker:
            return RiskCheckResult(False, RejectionReason.REJECTED_DUPLICATE_POSITION, "position_or_order_exists")

        if open_position_count >= self.behavior["max_concurrent_positions"]:
            return RiskCheckResult(False, RejectionReason.REJECTED_MAX_DAILY_RISK, "max_concurrent_positions")

        if self.trades_today >= self.safety["max_trades_per_day"]:
            return RiskCheckResult(False, RejectionReason.REJECTED_MAX_DAILY_RISK, "max_trades_per_day")

        if self.consecutive_losses >= self.safety["max_consecutive_losses"]:
            return RiskCheckResult(False, RejectionReason.REJECTED_MAX_DAILY_RISK, "max_consecutive_losses")

        max_daily_loss_dollars = -abs(self.safety["max_daily_loss_pct"]) * account_equity
        if self.daily_realized_pnl <= max_daily_loss_dollars:
            return RiskCheckResult(False, RejectionReason.REJECTED_MAX_DAILY_RISK, "max_daily_loss")

        if self.is_ticker_cooling_down(ticker, now):
            return RiskCheckResult(False, RejectionReason.REJECTED_MAX_DAILY_RISK, "ticker_cooldown_after_stop")

        if spread_pct is None or spread_pct > max_spread_pct:
            return RiskCheckResult(False, RejectionReason.REJECTED_SPREAD_TOO_WIDE, "spread_recheck_failed")

        return RiskCheckResult(True)

    def can_average_down(self) -> bool:
        # Spec section 25: never permitted, regardless of configuration drift.
        return False
