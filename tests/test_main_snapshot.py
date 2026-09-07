"""Regression test for a real bug: TradingBot._snapshot_for used to pass ALL
accumulated bars for a ticker into compute_snapshot, not just the current session's
bars. state.bars never resets at a day boundary (by design, so other consumers keep
full history), so opening range / VWAP / EMA / RVOL would silently blend a prior
day's bars into "today" the moment the bot (or a backtest) runs past midnight."""

from datetime import datetime, timedelta, timezone

from broker.paper import PaperBrokerAdapter
from config_loader import load_config
from data.market_data import InMemoryMarketDataProvider, Quote
from main import TradingBot
from models.bar import Bar
from reporting.journal import SignalJournal, TradeJournal


def make_bot(tmp_path):
    config = load_config()
    provider = InMemoryMarketDataProvider()
    broker = PaperBrokerAdapter(starting_equity=100_000)
    sj = SignalJournal(str(tmp_path / "signals.jsonl"))
    tj = TradeJournal(str(tmp_path / "trades.jsonl"))
    return TradingBot(config, provider, broker, sj, tj), provider


def test_snapshot_only_uses_todays_bars_not_prior_days(tmp_path):
    bot, provider = make_bot(tmp_path)

    day1 = datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc)
    # Day 1: a wide, high-priced opening range (10.00-10.60).
    provider.push_bar("AG", Bar(timestamp=day1, open=10.00, high=10.50, low=10.00, close=10.30, volume=100_000))
    provider.push_bar("AG", Bar(
        timestamp=day1 + timedelta(minutes=5), open=10.30, high=10.60, low=10.20, close=10.40, volume=100_000
    ))

    day2 = datetime(2026, 3, 3, 9, 30, tzinfo=timezone.utc)
    # Day 2: a completely different, much lower opening range (5.00-5.20).
    provider.push_bar("AG", Bar(timestamp=day2, open=5.00, high=5.10, low=5.00, close=5.05, volume=50_000))
    provider.push_bar("AG", Bar(
        timestamp=day2 + timedelta(minutes=5), open=5.05, high=5.20, low=5.00, close=5.10, volume=50_000
    ))
    provider.push_quote("AG", Quote(bid=5.09, ask=5.10, last=5.10, timestamp=day2), received_at=day2)

    snapshot = bot._snapshot_for("AG", day2 + timedelta(minutes=6))

    assert snapshot is not None
    # If day 1's bars leaked in, opening_range_high would be 10.60 (day 1's high),
    # not day 2's own 5.20.
    assert snapshot.opening_range_high == 5.20
    assert snapshot.opening_range_low == 5.00
    assert snapshot.last_price == 5.10
