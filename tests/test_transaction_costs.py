"""Transaction cost model.

The point of costing per SHARE rather than in basis points is that the one-cent
minimum tick, not a percentage, is what actually binds this strategy: over half its
traded notional is in sub-$10 miners where a penny is 10+ bps.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from execution.costs import TransactionCostModel


def test_penny_spread_costs_half_a_cent_per_share_per_side():
    model = TransactionCostModel(spread_ticks=1.0)
    assert model.per_side(price=50.0, shares=1000) == 5.0
    assert model.round_trip(entry_price=50.0, exit_price=51.0, shares=1000) == 10.0


def test_same_notional_costs_far_more_in_a_cheap_stock():
    """The whole reason for a per-share model. Equal dollar positions, 25x apart in
    relative cost -- a flat-bps model would price these identically and hide it."""
    model = TransactionCostModel(spread_ticks=1.0)
    cheap_bps = model.round_trip(4.05, 4.05, int(25000 / 4.05)) / (25000 * 2) * 10000
    rich_bps = model.round_trip(94.61, 94.61, int(25000 / 94.61)) / (25000 * 2) * 10000
    assert round(cheap_bps, 1) == 12.3
    assert round(rich_bps, 1) == 0.5


def test_impact_and_commission_stack_on_top_of_spread():
    model = TransactionCostModel(spread_ticks=1.0, impact_bps=2.0, commission_per_order=1.0)
    # 1000 shares at $10: $5 spread + $2 impact (2bps of $10k) + $1 commission
    assert model.per_side(price=10.0, shares=1000) == 8.0


def test_crossing_fraction_scales_only_the_spread_term():
    model = TransactionCostModel(spread_ticks=1.0, impact_bps=2.0, crossing_fraction=0.5)
    # spread halves to $2.50, impact untouched at $2
    assert model.per_side(price=10.0, shares=1000) == 4.5


def test_default_model_is_free_and_reports_itself_disabled():
    """An omitted cost model must reproduce the old frictionless backtest exactly."""
    model = TransactionCostModel()
    assert not model.enabled
    assert model.round_trip(10.0, 11.0, 1000) == 0.0


def test_zero_or_negative_shares_cost_nothing():
    model = TransactionCostModel(spread_ticks=1.0, commission_per_order=5.0)
    assert model.per_side(price=10.0, shares=0) == 0.0


def test_from_config_reads_the_shipped_defaults():
    from config_loader import load_config
    model = TransactionCostModel.from_config(load_config().risk)
    assert model.spread_ticks == 1.0
    assert model.crossing_fraction == 1.0
    assert model.enabled


def test_from_config_tolerates_a_missing_section():
    assert not TransactionCostModel.from_config({}).enabled
