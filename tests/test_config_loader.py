import pytest

from config_loader import ConfigError, load_config


MINIMAL_TICKERS = """
tickers:
  AG:
    benchmark: SIL
    max_spread_pct: 0.20
    overnight_category: normal
    overnight_position_multiplier: 1.0
    manual_only: false
    profit_target_pct: [3.0, 5.5]
    volatility_category: normal
"""

MINIMAL_STRATEGY = "candle_timeframe: 5m\n"
MINIMAL_RISK = """
safety:
  max_spread_pct_default: 0.25
emergency:
  kill_switch_enabled: false
  kill_switch_file: "logs/KILL_SWITCH"
"""
MINIMAL_SCHEDULE = "windows: []\n"


def write_config_dir(tmp_path, broker_yaml: str):
    (tmp_path / "tickers.yaml").write_text(MINIMAL_TICKERS)
    (tmp_path / "strategy.yaml").write_text(MINIMAL_STRATEGY)
    (tmp_path / "risk.yaml").write_text(MINIMAL_RISK)
    (tmp_path / "schedule.yaml").write_text(MINIMAL_SCHEDULE)
    (tmp_path / "broker.yaml").write_text(broker_yaml)
    return str(tmp_path)


def test_paper_mode_loads_successfully(tmp_path):
    config_dir = write_config_dir(tmp_path, "mode: paper\npaper:\n  starting_equity: 100000\n  fill_model: touch\n")
    config = load_config(config_dir)
    assert config.broker["mode"] == "paper"
    assert "AG" in config.tickers


def test_live_mode_is_rejected(tmp_path):
    config_dir = write_config_dir(tmp_path, "mode: live\n")
    with pytest.raises(ConfigError):
        load_config(config_dir)


def test_unrecognized_mode_is_rejected(tmp_path):
    config_dir = write_config_dir(tmp_path, "mode: production\n")
    with pytest.raises(ConfigError):
        load_config(config_dir)


def test_sandbox_mode_requires_etrade_environment_sandbox(tmp_path):
    config_dir = write_config_dir(
        tmp_path,
        "mode: sandbox\netrade:\n  environment: production\n",
    )
    with pytest.raises(ConfigError):
        load_config(config_dir)


def test_sandbox_mode_loads_when_etrade_environment_is_sandbox(tmp_path):
    config_dir = write_config_dir(
        tmp_path,
        "mode: sandbox\netrade:\n  environment: sandbox\n  consumer_key_env: X\n  consumer_secret_env: Y\n"
        "  oauth_token_env: Z\n  oauth_token_secret_env: W\n  account_id_env: V\n",
    )
    config = load_config(config_dir)
    assert config.broker["mode"] == "sandbox"


def test_market_data_source_defaults_to_memory(tmp_path):
    config_dir = write_config_dir(tmp_path, "mode: paper\npaper:\n  starting_equity: 100000\n  fill_model: touch\n")
    config = load_config(config_dir)
    assert config.broker.get("market_data_source", "memory") == "memory"


def test_market_data_source_etrade_requires_sandbox_environment(tmp_path):
    config_dir = write_config_dir(
        tmp_path,
        "mode: paper\npaper:\n  starting_equity: 100000\n  fill_model: touch\n"
        "market_data_source: etrade\netrade:\n  environment: production\n",
    )
    with pytest.raises(ConfigError):
        load_config(config_dir)


def test_market_data_source_etrade_with_paper_broker_loads(tmp_path):
    config_dir = write_config_dir(
        tmp_path,
        "mode: paper\npaper:\n  starting_equity: 100000\n  fill_model: touch\n"
        "market_data_source: etrade\netrade:\n  environment: sandbox\n  consumer_key_env: X\n"
        "  consumer_secret_env: Y\n  oauth_token_env: Z\n  oauth_token_secret_env: W\n  account_id_env: V\n",
    )
    config = load_config(config_dir)
    assert config.broker["mode"] == "paper"
    assert config.broker["market_data_source"] == "etrade"


def test_unrecognized_market_data_source_is_rejected(tmp_path):
    config_dir = write_config_dir(
        tmp_path,
        "mode: paper\npaper:\n  starting_equity: 100000\n  fill_model: touch\nmarket_data_source: bogus\n",
    )
    with pytest.raises(ConfigError):
        load_config(config_dir)


def test_approved_universe_excludes_strategy_excluded_tickers(tmp_path):
    tickers_yaml = MINIMAL_TICKERS + """
  AQN:
    benchmark: XLU
    strategy: excluded
    max_spread_pct: 0.20
    overnight_category: normal
    overnight_position_multiplier: 1.0
    manual_only: false
    profit_target_pct: [1.5, 2.5]
    volatility_category: low
"""
    (tmp_path / "tickers.yaml").write_text(tickers_yaml)
    (tmp_path / "strategy.yaml").write_text(MINIMAL_STRATEGY)
    (tmp_path / "risk.yaml").write_text(MINIMAL_RISK)
    (tmp_path / "schedule.yaml").write_text(MINIMAL_SCHEDULE)
    (tmp_path / "broker.yaml").write_text("mode: paper\npaper:\n  starting_equity: 100000\n  fill_model: touch\n")

    config = load_config(str(tmp_path))
    assert "AQN" not in config.approved_universe()
    assert "AG" in config.approved_universe()


def test_shipped_stop_floor_is_half_a_percent():
    """Pins the shipped value. The floor was adopted on a holdout result that was
    significant (paired t=+2.36) while 1.0% and above destroyed the runner tail
    (>5R trades 41 -> 1 -> 0) and 2.0% took drawdown to 17%. The usable band is
    narrow, so a drive-by change to this number is a real risk and should have to
    break a test."""
    config = load_config()
    assert config.risk["stops"]["min_stop_pct_of_price"] == 0.5


def test_shipped_floor_actually_reaches_the_stop_calculation():
    """The floor is only worth anything if the signal engine reads it, so assert on
    a stop the 5-minute ATR would otherwise place inside the noise band rather than
    on the config value alone."""
    from risk.position_sizing import final_stop_price
    config = load_config()
    pct = config.risk["stops"]["min_stop_pct_of_price"]
    entry = 20.125
    # CDE's actual 2026-09-17 ATR, which on its own gives a 0.22% stop
    stop = final_stop_price(entry, atr_value=0.0526, atr_multiplier=0.85,
                            swing_low=None, min_stop_distance=entry * pct / 100.0)
    assert (entry - stop) / entry >= 0.005 - 1e-9
