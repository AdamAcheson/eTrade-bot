"""Mocked tests for the E*TRADE adapter: no real network calls are made. A fake
OAuth1Session-like object is injected in place of ETradeOAuth.build_session's
result, and canned JSON responses stand in for what E*TRADE's sandbox would return
(shapes taken from the same docs cited in broker/etrade.py's module docstring)."""

import json as json_module

import pytest

from broker.base import OrderSide
from broker.etrade import ETradeAPIError, ETradeBrokerAdapter, ETradeSafetyError
from broker.etrade_auth import ETradeAuthError, ETradeOAuth


BROKER_CONFIG = {
    "etrade": {
        "environment": "sandbox",
        "consumer_key_env": "TEST_ETRADE_CONSUMER_KEY",
        "consumer_secret_env": "TEST_ETRADE_CONSUMER_SECRET",
        "oauth_token_env": "TEST_ETRADE_OAUTH_TOKEN",
        "oauth_token_secret_env": "TEST_ETRADE_OAUTH_TOKEN_SECRET",
        "account_id_env": "TEST_ETRADE_ACCOUNT_ID",
    }
}


class FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.content = json_module.dumps(body).encode() if body else b""
        self.text = json_module.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self):
        self.responses = {}  # (method, path) -> FakeResponse
        self.calls = []

    def set_response(self, method: str, path: str, status_code: int, body: dict):
        self.responses[(method, path)] = FakeResponse(status_code, body)

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        path = url.split("apisb.etrade.com", 1)[1]
        self.calls.append({"method": method, "path": path, "params": params, "json": json})
        key = (method, path)
        if key not in self.responses:
            raise AssertionError(f"no fake response registered for {key}; calls so far: {self.calls}")
        return self.responses[key]


@pytest.fixture
def env_credentials(monkeypatch):
    monkeypatch.setenv("TEST_ETRADE_CONSUMER_KEY", "ck")
    monkeypatch.setenv("TEST_ETRADE_CONSUMER_SECRET", "cs")
    monkeypatch.setenv("TEST_ETRADE_OAUTH_TOKEN", "tok")
    monkeypatch.setenv("TEST_ETRADE_OAUTH_TOKEN_SECRET", "toksec")


@pytest.fixture
def fake_session(monkeypatch, env_credentials):
    session = FakeSession()
    monkeypatch.setattr(ETradeOAuth, "build_session", lambda self, access_token_pair: session)
    return session


def make_adapter(broker_config=None):
    return ETradeBrokerAdapter(broker_config or BROKER_CONFIG)


# --- safety ------------------------------------------------------------------

def test_production_environment_is_rejected(env_credentials):
    bad_config = {"etrade": {**BROKER_CONFIG["etrade"], "environment": "production"}}
    with pytest.raises(ETradeSafetyError):
        ETradeBrokerAdapter(bad_config)


def test_missing_consumer_credentials_raises(monkeypatch):
    monkeypatch.delenv("TEST_ETRADE_CONSUMER_KEY", raising=False)
    monkeypatch.delenv("TEST_ETRADE_CONSUMER_SECRET", raising=False)
    with pytest.raises(ETradeAuthError):
        ETradeBrokerAdapter(BROKER_CONFIG)


def test_missing_access_token_raises(monkeypatch):
    monkeypatch.setenv("TEST_ETRADE_CONSUMER_KEY", "ck")
    monkeypatch.setenv("TEST_ETRADE_CONSUMER_SECRET", "cs")
    monkeypatch.delenv("TEST_ETRADE_OAUTH_TOKEN", raising=False)
    with pytest.raises(ETradeAuthError):
        ETradeBrokerAdapter(BROKER_CONFIG)


# --- account resolution --------------------------------------------------------

def test_get_account_parses_balance(fake_session, monkeypatch):
    monkeypatch.setenv("TEST_ETRADE_ACCOUNT_ID", "12345678")
    fake_session.set_response("GET", "/v1/accounts/list.json", 200, {
        "AccountListResponse": {"Accounts": {"Account": [
            {"accountId": "12345678", "accountIdKey": "key-abc", "accountStatus": "ACTIVE"},
        ]}}
    })
    fake_session.set_response("GET", "/v1/accounts/key-abc/balance.json", 200, {
        "BalanceResponse": {
            "Computed": {
                "RealTimeValues": {"totalAccountValue": 100000.0},
                "cashAvailableForInvestment": 50000.0,
                "cashBuyingPower": 100000.0,
            }
        }
    })
    adapter = make_adapter()
    account = adapter.get_account()
    assert account.equity == pytest.approx(100000.0)
    assert account.cash == pytest.approx(50000.0)
    assert account.buying_power == pytest.approx(100000.0)


def test_resolve_account_id_key_disambiguates_by_configured_id(fake_session, monkeypatch):
    monkeypatch.setenv("TEST_ETRADE_ACCOUNT_ID", "222")
    fake_session.set_response("GET", "/v1/accounts/list.json", 200, {
        "AccountListResponse": {"Accounts": {"Account": [
            {"accountId": "111", "accountIdKey": "key-111", "accountStatus": "ACTIVE"},
            {"accountId": "222", "accountIdKey": "key-222", "accountStatus": "ACTIVE"},
        ]}}
    })
    adapter = make_adapter()
    assert adapter._resolve_account_id_key() == "key-222"


def test_resolve_account_id_key_raises_when_ambiguous(fake_session, monkeypatch):
    monkeypatch.delenv("TEST_ETRADE_ACCOUNT_ID", raising=False)
    fake_session.set_response("GET", "/v1/accounts/list.json", 200, {
        "AccountListResponse": {"Accounts": {"Account": [
            {"accountId": "111", "accountIdKey": "key-111", "accountStatus": "ACTIVE"},
            {"accountId": "222", "accountIdKey": "key-222", "accountStatus": "ACTIVE"},
        ]}}
    })
    adapter = make_adapter()
    with pytest.raises(ETradeAPIError):
        adapter._resolve_account_id_key()


# --- orders --------------------------------------------------------------------

def _with_account(fake_session):
    fake_session.set_response("GET", "/v1/accounts/list.json", 200, {
        "AccountListResponse": {"Accounts": {"Account": [
            {"accountId": "111", "accountIdKey": "key-111", "accountStatus": "ACTIVE"},
        ]}}
    })


def test_submit_limit_order_previews_then_places(fake_session, monkeypatch):
    monkeypatch.delenv("TEST_ETRADE_ACCOUNT_ID", raising=False)
    _with_account(fake_session)
    fake_session.set_response("POST", "/v1/accounts/key-111/orders/preview.json", 200, {
        "PreviewOrderResponse": {"PreviewIds": [{"previewId": 987}]}
    })
    fake_session.set_response("POST", "/v1/accounts/key-111/orders/place.json", 200, {
        "PlaceOrderResponse": {"OrderIds": {"orderId": 555}}
    })

    adapter = make_adapter()
    order = adapter.submit_limit_order("AG", OrderSide.BUY, 100, 10.40)

    assert order.order_id == "555"
    assert order.ticker == "AG"
    assert order.side == OrderSide.BUY
    assert order.quantity == 100
    assert order.limit_price == pytest.approx(10.40)

    preview_call = next(c for c in fake_session.calls if c["path"].endswith("/orders/preview.json"))
    body = preview_call["json"]["PreviewOrderRequest"]
    assert body["Order"][0]["priceType"] == "LIMIT"
    assert body["Order"][0]["limitPrice"] == 10.40
    assert body["Order"][0]["Instrument"][0]["orderAction"] == "BUY"
    assert body["Order"][0]["Instrument"][0]["quantity"] == 100

    place_call = next(c for c in fake_session.calls if c["path"].endswith("/orders/place.json"))
    assert place_call["json"]["PlaceOrderRequest"]["PreviewIds"] == [{"previewId": 987}]


def test_cancel_order_sends_order_id(fake_session, monkeypatch):
    monkeypatch.delenv("TEST_ETRADE_ACCOUNT_ID", raising=False)
    _with_account(fake_session)
    fake_session.set_response("PUT", "/v1/accounts/key-111/orders/cancel.json", 200, {"CancelOrderResponse": {}})

    adapter = make_adapter()
    adapter.cancel_order("555")

    cancel_call = fake_session.calls[-1]
    assert cancel_call["json"] == {"CancelOrderRequest": {"orderId": 555}}


def test_get_quote_parses_fields(fake_session, monkeypatch):
    monkeypatch.delenv("TEST_ETRADE_ACCOUNT_ID", raising=False)
    fake_session.set_response("GET", "/v1/market/quote/AG.json", 200, {
        "QuoteResponse": {"QuoteData": [{"All": {"bid": 10.39, "ask": 10.40, "lastTrade": 10.40, "totalVolume": 500000}}]}
    })
    adapter = make_adapter()
    quote = adapter.get_quote("AG")
    assert quote == {"bid": 10.39, "ask": 10.40, "last": 10.40, "volume": 500000.0}


def test_is_connected_false_on_api_error(fake_session, monkeypatch):
    monkeypatch.delenv("TEST_ETRADE_ACCOUNT_ID", raising=False)
    # No response registered for accounts/list.json -> FakeSession raises
    # AssertionError, not ETradeAPIError, so simulate a 500 instead.
    fake_session.set_response("GET", "/v1/accounts/list.json", 500, {"Error": {"message": "boom"}})
    adapter = make_adapter()
    assert adapter.is_connected() is False
