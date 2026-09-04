# Mining Stock Trading Bot — Architecture

This document is the "first task" deliverable required before implementation: proposed
architecture, file tree, data models, config schemas, the ticker state machine, and
pseudocode for the main loop. Phase 1 (market data models + indicators + signal engine,
no order placement) is implemented on top of this design.

## 1. Design Principles

- **Paper trading is the only mode that can execute today.** There is no code path that
  can reach a real brokerage endpoint. `broker/etrade.py` exists only as an
  interface-shaped stub that raises `NotImplementedError` on every method.
- **Strict layering.** `MarketDataProvider` → `StrategyEngine` (benchmark + setups +
  scoring) → `RiskManager` → `ExecutionEngine` (`OrderManager`) → `PositionManager` →
  `Journal` / `ReportingEngine`. Strategy code never imports a broker or a specific data
  vendor; everything is injected through the abstract interfaces in `broker/base.py`
  and `data/market_data.py`.
- **Config over code.** Every ticker-specific number (benchmark, spread cap, ATR
  multiplier, overnight category, target range) lives in `config/*.yaml`, not in
  strategy source.
- **Long-only, one-position-default, no pyramiding, no averaging down.** These are
  enforced in `PositionManager`/`RiskManager`, not just documented.

## 2. File Tree

```
eTrade-bot/
├── config/
│   ├── tickers.yaml       # per-symbol benchmark, spread cap, overnight category, targets
│   ├── strategy.yaml      # scoring weights, thresholds, indicator periods, chase rules
│   ├── risk.yaml          # account risk %, daily safety limits, stop/partial-exit params
│   ├── broker.yaml        # broker mode (paper/live), sandbox endpoints, env var names
│   └── schedule.yaml      # ET trading windows and their priority/behavior
├── src/
│   ├── main.py                    # wiring + event-loop pseudocode/skeleton (Phase 1)
│   ├── config_loader.py           # loads + validates the YAML files above
│   ├── models/
│   │   ├── bar.py                 # OHLCV Bar
│   │   ├── signal.py              # Signal + Decision + RejectionReason
│   │   ├── trade.py                # Trade (journal record) + TradeState + ExitReason
│   │   └── position.py            # Position (open-trade working state)
│   ├── data/
│   │   ├── market_data.py         # MarketDataProvider ABC, TickerMarketState, in-memory impl
│   │   └── indicators.py          # VWAP, EMA, ATR, RVOL, opening range, spread, swings
│   ├── strategy/
│   │   ├── benchmark.py           # benchmark confirmation rule
│   │   ├── setups.py              # ORB+pullback, VWAP reclaim, chase/overextension rule
│   │   ├── scoring.py             # 0-100 setup scoring
│   │   └── signal_engine.py       # orchestrates eligibility -> chase -> benchmark ->
│   │                               #   setup -> R:R -> score -> Signal, for every ticker
│   ├── risk/
│   │   ├── position_sizing.py     # ATR/structure stop, R-based target, share sizing
│   │   └── risk_manager.py        # daily safety limits, cooldowns, kill switch, no-avg-down
│   ├── positions/
│   │   ├── position_manager.py    # per-ticker state machine, breakeven/partial exit mgmt
│   │   └── overnight.py           # 3:50pm overnight-eligibility evaluation + size reduction
│   ├── execution/
│   │   └── order_manager.py       # pre-submit rechecks + duplicate-order protection
│   ├── broker/
│   │   ├── base.py                # BrokerInterface ABC
│   │   ├── paper.py                # PaperBrokerAdapter (simulated fills, default broker)
│   │   └── etrade.py               # ETradeBrokerAdapter STUB — not implemented, not enabled
│   └── reporting/
│       ├── journal.py             # per-signal log + per-trade journal (CSV/JSONL)
│       └── daily_report.py        # daily/cumulative performance + missed-opportunity report
├── tests/
├── logs/
├── reports/
└── docs/ARCHITECTURE.md
```

## 3. Data Models (see `src/models/`)

- **Bar**: `timestamp, open, high, low, close, volume`.
- **Signal**: one row per evaluated ticker per evaluation tick — every field listed in
  spec §27 (price/VWAP/EMA/ATR/RVOL/opening range, benchmark fields, setup type/score,
  entry/stop/target/R, `decision`, `rejection_reason`).
- **Trade**: one row per completed simulated trade — every field in spec §28
  (entry/exit price+time, shares, stops/targets, R return, MFE/MAE, benchmark return
  during trade, setup metadata, overnight flag, `exit_reason`).
- **Position**: live working state for an open trade — `state` (see state machine),
  `initial_stop`/`current_stop`, `initial_target`/`current_target`,
  `maximum_favorable_excursion`/`maximum_adverse_excursion`, partial-exit tracking.

`RejectionReason` and `ExitReason` are `enum.Enum`s so log values are exact string
constants matching spec §27/§28 (e.g. `REJECTED_BENCHMARK_CONFIRMATION`,
`REJECTED_OVEREXTENDED`, `STOP_HIT`, `OVERNIGHT_REJECTED`).

## 4. `config/tickers.yaml` schema

```yaml
tickers:
  AG:
    benchmark: SIL
    sector: silver_miner
    max_spread_pct: 0.20        # percent, e.g. 0.20 = 0.20%
    overnight_category: normal  # normal | conditional | manual_only
    overnight_position_multiplier: 1.0   # applied when category == conditional
    manual_only: false
    profit_target_pct: [3.0, 5.5]        # [min, max] typical target range, percent
    volatility_category: normal          # low | moderate | high | very_high (informational;
                                          # actual sizing/targets use live ATR_PERCENT)
  USAR:
    benchmark: REMX
    sector: rare_earth
    max_spread_pct: 0.20
    overnight_category: conditional
    overnight_position_multiplier: 0.50
    manual_only: false
    profit_target_pct: [4.0, 8.0]
    volatility_category: high
  NZAUF:
    benchmark: GDXJ
    sector: exploration_otc
    max_spread_pct: 0.75
    overnight_category: manual_only
    overnight_position_multiplier: 0.0
    manual_only: true
    profit_target_pct: null
    volatility_category: very_high
  AQN:
    benchmark: XLU
    sector: utility            # excluded from mining strategy universe (spec §3)
    strategy: excluded
    max_spread_pct: 0.20
    overnight_category: normal
    overnight_position_multiplier: 1.0
    manual_only: false
    profit_target_pct: [1.5, 2.5]
    volatility_category: low
```

All 22 approved symbols + NZAUF/AAGAF (manual-only OTC) + AQN (excluded/utility, kept
only so it's never silently mistaken for a mining ticker) are populated this way in the
real file.

## 5. `config/strategy.yaml` schema

```yaml
candle_timeframe: 5m
indicators:
  ema_fast: 9
  ema_slow: 20
  atr_period: 14

eligibility:
  min_relative_volume: 1.20

chase_rule:
  max_consecutive_large_green_candles: 3
  large_candle_body_atr_multiple: 1.0    # "unusually large" = body >= 1.0x ATR
  max_atr_above_vwap: 1.0
  max_pct_above_open_without_consolidation: 5.0

benchmark_confirmation:
  require_price_above_vwap: true
  require_ema_alignment: true       # 9EMA >= 20EMA
  require_no_fresh_intraday_low: true

setups:
  opening_range_minutes: 10          # 9:30-9:40
  pullback_max_atr_from_breakout: 1.0
  vwap_reclaim_lookback_bars: 6

reward_risk:
  minimum_r: 1.5
  preferred_r_min: 1.75

scoring:
  weights:
    benchmark_strength: 20
    relative_volume: 15
    price_vs_vwap: 10
    ema_alignment: 10
    opening_range_structure: 15
    pullback_quality: 10
    risk_reward: 10
    spread_liquidity: 5
    sector_strength: 5
  minimum_entry_score:
    primary_window: 70    # 9:40-11:30, 2:00-3:15
    midday_window: 80     # 11:30-2:00 (low priority period)

trade_management:
  breakeven_trigger_r: 1.0
  partial_exit:
    enabled: true
    trigger_r: 1.5
    sell_fraction: 0.35   # configurable 0.25-0.50 per spec
```

## 6. `config/risk.yaml` schema

```yaml
account:
  max_account_risk_per_trade: 0.0075   # 0.75% of equity; configurable, not final

sizing:
  max_position_size_dollars: 25000
  max_position_size_pct_equity: 0.25

stops:
  atr_multiplier:
    low_volatility_diversified: 0.75   # RIO, BHP, etc.
    normal: 0.85
    high_volatility_speculative: 0.95

safety:
  max_trades_per_day: 3
  max_daily_loss_pct: 0.02
  max_consecutive_losses: 2
  max_position_size_dollars: 25000
  max_slippage_pct: 0.10
  max_spread_pct_default: 0.25         # fallback; per-ticker overrides in tickers.yaml
  data_staleness_limit_seconds: 30
  broker_connection_check_interval_seconds: 15
  ticker_cooldown_after_stop_minutes: 45

behavior:
  averaging_down: false
  pyramiding: false
  max_concurrent_positions: 1

overnight:
  default_position_multiplier: 1.0     # normal category
  reduced_position_multiplier: 0.50    # conditional category default

emergency:
  kill_switch_enabled: true
  kill_switch_file: "logs/KILL_SWITCH"  # presence of this file halts new entries
```

## 7. `config/broker.yaml` schema

```yaml
mode: paper   # paper | live  -- ONLY "paper" is implemented; "live" raises at startup

paper:
  starting_equity: 100000
  fill_model: touch   # limit order fills when quote touches/crosses the limit price

etrade:
  # Sandbox vs production is an explicit, separate config block. No code path reads
  # production credentials or a production base URL in Phase 1/2 — ETradeBrokerAdapter
  # is a stub. When implemented later, this block will point at E*TRADE's documented
  # sandbox OAuth/API base URLs; production activation will require both
  # ETRADE_ENV=production in the environment AND an explicit `mode: live` here.
  environment: sandbox
  consumer_key_env: ETRADE_SANDBOX_CONSUMER_KEY
  consumer_secret_env: ETRADE_SANDBOX_CONSUMER_SECRET
  account_id_env: ETRADE_SANDBOX_ACCOUNT_ID
```

## 8. `config/schedule.yaml` schema

```yaml
timezone: America/New_York
windows:
  - name: observation
    start: "09:30"
    end: "09:40"
    allow_new_entries: false
  - name: primary_entry
    start: "09:40"
    end: "11:30"
    allow_new_entries: true
    min_score_key: primary_window
  - name: low_priority
    start: "11:30"
    end: "14:00"
    allow_new_entries: true
    min_score_key: midday_window
  - name: secondary_entry
    start: "14:00"
    end: "15:15"
    allow_new_entries: true
    min_score_key: primary_window
  - name: position_management_only
    start: "15:15"
    end: "15:50"
    allow_new_entries: false
  - name: overnight_review
    start: "15:50"
    end: "16:00"
    allow_new_entries: false
market_close: "16:00"
```

## 9. Ticker State Machine (`src/models/trade.py: TradeState`)

```
WATCHING -> SETUP_FORMING -> ENTRY_ELIGIBLE -> ORDER_PENDING -> POSITION_OPEN
POSITION_OPEN -> POSITION_PARTIAL (on partial exit)
POSITION_OPEN | POSITION_PARTIAL -> OVERNIGHT_REVIEW (at ~3:50pm, if still open)
OVERNIGHT_REVIEW -> OVERNIGHT_POSITION (thesis valid) | EXIT_PENDING (thesis invalid)
EXIT_PENDING -> CLOSED
OVERNIGHT_POSITION -> (next session) -> POSITION_OPEN | EXIT_PENDING -> CLOSED
any state -> LOCKED_OUT  (risk-limit breach, kill switch, data-quality halt)
```

`PositionManager` is the only component allowed to move a ticker's state. It refuses to
create a second independent entry for a ticker already in `POSITION_OPEN`/
`POSITION_PARTIAL` unless `behavior.pyramiding` is `true` (it is `false` by default and
Phase 1/2 do not implement pyramiding at all).

## 10. Main Loop Pseudocode (`src/main.py`)

```
load config (tickers, strategy, risk, broker, schedule)
assert broker.mode == "paper"        # hard stop otherwise, see main.py
data_provider = build_market_data_provider(config)   # Phase 1: in-memory/replay
broker_adapter = PaperBrokerAdapter(config)
risk_manager = RiskManager(config)
position_manager = PositionManager(config)
order_manager = OrderManager(broker_adapter, position_manager, risk_manager)
signal_log = SignalJournal(...); trade_journal = TradeJournal(...)

event_loop:
    while market_session_active():
        if kill_switch_triggered() or not risk_manager.broker_connection_ok():
            risk_manager.block_new_entries()
            # position management may still run in a degraded/safe mode

        current_window = schedule.current_window(now())

        for ticker in approved_universe:
            state = data_provider.get_ticker_state(ticker)          # continuously updated
            bench_state = data_provider.get_ticker_state(config.benchmark_of(ticker))
            if data_provider.is_stale(ticker) or data_provider.is_stale(bench):
                signal_log.log(REJECTED_DATA_QUALITY); continue

            position = position_manager.get(ticker)
            if position and position.is_open():
                position_manager.manage(position, state, bench_state)   # breakeven/partial/stop/target
                continue

            if not current_window.allow_new_entries:
                signal_log.log(ticker, REJECTED_TIME_WINDOW); continue

            signal = signal_engine.evaluate(ticker, state, bench_state, current_window)
            signal_log.log(signal)

            if signal.decision == ENTRY_CANDIDATE:
                if risk_manager.can_open_new_position(ticker, signal):
                    order_manager.submit_entry(ticker, signal)        # limit order, rechecked
                else:
                    signal_log.log(ticker, REJECTED_MAX_DAILY_RISK / REJECTED_DUPLICATE_POSITION / ...)

        if time_is_approximately("15:50"):
            for position in position_manager.open_positions():
                decision = overnight.evaluate(position, ...)
                position_manager.apply_overnight_decision(position, decision)  # hold (sized
                                                                                 # per multiplier) or exit

        sleep(poll_interval_seconds)   # short interval driven by an event loop, not a
                                        # fixed 15/30 min schedule (spec §6)

end_of_day:
    trade_journal.flush()
    daily_report.generate(signal_log, trade_journal, date)
```

`main.py` in Phase 1 wires this together against the in-memory/replay data provider and
the paper broker only — there is no network call to a real market-data vendor or to
E*TRADE yet. That is intentionally deferred to the "connect to sandbox" step, after
Phase 1's models/indicators/strategy/risk/tests are in place, per spec §36.

## 11. What Could Ever Place a Real Order (spec §37)

- Only `broker/etrade.py::ETradeBrokerAdapter` could ever reach E*TRADE's network API,
  and every method in it currently raises `NotImplementedError`. There is no HTTP client
  configured in it.
- `broker/base.py::BrokerInterface` is the only type `OrderManager` depends on; `main.py`
  hard-fails at startup unless `broker.yaml: mode == "paper"`, which is enforced in
  `src/config_loader.py` (`ConfigError` if `mode != "paper"`), and `PaperBrokerAdapter`
  is the only adapter implemented, so there is no path that can currently submit a real
  order.
- Sandbox vs. production is selected by two independent switches that must **both** be
  set once E*TRADE integration exists: `broker.yaml: mode: live` AND
  `ETRADE_ENV=production` in the environment. Phase 1/2 ship with neither wired to
  anything live.
- The kill switch (`risk.yaml: emergency.kill_switch_enabled` + presence of
  `logs/KILL_SWITCH`) is checked every loop iteration in `main.py` and blocks all new
  entries; it does not touch position management/exit logic, which must still be able to
  flatten risk.
