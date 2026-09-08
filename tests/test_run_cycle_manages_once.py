"""Regression test for a latent bug that only surfaces above
max_concurrent_positions: 1 (the only value ever backtested): run_cycle() called
manage_open_positions() once per ticker holding a position, and that method
already loops over ALL open positions -- so with N concurrent positions each was
managed N times per cycle. Also pins the exits-before-entries ordering, so a
position closing on this bar frees its slot in the same cycle rather than that
depending on where the held ticker sorts in the universe."""

from datetime import datetime, timezone

from broker.paper import PaperBrokerAdapter
from config_loader import load_config
from data.market_data import InMemoryMarketDataProvider, Quote
from main import TradingBot
from models.bar import Bar
from reporting.journal import SignalJournal, TradeJournal


def make_bot(tmp_path, max_concurrent=3):
    config = load_config()
    config.risk["behavior"]["max_concurrent_positions"] = max_concurrent
    provider = InMemoryMarketDataProvider()
    broker = PaperBrokerAdapter(starting_equity=100_000)
    sj = SignalJournal(str(tmp_path / "signals.jsonl"))
    tj = TradeJournal(str(tmp_path / "trades.jsonl"))
    return TradingBot(config, provider, broker, sj, tj), provider


def seed(provider, symbols, now):
    for symbol, price in symbols:
        provider.push_bar(symbol, Bar(timestamp=now, open=price - 0.3, high=price + 0.2,
                                      low=price - 0.4, close=price, volume=100_000))
        provider.push_quote(symbol, Quote(bid=price - 0.01, ask=price, last=price, timestamp=now),
                            received_at=now)


def test_each_open_position_is_managed_exactly_once_per_cycle(tmp_path, monkeypatch):
    bot, provider = make_bot(tmp_path, max_concurrent=3)
    now = datetime(2026, 3, 2, 10, 30, tzinfo=timezone.utc)

    held = ["AG", "HL", "EXK"]
    seed(provider, [(t, 10.30) for t in held] + [("SIL", 50.30)], now)

    for ticker in held:
        bot.position_manager.open_position(
            ticker=ticker, benchmark="SIL", entry_time=now, entry_price=10.30,
            shares=100, stop_price=9.50, target_price=12.00,
            setup_type="VWAP_RECLAIM", setup_score=85.0,
        )
    assert bot.position_manager.open_position_count() == 3

    manage_calls = []
    real_manage = bot.position_manager.manage

    def counting_manage(ticker, *a, **k):
        manage_calls.append(ticker)
        return real_manage(ticker, *a, **k)

    monkeypatch.setattr(bot.position_manager, "manage", counting_manage)
    # Stub entries out -- this test is only about the management path.
    monkeypatch.setattr(bot, "evaluate_and_maybe_enter", lambda *a, **k: None)

    bot.run_cycle(now)

    # Each held position managed exactly once -- previously each was managed
    # three times (once per held ticker encountered in the universe loop).
    assert sorted(manage_calls) == sorted(held)


def test_entries_are_not_evaluated_for_tickers_already_held(tmp_path, monkeypatch):
    bot, provider = make_bot(tmp_path, max_concurrent=3)
    now = datetime(2026, 3, 2, 10, 30, tzinfo=timezone.utc)
    seed(provider, [("AG", 10.30), ("SIL", 50.30)], now)

    bot.position_manager.open_position(
        ticker="AG", benchmark="SIL", entry_time=now, entry_price=10.30,
        shares=100, stop_price=9.50, target_price=12.00,
        setup_type="VWAP_RECLAIM", setup_score=85.0,
    )

    evaluated = []
    monkeypatch.setattr(bot, "evaluate_and_maybe_enter", lambda ticker, when: evaluated.append(ticker))
    monkeypatch.setattr(bot, "manage_open_positions", lambda when: None)

    bot.run_cycle(now)

    assert "AG" not in evaluated
    assert len(evaluated) > 0  # the rest of the universe still gets evaluated
