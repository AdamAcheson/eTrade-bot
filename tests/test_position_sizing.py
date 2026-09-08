import pytest

from risk.position_sizing import (
    calc_position_size,
    r_multiple_target,
    reward_risk_ratio,
    risk_per_share,
)


def test_risk_per_share():
    assert risk_per_share(10.0, 9.5) == pytest.approx(0.5)


def test_reward_risk_ratio():
    assert reward_risk_ratio(entry_price=10.0, stop_price=9.5, target_price=11.0) == pytest.approx(2.0)


def test_reward_risk_ratio_invalid_stop_returns_none():
    assert reward_risk_ratio(entry_price=10.0, stop_price=10.0, target_price=11.0) is None


def test_r_multiple_target():
    target = r_multiple_target(entry_price=10.0, stop_price=9.5, r_multiple=1.75)
    assert target == pytest.approx(10.875)


def test_calc_position_size_basic_risk_based():
    result = calc_position_size(
        account_equity=100_000,
        max_account_risk_per_trade=0.0075,
        entry_price=10.0,
        stop_price=9.5,
    )
    # risk_dollars = 750, risk_per_share = 0.5 -> 1500 shares
    assert result.shares == 1500
    assert result.risk_dollars == pytest.approx(750.0)
    assert result.capped_by is None


def test_calc_position_size_capped_by_dollar_limit():
    result = calc_position_size(
        account_equity=100_000,
        max_account_risk_per_trade=0.0075,
        entry_price=10.0,
        stop_price=9.5,
        max_position_size_dollars=5000,
    )
    # uncapped would be 1500 shares * $10 = $15,000 > $5,000 cap -> 500 shares
    assert result.shares == 500
    assert result.capped_by == "max_position_size_dollars"


def test_calc_position_size_capped_by_pct_equity():
    result = calc_position_size(
        account_equity=100_000,
        max_account_risk_per_trade=0.05,  # deliberately large to trigger the pct cap
        entry_price=10.0,
        stop_price=9.5,
        max_position_size_dollars=1_000_000,
        max_position_size_pct_equity=0.10,
    )
    # pct cap: 100,000 * 0.10 / 10.0 = 1000 shares
    assert result.shares == 1000
    assert result.capped_by == "max_position_size_pct_equity"


def test_calc_position_size_invalid_stop_returns_zero_shares():
    result = calc_position_size(
        account_equity=100_000,
        max_account_risk_per_trade=0.0075,
        entry_price=10.0,
        stop_price=10.0,
    )
    assert result.shares == 0
    assert result.capped_by == "invalid_stop"


def test_calc_position_size_flat_dollar_risk_overrides_pct_of_equity():
    result = calc_position_size(
        account_equity=100_000,
        max_account_risk_per_trade=0.0075,  # would otherwise give $750 risk
        entry_price=10.0,
        stop_price=9.5,
        max_risk_dollars_per_trade=100,
    )
    # $100 / $0.5 risk-per-share = 200 shares, not the 1500 the %-of-equity budget would give.
    assert result.shares == 200
    assert result.risk_dollars == pytest.approx(100.0)


def test_calc_position_size_flat_dollar_risk_independent_of_equity():
    small_account = calc_position_size(
        account_equity=10_000, max_account_risk_per_trade=0.0075,
        entry_price=10.0, stop_price=9.5, max_risk_dollars_per_trade=100,
    )
    large_account = calc_position_size(
        account_equity=1_000_000, max_account_risk_per_trade=0.0075,
        entry_price=10.0, stop_price=9.5, max_risk_dollars_per_trade=100,
    )
    assert small_account.shares == large_account.shares == 200


def test_position_size_adjusts_to_stop_distance_not_the_other_way_around():
    # A wider stop must produce fewer shares for the same dollar risk -- the stop is
    # never tightened just to buy more shares (spec section 14).
    tight = calc_position_size(100_000, 0.0075, entry_price=10.0, stop_price=9.8)
    wide = calc_position_size(100_000, 0.0075, entry_price=10.0, stop_price=9.0)
    assert wide.shares < tight.shares
