"""broker/ibkr.py against a fake TWS. What must hold: it can only ever run on a paper
account; it reports what actually FILLED, never what was asked for; nothing it
places is left resting unwatched; and the bot books positions and exits from those
real fills."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("ib_async")

from broker.base import OrderSide, OrderStatus
from broker.ibkr import IBKRPaperBrokerAdapter, IBKRSafetyError, _round_limit, check_paper_settings
from config_loader import ConfigError, load_config

CFG = {"ibkr": {"host": "127.0.0.1", "port": 7497, "client_id": 21,
                "fill_timeout_seconds": 10, "cancel_timeout_seconds": 5}}


class FakeTrade:
    def __init__(self, contract, order):
        self.contract = contract
        self.order = order
        self.orderStatus = SimpleNamespace(status="Submitted", filled=0.0, avgFillPrice=0.0)
        self.fills = []

    def isDone(self):
        return self.orderStatus.status in ("Filled", "Cancelled", "ApiCancelled", "Inactive")


class FakeIB:
    """`plan` lists, per order placed, how many shares fill (None = all of them) and
    at what price (None = the limit)."""

    def __init__(self, accounts=("DU1234567",), plan=None, positions=()):
        self.accounts = list(accounts)
        self.plan = list(plan or [])
        self._positions = list(positions)
        self.placed, self.cancelled = [], []
        self.connect_kwargs = None
        self.connected = False
        self.next_id = 1
        self._trades = []

    def connect(self, host, port, **kw):
        self.connect_kwargs = dict(host=host, port=port, **kw)
        self.connected = True

    def disconnect(self):
        self.connected = False

    def isConnected(self):
        return self.connected

    def managedAccounts(self):
        return self.accounts

    def qualifyContracts(self, c):
        return [SimpleNamespace(symbol=c.symbol, conId=1, secType="STK")]

    def placeOrder(self, contract, order):
        existing = [t for t in self._trades if t.order is order]
        if existing:                       # a modify re-sends the same order object
            return existing[0]
        order.orderId = self.next_id
        self.next_id += 1
        trade = FakeTrade(contract, order)
        self._trades.append(trade)
        self.placed.append(order)
        qty, price = self.plan.pop(0) if self.plan else (None, None)
        qty = order.totalQuantity if qty is None else qty
        if qty > 0:
            px = order.lmtPrice if price is None else price
            trade.orderStatus.filled = float(qty)
            trade.orderStatus.avgFillPrice = px
            trade.fills = [SimpleNamespace(commissionReport=SimpleNamespace(commission=1.0))]
            if qty >= order.totalQuantity:
                trade.orderStatus.status = "Filled"
        return trade

    def cancelOrder(self, order):
        self.cancelled.append(order.orderId)
        for t in self._trades:
            if t.order is order and not t.isDone():
                t.orderStatus.status = "Cancelled"

    def trades(self):
        return list(self._trades)

    def openTrades(self):
        return [t for t in self._trades if not t.isDone()]

    def reqPositions(self):
        return self._positions

    def accountSummary(self, account=""):
        return [SimpleNamespace(tag="NetLiquidation", value="5000", currency="USD"),
                SimpleNamespace(tag="TotalCashValue", value="5000", currency="USD"),
                SimpleNamespace(tag="BuyingPower", value="20000", currency="USD")]

    def sleep(self, s):
        pass


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _adapter(ib=None, cfg=CFG):
    clock = Clock()
    ib = ib or FakeIB()
    return IBKRPaperBrokerAdapter(cfg, ib=ib, sleep=clock.sleep, clock=clock), ib


# --- it can only ever be a paper account --------------------------------------

@pytest.mark.parametrize("port", [7496, 4001, 1234, None])
def test_non_paper_ports_are_refused(port):
    with pytest.raises(IBKRSafetyError):
        check_paper_settings({"host": "127.0.0.1", "port": port})


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "tws.example.com"])
def test_non_local_hosts_are_refused(host):
    with pytest.raises(IBKRSafetyError):
        check_paper_settings({"host": host, "port": 7497})


def test_a_live_account_is_refused_and_disconnected():
    ib = FakeIB(accounts=("U7654321",))
    with pytest.raises(IBKRSafetyError, match="paper"):
        _adapter(ib)
    assert not ib.connected


def test_a_live_account_alongside_a_paper_one_is_refused():
    ib = FakeIB(accounts=("DU1234567", "U7654321"))
    with pytest.raises(IBKRSafetyError):
        _adapter(ib)


def test_no_accounts_is_refused():
    with pytest.raises(IBKRSafetyError):
        _adapter(FakeIB(accounts=()))


def test_configured_account_must_match():
    cfg = {"ibkr": {**CFG["ibkr"], "account_id": "DU9999999"}}
    with pytest.raises(IBKRSafetyError):
        _adapter(FakeIB(), cfg)


def test_connects_with_orders_enabled_to_the_configured_port():
    _, ib = _adapter()
    assert ib.connect_kwargs["readonly"] is False
    assert ib.connect_kwargs["port"] == 7497 and ib.connect_kwargs["clientId"] == 21


def test_every_order_carries_the_paper_account_and_is_a_day_limit():
    a, ib = _adapter()
    a.submit_limit_order("AG", OrderSide.BUY, 10, 18.70)
    o = ib.placed[0]
    assert o.account == "DU1234567" and o.orderType == "LMT" and o.tif == "DAY"
    assert o.outsideRth is False


def _write_config(tmp_path, broker_yaml):
    import shutil
    d = tmp_path / "config"
    shutil.copytree(load_config().config_dir, d)
    (d / "broker.yaml").write_text(broker_yaml)
    return str(d)


def test_config_loader_accepts_ibkr_paper(tmp_path):
    d = _write_config(tmp_path, "mode: ibkr_paper\nibkr:\n  host: 127.0.0.1\n  port: 7497\n")
    assert load_config(d).broker["mode"] == "ibkr_paper"


def test_config_loader_refuses_the_live_port(tmp_path):
    d = _write_config(tmp_path, "mode: ibkr_paper\nibkr:\n  host: 127.0.0.1\n  port: 7496\n")
    with pytest.raises(ConfigError, match="paper"):
        load_config(d)


def test_shipped_config_is_still_the_simulator():
    assert load_config().broker["mode"] == "paper"


# --- it reports what actually filled ----------------------------------------------

def test_full_fill():
    a, _ = _adapter(FakeIB(plan=[(None, 18.69)]))
    o = a.submit_limit_order("AG", OrderSide.BUY, 100, 18.70)
    assert o.status == OrderStatus.FILLED and o.filled_quantity == 100
    assert o.avg_fill_price == 18.69 and o.commission == 1.0


def test_no_fill_is_cancelled_not_left_resting():
    a, ib = _adapter(FakeIB(plan=[(0, None)]))
    o = a.submit_limit_order("AG", OrderSide.BUY, 100, 18.70)
    assert o.status == OrderStatus.CANCELLED and o.filled_quantity == 0
    assert ib.cancelled == [int(o.order_id)]
    assert a.get_open_orders() == []


def test_partial_fill_reports_the_part_and_cancels_the_rest():
    a, ib = _adapter(FakeIB(plan=[(40, 18.70)]))
    o = a.submit_limit_order("AG", OrderSide.BUY, 100, 18.70)
    assert o.filled_quantity == 40 and o.status == OrderStatus.CANCELLED
    assert len(ib.cancelled) == 1


@pytest.mark.parametrize("price,side,expected", [
    (18.701, OrderSide.BUY, 18.71), (18.709, OrderSide.SELL, 18.70),
    (18.70, OrderSide.BUY, 18.70), (18.70, OrderSide.SELL, 18.70),
    (18.7000000001, OrderSide.BUY, 18.70)])
def test_limits_are_whole_cents_rounded_toward_filling(price, side, expected):
    assert _round_limit(price, side) == expected


def test_buying_power_is_cash_not_margin():
    a, _ = _adapter()
    acct = a.get_account()
    assert acct.cash == 5000 and acct.buying_power == 5000 and acct.equity == 5000


def test_positions_are_this_accounts_stocks_only():
    pos = [SimpleNamespace(account="DU1234567", contract=SimpleNamespace(symbol="AG", secType="STK"),
                           position=100.0, avgCost=18.7),
           SimpleNamespace(account="DU1234567", contract=SimpleNamespace(symbol="HL", secType="STK"),
                           position=0.0, avgCost=5.0),
           SimpleNamespace(account="DU0000000", contract=SimpleNamespace(symbol="CDE", secType="STK"),
                           position=50.0, avgCost=9.0)]
    a, _ = _adapter(FakeIB(positions=pos))
    got = a.get_positions()
    assert list(got) == ["AG"] and got["AG"].quantity == 100


def test_replace_modifies_the_same_order():
    a, ib = _adapter(FakeIB(plan=[(0, None)]))
    a.fill_timeout = -1                       # return immediately, leave it working
    a.cancel_timeout = -1
    ib.cancelOrder = lambda order: None       # and do not cancel it
    o = a.submit_limit_order("AG", OrderSide.BUY, 10, 9.00)
    r = a.replace_order(o.order_id, 9.105)
    assert r.order_id == o.order_id and r.limit_price == 9.11
    assert len(ib.placed) == 1


# --- the bot books real fills -----------------------------------------------------

def _bot(tmp_path, ib):
    from data.market_data import InMemoryMarketDataProvider
    from main import TradingBot
    from reporting.journal import SignalJournal, TradeJournal
    config = load_config()
    adapter, _ = _adapter(ib)
    bot = TradingBot(config, InMemoryMarketDataProvider(), adapter,
                     SignalJournal(str(tmp_path / "s.jsonl")), TradeJournal(str(tmp_path / "t.jsonl")))
    return bot


def _enter(bot, monkeypatch, ticker, now, price=10.30):
    from test_exposure_cap import _enter as enter
    enter(bot, bot.data_provider, monkeypatch, ticker, now, price=price)


NOW = datetime(2026, 3, 2, 14, 55, tzinfo=timezone.utc)


def test_partial_entry_opens_only_the_filled_shares(tmp_path, monkeypatch):
    bot = _bot(tmp_path, FakeIB(plan=[(40, 10.29)]))
    t = bot.config.auto_tradeable_universe()[0]
    _enter(bot, monkeypatch, t, NOW)
    pos = bot.position_manager.get_position(t)
    assert pos.shares == 40 and pos.entry_price == 10.29
    assert bot.risk_manager.purchases_today == pytest.approx(40 * 10.29)


def test_unfilled_entry_opens_nothing_and_frees_the_ticker(tmp_path, monkeypatch):
    bot = _bot(tmp_path, FakeIB(plan=[(0, None), (None, None)]))
    t = bot.config.auto_tradeable_universe()[0]
    _enter(bot, monkeypatch, t, NOW)
    assert not bot.position_manager.has_open_position(t)
    assert not bot.position_manager.has_pending_order(t)
    assert bot.risk_manager.purchases_today == 0
    _enter(bot, monkeypatch, t, NOW)            # a later signal can still enter
    assert bot.position_manager.has_open_position(t)


def _held(ticker, qty):
    return [SimpleNamespace(account="DU1234567", contract=SimpleNamespace(symbol=ticker, secType="STK"),
                            position=float(qty), avgCost=10.3)]


def test_exit_retries_below_the_bid_and_books_the_real_fill(tmp_path, monkeypatch, capsys):
    ib = FakeIB()
    bot = _bot(tmp_path, ib)
    t = bot.config.auto_tradeable_universe()[0]
    _enter(bot, monkeypatch, t, NOW)
    shares = bot.position_manager.get_position(t).shares
    ib._positions = _held(t, shares)
    ib.plan = [(0, None), (shares, 10.21)]     # first sell misses, second fills
    sold = bot._submit_exit_and_simulate(t, shares, 10.25)
    assert sold == shares
    sells = [o for o in ib.placed if o.action == "SELL"]
    assert [o.lmtPrice for o in sells] == [10.25, 10.22]    # 10.25 * (1 - 0.25%) = 10.224 -> 10.22
    assert bot._exit_price(10.00) == pytest.approx(10.21)
    assert "EXIT INCOMPLETE" not in capsys.readouterr().out


def test_exit_that_never_fills_is_reported(tmp_path, monkeypatch, capsys):
    ib = FakeIB()
    bot = _bot(tmp_path, ib)
    t = bot.config.auto_tradeable_universe()[0]
    _enter(bot, monkeypatch, t, NOW)
    shares = bot.position_manager.get_position(t).shares
    ib._positions = _held(t, shares)
    ib.plan = [(0, None)] * 3
    assert bot._submit_exit_and_simulate(t, shares, 10.25) == 0
    assert len([o for o in ib.placed if o.action == "SELL"]) == 3
    assert "EXIT INCOMPLETE" in capsys.readouterr().out
    assert bot._exit_price(10.00) == 10.00


def test_simulator_exits_are_unchanged(tmp_path, monkeypatch):
    """Backtests run on the simulator; their exit prices must not move."""
    from test_exposure_cap import _bot as sim_bot
    bot, provider = sim_bot(tmp_path, equity=5000)
    t = bot.config.auto_tradeable_universe()[0]
    from test_exposure_cap import _enter as enter
    enter(bot, provider, monkeypatch, t, NOW)
    bot._submit_exit_and_simulate(t, bot.position_manager.get_position(t).shares, 10.25)
    assert bot._exit_price(9.99) == 9.99


# --- scripts/ibkr_paper_order_test.py ---------------------------------------------

def _script():
    import importlib.util, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "ibkr_paper_order_test", os.path.join(root, "scripts", "ibkr_paper_order_test.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ScriptIB(FakeIB):
    """Holds what it fills, so the script's before/after position check is real."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.errorEvent = _Event()
        self.qty = 0

    def placeOrder(self, contract, order):
        trade = super().placeOrder(contract, order)
        if trade.orderStatus.status == "Filled":
            self.qty += int(order.totalQuantity) * (1 if order.action == "BUY" else -1)
        return trade

    def reqPositions(self):
        return _held("AG", self.qty) if self.qty else []

    def reqHistoricalData(self, contract, **kw):
        return [SimpleNamespace(close=18.71)]


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self


def _run_script(ib, answer="YES"):
    mod = _script()
    clock = Clock()
    lines = []
    code = mod.run(lambda: IBKRPaperBrokerAdapter(CFG, ib=ib, sleep=clock.sleep, clock=clock),
                   "AG", ask=lambda _: answer, out=lines.append)
    return code, "\n".join(lines)


def test_script_happy_path():
    ib = ScriptIB(plan=[(0, None), (None, 18.72), (None, 18.70)])
    code, text = _run_script(ib)
    assert code == 0, text
    assert "order connection works" in text
    buy_far, buy, sell = ib.placed
    assert buy_far.lmtPrice == 9.35 and buy.lmtPrice == 19.09 and sell.lmtPrice == 18.33
    assert all(o.totalQuantity == 1 for o in ib.placed)
    assert ib.qty == 0 and not ib.connected


def test_script_places_nothing_without_yes():
    ib = ScriptIB()
    code, text = _run_script(ib, answer="yes")
    assert code == 0 and ib.placed == [] and "Aborted" in text


def test_script_refuses_a_live_account():
    code, text = _run_script(ScriptIB(accounts=("U7654321",)))
    assert code == 2 and "REFUSED" in text


def test_script_flags_a_share_left_behind():
    ib = ScriptIB(plan=[(0, None), (None, None), (0, None)])   # the sell never fills
    code, text = _run_script(ib)
    assert code == 1
    assert "Sell the extra AG share in TWS" in text


def test_no_resend_on_a_broker_whose_fills_arrive_later(tmp_path, monkeypatch):
    """E*TRADE returns PENDING. Re-sending then could sell the same shares twice."""
    ib = FakeIB()
    bot = _bot(tmp_path, ib)
    t = bot.config.auto_tradeable_universe()[0]
    _enter(bot, monkeypatch, t, NOW)
    shares = bot.position_manager.get_position(t).shares
    ib._positions = _held(t, shares)
    ib.plan = [(0, None)] * 3
    monkeypatch.setattr(type(bot.broker), "reports_final_fills", False)
    bot._submit_exit_and_simulate(t, shares, 10.25)
    assert len([o for o in ib.placed if o.action == "SELL"]) == 1


def test_script_ignores_orders_left_over_from_an_earlier_run():
    """2026-09-24: a TWS pop-up held the first run's orders, so a 1-share buy at half
    price was still open during the second run and was counted as that run's failure."""
    from ib_async import LimitOrder
    ib = ScriptIB(plan=[(0, None), (0, None), (None, 18.72), (None, 18.70)])
    stale = LimitOrder("BUY", 1, 9.32)
    stale_trade = ib.placeOrder(SimpleNamespace(symbol="AG"), stale)
    stale_trade.orderStatus.status = "Submitted"   # still resting
    ib.cancelOrder = lambda order: [t for t in ib._trades if t.order is order and t is not stale_trade
                                    and setattr(t.orderStatus, "status", "Cancelled")]
    code, text = _run_script(ib)
    assert "1 order(s) were already open before this test" in text
    assert code == 0, text


# --- scripts/ibkr_paper_flatten.py -----------------------------------------------

def _flatten():
    import importlib.util, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "ibkr_paper_flatten", os.path.join(root, "scripts", "ibkr_paper_flatten.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FlattenIB(ScriptIB):
    """Like TWS for orders placed by ANOTHER client id: a global cancel cancels them
    at TWS, but no status update reaches this connection, so its cached copy still
    reads "Submitted". Only a fresh reqAllOpenOrders shows the truth."""

    def reqAllOpenOrders(self):
        return [t for t in self._trades if not t.isDone() and not getattr(t, "gone", False)]

    def reqGlobalCancel(self):
        for t in self._trades:
            t.gone = True


def _run_flatten(ib, answer="YES"):
    clock = Clock()
    lines = []
    code = _flatten().run(lambda: IBKRPaperBrokerAdapter(CFG, ib=ib, sleep=clock.sleep, clock=clock),
                          ask=lambda _: answer, out=lines.append)
    return code, "\n".join(lines)


def _leave_behind(ib):
    """The state the pop-up left: 1 AG share held and a half-price buy still open."""
    from ib_async import LimitOrder
    ib.qty = 1
    ib.plan = [(0, None)]
    t = ib.placeOrder(SimpleNamespace(symbol="AG"), LimitOrder("BUY", 1, 9.32))
    t.orderStatus.status = "Submitted"


def test_flatten_cancels_orders_and_sells_positions():
    ib = FlattenIB()
    _leave_behind(ib)
    code, text = _run_flatten(ib)
    assert code == 0, text
    assert "account is clean" in text
    assert ib.qty == 0 and ib.reqAllOpenOrders() == []
    assert len(ib.openTrades()) == 1          # the stale cached copy the old check trusted
    sell = ib.placed[-1]
    assert sell.action == "SELL" and sell.orderType == "MKT" and sell.account == "DU1234567"


def test_flatten_changes_nothing_without_yes():
    ib = FlattenIB()
    _leave_behind(ib)
    code, text = _run_flatten(ib, answer="no")
    assert "Aborted" in text and ib.qty == 1 and len(ib.reqAllOpenOrders()) == 1


def test_flatten_refuses_a_live_account():
    code, text = _run_flatten(FlattenIB(accounts=("U7654321",)))
    assert code == 2 and "REFUSED" in text


def test_flatten_on_a_clean_account_does_nothing():
    ib = FlattenIB()
    code, text = _run_flatten(ib)
    assert code == 0 and "Nothing to clean up" in text and ib.placed == []
