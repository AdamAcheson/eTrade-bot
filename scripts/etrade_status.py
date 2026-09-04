#!/usr/bin/env python3
"""Read-only account status: balance, open positions (with live unrealized P&L),
and open orders. Run this anytime to see what's actually happening in your E*TRADE
sandbox account. Never places, previews, or cancels an order.

Usage:
    python3 scripts/etrade_status.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from config_loader import load_config, ConfigError  # noqa: E402
from broker.etrade import ETradeAPIError, ETradeBrokerAdapter, ETradeSafetyError  # noqa: E402
from broker.etrade_auth import ETradeAuthError  # noqa: E402


def main() -> int:
    try:
        config = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    if config.broker["mode"] != "sandbox":
        print("config/broker.yaml mode must be 'sandbox' to run this check.", file=sys.stderr)
        return 1

    try:
        adapter = ETradeBrokerAdapter(config.broker)
    except (ETradeSafetyError, ETradeAuthError) as e:
        print(f"Setup error: {e}", file=sys.stderr)
        return 1

    print("=" * 60)
    print("ACCOUNT BALANCE")
    print("=" * 60)
    try:
        account = adapter.get_account()
        print(f"  Equity:        ${account.equity:,.2f}")
        print(f"  Cash:          ${account.cash:,.2f}")
        print(f"  Buying power:  ${account.buying_power:,.2f}")
    except ETradeAPIError as e:
        print(f"  FAILED: {e} (status={e.status_code})")
        print(f"  body: {e.body}")
        return 1

    print()
    print("=" * 60)
    print("OPEN POSITIONS")
    print("=" * 60)
    try:
        positions = adapter.get_positions()
    except ETradeAPIError as e:
        print(f"  FAILED: {e} (status={e.status_code})")
        print(f"  body: {e.body}")
        return 1

    if not positions:
        print("  No open positions.")
    else:
        total_unrealized = 0.0
        for ticker, pos in positions.items():
            cost_basis = pos.quantity * pos.avg_price
            line = f"  {ticker:<8} qty={pos.quantity:<8} avg_price=${pos.avg_price:,.2f}  cost_basis=${cost_basis:,.2f}"
            try:
                quote = adapter.get_quote(ticker)
                market_value = pos.quantity * quote["last"]
                unrealized = market_value - cost_basis
                total_unrealized += unrealized
                pct = (unrealized / cost_basis * 100) if cost_basis else 0.0
                line += f"  last=${quote['last']:,.2f}  unrealized=${unrealized:,.2f} ({pct:+.2f}%)"
            except ETradeAPIError:
                line += "  (could not fetch live quote)"
            print(line)
        print(f"\n  Total unrealized P&L: ${total_unrealized:,.2f}")

    print()
    print("=" * 60)
    print("OPEN ORDERS")
    print("=" * 60)
    try:
        orders = adapter.get_open_orders()
    except ETradeAPIError as e:
        print(f"  FAILED: {e} (status={e.status_code})")
        print(f"  body: {e.body}")
        return 1

    if not orders:
        print("  No open orders.")
    else:
        for order in orders:
            print(
                f"  #{order.order_id}  {order.side.value} {order.quantity} {order.ticker} "
                f"@ ${order.limit_price:,.2f}  status={order.status.value}"
            )

    print()
    print("(read-only -- nothing was placed, changed, or cancelled)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
