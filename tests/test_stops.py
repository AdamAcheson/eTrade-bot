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
