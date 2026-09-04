"""E*TRADE broker adapter -- STUB ONLY. Not implemented, not enabled, and not
reachable from any code path in this codebase today.

Why this file is safe as-is (spec section 37):
  * Every method below raises NotImplementedError. There is no HTTP client, no OAuth
    flow, and no request construction here -- nothing in this file can reach a
    network, sandbox or production.
  * `config_loader.load_config()` raises ConfigError at startup unless
    `config/broker.yaml: mode == "paper"`, so nothing in main.py can ever construct
    this class today regardless of what `broker.yaml: etrade.environment` says.
  * When this adapter is actually implemented (a future, separate task), it MUST:
      - read credentials only from the environment variable NAMES configured in
        broker.yaml (consumer_key_env / consumer_secret_env / oauth_token_env /
        oauth_token_secret_env / account_id_env), never hard-coded values;
      - default `etrade.environment` to "sandbox" and use E*TRADE's documented
        sandbox base URL for anything other than an explicit, human-authorized
        production configuration;
      - require BOTH `broker.yaml: mode: live` AND `ETRADE_ENV=production` in the
        environment before constructing a production session -- one switch alone
        must never be sufficient;
      - use only documented E*TRADE API endpoints (Order, Accounts, Market Data) --
        never invented/guessed endpoints.
  * The kill switch (risk.yaml: emergency.kill_switch_enabled +
    logs/KILL_SWITCH) is checked by RiskManager/main.py before any order is placed
    through whatever BrokerInterface implementation is active, including this one
    once/if it is implemented.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from broker.base import Account, BrokerInterface, BrokerPosition, Order, OrderSide


class ETradeNotImplementedError(NotImplementedError):
    pass


class ETradeBrokerAdapter(BrokerInterface):
    def __init__(self, *_args, **_kwargs) -> None:
        raise ETradeNotImplementedError(
            "ETradeBrokerAdapter is a stub. E*TRADE sandbox/production integration "
            "has not been implemented yet. Use broker.paper.PaperBrokerAdapter."
        )

    def get_account(self) -> Account:
        raise ETradeNotImplementedError

    def get_positions(self) -> Dict[str, BrokerPosition]:
        raise ETradeNotImplementedError

    def get_open_orders(self, ticker: Optional[str] = None) -> List[Order]:
        raise ETradeNotImplementedError

    def submit_limit_order(self, ticker: str, side: OrderSide, quantity: int, limit_price: float) -> Order:
        raise ETradeNotImplementedError

    def cancel_order(self, order_id: str) -> None:
        raise ETradeNotImplementedError

    def replace_order(self, order_id: str, new_limit_price: float) -> Order:
        raise ETradeNotImplementedError

    def get_order_status(self, order_id: str) -> Order:
        raise ETradeNotImplementedError

    def close_position(self, ticker: str) -> Optional[Order]:
        raise ETradeNotImplementedError

    def is_connected(self) -> bool:
        raise ETradeNotImplementedError
