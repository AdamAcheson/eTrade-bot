#!/usr/bin/env python3
"""Read-only connectivity check against E*TRADE's sandbox API. Run this LOCALLY,
after scripts/etrade_authorize.py, before trusting ETradeBrokerAdapter for anything
else. It only calls get_account() and get_quote() -- it NEVER places, previews, or
cancels an order.

Usage:
    # requires config/broker.yaml: mode: sandbox, and all 5 ETRADE_SANDBOX_* env
    # vars set (consumer key/secret, oauth token/secret, account id) -- see
    # .env.example and scripts/etrade_authorize.py
    python scripts/etrade_sandbox_check.py [SYMBOL]

If field names in the parsed account/quote output look wrong for your account type,
that's a real possibility (see the "not live-tested" note in broker/etrade.py) --
please report back what E*TRADE actually returns so parsing can be corrected.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from config_loader import load_config, ConfigError  # noqa: E402
from broker.etrade import ETradeAPIError, ETradeBrokerAdapter, ETradeSafetyError  # noqa: E402
from broker.etrade_auth import ETradeAuthError  # noqa: E402


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "AG"

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

    print("Connected. Fetching account info (read-only)...")
    try:
        account = adapter.get_account()
        print(f"  equity=${account.equity:,.2f}  cash=${account.cash:,.2f}  buying_power=${account.buying_power:,.2f}")
    except ETradeAPIError as e:
        print(f"  FAILED: {e} (status={e.status_code})\n  body: {e.body}", file=sys.stderr)
        return 1

    print(f"Fetching a quote for {symbol} (read-only)...")
    try:
        quote = adapter.get_quote(symbol)
        print(f"  bid={quote['bid']}  ask={quote['ask']}  last={quote['last']}  volume={quote['volume']}")
    except ETradeAPIError as e:
        print(f"  FAILED: {e} (status={e.status_code})\n  body: {e.body}", file=sys.stderr)
        return 1

    print()
    print("Read-only checks passed. No order was placed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
