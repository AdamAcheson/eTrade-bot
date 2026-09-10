"""Tests for the ratcheting trailing stop.

Motivation, from the 183-day backtest: 23 of 96 trades exited for exactly $0.00
after reaching a median 1.12% favorable excursion. breakeven_trigger_r moves the
stop to the entry price once and never again, so any trade that ran up and gave
it back closed flat -- while occupying the single position slot that ~90% of
qualifying signals are turned away for."""

from datetime import datetime, timedelta

import pytest

from models.trade import ExitReason
from positions.position_manager import PositionManager

T0 = datetime(2026, 3, 2, 10, 0)


def open_position(mgr, entry=100.0, stop=99.0, target=103.0):
    mgr.mark_order_pending("AG")
    return mgr.open_position(
        ticker="AG", benchmark="SIL", entry_time=T0, entry_price=entry,
        shares=100, stop_price=stop, target_price=target,
        setup_type="VWAP_RECLAIM", setup_score=80.0,
    )


def manage(mgr, price, minutes=5, **kw):
    params = dict(
        breakeven_trigger_r=1.0, partial_exit_enabled=False,
        partial_exit_trigger_r=1.5, partial_exit_sell_fraction=0.35,
    )
    params.update(kw)
    return mgr.manage("AG", current_price=price, current_time=T0 + timedelta(minutes=minutes), **params)


@pytest.fixture
def mgr():
    return PositionManager(max_concurrent_positions=1)


def test_disabled_by_default_stop_parks_at_entry(mgr):
    """The behavior being replaced: +1R moves the stop to entry, and it stays there
    no matter how far the trade runs."""
    p = open_position(mgr)
    manage(mgr, 101.0)          # +1R -> breakeven
    manage(mgr, 102.8)          # runs to +2.8R
    assert p.current_stop == 100.0
    action = manage(mgr, 100.0)
    assert action.should_exit and action.exit_price == 100.0   # exits flat


def test_trailing_ratchets_up_under_the_high_water_mark(mgr):
    p = open_position(mgr)
    trail = dict(trailing_enabled=True, trailing_atr_multiplier=1.0, atr=0.5)
    manage(mgr, 101.0, **trail)
    assert p.current_stop == 100.5          # 101.0 high-water - 1 x 0.5 ATR
    manage(mgr, 102.8, **trail)
    assert p.current_stop == 102.3
    action = manage(mgr, 102.3, **trail)
    assert action.should_exit
    assert action.exit_price == 102.3       # +2.3R kept, not $0.00


def test_stop_never_ratchets_down(mgr):
    p = open_position(mgr)
    trail = dict(trailing_enabled=True, trailing_atr_multiplier=1.0, atr=0.5)
    manage(mgr, 102.8, **trail)
    high = p.current_stop
    manage(mgr, 101.2, **trail)             # price falls back, but not to the stop
    assert p.current_stop == high


def test_does_not_engage_before_activation_r(mgr):
    p = open_position(mgr)
    manage(mgr, 100.5, trailing_enabled=True, trailing_atr_multiplier=1.0,
           trailing_activate_r=2.0, atr=0.5)
    assert p.current_stop == 99.0           # still the initial stop at +0.5R


def test_missing_or_zero_atr_is_a_no_op(mgr):
    """A snapshot early in the session can have no ATR yet -- that must leave the
    stop alone rather than trailing to the high-water mark itself."""
    for bad in (None, 0.0):
        m = PositionManager(max_concurrent_positions=1)
        p = open_position(m)
        m.manage("AG", current_price=102.0, current_time=T0 + timedelta(minutes=5),
                 breakeven_trigger_r=1.0, partial_exit_enabled=False,
                 partial_exit_trigger_r=1.5, partial_exit_sell_fraction=0.35,
                 trailing_enabled=True, trailing_atr_multiplier=1.0, atr=bad)
        assert p.current_stop == 100.0      # breakeven only


def test_trailing_exit_is_labelled_trailing_stop(mgr):
    open_position(mgr)
    trail = dict(trailing_enabled=True, trailing_atr_multiplier=1.0, atr=0.5)
    manage(mgr, 102.0, **trail)
    action = manage(mgr, 101.5, **trail)
    assert action.should_exit and action.exit_reason == ExitReason.TRAILING_STOP


def test_a_loser_still_exits_at_the_original_stop(mgr):
    """Trailing must not interfere with a trade that never goes favorable."""
    open_position(mgr)
    action = manage(mgr, 99.0, trailing_enabled=True, trailing_atr_multiplier=1.0, atr=0.5)
    assert action.should_exit
    assert action.exit_reason == ExitReason.STOP_HIT and action.exit_price == 99.0
