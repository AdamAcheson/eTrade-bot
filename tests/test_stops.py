import pytest

from risk.position_sizing import (
    atr_multiplier_for_category,
    atr_stop_price,
    final_stop_price,
    structure_stop_price,
)


def test_atr_stop_price():
    assert atr_stop_price(entry_price=10.0, atr_value=0.2, atr_multiplier=0.85) == pytest.approx(9.83)


def test_structure_stop_price_passthrough():
    assert structure_stop_price(9.5) == 9.5
    assert structure_stop_price(None) is None


def test_final_stop_uses_wider_of_atr_and_structure():
    # ATR stop is tighter (9.90) than structure stop (9.70) -> use the wider (lower).
    stop = final_stop_price(entry_price=10.0, atr_value=0.1176, atr_multiplier=0.85, swing_low=9.70)
    assert stop == pytest.approx(9.70)


def test_final_stop_falls_back_to_atr_when_no_structure():
    stop = final_stop_price(entry_price=10.0, atr_value=0.2, atr_multiplier=0.85, swing_low=None)
    assert stop == pytest.approx(9.83)


def test_final_stop_uses_atr_when_structure_is_wider():
    # Structure stop (9.95) is tighter than ATR stop (9.83) -> ATR stop wins (wider).
    stop = final_stop_price(entry_price=10.0, atr_value=0.2, atr_multiplier=0.85, swing_low=9.95)
    assert stop == pytest.approx(9.83)


@pytest.mark.parametrize("category,expected_key", [
    ("low", "low_volatility_diversified"),
    ("normal", "normal"),
    ("moderate", "normal"),
    ("high", "high_volatility_speculative"),
    ("very_high", "high_volatility_speculative"),
])
def test_atr_multiplier_for_category(category, expected_key):
    multipliers = {
        "low_volatility_diversified": 0.75,
        "normal": 0.85,
        "high_volatility_speculative": 0.95,
    }
    assert atr_multiplier_for_category(category, multipliers) == multipliers[expected_key]


# --- stop floor (min_stop_distance) -------------------------------------------
# ATR here is a 14-period average of FIVE-MINUTE bars, so it measures roughly the
# last 70 minutes and collapses in a quiet afternoon. On 2026-09-17 that put a CDE
# stop 0.22% below entry against a 5.09% median daily range, and a 0.42% move took
# it out. These pin the floor that lets the stop reference real volatility.

def test_floor_widens_a_stop_that_atr_placed_inside_the_noise():
    from risk.position_sizing import final_stop_price
    # CDE as it actually happened: entry 20.125, 5-min ATR 0.0526, multiplier 0.85
    unfloored = final_stop_price(20.125, atr_value=0.0526, atr_multiplier=0.85, swing_low=None)
    assert round(20.125 - unfloored, 4) == 0.0447          # 0.22% of price
    floored = final_stop_price(20.125, atr_value=0.0526, atr_multiplier=0.85,
                               swing_low=None, min_stop_distance=20.125 * 0.005)
    assert round(20.125 - floored, 4) == round(20.125 * 0.005, 4)  # 0.50% of price
    assert floored < unfloored


def test_floor_never_tightens_an_already_wide_stop():
    """The floor is a MINIMUM distance, not a target. A volatile session that
    produces a wide ATR stop must keep it -- tightening to the floor would be the
    one thing spec section 14 forbids, moving the stop to suit sizing."""
    from risk.position_sizing import final_stop_price
    wide = final_stop_price(100.0, atr_value=5.0, atr_multiplier=0.85, swing_low=None)
    floored = final_stop_price(100.0, atr_value=5.0, atr_multiplier=0.85,
                               swing_low=None, min_stop_distance=1.0)
    assert wide == floored == 100.0 - 4.25


def test_floor_still_loses_to_an_even_wider_structure_stop():
    """Structure, ATR and floor are three candidates and the WIDEST wins."""
    from risk.position_sizing import final_stop_price
    stop = final_stop_price(100.0, atr_value=0.1, atr_multiplier=0.85,
                            swing_low=97.0, min_stop_distance=1.0)
    assert stop == 97.0


def test_zero_or_none_floor_is_inert():
    """Default must reproduce the shipped behaviour exactly, so enabling the floor
    is an explicit decision rather than a silent change to every stop."""
    from risk.position_sizing import final_stop_price
    base = final_stop_price(50.0, atr_value=0.2, atr_multiplier=0.85, swing_low=None)
    assert final_stop_price(50.0, atr_value=0.2, atr_multiplier=0.85,
                            swing_low=None, min_stop_distance=None) == base
    assert final_stop_price(50.0, atr_value=0.2, atr_multiplier=0.85,
                            swing_low=None, min_stop_distance=0.0) == base
