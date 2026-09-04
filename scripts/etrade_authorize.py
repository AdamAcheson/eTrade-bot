#!/usr/bin/env python3
"""Run this LOCALLY on your own machine (needs a browser) to complete E*TRADE's
3-legged OAuth flow and obtain a sandbox access token/secret.

This cannot be run by an AI assistant on your behalf: step 2 requires you to log
into E*TRADE in a browser and read a one-time verifier code off the page.

Usage:
    export ETRADE_SANDBOX_CONSUMER_KEY=...      # from your E*TRADE developer app
    export ETRADE_SANDBOX_CONSUMER_SECRET=...
    python scripts/etrade_authorize.py

    (or put both in a local .env and `set -a; source .env; set +a` first)

What it does:
    1. Requests a temporary request token from E*TRADE.
    2. Prints a URL for you to open in a browser and log in.
    3. Prompts you to paste the verifier code E*TRADE shows you.
    4. Exchanges it for an access token + secret and prints them.

Nothing is written to disk by this script. Copy the printed values into your local
.env (see .env.example) as ETRADE_SANDBOX_OAUTH_TOKEN / ETRADE_SANDBOX_OAUTH_TOKEN_SECRET
-- never commit .env. E*TRADE access tokens expire at midnight US Eastern or after 2
hours of inactivity; re-run this script (or use ETradeOAuth.renew_access_token) when
that happens.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from config_loader import load_config, ConfigError  # noqa: E402
from broker.etrade_auth import ETradeAuthError, ETradeOAuth, load_consumer_credentials  # noqa: E402


def main() -> int:
    try:
        config = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    try:
        credentials = load_consumer_credentials(config.broker)
    except ETradeAuthError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    oauth = ETradeOAuth(credentials)

    print("Requesting a temporary request token from E*TRADE...")
    request_token = oauth.get_request_token()

    authorize_url = oauth.get_authorize_url(request_token.token)
    print()
    print("Open this URL in a browser and log in to your E*TRADE SANDBOX account:")
    print()
    print(f"    {authorize_url}")
    print()
    verifier = input("Paste the verification code E*TRADE shows you: ").strip()

    print("Exchanging verifier for an access token...")
    access_token = oauth.get_access_token(request_token, verifier)

    etrade_cfg = config.broker["etrade"]
    print()
    print("Success. Add these to your local .env (never commit it):")
    print()
    print(f"  {etrade_cfg['oauth_token_env']}={access_token.token}")
    print(f"  {etrade_cfg['oauth_token_secret_env']}={access_token.token_secret}")
    print()
    print("This access token expires at midnight US Eastern or after 2 hours of "
          "inactivity -- re-run this script when it does.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
