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
