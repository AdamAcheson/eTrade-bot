"""Simulated broker (spec section 2 Phase 2, section 32). This is the DEFAULT and, for
now, ONLY functioning broker adapter. It fills resting limit orders itself based on
quotes the caller feeds it (`process_quote`) -- it never talks to a network."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from broker.base import Account, BrokerInterface, BrokerPosition, Order, OrderSide, OrderStatus


class PaperBrokerAdapter(BrokerInterface):
    def __init__(self, starting_equity: float, fill_model: str = "touch") -> None:
        self.cash = starting_equity
        self.fill_model = fill_model
        self._orders: Dict[str, Order] = {}
        self._positions: Dict[str, BrokerPosition] = {}
        self._connected = True

    # --- BrokerInterface ---------------------------------------------------
    def get_account(self) -> Account:
        market_value = sum(p.quantity * p.avg_price for p in self._positions.values())
        equity = self.cash + market_value
        return Account(equity=equity, cash=self.cash, buying_power=self.cash)

    def get_positions(self) -> Dict[str, BrokerPosition]:
        return dict(self._positions)

    def get_open_orders(self, ticker: Optional[str] = None) -> List[Order]:
        orders = [o for o in self._orders.values() if o.status == OrderStatus.PENDING]
        if ticker:
            orders = [o for o in orders if o.ticker == ticker]
        return orders

    def submit_limit_order(self, ticker: str, side: OrderSide, quantity: int, limit_price: float) -> Order:
        order = Order(
            order_id=str(uuid.uuid4()),
            ticker=ticker,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
            status=OrderStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self._orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> None:
        order = self._orders.get(order_id)
        if order and order.status == OrderStatus.PENDING:
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.utcnow()

    def replace_order(self, order_id: str, new_limit_price: float) -> Order:
        old = self._orders.get(order_id)
        if old is None:
            raise KeyError(order_id)
        self.cancel_order(order_id)
        return self.submit_limit_order(old.ticker, old.side, old.quantity - old.filled_quantity, new_limit_price)

    def get_order_status(self, order_id: str) -> Order:
        return self._orders[order_id]

    def close_position(self, ticker: str) -> Optional[Order]:
        position = self._positions.get(ticker)
        if position is None or position.quantity <= 0:
            return None
        # Simplification for paper mode: flatten is treated as an immediate fill at
        # the position's own average price when no live quote is supplied; callers
        # that have a current quote should instead submit_limit_order + process_quote.
        return self.submit_limit_order(ticker, OrderSide.SELL, position.quantity, position.avg_price)

    def is_connected(self) -> bool:
        return self._connected

    def set_connected(self, connected: bool) -> None:
        self._connected = connected

    # --- simulation-only helpers --------------------------------------------
    def process_quote(self, ticker: str, bid: float, ask: float) -> List[Order]:
        """Check resting orders for `ticker` against a fresh quote and fill any that
        touch/cross their limit price ('touch' fill model). Returns newly filled/updated
        orders."""
        updated: List[Order] = []
        for order in self._orders.values():
            if order.ticker != ticker or order.status != OrderStatus.PENDING:
                continue

            if order.side == OrderSide.BUY and ask <= order.limit_price:
                self._fill(order, fill_price=min(order.limit_price, ask))
                updated.append(order)
            elif order.side == OrderSide.SELL and bid >= order.limit_price:
                self._fill(order, fill_price=max(order.limit_price, bid))
                updated.append(order)
        return updated

    def _fill(self, order: Order, fill_price: float) -> None:
        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.avg_fill_price = fill_price
        order.updated_at = datetime.utcnow()

        position = self._positions.get(order.ticker)
        if order.side == OrderSide.BUY:
            self.cash -= fill_price * order.quantity
            if position is None:
                self._positions[order.ticker] = BrokerPosition(order.ticker, order.quantity, fill_price)
            else:
                total_shares = position.quantity + order.quantity
                position.avg_price = (position.avg_price * position.quantity + fill_price * order.quantity) / total_shares
                position.quantity = total_shares
        else:
            self.cash += fill_price * order.quantity
            if position is not None:
                position.quantity -= order.quantity
                if position.quantity <= 0:
                    del self._positions[order.ticker]
