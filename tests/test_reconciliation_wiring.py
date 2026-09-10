"""End-to-end checks that reconciliation is actually consulted by TradingBot.

The unit tests in test_reconciliation.py prove the comparison is right; these
prove it is reached. The entry gate and the exit clamp are the two places where
getting this wrong costs real money against a real account, and both were
previously absent -- the bot never called broker.get_positions() at all.

Follows test_entry_opens_position.py in stubbing evaluate_ticker rather than
warming up real indicators: the path under test is signal -> reconciliation ->
order, not signal generation."""

from datetime import datetime, timezone

from broker.base import BrokerPosition, OrderSide
from broker.paper import PaperBrokerAdapter
from config_loader import load_config
from data.market_data import InMemoryMarketDataProvider, Quote
from main import TradingBot
from models.bar import Bar
from models.signal import Decision, Signal
from reporting.journal import SignalJournal, TradeJournal

TICKER = "AG"
NOW = datetime(2026, 3, 2, 9, 55, tzinfo=timezone.utc)


def make_bot(tmp_path):
    config = load_config()
    provider = InMemoryMarketDataProvider()
    broker = PaperBrokerAdapter(starting_equity=100_000)
    bot = TradingBot(config, provider, broker,
                     SignalJournal(str(tmp_path / "s.jsonl")),
                     TradeJournal(str(tmp_path / "t.jsonl")))
    benchmark = config.tickers[TICKER].benchmark
    for symbol, price in ((TICKER, 10.30), (benchmark, 50.30)):
        provider.push_bar(symbol, Bar(timestamp=NOW, open=price - 0.3, high=price + 0.2,
                                      low=price - 0.4, close=price, volume=100_000))
        provider.push_quote(symbol, Quote(bid=price - 0.01, ask=price, last=price,
                                          timestamp=NOW), received_at=NOW)
    return bot, provider, broker


def stub_entry_signal(bot, monkeypatch):
    benchmark = bot.config.tickers[TICKER].benchmark
    sig = Signal(
        timestamp=NOW, ticker=TICKER, benchmark=benchmark,
        price=10.30, bid=10.29, ask=10.30, spread=0.01,
        vwap=10.20, ema_9=10.20, ema_20=10.10, atr=0.15, atr_percent=1.45,
        relative_volume=1.5, opening_range_high=10.12, opening_range_low=9.95,
        benchmark_price=50.30, benchmark_vwap=50.0, benchmark_ema_9=50.2,
        benchmark_ema_20=50.0, setup_type="ORB_PULLBACK_CONTINUATION", setup_score=85.0,
        entry_candidate=True, entry_price=10.30, stop=10.10, target=10.70,
        risk_per_share=0.20, expected_reward=0.40, r_ratio=2.0,
        decision=Decision.ENTRY_CANDIDATE, rejection_reason=None,
    )
    monkeypatch.setattr("main.evaluate_ticker", lambda *a, **k: sig)


def test_entry_is_blocked_in_a_ticker_the_account_already_holds(tmp_path, monkeypatch):
    """The owner holds 500 AG. Buying more means the eventual sell comes out of one
    pool and FIFO disposes their lots, not the bot's."""
    bot, _, broker = make_bot(tmp_path)
    stub_entry_signal(bot, monkeypatch)
    broker._positions[TICKER] = BrokerPosition(ticker=TICKER, quantity=500, avg_price=9.00)

    bot.refresh_reconciliation()
    bot.evaluate_and_maybe_enter(TICKER, NOW)

    assert not bot.position_manager.has_open_position(TICKER)
    assert broker.get_positions()[TICKER].quantity == 500      # untouched


def test_entry_proceeds_normally_on_a_clean_account(tmp_path, monkeypatch):
    bot, _, broker = make_bot(tmp_path)
    stub_entry_signal(bot, monkeypatch)

    bot.refresh_reconciliation()
    bot.evaluate_and_maybe_enter(TICKER, NOW)

    assert bot.position_manager.has_open_position(TICKER)


def test_a_broker_that_cannot_be_reached_blocks_every_entry(tmp_path, monkeypatch):
    """Unverifiable is not the same as empty. Entering on an account whose contents
    cannot be read is precisely the case this exists to prevent."""
    bot, _, broker = make_bot(tmp_path)
    stub_entry_signal(bot, monkeypatch)
    monkeypatch.setattr(broker, "get_positions",
                        lambda: (_ for _ in ()).throw(ConnectionError("timeout")))

    bot.refresh_reconciliation()
    bot.evaluate_and_maybe_enter(TICKER, NOW)

    assert not bot.position_manager.has_open_position(TICKER)
    assert bot.reconciliation.is_blocked(TICKER)


def test_an_exit_never_sells_more_than_the_broker_holds(tmp_path, monkeypatch):
    """Selling 300 shares against a broker holding 100 does not flatten the
    position -- it opens a 200-share short, whose loss is unbounded."""
    bot, _, broker = make_bot(tmp_path)
    stub_entry_signal(bot, monkeypatch)
    bot.refresh_reconciliation()
    bot.evaluate_and_maybe_enter(TICKER, NOW)
    held = broker.get_positions()[TICKER].quantity
    assert held > 0

    # Simulate drift: the account really has 10 shares, the bot believes it has all.
    broker._positions[TICKER] = BrokerPosition(ticker=TICKER, quantity=10, avg_price=10.30)
    sold = bot._submit_exit_and_simulate(TICKER, held, 10.29)

    assert sold == 10
    sells = [o for o in broker._orders.values() if o.side == OrderSide.SELL]
    assert sells and all(o.quantity <= 10 for o in sells)


def test_no_sell_order_at_all_when_the_broker_holds_nothing(tmp_path, monkeypatch):
    bot, _, broker = make_bot(tmp_path)
    stub_entry_signal(bot, monkeypatch)
    bot.refresh_reconciliation()
    bot.evaluate_and_maybe_enter(TICKER, NOW)

    broker._positions.pop(TICKER, None)
    assert bot._submit_exit_and_simulate(TICKER, 300, 10.29) == 0
    assert not [o for o in broker._orders.values() if o.side == OrderSide.SELL]


def test_run_cycle_refreshes_before_acting(tmp_path, monkeypatch):
    """A stale reconciliation is worse than none, so run_cycle must refresh before
    the entry gate or the exit clamp reads it."""
    bot, _, broker = make_bot(tmp_path)
    stub_entry_signal(bot, monkeypatch)
    broker._positions[TICKER] = BrokerPosition(ticker=TICKER, quantity=500, avg_price=9.00)
    assert not bot.reconciliation.is_blocked(TICKER)     # not yet fetched

    bot.run_cycle(NOW)

    assert bot.reconciliation.is_blocked(TICKER)
    assert not bot.position_manager.has_open_position(TICKER)


def test_a_persistent_discrepancy_is_reported_once_not_every_cycle(tmp_path, capsys):
    bot, _, broker = make_bot(tmp_path)
    broker._positions[TICKER] = BrokerPosition(ticker=TICKER, quantity=500, avg_price=9.00)
    for _ in range(3):
        bot.refresh_reconciliation()
    out = capsys.readouterr().out
    assert out.count("did not open") == 1
