"""E*TRADE OAuth 1.0a (3-legged) authentication.

Endpoints below are taken directly from E*TRADE's published API documentation
(apisb.etrade.com/docs/api/authorization/*.html) and the endpoint URLs used by the
widely-used open-source `pyetrade` client, cross-checked against both -- nothing here
is guessed (spec section 36: "do not invent endpoints").

Important, easy-to-miss fact confirmed from the docs: the OAuth token endpoints
(request_token / authorize / access_token / renew / revoke) always live on
`api.etrade.com` / `us.etrade.com`, EVEN WHEN you intend to trade against the sandbox.
Only the actual trading/market-data API calls made afterward switch host between
`apisb.etrade.com` (sandbox) and `api.etrade.com` (production) -- that split happens
in `broker/etrade.py`, not here.

This flow requires a human with a browser (E*TRADE has no headless/sandbox-only
shortcut): step 2 below opens a page where the user logs in and is shown a one-time
verifier code. That cannot happen inside an unattended process, which is why this
module is driven by `scripts/etrade_authorize.py`, a script the user runs locally.

Credentials are only ever read from environment variables named in
config/broker.yaml -- never hard-coded, never logged, never written back to any file
this codebase controls.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Tuple

from requests_oauthlib import OAuth1Session

REQUEST_TOKEN_URL = "https://api.etrade.com/oauth/request_token"
AUTHORIZE_URL = "https://us.etrade.com/e/t/etws/authorize"
ACCESS_TOKEN_URL = "https://api.etrade.com/oauth/access_token"
RENEW_ACCESS_TOKEN_URL = "https://api.etrade.com/oauth/renew_access_token"
REVOKE_ACCESS_TOKEN_URL = "https://api.etrade.com/oauth/revoke_access_token"


class ETradeAuthError(Exception):
    pass


@dataclass(frozen=True)
class ConsumerCredentials:
    consumer_key: str
    consumer_secret: str


def load_consumer_credentials(broker_config: dict) -> ConsumerCredentials:
    """Reads the app-level consumer key/secret from the environment variable NAMES
    configured in broker.yaml (etrade.consumer_key_env / consumer_secret_env) --
    never from the config file or source code directly."""
    etrade_cfg = broker_config["etrade"]
    key_env = etrade_cfg["consumer_key_env"]
    secret_env = etrade_cfg["consumer_secret_env"]
    key = os.environ.get(key_env)
    secret = os.environ.get(secret_env)
    if not key or not secret:
        raise ETradeAuthError(
            f"Missing E*TRADE consumer credentials. Set the {key_env} and "
            f"{secret_env} environment variables (see .env.example)."
        )
    return ConsumerCredentials(consumer_key=key, consumer_secret=secret)


@dataclass(frozen=True)
class TokenPair:
    token: str
    token_secret: str


class ETradeOAuth:
    """Drives the 3-legged OAuth1 dance. Nothing in this class talks to
    apisb.etrade.com/api.etrade.com's trading or market-data endpoints -- only the
    OAuth token endpoints, which are the same regardless of sandbox vs production."""

    def __init__(self, credentials: ConsumerCredentials) -> None:
        self.credentials = credentials

    def get_request_token(self) -> TokenPair:
        session = OAuth1Session(
            self.credentials.consumer_key,
            client_secret=self.credentials.consumer_secret,
            callback_uri="oob",
        )
        response = session.fetch_request_token(REQUEST_TOKEN_URL)
        return TokenPair(token=response["oauth_token"], token_secret=response["oauth_token_secret"])

    def get_authorize_url(self, request_token: str) -> str:
        return f"{AUTHORIZE_URL}?key={self.credentials.consumer_key}&token={request_token}"

    def get_access_token(self, request_token_pair: TokenPair, verifier: str) -> TokenPair:
        session = OAuth1Session(
            self.credentials.consumer_key,
            client_secret=self.credentials.consumer_secret,
            resource_owner_key=request_token_pair.token,
            resource_owner_secret=request_token_pair.token_secret,
            verifier=verifier,
        )
        response = session.fetch_access_token(ACCESS_TOKEN_URL)
        return TokenPair(token=response["oauth_token"], token_secret=response["oauth_token_secret"])

    def renew_access_token(self, access_token_pair: TokenPair) -> None:
        session = self.build_session(access_token_pair)
        resp = session.get(RENEW_ACCESS_TOKEN_URL)
        if resp.status_code != 200:
            raise ETradeAuthError(f"Failed to renew access token: HTTP {resp.status_code}: {resp.text}")

    def revoke_access_token(self, access_token_pair: TokenPair) -> None:
        session = self.build_session(access_token_pair)
        resp = session.get(REVOKE_ACCESS_TOKEN_URL)
        if resp.status_code != 200:
            raise ETradeAuthError(f"Failed to revoke access token: HTTP {resp.status_code}: {resp.text}")

    def build_session(self, access_token_pair: TokenPair) -> OAuth1Session:
        """An OAuth1Session pre-signed with the access token, ready to call
        apisb.etrade.com or api.etrade.com trading/market-data endpoints."""
        return OAuth1Session(
            self.credentials.consumer_key,
            client_secret=self.credentials.consumer_secret,
            resource_owner_key=access_token_pair.token,
            resource_owner_secret=access_token_pair.token_secret,
        )


def load_access_token_pair(broker_config: dict) -> TokenPair:
    """Reads the per-session access token/secret (obtained via
    scripts/etrade_authorize.py) from the environment variable names configured in
    broker.yaml. E*TRADE access tokens expire at midnight US Eastern or after 2 hours
    of inactivity, so this pair needs to be refreshed (re-run the authorize script,
    or call ETradeOAuth.renew_access_token) periodically."""
    etrade_cfg = broker_config["etrade"]
    token_env = etrade_cfg["oauth_token_env"]
    secret_env = etrade_cfg["oauth_token_secret_env"]
    token = os.environ.get(token_env)
    secret = os.environ.get(secret_env)
    if not token or not secret:
        raise ETradeAuthError(
            f"Missing E*TRADE access token. Set {token_env} and {secret_env} "
            f"(run scripts/etrade_authorize.py to obtain them)."
        )
    return TokenPair(token=token, token_secret=secret)
