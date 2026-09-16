"""Tests for the pre-backtest liquidity screen.

This screen is the only thing standing between a hand-supplied ticker list and an
untradeable name entering the universe, because the backtest cannot catch one:
scripts/backtest.py synthesizes bid/ask from the ticker's own configured
max_spread_pct, so the spread gate never fires there and a $0.40 stock with a
1-cent real spread backtests as though it cost 0.045% to cross.

The thresholds are anchored to decisions already made in config/tickers.yaml, so
the tests check the screen against those decisions rather than against itself.
"""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import screen_liquidity as sl  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_vzla_profile_passes_it_is_the_weakest_enabled_name():
    """VZLA sets the floor: 78 bars/day, $5.7M. If a change to the thresholds
    started rejecting it, the screen would be contradicting the shipped config."""
    ok, reasons = sl.passes(78, 5_700_000)
    assert ok, reasons


def test_nexa_profile_fails_it_was_dropped_for_exactly_this():
    ok, reasons = sl.passes(37, 300_000)
    assert not ok
    assert len(reasons) == 2  # fails on BOTH coverage and dollar volume


@pytest.mark.parametrize("bars,dollars", [(59, 200_000), (16, 20_000), (61, 1_800_000)])
def test_known_thin_names_are_rejected(bars, dollars):
    """TRX, RMCO and CTGO as measured from the cache."""
    assert not sl.passes(bars, dollars)[0]


def test_each_criterion_can_fail_alone():
    """A name can be liquid in dollars but print sporadically, or print every bar
    while trading tiny size. Either is disqualifying on its own."""
    ok, reasons = sl.passes(40, 50_000_000)
    assert not ok and len(reasons) == 1 and "coverage" in reasons[0]

    ok, reasons = sl.passes(78, 1_000_000)
    assert not ok and len(reasons) == 1 and "$vol" in reasons[0]


def test_thresholds_sit_between_the_two_reference_names():
    """Pin the thresholds against the config decisions they were derived from, so
    a later 'tidy-up' of the constants has to confront both."""
    assert 37 < sl.MIN_BARS_PER_DAY <= 78
    assert 300_000 < sl.MIN_DOLLAR_VOLUME <= 5_700_000


def test_config_exclusions_and_screen_agree():
    """Every name this project excluded for liquidity must still fail the screen,
    and every enabled name whose numbers are recorded must still pass. The numbers
    live in the config comments, so this catches a threshold edit that would
    silently re-enable something."""
    with open(os.path.join(ROOT, "config", "tickers.yaml")) as f:
        tickers = yaml.safe_load(f)["tickers"]
    # measured from data_cache at the time each was added
    measured = {
        "NEXA": (37, 300_000), "TRX": (59, 200_000), "RMCO": (16, 20_000),
        "CTGO": (61, 1_800_000), "NB": (69, 700_000), "EMAT": (62, 900_000),
        "GFI": (78, 67_900_000), "HMY": (78, 47_300_000),
        "AUGO": (78, 35_600_000), "PPTA": (78, 9_400_000),
    }
    for symbol, (bars, dollars) in measured.items():
        assert symbol in tickers, f"{symbol} missing from config/tickers.yaml"
        excluded = tickers[symbol].get("strategy") == "excluded"
        screened_out = not sl.passes(bars, dollars)[0]
        assert excluded == screened_out, (
            f"{symbol}: config says excluded={excluded}, screen says exclude={screened_out}"
        )


def test_profile_returns_none_for_an_uncached_symbol():
    assert sl.profile("NOT_A_REAL_TICKER") is None
