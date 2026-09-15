"""Guards on the alternate-universe experiment configs.

config_experiments/ exists so the strategy can be measured on names it must never
trade live. Two things have to stay true for that to be safe, and neither is
enforced by anything but this file:

  1. The shipped universe never acquires an experiment ticker. If TSLA ended up in
     config/tickers.yaml, run_bot.py would trade it -- the live loop reads the same
     auto_tradeable_universe() the backtest does.
  2. The experiment directories' strategy/risk/broker/schedule stay symlinked to
     config/. If one were copied instead, a later parameter change would apply to
     the mining book and not the experiment, and the comparison would silently
     start measuring two variables instead of one.
"""

import os

import pytest
import yaml

from config_loader import load_config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPERIMENTS = ("xsector", "combined")
SHARED = ("strategy.yaml", "risk.yaml", "broker.yaml", "schedule.yaml")


def _tickers(path):
    with open(path) as f:
        return set(yaml.safe_load(f)["tickers"])


def test_shipped_universe_contains_no_experiment_only_tickers():
    shipped = _tickers(os.path.join(ROOT, "config", "tickers.yaml"))
    xsector = _tickers(os.path.join(ROOT, "config_experiments", "xsector", "tickers.yaml"))
    assert shipped.isdisjoint(xsector), (
        f"experiment tickers leaked into the live universe: {sorted(shipped & xsector)}"
    )


@pytest.mark.parametrize("experiment", EXPERIMENTS)
@pytest.mark.parametrize("filename", SHARED)
def test_experiment_shares_parameters_by_symlink(experiment, filename):
    path = os.path.join(ROOT, "config_experiments", experiment, filename)
    assert os.path.islink(path), f"{path} must be a symlink to config/{filename}, not a copy"
    assert os.path.realpath(path) == os.path.join(ROOT, "config", filename)


@pytest.mark.parametrize("experiment", EXPERIMENTS)
def test_experiment_config_loads_and_is_tradeable(experiment):
    config = load_config(os.path.join(ROOT, "config_experiments", experiment))
    universe = config.auto_tradeable_universe()
    assert universe, f"{experiment} has no tradeable tickers"
    for ticker in universe:
        # A benchmark that isn't cached backtests to zero entries rather than
        # failing, so an unreachable benchmark would read as "the strategy doesn't
        # travel" when it actually never ran.
        assert config.benchmark_of(ticker), f"{ticker} has no benchmark"


def test_combined_is_the_union_of_the_two_books():
    shipped = _tickers(os.path.join(ROOT, "config", "tickers.yaml"))
    xsector = _tickers(os.path.join(ROOT, "config_experiments", "xsector", "tickers.yaml"))
    combined = _tickers(os.path.join(ROOT, "config_experiments", "combined", "tickers.yaml"))
    assert combined == shipped | xsector
