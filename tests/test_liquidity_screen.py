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


def test_every_excluded_ticker_has_a_written_rationale():
    """`strategy: excluded` is a one-word switch that erases a name from the live
    universe, and nothing about it says why. Rationale in this repo lives either in
    comments directly above the ticker key (NEXA, AQN) or inside its block (the
    liquidity batch), so accept both -- but require one of them. Without this, a
    name dropped on a hunch is indistinguishable from one dropped for cause, and
    the next person re-deriving the universe cannot tell which are safe to revisit."""
    with open(os.path.join(ROOT, "config", "tickers.yaml")) as f:
        raw = f.read()
    lines = raw.split("\n")
    excluded = []
    for i, line in enumerate(lines):
        m = re.match(r"^  ([A-Z]+):$", line)
        if not m:
            continue
        block, j = [], i + 1
        while j < len(lines) and lines[j].startswith("    "):
            block.append(lines[j])
            j += 1
        if any(b.strip() == "strategy: excluded" for b in block):
            preceding, k = [], i - 1
            while k >= 0 and lines[k].strip().startswith("#"):
                preceding.append(lines[k])
                k -= 1
            excluded.append((m.group(1), preceding, block))

    assert excluded, "no excluded tickers found -- the parser is probably broken"
    for symbol, preceding, block in excluded:
        rationale = [b for b in block if b.strip().startswith("#")] + preceding
        assert rationale, f"{symbol} is excluded with no written rationale anywhere"


def test_a_name_excluded_while_passing_the_screen_must_say_it_is_not_liquidity():
    """Guards the case that existed briefly for GFI and HMY: excluded on P&L while
    passing the screen. None currently exist, so this checks the rule holds for
    whatever set is present rather than asserting a fixed list."""
    with open(os.path.join(ROOT, "config", "tickers.yaml")) as f:
        raw = f.read()
    measured = {"GFI": (78, 67_900_000), "HMY": (78, 47_300_000),
                "AUGO": (78, 35_600_000), "PPTA": (78, 9_400_000),
                "VZLA": (78, 5_700_000)}
    for symbol, (bars, dollars) in measured.items():
        if not sl.passes(bars, dollars)[0]:
            continue
        block = re.search(rf"^  {symbol}:\n((?:    .*\n)+)", raw, re.M)
        assert block, f"{symbol} missing from config"
        if "strategy: excluded" in block.group(1):
            assert "NOT a liquidity call" in block.group(1), (
                f"{symbol} is excluded but passes the liquidity screen, and does not say why"
            )


def test_enabled_names_all_pass_the_screen():
    """The measured numbers for every enabled name recorded in this file."""
    with open(os.path.join(ROOT, "config", "tickers.yaml")) as f:
        tickers = yaml.safe_load(f)["tickers"]
    enabled = {"GFI": (78, 67_900_000), "HMY": (78, 47_300_000),
               "AUGO": (78, 35_600_000), "PPTA": (78, 9_400_000),
               "VZLA": (78, 5_700_000)}
    for symbol, (bars, dollars) in enabled.items():
        assert tickers[symbol].get("strategy") != "excluded", f"{symbol} unexpectedly excluded"
        assert sl.passes(bars, dollars)[0], f"{symbol} is enabled but fails the screen"


def test_profile_returns_none_for_an_uncached_symbol():
    assert sl.profile("NOT_A_REAL_TICKER") is None
