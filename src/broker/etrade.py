"""E*TRADE broker adapter -- SANDBOX ONLY.

Endpoints and request/response shapes below come from E*TRADE's published API docs
(apisb.etrade.com/docs/api/{order,account,market}/*.html), cross-checked against the
`pyetrade` open-source client (spec section 36: never invent endpoints). This has NOT
been exercised against a real sandbox account by this assistant -- it never had (and
never asked for) your actual credentials. Before trusting it:

  1. Run `scripts/etrade_sandbox_check.py` first (read-only: lists accounts, gets a
     balance, gets one quote -- never places an order) and confirm the parsed output
     looks right for your account.
  2. Only then try a single small test order manually, away from the automated loop.

Why this is safe to add without enabling live trading (spec section 37):
  * `environment` MUST be "sandbox" -- the constructor raises ETradeSafetyError for
    anything else, including "production". There is no configuration value that
    flips this adapter onto api.etrade.com today.
  * `config_loader.load_config()` separately requires `broker.yaml: mode` to be
    "paper" or "sandbox" -- "live" still raises at startup regardless of what this
    file does.
  * OAuth token endpoints (see broker/etrade_auth.py) are identical for sandbox and
    production; what makes this sandbox-only is the trading/market-data host
    (`apisb.etrade.com`) hard-coded below, plus the environment check above.
  * Credentials are read only from the environment variable NAMES configured in
    broker.yaml -- never hard-coded, never logged.
"""

from __future__ import annotations

import os
import uuid
from typing import Dict, List, Optional

from broker.base import Account, BrokerInterface, BrokerPosition, Order, OrderSide, OrderStatus
from broker.etrade_auth import ETradeOAuth, load_access_token_pair, load_consumer_credentials

SANDBOX_BASE_URL = "https://apisb.etrade.com"

_ORDER_STATUS_MAP = {
    "OPEN": OrderStatus.PENDING,
    "PENDING_EXECUTION": OrderStatus.PENDING,
    "EXECUTED": OrderStatus.FILLED,
    "PARTIALLY_EXECUTED": OrderStatus.PARTIALLY_FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "CANCEL_REQUESTED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.REJECTED,
    "DO_NOT_EXERCISE": OrderStatus.REJECTED,
}


class ETradeSafetyError(Exception):
    pass


class ETradeAPIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _find_first(d: dict, *paths: str):
    """Look up the first present key path (dot-separated) in a nested dict. E*TRADE's
    JSON responses nest fields differently across account/product types; this makes
    parsing fail loudly (KeyError-style) rather than silently returning a wrong
    number when a field isn't where expected."""
    for path in paths:
        node = d
        try:
            for part in path.split("."):
                node = node[part]
            return node
        except (KeyError, TypeError, IndexError):
            continue
    raise KeyError(f"none of {paths} found in response: {d}")


class ETradeBrokerAdapter(BrokerInterface):
    def __init__(self, broker_config: dict) -> None:
        environment = broker_config["etrade"]["environment"]
        if environment != "sandbox":
            raise ETradeSafetyError(
                f"ETradeBrokerAdapter only supports environment: sandbox (got {environment!r}). "
                "Production trading is not implemented."
            )
        self.base_url = SANDBOX_BASE_URL
        credentials = load_consumer_credentials(broker_config)
        access_token_pair = load_access_token_pair(broker_config)
        self._oauth = ETradeOAuth(credentials)
        self._session = self._oauth.build_session(access_token_pair)

        account_id_env = broker_config["etrade"].get("account_id_env")
        configured_account_id = os.environ.get(account_id_env) if account_id_env else None
        self._account_id_key: Optional[str] = None
        self._configured_account_id = configured_account_id
        self._client_order_ids: Dict[str, str] = {}  # our order_id -> E*TRADE clientOrderId

    # --- low-level HTTP -------------------------------------------------------
    def _request(self, method: str, path: str, params: Optional[dict] = None, json_body: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        resp = self._session.request(method, url, params=params, json=json_body, headers=headers, timeout=15)
        if resp.status_code >= 400:
            raise ETradeAPIError(
                f"{method} {path} failed: HTTP {resp.status_code}", status_code=resp.status_code, body=resp.text
            )
        if not resp.content:
            return {}
        return resp.json()

    # --- account resolution -----------------------------------------------------
    def _resolve_account_id_key(self) -> str:
        if self._account_id_key:
            return self._account_id_key
        data = self._request("GET", "/v1/accounts/list.json")
        accounts = _find_first(data, "AccountListResponse.Accounts.Account")
        if isinstance(accounts, dict):
            accounts = [accounts]
        active = [a for a in accounts if a.get("accountStatus") == "ACTIVE"]
        candidates = active or accounts

        if self._configured_account_id:
            for a in candidates:
                if str(a.get("accountId")) == str(self._configured_account_id):
                    self._account_id_key = a["accountIdKey"]
                    return self._account_id_key
            raise ETradeAPIError(
                f"No account with accountId={self._configured_account_id} found among "
                f"{[a.get('accountId') for a in candidates]}"
            )

        if len(candidates) == 1:
            self._account_id_key = candidates[0]["accountIdKey"]
            return self._account_id_key

        raise ETradeAPIError(
            "Multiple E*TRADE accounts found and no account_id_env configured to disambiguate: "
            f"{[a.get('accountId') for a in candidates]}"
        )

    # --- BrokerInterface ---------------------------------------------------
    def get_account(self) -> Account:
        account_id_key = self._resolve_account_id_key()
        data = self._request(
            "GET",
            f"/v1/accounts/{account_id_key}/balance.json",
            params={"instType": "BROKERAGE", "realTimeNAV": "true"},
        )
        balance = _find_first(data, "BalanceResponse")
        equity = _find_first(
            balance,
            "Computed.RealTimeValues.totalAccountValue",
            "Computed.netAccountValue",
            "netAccountValue",
        )
        cash = _find_first(
            balance,
            "Computed.cashAvailableForInvestment",
            "Computed.cashBalance",
            "cashAvailableForInvestment",
        )
        # Real sandbox MARGIN-account responses observed in practice have no
        # buying-power-specific field at all (confirmed against a live sandbox
        # account) -- fall back to the cash figures already resolved above rather
        # than crashing when that's the case.
        buying_power = _find_first(
            balance,
            "Computed.cashBuyingPower",
            "Computed.marginBuyingPower",
            "cashBuyingPower",
            "Computed.cashAvailableForInvestment",
            "Computed.cashAvailableForWithdrawal",
        )
        return Account(equity=float(equity), cash=float(cash), buying_power=float(buying_power))

    def get_positions(self) -> Dict[str, BrokerPosition]:
        account_id_key = self._resolve_account_id_key()
        try:
            data = self._request("GET", f"/v1/accounts/{account_id_key}/portfolio.json")
        except ETradeAPIError as e:
            if e.status_code == 204:
                return {}
            raise
        try:
            portfolios = _find_first(data, "PortfolioResponse.AccountPortfolio")
        except KeyError:
            return {}
        if isinstance(portfolios, dict):
            portfolios = [portfolios]

        positions: Dict[str, BrokerPosition] = {}
        for portfolio in portfolios:
            entries = portfolio.get("Position", [])
            if isinstance(entries, dict):
                entries = [entries]
            for entry in entries:
                symbol = _find_first(entry, "Product.symbol", "symbolDescription")
                quantity = _find_first(entry, "quantity")
                avg_price = _find_first(entry, "pricePaid", "Complete.pricePaid")
                positions[symbol] = BrokerPosition(ticker=symbol, quantity=int(quantity), avg_price=float(avg_price))
        return positions

    def get_open_orders(self, ticker: Optional[str] = None) -> List[Order]:
        account_id_key = self._resolve_account_id_key()
        params = {"status": "OPEN"}
        if ticker:
            params["symbol"] = ticker
        try:
            data = self._request("GET", f"/v1/accounts/{account_id_key}/orders.json", params=params)
        except ETradeAPIError as e:
            if e.status_code == 204:
                return []
            raise
        try:
            order_entries = _find_first(data, "OrdersResponse.Order")
        except KeyError:
            return []
        if isinstance(order_entries, dict):
            order_entries = [order_entries]
        return [self._parse_order(o) for o in order_entries]

    def _parse_order(self, order_json: dict) -> Order:
        detail = order_json.get("OrderDetail", [order_json])
        if isinstance(detail, list):
            detail = detail[0]
        instrument = detail.get("Instrument", [{}])
        if isinstance(instrument, list):
            instrument = instrument[0]
        side = OrderSide.BUY if instrument.get("orderAction", "BUY") == "BUY" else OrderSide.SELL
        status_str = detail.get("status", "OPEN")
        return Order(
            order_id=str(order_json.get("orderId")),
            ticker=_find_first(instrument, "Product.symbol"),
            side=side,
            quantity=int(instrument.get("orderedQuantity", instrument.get("quantity", 0))),
            limit_price=float(detail.get("limitPrice", 0.0)),
            status=_ORDER_STATUS_MAP.get(status_str, OrderStatus.PENDING),
            filled_quantity=int(instrument.get("filledQuantity", 0)),
            avg_fill_price=float(instrument["averageExecutionPrice"]) if instrument.get("averageExecutionPrice") else None,
        )

    def _build_order_body(self, ticker: str, side: OrderSide, quantity: int, limit_price: float, client_order_id: str) -> dict:
        return {
            "PreviewOrderRequest": {
                "orderType": "EQ",
                "clientOrderId": client_order_id,
                "Order": [
                    {
                        "allOrNone": False,
                        "priceType": "LIMIT",
                        "orderTerm": "GOOD_FOR_DAY",
                        "marketSession": "REGULAR",
                        "limitPrice": limit_price,
                        "Instrument": [
                            {
                                "Product": {"securityType": "EQ", "symbol": ticker},
                                "orderAction": side.value,
                                "quantityType": "QUANTITY",
                                "quantity": quantity,
                            }
                        ],
                    }
                ],
            }
        }

    def submit_limit_order(self, ticker: str, side: OrderSide, quantity: int, limit_price: float) -> Order:
        account_id_key = self._resolve_account_id_key()
        client_order_id = uuid.uuid4().hex[:20]
        preview_body = self._build_order_body(ticker, side, quantity, limit_price, client_order_id)

        preview_resp = self._request(
            "POST", f"/v1/accounts/{account_id_key}/orders/preview.json", json_body=preview_body
        )
        preview_ids = _find_first(preview_resp, "PreviewOrderResponse.PreviewIds")
        if isinstance(preview_ids, dict):
            preview_ids = [preview_ids]

        place_body = dict(preview_body["PreviewOrderRequest"])
        place_body["PreviewIds"] = preview_ids
        place_resp = self._request(
            "POST",
            f"/v1/accounts/{account_id_key}/orders/place.json",
            json_body={"PlaceOrderRequest": place_body},
        )

        order_id = str(_find_first(place_resp, "PlaceOrderResponse.OrderIds.orderId", "PlaceOrderResponse.orderIds.orderId"))
        self._client_order_ids[order_id] = client_order_id
        return Order(
            order_id=order_id,
            ticker=ticker,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
            status=OrderStatus.PENDING,
        )

    def cancel_order(self, order_id: str) -> None:
        account_id_key = self._resolve_account_id_key()
        self._request(
            "PUT",
            f"/v1/accounts/{account_id_key}/orders/cancel.json",
            json_body={"CancelOrderRequest": {"orderId": int(order_id)}},
        )

    def replace_order(self, order_id: str, new_limit_price: float) -> Order:
        account_id_key = self._resolve_account_id_key()
        existing = self.get_order_status(order_id)
        client_order_id = uuid.uuid4().hex[:20]
        preview_body = self._build_order_body(
            existing.ticker, existing.side, existing.quantity - existing.filled_quantity, new_limit_price, client_order_id
        )
        preview_resp = self._request(
            "PUT",
            f"/v1/accounts/{account_id_key}/orders/{order_id}/change/preview.json",
            json_body=preview_body,
        )
        preview_ids = _find_first(preview_resp, "PreviewOrderResponse.PreviewIds")
        if isinstance(preview_ids, dict):
            preview_ids = [preview_ids]
        place_body = dict(preview_body["PreviewOrderRequest"])
        place_body["PreviewIds"] = preview_ids
        place_resp = self._request(
            "PUT",
            f"/v1/accounts/{account_id_key}/orders/{order_id}/change/place.json",
            json_body={"PlaceOrderRequest": place_body},
        )
        new_order_id = str(_find_first(place_resp, "PlaceOrderResponse.OrderIds.orderId", "PlaceOrderResponse.orderIds.orderId"))
        return Order(
            order_id=new_order_id,
            ticker=existing.ticker,
            side=existing.side,
            quantity=existing.quantity - existing.filled_quantity,
            limit_price=new_limit_price,
            status=OrderStatus.PENDING,
        )

    def get_order_status(self, order_id: str) -> Order:
        account_id_key = self._resolve_account_id_key()
        data = self._request("GET", f"/v1/accounts/{account_id_key}/orders.json", params={"status": "OPEN"})
        try:
            order_entries = _find_first(data, "OrdersResponse.Order")
        except KeyError:
            order_entries = []
        if isinstance(order_entries, dict):
            order_entries = [order_entries]
        for o in order_entries:
            if str(o.get("orderId")) == str(order_id):
                return self._parse_order(o)
        raise ETradeAPIError(
            f"Order {order_id} not found among open orders -- it may have already "
            f"filled, been cancelled, or expired. Query order history if you need its "
            f"final state (not implemented here)."
        )

    def close_position(self, ticker: str) -> Optional[Order]:
        positions = self.get_positions()
        position = positions.get(ticker)
        if position is None or position.quantity <= 0:
            return None
        # No live quote available from BrokerInterface's close_position() signature --
        # callers that have a current quote should submit_limit_order(SELL, ...)
        # directly with a marketable limit price instead of relying on this method.
        raise NotImplementedError(
            "ETradeBrokerAdapter.close_position needs a live quote to pick a marketable "
            "limit price; call submit_limit_order(ticker, OrderSide.SELL, quantity, "
            "limit_price) directly with a current bid instead."
        )

    def is_connected(self) -> bool:
        try:
            self._request("GET", "/v1/accounts/list.json")
            return True
        except ETradeAPIError:
            return False

    # --- market data helper (used by data/etrade_market_data.py) ---------------
    def get_quote(self, symbol: str) -> dict:
        data = self._request("GET", f"/v1/market/quote/{symbol}.json")
        quote_data = _find_first(data, "QuoteResponse.QuoteData")
        if isinstance(quote_data, list):
            quote_data = quote_data[0]
        all_details = _find_first(quote_data, "All")
        return {
            "bid": float(_find_first(all_details, "bid")),
            "ask": float(_find_first(all_details, "ask")),
            "last": float(_find_first(all_details, "lastTrade")),
            "volume": float(_find_first(all_details, "totalVolume")),
        }
