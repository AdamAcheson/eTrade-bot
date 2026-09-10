"""Tests for reconciling the bot's position bookkeeping against the broker's.

Nothing in the bot called BrokerInterface.get_positions() before this: it assumed
the trading account contained nothing but what it had put there. Against a real
account that assumption fails in two directions, and both are expensive --
see src/positions/reconciliation.py for why."""

from positions.reconciliation import Reconciliation, reconcile, sellable_shares


class Pos:
    def __init__(self, quantity):
        self.quantity = quantity


def test_a_clean_account_reports_nothing():
    r = reconcile({"AG": Pos(100)}, {"AG": 100})
    assert not r.has_discrepancy
    assert not r.is_blocked("AG")
    assert r.describe() == []


def test_shares_the_bot_never_bought_block_the_ticker():
    """The owner already holds AG. An exit would sell from the combined pool, and
    FIFO disposes their oldest lots first."""
    r = reconcile({"AG": Pos(500)}, {})
    assert r.externally_held == {"AG": 500}
    assert r.is_blocked("AG")
    assert "did not open" in r.describe()[0]


def test_a_partial_overlap_blocks_on_the_excess_only():
    # Bot opened 200; broker shows 700, so 500 are the owner's.
    r = reconcile({"AG": Pos(700)}, {"AG": 200})
    assert r.externally_held == {"AG": 500}
    assert r.is_blocked("AG")


def test_broker_holding_fewer_shares_is_flagged_not_blocked():
    """A fill that never happened. Entries aren't the danger here -- selling 300
    shares that don't exist is, because it opens a short."""
    r = reconcile({"AG": Pos(100)}, {"AG": 300})
    assert r.short_at_broker == {"AG": (300, 100)}
    assert not r.is_blocked("AG")
    assert "clamped" in r.describe()[0]


def test_a_position_the_broker_has_never_heard_of():
    r = reconcile({}, {"AG": 300})
    assert r.short_at_broker == {"AG": (300, 0)}


def test_a_short_balance_is_always_foreign():
    """The bot only ever BUYs to enter, so a negative balance cannot be its own."""
    r = reconcile({"AG": Pos(-400)}, {})
    assert r.is_blocked("AG")


def test_untouched_tickers_are_ignored():
    r = reconcile({"AG": Pos(100)}, {"AG": 100, "CDE": 0})
    assert not r.has_discrepancy


def test_multiple_tickers_are_reported_independently():
    r = reconcile({"AG": Pos(500), "CDE": Pos(50)}, {"CDE": 100, "HL": 10})
    assert r.is_blocked("AG") and not r.is_blocked("CDE")
    assert r.short_at_broker == {"CDE": (100, 50), "HL": (10, 0)}
    assert len(r.describe()) == 3


# --- sellable_shares --------------------------------------------------------

def test_sell_is_clamped_to_what_the_broker_confirms():
    assert sellable_shares("AG", 300, {"AG": Pos(100)}) == 100


def test_selling_what_the_broker_does_not_have_sends_nothing():
    assert sellable_shares("AG", 300, {}) == 0
    assert sellable_shares("AG", 300, {"AG": Pos(0)}) == 0


def test_a_normal_exit_is_not_clamped():
    assert sellable_shares("AG", 300, {"AG": Pos(300)}) == 300


def test_sell_never_goes_negative_against_a_short_balance():
    assert sellable_shares("AG", 300, {"AG": Pos(-100)}) == 0


def test_an_empty_reconciliation_blocks_nothing():
    assert not Reconciliation().has_discrepancy
    assert not Reconciliation().is_blocked("AG")
