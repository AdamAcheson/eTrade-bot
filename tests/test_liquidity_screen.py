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
import re
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


def test_every_name_failing_the_screen_is_excluded_in_config():
    """One direction only, and it is the direction that matters for safety: a name
    too thin to trade must never be enabled. The converse does NOT hold -- a name
    can pass the screen and still be excluded for an unrelated reason (GFI and HMY
    were dropped on P&L, not liquidity), so asserting equality here would force a
    liquidity justification onto decisions that never had one."""
    with open(os.path.join(ROOT, "config", "tickers.yaml")) as f:
        tickers = yaml.safe_load(f)["tickers"]
    thin = {"NEXA": (37, 300_000), "TRX": (59, 200_000), "RMCO": (16, 20_000),
            "CTGO": (61, 1_800_000), "NB": (69, 700_000), "EMAT": (62, 900_000)}
    for symbol, (bars, dollars) in thin.items():
        assert not sl.passes(bars, dollars)[0], f"{symbol} should fail the screen"
        assert tickers[symbol].get("strategy") == "excluded", (
            f"{symbol} fails the liquidity screen but is still enabled"
        )


def test_non_liquidity_exclusions_state_that_they_are_not_liquidity_calls():
    """A reader looking at `strategy: excluded` cannot tell WHY from the switch
    alone. Anything excluded while PASSING the screen has to say so in place, or
    the next person re-deriving the universe will assume it was too thin."""
    with open(os.path.join(ROOT, "config", "tickers.yaml")) as f:
        raw = f.read()
    passes_screen = {"GFI": (78, 67_900_000), "HMY": (78, 47_300_000)}
    for symbol, (bars, dollars) in passes_screen.items():
        assert sl.passes(bars, dollars)[0], f"{symbol} should pass the screen"
        block = re.search(rf"^  {symbol}:\n((?:    .*\n)+)", raw, re.M)
        assert block, f"{symbol} missing from config"
        assert "strategy: excluded" in block.group(1)
        assert "NOT a liquidity call" in block.group(1), (
            f"{symbol} is excluded but passes the screen, and does not say why"
        )


def test_enabled_names_all_pass_the_screen():
    """The measured numbers for every name recorded in this file."""
    with open(os.path.join(ROOT, "config", "tickers.yaml")) as f:
        tickers = yaml.safe_load(f)["tickers"]
    enabled = {"AUGO": (78, 35_600_000), "PPTA": (78, 9_400_000), "VZLA": (78, 5_700_000)}
    for symbol, (bars, dollars) in enabled.items():
        assert tickers[symbol].get("strategy") != "excluded"
        assert sl.passes(bars, dollars)[0], f"{symbol} is enabled but fails the screen"


def test_profile_returns_none_for_an_uncached_symbol():
    assert sl.profile("NOT_A_REAL_TICKER") is None
