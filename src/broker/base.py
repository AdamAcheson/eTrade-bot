"""Broker abstraction (spec section 32). Strategy/execution code depends only on this
interface, never on a specific broker's API -- see broker/paper.py (implemented,
default) and broker/etrade.py (stub, not implemented, see its module docstring for the
safety rationale)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Order:
    order_id: str
    ticker: str
    side: OrderSide
    quantity: int
    limit_price: float
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
    avg_fill_price: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class Account:
    equity: float
    cash: float
    buying_power: float


@dataclass
class BrokerPosition:
    ticker: str
    quantity: int
    avg_price: float


class BrokerInterface(ABC):
    @abstractmethod
    def get_account(self) -> Account:
        ...

    @abstractmethod
    def get_positions(self) -> Dict[str, BrokerPosition]:
        ...

    @abstractmethod
    def get_open_orders(self, ticker: Optional[str] = None) -> List[Order]:
        ...

    @abstractmethod
    def submit_limit_order(self, ticker: str, side: OrderSide, quantity: int, limit_price: float) -> Order:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        ...

    @abstractmethod
    def replace_order(self, order_id: str, new_limit_price: float) -> Order:
        ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> Order:
        ...

    @abstractmethod
    def close_position(self, ticker: str) -> Optional[Order]:
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        ...
