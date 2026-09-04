#!/usr/bin/env python3
"""Places ONE test limit order against your E*TRADE SANDBOX account (fake money,
sandbox only -- ETradeBrokerAdapter refuses to construct against anything else).
This is the first time submit_limit_order() has ever been exercised against a real
account, so this script requires an explicit typed confirmation before submitting,
and does nothing else (no loop, no repeated orders, no cancellation).

Usage:
    python3 scripts/etrade_test_order.py [SYMBOL] [QUANTITY] [LIMIT_PRICE]

    Defaults: SYMBOL=AAPL, QUANTITY=1, LIMIT_PRICE=1.00 (deliberately low/unlikely
    to fill, since sandbox price data is mock/static anyway -- the point of this
    script is to confirm the preview->place mechanics work, not to test fills).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from config_loader import load_config, ConfigError  # noqa: E402
from broker.base import OrderSide  # noqa: E402
from broker.etrade import ETradeAPIError, ETradeBrokerAdapter, ETradeSafetyError  # noqa: E402
from broker.etrade_auth import ETradeAuthError  # noqa: E402


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    quantity = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    limit_price = float(sys.argv[3]) if len(sys.argv) > 3 else 1.00

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    if config.broker["mode"] != "sandbox":
        print("config/broker.yaml mode must be 'sandbox' to run this.", file=sys.stderr)
        return 1

    try:
        adapter = ETradeBrokerAdapter(config.broker)
    except (ETradeSafetyError, ETradeAuthError) as e:
        print(f"Setup error: {e}", file=sys.stderr)
        return 1

    print("=" * 60)
    print("ABOUT TO SUBMIT A TEST ORDER (sandbox only, fake money)")
    print("=" * 60)
    print(f"  Side:        BUY")
    print(f"  Symbol:      {symbol}")
    print(f"  Quantity:    {quantity}")
    print(f"  Limit price: ${limit_price:,.2f}")
    print()
    confirm = input("Type YES (all caps) to submit this order, anything else to abort: ")
    if confirm != "YES":
        print("Aborted -- no order submitted.")
        return 0

    try:
        order = adapter.submit_limit_order(symbol, OrderSide.BUY, quantity, limit_price)
    except ETradeAPIError as e:
        print(f"FAILED: {e} (status={e.status_code})")
        print(f"body: {e.body}")
        return 1

    print()
    print("Order submitted.")
    print(f"  order_id: {order.order_id}")
    print(f"  status:   {order.status.value}")
    print()
    print("Run scripts/etrade_status.py to see it in your open orders list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
