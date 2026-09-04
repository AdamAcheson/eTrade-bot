"""Signal model: one record per ticker per strategy evaluation. Field set matches
spec section 27 exactly, so the signal log is the audit trail for every decision the
bot makes -- entries and rejections alike."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional


class Decision(str, Enum):
    ENTRY_CANDIDATE = "ENTRY_CANDIDATE"
    REJECTED = "REJECTED"


class RejectionReason(str, Enum):
    REJECTED_TIME_WINDOW = "REJECTED_TIME_WINDOW"
    REJECTED_BENCHMARK = "REJECTED_BENCHMARK"
    REJECTED_BENCHMARK_CONFIRMATION = "REJECTED_BENCHMARK_CONFIRMATION"
    REJECTED_LOW_VOLUME = "REJECTED_LOW_VOLUME"
    REJECTED_WIDE_SPREAD = "REJECTED_WIDE_SPREAD"
    REJECTED_SPREAD_TOO_WIDE = "REJECTED_SPREAD_TOO_WIDE"
    REJECTED_BELOW_VWAP = "REJECTED_BELOW_VWAP"
    REJECTED_EMA_ALIGNMENT = "REJECTED_EMA_ALIGNMENT"
    REJECTED_OVEREXTENDED = "REJECTED_OVEREXTENDED"
    REJECTED_POOR_RISK_REWARD = "REJECTED_POOR_RISK_REWARD"
    REJECTED_MAX_DAILY_RISK = "REJECTED_MAX_DAILY_RISK"
    REJECTED_DUPLICATE_POSITION = "REJECTED_DUPLICATE_POSITION"
    REJECTED_MANUAL_ONLY = "REJECTED_MANUAL_ONLY"
    REJECTED_DATA_QUALITY = "REJECTED_DATA_QUALITY"
    REJECTED_NO_SETUP = "REJECTED_NO_SETUP"
    REJECTED_LOW_SCORE = "REJECTED_LOW_SCORE"


@dataclass
class Signal:
    timestamp: datetime
    ticker: str
    benchmark: str

    price: float
    bid: float
    ask: float
    spread: float

    vwap: float
    ema_9: float
    ema_20: float
    atr: float
    atr_percent: float
    relative_volume: float

    opening_range_high: Optional[float]
    opening_range_low: Optional[float]

    benchmark_price: float
    benchmark_vwap: float
    benchmark_ema_9: float
    benchmark_ema_20: float

    setup_type: Optional[str] = None
    setup_score: Optional[float] = None

    entry_candidate: bool = False
    entry_price: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    risk_per_share: Optional[float] = None
    expected_reward: Optional[float] = None
    r_ratio: Optional[float] = None
    position_size: Optional[int] = None

    decision: Decision = Decision.REJECTED
    rejection_reason: Optional[RejectionReason] = None

    extra: dict = field(default_factory=dict)

    def as_log_row(self) -> dict:
        row = asdict(self)
        row["timestamp"] = self.timestamp.isoformat()
        row["decision"] = self.decision.value
        row["rejection_reason"] = self.rejection_reason.value if self.rejection_reason else None
        row.pop("extra")
        return row
