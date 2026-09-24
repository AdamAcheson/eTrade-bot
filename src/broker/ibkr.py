"""Interactive Brokers broker adapter -- PAPER ACCOUNTS ONLY.

Talks to Trader Workstation (TWS) or IB Gateway running on the SAME computer, over
the TWS API, using ib_async. Nothing here reaches IBKR's servers directly: TWS does
that, logged in as whichever account the user opened.

Why this cannot trade real money (three independent checks, all must pass):
  * config_loader.load_config() only accepts `mode: ibkr_paper` with a paper port
    (7497 TWS / 4002 Gateway) on this computer (127.0.0.1 / localhost / ::1).
  * This constructor repeats those checks, then refuses to keep the connection
    unless EVERY account TWS reports is a paper account (id starting "DU"). A TWS
    logged into a live account reports a "U..." id and the constructor raises
    IBKRSafetyError before any order method can run.
  * Every order is sent with that verified paper account id attached, and
    submit_limit_order re-checks it.

Fills are asynchronous at a real broker. The rest of the bot (main.py) was written
against PaperBrokerAdapter, where an order is filled or not by the time
submit_limit_order returns. To keep that contract, submit_limit_order here WAITS up
to `fill_timeout_seconds` for the order to finish, then cancels whatever has not
filled, and returns the final state. filled_quantity may be anywhere from 0 to the
full quantity; callers must use it, not the quantity they asked for.
"""

from __future__ import annotations

import math
import time
from typing import Dict, List, Optional

from broker.base import Account, BrokerInterface, BrokerPosition, Order, OrderSide, OrderStatus

PAPER_PORTS = (7497, 4002)
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

# ib_async OrderStatus.status -> ours. Anything not listed is still working.
_DONE_STATUS = {
    "Filled": OrderStatus.FILLED,
    "Cancelled": OrderStatus.CANCELLED,
    "ApiCancelled": OrderStatus.CANCELLED,
    "Inactive": OrderStatus.REJECTED,
}


class IBKRSafetyError(Exception):
    pass


def check_paper_settings(ibkr_cfg: dict) -> None:
    """Shared with config_loader so the rule lives in one place."""
    port = ibkr_cfg.get("port")
    host = ibkr_cfg.get("host", "127.0.0.1")
    if port not in PAPER_PORTS:
        raise IBKRSafetyError(
            f"ibkr.port must be a paper-trading port {PAPER_PORTS} (7497 TWS paper, "
            f"4002 IB Gateway paper); got {port!r}. Live trading is not implemented.")
    if host not in LOCAL_HOSTS:
        raise IBKRSafetyError(
            f"ibkr.host must be this computer {LOCAL_HOSTS}; got {host!r}. The TWS API "
            f"must never be opened to the network.")


def is_paper_account(account_id: str) -> bool:
    return str(account_id).upper().startswith("DU")


def _round_limit(price: float, side: OrderSide) -> float:
    """US stocks over $1 trade in whole cents; IBKR rejects a limit with more
    decimals. Round toward the marketable side so rounding never makes a
    must-fill-now order less likely to fill."""
    cents = price * 100
    cents = math.ceil(cents - 1e-6) if side == OrderSide.BUY else math.floor(cents + 1e-6)
    return round(cents / 100, 2)


class IBKRPaperBrokerAdapter(BrokerInterface):
    reports_final_fills = True   # submit_limit_order waits, then cancels the rest

    def __init__(self, broker_config: dict, ib=None, sleep=None, clock=None) -> None:
        cfg = dict(broker_config.get("ibkr") or {})
        check_paper_settings(cfg)
        self.host = cfg.get("host", "127.0.0.1")
        self.port = int(cfg["port"])
        self.client_id = int(cfg.get("client_id", 21))
        self.fill_timeout = float(cfg.get("fill_timeout_seconds", 10))
        self.cancel_timeout = float(cfg.get("cancel_timeout_seconds", 5))
        wanted = str(cfg.get("account_id") or "").strip()

        if ib is None:
            from ib_async import IB
            ib = IB()
        self.ib = ib
        self._sleep = sleep or self.ib.sleep
        self._clock = clock or time.monotonic
        self._contracts: Dict[str, object] = {}
        self._trades: Dict[str, object] = {}

        # readonly=False is required to place orders; TWS's own "Read-Only API"
        # box must also be unticked.
        self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=10, readonly=False)
        accounts = list(self.ib.managedAccounts() or [])
        live = [a for a in accounts if not is_paper_account(a)]
        if not accounts or live:
            self.ib.disconnect()
            raise IBKRSafetyError(
                f"TWS reports accounts {accounts or 'none'}; every one must be a paper "
                f"account (id starting 'DU'). Log TWS into Paper Trading and try again.")
        if wanted and wanted not in accounts:
            self.ib.disconnect()
            raise IBKRSafetyError(f"ibkr.account_id {wanted!r} is not among {accounts}.")
        self.account = wanted or accounts[0]

    # --- helpers --------------------------------------------------------------
    def _contract(self, ticker: str):
        if ticker not in self._contracts:
            from ib_async import Stock
            qualified = self.ib.qualifyContracts(Stock(ticker, "SMART", "USD"))
            if not qualified:
                raise ValueError(f"IBKR does not recognise {ticker} (SMART, USD)")
            self._contracts[ticker] = qualified[0]
        return self._contracts[ticker]

    def _wait(self, trade, seconds: float) -> None:
        deadline = self._clock() + seconds
        while not trade.isDone() and self._clock() < deadline:
            self._sleep(0.2)

    def _to_order(self, trade) -> Order:
        st = trade.orderStatus
        filled = int(round(float(st.filled or 0)))
        status = _DONE_STATUS.get(st.status)
        if status is None:
            status = OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.PENDING
        fills = list(getattr(trade, "fills", None) or [])
        commission = sum(float(getattr(f.commissionReport, "commission", 0.0) or 0.0)
                         for f in fills if getattr(f, "commissionReport", None) is not None)
        return Order(
            order_id=str(trade.order.orderId),
            ticker=trade.contract.symbol,
            side=OrderSide(trade.order.action),
            quantity=int(round(float(trade.order.totalQuantity))),
            limit_price=float(trade.order.lmtPrice),
            status=status,
            filled_quantity=filled,
            avg_fill_price=float(st.avgFillPrice) if filled > 0 and st.avgFillPrice else None,
            commission=commission if fills else None,
        )

    def _find_trade(self, order_id: str):
        trade = self._trades.get(str(order_id))
        if trade is None:
            for t in self.ib.trades():
                if str(t.order.orderId) == str(order_id):
                    trade = t
                    break
        if trade is None:
            raise KeyError(order_id)
        return trade

    # --- BrokerInterface --------------------------------------------------------
    def get_account(self) -> Account:
        values = {}
        for av in self.ib.accountSummary(self.account):
            if av.currency in ("USD", "") and av.tag not in values:
                values[av.tag] = av.value
        cash = float(values.get("TotalCashValue", 0.0))
        # Cash only. The paper account is margin-type and reports 4x buying power;
        # the live account is cash, so the bot must never be told it can borrow.
        return Account(equity=float(values.get("NetLiquidation", 0.0)), cash=cash, buying_power=cash)

    def get_positions(self) -> Dict[str, BrokerPosition]:
        out: Dict[str, BrokerPosition] = {}
        for p in self.ib.reqPositions():
            if p.account != self.account or getattr(p.contract, "secType", "STK") != "STK":
                continue
            qty = int(round(float(p.position)))
            if qty != 0:
                out[p.contract.symbol] = BrokerPosition(p.contract.symbol, qty, float(p.avgCost))
        return out

    def get_open_orders(self, ticker: Optional[str] = None) -> List[Order]:
        orders = [self._to_order(t) for t in self.ib.openTrades()]
        return [o for o in orders if ticker is None or o.ticker == ticker]

    def submit_limit_order(self, ticker: str, side: OrderSide, quantity: int, limit_price: float) -> Order:
        from ib_async import LimitOrder
        if not is_paper_account(self.account):   # belt and braces; see module docstring
            raise IBKRSafetyError(f"refusing to order on non-paper account {self.account}")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        price = _round_limit(limit_price, side)
        order = LimitOrder(side.value, quantity, price, account=self.account, tif="DAY",
                           outsideRth=False)
        trade = self.ib.placeOrder(self._contract(ticker), order)
        self._trades[str(trade.order.orderId)] = trade
        self._wait(trade, self.fill_timeout)
        if not trade.isDone():
            # Unfilled remainder of a must-fill-now order: cancel it rather than leave
            # it resting where the bot is not tracking it.
            self.ib.cancelOrder(trade.order)
            self._wait(trade, self.cancel_timeout)
        return self._to_order(trade)

    def cancel_order(self, order_id: str) -> None:
        trade = self._find_trade(order_id)
        if not trade.isDone():
            self.ib.cancelOrder(trade.order)
            self._wait(trade, self.cancel_timeout)

    def replace_order(self, order_id: str, new_limit_price: float) -> Order:
        trade = self._find_trade(order_id)
        if trade.isDone():
            raise ValueError(f"order {order_id} is already {trade.orderStatus.status}")
        side = OrderSide(trade.order.action)
        trade.order.lmtPrice = _round_limit(new_limit_price, side)
        trade = self.ib.placeOrder(trade.contract, trade.order)   # same id = modify
        self._trades[str(order_id)] = trade
        return self._to_order(trade)

    def get_order_status(self, order_id: str) -> Order:
        return self._to_order(self._find_trade(order_id))

    def close_position(self, ticker: str) -> Optional[Order]:
        """Emergency flatten. The interface gives no price to set a limit at, so this
        is the one MARKET order in the codebase; the strategy's own exits go through
        submit_limit_order with a marketable limit instead."""
        from ib_async import MarketOrder
        position = self.get_positions().get(ticker)
        if position is None or position.quantity <= 0:
            return None
        order = MarketOrder("SELL", position.quantity, account=self.account, tif="DAY")
        trade = self.ib.placeOrder(self._contract(ticker), order)
        self._trades[str(trade.order.orderId)] = trade
        self._wait(trade, self.fill_timeout)
        return self._to_order(trade)

    def is_connected(self) -> bool:
        return bool(self.ib.isConnected())

    def disconnect(self) -> None:
        self.ib.disconnect()
