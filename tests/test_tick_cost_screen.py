"""The min_price eligibility gate.

This is a TRANSACTION COST screen, not a quality judgement. The one-cent minimum
tick is a fixed cost per share, so its cost in basis points is inversely
proportional to price: crossing a penny-wide market costs 12 bps on a $4 stock and
0.5 bps on a $95 one. With costs modelled, over half the holdout's traded notional
sat in sub-$10 names where the tick alone consumed most of the edge.

It is deliberately separate from the max_spread_pct gate, which asks whether a
name's quote is unusually wide FOR THAT NAME. A $4 stock with a perfectly tight
one-cent quote passes that gate and still cannot afford to be traded.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from factories import base_stock_snapshot  # noqa: E402

from strategy.setups import basic_eligibility  # noqa: E402


def _cheap():
    """A $4 name with a one-cent quote -- as tight as a US equity can legally be."""
    return base_stock_snapshot(last_price=4.05, bid=4.04, ask=4.05, vwap=4.00,
                               ema_9=3.98, swing_low=3.90)


def _rich():
    return base_stock_snapshot(last_price=94.61, bid=94.60, ask=94.61, vwap=93.00,
                               ema_9=92.50, swing_low=91.00)


def test_rejects_a_stock_whose_tick_is_unaffordable():
    result = basic_eligibility(_cheap(), max_spread_pct=0.50, min_relative_volume=1.1,
                               min_price=10.0)
    assert not result.eligible
    assert result.reason == "REJECTED_TICK_COST"


def test_admits_a_stock_above_the_threshold():
    assert basic_eligibility(_rich(), max_spread_pct=0.50, min_relative_volume=1.1,
                             min_price=10.0).eligible


def test_zero_disables_the_screen():
    """Default must admit exactly what the old code did, so enabling the screen is
    an explicit decision rather than a silent narrowing of the universe."""
    assert basic_eligibility(_cheap(), max_spread_pct=0.50, min_relative_volume=1.1,
                             min_price=0.0).eligible
    assert basic_eligibility(_cheap(), max_spread_pct=0.50,
                             min_relative_volume=1.1).eligible


def test_is_distinct_from_the_wide_spread_gate():
    """The cheap name's quote is one cent -- it CANNOT be tighter -- so the spread
    gate passes it. Only the tick-cost gate catches it. Collapsing the two would
    lose this case entirely."""
    cheap = _cheap()
    assert basic_eligibility(cheap, max_spread_pct=0.50, min_relative_volume=1.1).eligible
    assert basic_eligibility(cheap, max_spread_pct=0.50, min_relative_volume=1.1,
                             min_price=10.0).reason == "REJECTED_TICK_COST"


def test_threshold_maps_to_the_documented_cost_ceiling():
    """min_price 10 is documented as capping tick cost at 5 bps per side. Pin the
    arithmetic so the config comment and the behaviour cannot drift apart."""
    half_tick = 0.005
    assert round(half_tick / 10.0 * 10000, 1) == 5.0
    assert round(half_tick / 20.0 * 10000, 1) == 2.5
    at_ten = base_stock_snapshot(last_price=10.00, bid=9.99, ask=10.00, vwap=9.90,
                                 ema_9=9.85, swing_low=9.50)
    assert basic_eligibility(at_ten, max_spread_pct=0.50, min_relative_volume=1.1,
                             min_price=10.0).eligible


def test_shipped_threshold_is_ten_dollars():
    """Pins the shipped value. The sweep is a broad plateau in net ($5 and $10 are
    indistinguishable at t=-0.14) but NOT in drawdown or in cost headroom, which is
    why $10 was chosen over the marginally higher-netting $5. A drive-by change
    should have to break a test."""
    from config_loader import load_config
    assert load_config().strategy["eligibility"]["min_price"] == 10.0


def test_shipped_threshold_actually_rejects_the_names_it_is_meant_to():
    """Asserts on behaviour, not the config value: the sub-$10 miners that carried
    57% of the holdout's traded notional must now be refused at their old prices."""
    from config_loader import load_config
    min_price = load_config().strategy["eligibility"]["min_price"]
    for price in (4.05, 4.37, 5.41, 6.70, 7.46):  # SVM, EXK, FSM, HL, AG in the holdout
        snap = base_stock_snapshot(last_price=price, bid=price - 0.01, ask=price,
                                   vwap=price * 0.98, ema_9=price * 0.97,
                                   swing_low=price * 0.95)
        result = basic_eligibility(snap, max_spread_pct=0.50, min_relative_volume=1.1,
                                   min_price=min_price)
        assert result.reason == "REJECTED_TICK_COST", price
    # ...and the same names at their later, post-rally prices must be admitted
    for price in (10.70, 10.84, 19.39, 20.98):
        snap = base_stock_snapshot(last_price=price, bid=price - 0.01, ask=price,
                                   vwap=price * 0.98, ema_9=price * 0.97,
                                   swing_low=price * 0.95)
        assert basic_eligibility(snap, max_spread_pct=0.50, min_relative_volume=1.1,
                                 min_price=min_price).eligible, price
