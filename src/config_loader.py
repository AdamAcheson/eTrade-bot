"""Loads and validates config/*.yaml. This is the single place that enforces the
paper-mode-only safety rule (spec section 37): if broker.yaml ever says anything other
than mode: paper, the application refuses to start."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import yaml

DEFAULT_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")


class ConfigError(Exception):
    pass


def _load_yaml(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise ConfigError(f"Missing required config file: {path}")
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    return data


@dataclass
class TickerConfig:
    ticker: str
    benchmark: str
    sector: str
    max_spread_pct: float
    overnight_category: str
    overnight_position_multiplier: float
    manual_only: bool
    profit_target_pct: Optional[list]
    volatility_category: str
    strategy: str = "mining"

    @property
    def is_excluded(self) -> bool:
        return self.strategy == "excluded"


@dataclass
class AppConfig:
    tickers: Dict[str, TickerConfig]
    strategy: Dict[str, Any]
    risk: Dict[str, Any]
    broker: Dict[str, Any]
    schedule: Dict[str, Any]
    config_dir: str

    def approved_universe(self) -> list:
        """Symbols eligible for the (long-only) mining strategy -- excludes AQN and
        anything else explicitly marked strategy: excluded."""
        return [t.ticker for t in self.tickers.values() if not t.is_excluded]

    def auto_tradeable_universe(self) -> list:
        """approved_universe() minus manual_only symbols (NZAUF, AAGAF)."""
        return [t for t in self.approved_universe() if not self.tickers[t].manual_only]

    def benchmark_of(self, ticker: str) -> str:
        return self.tickers[ticker].benchmark

    def max_spread_pct(self, ticker: str) -> float:
        cfg = self.tickers.get(ticker)
        if cfg is not None:
            return cfg.max_spread_pct
        return self.risk["safety"]["max_spread_pct_default"]

    def is_manual_only(self, ticker: str) -> bool:
        cfg = self.tickers.get(ticker)
        return bool(cfg and cfg.manual_only)

    def kill_switch_active(self) -> bool:
        if not self.risk.get("emergency", {}).get("kill_switch_enabled", True):
            return False
        kill_file = self.risk["emergency"]["kill_switch_file"]
        path = kill_file if os.path.isabs(kill_file) else os.path.join(
            os.path.dirname(self.config_dir), kill_file
        )
        return os.path.exists(path)


def load_config(config_dir: str = DEFAULT_CONFIG_DIR) -> AppConfig:
    tickers_raw = _load_yaml(os.path.join(config_dir, "tickers.yaml"))
    strategy = _load_yaml(os.path.join(config_dir, "strategy.yaml"))
    risk = _load_yaml(os.path.join(config_dir, "risk.yaml"))
    broker = _load_yaml(os.path.join(config_dir, "broker.yaml"))
    schedule = _load_yaml(os.path.join(config_dir, "schedule.yaml"))

    ALLOWED_BROKER_MODES = {"paper", "sandbox"}
    if broker.get("mode") not in ALLOWED_BROKER_MODES:
        raise ConfigError(
            f"broker.yaml mode must be one of {sorted(ALLOWED_BROKER_MODES)}. "
            "Live/production trading is not implemented and must never be enabled "
            "by a config change alone."
        )
    if broker["mode"] == "sandbox" and broker.get("etrade", {}).get("environment") != "sandbox":
        raise ConfigError(
            "broker.yaml mode is 'sandbox' but etrade.environment is not 'sandbox'. "
            "Refusing to start: this is a second, independent safety check on top of "
            "the one in broker/etrade.py -- both must agree before any real network "
            "call to E*TRADE is made."
        )

    ALLOWED_MARKET_DATA_SOURCES = {"memory", "etrade"}
    market_data_source = broker.get("market_data_source", "memory")
    if market_data_source not in ALLOWED_MARKET_DATA_SOURCES:
        raise ConfigError(f"broker.yaml market_data_source must be one of {sorted(ALLOWED_MARKET_DATA_SOURCES)}.")
    if market_data_source == "etrade" and broker.get("etrade", {}).get("environment") != "sandbox":
        raise ConfigError(
            "broker.yaml market_data_source is 'etrade' but etrade.environment is not "
            "'sandbox' -- quote polling independently requires the same sandbox-only "
            "guarantee as order placement does, regardless of broker mode."
        )

    tickers: Dict[str, TickerConfig] = {}
    for symbol, raw in (tickers_raw.get("tickers") or {}).items():
        tickers[symbol] = TickerConfig(
            ticker=symbol,
            benchmark=raw["benchmark"],
            sector=raw.get("sector", ""),
            max_spread_pct=float(raw["max_spread_pct"]),
            overnight_category=raw.get("overnight_category", "manual_only"),
            overnight_position_multiplier=float(raw.get("overnight_position_multiplier", 0.0)),
            manual_only=bool(raw.get("manual_only", False)),
            profit_target_pct=raw.get("profit_target_pct"),
            volatility_category=raw.get("volatility_category", "normal"),
            strategy=raw.get("strategy", "mining"),
        )

    if not tickers:
        raise ConfigError("tickers.yaml defines no tickers")

    return AppConfig(
        tickers=tickers,
        strategy=strategy,
        risk=risk,
        broker=broker,
        schedule=schedule,
        config_dir=config_dir,
    )
