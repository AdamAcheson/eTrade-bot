"""Daily safety rules (spec section 24), no-averaging-down (section 25), no-revenge-
trading / ticker cooldown (section 26), and the kill switch. This is the gate between
"the signal engine found a candidate" and "the order manager may submit an order"."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from models.signal import RejectionReason


def business_days_elapsed(start: date, end: date) -> int:
    """Business days (Mon-Fri) strictly after `start` through `end` inclusive; 0 when
    they are the same date. Market holidays are NOT modelled -- a holiday inside the
    window is counted as a business day, so a day trade ages out of the window very
    slightly sooner than a broker counting real trading days would allow. Set
    safety.pattern_day_trader.rolling_business_days to 6 to buy back that margin if
    your broker's counting turns out to be stricter."""
    if end <= start:
        return 0
    delta = (end - start).days
    if delta > 30:
        # Far outside any plausible rolling window -- skip the loop entirely.
        return 999
    elapsed = 0
    cur = start
    for _ in range(delta):
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            elapsed += 1
    return elapsed


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
        # FINRA pattern-day-trader limit (safety.pattern_day_trader). Absent or
        # disabled = unlimited day trades, which is the correct behaviour for an
        # account at or above the $25,000 PDT equity minimum.
        self.pdt = self.safety.get("pattern_day_trader") or {}

        self.trades_today: int = 0
        self.daily_realized_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.cooldown_until: Dict[str, datetime] = {}
        self._entries_blocked: bool = False
        self._block_reason: Optional[str] = None
        # One entry per day trade (a position opened and closed in the same session).
        # Deliberately NOT cleared by reset_daily_counters -- the whole point is that
        # it survives across sessions.
        self._day_trade_dates: List[date] = []

    # --- external state feeds -------------------------------------------------
    def block_new_entries(self, reason: str) -> None:
        self._entries_blocked = True
        self._block_reason = reason

    def unblock_new_entries(self) -> None:
        self._entries_blocked = False
        self._block_reason = None

    def record_trade_result(
        self,
        pnl: float,
        now: Optional[datetime] = None,
        ticker: Optional[str] = None,
        entry_time: Optional[datetime] = None,
    ) -> None:
        """entry_time is the position's ENTRY timestamp; `now` is its exit. When both
        fall in the same session the round trip is a day trade and is recorded against
        the PDT window. Passing entry_time=None skips that accounting entirely, so a
        caller that genuinely doesn't know cannot silently under-count."""
        now = now or datetime.utcnow()
        if entry_time is not None and entry_time.date() == now.date():
            self.record_day_trade(now.date())
        self.trades_today += 1
        self.daily_realized_pnl += pnl
        if pnl < 0:
            self.consecutive_losses += 1
            if ticker:
                cooldown_minutes = self.safety["ticker_cooldown_after_stop_minutes"]
                self.cooldown_until[ticker] = now + timedelta(minutes=cooldown_minutes)
        else:
            self.consecutive_losses = 0

    # --- pattern day trader accounting ----------------------------------------
    def record_day_trade(self, session_date: date) -> None:
        self._day_trade_dates.append(session_date)

    def day_trades_in_window(self, as_of: date) -> int:
        """Day trades inside the rolling window ending `as_of`, pruning anything that
        has aged out. Pruning on read keeps the list bounded without a separate
        housekeeping call."""
        window = int(self.pdt.get("rolling_business_days", 5))
        self._day_trade_dates = [
            d for d in self._day_trade_dates if business_days_elapsed(d, as_of) <= window - 1
        ]
        return len(self._day_trade_dates)

    def day_trades_remaining(self, as_of: date) -> Optional[int]:
        """None when the limit is disabled (i.e. unlimited)."""
        if not self.pdt.get("enabled", False):
            return None
        return max(0, int(self.pdt.get("max_day_trades", 3)) - self.day_trades_in_window(as_of))

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
        open_positions_opened_today: int = 0,
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

        # PDT gate. Checked at ENTRY, not exit: this strategy closes ~99% of round
        # trips the same session, so an entry taken with no day trades left would
        # almost certainly become the one that trips the limit -- and by the time the
        # exit comes round there is no way to avoid it without holding stock
        # overnight that the thesis says to sell. Blocking the entry is the only
        # point at which the choice is still available. Conservative by construction:
        # it also blocks the occasional entry that would have been held overnight and
        # so would never have counted as a day trade.
        #
        # open_positions_opened_today is load-bearing, not a refinement: counting only
        # COMPLETED day trades lets max_concurrent_positions of them be opened before
        # the first one closes, and every one of those closes the same session. With 5
        # slots and a limit of 3 that silently produced 5 day trades in a day -- the
        # backtest showed windows of 5 against a limit of 3 until this term was added.
        # Each position opened today is a day trade in waiting, so it has to be spent
        # against the budget up front.
        remaining = self.day_trades_remaining((now or datetime.utcnow()).date())
        if remaining is not None and remaining - open_positions_opened_today <= 0:
            return RiskCheckResult(False, RejectionReason.REJECTED_PDT_LIMIT, "pattern_day_trader_limit")

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
