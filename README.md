# Mining Stock Trading Bot

A rules-based, long-only intraday momentum/pullback trading bot for a fixed universe
of mining and commodity-related securities. **Paper trading only.** See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design (architecture,
file tree, config schemas, state machine, main-loop pseudocode) and the exact
safeguards that keep live/production trading disabled.

## Status: Phase 1 (+ Phase 2 groundwork)

* **Phase 1 -- Market data + signal engine**: implemented. Indicators (VWAP, EMA,
  ATR, relative volume, opening range, spread, swing highs/lows), benchmark
  confirmation, the "do not chase" rule, the two preferred setups (opening-range
  breakout + pullback, VWAP reclaim), and 0-100 setup scoring all have unit and
  scenario test coverage.
* **Phase 2 -- Paper trading**: the simulated broker (`broker/paper.py`), order
  manager (duplicate-order protection + pre-submit rechecks), position manager
  (state machine + breakeven/partial-exit trade management), and overnight-holding
  evaluation are implemented and wired together in `src/main.py: TradingBot`.
* **Phase 3 -- Strategy evaluation**: `reporting/daily_report.py` produces the daily
  report (win rate, R multiples, drawdown, missed-opportunity analysis) and
  read-only feedback-loop suggestions.
* **Phase 4 -- Live trading**: **not implemented.** `broker/etrade.py` is a stub
  whose every method raises `NotImplementedError`. See
  `docs/ARCHITECTURE.md` section 11 for exactly what would have to change (and how
  many independent switches would have to be flipped) before any real order could
  ever be placed.

There is currently **no connection to a live market-data feed or to E*TRADE**.
`src/main.py` wires the full pipeline together against an in-memory data provider so
it can be exercised by tests; running `python src/main.py` prints a status message
rather than starting a live loop.

## Project layout

See `docs/ARCHITECTURE.md` for the annotated file tree. In short:

```
config/     tickers.yaml, strategy.yaml, risk.yaml, broker.yaml, schedule.yaml
src/        broker/ data/ strategy/ risk/ positions/ execution/ reporting/ models/
tests/      unit tests + scenario tests
logs/       signal log (jsonl)
reports/    trade journal (jsonl)
```

## Running the tests

```
pip install -r requirements.txt
pytest
```

## Trading universe & rules

The approved symbols, benchmark ETF mappings, spread limits, overnight categories,
and profit-target ranges live in `config/tickers.yaml` (spec sections 3-4, 13,
16, 18-21) -- nothing ticker-specific is hard-coded in strategy source. Strategy
thresholds (scoring weights, chase-rule limits, reward/risk minimums) live in
`config/strategy.yaml`. Money-management and daily safety limits live in
`config/risk.yaml`, including the kill switch
(`emergency.kill_switch_enabled` + presence of `logs/KILL_SWITCH`).

`AQN` is present in `tickers.yaml` only so it is never silently miscategorized --
it is marked `strategy: excluded` and is never traded by the mining strategy.
`NZAUF` and `AAGAF` are `manual_only: true`: the bot evaluates and scores them but
never submits an order for them.

## Safety

* Paper trading is the only mode `config_loader.load_config()` will accept --
  it raises if `config/broker.yaml: mode` is anything other than `paper`.
* No short selling, no averaging down, no pyramiding (all hard-disabled, not just
  defaulted).
* A stopped-out ticker enters a configurable cooldown before it can be re-entered
  (no revenge trading).
* Every evaluated signal (entry or rejection, with reason) and every completed trade
  is logged (`logs/signals.jsonl`, `reports/trades.jsonl`).
