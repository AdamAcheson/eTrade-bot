from datetime import datetime, timezone

from data.etrade_market_data import ETradeMarketDataProvider


class FakeAdapter:
    def __init__(self, quotes):
        self._quotes = list(quotes)
        self.connected = True

    def get_quote(self, symbol):
        return self._quotes.pop(0)

    def is_connected(self):
        return self.connected


def q(bid, ask, last, volume):
    return {"bid": bid, "ask": ask, "last": last, "volume": volume}


def test_aggregates_ticks_within_same_window_into_one_bar():
    adapter = FakeAdapter([
        q(10.00, 10.01, 10.00, 100_000),
        q(10.05, 10.06, 10.05, 120_000),
        q(9.98, 9.99, 9.98, 150_000),
        q(10.10, 10.11, 10.10, 180_000),
    ])
    provider = ETradeMarketDataProvider(adapter, bar_interval_seconds=300)

    t0 = datetime(2026, 3, 2, 9, 30, 0, tzinfo=timezone.utc)
    provider.poll("AG", now=t0)
    provider.poll("AG", now=t0.replace(second=30))
    provider.poll("AG", now=t0.replace(minute=32))
    provider.poll("AG", now=t0.replace(minute=34, second=59))

    # still within the same 5-minute window [9:30, 9:35) -> no closed bar yet
    assert provider.get_state("AG").bars == []

    # next poll in the following window closes the first bar
    adapter._quotes.append(q(10.20, 10.21, 10.20, 200_000))
    provider.poll("AG", now=t0.replace(minute=35, second=1))

    bars = provider.get_state("AG").bars
    assert len(bars) == 1
    bar = bars[0]
    assert bar.open == 10.00
    assert bar.high == 10.10
    assert bar.low == 9.98
    assert bar.close == 10.10
    assert bar.volume == 80_000  # 180,000 - 100,000

    # the live quote reflects the most recent poll, not the closed bar
    state = provider.get_state("AG")
    assert state.quote.last == 10.20


def test_flush_forming_bar_closes_partial_bar():
    adapter = FakeAdapter([q(10.00, 10.01, 10.00, 100_000), q(10.05, 10.06, 10.05, 110_000)])
    provider = ETradeMarketDataProvider(adapter, bar_interval_seconds=300)
    t0 = datetime(2026, 3, 2, 15, 55, 0, tzinfo=timezone.utc)
    provider.poll("AG", now=t0)
    provider.poll("AG", now=t0.replace(second=30))

    assert provider.get_state("AG").bars == []
    provider.flush_forming_bar("AG")
    bars = provider.get_state("AG").bars
    assert len(bars) == 1
    assert bars[0].volume == 10_000


def test_is_stale_works_correctly_after_a_bar_closes():
    """Regression test: _close_bar previously called push_bar without received_at,
    silently defaulting to a naive datetime.utcnow() and clobbering the tz-aware
    timestamp push_quote had just set moments earlier in the same poll() call --
    causing is_stale() to raise "can't subtract offset-naive and offset-aware
    datetimes" the first time a bar closed during a live run."""
    adapter = FakeAdapter([
        q(10.00, 10.01, 10.00, 100_000),
        q(10.05, 10.06, 10.05, 120_000),
    ])
    provider = ETradeMarketDataProvider(adapter, bar_interval_seconds=300)
    t0 = datetime(2026, 3, 2, 9, 30, 0, tzinfo=timezone.utc)
    provider.poll("AG", now=t0)
    # crosses into the next 5-minute window -> triggers a bar close internally
    t1 = t0.replace(minute=35, second=1)
    provider.poll("AG", now=t1)

    assert provider.is_stale("AG", staleness_limit_seconds=30, now=t1) is False
    assert provider.is_stale("AG", staleness_limit_seconds=30, now=t1.replace(minute=40)) is True


def test_is_connected_delegates_to_adapter():
    adapter = FakeAdapter([])
    provider = ETradeMarketDataProvider(adapter)
    assert provider.is_connected() is True
    adapter.connected = False
    assert provider.is_connected() is False
