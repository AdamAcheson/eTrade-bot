# Mining Stock Trading Bot

A rules-based, long-only intraday momentum/pullback trading bot for a fixed universe
of mining and commodity-related securities. **Paper trading by default; E*TRADE
SANDBOX only beyond that -- production trading is not implemented.** See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design (architecture,
file tree, config schemas, state machine, main-loop pseudocode) and the exact
safeguards that keep live/production trading disabled.

## Status: Phase 1 + Phase 2 (paper and E*TRADE sandbox)

* **Phase 1 -- Market data + signal engine**: implemented. Indicators (VWAP, EMA,
  ATR, relative volume, opening range, spread, swing highs/lows), benchmark
  confirmation, the "do not chase" rule, the two preferred setups (opening-range
  breakout + pullback, VWAP reclaim), and 0-100 setup scoring all have unit and
  scenario test coverage.
* **Phase 2 -- Paper trading**: the simulated broker (`broker/paper.py`), order
  manager (duplicate-order protection + pre-submit rechecks), position manager
  (state machine + breakeven/partial-exit trade management), and overnight-holding
  evaluation are implemented and wired together in `src/main.py: TradingBot`. This
  is still the default (`config/broker.yaml: mode: paper`).
* **Phase 2b -- E*TRADE sandbox connection**: `broker/etrade.py` (`ETradeBrokerAdapter`)
  makes real HTTP calls to E*TRADE's documented sandbox API (accounts, orders,
  quotes) using OAuth1 (`broker/etrade_auth.py`). Since E*TRADE's API has no
  historical-bar endpoint, `data/etrade_market_data.py` builds 5-minute bars itself
  by polling quotes. **Verified against a real sandbox account**: account balance,
  positions, quotes, and order placement (preview -> place) all confirmed working
  (two real response-parsing bugs were found and fixed this way). Note: E*TRADE's
  sandbox does not realistically track state between calls -- placing an order
  doesn't change what a later `GET /orders` returns -- so it validates the API
  integration itself, not realistic account behavior over time.
* **Phase 3 -- Strategy evaluation**: `reporting/daily_report.py` produces the daily
  report (win rate, R multiples, drawdown, missed-opportunity analysis) and
  read-only feedback-loop suggestions.
* **Phase 4 -- Live/production trading**: **not implemented, anywhere.**
  `ETradeBrokerAdapter` raises `ETradeSafetyError` for any `environment` other than
  `"sandbox"`, and `config_loader.load_config()` separately raises unless
  `broker.yaml: mode` is `"paper"` or `"sandbox"`. See `docs/ARCHITECTURE.md`
  section 11 for the full safety chain.

## Connecting to E*TRADE sandbox

1. Register a sandbox app on E*TRADE's developer portal to get a consumer key/secret.
2. `cp .env.example .env` and fill in `ETRADE_SANDBOX_CONSUMER_KEY` /
   `ETRADE_SANDBOX_CONSUMER_SECRET`. Never commit `.env`.
3. `set -a; source .env; set +a` (or otherwise export those two vars), then run
   `python scripts/etrade_authorize.py` **locally, in a real terminal with a
   browser** -- it walks you through E*TRADE's 3-legged OAuth flow and prints an
   access token/secret for you to add to `.env`. This can't be done from inside an
   AI assistant session; the flow requires you to log in and read a verifier code.
4. Set `config/broker.yaml: mode: sandbox`.
5. Run `python scripts/etrade_sandbox_check.py` -- read-only (lists your account,
   gets one quote), places no orders. Confirm the output looks right before doing
   anything else with `ETradeBrokerAdapter`.
6. E*TRADE access tokens expire at midnight US Eastern or after 2 hours idle --
   re-run step 3 when that happens.

Other scripts once you're connected: `scripts/etrade_status.py` (read-only account
balance/positions/orders with live unrealized P&L), `scripts/etrade_test_order.py`
(places one confirmed-by-hand test order).

## Running the live bot loop

`scripts/run_bot.py` actually starts the continuous polling loop described in
`docs/ARCHITECTURE.md` section 10 -- it's the only entry point that does (running
`src/main.py` directly just prints a status message). It requires
`config/broker.yaml: market_data_source: etrade`, which is independent of `mode`:

* `mode: paper` + `market_data_source: etrade` -- real E*TRADE sandbox quotes drive
  the strategy, but fills/P&L stay in the local `PaperBrokerAdapter` (useful because
  E*TRADE's sandbox doesn't track order/position state realistically, per above).
* `mode: sandbox` + `market_data_source: etrade` -- same real quotes, but orders
  also actually go to E*TRADE.

## Backtesting against real historical data

E*TRADE's sandbox quotes are static (never move), so the strategy can never
actually fire against it -- confirmed by a 7.5-hour live run that logged nothing
but `REJECTED_BENCHMARK_CONFIRMATION`/`REJECTED_TIME_WINDOW` all day. To see the
strategy evaluate real price action, `scripts/backtest.py` replays real historical
5-minute bars (from Yahoo Finance's public chart API -- the last ~60 days is what's
available for 5-minute data) through the exact same `TradingBot` /
`PaperBrokerAdapter` / `InMemoryMarketDataProvider` stack used everywhere else. It
is not a separate simulation engine.

```
python scripts/fetch_historical_data.py   # once, populates data_cache/historical/
python scripts/backtest.py                # replays all cached days
python scripts/backtest.py --days 10      # just the most recent 10 trading days
```

Known approximations (Yahoo's free intraday data has no bid/ask): spread is
synthesized as a fraction of each ticker's configured `max_spread_pct`; relative
volume uses a time-of-day-aware baseline computed from strictly prior days only (no
lookahead bias) -- the first backtest day has no baseline yet, mirroring a real
bot's cold start.

This is also how a real, previously-undetected bug was found: `evaluate_and_maybe_
enter()` submitted entry orders but discarded the result, so a filled order never
became a tracked position -- real signals fired but zero trades ever closed. Fixed
in `src/main.py`; see `tests/test_entry_opens_position.py` for the regression test.
A second bug found the same way: `_snapshot_for` used every bar ever recorded for a
ticker, not just the current session's, so indicators would silently blend
yesterday's data into today's once the bot ran past a single day -- see
`tests/test_main_snapshot.py`.

```
python scripts/run_bot.py [poll_interval_seconds]   # default 30s, Ctrl+C to stop
```

Known limitation: there's no historical daily-volume feed wired up, so relative
volume (RVOL) reads as unavailable and most signals will reject on
`REJECTED_LOW_VOLUME` -- this run validates that the full pipeline (poll -> bars ->
indicators -> strategy -> risk -> logging) executes continuously against a real
broker connection, not that it will find live entries with wiring this thin.

`src/main.py` still doesn't start a live loop when run directly, in either mode --
running `python src/main.py` prints a status message and exits.

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

* `config_loader.load_config()` only accepts `broker.yaml: mode: paper` or
  `sandbox` -- anything else (including `live`) raises at startup.
* `ETradeBrokerAdapter` independently refuses to construct unless
  `etrade.environment: sandbox` -- two separate checks have to agree before any
  network call to E*TRADE happens at all, and neither can reach production.
* Credentials are read only from environment variables named in `broker.yaml` --
  never hard-coded, never logged, never committed (`.env` is gitignored).
* No short selling, no averaging down, no pyramiding (all hard-disabled, not just
  defaulted).
* A stopped-out ticker enters a configurable cooldown before it can be re-entered
  (no revenge trading).
* Every evaluated signal (entry or rejection, with reason) and every completed trade
  is logged (`logs/signals.jsonl`, `reports/trades.jsonl`).
